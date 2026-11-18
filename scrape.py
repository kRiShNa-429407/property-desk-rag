"""Collect public HTML pages into data/knowledge.txt without bypassing site restrictions."""
import argparse
from collections import deque
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
from pathlib import Path
import time
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser
import xml.etree.ElementTree as ET

from bs4 import BeautifulSoup
import requests
from pypdf import PdfReader

ROOT = Path(__file__).parent
SEPARATOR = '\n' + '=' * 72 + '\n'
AGENT = 'PropertyDeskBot/1.0'
SITES = {
    'DarGlobal': ['https://darglobal.co.uk/', 'https://darglobal.co.uk/about',
                  'https://darglobal.co.uk/landing-page/aida'],
    'Wasalt': ['https://wasalt.sa/en', 'https://wasalt.sa/en/corporate',
               'https://wasalt.sa/en/apartments-for-sale-in-riyadh'],
}

# Official downloadable sources remain available when the marketing site blocks HTML requests.
DARGLOBAL_BROCHURES = [
    ('AIDA Oceana brochure', 'https://cdn.darglobal.co.uk/Aida_Phase_01_EN_8e90a16291.pdf'),
    ('AIDA Trump brochure', 'https://cdn.darglobal.co.uk/DG_AIDA_Trump_Brochure_EN_6a6b1c7469.pdf'),
]


def clean_url(url, host):
    parts = urlsplit(url)
    if parts.scheme != 'https' or parts.netloc != host:
        return None
    if parts.query or any(part in parts.path.lower() for part in
                          ['/login', '/signin', '/register', '/api/', '/_next/', '/ar/']):
        return None
    if Path(parts.path).suffix.lower() in {'.pdf', '.jpg', '.png', '.webp', '.svg', '.js', '.css', '.zip', '.mp4'}:
        return None
    if host == 'wasalt.sa' and not (parts.path == '/en' or parts.path.startswith('/en/')):
        return None
    return urlunsplit(('https', host, parts.path.rstrip('/') or '/', '', ''))


def extract_page(html):
    soup = BeautifulSoup(html, 'html.parser')
    if soup.select_one('iframe#main-iframe') or any(marker in html.lower() for marker in
            ['_incapsula_resource', 'cf-chl-', 'verify you are human', 'access denied']):
        raise ValueError('Source returned an access-control page')
    meta = soup.find('meta', attrs={'name': lambda value: value and value.lower() == 'robots'})
    if meta and any(rule in meta.get('content', '').lower() for rule in ['noai', 'noindex', 'nosnippet']):
        raise ValueError('Page metadata restricts collection')
    title = soup.title.get_text(' ', strip=True) if soup.title else 'Untitled page'
    links = [link['href'] for link in soup.select('a[href]')]
    for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'form', 'noscript']):
        tag.decompose()
    content = soup.find('main') or soup.find('article') or soup.body or soup
    lines = [line.strip() for line in content.get_text('\n', strip=True).splitlines() if line.strip()]
    text = '\n'.join(lines)
    if len(text.split()) < 30:
        raise ValueError('No meaningful page text; page may require JavaScript')
    return title, text, links


class Crawler:
    def __init__(self, delay=1):
        self.session = requests.Session()
        self.session.headers['User-Agent'] = AGENT
        self.delay = delay

    def get(self, url):
        time.sleep(self.delay)
        response = self.session.get(url, timeout=(10, 30))
        response.raise_for_status()
        # Do not follow a cross-domain redirect into a different site's data.
        if urlsplit(response.url).netloc != urlsplit(url).netloc:
            raise ValueError('Cross-domain redirect')
        return response

    def robots(self, origin):
        try:
            response = self.get(origin + '/robots.txt')
        except requests.HTTPError as error:
            if error.response.status_code == 404:
                policy = RobotFileParser()
                policy.parse([])
                return policy, [origin + '/sitemap.xml']
            raise
        if '<html' in response.text.lower():
            raise ValueError('robots.txt returned an HTML access-control page')
        policy = RobotFileParser()
        policy.parse(response.text.splitlines())
        return policy, policy.site_maps() or [origin + '/sitemap.xml']

    def sitemap_links(self, maps, host, report):
        pages, visited = [], set()
        queue = deque(maps)
        while queue:
            url = queue.popleft()
            if url in visited or len(visited) >= 50 or urlsplit(url).netloc != host:
                continue
            visited.add(url)
            try:
                root = ET.fromstring(self.get(url).content)
                locations = [item.text for item in root.findall('.//{*}loc') if item.text]
                if root.tag.endswith('sitemapindex'):
                    queue.extend(locations)
                else:
                    pages.extend(locations)
            except (requests.RequestException, ValueError, ET.ParseError) as error:
                report.append({'url': url, 'status': 'sitemap_failed', 'reason': str(error)})
        return pages

    def collect(self, name, seeds, max_pages, report):
        host = urlsplit(seeds[0]).netloc
        origin = 'https://' + host
        try:
            policy, maps = self.robots(origin)
        except (requests.RequestException, ValueError) as error:
            report.append({'source': name, 'url': origin, 'status': 'blocked', 'reason': str(error)})
            return []
        pause = policy.crawl_delay(AGENT) or policy.crawl_delay('*') or self.delay
        self.delay = max(self.delay, pause)
        queue = deque(seeds + self.sitemap_links(maps, host, report))
        visited, hashes, pages = set(), set(), []
        consecutive_blocks = 0
        while queue and (max_pages == 0 or len(visited) < max_pages):
            url = clean_url(queue.popleft(), host)
            if not url or url in visited:
                continue
            visited.add(url)
            if not policy.can_fetch(AGENT, url):
                report.append({'url': url, 'status': 'robots_disallowed'})
                continue
            try:
                response = self.get(url)
                if 'text/html' not in response.headers.get('Content-Type', ''):
                    continue
                title, text, links = extract_page(response.text)
                consecutive_blocks = 0
                queue.extend(urljoin(url, link) for link in links)
                digest = hashlib.sha256(text.encode()).hexdigest()
                if digest in hashes:
                    continue
                hashes.add(digest)
                pages.append({'source': name, 'title': title.replace('\n', ' '), 'url': url,
                              'collected': datetime.now(timezone.utc).date().isoformat(), 'text': text})
                print(f'Collected {url}', flush=True)
            except (requests.RequestException, ValueError) as error:
                report.append({'url': url, 'status': 'failed', 'reason': str(error)})
                if 'access-control' in str(error) or (isinstance(error, requests.HTTPError) and error.response.status_code in (403, 429)):
                    consecutive_blocks += 1
                    if consecutive_blocks >= 3:
                        report.append({'source': name, 'status': 'stopped', 'reason': 'Three consecutive access restrictions; stopping requests to this source.'})
                        break
        report.append({'source': name, 'status': 'finished', 'attempted': len(visited),
                       'collected': len(pages), 'limit_reached': bool(queue) and max_pages > 0 and len(visited) >= max_pages})
        return pages


