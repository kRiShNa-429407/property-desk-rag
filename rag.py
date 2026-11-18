from dataclasses import dataclass
from pathlib import Path
import re

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

DATA_FILE = Path(__file__).parent / 'data' / 'knowledge.txt'
SEPARATOR = '\n' + '=' * 72 + '\n'


@dataclass(frozen=True)
class Passage:
    source: str
    title: str
    url: str
    collected: str
    text: str


def load_passages(path=DATA_FILE, chunk_size=180, overlap=35):
    """Split page blocks from the text file into overlapping passages."""
    if chunk_size <= overlap or overlap < 0:
        raise ValueError('Chunk size must be larger than overlap.')
    passages = []
    for block in Path(path).read_text(encoding='utf-8').split(SEPARATOR):
        metadata, separator, text = block.strip().partition('\n\n')
        if not separator or not text.strip():
            continue
        fields = dict(line.split(': ', 1) for line in metadata.splitlines() if ': ' in line)
        if not fields.get('URL') or not fields.get('Source'):
            continue
        words = text.split()
        for start in range(0, len(words), chunk_size - overlap):
            passages.append(Passage(
                source=fields['Source'], title=fields.get('Title', fields['Source']),
                url=fields['URL'], collected=fields.get('Collected', 'unknown'),
                text=' '.join(words[start:start + chunk_size]),
            ))
            if start + chunk_size >= len(words):
                break
    return passages


def normalize(text):
    text = text.lower().replace('dar global', 'darglobal')
    for plural, singular in [('apartments', 'apartment'), ('villas', 'villa'),
                             ('bedrooms', 'bedroom'), ('prices', 'price')]:
        text = re.sub(r'\b' + plural + r'\b', singular, text)
    return text


class Retriever:
    def __init__(self, passages):
        if not passages:
            raise ValueError('The text file has no readable pages. Run scrape.py first.')
        self.passages = passages
        self.vectorizer = TfidfVectorizer(stop_words='english', ngram_range=(1, 2), sublinear_tf=True)
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
        return results
