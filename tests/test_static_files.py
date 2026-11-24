import asyncio
import gzip
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from starlette.staticfiles import StaticFiles
from streamlit.web.server.starlette.starlette_gzip_middleware import MediaAwareGZipMiddleware


class StaticFileTests(unittest.IsolatedAsyncioTestCase):
    async def request(self, app, headers, method='GET'):
        messages = []

        async def receive():
            await asyncio.Event().wait()

        async def send(message):
            if not messages:
                self.assertEqual(message['type'], 'http.response.start')
            else:
                self.assertEqual(message['type'], 'http.response.body')
            messages.append(message.copy())

        scope = {'type': 'http', 'asgi': {'version': '3.0', 'spec_version': '2.4'},
                 'http_version': '1.1', 'method': method, 'scheme': 'http',
                 'path': '/styles.css', 'raw_path': b'/styles.css', 'root_path': '',
                 'query_string': b'', 'headers': headers, 'extensions': {}}
        await app(scope, receive, send)
        return messages[0], b''.join(item.get('body', b'') for item in messages[1:])

    async def test_static_get_head_and_browser_cache(self):
        with TemporaryDirectory() as directory:
            content = b'body { color: navy; }\n' * 200
            Path(directory, 'styles.css').write_bytes(content)
            app = MediaAwareGZipMiddleware(StaticFiles(directory=directory))
            for encoding in (b'gzip', b'identity'):
                with self.subTest(encoding=encoding):
                    headers = [(b'accept-encoding', encoding)]
                    start, body = await self.request(app, headers)
                    self.assertEqual(start['status'], 200)
                    response_headers = dict(start['headers'])
                    if encoding == b'gzip':
                        self.assertEqual(response_headers[b'content-encoding'], b'gzip')
                        self.assertEqual(gzip.decompress(body), content)
                    else:
                        self.assertEqual(body, content)
                    cached, body = await self.request(app, headers + [(b'if-none-match', response_headers[b'etag'])])
                    self.assertEqual(cached['status'], 304)
                    self.assertEqual(body, b'')
                    head, body = await self.request(app, headers, method='HEAD')
                    self.assertEqual(head['status'], 200)
                    self.assertEqual(body, b'')
