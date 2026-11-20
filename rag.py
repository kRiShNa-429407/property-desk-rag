from dataclasses import dataclass
from html import unescape
import json
from pathlib import Path
import re
import unicodedata
from urllib.parse import urlsplit, urlunsplit

from bs4 import BeautifulSoup
from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS, TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from catalog import query_catalog
from mongo_store import get_collection
from wasalt import search_listings

DATA_DIR = Path(__file__).parent / 'data'
SEPARATOR = '\n' + '=' * 72 + '\n'


@dataclass(frozen=True)
class Passage:
    source: str
    title: str
    url: str
    collected: str
    text: str
    answer: str = ''


def knowledge_files():
    names = ['darglobal.json', 'wasalt.json', 'knowledge.json', 'knowledge.txt']
    return [DATA_DIR / name for name in names if (DATA_DIR / name).is_file()]


def clean_text(text):
    if re.search(r'<(?:html|body|main|article|p|div|section)\b', text, re.I):
        soup = BeautifulSoup(text, 'html.parser')
        for element in soup.select('script, style, nav, footer, header, form, noscript'):
            element.decompose()
        text = soup.get_text('\n')
    text = unicodedata.normalize('NFKC', unescape(text))
    controls = {'register interest', 'register interest.', 'view more', 'view all',
                'we value our privacy', 'find out moreaccept', 'english|العربية'}
    lines, seen = [], set()
    for line in text.splitlines():
        line = ' '.join(line.split())
        key = line.casefold()
        if not line or key in controls or key.startswith('we use cookies to enhance your browsing experience'):
            continue
        if len(line) >= 60 and key in seen:
            continue
        seen.add(key)
        lines.append(line)
    return '\n'.join(lines)


def json_documents(path):
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    rows = data.get('pages') if isinstance(data, dict) else data
    if not isinstance(rows, list):
        raise ValueError(f'{path.name}: expected a list of pages or an object with a pages list.')
    documents = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        status = row.get('status', 200)
        if isinstance(status, int) and not 200 <= status < 300:
            continue
        url = row.get('canonical') or row.get('url') or ''
        if not isinstance(url, str):
            continue
        parts = urlsplit(url)
        if parts.scheme not in {'http', 'https'} or not parts.hostname:
            continue
        url = urlunsplit((parts.scheme, parts.netloc.lower(), parts.path.rstrip('/') or '/', parts.query, ''))
        host = parts.hostname.lower()
        if host == 'darglobal.co.uk' or host.endswith('.darglobal.co.uk'):
            source = 'DarGlobal'
        elif host in {'wasalt.sa', 'wasalt.com'} or host.endswith(('.wasalt.sa', '.wasalt.com')):
            source = 'Wasalt'
        else:
            source = row.get('source') or host
        text = row.get('text') or row.get('content') or ''
        if not isinstance(text, str):
            raise ValueError(f'{path.name}: page text must be a string ({url}).')
        text = clean_text(text)
        if text:
            documents.append(Passage(source, row.get('title') or row.get('h1') or source,
                                     url, row.get('collected') or row.get('scrapedAt') or 'unknown', text))
    if not documents:
        raise ValueError(f'{path.name}: no readable pages with text and a valid URL.')
    return documents


def text_documents(path):
    documents = []
    for block in path.read_text(encoding='utf-8').split(SEPARATOR):
        metadata, separator, text = block.strip().partition('\n\n')
        if not separator or not text.strip():
            continue
        fields = dict(line.split(': ', 1) for line in metadata.splitlines() if ': ' in line)
        if not fields.get('URL') or not fields.get('Source'):
            continue
        documents.append(Passage(fields['Source'], fields.get('Title', fields['Source']),
                                 fields['URL'], fields.get('Collected', 'unknown'), clean_text(text)))
    return documents


def load_passages(path=None, chunk_size=180, overlap=35):
    if chunk_size <= overlap or overlap < 0:
        raise ValueError('Chunk size must be larger than overlap.')
    files = [Path(path)] if path is not None else knowledge_files()
    documents = []
    for file in files:
        if file.suffix.lower() == '.json':
            documents.extend(json_documents(file))
    json_sources = {item.source for item in documents}
    for file in files:
        if file.suffix.lower() == '.txt':
            documents.extend(item for item in text_documents(file) if item.source not in json_sources)
    passages, seen_pages, seen_chunks = [], set(), set()
    for item in documents:
        words = item.text.split()
        page_key = (item.source, ' '.join(words).casefold())
        if page_key in seen_pages:
            continue
        seen_pages.add(page_key)
        for start in range(0, len(words), chunk_size - overlap):
            text = ' '.join(words[start:start + chunk_size])
            chunk_key = (item.source, text.casefold())
            if chunk_key not in seen_chunks:
                passages.append(Passage(item.source, item.title, item.url, item.collected, text))
                seen_chunks.add(chunk_key)
            if start + chunk_size >= len(words):
                break
    return passages


def normalize(text):
    text = text.lower().replace('dar global', 'darglobal')
    text = re.sub(r'\bgdv\b', 'gross development value', text)
    for plural, singular in [('apartments', 'apartment'), ('villas', 'villa'),
                             ('bedrooms', 'bedroom'), ('prices', 'price')]:
        text = re.sub(r'\b' + plural + r'\b', singular, text)
    return text


class Retriever:
    def __init__(self, passages):
        if not passages:
            raise ValueError('No readable knowledge found. Add a JSON knowledge file to data/.')
        self.passages = passages
        stop_words = sorted(set(ENGLISH_STOP_WORDS) | {'does', 'did', 'tell', 'explain', 'please', 'know', 'want'})
        self.vectorizer = TfidfVectorizer(stop_words=stop_words, ngram_range=(1, 2), sublinear_tf=True)
        self.matrix = self.vectorizer.fit_transform([
            normalize(f'{item.source} {item.title} {item.text}') for item in passages
        ])

    def search(self, question, source='All sources', limit=5):
        normalized = normalize(question)
        if source == 'All sources':
            mentions_dar = bool(re.search(r'\b(aida|darglobal)\b', normalized))
            mentions_wasalt = bool(re.search(r'\bwasalt\b', normalized))
            if mentions_dar and not mentions_wasalt:
                source = 'DarGlobal'
            elif mentions_wasalt and not mentions_dar:
                source = 'Wasalt'
        if source != 'DarGlobal':
            matches = query_catalog(get_collection(), question, limit)
            if matches:
                return [Passage('Wasalt', row['title'], row['url'], 'unknown', row['text'], row.get('answer', ''))
                        for row in matches]
        query = self.vectorizer.transform([normalized])
        scores = cosine_similarity(query, self.matrix).ravel()
        ranked = sorted(enumerate(scores), key=lambda pair: pair[1], reverse=True)
        results = []
        for position, score in ranked:
            passage = self.passages[position]
            if score < 0.06 or (source != 'All sources' and passage.source != source):
                continue
            results.append(passage)
            if len(results) == limit:
                break
        if source != 'DarGlobal':
            ignored = set(ENGLISH_STOP_WORDS) | {'wasalt', 'show', 'find', 'available', 'property',
                                                'properties', 'list', 'listing', 'listings', 'please', 'want'}
            terms = [word for word in re.findall(r'\w+', normalized) if word not in ignored]
            terms = ['aldammam' if word == 'dammam' else word for word in terms]
            listings = [Passage('Wasalt', row['title'], row['url'], 'unknown', row['text'])
                        for row in search_listings(get_collection(), terms, limit)]
            results = listings + results
        return results[:limit]
