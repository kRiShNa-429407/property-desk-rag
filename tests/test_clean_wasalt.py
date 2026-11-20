import gzip
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4

from clean_wasalt import clean_exports, merge_records, records_in
from mongo_store import get_collection


class CleaningTests(unittest.TestCase):
    def test_merge_keeps_all_fields_and_meaningful_latest_values(self):
        old = {'name': 'Home', 'price': 100, 'flag': True, 'rooms': 4,
               'images': ['one.jpg'], 'attributes': [{'key': 'beds', 'value': 3, 'unit': 'rooms'}]}
        new = {'price': None, 'flag': False, 'rooms': 0, 'images': ['two.jpg'],
               'attributes': [{'key': 'beds', 'value': 4}], 'custom': {'important': 'keep'}}
        result = merge_records(new, old)
        self.assertEqual(result['name'], 'Home')
        self.assertIsNone(result['price'])
        self.assertIs(result['flag'], False)
        self.assertEqual(result['rooms'], 0)
        self.assertEqual(result['images'], ['two.jpg', 'one.jpg'])
        self.assertEqual(result['attributes'], [{'key': 'beds', 'value': 4, 'unit': 'rooms'}])
        self.assertEqual(result['custom'], new['custom'])
        self.assertEqual(old['price'], 100)

    def test_nested_listings_are_recovered(self):
        self.assertEqual(list(records_in([[{'id': 1}], [[{'id': 2}]], {'id': 3}])),
                         [{'id': 1}, {'id': 2}, {'id': 3}])

    def test_files_are_deduplicated_with_history_and_metadata_preserved(self):
        client = get_collection().database.client
        name = 'property_desk_test_' + uuid4().hex
        database = client[name]
        self.addCleanup(client.drop_database, name)
        with TemporaryDirectory() as directory:
            root = Path(directory) / 'source'
            root.mkdir()
            old = {'id': 1, 'updatedAt': '2026-01-01T00:00:00Z',
                   'propertyInfo': {'salePrice': 100, 'title': 'Home'}, 'images': ['old.jpg']}
            new = {'id': 1, 'updatedAt': '2026-11-19T00:00:00Z',
                   'propertyInfo': {'salePrice': None, 'currency': 'SAR'}, 'images': ['new.jpg']}
            payloads = {'one.json': {'status': True, 'data': {'properties': [old, old], 'count': 50}},
                        'two.json': {'data': {'properties': [new, [{'id': 2, 'details': {'keep': 0}}]]}},
                        'cities.json': [{'id': 1, 'name': 'Riyadh'}]}
            originals = {}
            for filename, payload in payloads.items():
                raw = json.dumps(payload).encode()
                (root / filename).write_bytes(raw)
                originals[filename] = raw
            output = Path(directory) / 'clean'
            report = clean_exports(root, output, database)
            self.assertEqual(report['input_records'], 4)
            self.assertEqual(report['unique_records'], 2)
            self.assertEqual(report['duplicate_entries'], 2)
            self.assertEqual(report['exact_duplicates'], 1)
            self.assertEqual(report['nested_records'], 1)
            with gzip.open(output / 'records.jsonl.gz', 'rt', encoding='utf-8') as handle:
                rows = {row['id']: row for row in map(json.loads, handle)}
            self.assertIsNone(rows['property:1']['record']['propertyInfo']['salePrice'])
            self.assertEqual(rows['property:1']['record']['propertyInfo']['title'], 'Home')
            self.assertEqual(rows['property:2']['record']['details']['keep'], 0)
            with gzip.open(output / 'history.jsonl.gz', 'rt', encoding='utf-8') as handle:
                history = [row['record'] for row in map(json.loads, handle)]
            self.assertIn(old, history)
            self.assertIn(new, history)
            with gzip.open(output / 'metadata.jsonl.gz', 'rt', encoding='utf-8') as handle:
                metadata = {row['source_file']: row['data'] for row in map(json.loads, handle)}
            self.assertEqual(metadata['cities.json'], payloads['cities.json'])
            self.assertEqual(metadata['one.json']['data']['count'], 50)
            for filename, raw in originals.items():
                self.assertEqual((root / filename).read_bytes(), raw)


if __name__ == '__main__':
    unittest.main()
