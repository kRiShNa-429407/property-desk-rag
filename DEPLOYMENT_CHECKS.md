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
