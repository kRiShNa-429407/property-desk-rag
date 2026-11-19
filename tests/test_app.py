from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

import requests
from streamlit.testing.v1 import AppTest

from llm import ChatError, ask
from rag import DATA_FILE, Retriever, SEPARATOR, load_passages
from scrape import clean_url, extract_page


class RetrievalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.retriever = Retriever(load_passages())

    def test_project_amenities_are_retrieved(self):
        hits = self.retriever.search('What amenities does AIDA offer?')
        self.assertTrue(hits)
        self.assertIn('AIDA', hits[0].title)
        text = ' '.join(item.text.lower() for item in hits)
        self.assertIn('pool', text)
        self.assertIn('gym', text)
        self.assertTrue(all(item.source == 'DarGlobal' for item in hits))

    def test_wasalt_listing_workflow(self):
        hits = self.retriever.search('How do I list my property on Wasalt?', source='Wasalt')
        self.assertTrue(all(item.source == 'Wasalt' for item in hits))
        self.assertTrue(any('account' in item.text.lower() and 'upload' in item.text.lower() for item in hits))

    def test_unknown_terms_do_not_match(self):
        self.assertEqual(self.retriever.search('quantum neutrino spectroscopy'), [])

    def test_company_gdv_question_retrieves_corporate_figure(self):
        for question in ['what is the GROSS DEVELOPMENT VALUE of DarGlobal?',
                         'What is DarGlobal GDV?', 'What is Dar Global gross development value?']:
            with self.subTest(question=question):
                hits = self.retriever.search(question)
                self.assertTrue(hits)
                self.assertIn('US$23 billion', hits[0].text)
                self.assertIn('26 October 2026', hits[0].text)
                self.assertIn('/press/', hits[0].url)

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
    def setUp(self):
        self.passages = load_passages()[:1]
        self.client = Mock()
        self.client.post.return_value.status_code = 200
        self.client.post.return_value.json.return_value = {
            'choices': [{'message': {'content': 'A grounded answer [1].'}}], 'model': 'test-free-model'}

    def test_request_is_free_and_grounded(self):
        answer, model = ask('What is DarGlobal?', self.passages, 'test-key', session=self.client)
        request = self.client.post.call_args.kwargs
        self.assertEqual(request['json']['model'], 'openrouter/free')
        self.assertEqual(request['json']['provider']['max_price'], {'prompt': 0, 'completion': 0})
        self.assertNotIn('models', request['json'])
        self.assertIn(self.passages[0].text, request['json']['messages'][0]['content'])
        self.assertIn('[1]', answer)

    def test_rate_limit_has_no_paid_retry(self):
        self.client.post.return_value.status_code = 429
        with self.assertRaisesRegex(ChatError, 'rate limit'):
            ask('Question', self.passages, 'test-key', session=self.client)
        self.client.post.assert_called_once()

    def test_missing_key_never_makes_request(self):
        with self.assertRaises(ChatError):
            ask('Question', self.passages, '', session=self.client)
        self.client.post.assert_not_called()

    def test_network_errors_are_readable(self):
        self.client.post.side_effect = requests.Timeout()
        with self.assertRaisesRegex(ChatError, 'Could not reach'):
            ask('Question', self.passages, 'test-key', session=self.client)


class ScraperTests(unittest.TestCase):
    def test_external_and_query_urls_are_excluded(self):
        self.assertIsNone(clean_url('https://example.com/test', 'wasalt.sa'))
        self.assertIsNone(clean_url('https://wasalt.sa/en?page=2', 'wasalt.sa'))
        self.assertEqual(clean_url('https://wasalt.sa/en/corporate#about', 'wasalt.sa'), 'https://wasalt.sa/en/corporate')

    def test_challenge_is_not_saved_as_knowledge(self):
        with self.assertRaisesRegex(ValueError, 'access-control'):
            extract_page('<html><script src="/_Incapsula_Resource"></script></html>')

    def test_navigation_is_removed_but_main_text_remains(self):
        html = '<html><title>Project</title><nav>Navigation</nav><main>' + 'Public property description. ' * 20 + '</main></html>'
        title, text, _ = extract_page(html)
        self.assertEqual(title, 'Project')
        self.assertNotIn('Navigation', text)
        self.assertIn('Public property description', text)


class StreamlitTests(unittest.TestCase):
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
