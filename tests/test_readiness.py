from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
from pymongo.errors import AutoReconnect

from catalog import query_catalog
from mongo_fixtures import MongoCase
from mongo_store import DatabaseNotReady, mongo_settings, sync_documents


class ReadinessTests(MongoCase):
    def test_production_rejects_missing_or_local_mongo_uri(self):
        with patch.dict('os.environ', {}, clear=True):
            for uri in ['', 'mongodb://127.0.0.1:27017', 'mongodb://localhost:27017', 'mongodb://mongo:27017']:
                with patch('mongo_store.dotenv_values', return_value={'MONGODB_URI': uri}):
                    with self.assertRaises(DatabaseNotReady):
                        mongo_settings(production=True)

    def test_production_accepts_external_uri_without_enabling_imports(self):
        with patch.dict('os.environ', {}, clear=True), patch('mongo_store.dotenv_values', return_value={
                'MONGODB_URI': 'mongodb+srv://user:private@example.mongodb.net/',
                'MONGODB_DATABASE': 'property_desk'}):
            uri, database, auto_seed = mongo_settings(production=True)
        self.assertEqual(database, 'property_desk')
        self.assertFalse(auto_seed)

    def test_empty_database_does_not_report_zero_website_listings(self):
        with self.assertRaises(DatabaseNotReady):
            query_catalog(self.collection, 'How many properties in Riyadh?')

    def test_sync_does_not_create_a_second_full_collection(self):
        self.collection.insert_many([{'_id': 'old', 'updated': '2026-01-01'},
                                     {'_id': 'keep', 'updated': '2026-11-19', 'value': 'latest'}])
        result = sync_documents(self.collection, [
            {'_id': 'keep', 'updated': '2026-01-01', 'value': 'older'},
            {'_id': 'new', 'updated': '2026-11-20', 'value': 42}], search_indexes=False)
        self.assertEqual(result['unique_listings'], 2)
        self.assertEqual(self.collection.find_one({'_id': 'keep'})['value'], 'latest')
        self.assertIsNone(self.collection.find_one({'_id': 'old'}))
        self.assertEqual(self.collection.database.list_collection_names(), ['wasalt'])

    def test_sync_validates_all_input_before_modifying_data(self):
        self.collection.insert_one({'_id': 'keep', 'value': 'original'})
        def broken():
            yield {'_id': 'keep', 'value': 'changed'}
            raise ValueError('Invalid input')
        with self.assertRaises(ValueError):
            sync_documents(self.collection, broken(), search_indexes=False)
        self.assertEqual(self.collection.find_one({'_id': 'keep'})['value'], 'original')

    def test_interrupted_sync_never_deletes_stale_records(self):
        self.collection.insert_one({'_id': 'keep', 'value': 'original'})
        wrapped = Mock(wraps=self.collection)
        calls = 0
        def write(operations, **options):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise AutoReconnect('Connection interrupted')
            return self.collection.bulk_write(operations, **options)
        wrapped.bulk_write.side_effect = write
        documents = ({'_id': f'new-{number}', 'updated': ''} for number in range(1001))
        with self.assertRaises(AutoReconnect):
            sync_documents(wrapped, documents, search_indexes=False)
        self.assertIsNotNone(self.collection.find_one({'_id': 'keep'}))
        wrapped.delete_many.assert_not_called()

    def test_mongo_updates_are_visible_without_cache_clearing(self):
        self.collection.insert_one({'_id': 'property:1', 'record_type': 'property', 'city': 'Riyadh',
                                    'district': '', 'purpose': 'sale', 'kind': 'Apartment', 'price': 600000,
                                    'currency': 'SAR', 'bedrooms': 3})
        answer = query_catalog(self.collection, 'What is the average price of apartments in Riyadh?')[0]['answer']
        self.assertIn('600,000.00', answer)
        self.collection.update_one({'_id': 'property:1'}, {'$set': {'price': 800000}})
        answer = query_catalog(self.collection, 'What is the average price of apartments in Riyadh?')[0]['answer']
        self.assertIn('800,000.00', answer)
