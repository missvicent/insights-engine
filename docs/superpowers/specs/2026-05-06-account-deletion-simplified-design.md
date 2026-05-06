# Account Deletion (Instant, Inline-Wipe) — Design

**Date:** 2026-05-06
**Status:** Approved (brainstorming) — pending implementation
**Owner:** vicentbnf@gmail.com
**Supersedes:** [2026-04-29-account-deletion-design.md](./2026-04-29-account-deletion-design.md)

## Goal

Let a user delete their account from settings. The wipe happens inline:
when the user clicks Delete (after a frontend "are you sure?" modal), the
backend immediately erases all of the user's data across the 12 user-owned
tables, deletes the Clerk user, and returns a success response. No grace
period, no email confirmation, no cron.

This is a deliberate simplification of the previously approved 30-day
grace-period design. The old design's complexity (token flow, pg_cron,
reconciliation, account-locked state machine) was sized for an enterprise
recovery story we don't need at this stage. The objective is to ship
account deletion now and revisit recovery semantics if real users ever
ask for them.

## Non-goals

- 30-day grace period or any user-cancellable recovery window.
- Email confirmation step before the wipe.
- Anonymisation, retention, or audit-only copies of deleted data.
- Wiping point-in-time backups (out of our control; ages out per Supabase
  Pro retention).
- Frontend implementation of the "are you sure?" modal (separate concern).
- Cron infrastructure of any kind — that lives independently with budget
  archival ([2026-04-24-budget-archival-cron-design.md](./2026-04-24-budget-archival-cron-design.md)).
- Stripe / payment-provider deletion (no payments in scope).

## Deltas vs the original 30-day-grace spec

