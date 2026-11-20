import gzip
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from mongo_fixtures import MongoCase
from mongo_store import _connect
from rag import Passage, Retriever, load_passages
from wasalt import count_listings, document_from_cleaned, search_listings


def property_row(identifier=1, city='Riyadh', price=650000, updated='2026-11-18'):
    return {'id': identifier, 'updatedAt': updated,
            'propertyInfo': {'slug': f'apartment-{identifier}', 'title': 'Apartment for Sale',
                             'city': city, 'propertyFor': 'sale', 'salePrice': price,
                             'currencyType': 'SAR', 'propertySubType': 'Apartment'},
            'attributes': [{'name': 'Bedrooms', 'value': 3}],
            'featureAmenities': [{'name': 'Pool'}],
            'scoringData': {'original': 123}, 'propertyFiles': {'images': ['photo.jpg']}}


class WasaltTests(MongoCase):
    def test_empty_mongo_database_is_seeded_from_cleaned_records(self):
        with TemporaryDirectory() as directory:
            folder = Path(directory) / 'data' / 'wasalt-clean'
            folder.mkdir(parents=True)
            record = property_row()
            with gzip.open(folder / 'records.jsonl.gz', 'wt', encoding='utf-8') as handle:
                handle.write(json.dumps({'id': 'property:1', 'record': record}) + '\n')
            with patch('mongo_store.ROOT', Path(directory)):
                collection = _connect(os.getenv('MONGODB_TEST_URI', 'mongodb://127.0.0.1:27017'),
                                      self.collection.database.name, True)
            self.assertEqual(collection.count_documents({}), 1)
            self.assertEqual(collection.find_one()['raw'], record)
            self.assertGreaterEqual(len(collection.index_information()), 4)

    def test_city_total_counts_entire_collection_and_excludes_project_cards(self):
        with TemporaryDirectory() as directory:
            rows = [property_row(i) for i in range(1, 9)]
            rows[0]['propertyInfo']['propertyFor'] = 'rent'
            rows += [property_row(9, city='Jeddah'), property_row(10, city='Riyadh Al Khabra'),
                     {'id': 20, 'projectInfo': {'slug': 'homes', 'title': 'New Homes', 'city': 'Riyadh'}}]
            self.load_rows(directory, rows)
            summary = count_listings(self.collection, 'How many total properties in Riyadh?')
            self.assertIn('8 property listings in Riyadh', summary['text'])
            self.assertIn('Sale listings: 7', summary['text'])
            self.assertIn('Rental listings: 1', summary['text'])
            with patch('rag.get_collection', return_value=self.collection):
                retriever = Retriever([Passage('Wasalt', 'Help', 'https://wasalt.sa/en',
                                              'unknown', 'Create an account to list property.')])
                hits = retriever.search('How many total properties in Riyadh?', limit=1)
                self.assertIn('8 property listings', hits[0].text)

    def test_import_keeps_latest_record_and_full_original_data(self):
        with TemporaryDirectory() as directory:
            rows = [property_row(updated='2026-11-19'),
                    property_row(price=900000, updated='2026-11-01'), property_row(2, city='Jeddah')]
            result = self.load_rows(directory, rows)
            self.assertEqual(result['unique_listings'], 2)
            self.assertEqual(result['duplicates'], 1)
            hits = search_listings(self.collection, ['apartments', 'Riyadh'])
            self.assertEqual(len(hits), 1)
            self.assertIn('SAR 650000', hits[0]['text'])
            original = self.collection.find_one({'_id': 'property:1'})['raw']
            self.assertEqual(original['propertyFiles']['images'], ['photo.jpg'])
            self.assertEqual(original['scoringData'], {'original': 123})
            self.assertEqual(search_listings(self.collection, ['Riyadh', 'Jeddah']), [])

    def test_failed_import_preserves_existing_collection(self):
        with TemporaryDirectory() as directory:
            self.load_rows(directory, [property_row()])
            with self.assertRaisesRegex(ValueError, 'No usable'):
                self.load_rows(directory, [])
            self.assertEqual(self.collection.count_documents({}), 1)

    def test_mongo_listings_and_text_help_pages_remain_available(self):
        with TemporaryDirectory() as directory:
            folder = Path(directory)
            self.load_rows(directory, [property_row()])
            (folder / 'knowledge.txt').write_text(
                'Source: Wasalt\nURL: https://wasalt.sa/en\n\nCreate an account to list your property.',
                encoding='utf-8')
            with patch('rag.DATA_DIR', folder), patch('rag.get_collection', return_value=self.collection):
                retriever = Retriever(load_passages())
                hits = retriever.search('Show apartments for sale in Riyadh')
                self.assertTrue(any('/property/sale/' in item.url for item in hits))
                help_hits = retriever.search('How do I list my property on Wasalt?')
                self.assertIn('account', help_hits[0].text)
                self.assertEqual(retriever.search('Show apartments in Riyadh', source='DarGlobal'), [])

    def test_project_prices_are_kept_separate_from_individual_properties(self):
        row = {'id': 1, 'projectInfo': {'slug': 'project-1', 'title': 'New Homes', 'cityId': 328,
               'conversionPriceMin': 800000, 'conversionPriceMax': 1000000, 'currencyType': 'SAR'}}
        record = document_from_cleaned({'id': 'project:1', 'record': row})
        self.assertEqual(record['_id'], 'project:1')
        self.assertEqual(record['record_type'], 'project')
        self.assertIn('cityId=328', record['url'])
        self.assertIn('Project price from: SAR 800000', record['text'])
        self.assertIsNone(record['price'])
        self.assertEqual(record['raw'], row)


if __name__ == '__main__':
    unittest.main()
