# R3 — Policy Convergence Radar: MFN · Section 232 Tariffs · PBM Reform  (rank #3)
Load with: CLAUDE.md + R0 + this file + docs/DATA_SOURCES.md (Policy section).

## Research questions
1. What federal actions on drug pricing, tariffs, and PBMs are proposed, final, or in effect, and when?
2. Which actions affect my class, my channel (Part B/D, Medicaid, commercial), or my supply chain?
3. How do these actions combine? For example, a product exposed to both tariffs and an MFN agreement.
4. What changed this week?

## Data (public)
Federal Register API (rules, proposed rules, notices, presidential documents) · Congress.gov API (bills, status, actions) ·
Regulations.gov API (dockets, comment periods) · FTC (PBM 6(b) study reports, as documents) · CMS fact sheets and guidance ·
Census International Trade API / USITC DataWeb (pharma imports by HTS chapter 30 and country, as tariff exposure context) ·
SEC EDGAR (company filings mentioning tariffs, MFN, or PBM, as context).

## Data model additions
`policy_items(id, title, type ENUM(rule,proposed_rule,bill,executive_action,agreement,report), topic ENUM(mfn,tariff,pbm,ira,other),
status, status_date, effective_date NULL, agency, source_url, summary_md, affected_channels[], affected_classes[], last_checked_at)`
Summaries are AI-generated from the official text with citations to paragraphs, then marked "AI summary — verify against source".

## Views
| View | Visual |
|---|---|
| Radar | Radial chart: 3 rings (Proposed / Final / In effect) × 4 sectors (MFN, Tariffs, PBM, IRA); dots = policy items |
| Timeline | Swimlane by topic with milestones (comment deadlines, effective dates) |
| Exposure Matrix | Matrix: my scope (class/channel/import origin) × policy items, with cells = relevance tag (Direct / Indirect / None) and rationale |
| Trade Context | Import value by source country for HTS ch. 30 (trend) |
| This Week | Change feed with diffs of status changes |

## Guided analyses
- "Policy exposure profile for <class or company>": the matrix plus a neutral narrative.
- "Upcoming deadlines (90 days)": comment periods and effective dates.

## Agent tools
`search_policy(topic?, status?, q?)`, `get_policy_item(id)`, `get_exposure(scope)`, `get_trade_trend(hts, years)`

## AC
```gherkin
Scenario: Status integrity
  Then every policy item shows status and status_date from its official source, and proposals are never labeled as law
Scenario: Daily refresh
  When the scheduled refresh runs
  Then new or changed items appear in "This Week" with the change described
Scenario: Neutral summaries
  Then summaries describe provisions and affected parties without evaluative language (checked by a lint list of banned terms)
```
**Headline KPI:** active items by status (Proposed / Final / In effect) touching the scope, plus deadlines in the next 30 days.
