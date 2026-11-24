import re


PROPERTY_WORDS = r'\b(propert(?:y|ies)|listings?|apartments?|villas?|land|buildings?|floors?)\b'
AMOUNT = r'(?:sar\s*)?([\d,]+(?:\.\d+)?\s*(?:million|thousand|k|m)?)(?!\w)'
BEDROOMS = r'\b(?:(?:at least|at most|minimum|maximum|exactly)\s+)?\d+\s*[- ]?bed(?:room)?s?\b'
BEDROOMS_AFTER = r'\bbed(?:room)?s?\s*(?:(?:at least|at most|minimum|maximum|of)\s+|:\s*)?\d+\b'
PRICE_RANGE = (r'\bbetween\s+' + AMOUNT + r'\s+and\s+' + AMOUNT +
               r'|\b(?:under|below|less than|up to|within|budget(?: of)?|at most|maximum(?: price)?(?: of)?|'
               r'over|above|more than|minimum price(?: of)?)\s+' + AMOUNT +
               r'(?!\s*[- ]?bed(?:room)?s?\b)')


def normalize_question(question):
    text = question.lower().strip(' .?!')
    numbers = 'zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty'.split()
    text = re.sub(r'\b(' + '|'.join(numbers) + r')\b',
                  lambda match: str(numbers.index(match[0])), text)
    for phrase, replacement in [('no more than', 'at most'), ('no fewer than', 'at least'),
                                ('not more than', 'at most'), ('not less than', 'at least'),
                                ('a minimum of', 'minimum'), ('a maximum of', 'maximum'),
                                ('minimum of', 'minimum'), ('maximum of', 'maximum')]:
        text = re.sub(r'\b' + phrase + r'\b', replacement, text)
    text = re.sub(r'\b(show|give me|list)\s+the\s+(\d+)\b', r'\1 \2', text)
    text = re.sub(r'\s+', ' ', text)
    return text


def resolve_search_question(question, history):
    """Carry property filters into a short refinement of the previous search."""
    text = normalize_question(question)
    previous = next((item.get('search_question', item['content']) for item in reversed(history)
                     if item['role'] == 'user'), '')
    if not previous:
        return text
    if not re.search(PROPERTY_WORDS, previous, re.I):
        if re.search(r'\b(it|they|those|these|them|that)\b', text):
            return f'{previous} {text}'
        return text
    refinement = (re.match(r'^(only|and|what about|how about|instead|now)\b', text) or
                  re.match(r'^which\s+(?:(?:one|1)\s+)?is\b', text) or
                  not re.search(PROPERTY_WORDS, text))
    if not refinement:
        return text
    # Leave a new company question outside the property search conversation.
    if re.search(r'\b(darglobal|aida|trump|who|why)\b', text):
        return text
    base = normalize_question(previous)
    text = re.sub(r'\b(?:give me|include|with)\s+(?:its |their |the )?listing links?\b', '', text)
    text = re.sub(r'\b(only|those|these|them|it|its|their|instead|now)\b', ' ', text)
    text = re.sub(r'^(?:and|what about|how about)\b', '', text.strip())
    if re.search(r'\bbed(?:room)?s?\b', text):
        base = re.sub(BEDROOMS_AFTER, ' ', base)
        base = re.sub(BEDROOMS, ' ', base)
    if re.search(PRICE_RANGE, text):
        base = re.sub(PRICE_RANGE, ' ', base)
    if re.search(r'\b(sale|rent|rental)\b', text):
        base = re.sub(r'\b(?:for\s+)?(?:sale|rent|rental)\b', ' ', base)
    if re.search(PROPERTY_WORDS, text):
        base = re.sub(PROPERTY_WORDS, ' ', base)
    location = re.search(r'\bin\s+([a-z][a-z -]*?)(?=\s+(?:under|below|with|for|and)\b|[?.!]|$)', text)
    if location:
        base = re.sub(r'\bin\s+[a-z][a-z -]*?(?=\s+(?:under|below|above|over|with|for|between|at|minimum|maximum|on)\b|$)', ' ', base)
    if re.search(r'\b(cheapest|lowest|expensive|highest|average|mean|how many|count|total|show|list)\b', text):
        base = re.sub(r'\b(how many|number of|count|total|average|mean|cheapest|lowest price|least expensive|most expensive|highest price)\b', ' ', base)
        base = re.sub(r'\b(show|give me|list|top|first)\s*\d*\b', ' ', base)
    text = re.sub(r'^which\s+(?:(?:one|1)\s+)?is\s+', '', text.strip())
    return re.sub(r'\s+', ' ', f'{base} {text}').strip(' .?!')
