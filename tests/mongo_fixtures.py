import gzip
import json
import os
from pathlib import Path
import unittest
from uuid import uuid4

from pymongo import MongoClient

from clean_wasalt import record_key
from wasalt import import_cleaned


class MongoCase(unittest.TestCase):
    def setUp(self):
        self.mongo = MongoClient(os.getenv('MONGODB_TEST_URI', 'mongodb://127.0.0.1:27017'),
                                 serverSelectionTimeoutMS=3000)
        name = 'property_desk_test_' + uuid4().hex
        self.collection = self.mongo[name]['wasalt']
        self.addCleanup(self.mongo.close)
        self.addCleanup(self.mongo.drop_database, name)

    def load_rows(self, directory, rows):
        path = Path(directory) / 'records.jsonl.gz'
        with gzip.open(path, 'wt', encoding='utf-8') as handle:
            for row in rows:
                handle.write(json.dumps({'id': record_key(row), 'record': row,
                                         'source_files': ['fixture.json']}) + '\n')
        return import_cleaned(Path(directory), self.collection)
