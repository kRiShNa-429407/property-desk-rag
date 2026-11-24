import json
import unittest
from unittest.mock import Mock, patch

from llm import ChatError
from mongo_fixtures import MongoCase
from rag import Passage, Retriever
from search_plan import InvalidPlan, execute_plan, interpret_question, read_plan, validate_plan


def search(**changes):
    return dict({'intent': 'search', 'operation': 'list', 'cities': ['Riyadh'], 'district': None,
                 'property_types': ['villa'], 'purpose': 'sale', 'price': {}, 'bedrooms': {},
                 'sort': 'recent', 'limit': 5}, **changes)


class PlanValidationTests(unittest.TestCase):
    def test_schema_response_is_converted_to_only_supported_filters(self):
        plan = search()
        plan.update(price=[{'operator': 'lt', 'value': 2000000}],
                    bedrooms=[{'operator': 'gte', 'value': 3}, {'operator': 'lte', 'value': 5}],
                    missing=None, features=[])
        parsed = read_plan(json.dumps(plan))
        self.assertEqual(parsed['price'], {'lt': 2000000})
        self.assertEqual(parsed['bedrooms'], {'gte': 3, 'lte': 5})
        client = Mock()
        response = Mock(status_code=200)
        response.json.return_value = {'choices': [{'message': {'content': json.dumps(plan)}}]}
        client.post.return_value = response
        interpret_question('Some new wording', [], ['schema-test-key'], session=client)
        payload = client.post.call_args.kwargs['json']
        self.assertEqual(payload['provider']['max_price'], {'prompt': 0, 'completion': 0})
        self.assertTrue(payload['provider']['require_parameters'])
        self.assertTrue(payload['response_format']['json_schema']['strict'])

    def test_schema_non_search_and_ambiguous_output(self):
        plan = search(intent='clarify', missing='purpose', features=[])
        self.assertEqual(read_plan(json.dumps(plan)), {'intent': 'clarify', 'missing': 'purpose'})
        plan['missing'] = None
        self.assertEqual(read_plan(json.dumps(plan)), {'intent': 'clarify', 'missing': 'question'})
        plan = search(price=[{'operator': 'lt', 'value': 20}, {'operator': 'lt', 'value': 40}],
                      bedrooms=[], missing=None, features=[])
        with self.assertRaises(InvalidPlan):
            read_plan(json.dumps(plan))

    def test_rejects_database_operators_and_unknown_fields(self):
        for plan in [search(pipeline=[{'$out': 'wasalt'}]), search(price={'$gt': 5}),
                     search(cities=[{'$ne': None}]), search(district='$where'),
                     search(price={'gte': True}), search(price={'lte': float('nan')}),
                     search(price={'gte': -1}), search(bedrooms={'eq': 3.5}),
                     search(limit=1000000), search(operation='delete'),
                     search(property_types=['anything']), search(price={'gte': 5, 'lte': 3}),
                     search(price={'eq': 3, 'lt': 5}), search(operation='compare', cities=['Riyadh'])]:
            with self.subTest(plan=plan), self.assertRaises(InvalidPlan):
                validate_plan(plan)

    def test_rejects_partial_or_malformed_model_output(self):
        for answer in ['not json', '[]', '{}', '{"intent":"search"}',
                       '{"intent":"knowledge","price":{"lt":100}}']:
            with patch('search_plan.complete', return_value=(answer, 'free')):
                with self.subTest(answer=answer), self.assertRaises(InvalidPlan):
                    interpret_question('Question', [], ['test-key'])

    def test_interpreter_uses_user_history_and_validated_previous_search(self):
        plan = search(price={'lt': 2000000}, bedrooms={'gte': 4})
        history = [{'role': 'user', 'content': 'Find villas under 3m'},
                   {'role': 'assistant', 'content': 'An unverified earlier answer'}]
        with patch('search_plan.complete', return_value=(json.dumps(plan), 'free')) as complete:
            result = interpret_question('Make the budget 2 million and at least four beds', history,
                                        ['test-key'], previous_search=search(price={'lt': 3000000}))
        self.assertEqual(result, plan)
        context = json.loads(complete.call_args.args[0][1]['content'])
        self.assertEqual(context['history'], ['Find villas under 3m'])
        self.assertEqual(context['previous_search']['price'], {'lt': 3000000})

    def test_unrelated_unsupported_and_ambiguous_questions_do_not_query_mongo(self):
        retriever = Retriever([Passage('DarGlobal', 'AIDA', 'https://darglobal.co.uk', '', 'AIDA is in Muscat Oman.')])
        for plan, phrase in [({'intent': 'out_of_scope'}, 'DarGlobal'),
                             ({'intent': 'unsupported', 'features': ['private pool']}, "can't reliably verify"),
                             ({'intent': 'clarify', 'missing': 'purpose'}, 'buy or rent')]:
            with patch('rag.interpret_question', return_value=plan), patch('rag.get_collection') as mongo:
                result = retriever.search('An unfamiliar question', api_keys=['test-key'])
                self.assertIn(phrase, result[0].answer)
                self.assertEqual(result[0].url, '')
                mongo.assert_not_called()

    def test_model_failure_is_a_useful_reply_without_guessing(self):
        retriever = Retriever([Passage('DarGlobal', 'AIDA', '', '', 'AIDA is in Oman.')])
        for error in [InvalidPlan('bad json'), ChatError('rate limit')]:
            with patch('rag.interpret_question', side_effect=error), patch('rag.get_collection') as mongo:
                result = retriever.search('Find me homes that fit my budget', api_keys=['test-key'])
                self.assertIn('right now', result[0].answer)
                mongo.assert_not_called()


