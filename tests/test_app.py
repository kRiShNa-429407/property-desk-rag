from pathlib import Path
import json
from itertools import count
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

import requests
from streamlit.testing.v1 import AppTest

from llm import ChatError, ask, next_api_key
from rag import Retriever, SEPARATOR, load_passages
from scrape import extract_page


class RetrievalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.retriever = Retriever(load_passages())

    def test_project_amenities_are_retrieved(self):
        hits = self.retriever.search('What amenities does AIDA offer?')
        self.assertTrue(hits)
        self.assertIn('AIDA', hits[0].text)
        text = ' '.join(item.text.lower() for item in hits)
        self.assertIn('pool', text)
        self.assertIn('fitness', text)
        self.assertTrue(all(item.source == 'DarGlobal' for item in hits))

    def test_wasalt_listing_workflow(self):
        hits = self.retriever.search('How do I list my property on Wasalt?', source='Wasalt')
        self.assertTrue(all(item.source == 'Wasalt' for item in hits))
        self.assertTrue(any('account' in item.text.lower() and 'upload' in item.text.lower() for item in hits))

    def test_unknown_terms_do_not_match(self):
        self.assertEqual(self.retriever.search('quantum neutrino spectroscopy'), [])

    def test_conversational_company_overview_returns_sources(self):
        hits = self.retriever.search('What is DarGlobal and what does it do?')
        self.assertTrue(hits)
        self.assertTrue(all(item.source == 'DarGlobal' for item in hits))

    def test_company_gdv_question_retrieves_corporate_figure(self):
        for question in ['what is the GROSS DEVELOPMENT VALUE of DarGlobal?',
                         'What is DarGlobal GDV?', 'What is Dar Global gross development value?']:
            with self.subTest(question=question):
                hits = self.retriever.search(question)
                self.assertTrue(hits)
                text = ' '.join(item.text for item in hits)
                self.assertIn('23', text)
                self.assertIn('gross development value', text.lower())
                self.assertTrue(all(item.source == 'DarGlobal' for item in hits))

    def test_chunk_overlap_and_source_metadata(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'knowledge.txt'
            text = ' '.join(f'word{i}' for i in range(22))
            path.write_text('Source: DarGlobal\nTitle: Demo\nURL: https://darglobal.co.uk/\nCollected: 2026-11-18\n\n' + text, encoding='utf-8')
            chunks = load_passages(path, chunk_size=10, overlap=2)
            self.assertEqual(len(chunks), 3)
            self.assertEqual(chunks[0].text.split()[-2:], chunks[1].text.split()[:2])
            self.assertEqual(chunks[-1].text.split()[-1], 'word21')
            self.assertEqual(chunks[-1].source, 'DarGlobal')


class ModelTests(unittest.TestCase):
    def test_key_rotation_ignores_empty_and_duplicate_keys(self):
        with patch('llm._key_index', count()):
            keys = ['', ' first ', 'second', 'first', 'third', 'fourth']
            self.assertEqual([next_api_key(keys) for _ in range(6)],
                             ['first', 'second', 'third', 'fourth', 'first', 'second'])
            self.assertEqual(next_api_key(['', '  ']), '')

    def setUp(self):
        cooldowns = patch('llm._key_cooldowns', {})
        cooldowns.start()
        self.addCleanup(cooldowns.stop)
        rotation = patch('llm._key_index', count())
        rotation.start()
        self.addCleanup(rotation.stop)
        self.passages = load_passages()[:1]
        self.client = Mock()
        self.client.post.return_value.status_code = 200
        self.client.post.return_value.json.return_value = {
            'choices': [{'message': {'content': 'A grounded answer [1].'}}], 'model': 'test-free-model'}

    def test_request_is_free_and_grounded(self):
        answer, model = ask('What is DarGlobal?', self.passages, 'test-key', session=self.client)
        request = self.client.post.call_args.kwargs
        self.assertEqual(request['json']['model'], 'inclusionai/ling-3.2-flash:free')
        self.assertFalse(request['json']['reasoning']['enabled'])
        self.assertEqual(request['json']['provider']['max_price'], {'prompt': 0, 'completion': 0})
        self.assertEqual(request['json']['models'],
                         ['nvidia/nemotron-3.5-lightning:free', 'google/gemma-4-31b-it:free'])
        self.assertTrue(all(model.endswith(':free') for model in request['json']['models']))
        context = request['json']['messages'][0]['content'].split('Current retrieved records:\n', 1)[1]
        self.assertEqual(self.passages[0].text, json.loads(context)[0]['text'])
        self.assertEqual(answer, 'A grounded answer.')

    def test_citation_cleanup_preserves_steps_dates_and_prices(self):
        self.client.post.return_value.json.return_value = {'choices': [{'message': {
            'content': '1. **Create an account** [1][2].\n2. Listed price: **SAR 900,000** in 2026 [1, 2].'
        }}]}
        answer, _ = ask('Question', self.passages, 'test-key', session=self.client)
        self.assertEqual(answer, '1. **Create an account**.\n2. Listed price: **SAR 900,000** in 2026.')

    def test_technical_opening_and_reference_markers_are_removed(self):
        self.client.post.return_value.json.return_value = {'choices': [{'message': {
            'content': 'Based on the provided passages, **DarGlobal** is a developer [1][3].\n\nIt develops properties.'
        }}]}
        answer, _ = ask('What is DarGlobal?', self.passages, 'test-key', session=self.client)
        self.assertEqual(answer, '**DarGlobal** is a developer.\n\nIt develops properties.')

    def test_rate_limit_has_no_paid_retry(self):
        self.client.post.return_value.status_code = 429
        with self.assertRaisesRegex(ChatError, 'rate limit'):
            ask('Question', self.passages, 'test-key', session=self.client)
        self.client.post.assert_called_once()

    def test_limited_key_retries_another_and_is_skipped_on_next_question(self):
        limited = Mock(status_code=429, headers={})
        limited.json.return_value = {'error': {'message': 'Rate limit exceeded: free-models-per-day'}}
        success = self.client.post.return_value
        self.client.post.side_effect = [limited, success, success]
        keys = ['first', 'second']
        with patch('llm.monotonic', return_value=100):
            answer, _ = ask('Question', self.passages, keys, session=self.client)
            self.assertEqual(answer, 'A grounded answer.')
            ask('Another question', self.passages, keys, session=self.client)
        self.assertEqual([call.kwargs['headers']['Authorization'] for call in self.client.post.call_args_list],
                         ['Bearer first', 'Bearer second', 'Bearer second'])

    def test_all_limited_keys_stop_after_one_attempt_each(self):
        self.client.post.return_value.status_code = 429
        self.client.post.return_value.headers = {}
        self.client.post.return_value.json.return_value = {'error': {'message': 'Too many requests'}}
        with patch('llm.monotonic', return_value=100):
            for question in ['Question', 'Next question']:
                with self.assertRaisesRegex(ChatError, 'rate limit'):
                    ask(question, self.passages, ['first', 'second', 'first'], session=self.client)
        self.assertEqual(self.client.post.call_count, 2)

    def test_retry_after_expiry_makes_key_available_again(self):
        self.client.post.return_value.status_code = 429
        self.client.post.return_value.headers = {'Retry-After': '120'}
        self.client.post.return_value.json.return_value = {'error': {'message': 'Too many requests'}}
        with patch('llm.monotonic', return_value=100):
            with self.assertRaises(ChatError):
                ask('Question', self.passages, ['first'], session=self.client)
        with patch('llm.monotonic', return_value=219):
            self.assertEqual(next_api_key(['first']), '')
        with patch('llm.monotonic', return_value=220):
            self.assertEqual(next_api_key(['first']), 'first')

    def test_authentication_error_does_not_retry_other_keys(self):
        self.client.post.return_value.status_code = 401
        with self.assertRaisesRegex(ChatError, 'authentication'):
            ask('Question', self.passages, ['first', 'second'], session=self.client)
        self.client.post.assert_called_once()

    def test_missing_key_never_makes_request(self):
        with self.assertRaises(ChatError):
            ask('Question', self.passages, '', session=self.client)
        self.client.post.assert_not_called()

    def test_daily_account_limit_is_explained_without_retry(self):
        self.client.post.return_value.status_code = 429
        self.client.post.return_value.json.return_value = {
            'error': {'message': 'Rate limit exceeded: free-models-per-day.'}}
        with self.assertRaisesRegex(ChatError, 'daily limit resets'):
            ask('Question', self.passages, 'test-key', session=self.client)
        self.client.post.assert_called_once()

    def test_network_errors_are_readable(self):
        self.client.post.side_effect = requests.Timeout()
        with self.assertRaisesRegex(ChatError, 'Could not reach'):
            ask('Question', self.passages, 'test-key', session=self.client)


class ScraperTests(unittest.TestCase):
    def test_challenge_is_not_saved_as_knowledge(self):
        with self.assertRaisesRegex(ValueError, 'access-control'):
            extract_page('<html><script src="/_Incapsula_Resource"></script></html>')

    def test_security_script_does_not_hide_a_real_rendered_page(self):
        html = '<html><script src="/_Incapsula_Resource"></script><main>' + 'Actual public company information. ' * 30 + '</main></html>'
        _, text, _ = extract_page(html)
        self.assertIn('Actual public company information', text)

    def test_navigation_is_removed_but_main_text_remains(self):
        html = '<html><title>Project</title><nav>Navigation</nav><main>' + 'Public property description. ' * 20 + '</main></html>'
        title, text, _ = extract_page(html)
        self.assertEqual(title, 'Project')
        self.assertNotIn('Navigation', text)
        self.assertIn('Public property description', text)


class StreamlitTests(unittest.TestCase):
    def test_two_browser_sessions_do_not_share_chat_messages(self):
        response = Mock(status_code=200)
        response.json.return_value = {'choices': [{'message': {'content': 'AIDA is in Oman.'}}]}
        with patch('dotenv.dotenv_values', return_value={'OPENROUTER_API_KEY': 'test-key'}), \
                patch('llm.requests.post', return_value=response):
            filename = str(Path(__file__).resolve().parents[1] / 'app.py')
            first = AppTest.from_file(filename).run(timeout=30)
            second = AppTest.from_file(filename).run(timeout=30)
            first.chat_input[0].set_value('Where is AIDA located?').run(timeout=30)
            self.assertEqual(len(first.session_state.messages), 2)
            self.assertEqual(len(second.session_state.messages), 0)
            first.button[0].click().run(timeout=30)
            self.assertEqual(len(first.session_state.messages), 0)
            self.assertEqual(len(second.session_state.messages), 0)

    def test_mongo_connection_error_has_a_readable_message(self):
        from pymongo.errors import ServerSelectionTimeoutError
        with patch('rag.get_collection', side_effect=ServerSelectionTimeoutError('unavailable')):
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app.py')).run(timeout=30)
            app.chat_input[0].set_value('How many properties in Riyadh?').run(timeout=30)
            self.assertEqual(len(app.exception), 0)
            self.assertTrue(any('temporarily unavailable' in item.value for item in app.error))

    def test_wasalt_count_does_not_require_an_api_key_or_model(self):
        with patch('dotenv.dotenv_values', return_value={}), \
                patch('llm.requests.post') as post:
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app.py')).run(timeout=30)
            app.chat_input[0].set_value('How many properties are there in Riyadh?').run(timeout=30)
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(len(app.error), 0)
            self.assertTrue(any('property listings for sale in Riyadh' in item.value for item in app.markdown))
            post.assert_not_called()

    def test_numbered_keys_rotate_on_chat_requests(self):
        response = Mock(status_code=200)
        response.json.return_value = {'choices': [{'message': {'content': 'DarGlobal develops properties.'}}]}
        with patch.dict('os.environ', {}, clear=True), \
                patch('dotenv.dotenv_values', return_value={
                    'OPENROUTER_API_KEY_1': 'first', 'OPENROUTER_API_KEY_2': 'second'}), \
                patch('llm._key_index', count()), \
                patch('llm.requests.post', return_value=response) as post:
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app.py')).run(timeout=30)
            post.assert_not_called()
            app.chat_input[0].set_value('What is DarGlobal?').run(timeout=30)
            app.chat_input[0].set_value('What amenities does AIDA offer?').run(timeout=30)
            self.assertEqual(len(app.exception), 0)
            self.assertEqual([call.kwargs['headers']['Authorization'] for call in post.call_args_list],
                             ['Bearer first', 'Bearer second'])

    def test_chat_loads_server_key_and_clears(self):
        response = Mock(status_code=200)
        response.json.return_value = {'choices': [{'message': {'content': 'AIDA has swimming pools [1].'}}]}
        with patch.dict('os.environ', {'OPENROUTER_API_KEY': ''}), \
                patch('dotenv.dotenv_values', return_value={'OPENROUTER_API_KEY': 'test-env-key'}) as dotenv, \
                patch('llm.requests.post', return_value=response) as post:
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app.py')).run(timeout=30)
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(len(app.text_input), 0)
            self.assertEqual(len(app.sidebar), 0)
            dotenv.assert_called_with(Path(__file__).resolve().parents[1] / '.env')
            app.chat_input[0].set_value('What amenities does AIDA offer?').run(timeout=30)
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(len(app.chat_message), 2)
            self.assertEqual(post.call_args.kwargs['headers']['Authorization'], 'Bearer test-env-key')
            self.assertTrue(any('swimming pools' in item.value for item in app.markdown))
            app.button[0].click().run()
            self.assertEqual(len(app.chat_message), 0)

    def test_missing_key_error_is_visible(self):
        with patch.dict('os.environ', {'OPENROUTER_API_KEY': ''}), \
                patch('dotenv.dotenv_values', return_value={}):
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app.py')).run(timeout=30)
            app.chat_input[0].set_value('What amenities does AIDA offer?').run(timeout=30)
            self.assertEqual(len(app.exception), 0)
            self.assertTrue(any('not configured' in item.value for item in app.error))


if __name__ == '__main__':
    unittest.main()
