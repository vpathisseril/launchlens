# R5 — Market Understanding & Differentiation Hub  (rank #5)
Load with: CLAUDE.md + R0 + this file + docs/DATA_SOURCES.md (Differentiation section).

## Research questions
1. Who are the current and upcoming competitors for this indication, and how is the market sized and growing?
2. How do labels differ (indications, line of therapy, dosing and route, boxed warnings, contraindications)?
3. What is in the pipeline (phase, endpoints, comparators, expected completion), and when do exclusivities expire?
4. Where are the unmet needs and white spaces in which a new product could stand out?

## Data (public)
openFDA (drug/label, drugs@fda, NDC, drug/event as safety-signal context only) · Orange Book & Purple Book (patents, exclusivity, biosimilars) ·
ClinicalTrials.gov API v2 · RxNorm · Part D/B Spending by Drug · PubMed E-utilities (evidence volume) · SEC EDGAR (sponsor context).

## Views
| View | Visual |
|---|---|
| Market Map | Treemap of class spend by product; growth chips |
| Competitor Matrix | Feature grid from labels: indication, line, route, dosing frequency, boxed warning, REMS (each cell cites the label section) |
| Pipeline Timeline | Gantt of trials by phase, with primary completion dates and comparators |
| Exclusivity Cliff | Timeline of patent/exclusivity expiries and biosimilar entries |
| Differentiation Canvas | 2×2 chosen by the user (e.g., dosing convenience × safety profile), with the user's product positioned via inputs and peers via label data |
| Evidence Pulse | Publications per year per product (PubMed counts) |

## Guided analyses
- "Competitive landscape for <indication>": auto-discovers competitors (labels + trials + RxNorm class); the user confirms the set.
- "White-space finder": label attributes no competitor offers, plus trial endpoints not yet tested (descriptive).

## Agent tools
`find_competitors(indication_text)`, `compare_labels(set_ids[])`, `get_pipeline(indication, phases[])`, `get_exclusivity(product)`, `get_market_size(class)`

## AC
```gherkin
Scenario: Label-grounded matrix
  Then every matrix cell links to the label section it came from (set_id + section)
Scenario: Competitor discovery is confirmable
  Then suggested competitors show their discovery source and the user can add or remove them before analysis
Scenario: Safety data caveat
  Then FAERS counts are shown with the standard caveat that reports do not establish causation
```
**Headline KPI:** number of active competitors + Phase 3 entrants within 24 months, plus class spend growth (YoY).
