# Deployment checks

Deployed and verified on 20 November 2026: https://property-desk-0y6t.onrender.com

| Check | Result |
|---|---|
| Atlas connection | Passed; hosted URI is configured in local .env. |
| Wasalt data | 73,247 records, 2,278 historical versions and 3,784 metadata records imported. |
| Data preservation | Counts unchanged after index optimization; 19 complete Atlas records matched the cleaned source exactly. |
| Free-tier storage | About 355 MB of logical data and indexes. Removed only sample_mflix with the user's approval. |
| Imports | CLI validates before syncing in place; no second full collection copy. Run imports while the chatbot is offline. A failed sync may leave partial updates; retry it before reopening the chatbot. |
| Startup | Production auto-seeding disabled. Empty catalogs and local-only production URIs fail clearly. |
| Cache | Retrieval cache holds one index version. MongoDB query results are not cached. |
| Sessions | Independent browser sessions and New chat reset verified. |
| API failures | Key rotation, cooldowns, free-only model fallbacks and friendly errors tested. |
| Live free model | Response received through configured free-model routing. |
| Concurrent Atlas searches | Eight requests with four workers; mean 1.41 seconds, slowest 1.86 seconds from this computer. Render timings remain unverified. |
| Automated tests | 63 passed against isolated local test databases. |
| Render blueprint | Validated against Render's public schema; configured for manual deployment. Live Render settings have not been changed. |
| Docker | Image successfully built and deployed by Render. |
| Render idle wake-up | Still requires testing after an authorized deployment. Free services sleep after 15 minutes without inbound traffic. |
| Public browser checks | Passed: Riyadh count 30,181; AIDA answer Muscat, Oman; New chat clears messages. Atlas network access from Render verified. |

Run read-only readiness checks before deploying:

```bash
python preflight.py --production
python preflight.py --production --model
```

The second command sends a free-model test request. Credentials are never printed. In Render, configure MONGODB_URI, MONGODB_DATABASE and every OpenRouter key used by the app; the local .env file is not included in the image.

After deployment, verify one Wasalt database answer and one DarGlobal model answer, refresh and reconnect, then repeat after more than 15 minutes of inactivity. The Streamlit health endpoint alone does not prove database or model readiness.

## Local fixes verified on 24 November 2026

These checks describe local validation before the server update.

- Natural bedroom wording supports number words, “at least” and “no more than”. Both tested Riyadh villa questions return 3,603.
- City count comparisons return Riyadh 30,181, Jeddah 26,605, difference 3,576. Counts refer to the imported snapshot.
- Follow-up questions retain city, property type and budget. Updated bedroom or budget requirements replace the previous requirement. New chat clears the conversation.
- Listing replies include areas where recorded and direct listing links.
- SAR sale prices below 10,000 for apartments, villas and buildings are flagged for review. Original prices and records remain unchanged. These prices are excluded from averages with an explanation; listing counts still include them.
- Streamlit 1.59.1 is paired with Starlette 1.3.1. Static file tests cover gzip, uncompressed responses, HEAD and conditional requests. This addresses the compatibility risk documented in https://github.com/streamlit/streamlit/issues/16341; Render still needs verification after deployment.
- 74 automated tests passed against isolated local MongoDB. Seven full-catalog checks passed against a local copy of all 73,247 normalized records.
- Six questions passed in a real local browser, including the three-turn villa search. Health, New chat and refresh passed, with no failed local HTTP responses or ASGI errors during the browser checks.

Local verification logs and results are in `.tools/local-regression.log`, `.tools/local-check-results.json` and `.tools/local-browser-results.json`. No Atlas imports, public requests, Git pushes or deployment changes were made for these checks.

## Natural-language handling verified locally on 25 November 2026

The local app uses a free model to interpret Wasalt questions into a constrained schema, then validates every field before constructing read-only MongoDB filters. Counts, averages and rankings are calculated by MongoDB. The basic query rules remain available when no model key is configured.

Validated search state stays in the browser's Streamlit session for follow-ups. Unclear budgets prompt clarification; unsupported features and unrelated questions receive brief replies without invented facts. No matching listings are distinguished from confirmed live unavailability. Invalid model output and unavailable models do not execute database queries.

- 86 automated tests passed, including malformed output, rejected database operators, strict/inclusive ranges, follow-up state and missing results.
- Eight actual free-model interpretation checks passed. These include varied purchase wording, city comparisons, a bedroom range, a pool request, an unrelated question, an AIDA knowledge question, changed follow-up filters and a vague budget.
- Ten local browser questions passed, including the villa follow-up chain, no matching listings, unsupported pool requests, an unrelated question and a real AIDA model answer. New chat and refresh passed; no failed local HTTP responses or ASGI errors occurred during these checks.
- Schema-capable model metadata was checked against OpenRouter's public model API. The interpreter uses only free models, requires support for its response parameters, and also performs local validation. Model fallback requests follow https://openrouter.ai/docs/guides/routing/model-fallbacks and structured output follows https://openrouter.ai/docs/guides/features/structured-outputs.

Results are in `.tools/question-interpretation-results.json` and `.tools/local-browser-results.json`. The preview runs on http://127.0.0.1:8503 with an isolated local copy of the catalog. Atlas and original source files were unchanged by these checks. Deployment of this update to the existing Render service was requested on 25 November 2026.
