# R0 — Research Workspace, Command Center & Hub Contract (build first)
Load with: CLAUDE.md + this file only.

## Purpose
One consistent research experience across all 5 hubs: projects, saved findings with citations, the research agent runtime,
watchlists, and executive brief export.

## Hub contract (every R1–R5 hub implements this interface)
```python
class ResearchHub(Protocol):
    key: str                      # "access" | "pricing" | "policy" | "targeting" | "differentiation"
    title: str; icon: str         # Lucide icon name
    def headline_kpis(scope) -> list[Kpi]          # for Command Center tiles (each Kpi has lineage)
    def views() -> list[ViewSpec]                  # tabs: id, title, chart type, query fn
    def guided_analyses() -> list[AnalysisSpec]    # one-click analyses with parameters
    def agent_tools() -> list[ToolSpec]            # read-only data tools for the hub's Research Agent
    def changes_since(dt) -> list[ChangeEvent]     # "what changed" feed (new data vintage, policy event, approval)
    def brief_sections() -> list[SectionSpec]      # contribution to the Executive Brief
```
`scope` = {therapeutic_area, indication_text, rxcui[], drug_class, states[], specialties[], period}.

## Data model
- `research_projects(id, owner_id, title, scope_json, status ENUM(active,archived), created_at, updated_at)`
- `findings(id, project_id, hub_key, title, body_md, chart_spec_json NULL, citations_json, pinned_by, created_at)`
  where `citations_json` = [{source_key, dataset_or_doc, vintage_or_date, url, retrieved_at, locator}]
- `watchlists(id, user_id, kind ENUM(drug,class,policy_item,competitor), ref, created_at)`, plus `notifications(id, user_id, event_json, read_at)`
- `briefs(id, project_id, version, content_md, generated_by_model, usage_event_id, created_at)`

## Screens
1. **Command Center** (`/app`): 5 hub tiles (icon, headline KPI, sparkline, freshness dot, "N changes this week"), a scope bar
   (therapeutic area · drug/class · states), a "Recent findings" list, and a watchlist alerts panel.
2. **Hub page layout** (shared): header (title, scope chips, freshness), left tab rail (views), main canvas (chart + table),
   right drawer (Research Agent chat with citations, collapsible), and a "Pin to project" action on every chart and answer.
3. **Research Project** (`/projects/{id}`): Kanban-style columns by hub, finding cards, drag to reorder, notes.
4. **Brief editor**: AI draft → user edits (Markdown) → export PDF/MD with disclaimer, sources appendix, and signature.

## Research Agent runtime (shared)
- Tool-calling loop (max 6 tool calls per turn) over **read-only** hub tools that return aggregates plus citation objects.
- The system prompt enforces: answer only from tool results; attach a citation `[n]` to every number or claim; say "not in available
  data" when unsupported; keep policy descriptions neutral with status dates; no medical, legal, or financial advice.
- **Citation verifier:** after generation, check that every `[n]` maps to a returned citation and that every number appears in tool output.
  If verification fails, regenerate once, then show a warning badge.
- Cost: summarize tool outputs before the final call; cache by (tools_hash, question, scope); use a cheap model for routing and a premium model for briefs.

## AC
```gherkin
Scenario: Pin a finding with citations
  When I pin a chart from any hub
  Then a finding is saved with chart spec and ≥1 citation, visible in the project
Scenario: Agent answers with verified citations
  When I ask "Which competitors face step therapy in most Part D plans?"
  Then every number in the answer has a citation that resolves to a cached source record
Scenario: Unsupported question
  When I ask something the data cannot answer
  Then the agent says so and suggests which source would be needed
Scenario: Brief export
  When I generate a brief for a project
  Then it contains sections from each hub with findings, a sources appendix, the disclaimer, and "By Varghese Pathisseril"
Scenario: Watchlist alert
  Given I watch a drug
  When a new negotiation list, label change, or formulary vintage mentions it
  Then I get an in-app notification within one refresh cycle
```
