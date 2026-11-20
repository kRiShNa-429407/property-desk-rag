"""Public-page scraper. Run python scrape.py to start or resume collection."""
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

from bs4 import BeautifulSoup
import requests

ROOT = Path(__file__).parent
AGENT = 'PropertyDeskBot/1.0'
SEPARATOR = '\n' + '=' * 72 + '\n'


def extract_page(html):
    soup = BeautifulSoup(html, 'html.parser')
    security_marker = soup.select_one('iframe#main-iframe') or any((marker in html.lower() for marker in ['_incapsula_resource', 'cf-chl-', 'verify you are human', 'access denied']))
    if security_marker and len(soup.get_text(' ', strip=True).split()) < 80:
        raise ValueError('Source returned an access-control page')
    meta = soup.find('meta', attrs={'name': lambda value: value and value.lower() == 'robots'})
    if meta and any((rule in meta.get('content', '').lower() for rule in ['noai', 'nosnippet'])):
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
    return (title, text, links)


class Crawler:

    def __init__(self, delay=1):
        self.session = requests.Session()
        self.session.headers['User-Agent'] = AGENT
        self.delay = delay

    def get(self, url):
        time.sleep(self.delay)
        response = self.session.get(url, timeout=(10, 30))
        response.raise_for_status()
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
                return (policy, [origin + '/sitemap.xml'])
            raise
        if '<html' in response.text.lower():
            raise ValueError('robots.txt returned an HTML access-control page')
        policy = RobotFileParser()
        policy.parse(response.text.splitlines())
        return (policy, policy.site_maps() or [origin + '/sitemap.xml'])


if __name__ == '__main__':
    from crawl_archive import main
    args = [arg for arg in sys.argv[1:] if arg != '--full-html']
    raise SystemExit(main(args))
