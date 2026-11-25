# R1 — Market Access & Payer Barriers Hub  (rank #1)
Load with: CLAUDE.md + R0 + this file + docs/DATA_SOURCES.md (Access section).

## Research questions the hub answers
1. How restrictive is coverage for my class and its competitors (tier, PA, step therapy, quantity limits)?
2. Which plans, states, and channels (Part D vs. Medicaid) are easiest or hardest to access?
3. Where does restrictive access line up with high demand? Those areas are the biggest launch risk.
4. How has access changed across formulary vintages (tightening or loosening)?

## Data (public)
CMS Part D formulary/plan files (tier, PA, ST, QL by plan × RxCUI) · CMS plan/contract enrollment · Part D Prescribers by Provider & Drug ·
Medicaid State Drug Utilization Data · RxNorm (drug/class mapping) · Part B Spending (for physician-administered drugs).

## Views (tabs)
| View | Visual | Metric definitions |
|---|---|---|
| Access Scorecard | KPI tiles + bullet charts | % plans covering · % preferred tier · PA rate · ST rate · QL rate (enrollment-weighted) |
| Restriction Heatmap | Heatmap: competitor × restriction × plan type | Share of enrolled lives facing each restriction |
| Geographic Access | Choropleth by state | Enrollment-weighted Access Ease Index (0–100) |
| Access vs. Demand | Quadrant scatter (state) | x = Access Ease, y = class claims per 1k beneficiaries; quadrants labeled "Win / Fix access / Build demand / Deprioritize" |
| Trend | Slope chart across vintages | Δ restriction rate per competitor |

**Access Ease Index** = 100 × enrollment-weighted mean of (covered) × (1 − 0.4·PA − 0.3·ST − 0.1·QL − 0.2·non-preferred tier).
Show the formula and allow weights to be edited in the Model Card.

## Guided analyses
- "Access risk for a new entrant in <class>": benchmarks the restrictions incumbents face.
- "Where access is the bottleneck": lists the top 10 states by high demand and low access ease.

## Research Agent tools
`get_access_summary(scope)`, `get_restrictions(rxcui[], plan_type)`, `get_state_access(scope)`, `get_access_trend(rxcui[])`

## AC
```gherkin
Scenario: Enrollment-weighted metrics
  Then each access KPI is weighted by plan enrollment and its tooltip shows the weighting and vintage
Scenario: Quadrant drill-down
  When I click a state in "Fix access"
  Then I see its plans and restrictions table with citations
Scenario: Vintage comparison
  When two formulary vintages exist
  Then the trend view shows Δ per competitor and flags changes > 10 pts
```
**Headline KPI (Command Center):** Enrollment-weighted Access Ease Index for the scope, with Δ vs. the prior vintage.
