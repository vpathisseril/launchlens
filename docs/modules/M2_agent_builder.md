# M2 — Agent Builder (user-configured AI agents)
Load with: CLAUDE.md + this file only.

## Purpose
Let users create, test, and save personal agents based on their preferences.

## Data model
- `agents(id, owner_id, name, icon, persona, goal, system_prompt, provider, model, temperature, max_tokens, tone ENUM(executive,analytical,concise,coaching),
  output_format ENUM(markdown,bullets,table,json), tools[], memory_enabled, is_template, created_at, updated_at)`
- `agent_runs(id, agent_id, user_id, input, output, usage_event_id, rating, at)`
- `tool_registry` (code-defined): all R1–R5 hub tools (see each R doc), `calculator`, `doc_qa` (user upload ≤ 5 MB, text only)

## Templates (seeded; is_template = true). One per research hub, plus a synthesizer
1. **Access Analyst** (R1): formulary restrictions, access vs. demand, access risk.
2. **Pricing Policy Analyst** (R2): IRA negotiation tracker, candidates, MFN items (neutral).
3. **Policy Radar Scout** (R3): what changed, deadlines, exposure matrix.
4. **Targeting Strategist** (R4): deciles, white space, analog ramps.
5. **Competitive Intel Analyst** (R5): labels, pipeline, exclusivity, white space.
6. **Executive Brief Writer**: synthesizes pinned findings across hubs into a board-ready brief.
Users clone a template and change persona, tone, model, and tool subset (limited to the hubs they can access).

## Builder UI (3-pane)
Left: configuration form, with guided fields, tooltips, and a live token estimate. Center: test chat. Right: a run inspector
(model, tokens, cost, latency, tool calls). The form has a **Guardrail preview** that shows the effective system prompt:
platform guardrails + user persona. Platform guardrails cannot be edited.

## Stories & AC
```gherkin
Scenario: Create from template
  When I clone "Launch Strategist" and change tone to concise
  Then a private agent is saved under my account
Scenario: Respect key policy
  Given I am on the default key
  Then the model dropdown only shows allowlisted models
Scenario: Tool permissions
  Given I lack access to the R2 hub
  Then R2 tools are hidden and rejected server-side
Scenario: Run logging
  When I run an agent
  Then a usage_event is written and the run appears in my history with cost
```

## Platform guardrail (prepended to every agent)
"You are an analytical assistant inside a research prototype. Use only the provided data and tools. Cite the data source for
every number. If data is missing or suppressed, say so. Do not give medical advice or make promotional claims about any product."
