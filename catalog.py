import re
from mongo_store import COLLATION, DatabaseNotReady


def numeric(value):
    match = re.fullmatch(r'\s*([\d,]+(?:\.\d+)?)\s*(k|m|million|thousand)?\s*', str(value or ''), re.I)
    if not match:
        return None
    multiplier = {'k': 1000, 'thousand': 1000, 'm': 1000000, 'million': 1000000}.get((match[2] or '').lower(), 1)
    return float(match[1].replace(',', '')) * multiplier


def extra_fields(text):
    fields = dict(line.split(': ', 1) for line in text.splitlines() if ': ' in line)
    price = fields.get('Listed rent') if fields.get('Purpose') == 'rent' else fields.get('Listed sale price')
    match = re.fullmatch(r'([A-Z]{3})\s+([\d,.]+)(?:\s+per\s+.+)?', price or '')
    return (numeric(match[2]) if match else None, match[1] if match else '',
            numeric(fields.get('Bedrooms') or fields.get('BEDROOMS')), fields.get('District', ''))


def query_catalog(collection, question, limit=5):
    text = question.lower().strip(' .?!')
    if collection is None or not re.search(r'\b(propert(?:y|ies)|listings?|apartments?|villas?|land|buildings?|floors?)\b', text):
        return None
    if re.search(r'\b(account|upload|fees?|contact|support|register|list my|post my|sell my|how do i)\b', text):
        return None
    database = collection
    print('PropertyDesk: checking catalog', flush=True)
    if database.estimated_document_count() == 0:
        raise DatabaseNotReady('Wasalt data must be imported before serving property searches.')
    print('PropertyDesk: catalog count checked', flush=True)
    cities = [value for value in database.distinct('city') if value]
    print('PropertyDesk: catalog cities loaded', flush=True)
    districts = [value for value in database.distinct('district') if value]
    print('PropertyDesk: catalog locations loaded', flush=True)
    def location(values):
        matches = [(match.start(), len(value), value) for value in values
                   if value.lower() in text
                   for match in re.finditer(r'(?<!\w)' + re.escape(value.lower()) + r'(?!\w)', text)]
        # Keep the longest overlapping name, e.g. Riyadh Al Khabra rather than Riyadh.
        matches = [item for item in matches if not any(other[0] <= item[0] and
                   other[0] + other[1] >= item[0] + item[1] and other[1] > item[1] for other in matches)]
        return list(dict.fromkeys(item[2] for item in sorted(matches)))
    city_matches, district_matches = location(cities), location(districts)
    if len(city_matches) > 1 or len(district_matches) > 1:
        return [{'title': 'Wasalt location search', 'url': 'https://wasalt.sa/en',
                 'text': 'Multiple locations need separate searches.',
                 'answer': 'Which city or district should I search first? I can give exact results for one location at a time.'}]
    city, district = next(iter(city_matches), None), next(iter(district_matches), None)
    if city and district and city.casefold() == district.casefold():
        district = None
    if not city and re.search(r'\bdammam\b', text):
        city = 'Aldammam'
    if not city and not district:
        unknown = re.search(r'\bin\s+([a-z][a-z -]*?)(?=\s+(?:under|below|above|over|with|on wasalt|for sale|for rent)\b|$)', text)
        if unknown:
            city = unknown[1].strip()
    kinds = [kind for kind in ['apartment', 'villa', 'land', 'building', 'floor']
             if re.search(r'\b' + kind + r's?\b', text)]
    sale, rent = bool(re.search(r'\bsale\b', text)), bool(re.search(r'\brent(?:al)?\b', text))
    purpose = ('sale' if sale else 'rent') if sale != rent else None
    filters = {'record_type': 'property'}
    for column, value in [('city', city), ('district', district), ('purpose', purpose)]:
        if value:
            filters[column] = value
    if kinds:
        filters['kind'] = {'$in': kinds}
    bedroom = (re.search(r'\b(\d+)\s*[- ]?\s*bed(?:room)?s?\b', text) or
               re.search(r'\bbed(?:room)?s?\s*(?:(?:at least|at most|minimum|maximum|of)\s+|:\s*)?(\d+)\b', text))
    if bedroom:
        bedroom_operator = '>=' if re.search(r'\b(at least|minimum)\s+' + bedroom[1] + r'\b', text) else '<=' if re.search(r'\b(at most|maximum)\s+' + bedroom[1] + r'\b', text) else '='
        comparison = {'=': '$eq', '>=': '$gte', '<=': '$lte'}[bedroom_operator]
        filters['bedrooms'] = {comparison: int(bedroom[1])}
    amount = r'(?:sar\s*)?([\d,]+(?:\.\d+)?\s*(?:million|thousand|k|m)?)(?!\w)'
    between = re.search(r'\bbetween\s+' + amount + r'\s+and\s+' + amount, text)
    if between:
        filters['price'] = {'$gte': numeric(between[1]), '$lte': numeric(between[2])}
    for pattern, operator in [(r'\b(?:under|below|less than)\s+' + amount, '<'),
                              (r'\b(?:up to|within|budget(?: of)?|at most|maximum(?: price)?(?: of)?)\s+' + amount, '<='),
                              (r'\b(?:over|above|more than)\s+' + amount, '>'),
                              (r'\b(?:minimum price(?: of)?)\s+' + amount, '>=')]:
        match = re.search(pattern, text)
        if match and not re.match(r'\s*[- ]?bed(?:room)?s?\b', text[match.end():]):
            comparison = {'<': '$lt', '<=': '$lte', '>': '$gt', '>=': '$gte'}[operator]
            filters.setdefault('price', {})[comparison] = numeric(match[1])
    count = bool(re.search(r'\b(how many|total|number of|count)\b', text))
    average = bool(re.search(r'\b(average|mean)\b', text) and re.search(r'\b(price|cost|rent)\b', text))
    cheapest = bool(re.search(r'\b(cheapest|lowest price|least expensive)\b', text))
    expensive = bool(re.search(r'\b(most expensive|highest price)\b', text))
    price_query = average or cheapest or expensive or 'price' in filters
    if price_query:
        filters['currency'] = 'SAR'
        prices = filters.setdefault('price', {})
        prices['$gt'] = max(0, prices.get('$gt', 0))
    if not (city or district or kinds or count or average or cheapest or expensive):
        return None
    remainder = text
    for value in [city, district]:
        if value:
            remainder = re.sub(r'(?<!\w)' + re.escape(value.lower()) + r'(?!\w)', ' ', remainder)
    supported_words = set(('how many total number count of property properties listing listings apartment apartments '
        'villa villas land building buildings floor floors are there in on at for sale rent rental wasalt '
        'give me the available show find list please i want would like with and or under below less than '
        'above over more between to up most maximum minimum price prices cost budget within bedrooms bedroom '
        'beds bed sar million thousand m k average mean cheapest lowest least expensive highest top first '
        'only what is a an can you get has have any exactly from details').split())
    unknown_filter = set(re.findall(r'[a-z]+', remainder)) - supported_words
    unsupported = re.search(r'\b(near|distance|pool|furnished|unfurnished|balcony|sea view|luxury|commercial|bathrooms?|sqm|sqft|cheap)\b', text)
    if unsupported or unknown_filter or (re.search(r'\bbeds?\b|\bbedrooms?\b', text) and not bedroom):
        return [{'title': 'Wasalt property search', 'url': 'https://wasalt.sa/en',
                 'text': 'The requested filter cannot be verified by this search.',
                 'answer': "I can't reliably filter for that feature yet. I can search by city, district, property type, price and bedrooms."}]
    pipeline = [{'$match': filters}, {'$group': {
        '_id': None, 'total': {'$sum': 1},
        'sales': {'$sum': {'$cond': [{'$eq': ['$purpose', 'sale']}, 1, 0]}},
        'rentals': {'$sum': {'$cond': [{'$eq': ['$purpose', 'rent']}, 1, 0]}},
        'priced': {'$sum': {'$cond': [{'$isNumber': '$price'}, 1, 0]}},
        'average': {'$avg': '$price'},
    }}]
    totals = next(iter(database.aggregate(pipeline, collation=COLLATION)),
                  {'total': 0, 'sales': 0, 'rentals': 0, 'priced': 0, 'average': None})
    if price_query and ((totals['sales'] and totals['rentals']) or (rent and average)):
        return [{'title': 'Wasalt price search', 'url': 'https://wasalt.sa/en',
                 'text': 'Sale and rental prices, or different rental billing periods, cannot be averaged together.',
                 'answer': ('Are you looking to buy or rent? I need to keep sale prices and rent separate.'
                            if not purpose else "I can't calculate a reliable average rent without matching rental billing periods.")}]
    scope = ' and '.join(kinds) if kinds else 'property'
    scope += ' listings'
    if purpose or (totals['total'] and totals['sales'] == totals['total']):
        scope += ' for rent' if purpose == 'rent' else ' for sale'
    elif totals['total'] and totals['rentals'] == totals['total']:
        scope += ' for rent'
    if district:
        scope += f' in {district}'
    if city:
        scope += f'{", " if district else " in "}{city}'
    if bedroom:
        qualifier = {'=': '', '>=': 'at least ', '<=': 'at most '}[bedroom_operator]
        scope += f' with {qualifier}{bedroom[1]} bedrooms'
    if price_query and not (average or cheapest or expensive):
        scope += ' matching your price range'
    summary = {'title': 'Wasalt matching properties', 'url': 'https://wasalt.sa/en',
               'text': f"Full catalog count: {totals['total']:,} {scope}.\nSale listings: {totals['sales'] or 0:,}. Rental listings: {totals['rentals'] or 0:,}.\n"
                       'This is not a live count of the entire Wasalt website.'}
    if count:
        summary['answer'] = f"I have details for **{totals['total']:,} {scope}**. Wasalt's live total may differ."
        return [summary]
    if average:
        summary['answer'] = (f"The average listed price is **SAR {totals['average']:,.2f}** across **{totals['total']:,} {scope}**. "
                             'This uses listed SAR prices; current prices may differ.' if totals['total'] else
                             "I don't have matching listings with a stated SAR price to calculate an average.")
        return [summary]
    requested = re.search(r'\b(?:show|give me|list|top|first)\s+(\d+)\b', text)
    limit = min(int(requested[1]), 20) if requested else 1 if cheapest or expensive else limit
    order = [('price', -1)] if expensive else [('price', 1)] if cheapest else [('updated', -1)]
    projection = {field: 1 for field in ['title', 'url', 'text', 'updated', 'city', 'district',
                                         'kind', 'price', 'currency', 'bedrooms']}
    projection['_id'] = 0
    rows = list(database.find(filters, projection).collation(COLLATION)
                .sort(order + [('_id', 1)]).limit(max(1, limit)))
    if not rows:
        summary['answer'] = "I don't have listings matching those filters. Try another location or adjust your requirements."
        return [summary]
    lines = [f"Here {'is' if len(rows) == 1 else 'are'} **{len(rows)} of {totals['total']:,} {scope}**:",
             '', '| Location | Type | Bedrooms | Listed price |', '|---|---|---|---|']
    for row in rows:
        place = ', '.join(filter(None, [row['district'], row['city']])) or 'Not specified'
        bedrooms = f"{row['bedrooms']:g}" if row['bedrooms'] is not None else 'Not specified'
        price = (row['currency'] + ' ' + f"{row['price']:,.2f}".rstrip('0').rstrip('.')) if row['price'] is not None else 'Not specified'
        cells = [place, row['kind'] or 'Not specified', bedrooms, price]
        lines.append('| ' + ' | '.join(cell.replace('|', '\\|').replace('\n', ' ') for cell in cells) + ' |')
    lines += ['', 'Prices are listed figures; current availability may change.']
    summary['answer'] = '\n'.join(lines)
    return [summary] + rows
