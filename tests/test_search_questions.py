import unittest

from search_questions import resolve_search_question


class SearchQuestionTests(unittest.TestCase):
    def history(self, question):
        return [{'role': 'user', 'content': question}]

    def test_follow_up_keeps_city_type_and_budget(self):
        history = self.history('Show me villas for sale in Riyadh under SAR 3 million')
        refined = resolve_search_question('Only those with at least five bedrooms.', history)
        self.assertIn('riyadh', refined)
        self.assertIn('under sar 3 million', refined)
        self.assertIn('at least 5 bedrooms', refined)
        history.append({'role': 'user', 'content': 'Only those with at least five bedrooms.',
                        'search_question': refined})
        cheapest = resolve_search_question('Which is the cheapest? Give me its listing link.', history)
        self.assertIn('at least 5 bedrooms', cheapest)
        self.assertIn('cheapest', cheapest)
        self.assertNotIn('listing link', cheapest)

    def test_new_budget_and_bedrooms_replace_previous_filters(self):
        history = self.history('How many villas for sale in Riyadh under SAR 3 million with minimum 5 bedrooms?')
        result = resolve_search_question('Only those under SAR 2 million with no more than four bedrooms', history)
        self.assertNotIn('3 million', result)
        self.assertNotIn('5 bedrooms', result)
        self.assertIn('under sar 2 million', result)
        self.assertIn('at most 4 bedrooms', result)

    def test_cheapest_replaces_count_intent(self):
        history = self.history('How many villas for sale in Riyadh under 3m?')
        result = resolve_search_question('Which is the cheapest?', history)
        self.assertNotIn('how many', result)
        self.assertIn('cheapest', result)

    def test_city_switch_and_topic_switch(self):
        history = self.history('Show apartments for sale in Riyadh under 1m')
        result = resolve_search_question('What about in Jeddah?', history)
        self.assertNotIn('riyadh', result)
        self.assertIn('jeddah', result)
        self.assertIn('under 1m', result)
        self.assertEqual(resolve_search_question('Where is AIDA located?', history), 'where is aida located')

    def test_independent_question_does_not_inherit_filters(self):
        history = self.history('Show villas in Riyadh under 3m')
        self.assertEqual(resolve_search_question('How many apartments in Jeddah?', history),
                         'how many apartments in jeddah')
        question = 'Which city has more properties for sale, Riyadh or Jeddah?'
        self.assertEqual(resolve_search_question(question, history), question.lower().rstrip('?'))