class PlannedSearchTests(MongoCase):
    def setUp(self):
        super().setUp()
        rows = [('1', 'Riyadh', 1500000, 4), ('2', 'Riyadh', 2000000, 5),
                ('3', 'Riyadh', 2500000, 6), ('4', 'Jeddah', 1800000, 4)]
        self.collection.insert_many([{'_id': number, 'record_type': 'property', 'city': city,
                                     'district': 'Centre', 'purpose': 'sale', 'kind': 'Villa',
                                     'price': price, 'currency': 'SAR', 'bedrooms': beds,
                                     'updated': '', 'title': 'Villa', 'text': '',
                                     'url': 'https://wasalt.sa/en/property/sale/' + number}
                                    for number, city, price, beds in rows])

    def test_strict_inclusive_and_bedroom_ranges(self):
        for price, expected in [({'lt': 2000000}, '1 villa'), ({'lte': 2000000}, '2 villa')]:
            result = execute_plan(self.collection, search(operation='count', price=price, bedrooms={'gte': 4, 'lte': 5}))
            self.assertIn(expected, result[0]['answer'])

    def test_sort_limit_and_no_match(self):
        result = execute_plan(self.collection, search(sort='price_asc', limit=2))
        self.assertEqual([row['price'] for row in result[1:]], [1500000, 2000000])
        result = execute_plan(self.collection, search(price={'lt': 1}))
        self.assertIn("don't have listings matching", result[0]['answer'])
        result = execute_plan(self.collection, search(operation='count', price={'lt': 1}))
        self.assertIn("don't have any matching listings", result[0]['answer'])

    def test_city_comparison_and_average_are_calculated_by_mongo(self):
        result = execute_plan(self.collection, search(operation='compare', cities=['Riyadh', 'Jeddah']))
        self.assertIn('Riyadh has 2 more listings', result[0]['answer'])
        result = execute_plan(self.collection, search(operation='average'))
        self.assertIn('SAR 2,000,000.00', result[0]['answer'])

    def test_model_translates_wording_but_does_not_supply_the_count(self):
        retriever = Retriever([Passage('DarGlobal', 'AIDA', '', '', 'AIDA is in Oman.')])
        plan = search(operation='count', price={'lt': 2000000}, bedrooms={'gte': 4})
        for question in ['Any family homes for purchase in the capital below two million with four or more beds?',
                         'Count Riyadh villas for sale under 2m with minimum four bedrooms']:
            with patch('rag.interpret_question', return_value=plan), patch('rag.get_collection', return_value=self.collection):
                result = retriever.search(question, api_keys=['test-key'])
            self.assertIn('1 villa listings', result[0].answer)
            self.assertEqual(result[0].search_plan, plan)

    def test_chat_stores_plan_for_follow_up_and_never_sends_count_to_answer_model(self):
        from pathlib import Path
        from streamlit.testing.v1 import AppTest
        first = search(operation='count', price={'lt': 2000000})
        second = search(operation='count', price={'lte': 2000000}, bedrooms={'gte': 4})
        with patch('dotenv.dotenv_values', return_value={'OPENROUTER_API_KEY': 'test-plan-key'}), \
                patch('rag.interpret_question', side_effect=[first, second]) as interpret, \
                patch('rag.get_collection', return_value=self.collection), patch('llm.requests.post') as post:
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app.py')).run(timeout=30)
            app.chat_input[0].set_value('How many family homes can I buy below 2 million?').run(timeout=30)
            app.chat_input[0].set_value('Include exactly 2 million and four or more bedrooms').run(timeout=30)
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(len(app.error), 0)
            self.assertEqual(interpret.call_args.args[3], first)
            self.assertEqual(app.session_state.messages[-2]['search_plan'], second)
            self.assertTrue(any('2 villa listings' in message.value for message in app.markdown))
            post.assert_not_called()
            app.button[0].click().run(timeout=30)
            self.assertEqual(app.session_state.messages, [])