def save_pages(pages, path):
    blocks = []
    for page in pages:
        blocks.append(f'Source: {page["source"]}\nTitle: {page["title"]}\nURL: {page["url"]}\n'
                      f'Collected: {page["collected"]}\n\n{page["text"]}')
    temporary = path.with_suffix('.tmp')
    temporary.write_text(SEPARATOR.join(blocks) + '\n', encoding='utf-8')
    temporary.replace(path)


def read_saved_pages(path):
    if not path.exists():
        return []
    pages = []
    for block in path.read_text(encoding='utf-8').split(SEPARATOR):
        metadata, separator, text = block.strip().partition('\n\n')
        if not separator:
            continue
        fields = dict(line.split(': ', 1) for line in metadata.splitlines() if ': ' in line)
        if fields.get('Source') and fields.get('URL'):
            pages.append({'source': fields['Source'], 'title': fields.get('Title', ''),
                          'url': fields['URL'], 'collected': fields.get('Collected', 'unknown'), 'text': text})
    return pages


def collect_brochures(crawler, report):
    """Extract text from public DarGlobal PDFs, with a page-specific source for each block."""
    origin = 'https://cdn.darglobal.co.uk'
    try:
        policy, _ = crawler.robots(origin)
    except (requests.RequestException, ValueError) as error:
        report.append({'source': 'DarGlobal', 'url': origin, 'status': 'blocked', 'reason': str(error)})
        return []
    pages = []
    for title, url in DARGLOBAL_BROCHURES:
        if not policy.can_fetch(AGENT, url):
            report.append({'url': url, 'status': 'robots_disallowed'})
            continue
        try:
            response = crawler.get(url)
            if not response.content.startswith(b'%PDF'):
                raise ValueError('Source did not return a PDF')
            reader = PdfReader(BytesIO(response.content))
            count = 0
            for number, page in enumerate(reader.pages, 1):
                text = (page.extract_text() or '').strip()
                if len(text.split()) < 20:
                    continue
                pages.append({'source': 'DarGlobal', 'title': f'{title} — page {number}',
                              'url': f'{url}#page={number}',
                              'collected': datetime.now(timezone.utc).date().isoformat(),
                              'text': 'Official brochure text; published project descriptions may be historical.\n' + text})
                count += 1
            report.append({'source': 'DarGlobal', 'url': url, 'status': 'pdf_collected', 'text_pages': count})
            print(f'Collected {count} text pages from {title}', flush=True)
        except (requests.RequestException, ValueError, OSError) as error:
            report.append({'url': url, 'status': 'pdf_failed', 'reason': str(error)})
    return pages


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--max-pages', type=int, default=100, help='Maximum attempted pages per site; 0 means all discovered pages.')
    args = parser.parse_args()
    if args.max_pages < 0:
        parser.error('--max-pages cannot be negative')
    report, pages = [], []
    crawler = Crawler()
    for name, seeds in SITES.items():
        pages.extend(crawler.collect(name, seeds, args.max_pages, report))
    if not any(page['source'] == 'DarGlobal' for page in pages):
        pages.extend(collect_brochures(crawler, report))
    data = ROOT / 'data'
    data.mkdir(exist_ok=True)
    (data / 'scrape_report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    if pages:
        missing = set(SITES) - {page['source'] for page in pages}
        output = data / ('knowledge.partial.txt' if missing else 'knowledge.txt')
        save_pages(pages, output)
        print(f'Saved {len(pages)} pages to {output.name}.')
        if missing:
            # Refresh successful sources without discarding the blocked source's dated records.
            previous = read_saved_pages(data / 'knowledge.txt')
            retained = [page for page in previous if page['source'] in missing]
            save_pages(retained + pages, data / 'knowledge.txt')
            print(f'Incomplete collection: {", ".join(sorted(missing))}. Retained {len(retained)} existing records for blocked sources in knowledge.txt.')
            return 1
        return 0
    print('No pages collected: both sources blocked the crawler. Existing knowledge.txt preserved. See data/scrape_report.json.')
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
