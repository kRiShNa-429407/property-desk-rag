from functools import lru_cache
import json
import os
from pathlib import Path
from tempfile import SpooledTemporaryFile
from threading import Lock
from urllib.parse import urlsplit
from uuid import uuid4

from dotenv import dotenv_values
from pymongo import IndexModel, MongoClient, UpdateOne
from pymongo.collation import Collation
from pymongo.errors import PyMongoError


ROOT = Path(__file__).parent
COLLATION = Collation(locale='en', strength=2)
_seed_lock = Lock()


class DatabaseNotReady(PyMongoError):
    pass


def mongo_settings(production=None):
    values = dotenv_values(ROOT / '.env')
    def setting(name, default=''):
        return os.getenv(name) or values.get(name) or default
    production = setting('APP_ENV') == 'production' if production is None else production
    uri = setting('MONGODB_URI', '' if production else 'mongodb://127.0.0.1:27017')
    hosts = urlsplit(uri).netloc.rsplit('@', 1)[-1].lower().split(',')
    if production and (not uri or any(host.split(':')[0] in {'localhost', '127.0.0.1', 'mongo', '['} for host in hosts)):
        raise DatabaseNotReady('Production requires an external MONGODB_URI; the local database cannot be used.')
    return uri, setting('MONGODB_DATABASE', 'property_desk'), setting('MONGODB_AUTO_SEED', 'false').lower() == 'true'


def sync_documents(collection, documents, search_indexes=True, allow_empty=False):
    """Validate first, update in place, and remove stale IDs only after every batch succeeds."""
    run, identifiers, processed = uuid4().hex, set(), 0
    with SpooledTemporaryFile(mode='w+t', encoding='utf-8', max_size=2 * 1024 * 1024) as source:
        for document in documents:
            identifiers.add(document['_id'])
            source.write(json.dumps(document, ensure_ascii=False) + '\n')
            processed += 1
        if not identifiers and not allow_empty:
            raise ValueError('No usable Wasalt listings were found; the existing collection is unchanged.')
        if search_indexes:
            create_indexes(collection)
        source.seek(0)
        operations = []
        for line in source:
            document = json.loads(line)
            document['_import_run'] = run
            operations.append(UpdateOne({'_id': document['_id']}, [{'$replaceWith': {'$cond': [
                {'$or': [{'$eq': [{'$type': '$updated'}, 'missing']},
                         {'$lt': ['$updated', document.get('updated', '')]}]},
                {'$literal': document}, {'$mergeObjects': ['$$ROOT', {'_import_run': run}]}]}}], upsert=True))
            if len(operations) == 1000:
                collection.bulk_write(operations, ordered=True)
                operations = []
        if operations:
            collection.bulk_write(operations, ordered=True)
        if collection.count_documents({'_import_run': run}) != len(identifiers):
            raise ValueError('Import verification failed; stale records were not removed. Retry the import.')
        collection.delete_many({'_import_run': {'$ne': run}})
    return {'unique_listings': len(identifiers), 'duplicates': processed - len(identifiers)}


def create_indexes(collection):
    for name, details in collection.index_information().items():
        if details.get('weights') and set(details['weights']) != {'title', 'city', 'district', 'kind'}:
            collection.drop_index(name)
    collection.create_indexes([
        IndexModel([('city', 1), ('kind', 1), ('purpose', 1)], collation=COLLATION),
        IndexModel([('city', 1), ('kind', 1), ('price', 1), ('bedrooms', 1)], collation=COLLATION),
        IndexModel([('title', 'text'), ('city', 'text'), ('district', 'text'), ('kind', 'text')], name='wasalt_search_text'),
    ])


def replace_documents(collection, documents, search_indexes=True, allow_empty=False):
    staging = collection.database[f'{collection.name}_import_{uuid4().hex}']
    processed, operations = 0, []
    try:
        for document in documents:
            processed += 1
            operations.append(UpdateOne({'_id': document['_id']}, [{'$replaceWith': {'$cond': [
                {'$or': [{'$eq': [{'$type': '$updated'}, 'missing']},
                         {'$lt': ['$updated', document.get('updated', '')]}]},
                {'$literal': document}, '$$ROOT']}}], upsert=True))
            if len(operations) == 1000:
                staging.bulk_write(operations, ordered=True)
                operations = []
        if operations:
            staging.bulk_write(operations, ordered=True)
        total = staging.count_documents({})
        if not total and not allow_empty:
            raise ValueError('No usable Wasalt listings were found; the existing collection is unchanged.')
        if not total:
            collection.database.create_collection(staging.name)
        if search_indexes:
            create_indexes(staging)
        staging.rename(collection.name, dropTarget=True)
        return {'unique_listings': total, 'duplicates': processed - total}
    except BaseException:
        staging.drop()
        raise


@lru_cache(maxsize=4)
def _connect(uri, database_name, initialize, timeout_ms=15000):
    print('PropertyDesk: creating database client', flush=True)
    client = MongoClient(uri, serverSelectionTimeoutMS=5000, connectTimeoutMS=5000,
                         timeoutMS=timeout_ms, maxPoolSize=20, maxIdleTimeMS=60000,
                         waitQueueTimeoutMS=5000, appname='Property Desk')
    collection = client[database_name]['wasalt']
    print('PropertyDesk: database client created', flush=True)
    seed = ROOT / 'data' / 'wasalt-clean' / 'records.jsonl.gz'
    with _seed_lock:
        if initialize and collection.estimated_document_count() == 0 and seed.is_file():
            from wasalt import import_cleaned
            import_cleaned(seed.parent, collection)
    return collection


def get_collection(initialize=None, timeout_ms=15000):
    uri, database, auto_seed = mongo_settings()
    initialize = auto_seed if initialize is None else initialize
    return _connect(uri, database, initialize, timeout_ms)