| Dropped from original | Reason |
|---|---|
| `account_deletion_requests` table and its 7-state lifecycle | One-shot inline operation has no states to persist. |
| `deletion_tokens.py`, `/confirm`, `/cancel`, `/status` endpoints | No token flow when the wipe is inline. |
| Confirmation email + scheduled email (Emails #1, #2) | No grace period to confirm or schedule against. |
| pg_cron + pg_net + `app.cron_secret` + `internal_cron.py` + `CRON_SHARED_SECRET` | No deferred work. |
| Reconciliation cron | The webhook backstop covers the only orphan case left. |
| Account-locked guard against `account_deletion_requests.status='failed'` | Replaced by a profiles-existence guard that handles deletion AND token revocation in one query (see Auth section). |
| `_DEV_OVERRIDE_DELETION_DATE`, `APP_ENV` override, `APP_BASE_URL`, `FRONTEND_BASE_URL` | No email links to build, no time window to fast-forward. |

| Kept from original | Reason |
|---|---|
| `delete_user_data(p_clerk_user_id)` SECURITY DEFINER RPC, FK-correct order, idempotent | Same destructive SQL is needed; idempotency lets the webhook be a backstop. |
| `account_deletion_audit` table | Compliance + support trail; cheap to keep, expensive to add later. |
| `webhook_events` table for Svix idempotency | Already needed by the existing `user.created` welcome flow. |
| Clerk webhook for `user.deleted` | Becomes a backstop, not source of truth. |
| `webhooks_clerk.py` folding the existing `emails.py` | Single Clerk webhook URL, both event types. |
| `ACCOUNT_DELETION_ENABLED` feature gate | Ship dark, flip on after staging validation. |

## Architectural decisions

| Decision | Choice | Why |
|---|---|---|
| When data is wiped | Inline, in the request handler | One-shot operation; no async needed. |
| Order of inline steps | Wipe DB first, then Clerk DELETE | Wipe-first means a Clerk failure can never leave orphan data. |
| Webhook role | Idempotent backstop only | The wipe already happened inline; the webhook protects against the (rare) case where the inline Clerk DELETE fails and the user is later deleted via the Clerk dashboard. |
| Token / session revocation | `profile_exists` guard in `get_user_ctx` | The wipe deletes the `profiles` row; missing row → 401. True instant revocation, no separate state table. |
| Source email for confirmation message | `profiles.email`, captured BEFORE the wipe | JWT email claim is unreliable on Clerk; profiles row is the source of truth and is about to be deleted. |
| Email send failure handling | Log + continue (best-effort) | Don't roll back a successful deletion because of a Resend outage. |
| Clerk DELETE failure handling | 3x exponential backoff in `clerk_admin.py`; final failure → audit "clerk_delete_failed" + 502 | Data is already gone; user can retry; webhook reconciles if Clerk dashboard is used later. |
| Clerk DELETE returning 404 | Treat as success | Idempotent against partial prior runs. |
| Audit privacy | sha256 of `user_id`, no email/IP/name | Audit must not itself violate the deletion. |

## Data model

### `account_deletion_audit`

| Column | Type | Notes |
|---|---|---|
| `id` | `bigserial pk` | |
| `user_id_hash` | `bytea not null` | `digest(user_id, 'sha256')` |
| `event` | `text not null` | `request_initiated`, `user_data_deleted`, `clerk_delete_failed` |
| `occurred_at` | `timestamptz default now()` | |
| `metadata` | `jsonb` | Stage-specific. Must NOT contain email, IP, name, or raw user_id. |

**RLS:** enabled, **no policies for `authenticated`** → service role only.

### `webhook_events`

| Column | Type |
|---|---|
| `svix_id` | `text primary key` |
| `received_at` | `timestamptz default now()` |

`INSERT ... ON CONFLICT DO NOTHING RETURNING svix_id` for idempotency.
Used by both `user.created` and `user.deleted` event handlers.

## `delete_user_data(p_clerk_user_id text)`

`SECURITY DEFINER`, owned by `postgres`, `revoke execute from public`,
`grant execute to service_role`. Single transaction (implicit in PL/pgSQL).
FK-correct order (unchanged from original spec):

1. `transactions`
2. `debt_payments`
3. `allocations`
4. `budget_archive_reports`
5. `recurring_transactions`
6. `debts`
7. `goals`
8. `budgets`
9. `accounts`
10. `categories WHERE is_system = false`
11. `user_settings`
12. `profiles`

Then insert audit row `('user_data_deleted', sha256(p_clerk_user_id))`.

Idempotent: re-running on an already-deleted user is a no-op (every
DELETE affects 0 rows; the audit insert still fires, which is fine —
multiple `user_data_deleted` rows for the same hash are harmless).

## End-to-end flow

```
User clicks Delete in app
  └─ Frontend "are you sure?" modal
        └─ POST /account/delete  (Clerk JWT)
              ├─ Step 1: feature flag check (else 503)
              ├─ Step 2: capture email — fetch from profiles via service-role client
              ├─ Step 3: audit insert "request_initiated"
              ├─ Step 4: rpc('delete_user_data', user_id)  ← data is gone here
              │          (RPC writes its own audit row "user_data_deleted")
              ├─ Step 5: Clerk admin DELETE /v1/users/{user_id}  (3x exp backoff)
              │          ├─ on success → continue
              │          ├─ on 404 → treat as success (idempotent)
              │          └─ on final failure → audit "clerk_delete_failed", return 502
              ├─ Step 6: send "deleted" email — best-effort, log on failure
              └─ Step 7: 204 No Content

Webhook backstop:
  POST /webhooks/clerk  (Svix-signed)
        ├─ Svix verify → 401 on bad sig
        ├─ INSERT INTO webhook_events (svix_id) ON CONFLICT DO NOTHING RETURNING ...
        │  → if no row returned, duplicate, 200 immediately
        ├─ if event.type == 'user.created' → existing welcome-email path
        └─ if event.type == 'user.deleted' → rpc('delete_user_data', clerk_id)
                                              (no-op when data already wiped)
```

### Step-order rationale

- **Wipe before Clerk DELETE.** A Clerk-first approach would risk leaving
  orphan data if the inline RPC failed afterwards.
- **Capture email before the wipe.** Step 4 deletes `profiles.email`. The
  JWT email claim varies by Clerk template config; `profiles.email` is
  the source of truth.
- **Audit "request_initiated" before the wipe.** Survives RPC failure
  (separate transaction), so the attempt is always recorded.
- **Email last, best-effort.** A Resend outage shouldn't roll back a
  successful deletion. The user has the in-app success state.
- **Clerk DELETE failure returns 502, not 500.** Signals "downstream
  dependency"; frontend can show "data deleted, but auth provider sync
  failed — retry."
- **Synchronous Clerk call (no BackgroundTasks).** Tail latency on Clerk
  admin can spike, but if it does, the user wants to know — fire-and-
  forget would hide failures.

## Components

```
app/
├── auth/jwks.py                    (unchanged)
├── config.py                       +CLERK_SECRET_KEY, +SUPABASE_SERVICE_ROLE_KEY,
│                                    +ACCOUNT_DELETION_ENABLED, +DELETION_COMPLETED_TEMPLATE_ID
├── context.py                      (unchanged)
├── db/client.py                    +build_service_role_client()
│                                   +fetch_profile_email(user_id)
│                                   +profile_exists(user_id)         ← used by get_user_ctx guard
│                                   +insert_audit_event(...)
│                                   +record_webhook_event(svix_id)
│                                   +call_delete_user_data(user_id)
├── models/schemas.py               +AuditEvent enum (request_initiated,
│                                    user_data_deleted, clerk_delete_failed)
├── services/
│   ├── clerk_admin.py              NEW — DELETE /v1/users/{id} with 3x exp backoff
│   ├── deletion_service.py         NEW — orchestration; no DB calls inside
│   └── email_service.py            +send_account_deleted(email)
└── routes/
    ├── deps.py                     +profile-existence guard in get_user_ctx
    ├── account_deletion.py         NEW — POST /account/delete
    └── webhooks_clerk.py           NEW — folds emails.py:
                                          - svix verify
                                          - idempotency via webhook_events
                                          - dispatch on event.type:
                                              user.created → existing welcome flow
                                              user.deleted → call_delete_user_data (backstop)

app/routes/emails.py                DELETED after webhooks_clerk.py is live + tested

supabase/migrations/
└── 2026MMDDHHMMSS_account_deletion.sql   NEW — one migration, three things:
                                                 - account_deletion_audit + RLS
                                                 - webhook_events
                                                 - delete_user_data() function

tests/
├── test_clerk_admin.py             NEW — retry behavior, 5xx → exp backoff, 4xx → no retry
├── test_deletion_service.py        NEW — orchestration order, email-on-success-only,
│                                     clerk-failure-after-wipe path
├── test_account_deletion_route.py  NEW — happy path + feature-flag-off + missing-profile
├── test_webhooks_clerk.py          NEW — svix sig, idempotency, both event types
├── test_deps.py                    +missing-profile → 401 case
└── test_email_service.py           +send_account_deleted assertions
```

## Architectural rules (per CLAUDE.md)

- `routes/account_deletion.py` is thin: feature-flag check, build
  service-role client, call `deletion_service.delete_account(user_ctx,
  sr_client)`, return 204.
- `services/deletion_service.py` orchestrates but does not query Supabase
  directly. It receives the service-role client as an argument and calls
  `db/client.py` functions with it.
- `services/clerk_admin.py` is pure HTTP. No DB, no orchestration, no
  feature flags. Single responsibility: "delete a Clerk user, with retry."
- `build_service_role_client()` is imported only by
  `routes/account_deletion.py` and `routes/webhooks_clerk.py`. Enforced
  by grep in CI.
- All Pydantic models in `schemas.py`. The `AuditEvent` enum lives there.

## Auth

| Endpoint | Auth |
|---|---|
| `POST /account/delete` | Clerk JWT via `get_user_ctx` |
| `POST /webhooks/clerk` | Svix signature |

`get_user_ctx` gains a profile-existence guard: after JWT verification,
`SELECT 1 FROM profiles WHERE user_id = sub`. Missing → 401 Unauthorized.

This single check covers two concerns at once:

1. **Deleted users:** the wipe deletes the `profiles` row, so any
   subsequent authenticated request fails the guard. True instant
   revocation, even though the JWT itself remains cryptographically
   valid until its `exp`.
2. **Stale tokens / unknown subjects:** any `sub` we've never seen
   (or that has been wiped) is rejected with the same uniform 401,
   leaking no information about whether the account ever existed.

Cost: +1 indexed query per authenticated request. No caching — instant
revocation must be… instant.

Returning 401 (not 410 Gone) is deliberate: 410 would confirm "this user
existed and was deleted," which is information we don't owe the caller.

## Failure modes & recovery

| Failure | Behaviour | Recovery |
|---|---|---|
| Feature flag off | 503 | Frontend hides button; 503 is defense-in-depth. |
| Profiles row already missing | 401 (from guard, before route body runs) | None — already deleted. |
| `delete_user_data()` RPC fails | 500. Audit "request_initiated" already written. Clerk DELETE never runs. | User retries; RPC is idempotent. |
| Clerk DELETE fails after 3 retries | 502. Audit "clerk_delete_failed" written. Data already gone. | User retries → wipe is no-op, Clerk DELETE retried. Webhook reconciles if operator deletes via Clerk dashboard. |
| Clerk DELETE returns 404 | Treat as success (idempotent). | Built-in. |
| Resend send fails | Log warning, continue to 204. | None — courtesy email, not a correctness requirement. |
| Webhook never arrives | No-op. Wipe already happened inline. | None. |
| Webhook arrives normally | Svix verify → idempotency → dispatch → no-op RPC. 200. | Happy backstop. |
| Duplicate webhook delivery | `webhook_events` PK conflict short-circuits. 200. | Built-in. |
| User double-clicks delete | Both requests race. RPC is idempotent. Clerk DELETE: one 200, one 404 (treated as success). User gets two emails. | Acceptable. Frontend disables button on submit; this is the "what if it gets through anyway" path. |
| Auth'd request lands between wipe and Clerk DELETE | Profile guard → 401. | Built-in. |
| JWT issued before deletion, used after | Profile guard → 401. | True instant revocation. |
| Render instance dies after wipe, before Clerk DELETE | Wipe persisted. Clerk user alive but unable to use the API (profile guard). | Operator deletes Clerk user manually via dashboard; webhook fires; no-op RPC. Document in rollback runbook. |
| Clerk webhook secret rotated mid-deployment | Signature fails → 401 → Clerk retries. Inline wipe already succeeded; webhook-side is cosmetic. | Update env var, restart. |

## Environment variables (Render)

| Var | Status | Purpose |
|---|---|---|
| `CLERK_ISSUER` | existing | JWT verification |
| `CLERK_WEBHOOK_SECRET` | existing | Svix verification |
| `CLERK_SECRET_KEY` | NEW | Clerk Backend API admin token (DELETE /v1/users) |
| `RESEND_API_KEY` | existing | |
| `RESEND_FROM_EMAIL` | existing | |
| `SUPABASE_URL` | existing | |
| `SUPABASE_ANON_KEY` | existing | |
| `SUPABASE_SERVICE_ROLE_KEY` | NEW | Used only by `routes/account_deletion.py` and `routes/webhooks_clerk.py` |
| `ACCOUNT_DELETION_ENABLED` | NEW | Feature gate, default `false` |
| `DELETION_COMPLETED_TEMPLATE_ID` | NEW | Resend template ID for the deletion-confirmation email |

## Phased rollout

### Phase 1 — Database foundation *(one migration, one PR)*

- Migration `2026MMDDHHMMSS_account_deletion.sql` with `account_deletion_audit` + RLS, `webhook_events`, `delete_user_data()`.
- SQL smoke test: seed a fake user across all 12 tables, call the function, verify everything is gone, run again, verify no-op.
- *Blocks everything downstream.*

### Phase 2 — Pure modules

- `services/clerk_admin.py` — TDD: 5xx → exp backoff (3 attempts), 4xx (incl. 404) → no retry, success → return.
- `services/email_service.py` — add `send_account_deleted` + Settings field for the Resend template ID.
- `models/schemas.py` — `AuditEvent` enum.
- `config.py` — new env vars.
- `db/client.py` — service-role client, fetchers, audit insert, idempotency helper, RPC wrapper.
- All unit-tested with mocks. No FastAPI surface yet.

### Phase 3 — Wiring

- `services/deletion_service.py` — TDD on order + branch behavior.
- `routes/account_deletion.py` — single `POST /account/delete`, feature-flag gated, registered in `main.py`.
- `routes/deps.py` — add `profile_exists` guard, return 401 on miss. Update `test_deps.py`.
- `routes/webhooks_clerk.py` — folds `user.created` (existing welcome) + `user.deleted` (backstop). Registered in `main.py`. **Do not delete `emails.py` yet.**
- Run full suite. With feature flag default `false`, this is dark-launchable.
- Cutover: delete `app/routes/emails.py` and drop its `include_router` from `main.py` *only after* `webhooks_clerk.py` is confirmed handling `user.created` in staging. Last commit of the phase.

### Phase 4 — Config + flip *(ops-only, no code)*

- `render.yaml`: declare new env vars.
- Render dashboard: set `CLERK_SECRET_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `DELETION_COMPLETED_TEMPLATE_ID` (still flag-off).
- Resend dashboard: create the deleted-confirmation template.
- Clerk dashboard: confirm webhook URL points at `/webhooks/clerk`, verify `user.created` and `user.deleted` events are subscribed, confirm Account Portal "delete account" UI is disabled (we own the trigger).
- Smoke test from Clerk dashboard's "Send test event" for both event types.
- End-to-end manual test in production with `ACCOUNT_DELETION_ENABLED=false`: seed a throwaway user, hit the route directly with a temporary flag override or `dev_only` guard, confirm wipe, audit, Clerk deletion, email, webhook backstop no-op.
- **Last action:** flip `ACCOUNT_DELETION_ENABLED=true` in production.

## What we deliberately don't do

- No grace period. The frontend "are you sure?" modal is the only confirmation step.
- No email confirmation step. Inline deletion + completion email replace it.
- No tokens, no `/confirm` or `/cancel` endpoints.
- No cron of any kind for deletion. Cron infrastructure exists for budget archival; deletion does not borrow it.
- No `account_deletion_requests` state table. The audit log is the only persistent record.
- No retry of `delete_user_data()` itself. The function is designed to be invoked exactly once per request and is naturally idempotent on retry.
- No fire-and-forget Clerk delete. Synchronous, so failures are visible.
- No Clerk-existence check in `get_user_ctx`. The profile-existence check is sufficient and avoids per-request Clerk API calls.
- No anonymisation table. Audit log uses sha256(user_id); nothing else about the user persists.

## Test priorities (de-risk these first)

1. `delete_user_data()` against a seeded test user — FK ordering, idempotency.
2. `clerk_admin.py` retry behavior — 5xx exp backoff, 4xx no retry, 404 → success.
3. `deletion_service.py` orchestration — order of operations, email-on-success-only, clerk-failure-after-wipe path.
4. `webhooks_clerk.py` — Svix signature, idempotency via `webhook_events`, both event types dispatched correctly.
5. `get_user_ctx` profile guard — 401 on missing profile, 200 on present profile.

Templates and UI hiding are recoverable. The above are not.

## Acceptance criteria

- [ ] Phase 1 migration applied, function smoke test documented in PR.
- [ ] Phase 2 unit tests green, no FastAPI surface exposed.
- [ ] Phase 3 full test suite green, dark-launched (flag off).
- [ ] Phase 4 dashboard config verified, manual end-to-end run documented.
- [ ] `ACCOUNT_DELETION_ENABLED=true` is the **last** change before users see the flow.

## Open items (acknowledged, deferred)

- Email template content & Resend template ID — to be authored alongside Phase 2; placeholder ID in `Settings` until then.
- Backups: deletion is from the live DB. Backups age out per Supabase retention. Surfaced in privacy policy, not in this spec.
- If users later request a recovery window, revisit by reintroducing the original 30-day-grace design as a layer on top of the inline path (the audit table and Clerk webhook are already in place).

## Approval

Approved 2026-05-06 by vicentbnf@gmail.com over the brainstorming session.
Implementation will be carried out by the owner; no separate writing-plans
hand-off requested.
