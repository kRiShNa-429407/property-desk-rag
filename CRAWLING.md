# Full HTML collection

Run or resume the complete public crawl:

```bash
pip install -r requirements-crawl.txt
python scrape.py --full-html
```

DarGlobal is collected through ordinary browser rendering, using Edge on Windows or Playwright Chromium on other systems. For the latter, install the browser with `playwright install chromium`. Wasalt uses direct HTTP requests. The two domains run in parallel, with one worker per domain and at least a one-second pause between page requests. No CAPTCHA interaction or protection-bypass tools are used.

This has no page-count limit. It discovers same-site URLs from robots.txt sitemaps and links, including individual listings, Arabic pages and pagination. The Wasalt sitemap set is hosted on its CDN; both plain XML and compressed XML are supported. Company and information pages are visited before individual listing pages.

Output files:

- `data/html/darglobal_html.txt`: complete browser-rendered HTML from DarGlobal, with URL, date, HTTP status and outcome for each page.
- `data/html/wasalt_html.txt`: complete server-returned HTML from Wasalt.
- `data/full_scraped_text.txt`: readable text extracted from successful pages.
- `data/full_crawl_urls.txt`: discovered URL inventory and outcome for each URL.
- `data/full_crawl_report.json`: counts, pending URLs, sitemap errors and collection status.

The HTML archives preserve scripts and embedded JSON; they are not shortened summaries. Challenge/error responses are explicitly labelled and do not enter the readable text file. The existing chatbot knowledge file remains available during this long collection. The large archives are excluded from Git and Docker builds.

Progress is checkpointed in `.tools/full_crawl.sqlite3`. Run the same command after an interruption to resume; already completed URLs are not fetched again. Only one crawler can run at a time. Use `python crawl_archive.py --retry-failed` to explicitly retry blocked or failed URLs later.

One Wasalt product sitemap alone contains 60,000 English listing URLs. Arabic variants, other listing types and category pages add more. At a minimum one-second pause per request, the full run can take days and require substantial local disk space. Collection pauses if less than 2 GB of disk remains or after repeated access restrictions. It does not bypass site protections, sign in or call paid services.

`finished` means the discovered permitted queue is exhausted without failed requests or sitemap failures. `incomplete` and non-zero pending/failed/blocked counts mean the site was not fully collected. Unlinked, authenticated or inaccessible content cannot be guaranteed. Additional public URLs discovered later may expand the queue. A failed URL remains in the inventory rather than silently disappearing.

On the development machine, the background process ID is saved in `.tools/full_crawl.pid`; logs are in `.tools/full_crawl.out.log` and `.tools/full_crawl.err.log`. The computer must remain awake for the process to continue. After a reboot or interruption, the same command resumes the saved queue.
