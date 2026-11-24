# Property Desk

A Streamlit chatbot for DarGlobal and Wasalt. Wasalt listings live in MongoDB; company and service questions use local page text and a free OpenRouter model.

Live app: https://property-desk-0y6t.onrender.com

Deployed on Render Free with MongoDB Atlas. Public database answers, model responses and chat reset have been verified.

## Run

Start MongoDB, then run:

```bash
pip install -r requirements.txt
streamlit run app.py
```

Add connection settings to `.env` beside `app.py`:

```dotenv
OPENROUTER_API_KEY=your_key
MONGODB_URI=mongodb://127.0.0.1:27017
MONGODB_DATABASE=property_desk
```

Open http://localhost:8501. Keys and database credentials stay on the server.

For key rotation, use `OPENROUTER_API_KEY_1` through `OPENROUTER_API_KEY_4`. Blank and duplicate keys are ignored. A rate-limited request tries another key once per key. Limited keys follow a numeric Retry-After duration, or a default cooldown of 60 seconds (one hour for daily quotas). Keys on the same account share its quota.

Chat uses `inclusionai/ling-3.2-flash:free` with free NVIDIA and Gemma fallbacks. Input and output price limits are zero. For Wasalt questions, a structured model response interprets the intent and filters; MongoDB calculates the counts, averages and listing results. This interpretation uses a free-model request. No model-generated MongoDB commands are executed.

The interpreter uses schema-capable free models: `baidu/ernie-5.5-flash-preview:free`, `nvidia/nemotron-3.5-super-vl:free` and `inclusionai/ling-3.2-flash:free`. Output must pass local validation even when the provider advertises schema support. All model requests use a priority-ordered fallback list and server-side keys.

## Wasalt data

Original JSON files stay in `data/wasalt-export/`. Cleaning keeps every field, recovers nested listing arrays, merges complementary details, and keeps distinct conflicting versions in history. Explicit null, false and zero values remain meaningful.

```bash
python clean_wasalt.py data/wasalt-export --output data/wasalt-clean
python wasalt.py data/wasalt-clean
```

The cleaned outputs are:

- `records.jsonl.gz`: one merged record per listing or project ID, with source filenames.
- `history.jsonl.gz`: distinct original versions where duplicate records differed.
- `metadata.jsonl.gz`: city catalogs, pagination and response metadata.
- `report.json`: counts, cleaning rules and checksums.

CLI imports validate the complete input and sync in place, avoiding a second full collection copy. Run imports while the chatbot is offline. Stale IDs are removed only after every batch succeeds; an interrupted import can be retried. The database contains `wasalt`, `wasalt_history` and `wasalt_metadata`. Every listing retains its full original fields under `raw`, alongside searchable fields for city, district, property type, purpose, price and bedrooms.

Search interpretation supports city, district, property type, sale/rent purpose, price, bedroom ranges, sorting and city count comparisons. Validated filters run through the same MongoDB executor as the basic query rules. Without a model key, the limited basic rules remain available; they are not a substitute for natural-language interpretation.

Follow-up searches carry the previous validated filters within that browser session. Missing facts, ambiguous requests, unsupported filters and unavailable models receive clear replies without guessed answers. Area, amenity, proximity and live-availability filters are not currently supported. Malformed model output cannot run a query.

Missing prices remain unknown and are excluded from averages. Unusually low SAR sale prices for apartments, villas and buildings are flagged for agent confirmation and excluded from averages without changing the original data. Counts exclude project cards and describe the imported catalog; the live website total and availability can differ. Production auto-seeding is disabled: import the database before deployment. Docker Compose enables seeding only for local development.

MongoDB Community is running locally with its files in `.tools/mongodb/`. After a restart, start MongoDB before Streamlit. Docker Compose below starts both services together.

## Other data

- `data/darglobal.json` supplies DarGlobal knowledge. It accepts a JSON array or an object containing a `pages` array with URL, title and text.
- `data/knowledge.txt` supplies general Wasalt service pages. Older DarGlobal text records are skipped while its JSON is present.
- `data/full_scraped_text.txt`, `data/html/` and `data/full_crawl_report.json` belong to the separate ongoing crawl.

To resume that crawl:

```bash
pip install -r requirements-crawl.txt
python scrape.py
```

Keep `.tools/full_crawl.sqlite3` while the crawler is running. It uses SQLite only for crawl checkpoints, independently of the chatbot's MongoDB data.

## Docker

```bash
docker compose up --build -d
```

Compose runs the chatbot and MongoDB with a persistent database volume. The image includes the cleaned Wasalt seed files; raw exports, crawl archives and secrets are excluded. The MongoDB port is internal to Compose. For a later hosted deployment, set MONGODB_URI to a reachable database connection. No hosting changes have been published.

Before hosting, run `python preflight.py --production --model`. This checks the connection, record count, indexes, storage headroom and a free-model response without deploying. See [DEPLOYMENT_CHECKS.md](DEPLOYMENT_CHECKS.md) for verified checks and remaining hosting tests.

## Tests

With local MongoDB running:

```bash
python -m unittest discover -s tests -v
```

Database tests use disposable databases named property_desk_test_* and leave the application database untouched. MONGODB_TEST_URI can override the test server.

The code is split between app.py (interface), rag.py (retrieval), llm.py (model requests), prompts.py (answer style), catalog.py (question rules and MongoDB queries), mongo_store.py (connections and imports), clean_wasalt.py (lossless cleaning) and wasalt.py (listing conversion and loading).
