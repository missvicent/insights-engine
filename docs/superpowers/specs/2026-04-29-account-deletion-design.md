# Account Deletion (GDPR-Style, 30-Day Grace) — Design

**Date:** 2026-04-29
**Status:** Approved (brainstorming) — pending implementation
**Owner:** vicentbnf@gmail.com

## Goal

Let a user delete their account from settings. Hard-delete all their data
across the 12 user-owned tables after a 30-day grace period during which
the user can cancel. Drive the irreversible cleanup from Clerk's
`user.deleted` webhook so admin-initiated deletions and user-initiated
deletions converge on the same destructive path.

## Non-goals

- Anonymisation, retention, or audit-only copies of deleted data.
- Wiping point-in-time backups (out of our control; ages out per
  Supabase Pro retention).
- Frontend implementation (separate concern).
- Stripe / payment-provider deletion (no payments in scope).

## Architectural decisions

| Decision | Choice | Why |
|---|---|---|
| Delete strategy | Hard delete | Personal-finance app, no regulatory obligation. |
| Grace period | 30 days, cancellable | User-recoverable mistakes. |
| Source of truth for destructive SQL | Clerk `user.deleted` webhook | Same path for user-, cron-, and admin-initiated deletions. |
| Webhook → SQL coupling | Service-role Supabase client calls `delete_user_data()` RPC. | Function is `SECURITY DEFINER`; bypass RLS only inside this single, trusted code path. |
| Cron transport | `pg_cron` → `pg_net.http_post` → FastAPI, secret in `X-Cron-Secret` header | Secrets live in Render env, not Supabase Vault. |
| Re-auth on delete request | None server-side | Confirmation email link is the second factor. |
| Audit privacy | SHA-256 of user_id, no email/IP/name | Audit must not itself violate the deletion. |
| Feature gate | `ACCOUNT_DELETION_ENABLED` env flag | Ship code dark, flip on after validation. |

## Data model

### `account_deletion_requests`

| Column | Type | Notes |
|---|---|---|
| `id` | `uuid pk default gen_random_uuid()` | |
| `user_id` | `text not null` | Clerk `sub` |
| `email` | `text not null` | Captured at request time so emails work after Clerk-side delete |
| `status` | `text` | `pending_confirmation` \| `scheduled` \| `cancelled` \| `processing` \| `clerk_called` \| `completed` \| `failed` |
| `confirmation_token_hash` | `bytea` | sha256 of url-safe token |
| `confirmation_token_expires_at` | `timestamptz` | `created_at + 1h` |
| `scheduled_deletion_at` | `timestamptz` | Set when status moves to `scheduled` |
| `created_at` | `timestamptz default now()` | |**
| `confirmed_at` | `timestamptz` | |**
| `cancelled_at` | `timestamptz` | |
| `clerk_called_at` | `timestamptz` | |
| `completed_at` | `timestamptz` | |
| `failed_at` | `timestamptz` | |
| `failure_reason` | `text` | |
| `last_error_at` | `timestamptz` | |
| `retry_count` | `int default 0` | |

**Indexes:**

- `idx_deletion_requests_due ON (scheduled_deletion_at) WHERE status='scheduled'` — cron's hot path.
- `unique idx_deletion_requests_active_per_user ON (user_id) WHERE status IN ('pending_confirmation','scheduled','processing','clerk_called')` — at most one open request per user.
- `idx_deletion_requests_user_status ON (user_id, status)` — for `GET /status` and the account-locked guard.

**RLS:** enabled.
- `select` where `user_id = auth.jwt()->>'sub'`
- `insert with check (user_id = auth.jwt()->>'sub')`
- `update using (user_id = auth.jwt()->>'sub') with check (user_id = auth.jwt()->>'sub' AND status='cancelled')`
- Service role bypasses for cron + webhook writes.

### `account_deletion_audit`

| Column | Type | Notes |
|---|---|---|
| `id` | `bigserial pk` | |
| `user_id_hash` | `bytea not null` | `digest(user_id, 'sha256')` |
| `event` | `text not null` | `request_created`, `request_confirmed`, `request_cancelled`, `clerk_delete_called`, `user_data_deleted`, `request_failed` |
| `occurred_at` | `timestamptz default now()` | |
| `metadata` | `jsonb` | Stage-specific. Must NOT contain email, IP, name, or raw user_id. |

**RLS:** enabled, **no policies for `authenticated`** → service role only.

### `webhook_events`

| Column | Type |
|---|---|
| `svix_id` | `text primary key` |
| `received_at` | `timestamptz default now()` |

`INSERT ... ON CONFLICT DO NOTHING RETURNING svix_id` for idempotency.

## `delete_user_data(p_clerk_user_id text)`

`SECURITY DEFINER`, owned by `postgres`, `revoke execute from public`,
`grant execute to service_role`. Single transaction (implicit in PL/pgSQL).
FK-correct order:

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

