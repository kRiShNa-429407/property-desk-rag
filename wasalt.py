import argparse
import gzip
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlencode

from catalog import extra_fields, query_catalog
from mongo_store import get_collection, replace_documents, sync_documents


def document_from_cleaned(entry):
    row = entry['record']
    info = row.get('propertyInfo') or row.get('projectInfo') or {}
    identity = entry['id']
    project = identity.startswith('project:')
    title = info.get('title') or f"Wasalt listing {row.get('id', '')}"
    purpose = info.get('propertyFor') or ''
    slug = info.get('slug')
    if slug and not project and purpose in {'sale', 'rent'}:
        url = f'https://wasalt.sa/en/property/{purpose}/{slug}'
    else:
        filters = {'countryId': 1, 'propertyFor': purpose or 'sale', 'type': 'residential'}
        if info.get('cityId'):
            filters['cityId'] = info['cityId']
        url = 'https://wasalt.sa/en/sale/search?' + urlencode(filters)
    lines = [title, f"Listing ID: {row.get('id', '')}"]
    for label, key in [('Property type', 'propertySubType'), ('Purpose', 'propertyFor'),
                       ('Address', 'address'), ('City', 'city'), ('District', 'district'),
                       ('Country', 'country'), ('Furnishing', 'furnishingType')]:
        if info.get(key):
            lines.append(f'{label}: {info[key]}')
    currency = info.get('currencyType') or info.get('conversionUnit') or ''
    for label, key in [('Listed sale price', 'salePrice'), ('Listed rent', 'expectedRent'),
                       ('Project price from', 'conversionPriceMin'), ('Project price to', 'conversionPriceMax')]:
        value = info.get(key)
        if value not in (None, '', 0, '0'):
            frequency = f" per {info['rentFreq']}" if key == 'expectedRent' and info.get('rentFreq') else ''
            lines.append(f'{label}: {currency} {value}{frequency}')
    for attribute in row.get('attributes') or []:
        if isinstance(attribute, dict) and attribute.get('name') and attribute.get('value') not in (None, ''):
            lines.append(f"{attribute['name']}: {attribute['value']} {attribute.get('unit') or ''}".strip())
    amenities = [item['name'] for item in row.get('featureAmenities') or []
                 if isinstance(item, dict) and item.get('name')]
    if amenities:
        lines.append('Amenities: ' + ', '.join(dict.fromkeys(amenities)))
    if row.get('unitsAvailable') is not None:
        lines.append(f"Listed units available: {row['unitsAvailable']}")
    if row.get('regaMojDesc'):
        lines.append('Description: ' + row['regaMojDesc'])
    for label, key in [('Published', 'publishedAt'), ('Updated', 'updatedAt')]:
        if row.get(key):
            lines.append(f'{label}: {row[key]}')
    text = '\n'.join(lines)
    price, price_currency, bedrooms, district = extra_fields(text)
    return {'_id': identity, 'record_type': 'project' if project else 'property',
            'title': title, 'url': url, 'updated': row.get('updatedAt') or row.get('publishedAt') or '',
            'text': text, 'city': info.get('city') or '', 'kind': info.get('propertySubType') or '',
            'purpose': purpose, 'price': price, 'currency': price_currency, 'bedrooms': bedrooms,
            'district': district, 'raw': row, 'source_files': entry.get('source_files', [])}


def read_jsonl(path):
    with gzip.open(path, 'rt', encoding='utf-8') as source:
        for line in source:
            if line.strip():
                yield json.loads(line)


def import_cleaned(directory, collection, in_place=False):
    directory = Path(directory)
    report = directory / 'report.json'
    if report.is_file():
        for name, details in json.loads(report.read_text(encoding='utf-8'))['artifacts'].items():
            if hashlib.sha256((directory / name).read_bytes()).hexdigest() != details['sha256']:
                raise ValueError(f'{name} does not match the cleaning report; import cancelled.')
    importer = sync_documents if in_place else replace_documents
    result = importer(collection, (document_from_cleaned(entry)
                                           for entry in read_jsonl(directory / 'records.jsonl.gz')))
    for filename, suffix in [('metadata.jsonl.gz', 'metadata'), ('history.jsonl.gz', 'history')]:
        path = directory / filename
        if not path.is_file():
            continue
        def documents():
            for row in read_jsonl(path):
                identity = row.get('source_file') or hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()
                yield {'_id': identity, **row}
        details = importer(collection.database[f'{collection.name}_{suffix}'], documents(),
                                    search_indexes=False, allow_empty=True)
        result[suffix + '_records'] = details['unique_listings']
    return result


def search_listings(collection, terms, limit=5):
    if not terms:
        return []
    aliases = {'apartments': 'apartment', 'villas': 'villa', 'bedrooms': 'bedroom'}
    terms = [aliases.get(term.lower(), term.lower()) for term in terms]
    query = {'record_type': 'property', '$text': {'$search': ' '.join(terms)},
             '$and': [{'text': {'$regex': r'\b' + re.escape(term) + r's?\b', '$options': 'i'}} for term in terms]}
    projection = {'title': 1, 'url': 1, 'updated': 1, 'text': 1, '_id': 0}
    return list(collection.find(query, projection).limit(limit))


def count_listings(collection, question):
    if not re.search(r'\b(how many|total|number of)\b', question.lower()):
        return None
    result = query_catalog(collection, question)
    return result[0] if result else None


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Import cleaned Wasalt records into MongoDB.')
    parser.add_argument('directory', type=Path, nargs='?', default=Path('data/wasalt-clean'))
    arguments = parser.parse_args()
    print('Syncing without a second collection copy. Run imports while the chatbot is offline.')
    print(json.dumps(import_cleaned(arguments.directory, get_collection(initialize=False, timeout_ms=120000), in_place=True), indent=2))
