import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from rag import SEPARATOR, clean_text, load_passages


class JsonKnowledgeTests(unittest.TestCase):
    def test_scraped_pages_use_text_once_and_skip_failed_pages(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'darglobal.json'
            pages = [
                {'url': 'https://darglobal.co.uk/aida#top', 'title': 'AIDA', 'status': 200,
                 'text': 'AIDA has a golf course.', 'paragraphs': ['Do not index this duplicate field.']},
                {'url': 'https://darglobal.co.uk/blocked', 'status': 403, 'text': 'Access denied'},
            ]
            path.write_text(json.dumps({'pages': pages}), encoding='utf-8')
            passages = load_passages(path)
            self.assertEqual(len(passages), 1)
            self.assertEqual(passages[0].text, 'AIDA has a golf course.')
            self.assertEqual(passages[0].source, 'DarGlobal')
            self.assertEqual(passages[0].url, 'https://darglobal.co.uk/aida')
            self.assertEqual(passages[0].collected, 'unknown')

    def test_duplicate_pages_and_chunks_are_indexed_once(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'darglobal.json'
            pages = [
                {'url': 'https://darglobal.co.uk/one', 'text': 'one two three four five six'},
                {'url': 'https://darglobal.co.uk/copy', 'text': 'ONE TWO THREE FOUR FIVE SIX'},
                {'url': 'https://darglobal.co.uk/two', 'text': 'one two three seven eight nine'},
            ]
            original = json.dumps(pages)
            path.write_text(original, encoding='utf-8')
            passages = load_passages(path, chunk_size=3, overlap=0)
            self.assertEqual([p.text for p in passages], ['one two three', 'four five six', 'seven eight nine'])
            self.assertEqual(path.read_text(encoding='utf-8'), original)

    def test_json_replaces_only_its_own_source(self):
        with TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / 'darglobal.json').write_text(json.dumps([
                {'url': 'https://darglobal.co.uk/', 'text': 'Current company information.'}
            ]), encoding='utf-8')
            (folder / 'knowledge.txt').write_text(SEPARATOR.join([
                'Source: DarGlobal\nURL: https://darglobal.co.uk/old\n\nOld company information.',
                'Source: Wasalt\nURL: https://wasalt.sa/\n\nCreate an account to list property.'
            ]), encoding='utf-8')
            with patch('rag.DATA_DIR', folder):
                passages = load_passages()
            self.assertEqual([p.source for p in passages], ['DarGlobal', 'Wasalt'])
            self.assertFalse(any('Old company' in p.text for p in passages))

    def test_cleaning_removes_controls_but_keeps_short_repeated_values(self):
        line = 'This long description contains useful facts about homes and facilities.'
        text = clean_text(f'Register Interest\n{line}\n{line}\n2\nBedrooms\n2\nBathrooms')
        self.assertEqual(text.count(line), 1)
        self.assertNotIn('Register Interest', text)
        self.assertEqual(text.splitlines().count('2'), 2)
        self.assertEqual(clean_text('<main><p>Homes &amp; gardens</p></main><script>tracking</script>'),
                         'Homes & gardens')

    def test_invalid_json_shape_has_a_readable_error(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'darglobal.json'
            path.write_text('{"pages": {}}', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'pages list'):
                load_passages(path)


if __name__ == '__main__':
    unittest.main()
