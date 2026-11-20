"""Resumable HTML collection for DarGlobal and Wasalt."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import threading
import time
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
import xml.etree.ElementTree as ET

from bs4 import BeautifulSoup
import requests

from scrape import AGENT, Crawler, ROOT, SEPARATOR, extract_page

SITES = {
    'DarGlobal': ('darglobal.co.uk', ['/', '/about', '/press', '/projects', '/investor-relations']),
    'Wasalt': ('wasalt.sa', ['/en', '/', '/en/corporate', '/en/projects']),
}
ASSET_TYPES = {'.pdf', '.jpg', '.jpeg', '.png', '.svg', '.webp', '.gif', '.ico', '.css',
               '.js', '.map', '.woff', '.woff2', '.ttf', '.zip', '.gz', '.mp4', '.mp3', '.xml'}
TRACKING = {'gclid', 'fbclid', 'msclkid', '_ga', 'referrer'}
REPORT_LOCK = threading.Lock()
TEXT_LOCK = threading.Lock()
EXPORT_LOCK = threading.Lock()


class BrowserCrawler(Crawler):
    """Fetch JavaScript pages in a browser."""
    def __init__(self):
        super().__init__()
        from playwright.sync_api import sync_playwright
        self.playwright = sync_playwright().start()
        options = {'headless': True}
        if os.name == 'nt':
            options['channel'] = 'msedge'
        self.browser = self.playwright.chromium.launch(**options)
        self.page = self.browser.new_page()

    def get(self, url):
        path = urlsplit(url).path.lower()
        if path.endswith(('robots.txt', '.xml', '.gz')):
            return super().get(url)
        time.sleep(self.delay)
        result = self.page.goto(url, wait_until='domcontentloaded', timeout=45000)
        try:
            self.page.wait_for_function('() => document.body && document.body.innerText.trim().length > 300', timeout=12000)
        except Exception:
            pass
        if urlsplit(self.page.url).hostname not in (urlsplit(url).hostname, 'www.' + urlsplit(url).hostname):
            raise ValueError('Browser redirected outside the source website')
        response = requests.Response()
        response.status_code = result.status if result else 200
        response.url = self.page.url
        response.encoding = 'utf-8'
        response._content = self.page.content().encode('utf-8')
        response.headers['Content-Type'] = 'text/html; charset=utf-8'
        response.raise_for_status()
        return response

    def close(self):
        self.browser.close()
        self.playwright.stop()


def normalize_url(url, host):
    parts = urlsplit(url)
    if parts.scheme not in ('http', 'https') or parts.hostname not in (host, 'www.' + host):
        return None
    if parts.username or parts.password or parts.port not in (None, 80, 443):
        return None
    if Path(parts.path).suffix.lower() in ASSET_TYPES:
        return None
    if any(segment in parts.path.lower().split('/') for segment in
           ('api', '_next', 'logout', 'signout', 'admin', 'checkout', 'payment')):
        return None
    query = sorted((key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
                   if not key.lower().startswith('utm_') and key.lower() not in TRACKING)
    return urlunsplit(('https', host, parts.path.rstrip('/') or '/', urlencode(query), ''))


def page_priority(url):
    path = urlsplit(url).path
    if '/property/' in path or '/عقار/' in path:
        return 2
    if any(word in path.lower() for word in ('for-sale', 'for-rent', 'للبيع', 'للايجار')):
        return 1
    return 0


class HtmlArchive:
    def __init__(self, output, state):
        self.output = Path(output)
        self.output.mkdir(parents=True, exist_ok=True)
        Path(state).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(state, timeout=60)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS pages (
                id INTEGER PRIMARY KEY, site TEXT NOT NULL, url TEXT NOT NULL UNIQUE,
                priority INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
                error TEXT, digest TEXT, html_bytes INTEGER DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS pending_pages ON pages(site, status, priority, id);
            CREATE TABLE IF NOT EXISTS sitemaps (
                url TEXT PRIMARY KEY, site TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', error TEXT
            );
            CREATE TABLE IF NOT EXISTS site_states (site TEXT PRIMARY KEY, state TEXT NOT NULL);
        ''')
        self.started = datetime.now(timezone.utc).isoformat()
        self.site_status = {}
        self.minimum_free_bytes = 2 * 1024 ** 3

    def discover(self, site, urls):
        host = SITES[site][0]
        clean = set()
        for url in urls:
            try:
                normalized = normalize_url(url, host)
            except ValueError:
                normalized = None
            if normalized:
                clean.add(normalized)
        self.db.executemany('INSERT OR IGNORE INTO pages(site,url,priority) VALUES(?,?,?)',
                            ((site, url, page_priority(url)) for url in sorted(clean)))
        self.db.commit()

    def archive_html(self, site, url, response, outcome):
        path = self.output / f'{site.lower()}_html.txt'
        stamp = datetime.now(timezone.utc).isoformat()
        header = f'\n\n{"=" * 96}\nURL: {url}\nFetched: {stamp}\nHTTP status: {response.status_code}\nOutcome: {outcome}\n\n'
        with path.open('a', encoding='utf-8', newline='\n') as stream:
            stream.write(header)
            stream.write(response.text)
            stream.write('\n')
            stream.flush()

    def archive_text(self, site, url, title, text):
        path = self.output.parent / 'full_scraped_text.txt'
        with TEXT_LOCK, path.open('a', encoding='utf-8', newline='\n') as stream:
            if stream.tell():
                stream.write(SEPARATOR)
            stream.write(f'Source: {site}\nTitle: {title}\nURL: {url}\n'
                         f'Collected: {datetime.now(timezone.utc).date().isoformat()}\n\n{text}\n')
            stream.flush()

    def report(self, status='running'):
        with REPORT_LOCK:
            self._report(status)

    def _report(self, status):
        for site, state in self.site_status.items():
            self.db.execute('INSERT OR REPLACE INTO site_states(site,state) VALUES(?,?)', (site, state))
        self.db.commit()
        states = dict(self.db.execute('SELECT site,state FROM site_states'))
        sites = {}
        for site in SITES:
            rows = self.db.execute('SELECT status,COUNT(*) FROM pages WHERE site=? GROUP BY status', (site,)).fetchall()
            counts = dict(rows)
            sitemap_rows = self.db.execute('SELECT status,COUNT(*) FROM sitemaps WHERE site=? GROUP BY status', (site,)).fetchall()
            sites[site] = {'state': states.get(site, 'waiting'), 'discovered': sum(counts.values()),
                           'pages': counts, 'sitemaps': dict(sitemap_rows)}
        result = {'status': status, 'started_at': self.started,
                  'updated_at': datetime.now(timezone.utc).isoformat(), 'sites': sites,
                  'raw_html_directory': str(self.output),
                  'readable_text': str(self.output.parent / 'full_scraped_text.txt'),
                  'note': 'Only public, robots-permitted URLs are fetched. Pending/failed/blocked URLs mean the archive is incomplete.'}
        target = self.output.parent / 'full_crawl_report.json'
        temporary = target.with_suffix('.tmp')
        temporary.write_text(json.dumps(result, indent=2), encoding='utf-8')
        temporary.replace(target)

    def mark(self, url, status, error=None, digest=None, html_bytes=0):
        self.db.execute('UPDATE pages SET status=?,error=?,digest=?,html_bytes=? WHERE url=?',
                        (status, error, digest, html_bytes, url))
        self.db.commit()

    def collect_sitemaps(self, site, crawler, maps, policy):
        host = SITES[site][0]
        self.db.executemany('INSERT OR IGNORE INTO sitemaps(url,site) VALUES(?,?)', ((url, site) for url in maps))
        self.db.commit()
        while True:
            row = self.db.execute("SELECT url FROM sitemaps WHERE site=? AND status='pending' LIMIT 1", (site,)).fetchone()
            if not row:
                break
            url = row[0]
            map_host = urlsplit(url).hostname or ''
            if map_host not in (host, 'www.' + host) and not map_host.endswith('.' + host):
                self.db.execute("UPDATE sitemaps SET status='excluded' WHERE url=?", (url,))
                self.db.commit()
                continue
            try:
                response = crawler.get(url)
                content = response.content
                if content.startswith(b'\x1f\x8b'):
                    content = gzip.decompress(content)
                tree = ET.fromstring(content)
                locations = [item.text.strip() for item in tree.findall('.//{*}loc') if item.text]
                if tree.tag.endswith('sitemapindex'):
                    self.db.executemany('INSERT OR IGNORE INTO sitemaps(url,site) VALUES(?,?)',
                                        ((urljoin(url, location), site) for location in locations))
                else:
                    self.discover(site, locations)
                self.db.execute("UPDATE sitemaps SET status='saved',error=NULL WHERE url=?", (url,))
                print(f'{site}: discovered {len(locations)} URLs from {url}', flush=True)
            except (requests.RequestException, ValueError, OSError, ET.ParseError) as error:
                self.db.execute("UPDATE sitemaps SET status='failed',error=? WHERE url=?", (str(error), url))
                print(f'{site}: sitemap unavailable: {url}: {error}', flush=True)
            self.db.commit()
            self.report()

    def export_urls(self):
        path = self.output.parent / 'full_crawl_urls.txt'
        with EXPORT_LOCK, path.open('w', encoding='utf-8', newline='\n') as stream:
            for site, url, status, error in self.db.execute('SELECT site,url,status,error FROM pages ORDER BY site,id'):
                stream.write(f'{site}\t{status}\t{url}')
                if error:
                    stream.write('\t' + error.replace('\n', ' '))
                stream.write('\n')

    def collect_site(self, site, max_pages=0, browser=False):
        host, paths = SITES[site]
        self.discover(site, [f'https://{host}{path}' for path in paths])
        (self.output / f'{site.lower()}_html.txt').touch(exist_ok=True)
        crawler = BrowserCrawler() if browser else Crawler(delay=1)
        if browser:
            self.db.execute("UPDATE pages SET status='pending',error=NULL WHERE site=? AND status='blocked'", (site,))
            self.db.commit()
        self.site_status[site] = 'discovering'
        self.report()
        try:
            policy, maps = crawler.robots('https://' + host)
        except (requests.RequestException, ValueError) as error:
            self.site_status[site] = 'blocked: robots.txt unavailable: ' + str(error)
            self.report()
            return
        crawler.delay = max(crawler.delay, policy.crawl_delay(AGENT) or policy.crawl_delay('*') or 1)
        self.collect_sitemaps(site, crawler, maps, policy)
        self.export_urls()
        self.site_status[site] = 'collecting'
        self.report()
        attempts, restricted = 0, 0
        while max_pages == 0 or attempts < max_pages:
            if shutil.disk_usage(self.output).free < self.minimum_free_bytes:
                self.site_status[site] = 'paused: less than 2 GB free disk space'
                self.report()
                return
            row = self.db.execute("SELECT url FROM pages WHERE site=? AND status='pending' ORDER BY priority,id LIMIT 1", (site,)).fetchone()
            if not row:
                self.site_status[site] = 'queue exhausted'
                break
            url = row[0]
            if not policy.can_fetch(AGENT, url):
                self.mark(url, 'robots_disallowed')
                continue
            attempts += 1
            response = None
            try:
                response = crawler.get(url)
                if 'text/html' not in response.headers.get('Content-Type', '').lower():
                    self.mark(url, 'not_html')
                    continue
                # Keep links before cleaning the page text.
                soup = BeautifulSoup(response.text, 'html.parser')
                self.discover(site, [urljoin(response.url, link['href']) for link in soup.select('a[href]')])
                try:
                    title, text, _ = extract_page(response.text)
                except ValueError as error:
                    blocked = 'access-control' in str(error)
                    self.archive_html(site, url, response, 'blocked' if blocked else 'html_saved_without_readable_text')
                    self.mark(url, 'blocked' if blocked else 'html_only', str(error), html_bytes=len(response.content))
                    if blocked:
                        restricted += 1
                    else:
                        restricted = 0
                else:
                    digest = hashlib.sha256(response.content).hexdigest()
                    self.archive_html(site, url, response, 'saved')
                    self.archive_text(site, url, title.replace('\n', ' '), text)
                    self.mark(url, 'saved', digest=digest, html_bytes=len(response.content))
                    restricted = 0
                    print(f'{site}: saved {url}', flush=True)
            except requests.HTTPError as error:
                response = error.response
                blocked = response is not None and response.status_code in (403, 429)
                if response is not None and 'text/html' in response.headers.get('Content-Type', '').lower():
                    self.archive_html(site, url, response, 'blocked' if blocked else 'http_error')
                self.mark(url, 'blocked' if blocked else 'failed', str(error))
                restricted = restricted + 1 if blocked else 0
            except (requests.RequestException, ValueError) as error:
                self.mark(url, 'failed', str(error))
            except Exception as error:
                if not browser:
                    raise
                self.mark(url, 'failed', str(error))
            self.report()
            if restricted >= 3:
                self.site_status[site] = 'paused: repeated access restrictions; no challenge bypass attempted'
                break
        else:
            self.site_status[site] = 'paused: requested page limit reached'
        self.export_urls()
        self.report()
        if browser:
            crawler.close()


def acquire_lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open('a+b')
    if path.stat().st_size == 0:
        handle.write(b'0')
        handle.flush()
    handle.seek(0)
    try:
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
        handle.close()
        raise RuntimeError('A full crawl is already running.') from error
    return handle


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='backslashreplace')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--max-pages', type=int, default=0, help='Attempt limit per site for a short run. Default 0 means unlimited.')
    parser.add_argument('--retry-failed', action='store_true', help='Retry previously failed or blocked page requests.')
    args = parser.parse_args(argv)
    if args.max_pages < 0:
        parser.error('--max-pages cannot be negative')
    lock = acquire_lock(ROOT / '.tools' / 'full_crawl.lock')
    archive = HtmlArchive(ROOT / 'data' / 'html', ROOT / '.tools' / 'full_crawl.sqlite3')
    try:
        if args.retry_failed:
            archive.db.execute("UPDATE pages SET status='pending',error=NULL WHERE status IN ('failed','blocked')")
            archive.db.execute("UPDATE sitemaps SET status='pending',error=NULL WHERE status='failed'")
            archive.db.commit()
        def collect_site(site):
            worker = HtmlArchive(ROOT / 'data' / 'html', ROOT / '.tools' / 'full_crawl.sqlite3')
            try:
                worker.collect_site(site, args.max_pages, browser=(site == 'DarGlobal'))
            except Exception as error:
                worker.site_status[site] = 'paused: ' + str(error)
                worker.report()
                print(f'{site}: {error}', flush=True)
            finally:
                worker.db.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(collect_site, SITES))
        remaining = archive.db.execute("SELECT COUNT(*) FROM pages WHERE status IN ('pending','failed','blocked')").fetchone()[0]
        failed_maps = archive.db.execute("SELECT COUNT(*) FROM sitemaps WHERE status='failed'").fetchone()[0]
        status = 'finished' if remaining == 0 and failed_maps == 0 else 'incomplete'
        archive.report(status)
        print(f'Crawl status: {status}. See data/full_crawl_report.json.', flush=True)
        return 0 if status == 'finished' else 1
    except (KeyboardInterrupt, OSError):
        archive.report('interrupted; run the same command to resume')
        raise
    finally:
        archive.export_urls()
        archive.db.close()
        lock.close()


if __name__ == '__main__':
    raise SystemExit(main())
