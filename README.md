# Property Desk

A small RAG chatbot for DarGlobal and Wasalt. Scraped page text lives in `data/knowledge.txt`. Python splits it into passages, TF-IDF retrieves relevant passages, and a free model on OpenRouter answers through a Streamlit chat interface.

No vector database, paid embeddings, separate backend or JavaScript build is needed.

## Run locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

Set your key in `.env` beside `app.py` before chatting:

```dotenv
OPENROUTER_API_KEY=your_openrouter_key
```

Open http://localhost:8501. The app reads `.env` automatically. The key stays on the server; visitors only see the chat. The file is excluded from Git and Docker images. An existing `OPENROUTER_API_KEY` environment variable takes precedence, including when Docker Compose injects it.

The model is fixed to `openrouter/free`. Both input and output provider price limits are zero, and there are no paid model fallbacks. Local retrieval is free. Free models have availability and rate limits. A configured key is required for AI answers.

## Collect the data

```bash
python scrape.py
python scrape.py --max-pages 0
```

The default is 100 attempted HTML pages per site. Use `0` to visit all discovered eligible HTML pages. The scraper checks robots.txt, follows same-domain links and sitemaps, skips forms, deduplicates pages, and pauses between requests. If DarGlobal HTML pages are blocked, it extracts text from two official, publicly downloadable PDF brochures. It does not collect authenticated content or bypass challenges. URL queries are excluded, so paginated, JavaScript-only and unlinked pages are not guaranteed to be collected.

**Collection status:** the bundled text file contains 50 Wasalt HTML pages and 85 text-bearing pages from two official DarGlobal brochures, plus a clearly labelled verified summary of DarGlobal's corporate update published 26 October 2026. That update fills a company-wide coverage gap: the brochures did not contain the group's gross development value. Its source HTML remains blocked to direct requests. This is a scoped public-data collection, not a complete scrape or a live inventory. `data/collection_notes.md` records provenance; `data/scrape_report.json` records attempted pages and whether the page limit was reached. Blocked sources retain their previous entries while successful sources are refreshed. A collection missing either source exits with status 1 and also saves newly collected pages separately to `knowledge.partial.txt`.

## Docker

```bash
docker compose up --build -d
```

Open http://localhost:8501. Optionally copy `.env.example` to `.env` and set the key before starting. Secrets are excluded from the image. The app runs as a non-root user and has a health check.

## Files

- `app.py`: Streamlit chat and source display.
- `scrape.py`: public-page crawler and text export.
- `rag.py`: text loading, chunking and retrieval.
- `llm.py`: free-only OpenRouter requests.
- `data/knowledge.txt`: the knowledge used by the chatbot.
- `tests/test_app.py`: retrieval, model request and Streamlit checks.

Run checks with `python -m unittest discover -s tests -v`.

With the configured free OpenRouter key, run `python tests/live_check.py` to check real model responses. Results are saved without credentials in `tests/live_results.json`. Container verification results are in `tests/docker_results.json`.

## Free deployment

`render.yaml` configures a Docker web service on Render's Free plan. Set `OPENROUTER_API_KEY` as a runtime secret. Visitors never enter API keys. Free instances sleep after inactivity and may take about a minute to wake up. This project does not provision paid compute, paid storage or a database.

A temporary Cloudflare Quick Tunnel can expose the running Streamlit process for testing. That URL is temporary and stops working when the host or tunnel stops; it is not an always-on cloud deployment.

The current test URL and verification details are in [DEPLOYMENT.md](DEPLOYMENT.md).
