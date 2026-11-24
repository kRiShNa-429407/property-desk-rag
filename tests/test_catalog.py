import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock

from catalog import extra_fields, query_catalog
from llm import ask
from rag import Passage
from search_questions import resolve_search_question
from mongo_fixtures import MongoCase


class CatalogTests(MongoCase):
    def setUp(self):
        super().setUp()
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        rows = []
        examples = [(1, 'Riyadh', 'Apartment', 650000, 3, 'sale'),
                    (2, 'Riyadh', 'Apartment', 950000, 3, 'sale'),
                    (3, 'Riyadh', 'Apartment', 1200000, 4, 'sale'),
                    (4, 'Jeddah', 'Villa', 800000, 3, 'sale'),
                    (5, 'Riyadh', 'Villa', 1500000, 5, 'sale'),
                    (6, 'Riyadh', 'Apartment', None, 3, 'sale'),
                    (7, 'Riyadh', 'Apartment', 500000, 2, 'sale'),
                    (8, 'Riyadh', 'Apartment', 30000, 3, 'rent')]
        for identifier, city, kind, price, beds, purpose in examples:
            rows.append({'id': identifier, 'updatedAt': '2026-11-18',
                         'propertyInfo': {'slug': f'home-{identifier}', 'title': kind, 'city': city,
                                          'district': 'Riyadh' if identifier == 7 else 'Centre',
                                          'propertySubType': kind, 'propertyFor': purpose,
                                          'salePrice': price if purpose == 'sale' else None,
                                          'expectedRent': price if purpose == 'rent' else None,
                                          'currencyType': 'SAR'},
                         'attributes': [{'name': 'Bedrooms', 'value': beds}]})
        self.rows = rows
        self.load_rows(self.directory.name, rows)

    def test_city_name_is_not_accidentally_treated_as_a_district(self):
        result = query_catalog(self.collection, 'How many properties in Riyadh?')
        self.assertIn('7 property listings in Riyadh', result[0]['answer'])

    def test_bedrooms_and_budget_are_exact_and_missing_prices_do_not_match(self):
        result = query_catalog(self.collection, 'Show apartments for sale in Riyadh with 3 bedrooms under SAR 1 million')
        self.assertEqual({row['url'].split('/')[-1] for row in result[1:]}, {'home-1', 'home-2'})
        count = query_catalog(self.collection, 'How many 3-bedroom apartments for sale in Riyadh under 1m?')
        self.assertIn('**2 apartment listings', count[0]['answer'])
        between = query_catalog(self.collection, 'Show apartments for sale in Riyadh between 700k and 1m')
        self.assertEqual(len(between), 2)
        self.assertTrue(between[1]['url'].endswith('home-2'))

    def test_average_uses_all_priced_matches_and_excludes_nulls(self):
        result = query_catalog(self.collection, 'What is the average price of apartments for sale in Riyadh?')
        self.assertIn('SAR 825,000.00', result[0]['answer'])
        self.assertIn('4 apartment listings', result[0]['answer'])

    def test_bedroom_ranges_and_reversed_wording_are_filtered(self):
        result = query_catalog(self.collection, 'How many apartments for sale in Riyadh with bedrooms at least 3?')
        self.assertIn('**4 apartment listings', result[0]['answer'])
        result = query_catalog(self.collection, 'Show apartments for sale in Riyadh with at most 2 bedrooms')
        self.assertEqual(len(result), 2)
        self.assertTrue(result[1]['url'].endswith('home-7'))

    def test_cheapest_is_sorted_numerically_over_all_matching_rows(self):
        result = query_catalog(self.collection, 'What is the cheapest apartment for sale in Riyadh?')
        self.assertEqual(len(result), 2)
        self.assertTrue(result[1]['url'].endswith('home-7'))

    def test_unsupported_filters_and_mixed_rental_prices_are_not_ignored(self):
        result = query_catalog(self.collection, 'How many villas with a pool in Riyadh?')
        self.assertIn("can't reliably filter", result[0]['answer'])
        multiple = query_catalog(self.collection, 'Show villas in Riyadh and Jeddah')
        self.assertIn('one location at a time', multiple[0]['answer'])
        average = query_catalog(self.collection, 'What is the average price of apartments in Riyadh?')
        self.assertIn('buy or rent', average[0]['answer'])
        unknown = query_catalog(self.collection, 'How many farm properties in Riyadh?')
        self.assertIn("can't reliably filter", unknown[0]['answer'])

    def test_exact_database_answers_do_not_call_or_depend_on_the_model(self):
        result = query_catalog(self.collection, 'How many villas in Riyadh?')[0]
        passage = Passage('Wasalt', result['title'], result['url'], 'unknown', result['text'], result['answer'])
        client = Mock()
        answer, model = ask('How many villas?', [passage], '', session=client)
        self.assertEqual(answer, result['answer'])
        self.assertEqual(model, 'wasalt-catalog')
        client.post.assert_not_called()

    def test_unknown_values_are_not_converted_to_zero(self):
        self.assertEqual(extra_fields('Bedrooms: Not specified\nPurpose: sale'), (None, '', None, ''))
        self.assertEqual(extra_fields('Purpose: sale\nListed sale price: SAR 650000.50\nBedrooms: 3')[:3],
                         (650000.5, 'SAR', 3))

    def test_natural_bedroom_wording(self):
        for phrase in ['at least four', 'minimum 4', 'no fewer than four', 'a minimum of four']:
            with self.subTest(phrase=phrase):
                result = query_catalog(self.collection, f'How many villas are available for sale in Riyadh under SAR 2 million with {phrase} bedrooms?')
                self.assertIn('**1 villa listings', result[0]['answer'])
        result = query_catalog(self.collection, 'How many apartments for sale in Riyadh with no more than three bedrooms?')
        self.assertIn('**4 apartment listings', result[0]['answer'])

    def test_city_comparison_counts_and_difference(self):
        result = query_catalog(self.collection, 'Which has more properties for sale, Riyadh or Jeddah? Give the count for each city and the difference.')
        self.assertIn('| Riyadh | 6 |', result[0]['answer'])
        self.assertIn('| Jeddah | 1 |', result[0]['answer'])
        self.assertIn('Riyadh has 5 more listings', result[0]['answer'])
        result = query_catalog(self.collection, 'Which has more properties for sale, Riyadh or Jeddah? Give the counts and difference.')
        self.assertIn('| Riyadh | 6 |', result[0]['answer'])
        self.assertIn('| Jeddah | 1 |', result[0]['answer'])
        self.assertIn('Riyadh has 5 more listings', result[0]['answer'])

    def test_follow_up_uses_all_filters_and_can_change_them(self):
        history = [{'role': 'user', 'content': 'Show villas for sale in Riyadh under 2m'}]
        resolved = resolve_search_question('Only those with at least five bedrooms', history)
        result = query_catalog(self.collection, resolved)
        self.assertTrue(result[1]['url'].endswith('home-5'))
        history.append({'role': 'user', 'content': 'Only those with at least five bedrooms', 'search_question': resolved})
        resolved = resolve_search_question('Which is the cheapest? Give me its listing link.', history)
        result = query_catalog(self.collection, resolved)
        self.assertTrue(result[1]['url'].endswith('home-5'))
        self.assertIn('[View listing]', result[0]['answer'])
        history = [{'role': 'user', 'content': 'Show apartments for sale in Riyadh under 1m with 3 bedrooms'}]
        resolved = resolve_search_question('Only those under 2m with at least four bedrooms', history)
        result = query_catalog(self.collection, resolved)
        self.assertEqual(len(result), 2)
        self.assertTrue(result[1]['url'].endswith('home-3'))

    def test_chat_retains_refined_search_and_clears_it_on_new_chat(self):
        from streamlit.testing.v1 import AppTest
        from unittest.mock import patch
        with patch('rag.get_collection', return_value=self.collection), patch('dotenv.dotenv_values', return_value={}), patch('llm.requests.post') as post:
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app.py')).run(timeout=30)
            for question in ['Show me villas for sale in Riyadh under SAR 2 million',
                             'Only those with at least five bedrooms',
                             'Which one is the cheapest? Give me its listing link.']:
                app.chat_input[0].set_value(question).run(timeout=30)
                self.assertEqual(len(app.exception), 0)
                self.assertEqual(len(app.error), 0)
            search = app.session_state.messages[-2]['search_question']
            self.assertIn('riyadh', search)
            self.assertIn('at least 5 bedrooms', search)
            self.assertIn('under sar 2 million', search)
            self.assertTrue(any('SAR 1,500,000' in message.value for message in app.markdown))
            post.assert_not_called()
            app.button[0].click().run(timeout=30)
            self.assertEqual(app.session_state.messages, [])

    def test_suspicious_prices_are_preserved_flagged_and_excluded_from_average(self):
        row = self.collection.find_one({'price': 500000})
        row['_id'] = 'property:unverified'
        row['price'] = 437
        row['url'] = 'https://wasalt.sa/en/property/sale/unverified'
        self.collection.insert_one(row)
        cheapest = query_catalog(self.collection, 'Show the five cheapest apartments for sale in Riyadh')
        self.assertIn('SAR 437 (unverified; unusually low)', cheapest[0]['answer'])
        self.assertEqual(len(cheapest) - 1, 5)
        average = query_catalog(self.collection, 'What is the average price of apartments for sale in Riyadh?')
        self.assertIn('SAR 825,000.00', average[0]['answer'])
        self.assertIn('Excluded 1 unusually low sale prices', average[0]['answer'])
        self.assertEqual(self.collection.find_one({'_id': row['_id']})['price'], 437)
        self.collection.delete_many({'_id': {'$ne': row['_id']}})
        average = query_catalog(self.collection, 'What is the average price of apartments for sale in Riyadh?')
        self.assertIn("can't calculate a reliable average", average[0]['answer'])

    def test_reimport_preserves_all_original_listing_fields(self):
        before = {row['_id']: row['raw'] for row in self.collection.find()}
        self.load_rows(self.directory.name, self.rows)
        after = {row['_id']: row['raw'] for row in self.collection.find()}
        self.assertEqual(before, after)


if __name__ == '__main__':
    unittest.main()
