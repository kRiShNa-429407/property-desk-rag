import argparse
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from pymongo import ReplaceOne

from mongo_store import get_collection


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def records_in(rows):
    for row in rows:
        if isinstance(row, list):
            yield from records_in(row)
        elif isinstance(row, dict):
            yield row
        else:
            raise ValueError('A listing array contains an unsupported value; no records were discarded.')


def record_key(row):
    kind = 'project' if row.get('projectInfo') and not row.get('propertyInfo') else 'property'
    identifier = row.get('id')
    if identifier is None:
        return 'unidentified:' + hashlib.sha256(canonical(row).encode('utf-8')).hexdigest()
    return f'{kind}:{identifier}'


def record_time(row):
    try:
        timestamp = datetime.fromisoformat(row.get('updatedAt') or row.get('publishedAt') or row.get('createdAt') or '')
        return timestamp.replace(tzinfo=timezone.utc) if timestamp.tzinfo is None else timestamp
    except (ValueError, TypeError):
        return datetime.min.replace(tzinfo=timezone.utc)


def merge_records(primary, secondary):
    if isinstance(primary, dict) and isinstance(secondary, dict):
        result = deepcopy(secondary)
        for key, value in primary.items():
            result[key] = merge_records(value, secondary[key]) if key in secondary else deepcopy(value)
        return result
    if isinstance(primary, list) and isinstance(secondary, list):
        key = next((key for key in ['key', 'id'] if primary and secondary and
                    all(isinstance(item, dict) and key in item for item in primary + secondary)), None)
        if key:
            result = {canonical(item[key]): deepcopy(item) for item in primary}
            for item in secondary:
                identity = canonical(item[key])
                result[identity] = merge_records(result[identity], item) if identity in result else deepcopy(item)
            return list(result.values())
        result, seen = [], set()
        for item in primary + secondary:
            identity = canonical(item)
            if identity not in seen:
                result.append(deepcopy(item))
                seen.add(identity)
        return result
    # Explicit null, false and zero in the newest record are meaningful values.
    return deepcopy(primary)


def clean_exports(source, output, database):
    source, output = Path(source).resolve(), Path(output).resolve()
    if output.is_relative_to(source):
        raise ValueError('Write cleaned data outside the source folder to keep the originals separate.')
    files = sorted(source.rglob('*.json'))
    if not files:
        raise ValueError('No JSON files were found in the Wasalt export folder.')
    output.mkdir(parents=True, exist_ok=True)
    work = database[f'wasalt_cleaning_{uuid4().hex}']
    token = uuid4().hex
    names = ['records.jsonl.gz', 'history.jsonl.gz', 'metadata.jsonl.gz']
    temporary = {name: output / f'.{token}-{name}' for name in names}
    counts = {'json_files': len(files), 'listing_files': 0, 'metadata_files': 0,
              'input_records': 0, 'duplicate_entries': 0, 'exact_duplicates': 0,
              'history_records': 0, 'nested_records': 0, 'input_bytes': 0}
    digest = hashlib.sha256()
    try:
        with gzip.open(temporary['history.jsonl.gz'], 'wt', encoding='utf-8') as history, \
                gzip.open(temporary['metadata.jsonl.gz'], 'wt', encoding='utf-8') as metadata:
            for path in files:
                raw = path.read_bytes()
                relative = path.relative_to(source).as_posix()
                file_hash = hashlib.sha256(raw).hexdigest()
                digest.update((relative + ':' + file_hash + '\n').encode())
                counts['input_bytes'] += len(raw)
                payload = json.loads(raw.decode('utf-8-sig'))
                data = payload.get('data') if isinstance(payload, dict) else None
                rows = data.get('properties') if isinstance(data, dict) else None
                if not isinstance(rows, list):
                    counts['metadata_files'] += 1
                    metadata.write(canonical({'source_file': relative, 'sha256': file_hash, 'data': payload}) + '\n')
                    continue
                counts['listing_files'] += 1
                flattened = list(records_in(rows))
                counts['nested_records'] += len(flattened) - sum(isinstance(row, dict) for row in rows)
                identifiers = [record_key(row) for row in flattened]
                existing = {row['_id']: row for row in work.find({'_id': {'$in': identifiers}})}
                operations = {}
                for row, identifier in zip(flattened, identifiers):
                    counts['input_records'] += 1
                    row_hash = hashlib.sha256(canonical(row).encode('utf-8')).hexdigest()
                    current = existing.get(identifier)
                    if current:
                        counts['duplicate_entries'] += 1
                        if row_hash in current['hashes']:
                            counts['exact_duplicates'] += 1
                        else:
                            if len(current['hashes']) == 1:
                                history.write(canonical({'id': identifier, 'source_files': current['sources'],
                                                         'record': current['record']}) + '\n')
                                counts['history_records'] += 1
                            history.write(canonical({'id': identifier, 'source_files': [relative], 'record': row}) + '\n')
                            counts['history_records'] += 1
                            newer = record_time(row) > record_time(current['record'])
                            current['record'] = merge_records(row, current['record']) if newer else merge_records(current['record'], row)
                            current['hashes'].append(row_hash)
                        if relative not in current['sources']:
                            current['sources'].append(relative)
                    else:
                        current = {'_id': identifier, 'record': row, 'hashes': [row_hash], 'sources': [relative]}
                        existing[identifier] = current
                    operations[identifier] = ReplaceOne({'_id': identifier}, current, upsert=True)
                if operations:
                    work.bulk_write(list(operations.values()), ordered=True)
                # Preserve pagination and other response metadata as well as the listings.
                page_metadata = {key: value for key, value in payload.items() if key != 'data'}
                page_metadata['data'] = {key: value for key, value in data.items() if key != 'properties'}
                metadata.write(canonical({'source_file': relative, 'sha256': file_hash, 'data': page_metadata}) + '\n')
        counts['unique_records'] = work.count_documents({})
        if counts['input_records'] - counts['duplicate_entries'] != counts['unique_records']:
            raise ValueError('Deduplication verification failed.')
        with gzip.open(temporary['records.jsonl.gz'], 'wt', encoding='utf-8') as cleaned:
            for document in work.find().sort('_id', 1):
                cleaned.write(canonical({'id': document['_id'], 'record': document['record'],
                                         'source_files': document['sources']}) + '\n')
        for name in names:
            temporary[name].replace(output / name)
        counts['source_manifest_sha256'] = digest.hexdigest()
        counts['artifacts'] = {name: {'bytes': (output / name).stat().st_size,
                                    'sha256': hashlib.sha256((output / name).read_bytes()).hexdigest()} for name in names}
        counts['policy'] = 'Originals unchanged; newest scalar values win; missing keys and unique list values merge; distinct conflicting versions retained in history.'
        (output / 'report.json').write_text(json.dumps(counts, indent=2), encoding='utf-8')
        return counts
    finally:
        work.drop()
        for path in temporary.values():
            path.unlink(missing_ok=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Deduplicate Wasalt JSON exports without dropping listing fields.')
    parser.add_argument('source', type=Path)
    parser.add_argument('--output', type=Path, default=Path('data/wasalt-clean'))
    args = parser.parse_args()
    print(json.dumps(clean_exports(args.source, args.output, get_collection(initialize=False).database), indent=2))
