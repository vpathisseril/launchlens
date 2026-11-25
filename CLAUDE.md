# CLAUDE.md — Master Guardrail (always loaded; keep under 120 lines)

## Project
LaunchLens: Launch Intelligence Research Workbench (5 research hubs + AI agents). Python 3.12 · FastAPI · Postgres · SQLAlchemy 2 · Alembic ·
Jinja2 + HTMX + Alpine · Tailwind · ECharts · Lucide. Hosted on Replit, deployed from GitHub. Branding: "By Varghese Pathisseril" · VP+AI.

## Token discipline (READ FIRST)
- Load ONLY the module doc named in the current task: `docs/modules/M<n>_*.md`. Do not scan the whole repo.
- Plan in 10 lines or fewer, then wait for "go". Output diffs or new files only. Summaries: 5 bullets or fewer.
- Do not paste long logs. Quote only the failing lines. Do not re-explain this file.
- The user will /clear between modules; persist decisions in `docs/DECISIONS.md` (ADR format, 1 short entry each).

## Repo layout
```
app/
  main.py            # app factory, middleware, routers
  core/              # config (pydantic-settings), security, db, logging, rate_limit
  models/            # SQLAlchemy models
  schemas/           # Pydantic v2
  services/          # business logic (pure, unit-tested)
  routers/           # thin HTTP layer
  agents/            # provider adapters, agent runtime, tool registry
  research/          # workspace, agent runtime, citation verifier, briefs
  hubs/              # access/ pricing/ policy/ targeting/ differentiation/ (each implements ResearchHub)
  data/              # connectors/, transforms/, curated models
  templates/ static/ # Jinja + Tailwind build output + themes
migrations/  tests/{unit,integration,e2e}/  docs/  scripts/
```

## Non-negotiables
1. No secrets in code, logs, tests, or commits. Config comes from env only (`.env.example` documents it).
2. Every route has authz: `require_user`, `require_module("<key>")`, or `require_admin`. Deny by default.
3. Forced onboarding: a logged-in user without `ai_key_status in {BYOK_VALID, DEFAULT_ACCEPTED}` is redirected to `/onboarding/ai-keys`.
4. Shared default key: model allowlist + `max_tokens_per_call` + `calls_per_day` per user (admin-editable). BYOK: unlimited.
5. User API keys are Fernet-encrypted (`FERNET_KEY`), validated by a 1-token test call, and shown masked only.
6. Every LLM call is logged to `usage_events` (user, provider, model, tokens in/out, est. cost, key_type), never the prompt text by default.
7. Every number and AI claim must carry lineage: `source_id`, `dataset`, `vintage`, `retrieved_at`. No synthetic data. Respect suppression (<11).
8. Audit log for admin actions and approvals (who, what, when, before/after).
9. Accessibility: WCAG 2.2 AA, keyboard-navigable, contrast ≥ 4.5:1, aria labels on icon buttons.
10. Show a "Research prototype — not promotional material, not legal, financial, or medical advice" disclaimer on hubs and exports.
11. Policy content is neutral and dated: status + status_date from the official source; proposals are never presented as law.
12. The agent may only state numbers that appear in tool output; the citation verifier must pass, or a warning badge is shown.

## Coding standards
- Type hints everywhere; `mypy --strict` on `app/services`, `app/agents`, `app/pharma`.
- `ruff check` + `ruff format`; functions ≤ 40 lines; no business logic in routers.
- Async SQLAlchemy sessions; Alembic migration for every model change; no raw SQL string interpolation.
- HTTP clients: `httpx.AsyncClient` with timeouts, retries (tenacity), and a polite User-Agent; cache responses in `source_cache`.
- Errors: domain exceptions → handled centrally → friendly UI toast + structured JSON log (no stack traces to users).

## Testing
- pytest + pytest-asyncio; factories via factory-boy; Playwright for e2e; at least 80% coverage on services.
- Each Gherkin scenario in a module doc maps to one test named `test_<story_id>_<scenario_slug>`.
- External APIs are mocked in unit/integration tests with recorded fixtures (respx); a nightly `-m live` suite hits the real endpoints.

## Security checklist (run before each PR)
`ruff` · `mypy` · `pytest --cov` · `bandit -r app` · `pip-audit` · `gitleaks detect` · headers (CSP, HSTS, X-Frame-Options DENY,
Referrer-Policy, Permissions-Policy) · CSRF on POST · auth rate limit (5/min/IP) · session rotation on login.

## Git
Conventional commits (`feat(m3): …`). One branch and PR per story group. CI must be green. No force-push to `main`.
Commit trailer: `Co-authored-by` as the tooling requires.

## Definition of Done (every story)
AC tests pass · coverage holds · lint/type/security clean · docs updated (module doc + DECISIONS) · screenshots for UI stories.

## UI rules
- Theme tokens live in `static/themes/*.css` (CSS variables). Never hard-code colors in templates.
- Fonts: Inter (UI), Source Serif 4 (display headings), JetBrains Mono (numbers/code). Self-host via `static/fonts`.
- Icons: Lucide only, 20px, stroke 1.75. Images: abstract or scientific illustrations that you own or that are CC0/Unsplash-licensed; no real patients or brand logos.
- KPI tiles: label, value, delta vs. benchmark, sparkline, and source tooltip. Charts: ECharts with the theme palette, with no 3D and no pie charts over 5 slices.
- Footer on every page: "By Varghese Pathisseril · VP+AI".

## Module index (load on demand)
Platform: M0 Core · M1 AI Key Vault & Usage · M2 Agent Builder · M4 Admin Console & Analytics · M5 Public Data Pipeline
Research: R0 Workspace + Hub Contract · R1 Market Access · R2 Pricing Policy (IRA + MFN) · R3 Policy Radar (MFN · Tariffs · PBM)
          · R4 Targeting & Time-to-Peak · R5 Market Understanding & Differentiation
Rule: every hub implements the R0 `ResearchHub` contract; no hub-specific UI shell.
Supporting: docs/THEMES.md · docs/DATA_SOURCES.md · docs/WORKFLOWS.md
