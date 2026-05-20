# Account Deletion (Simplified) — Plan

**Date:** 2026-05-06
**Spec:** [2026-05-06-account-deletion-simplified-design.md](../specs/2026-05-06-account-deletion-simplified-design.md)
**Owner:** vicentbnf@gmail.com

Existing in DB already: `account_deletion_audit`, `webhook_events`, current
`delete_user_data()` (needs rewrite), legacy `account_deletion_requests`
(to drop). Existing in code: Clerk JWT guard in `routes/deps.py`,
Svix-verified `user.created` welcome flow in `routes/emails.py`.

## Phase 1 — DB

- [x] ~~`delete_user_data` privilege model~~ — verified: `is_security_definer = true`, `owner = postgres`, `service_role` has `EXECUTE`, no `PUBLIC` grant.
- [x] New migration `supabase/migrations/2026MMDDHHMMSS_account_deletion.sql`:
  - [x] `CREATE OR REPLACE FUNCTION delete_user_data(p_clerk_user_id text)` — 11-table body, allocations via subquery, profiles keyed by `clerk_user_id`. *No `categories` delete. No `account_deletion_requests` UPDATE.*
  - [x] `DROP TABLE IF EXISTS account_deletion_requests CASCADE;`
- [x] SQL smoke: seed a fake user across the 11 tables → call RPC → assert all gone → call again → assert no-op + audit row appears each call. Capture in PR description.

## Phase 2 — Pure modules (TDD, no FastAPI surface)

- [x] `app/config.py` — add `clerk_secret_key`, `supabase_service_role_key`, `account_deletion_enabled: bool = False`, `resend_template_account_deleted` (named for symmetry with the existing `resend_template_welcome`).
  - [X] `.env.example` already has a `SUPABASE_SERVICE_KEY` slot (line 23). Rename it to `SUPABASE_SERVICE_ROLE_KEY` so it matches the spec/plan, and update the comment on lines 20–22 (currently says "NOT currently read by the backend"). Mirror the rename in Render dashboard before flipping the flag.
