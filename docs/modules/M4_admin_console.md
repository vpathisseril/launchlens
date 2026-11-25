# M4 — Admin Console, Access Management, Reporting & Analytics
Load with: CLAUDE.md + this file only.

## Sections (left nav, Lucide icons)
1. **Overview** (`gauge`): KPI tiles: total users, pending approvals (with oldest age), active users over 7 and 30 days, agent runs today,
   LLM cost MTD (BYOK vs. default), default-key quota hits, data freshness (stale sources in amber).
2. **Approvals** (`user-check`): queue with SLA timers (time left to the 24h window), bulk approve/reject with a reason, and the approval-mode toggle.
3. **Users & Access** (`users`): search and filter; per-user module grants (checkbox matrix); suspend/reactivate; reset key status.
4. **Modules** (`layout-grid`): create/edit modules (key, name, icon, description, active, default-on-approval, sort order).
5. **AI Governance** (`shield-check`): default-key policy editor, model price table, provider health, top spenders.
6. **Usage & Analytics** (`bar-chart-3`): DAU/WAU, funnel (signup → approved → onboarded → first project → first pinned finding → first brief),
   module adoption, cost by model, p50/p95 latency, error rate.
7. **Data Sources** (`database`): each source's status, last refresh, vintage, row counts, and a "Refresh now" button (queued).
   **Hub Health**: per hub, data freshness, agent answer volume, citation-verifier pass rate, and average user rating.
8. **Audit Log** (`scroll-text`): filterable and exportable (CSV).

## Stories & AC
```gherkin
Scenario: Only admins
  Given I am a normal user
  When I open /admin
  Then I get 404 (do not reveal that the route exists)
Scenario: Grant module
  When the admin grants the "pricing" hub to a user
  Then the user sees it on their next page load and an audit row is written with before/after
Scenario: Change default policy
  When the admin sets calls_per_day = 10
  Then the new limit applies immediately to default-key users and is shown on their quota meter
Scenario: SLA breach alert
  Given a request has been pending for more than 20h in MANUAL mode
  Then it is highlighted red and the admin receives a reminder email
Scenario: Export
  Then any table can be exported as CSV, with an audit entry
```

## Dashboard standards
- Grid: 12 columns, 24px gutters, cards with 16px radius, subtle elevation; at most 6 KPI tiles per row.
- Every chart shows a title, a one-line insight ("Default-key users hit limits 3× more on Mondays"), a source, and a timestamp.
- Date range picker (7 days, 30 days, 90 days, custom) is global per page. Empty states include guidance.
