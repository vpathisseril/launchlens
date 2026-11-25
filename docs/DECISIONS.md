# Architecture Decision Records

**ADR-001 · Server-rendered UI with HTMX instead of a SPA.** Fewer moving parts on Replit, no build step, and the app
works under a strict CSP. ECharts and Lucide are self-hosted under `/static/vendor`.

**ADR-002 · Synchronous SQLAlchemy 2.0.** Simpler and fast enough for a prototype. External fetches are cached, so
request latency is dominated by the first fetch of each source.

**ADR-003 · Schema via `create_all` + idempotent seed.** Alembic migrations are on the roadmap. The seed only inserts
missing rows, so restarts are safe.

**ADR-004 · Plain CSS design system with theme tokens, not a Tailwind build.** Four themes are CSS-variable files;
components read tokens only, so a new theme is one file.

**ADR-005 · In-memory rate limiter.** Correct for a single Autoscale instance. Move to Postgres or Redis if scaled out.

**ADR-006 · Provider adapters over raw HTTPS (no vendor SDKs).** One interface for Anthropic, OpenAI and Gemini.
Keys are passed per call and never logged. BYOK validation uses free model-list endpoints.

**ADR-007 · Provider-agnostic agent: deterministic router + server-side tools + citation verifier.** Instead of
provider-specific tool calling, the server picks hub tools by keyword (no LLM routing cost), runs them, and sends the
model a numbered, sourced digest. A verifier checks citation indices and that numbers appear in tool output, which
guards against invented figures.

**ADR-008 · Lineage-first fetch layer.** Every connector goes through `cached_json`, which stores the payload with
retrieval time and serves the last good copy (flagged STALE) on outage. Dataset IDs that change by release are
resolved at runtime (CMS catalog) or set by env var (PLACES, ACS year).

**ADR-009 · Formulary ingest filtered to tracked drugs.** CMS SPUF files are large. Ingest streams the ZIP and keeps
only plans plus rows for RxCUIs mapped from the scope's drugs, which keeps Replit storage small.

**ADR-010 · PDF via print stylesheet.** The brief's print view produces a clean PDF from the browser and avoids heavy
native PDF dependencies on Replit. Markdown export is also provided.
