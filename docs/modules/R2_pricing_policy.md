# R2 — Drug Pricing Policy Hub: IRA Negotiation + MFN  (rank #2)
Load with: CLAUDE.md + R0 + this file + docs/DATA_SOURCES.md (Pricing section).

## Research questions
1. Which drugs in my class (or competitors) are selected for Medicare negotiation, for which payment year, and at what negotiated price (MFP) and discount?
2. Which drugs are likely candidates for future cycles (high Medicare spend, years since approval, no generic or biosimilar)?
3. How do Part D and Part B dynamics differ (e.g., MFP-based reimbursement for Part B drugs)?
4. Which manufacturers have publicly announced MFN pricing agreements, and what has been disclosed?

## Data (public, verify each in P0)
CMS Medicare Drug Price Negotiation Program pages and files (selected drug lists by IPAY, published MFPs, explanations) ·
Part D & Part B Spending by Drug · Part B ASP pricing files · Medicaid NADAC · Drugs@FDA / Purple Book (approval dates and
exclusivity, used for candidate eligibility) · Federal Register (CMS guidance/rules) · official announcements (whitehouse.gov, hhs.gov) for MFN
items, stored as policy documents with date and status.

## Views
| View | Visual | Notes |
|---|---|---|
| Negotiation Tracker | Timeline by IPAY (2026 → 2027 → 2028+) + table | Drug, sponsor, Part B/D, Medicare spend, MFP, % discount vs. list (when published) |
| Candidate Radar | Ranked table + scatter (spend × years since approval) | **Candidate Likelihood Score**: transparent rules (spend rank, approval age vs. statutory thresholds, generic/biosimilar status, orphan exclusion) labeled "estimate" |
| Price Exposure | Waterfall | List → est. net (when public) → MFP; Part B ASP view |
| MFN Tracker | Card list with status chips (Announced · Details pending · In effect) | Company, products named, date, source link; neutral summaries |
| Class Impact | Stacked bar | Share of class Medicare spend under negotiated prices by year |

## Guided analyses
- "Negotiation exposure for <class>": % of class spend already selected, plus next-cycle candidates.
- "Launch price corridor signal": peers' negotiated or announced price levels as context (descriptive, not a recommendation).

## Agent tools
`get_selected_drugs(ipay?, class?)`, `get_mfp(drug)`, `get_candidates(scope)`, `get_mfn_items(company?)`, `get_spend_trend(rxcui[])`

## AC
```gherkin
Scenario: Official lists only
  Then every selected drug and MFP links to the CMS source with its publication date
Scenario: Candidate score transparency
  When I open a candidate
  Then I see each rule's input value, its threshold, and an "estimate, not a CMS determination" label
Scenario: MFN neutrality
  Then MFN items show only disclosed facts, a status, and a date, with no projected impact unless a source states one
```
**Headline KPI:** % of class Medicare spend under negotiated prices (current IPAY), plus the count of next-cycle candidates.
