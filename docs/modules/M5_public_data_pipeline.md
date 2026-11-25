# M5 — Public Data Pipeline (ingest · cache · lineage)
Load with: CLAUDE.md + this file + docs/DATA_SOURCES.md.

## Pattern
`connector (fetch) → raw cache (source_cache) → transform (pandas/polars) → curated tables → metrics → UI/agents`

## Data model
- `data_sources(id, key, name, publisher, url, license_note, refresh_cadence, last_refreshed_at, latest_vintage, status)`
- `source_cache(id, source_key, request_hash, params_json, payload (JSONB or object ref), retrieved_at, expires_at)`
- `ingest_runs(id, source_key, started_at, finished_at, rows_in, rows_out, status, error)`
- Curated tables grouped by hub: access (`cur_formulary_restrictions`, `cur_plan_enrollment`, `cur_medicaid_sdud`), pricing (`cur_negotiation_drugs`,
  `cur_drug_spend`, `cur_asp`, `cur_nadac`), policy (`policy_items`, `doc_chunks`, `cur_trade_hts30`), targeting (`cur_partd_prescriber_drug`, `cur_npi`,
  `cur_places_county`, `cur_acs_county`, `cur_open_payments_agg`), differentiation (`cur_labels`, `cur_trials`, `cur_exclusivity`, `cur_pubmed_counts`)
- `doc_chunks(id, source_key, doc_id, section, text, url, published_at, retrieved_at)` for citation-grade document retrieval (Postgres full-text search; pgvector optional)
- Each curated row carries `source_key`, `vintage`, and `retrieved_at` (the lineage columns).

## Rules
- Use official APIs or bulk files only. Be polite: limit concurrency, back off on retries, and identify the app in the User-Agent.
- Scope by research project: only fetch the drugs (by RxNorm/NDC), states, and specialties the launch needs, to keep storage within Replit limits.
- Large bulk files (e.g., Part D by provider and drug): use the dataset API filters where available; otherwise stream and filter on the fly.
- Normalize drugs to RxNorm (RxNav API); normalize prescribers to NPI + NUCC taxonomy; normalize geography to county FIPS.
- Keep suppression flags. Never impute values for suppressed cells.
- The ingestion CLI is `python -m app.pharma.ingest --source <key> --project <id>`. It also runs from the admin "Refresh now" queue.

## AC
```gherkin
Scenario: Cached fetch
  Given a request with the same params was fetched within its TTL
  Then no external call is made and the cache hit is logged
Scenario: Source outage
  Given an endpoint returns 5xx three times
  Then the run is marked failed, the admin sees the source in red, and the UI falls back to the last good vintage with a banner
Scenario: Lineage completeness
  Then 100% of curated rows have non-null source_key, vintage, retrieved_at (enforced by a DB constraint and a test)
```
