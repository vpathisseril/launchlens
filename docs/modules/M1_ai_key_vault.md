# M1 — AI Key Vault, Forced Onboarding & Usage Governance
Load with: CLAUDE.md + this file only.

## Purpose
Make every user configure their AI access at first login. Personal keys (BYOK) get unlimited use. The shared default key is restricted.

## Data model
- `user_api_keys(id, user_id, provider ENUM(anthropic,openai,google), key_ciphertext, key_last4, label, is_valid, validated_at, created_at)`
- `default_key_policy(id, provider, model_allowlist[], max_tokens_per_call, calls_per_day, monthly_budget_usd, is_enabled)` (admin-managed)
- `usage_events(id, user_id, agent_id NULL, provider, model, key_type ENUM(byok,default), tokens_in, tokens_out, est_cost_usd, latency_ms, status, at)`
- `model_prices(provider, model, usd_per_mtok_in, usd_per_mtok_out, updated_at)`: an admin-editable table; never hard-code prices.

## Flow (forced)
`/onboarding/ai-keys` is a 3-step wizard. Middleware blocks every `/app/*` and `/m/*` route until it is complete.
1. Choose: **Use my own key (recommended, unlimited)** or **Use the shared default (limited)**.
2. BYOK: select the provider, paste the key, then validate with a minimal test call. Store the key encrypted and show `••••last4`.
   Default: show the exact limits (model, tokens per call, calls per day) and require the "I understand" checkbox.
3. Pick a preferred provider/model per task type (chat, analysis, brief writing).

## Stories & AC
```gherkin
Scenario: Forced onboarding
  Given I am an approved user with ai_key_status = none
  When I open any app page
  Then I am redirected to /onboarding/ai-keys
Scenario: BYOK accepted
  When I submit a valid key
  Then it is stored encrypted, ai_key_status = byok_valid, and no quota applies to my calls
Scenario: Invalid key
  Then I see "Key could not be validated" and nothing is stored
Scenario: Default key limits
  Given I accepted the default key and calls_per_day = N
  When I make call N+1 today
  Then I get a friendly limit message with an "Add your own key" CTA, and no provider call is made
Scenario: Disallowed model on the default key
  Then the request is downgraded to the cheapest allowlisted model, and a notice is shown
Scenario: Key hygiene
  Then no log, response, template, or error ever contains the full key
```

## Cost and token optimization (built in)
- Route by task: cheap/fast models for classification and extraction, premium models only for the executive brief.
- Enable provider prompt caching for long, static system prompts and dataset summaries.
- Send LLMs aggregates and summaries, never raw tables; cap context at about 8k tokens per call through a summarization step.
- Cache identical requests (hash of prompt + model) for 24h in `llm_cache`.

## UI
Settings → "AI Access": provider cards (logo-free; Lucide icons), status pills (Valid · Default · Invalid), a usage sparkline (30 days),
and today's remaining default-quota meter.
