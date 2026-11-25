# Workflows: step-by-step phase prompts (paste one at a time into Claude Code; run /clear between phases)
Each prompt names exactly which files to load, which keeps token use low.

---
## W0 · One-time setup (you, about 20 minutes)
1. Create the GitHub repo `launchlens` (private) and clone it locally. Copy this prompt pack in.
2. Install: Python 3.12, uv, Node 20 (for the Tailwind build), git, Claude Code.
3. Get free keys: openFDA, Census, Congress.gov, Regulations.gov (api.data.gov). Have at least one LLM key (your BYOK) for testing.
4. Open Claude Code in the repo and paste `00_MASTER_PROMPT.md`.

## W1 · P0 Bootstrap & verify
> Read CLAUDE.md and docs/DATA_SOURCES.md. Scaffold the repo layout, pyproject (uv), ruff/mypy/pytest config, pre-commit
> (ruff, gitleaks), GitHub Actions CI (lint, type, test, bandit, pip-audit), .env.example, and a Makefile (`make dev|test|check|migrate`).
> Write `scripts/verify_sources.py` to ping each source and print a table: status, latest vintage, rate-limit notes. Run it and show the table.
> **Gate:** CI green on an empty app; the source table has no red rows (or documented fallbacks).

## W2 · P1 Requirements
> Read CLAUDE.md and docs/modules/*.md headers + AC only. Produce docs/BACKLOG.md: stories with ID, MoSCoW, estimate (S/M/L),
> dependencies, and Gherkin AC. Flag any conflicts or gaps as questions (max 5).
> **Gate:** I approve the backlog.

## W3 · P2 Design
> Read CLAUDE.md + docs/BACKLOG.md. Produce docs/ARCHITECTURE.md (Mermaid C4 context + container), docs/ERD.md (Mermaid),
> an OpenAPI skeleton, low-fi wireframes as HTML partials for: Landing, Signup, Pending, Onboarding-AI-Keys, Home, Agent Builder,
> Command Center, Hub page (R1 example), Research Project, Brief editor, Admin Overview. Add docs/THREAT_MODEL.md (STRIDE table + mitigations).
> **Gate:** I review the wireframes in the browser (`make dev`).

## W4 · P3 Develop (repeat per module, in this order: M0 → M1 → M5 → R0 → R1 → R2 → R3 → R4 → R5 → M2 → M4)
> Read CLAUDE.md and docs/modules/<M|R><n>_*.md ONLY (for R1–R5 also read R0 and the matching DATA_SOURCES section). Implement stories <IDs>. Plan first (≤10 lines), wait for "go", then
> code + migrations + tests. Run `make check`. Show the diff summary and test results. Update docs/DECISIONS.md.
> **Gate (per module):** all AC tests pass, `make check` is clean, screenshots attached, PR opened.

## W4b · Research quality check (after each hub)
> Read CLAUDE.md + the hub doc. Run 10 benchmark questions for this hub against the agent (write them to tests/research/<hub>.yaml with
> expected sources). Report citation-verifier pass rate, unsupported-answer handling, and latency. Fix anything below 95% pass.
> **Gate:** ≥95% citation pass, 0 fabricated numbers.

## W5 · P4 Test
> Read CLAUDE.md. Add Playwright e2e for: signup→pending→auto-approve→forced onboarding→create project→pin findings from R1–R5→ask agent→export brief→admin audit.
> Run axe accessibility checks on the 8 key screens and a contrast check on all 4 themes. Report coverage by package.
> **Gate:** e2e green, coverage ≥ 80% on services, zero serious axe issues.

## W6 · P5 Code quality & security
> Read CLAUDE.md. Run the full security checklist. Produce docs/SECURITY_REVIEW.md with an OWASP ASVS L1 checklist (pass/fail/N.A.),
> findings, and fixes. Verify that the security headers and CSP work with HTMX/Alpine (no unsafe-eval; use nonces).
> **Gate:** no high or critical findings open.

## W7 · P6 Git release
> Squash-merge the module PRs, tag v0.1.0, and generate CHANGELOG.md and release notes. Update README with: problem, solution,
> architecture diagram, screenshots, data sources, model card link, security posture, a "How I built it (SDLC)" section, and the
> signature "By Varghese Pathisseril".

## W8 · P7 GitHub → Replit (you + Claude Code)
1. In Replit, create a Repl with **Import from GitHub**, pick `launchlens`, and choose the `main` branch.
2. Add a **PostgreSQL** database in Replit. It sets `DATABASE_URL`.
3. **Secrets:** `SECRET_KEY`, `FERNET_KEY`, `ADMIN_EMAIL`, `ADMIN_PASSWORD`, `OPENFDA_API_KEY`, `CENSUS_API_KEY`, `CONGRESS_API_KEY`, `REGULATIONS_API_KEY`,
   the optional default-key provider secrets (`DEFAULT_ANTHROPIC_KEY` and/or others), and `SMTP_*`.
   Generate keys locally: `python -c "import secrets;print(secrets.token_urlsafe(48))"` and
   `python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"`.
4. `.replit` run command: `alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}`.
5. **Deploy:** an Autoscale deployment for the web app, plus **Scheduled** deployments: `python scripts/approval_sweep.py` (hourly) and
   `python -m app.data.refresh --policy --changes` (daily, 6 AM ET) for the policy feed, vintage checks, and watchlist alerts.
6. Smoke test: `/healthz`, the signup flow, admin login, a default-key quota hit, one question per hub, and one exported brief.
7. Future releases: push to GitHub → pull in Replit (Git pane) → redeploy. Keep `main` deployable.

## W9 · Portfolio polish
> Produce docs/DEMO_SCRIPT.md (a 3-minute walkthrough: "launches fail on access and pricing" → Command Center → R1 quadrant → R2 negotiation tracker →
> R3 radar → R4 deciles → R5 competitor matrix → exported brief → architecture and governance), a
> 1-page case study (Situation, Approach, Architecture, Impact, What I'd do next), and 3 LinkedIn post drafts (natural tone,
> no employer specifics), each signed "By Varghese Pathisseril".

---
### Token-saving habits
- Run /clear between phases. Name only the files to read. Ask for diffs. Batch small fixes into one prompt.
- If Claude drifts, reply: "Re-read CLAUDE.md non-negotiables and correct."