Then:
- `UPDATE account_deletion_requests SET status='completed', completed_at=now() WHERE user_id=p_clerk_user_id AND status IN ('clerk_called','scheduled','processing')`
- Insert audit row `('user_data_deleted', sha256(p_clerk_user_id))`.

Idempotent: re-running on an already-deleted user is a no-op (every
DELETE affects 0 rows; the UPDATE matches 0 rows).

## End-to-end flow

```
User clicks "Delete account"
  └─ POST /account/deletion/request (user JWT)
        ├─ create row (status=pending_confirmation), 1h token
        └─ Email #1 (confirmation link)

User clicks Email #1 link
  └─ GET /account/deletion/confirm?token=...    (NO JWT)
        ├─ verify token (constant-time), check expiry
        ├─ status -> scheduled, scheduled_deletion_at = now()+30d
        └─ Email #2 (scheduled, cancel link)

[any time within 30d]
User clicks "Cancel deletion"
  └─ POST /account/deletion/cancel (user JWT)
        └─ status -> cancelled

[Day 30]
pg_cron (every 15 min)
  └─ POST /internal/cron/process-deletions (X-Cron-Secret)
        └─ for each due row (FOR UPDATE SKIP LOCKED):
              ├─ Email #3 (deleted)
              ├─ DELETE Clerk user (3x retry on 5xx)
              └─ status -> clerk_called

Clerk fires user.deleted webhook
  └─ POST /webhooks/clerk (Svix-signed)
        ├─ idempotency check via svix-id
        └─ rpc('delete_user_data', clerk_id)
              └─ status -> completed, audit row written

Reconciliation cron (nightly)
  └─ for each row stuck in clerk_called > 1h:
        └─ rpc('delete_user_data', clerk_id) directly
```

## Components

```
app/
├── auth/jwks.py                     (unchanged)
├── context.py                       (unchanged)
├── db/client.py                     +Settings fields, +build_service_role_client(),
│                                     +deletion CRUD fetch_*/create_*
├── models/schemas.py                +DeletionRequestRow, +DeletionRequestStatus,
│                                     +DeletionStatusResponse
├── services/
│   ├── deletion_tokens.py           NEW (generate/hash/compare)
│   ├── deletion_service.py          NEW (orchestration, no DB calls inside)
│   ├── clerk_admin.py               NEW (DELETE /v1/users/{id} with retry)
│   └── email_service.py             +send_deletion_confirmation/_scheduled/_completed
└── routes/
    ├── deps.py                      +account-locked guard (1 query/req)
    ├── account_deletion.py          NEW (4 endpoints, user JWT)
    ├── webhooks_clerk.py            NEW (renamed/folded /emails/* webhooks)
    └── internal_cron.py             NEW (X-Cron-Secret-protected)

tests/
├── test_deletion_tokens.py          NEW (uniqueness, constant-time)
├── test_email_service.py            +deletion email assertions
├── test_account_deletion_routes.py  NEW (happy + edge paths, mocked Resend/Clerk)
├── test_webhooks_clerk.py           NEW (signature, idempotency)
└── manual_webhook.md                NEW (ngrok + svix listen runbook)
```

`app/routes/emails.py` is **deleted** after `webhooks_clerk.py` covers
both `user.created` and `user.deleted`. Single Clerk webhook URL.

## Architectural rules (per CLAUDE.md)

- `routes/account_deletion.py` is thin — verifies, calls
  `services/deletion_service.py`, returns response.
- `services/deletion_service.py` orchestrates but does not query Supabase
  directly; takes `UserContext` and calls `db/client.py` fetchers.
- `services/deletion_tokens.py` is pure — no FastAPI, no DB, unit-testable.
- `db/client.py` owns every Supabase call. `build_service_role_client()`
  is added there and is only imported by `webhooks_clerk.py` and
  `internal_cron.py`. Enforced by grep in CI.
- All Pydantic models in `models/schemas.py`. No inline models.

## Auth

| Endpoint | Auth |
|---|---|
| `POST /account/deletion/request` | Clerk JWT via `get_user_ctx` |
| `GET /account/deletion/confirm?token=...` | **None** — token IS the auth |
| `POST /account/deletion/cancel` | Clerk JWT via `get_user_ctx` |
| `GET /account/deletion/status` | Clerk JWT via `get_user_ctx` |
| `POST /webhooks/clerk` | Svix signature |
| `POST /internal/cron/process-deletions` | Constant-time compare on `X-Cron-Secret` |

`get_user_ctx` gains an account-locked guard: after JWT verification,
look up `account_deletion_requests` for `status='failed'` and reject
with 423 Locked if found. +1 indexed query per authenticated request;
no caching (cancel must take effect immediately).

## Failure modes & recovery

