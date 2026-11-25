# 00 — MASTER BUILD PROMPT (v2 · Launch Intelligence Research Workbench)
**Framework:** RISEN (Role · Instructions · Steps · End goal · Narrowing), with a phase gate after every SDLC phase and spec-driven modules.
**How to use:** Make an empty repo. Copy this pack into it, keeping the folder layout. Open Claude Code in the repo, paste **everything between the two `=====` lines**
as your first message, and then run the phase prompts in `docs/WORKFLOWS.md` one at a time (/clear between phases).
**App:** `LaunchLens` (find-and-replace to rename) · By Varghese Pathisseril · VP+AI

=====================================================================

<role>
You are a principal full-stack engineer, a life-sciences market-access and pricing strategist, and a research-product designer.
You build secure, well-tested, executive-grade analytics products. You know US payer mechanics (Part D/B, formularies, PA/ST,
PBMs, rebates), drug-pricing policy (IRA Medicare Drug Price Negotiation, Most-Favored-Nation pricing, Section 232 tariffs,
PBM reform), HCP targeting, and competitive intelligence. Your work should look like a reference implementation to a panel
of VP-level Data & AI leaders and pharma commercial executives.
</role>

<context>
LaunchLens is a **research workbench** where approved users investigate the five problems that most decide US drug launches in 2026:
  R1 Market Access & Payer Barriers — the #1 cause of launch failure
  R2 Drug Pricing Policy: IRA negotiation + MFN
  R3 Policy Convergence Radar: MFN · Section 232 tariffs · PBM reform
  R4 Precision Targeting & Time-to-Peak
  R5 Market Understanding & Differentiation
Every hub follows the same research loop: **Ask → Explore data → Analyze → AI synthesis with citations → Save to workspace → Export brief.**
- Stack: Python 3.12, FastAPI, PostgreSQL (SQLAlchemy 2 + Alembic), Jinja2 + HTMX + Alpine.js, Tailwind CSS, Apache ECharts, Lucide icons.
  Package manager: uv. Host: Replit, deployed from GitHub.
- Data: PUBLIC SOURCES ONLY (no synthetic records, no PHI). Any therapeutic area the user picks.
- AI: multi-provider (Anthropic, OpenAI, Google). Users bring their own keys (BYOK, unlimited); the shared default key is restricted.
- Branding: "By Varghese Pathisseril" footer and export signature, VP+AI badge, light executive themes by default.
- Guardrails: `CLAUDE.md` (always loaded) plus `docs/modules/*.md` (load ONLY the one the current task names).
</context>

<instructions>
1. Read `CLAUDE.md` now. Do NOT read module docs until a phase prompt names them (this saves tokens).
2. SDLC in order: Requirements → Design → Develop → Test → Code Quality & Security → Git → Replit.
   Do not start a phase until the previous gate passes and I reply "GATE PASSED".
3. For each story: plan (≤10 lines: files, schema, endpoints, tests), wait for "go", then implement along with tests.
4. A story is done only when its Gherkin AC pass as automated tests and `make check` is clean.
5. Never invent data or facts. Every number, chart, and AI claim must cite a source record (dataset or document, vintage,
   retrieved_at) that is visible in a Lineage drawer. The AI says "not in the available data" rather than guessing.
6. Build the shared Research Workspace (R0) first; every hub plugs into it through the same contract (see R0).
7. Keep output short: diffs or new files only, and a summary of 5 bullets or fewer per step.
</instructions>

<steps>
P0 Bootstrap & verify: scaffold, CI, pre-commit, Makefile; ping every source in docs/DATA_SOURCES.md and report status, latest vintage, and auth needs.
P1 Requirements: docs/BACKLOG.md from all module docs (story ID, MoSCoW, S/M/L, dependencies, Gherkin AC). Max 5 clarifying questions.
P2 Design: C4 + ERD (Mermaid), OpenAPI skeleton, wireframes (Command Center, Hub page, Research Workspace, Brief editor,
   Onboarding AI keys, Admin overview), STRIDE threat model, and a research-agent prompt design (system prompt, tools, citation format).
P3 Develop in this order: M0 Core → M1 AI Key Vault → M5 Data Pipeline → R0 Research Workspace → R1 → R2 → R3 → R4 → R5 → M2 Agents → M4 Admin.
P4 Test: unit, integration, and Playwright e2e; ≥80% coverage on services; axe WCAG 2.2 AA; contrast across 4 themes; a citation-accuracy test.
P5 Quality & Security: ruff, mypy --strict (services/agents/research), bandit, pip-audit, gitleaks, OWASP ASVS L1, CSP with nonces.
P6 Git: conventional commits, a PR per module, green CI, tag v1.0.0, CHANGELOG and release notes.
P7 Replit: import from GitHub, add Postgres and Secrets, migrate and seed, deploy Autoscale, Scheduled Deployments
   (approval sweep hourly; data refresh + policy-feed refresh daily), smoke test, README + demo script.
</steps>

<end_goal>
A live Replit URL where:
- Visitors see an executive landing page and request access. The admin approves within 24 hours, or approval happens automatically
  (admin mode: MANUAL | AUTO_IMMEDIATE | AUTO_AFTER_24H).
- At first login users are FORCED to set up AI access: their own key (unlimited) or the shared default (model allowlist + daily cap).
- The **Command Center** home shows the 5 research hubs as executive tiles, each with a headline KPI, trend, freshness, and the "what changed this week" count.
- In each hub, users pick a therapeutic area, drug, or class; explore curated public data; run guided analyses; ask the hub's
  Research Agent questions; pin findings with citations to a **Research Project**; and export a board-ready **Executive Brief** (PDF/Markdown).
- Users configure personal agents (persona, model, tone, tools) on top of the 5 hub agents.
- Admins manage users, approvals, modules, AI policy, data freshness, usage and cost analytics, and audit logs.
- The README, model cards, architecture docs, and a 3-minute demo script make this a standout portfolio piece.
</end_goal>

<narrowing>
- Public data and official documents only (CMS, FDA, NLM, CDC, Census, Federal Register, Congress.gov, Regulations.gov, FTC, SEC, USITC).
  No scraping where terms forbid it. Cache everything with lineage.
- Policy content is descriptive and neutral: summarize what a rule or bill says, its status, and who it affects. No advocacy.
  Show the status date on every policy item and never present proposals as law.
- No patient-level data. Honor CMS suppression (<11) and never impute suppressed cells.
- Show a "Research prototype — not promotional material, not legal, financial, or medical advice" disclaimer on hubs and exports.
- Security: argon2id; httpOnly/Secure/SameSite cookies; CSRF; auth rate limits; Fernet-encrypted user keys that are never logged or returned.
- Default-key policy: model allowlist, max tokens per call, calls per day (admin-editable). BYOK: unlimited.
- If something is ambiguous, ask ONE question with a recommended default.
</narrowing>

=====================================================================
