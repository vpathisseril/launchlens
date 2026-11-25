# Security Review (OWASP ASVS L1, abridged) + STRIDE

| Area | Control | Status |
|------|---------|--------|
| V2 Authentication | argon2id hashing; 12+ chars with 3 character classes; login 5/min/IP; non-enumerating signup/forgot; single-use 30-min reset tokens (hashed) | ✅ |
| V3 Sessions | Signed httpOnly cookie, SameSite=Lax, Secure in prod, 12 h max age, rotated on login | ✅ |
| V4 Access control | Deny by default: `require_user` / `require_module` / `require_admin`; admin surface returns 404 to non-admins; project and brief ownership checks | ✅ |
| V5 Validation & output | Pydantic/Form validation; Jinja autoescape; Markdown sanitized with nh3; CSV export escaping via `csv` | ✅ |
| V6 Cryptography | Fernet for user API keys; prod refuses to start without `SECRET_KEY` / `FERNET_KEY` | ✅ |
| V7 Errors & logging | Central handlers, no stack traces to users; audit log of approvals, grants, policy changes, exports; keys never logged | ✅ |
| V8 Data protection | Only last 4 of a key are displayed; no PHI; public data only | ✅ |
| V12 Files | Formulary ingest only from official cms.gov URLs; temp files deleted | ✅ |
| V13 API | CSRF on every POST (form token or `X-CSRF-Token` header for HTMX) | ✅ |
| V14 Config | CSP `default-src 'self'` + script nonces (no `unsafe-eval`), X-Frame-Options DENY, nosniff, Referrer-Policy, Permissions-Policy, HSTS in prod | ✅ |
| Supply chain | pip-audit: 0 known vulnerabilities; bandit clean; gitleaks in CI | ✅ |

## STRIDE highlights
- **Spoofing:** session rotation; forced admin password change on first login.
- **Tampering:** CSRF; server-side allowlists for tools and models (a user can't add tools or models beyond policy).
- **Repudiation:** append-only audit log with before/after values and IP.
- **Information disclosure:** keys encrypted, masked; module gating also enforced on agent tool use.
- **Denial of service:** rate limits on auth, ask (20/min/user), policy refresh; shared-key daily cap and monthly budget.
- **Elevation of privilege:** role checks on every admin route; agents edit only the owner's rows.
- **AI-specific:** the guardrail prompt is prepended and can't be edited; tools are read-only; the citation verifier
  flags unsupported numbers; prompt-injection exposure is limited because tools return aggregates from official sources.