| Failure | Behaviour |
|---|---|
| Resend down during deletion email | Log warning; deletion proceeds. We'd rather honour the schedule than hold data. |
| Clerk DELETE 5xx | Retry 3x exponential backoff inside `clerk_admin.py`. After 3, mark `failed`, alert. |
| Clerk DELETE succeeds, webhook never delivers | Reconciliation cron (nightly) finds rows in `clerk_called` > 1h with `completed_at IS NULL` and calls `delete_user_data` directly via service role. |
| FastAPI down when pg_cron fires | `pg_net` call fails; next 15-minute tick picks the same rows up (idempotent). |
| Duplicate Svix webhook delivery | `webhook_events` unique-key insert short-circuits. |
| User clicks confirmation token twice | Second click is a no-op (status already `scheduled`). |
| User clicks cancel after Clerk DELETE call | Status is `clerk_called`, not in cancellable set → 409 with friendly message. Already too late. |
| User re-signs up with same email during grace period | Clerk issues a new `sub`; old `user_id` deletion completes on schedule; new account is independent. |
| Two open requests for one user | Prevented by the unique partial index. Second `INSERT` fails → 409. |

## What we deliberately don't do

- No Vault. Cron secret is set with `ALTER DATABASE ... SET app.cron_secret`
  (superuser-readable only) and matches Render's `CRON_SHARED_SECRET`.
- No re-auth step server-side. The email confirmation link is the second factor.
- No retry of `delete_user_data()` itself on failure — the function is
  designed to be invoked exactly once per `clerk_called → completed`
  transition. Recovery is via reconciliation cron, not in-line retry.
- No anonymisation table. Audit log uses sha256 of user_id; nothing else
  about the user persists.

## Environment variables (Render)

| Var | Purpose |
|---|---|
| `CLERK_ISSUER` | (existing) JWT verification |
| `CLERK_WEBHOOK_SECRET` | (existing) Svix verification |
| `CLERK_SECRET_KEY` | NEW — Clerk Backend API admin token |
| `RESEND_API_KEY` | (existing) |
| `RESEND_FROM_EMAIL` | (existing) |
| `SUPABASE_URL` | (existing) |
| `SUPABASE_ANON_KEY` | (existing) |
| `SUPABASE_SERVICE_ROLE_KEY` | NEW — used in webhook + cron route only |
| `CRON_SHARED_SECRET` | NEW — matches Postgres `app.cron_secret` |
| `APP_BASE_URL` | NEW — used to build confirm/cancel links in emails |
| `FRONTEND_BASE_URL` | NEW — redirect target after `/confirm` |
| `ACCOUNT_DELETION_ENABLED` | NEW — feature gate, default `false` |
| `APP_ENV` | NEW — when `dev`, allows `_DEV_OVERRIDE_DELETION_DATE` to shrink the 30-day window |

## Phased rollout

1. **Phase 1 — Database foundation:** migration, `delete_user_data()`,
   indexes, pg_cron job (15 min), reconciliation cron (nightly).
2. **Phase 2 — Token + email service:** pure modules, fully unit-tested.
3. **Phase 3 — User-facing endpoints:** request, confirm, cancel, status,
   account-locked guard.
4. **Phase 4 — Server-to-server endpoints:** webhook (folded with
**   user.created), internal cron, Clerk admin client, idempotency table.
**5. **Phase 5 — Clerk + Render config:** dashboard changes, env vars.
6. **Phase 6 — Testing:** SQL function test, idempotency test,
   failure-mode tests, ngrok runbook, dev fast-forward.
7. **Phase 7 — Observability & rollout:** logging rules, alerting,
   feature flag, gradual rollout, rollback runbook.

## Cross-cutting blockers

| Item | Blocks |
|---|---|
| `delete_user_data` SQL function (Phase 1) | Webhook-driven deletion |
| Schema migration (Phase 1) | All Phase 3+ work |
| Webhook consolidation (Phase 4) | Removing `/emails/goodbye` cleanly |
| Clerk dashboard config (Phase 5) | Any production traffic |
| SQL function test (Phase 6) | Production deployment |
| Feature flag (Phase 7) | Production deployment (defaults off) |

## Test-first priorities (de-risk these first)

1. `delete_user_data()` against a seeded test user — FK ordering, idempotency.
2. Token issue + verify — auth surface; bugs here delete the wrong user.
3. Webhook signature + Svix idempotency.
4. Happy path with `_DEV_OVERRIDE_DELETION_DATE = now() + 1 minute`.

Templates and UI hiding are recoverable. The above three are not.

## Open items (acknowledged, deferred)

- Frontend lockout UX when `status='failed'` — owned by frontend.
- Email template content & Resend template IDs — to be authored alongside
  Phase 2; placeholder IDs in `Settings` until then.
- Backups: deletion is from the live DB. Backups age out per Supabase
  retention. Surfaced in privacy policy, not in this spec.

## Approval

Approved 2026-04-29 by vicentbnf@gmail.com over the brainstorming session.
Implementation plan to follow via the `writing-plans` skill.