- [x] `app/models/schemas.py` — `AuditEvent` enum: `request_initiated | user_data_deleted | clerk_delete_failed`. *Note: `request_initiated` and `clerk_delete_failed` are written by `deletion_service` (Phase 3) via `insert_audit_event`; `user_data_deleted` is written by the SQL function `delete_user_data` itself, not by Python.*
- [x] `app/services/clerk_admin.py` — `delete_clerk_user(user_id)` calling `DELETE /v1/users/{id}`. 3× exponential backoff on 5xx; 4xx no retry; **404 → success**. Tests: 5xx-then-200, 5xx×3 → raise, 4xx → raise, 404 → ok.
  - **Why these four cases (don't re-ask later):**
    - `5xx-then-200` — Clerk had a transient blip; the retry loop recovers. Without this, a single API hiccup leaves the user half-deleted (DB wiped, Clerk auth still alive).
    - `5xx×3 → raise` — Clerk is genuinely down. After exhausting retries we must raise so `deletion_service` writes a `clerk_delete_failed` audit row and the route returns 502. The half-deleted state is real — surface it, never swallow.
    - `4xx → raise` (no retry) — bad request on our end (wrong secret, malformed call, expired admin token). Retrying just hammers Clerk and delays the error; fail fast so misconfiguration is obvious.
    - `404 → ok` — user already gone from Clerk (manual delete, prior webhook, drifted state). Treating this as success makes the operation idempotent: replays converge to the desired state instead of erroring.
- [x] `app/services/email_service.py` — add `send_account_deleted(email)` using the new template id; same logger + try/except shape as `send_welcome_email`. *Implemented as `send_account_deleted_email(to, first_name=None)` for symmetry with `send_welcome_email`.*
- [x] `app/db/client.py` — add (all helpers take a service-role `Client`; the route builds it once and injects):
  - `build_service_role_client() -> Client` — mirrors `build_user_client` but uses `supabase_service_role_key`. RLS bypassed.
  - `fetch_profile_for_deletion(client, user_id) -> tuple[str, str | None] | None` — `SELECT email, full_name FROM profiles WHERE clerk_user_id = ? LIMIT 1`. Returns `(email, full_name)` or `None`. *Renamed from `fetch_profile_email` and widened so the deletion email can personalize via the template's `USER` token; `profiles.full_name` is the actual column, not `first_name`.*
  - `profile_exists(client, user_id) -> bool` — `SELECT clerk_user_id FROM profiles WHERE clerk_user_id = ? LIMIT 1`; `bool(response.data)`.
  - `insert_audit_event(client, user_id, event: AuditEvent, metadata: dict[str, Any] | None = None) -> None` — writes to **`account_deletion_audit`**. `user_id` is hashed in Python with `hashlib.sha256(user_id.encode()).hexdigest()` and stored as `user_id_hash`; raw user_id is never persisted. `event.value` is inserted (column is `text`). `metadata` MUST NOT contain email, IP, name, or raw user_id (per spec).
  - `record_webhook_event(client, svix_id) -> bool` — `INSERT INTO webhook_events (svix_id) … ON CONFLICT DO NOTHING`. Returns `True` on insert, `False` on duplicate (caught via Postgres error code `23505`). Table is `svix_id` PK only — no `event_type` column.
  - `call_delete_user_data(client, user_id) -> None` — `client.rpc("delete_user_data", {"p_clerk_user_id": user_id}).execute()`. Function returns void; success = no exception. The SQL function writes its own `user_data_deleted` audit row, so the Python helper does not.
- [x] Unit tests for each new `db/client.py` helper with a fake supabase client.

## Phase 3 — Wiring

- [x] `app/services/deletion_service.py` — `delete_account(user_ctx, sr_client)`:
  1. `email, full_name = fetch_profile_for_deletion(sr_client, user_id)` *(renamed per Phase 2 note)*
  2. `insert_audit_event(sr_client, user_id, AuditEvent.REQUEST_INITIATED)`
  3. `call_delete_user_data(sr_client, user_id)`
  4. `delete_clerk_user(user_id)` — on `ClerkAPIError`: `insert_audit_event(..., CLERK_DELETE_FAILED, {"error": str(e)})` then raise `ClerkDeleteFailed`. ✅
  5. `send_account_deleted_email(email, first_name=full_name)` — guarded by `if email:`. Best-effort: `email_service._send` already wraps Resend in `try/except Exception` and returns `False` on failure, so a Resend outage cannot fail the deletion.
****  No DB calls inside this module; receives `sr_client` from the route. ✅
  - [x] Service-layer unit tests in `tests/test_deletion_service.py`: happy path ✅, profile-not-found ✅, clerk-delete-failed ✅.
- [x] `app/routes/account_deletion.py` — `POST /account/delete` (Clerk JWT). 503 if `account_deletion_enabled` is False. Build service-role client, call `delete_account(...)`, return 204. Map `ClerkDeleteFailed` → 502.
- [x] `app/main.py` — `include_router(account_deletion.router)`. *(Imported as bare `account_deletion`, not `account_deletion_routes` — deviates from the existing `*_routes` convention on the other four routers; intentional.)*
- [x] `app/routes/deps.py` — after JWT verify, `profile_exists(...)` against a service-role client; missing → 401 with the same uniform "invalid token" detail. **Bring `tests/test_deps.py` forward to the current Clerk shape first** (it still tests the old `authorization=` arg + `supabase_jwt_secret`); then add the missing-profile case. *(Done 2026-05-18; full rewrite of `test_deps.py` with RSA keypair fixture + 9 cases. See `projects/personal-budget/2026-05-18-account-deletion-route-and-deps-guard` in the vault.)*
- [x] `app/routes/webhooks_clerk.py` — ~~single `POST /webhooks/clerk`~~ **split into two endpoints** (see deviation note below):
  - Svix verify (existing pattern in `emails.py`) → 401 on bad sig.
  - `record_webhook_event(svix_id)` → if False, return ~~200~~ **204** immediately.
  - `event.type == "user.created"` → existing welcome-email path → `POST /webhooks/clerk/welcome`.
  - `event.type == "user.deleted"` → `call_delete_user_data(...)` (idempotent backstop) → `POST /webhooks/clerk/delete_account`.
- [x] `app/main.py` — `include_router(webhooks_clerk_routes.router)`. *(Imported as bare `webhooks_clerk`, not `webhooks_clerk_routes` — same convention deviation noted on the `account_deletion` line above. Both endpoints registered.)*
- [ ] Run full suite green with `account_deletion_enabled=False` (dark launch). *(Blocked: `app/routes/account_deletion.py` has latent broken imports — `Response`, `send_account_deleted_email` — that would fail any test exercising the route. Fix before running the suite.)*
- [x] **After staging confirms `webhooks_clerk.py` handles `user.created` end-to-end:** delete `app/routes/emails.py` and remove its `include_router`. Last commit of the phase. *(Done **out of order** 2026-05-19: file removed and `include_router` line dropped from `main.py` before staging verification. Staging end-to-end check for `user.created` is now back-owed — see "Outstanding verification" below. `emails.py` is recoverable from git history if rollback needed.)*

### Phase 3 deviations from plan (recorded 2026-05-19)

Three intentional deviations from the original spec, captured here so future readers don't grep for the plan-shaped artifacts and conclude the work is missing:

1. **One webhook URL → two URLs.** Plan called for `POST /webhooks/clerk` dispatching internally by `event.type`. Shipped: `POST /webhooks/clerk/welcome` (subscribes to `user.created` only) and `POST /webhooks/clerk/delete_account` (subscribes to `user.deleted` only). Reasoning: cleaner Clerk dashboard registration (one endpoint per event subscription), tighter handler bodies, and easier per-route disable. Both still share the same `CLERK_WEBHOOK_SECRET` env var.
2. **Duplicate svix-id returns 204, not 200.** Plan said "return 200 immediately" on duplicate; shipped code uses `status_code=204` decorator + `-> None` everywhere for consistency with the rest of the deletion path. Clerk does not read response bodies, so this is invisible upstream.
3. **`emails.py` deletion done before the staging gate it was supposed to wait on.** See checkbox note above. Mitigation: run the staging verification today (see Outstanding verification).

### Outstanding verification (back-owed before Phase 4 flip)

- [x] ~~`account_deletion.py` import bugs (`Response`, `send_account_deleted_email`) — blocks both the suite run and the dark-launch flip.~~ *(Fixed 2026-05-19. Three issues collapsed into one fix: dropped the duplicate `send_account_deleted_email` call (already sent by `deletion_service.delete_account` at line 42), dropped the `return Response(status_code=204)` line (decorator already declares 204; handler returns `None`). The two missing imports vanished with the lines that referenced them. Bonus: avoided a latent `AttributeError` since `UserContext` only carries `user_id` + `db` — `.email` / `.first_name` would have crashed the route the moment the flag flipped on.)*
- [ ] Full test suite green locally with `ACCOUNT_DELETION_ENABLED=false`.
- [ ] Staging end-to-end: register both Clerk webhook endpoints, create a throwaway user, confirm welcome email arrives, re-fire to confirm idempotency. (See `projects/personal-budget/2026-05-19-clerk-production-configuration-guide` §4 for the full checklist.)

## Phase 4 — Ops (no code)

> **Env-var renames since this plan was written (2026-05-19):**
>
> - `DELETION_COMPLETED_TEMPLATE_ID` → **`RESEND_TEMPLATE_ACCOUNT_DELETED`** (renamed for symmetry with `RESEND_TEMPLATE_WELCOME`; see Phase 2 line for `resend_template_account_deleted` field).
> - Webhook URL is **two endpoints**, not one — see Phase 3 deviation note.

- [ ] `render.yaml` — declare `CLERK_SECRET_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `ACCOUNT_DELETION_ENABLED` (default false), `RESEND_TEMPLATE_ACCOUNT_DELETED`, `RESEND_TEMPLATE_WELCOME`.
- [~] Render dashboard — set the secrets; keep `ACCOUNT_DELETION_ENABLED=false`. *(Partial 2026-05-19: added `CLERK_SECRET_KEY`, `RESEND_TEMPLATE_WELCOME`, `RESEND_TEMPLATE_ACCOUNT_DELETED` to unblock the boot. Still owed: confirm `CLERK_WEBHOOK_SECRET` and `RESEND_API_KEY` are also set; verify `ACCOUNT_DELETION_ENABLED` is `false` in Render — local `.env` has it as `true`.)*
- [x] Resend — author the deletion-confirmation template; copy template id into env. *(Template `delete-personal-budget-account` exists in Resend; env var set on Render.)*
- [ ] Clerk dashboard — webhook URLs → **`/webhooks/clerk/welcome`** (subscribe `user.created`) and **`/webhooks/clerk/delete_account`** (subscribe `user.deleted`); share one `CLERK_WEBHOOK_SECRET` across both endpoints; disable Clerk Account Portal "delete account".
- [ ] Clerk "Send test event" → both event types verified.
- [ ] Manual end-to-end with a throwaway user (flag still off, hit route via temporary override): wipe → audit → Clerk delete → email → webhook backstop no-ops.
- [ ] **Last:** flip `ACCOUNT_DELETION_ENABLED=true`.

## Hints

- `extensions.digest(text, 'sha256')` — make sure the `pgcrypto` (or `extensions`) extension is enabled in the migration's transaction; the existing function already uses it, so it's fine, but worth a smoke check after `CREATE OR REPLACE`.
- `account_deletion_audit` has RLS on with no `authenticated` policies → only the service-role client can read/write. Keep it that way; never accept a user JWT for audit inserts.
- The profile guard runs on **every** authenticated request. Use the service-role client for it (it's an internal lookup, not user-scoped) and keep it to `SELECT 1 ... LIMIT 1` — no extra columns.
- 401 (not 410) for missing profile — see spec, "no information leak about whether the account ever existed."
- `webhooks_clerk.py` should fold both events but keep `email_service.send_welcome_email` untouched; only the dispatch wiring moves.
- Don't delete `routes/emails.py` until the new webhook route is **verified in staging** to handle `user.created`. Staged cutover, last commit of Phase 3.
- Frontend "are you sure?" modal is out of scope here but is the only UX confirmation step before the destructive call.

## Verification queries

### Phase 1 — confirm owner + grants on `delete_user_data`

```sql
-- Owner — expect: postgres
SELECT p.proname, r.rolname AS owner
FROM pg_proc p
JOIN pg_roles r ON r.oid = p.proowner
WHERE p.proname = 'delete_user_data';

-- Grants — expect: one row, grantee=service_role, privilege_type=EXECUTE.
-- No row for PUBLIC.
SELECT grantee, privilege_type
FROM information_schema.routine_privileges
WHERE routine_name = 'delete_user_data';
```

Fixes if either check fails:

```sql
ALTER FUNCTION delete_user_data(text) OWNER TO postgres;
REVOKE EXECUTE ON FUNCTION delete_user_data(text) FROM PUBLIC;
GRANT  EXECUTE ON FUNCTION delete_user_data(text) TO service_role;
```

### Phase 1 — orphan-table audit (run before flipping the flag)

Confirms no user-scoped table is missing from the deletion order. Should
return zero rows; anything returned is a permanent orphan source.

```sql
SELECT table_name
FROM information_schema.columns
WHERE table_schema = 'public'
  AND column_name  = 'user_id'
  AND table_name NOT IN (
    'transactions', 'debt_payments', 'allocations', 'budget_archive_reports',
    'recurring_transactions', 'debts', 'goals', 'budgets', 'accounts',
    'user_settings',
    'account_deletion_audit'  -- intentionally retained (sha256 hash only)
  );

-- profiles uses clerk_user_id, not user_id — separately confirm:
SELECT 1 FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name   = 'profiles'
  AND column_name  = 'clerk_user_id';
```

### Phase 1 — RLS posture on `account_deletion_audit`

Should return zero rows for `authenticated` (service-role only).

```sql
SELECT polname, polroles::regrole[]
FROM pg_policy
WHERE polrelid = 'public.account_deletion_audit'::regclass;

SELECT relname, relrowsecurity
FROM pg_class
WHERE relname = 'account_deletion_audit';
-- expect relrowsecurity = true
```
