import gzip
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock

from crawl_archive import HtmlArchive, normalize_url, page_priority


class ArchiveTests(unittest.TestCase):
    def test_pagination_is_kept_and_tracking_is_removed(self):
        self.assertEqual(normalize_url('https://wasalt.sa/en?page=2&utm_source=test#top', 'wasalt.sa'),
                         'https://wasalt.sa/en?page=2')

    def test_arabic_and_individual_listing_urls_are_in_scope(self):
        for url in ['https://wasalt.sa/property/sale/example-123', 'https://wasalt.sa/عقارات']:
            self.assertIsNotNone(normalize_url(url, 'wasalt.sa'))
        self.assertEqual(page_priority('https://wasalt.sa/en/property/sale/example-123'), 2)

    def test_external_urls_and_assets_are_excluded(self):
        self.assertIsNone(normalize_url('https://wasalt.sa.example.com/en', 'wasalt.sa'))
        self.assertIsNone(normalize_url('https://example.com/', 'wasalt.sa'))
        self.assertIsNone(normalize_url('https://wasalt.sa/logo.png', 'wasalt.sa'))

    def test_complete_html_including_scripts_is_preserved(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            archive = HtmlArchive(root / 'html', root / 'state.sqlite3')
            html = '<!DOCTYPE html><html><head><script type="application/ld+json">{"price":100}</script></head><body>Public text</body></html>'
            response = Mock(status_code=200, text=html)
            archive.archive_html('Wasalt', 'https://wasalt.sa/en', response, 'saved')
            saved = (root / 'html' / 'wasalt_html.txt').read_text(encoding='utf-8')
            self.assertIn(html, saved)
            self.assertIn('Outcome: saved', saved)
            archive.db.close()

    def test_resume_keeps_completed_urls_and_deduplicates_discovery(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            archive = HtmlArchive(root / 'html', root / 'state.sqlite3')
            archive.discover('Wasalt', ['https://wasalt.sa/en', 'https://wasalt.sa/en#top'])
            archive.mark('https://wasalt.sa/en', 'saved')
            archive.db.close()
            resumed = HtmlArchive(root / 'html', root / 'state.sqlite3')
            self.assertEqual(resumed.db.execute('SELECT COUNT(*) FROM pages').fetchone()[0], 1)
            self.assertEqual(resumed.db.execute('SELECT status FROM pages').fetchone()[0], 'saved')
            resumed.db.close()

    def test_compressed_cdn_sitemaps_are_discovered(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            archive = HtmlArchive(root / 'html', root / 'state.sqlite3')
            xml = b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>https://wasalt.sa/en/property/sale/example-123</loc></url></urlset>'
            response = Mock(content=gzip.compress(xml))
            crawler = Mock()
            crawler.get.return_value = response
            archive.collect_sitemaps('Wasalt', crawler, ['https://cdn.wasalt.sa/sitemap/products.xml.gz'], Mock())
            self.assertEqual(archive.db.execute('SELECT COUNT(*) FROM pages').fetchone()[0], 1)
            archive.report()
            report = json.loads((root / 'full_crawl_report.json').read_text(encoding='utf-8'))
            self.assertEqual(report['sites']['Wasalt']['discovered'], 1)
            archive.db.close()


if __name__ == '__main__':
    unittest.main()
