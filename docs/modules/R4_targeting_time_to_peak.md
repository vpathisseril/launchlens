# R4 — Precision Targeting & Time-to-Peak Hub  (rank #4)
Load with: CLAUDE.md + R0 + this file + docs/DATA_SOURCES.md (Targeting section).

## Research questions
1. Which prescribers and geographies drive class volume, and which are growing?
2. Where is need high but competitor penetration low (white space)?
3. How fast did analog launches in this class ramp, measured in years to reach X% of peak Medicare volume?
4. Which segments should be targeted first to shorten time-to-peak?

## Data (public)
Part D Prescribers by Provider & Drug (multi-year) · Part D Spending by Drug (multi-year, for analog ramps) · NPPES (specialty, location) ·
Open Payments (engagement context) · CDC PLACES (prevalence) · ACS (age, insurance, population) · HRSA shortage areas · R1 Access Ease Index (joined by state).

## Model: transparent Opportunity Score (HCP → county → state)
`score = w1·class_volume_pctile + w2·growth_pctile + w3·need_index + w4·access_ease(R1) + w5·competitor_vulnerability + w6·engagement_signal`
Default weights 25/20/15/20/10/10; sliders must sum to 100. Deciles are assigned after scoring.
**Backtest:** score on vintage t−1 and measure top-decile capture of vintage t class growth (lift chart). Results go on the Model Card.

## Analog ramp analysis
The user picks 2–5 analog drugs in the class (from Part D spending history). The hub plots Medicare spend or claims by year since first appearance,
indexed to peak, and reports median years to 50% and 80% of peak. These are labeled "Medicare-only proxy".

## Views
HCP Decile table (sortable, NPI, specialty, location, component waterfall) · White-Space map (county choropleth) · Segment mix (specialty × decile) ·
Analog Ramp curves · Scenario compare (two weight sets; top-100 overlap Venn).

## Agent tools
`get_top_hcps(scope, n, weights)`, `get_white_space(scope)`, `get_analog_ramp(rxcui[])`, `explain_score(npi|fips)`

## AC
```gherkin
Scenario: Live re-rank
  When I change a weight
  Then deciles recompute in under 2s for ≤50k HCPs and the brief inputs update
Scenario: Explainability
  When I open any HCP or county
  Then a waterfall shows each component's contribution with sources
Scenario: Suppression
  Then suppressed cells display "<11" and are excluded from totals with a footnote
```
**Headline KPI:** top-decile share of class volume, plus median analog years-to-80%-peak.
