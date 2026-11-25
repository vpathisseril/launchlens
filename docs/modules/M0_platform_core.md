# M0 — Platform Core (Landing, Signup, Approval, Auth, Module Access)
Load with: CLAUDE.md + this file only.

## Purpose
Give the app an executive public face, a controlled signup flow, secure login, and access gated by module.

## Data model
- `users(id, email UNIQUE, name, org, title, password_hash, role ENUM(user,admin), status ENUM(pending,approved,rejected,suspended),
  requested_at, approved_at, approved_by, approval_source ENUM(admin,auto), ai_key_status ENUM(none,byok_valid,default_accepted), last_login_at)`
- `modules(id, key UNIQUE, name, description, icon, is_active, is_default_on_approval, sort_order)`
- `user_modules(user_id, module_id, granted_by, granted_at)`  PK(user_id, module_id)
- `settings(key, value_json)`, which holds `approval_mode` ∈ {MANUAL, AUTO_IMMEDIATE, AUTO_AFTER_24H} and `approval_window_hours` (default 24)
- `audit_log(id, actor_id, action, entity, entity_id, before_json, after_json, ip, at)`

## Screens
1. **Landing** (`/`): hero ("Research the five forces that decide a drug launch" + "Request access"), 5 hub cards (Access · Pricing · Policy · Targeting ·
   Differentiation, each with a Lucide icon and one-line question), a research-loop strip (Ask → Explore → Analyze → Synthesize → Brief),
   an "How it works" timeline, a data-source logo strip (text badges only, no third-party logos), a trust/security section, and a footer signature.
2. **Signup** (`/signup`): name, work email, org, title, use-case (select), password (zxcvbn ≥ 3), terms checkbox.
3. **Pending** (`/pending`): status card with a countdown to the auto-approval time (if that mode is on) and an email-confirmation note.
4. **Login** (`/login`), **Logout**, **Forgot/Reset password** (signed, single-use token, expires after 30 minutes).
5. **Home** (`/app`): the Command Center (see R0), showing only granted hubs.

## User stories & acceptance criteria
**M0-S1 Signup**
```gherkin
Scenario: New visitor requests access
  Given I am on /signup
  When I submit valid details
  Then a user is created with status "pending"
  And I am redirected to /pending showing the expected decision time
  And the admin sees the request in the Approvals queue
Scenario: Duplicate email
  When I sign up with an existing email
  Then I see a generic message that does not reveal whether the account exists
```
**M0-S2 Approval within 24 hours**
```gherkin
Scenario: Admin approves
  Given approval_mode is MANUAL or AUTO_AFTER_24H
  When the admin approves a pending user
  Then status = approved, approval_source = admin, default modules are granted, and an audit entry is written
Scenario: Auto-approval after the window
  Given approval_mode is AUTO_AFTER_24H and a user has been pending for more than 24h
  When the approval sweep runs (Scheduled Deployment, hourly) OR the user tries to log in
  Then status = approved, approval_source = auto
Scenario: Immediate auto-approval
  Given approval_mode is AUTO_IMMEDIATE
  When a user signs up
  Then they are approved immediately and can log in
```
**M0-S3 Login & gating**
```gherkin
Scenario: Pending user tries to log in
  Then they see /pending (no session is created)
Scenario: Approved user logs in for the first time
  Then they are redirected to /onboarding/ai-keys (M1) before any module
Scenario: Accessing an ungranted module
  When I open /hubs/pricing without access
  Then I get 403 with a "Request access" button that notifies the admin
```

## Implementation notes
- The approval sweep is idempotent (`scripts/approval_sweep.py`). The same logic also runs lazily on login, so Autoscale cold starts are safe.
- Email: an SMTP adapter that is no-op in dev (it logs to the console). Notify the admin on new signups.
- Rate limits: signup 3/hour/IP, login 5/min/IP (slowapi).
- Seed: admin from `ADMIN_EMAIL` / `ADMIN_PASSWORD` env vars on first run only; force a password change on first admin login.

## Done when
All scenarios are automated, the landing page scores Lighthouse ≥ 90 (performance and accessibility), and there are audit entries for every approval.
