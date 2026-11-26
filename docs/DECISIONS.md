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

**ADR-011 · Additive schema upgrades on start + PostgreSQL in CI.** v1.0.0 passed on SQLite but failed on Replit
PostgreSQL (VARCHAR(40) overflow in seed labels). Fix: widen columns, apply idempotent `ALTER … TYPE VARCHAR(n)` on start
(`app/core/schema_upgrade.py`), enforce VARCHAR limits in tests via a flush hook, and run the suite on PostgreSQL in CI.

**ADR-012 · Weekly full refresh.** A scheduled job re-fetches every public source once per week inside a
`force_refresh()` context (each request at most once per run), refreshes the policy feed, and renders every hub view for
the starter presets and all saved user scopes, so users hit warm caches. Sources not refreshed in 8 days show as
Overdue in Admin → Data sources. On-demand fetches with per-source TTLs still apply between runs.

**ADR-013 · Publisher-aware, sequential weekly pipeline.** `scripts/weekly_pipeline.py` runs preflight → approvals →
policy feed → baseline → scope warm-up → housekeeping in one process. "Due" mode re-downloads only cached requests
older than each source's cadence (7/30/90 days); new CMS data years are detected via the weekly catalog check
(new URL). Single-flight lock (Postgres advisory lock), 20-min budget, 25-scope cap, memory released per scope,
cache pruning, and no LLM calls (asserted via usage-event count).

**ADR-014 · One catalogue of user-facing AI messages.** `app/services/ai_errors.py` maps every failure (shared-key
quota/budget, no access, model choice, provider auth/billing/rate-limit/model/outage) to a title, a plain-language fix
and a direct link. Only the shared default key carries LaunchLens limits; provider limits on personal keys are labelled
as provider limits. A key the provider rejects is marked invalid and the request retries once with the user's other
valid key; forced onboarding resumes only when no usable key remains.

**ADR-015 · Healthcare model profiles instead of a "medical model".** The Anthropic, OpenAI and Gemini APIs used here
offer no dedicated medical model (MedGemma is open-weights/self-hosted). Domain fit comes from an accuracy-first default
profile (flagship models for answers and briefs), Balanced and Cost-saver alternatives, and healthcare guardrails in
every prompt (exact regulatory terms, labelled indication vs. trial endpoint, no efficacy/safety inference, no
patient-level speculation).

**ADR-016 · Single source of truth for "About".** `app/content/about.py` holds all product wording once; live facts
(approval window, shared-key limits, sources and refresh cadence, profiles, hubs) are read at request time. The About
page, landing page, footer, sign-up terms and briefs all render from it.

**ADR-017 · Email-free admin recovery.** Setting the `ADMIN_RESET_PASSWORD` secret and restarting resets (or re-creates)
the `ADMIN_EMAIL` admin with that password, re-activates it, forces a password change at next sign-in, and writes an
audit entry. It is applied once per secret value (hash stored), so later restarts don't undo the admin's new
password. `python -m scripts.admin_recovery --list` shows admin emails.
