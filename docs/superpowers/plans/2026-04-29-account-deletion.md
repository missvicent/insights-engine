# Account Deletion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a GDPR-style account deletion feature with a 30-day cancellable grace period, hard-deletion of all user data, driven by a Clerk `user.deleted` webhook through a `SECURITY DEFINER` Postgres function.

**Architecture:** Three layers. (1) Postgres holds state in `account_deletion_requests`, executes destructive SQL via `delete_user_data()`, and pings FastAPI from `pg_cron` via `pg_net`. (2) FastAPI exposes user-facing endpoints (Clerk JWT) for request/confirm/cancel/status, and server-to-server endpoints (Svix signature, shared-secret header) for the webhook and cron callback. (3) Resend delivers the three transactional emails. Source of truth for destructive cleanup is the Clerk `user.deleted` webhook so cron-, user-, and admin-initiated deletions converge on one path.

**Tech Stack:** Python 3.13, FastAPI, Pydantic v2, Supabase (Postgres + RLS + pg_cron + pg_net + pgcrypto), Clerk (RS256 JWT verification, Backend API admin client), Resend (Python SDK), Svix (webhook signature), pytest. All deps already in `requirements.txt`.

**Spec reference:** `docs/superpowers/specs/2026-04-29-account-deletion-design.md`.

---

## File map

### New files
| Path | Responsibility |
|---|---|
| `supabase/migrations/20260429000000_account_deletion.sql` | Tables, indexes, RLS, `delete_user_data()`, `webhook_events`, helper hashing function |
| `supabase/migrations/20260429000100_account_deletion_cron.sql` | pg_cron schedule for cron + reconciliation jobs (kept separate so it can be re-applied with different URLs per env) |
| `app/services/deletion_tokens.py` | Pure: generate / hash / constant-time compare confirmation tokens |
| `app/services/deletion_service.py` | Orchestration of request/confirm/cancel — no Supabase calls inside; takes `UserContext` and calls `db/client.py` fetchers |
| `app/services/clerk_admin.py` | `httpx`-backed Clerk Backend API wrapper for `DELETE /v1/users/{id}` with retry |
| `app/routes/account_deletion.py` | Four user-facing endpoints |
| `app/routes/webhooks_clerk.py` | Single `/webhooks/clerk` handler dispatching `user.created` and `user.deleted`. Replaces `app/routes/emails.py`. |
| `app/routes/internal_cron.py` | `POST /internal/cron/process-deletions` — secret header, processes due requests |
| `tests/test_deletion_tokens.py` | Unit tests for token module |
| `tests/test_deletion_service.py` | Service orchestration with mocked db fetchers |
| `tests/test_clerk_admin.py` | Retry behaviour, header construction |
| `tests/test_email_service_deletion.py` | New deletion email send functions |
| `tests/test_account_deletion_routes.py` | Integration tests via `TestClient` and `dependency_overrides` |
| `tests/test_webhooks_clerk.py` | Svix signature, dispatch by event type, idempotency |
| `tests/test_internal_cron.py` | Secret header, due-row selection, Clerk failure handling |
| `tests/test_account_lock.py` | `get_user_ctx` returns 423 when status='failed' for the user |
| `tests/manual_webhook.md` | Runbook for testing webhooks locally with Svix CLI + ngrok |
| `docs/runbooks/account-deletion.md` | Runbook: rollback, manual deletion, known failure modes |

### Modified files
| Path | Change |
|---|---|
| `app/db/client.py` | New `Settings` fields, `build_service_role_client()`, deletion CRUD functions, `webhook_events` insert helper |
| `app/services/email_service.py` | Three new send functions for deletion lifecycle; existing welcome/goodbye stay until Phase 4.5 |
| `app/models/schemas.py` | `DeletionRequestStatus`, `DeletionRequestRow`, `DeletionStatusResponse` |
| `app/main.py` | Register new routers, drop emails router, gate user-facing endpoints behind `ACCOUNT_DELETION_ENABLED` |
| `app/routes/deps.py` | Account-locked guard after JWT verification |
| `.env.example` | Add the new env vars |
| `render.yaml` | Add new env vars (sync: false) |

### Deleted files
| Path | When |
|---|---|
| `app/routes/emails.py` | Phase 4.5, after `webhooks_clerk.py` covers `user.created` + `user.deleted` |

---

## Conventions (match existing codebase)

- **Imports:** stdlib → third-party → local. Ruff with line-length 88, double-quote strings.
- **Type hints on every function signature.**
- **Tests use `class TestSomething:` style** with `pytest` fixtures. See `tests/test_insights_route.py`.
- **Routes are thin** — verify, call service, return response. Per CLAUDE.md.
- **`UserContext` is the shared currency** between routes and `db/`. Per `app/context.py`.
- **`fetch_*` for DB reads, verb+noun for engine functions.** Per CLAUDE.md.
- **`raise HTTPException(status_code=...)`** in routes — do NOT use `from None` indiscriminately, only when the upstream exception is sensitive.
- **Commit cadence:** one task = one commit. Format: `feat(area): summary` or `test(area): summary` matching existing log style.

---

## Phase 1 — Database foundation

> All Phase 1 tasks land SQL files under `supabase/migrations/`. The project doesn't currently have a migrations directory or Supabase CLI; you create the directory in Task 1.1. SQL is **applied by hand via Supabase Dashboard SQL editor** (Project → SQL Editor → New Query → paste the file → Run). This is a documentation step inside each task.

### Task 1.1 — Bootstrap migrations directory and write `account_deletion_requests` table

**Files:**
- Create: `supabase/migrations/20260429000000_account_deletion.sql`

- [x] **Step 1.1.1: Create the directory**

```bash
mkdir -p supabase/migrations
```

- [x] **Step 1.1.2: Write `account_deletion_requests` schema**

Create `supabase/migrations/20260429000000_account_deletion.sql` with:

```sql
-- Account deletion: state machine + audit log + destructive function.
-- Apply via Supabase Dashboard → SQL Editor. Idempotent (uses IF NOT EXISTS).

create extension if not exists pgcrypto;

-- ── State machine table ──────────────────────────────────────────────────────
create table if not exists public.account_deletion_requests (
    id uuid primary key default gen_random_uuid(),
    user_id text not null,
    email text not null,
    status text not null
        check (status in (
            'pending_confirmation',
            'scheduled',
            'cancelled',
            'processing',
            'clerk_called',
            'completed',
            'failed'
        )),
    confirmation_token_hash text,  -- hex-encoded sha256 (text avoids PostgREST bytea round-trip)
    confirmation_token_expires_at timestamptz,
    scheduled_deletion_at timestamptz,
    created_at timestamptz not null default now(),
    confirmed_at timestamptz,
    cancelled_at timestamptz,
    clerk_called_at timestamptz,
    completed_at timestamptz,
    failed_at timestamptz,
    failure_reason text,
    last_error_at timestamptz,
    retry_count int not null default 0
);

create index if not exists idx_deletion_requests_due
    on public.account_deletion_requests (scheduled_deletion_at)
    where status = 'scheduled';

create unique index if not exists idx_deletion_requests_active_per_user
    on public.account_deletion_requests (user_id)
    where status in ('pending_confirmation', 'scheduled', 'processing', 'clerk_called');

create index if not exists idx_deletion_requests_user_status
    on public.account_deletion_requests (user_id, status);
```

- [x] **Step 1.1.3: Apply via dashboard, verify no errors**

In Supabase SQL Editor, run the file. Expected: `Success. No rows returned.`

Verify table created:
```sql
select count(*) from public.account_deletion_requests;
```
Expected: 0.

- [x] **Step 1.1.4: Commit**

```bash
git add supabase/migrations/20260429000000_account_deletion.sql
git commit -m "feat(db): account_deletion_requests state-machine table

Schema only — RLS, audit log, and delete_user_data() arrive in
follow-up commits inside the same migration file."
```

---

### Task 1.2 — Add `account_deletion_audit` and `webhook_events`

**Files:**
- Modify: `supabase/migrations/20260429000000_account_deletion.sql`

- [x] **Step 1.2.1: Append audit and idempotency tables**

Append to the migration file:

```sql
-- ── Append-only audit log. NO email/IP/name. user_id stored hashed. ─────────
create table if not exists public.account_deletion_audit (
    id bigserial primary key,
    user_id_hash bytea not null,
    event text not null
        check (event in (
            'request_created',
            'request_confirmed',
            'request_cancelled',
            'clerk_delete_called',
            'user_data_deleted',
            'request_failed'
        )),
    occurred_at timestamptz not null default now(),
    metadata jsonb
);

create index if not exists idx_audit_user_hash
    on public.account_deletion_audit (user_id_hash, occurred_at desc);

-- ── Webhook idempotency. Drop oldest rows in retention task if needed. ──────
create table if not exists public.webhook_events (
    svix_id text primary key,
    received_at timestamptz not null default now()
);
```

- [x] **Step 1.2.2: Apply incremental SQL via dashboard**

Run the appended block only (do not re-run the whole file unless you trust IF NOT EXISTS — it does, but minimise blast radius).

Verify:
```sql
select count(*) from public.account_deletion_audit;
select count(*) from public.webhook_events;
```
Both 0.

- [x] **Step 1.2.3: Commit**

```bash
git add supabase/migrations/20260429000000_account_deletion.sql
git commit -m "feat(db): account_deletion_audit + webhook_events tables"
```

---

### Task 1.3 — RLS policies on `account_deletion_requests` and audit

**Files:**
- Modify: `supabase/migrations/20260429000000_account_deletion.sql`

- [x] **Step 1.3.1: Append RLS policies**

```sql
-- ── RLS ─────────────────────────────────────────────────────────────────────
alter table public.account_deletion_requests enable row level security;
alter table public.account_deletion_audit enable row level security;
alter table public.webhook_events enable row level security;

-- Users may only see their own requests.
drop policy if exists "deletion_requests_select_own"
    on public.account_deletion_requests;
create policy "deletion_requests_select_own"
    on public.account_deletion_requests
    for select
    to authenticated
    using (user_id = auth.jwt() ->> 'sub');

-- Users may only insert requests for themselves.
drop policy if exists "deletion_requests_insert_own"
    on public.account_deletion_requests;
create policy "deletion_requests_insert_own"
    on public.account_deletion_requests
    for insert
    to authenticated
    with check (user_id = auth.jwt() ->> 'sub');

-- Users may only set status='cancelled' on their own row.
drop policy if exists "deletion_requests_cancel_own"
    on public.account_deletion_requests;
create policy "deletion_requests_cancel_own"
    on public.account_deletion_requests
    for update
    to authenticated
    using (user_id = auth.jwt() ->> 'sub')
    with check (user_id = auth.jwt() ->> 'sub' and status = 'cancelled');

-- Audit + webhook_events: service-role only. No policy => no access for
-- authenticated/anon. Service role bypasses RLS by default.
```

- [x] **Step 1.3.2: Apply via dashboard**

- [x] **Step 1.3.3: Smoke-test RLS denies anon access**

In dashboard SQL editor, switch role to `authenticated` (top-right role selector) and run:
```sql
select count(*) from public.account_deletion_audit;
```
Expected: 0 rows AND no error (RLS silently filters all rows for `authenticated`).

```sql
select count(*) from public.account_deletion_requests;
```
Expected: 0 rows (same — no rows exist that match).

- [x] **Step 1.3.4: Commit**

```bash
git add supabase/migrations/20260429000000_account_deletion.sql
git commit -m "feat(db): RLS policies for deletion requests + audit"
```

---

### Task 1.4 — Implement `delete_user_data()` SECURITY DEFINER function

**Files:**
- Modify: `supabase/migrations/20260429000000_account_deletion.sql`

> FK-correct order from the spec: transactions → debt_payments → allocations → budget_archive_reports → recurring_transactions → debts → goals → budgets → accounts → categories(non-system) → user_settings → profiles.

- [ ] **Step 1.4.1: Append the function**

```sql
-- ── Destructive function. Single transaction, idempotent. ────────────────────
create or replace function public.delete_user_data(p_clerk_user_id text)
returns void
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
    -- FK-respecting deletion order. DELETE on a non-existent user is a no-op.
    delete from public.transactions where user_id = p_clerk_user_id;
    delete from public.debt_payments where user_id = p_clerk_user_id;
    delete from public.allocations where user_id = p_clerk_user_id;
    delete from public.budget_archive_reports where user_id = p_clerk_user_id;
    delete from public.recurring_transactions where user_id = p_clerk_user_id;
    delete from public.debts where user_id = p_clerk_user_id;
    delete from public.goals where user_id = p_clerk_user_id;
    delete from public.budgets where user_id = p_clerk_user_id;
    delete from public.accounts where user_id = p_clerk_user_id;
    delete from public.categories
        where user_id = p_clerk_user_id and is_system = false;
    delete from public.user_settings where user_id = p_clerk_user_id;
    delete from public.profiles where user_id = p_clerk_user_id;

    update public.account_deletion_requests
        set status = 'completed',
            completed_at = now()
        where user_id = p_clerk_user_id
          and status in ('clerk_called', 'scheduled', 'processing');

    insert into public.account_deletion_audit (user_id_hash, event, metadata)
    values (
        digest(p_clerk_user_id, 'sha256'),
        'user_data_deleted',
        jsonb_build_object('called_at', now())
    );
end;
$$;

revoke all on function public.delete_user_data(text) from public;
grant execute on function public.delete_user_data(text) to service_role;
```

- [ ] **Step 1.4.2: Apply via dashboard**

- [ ] **Step 1.4.3: Smoke test on a fake user**

```sql
select public.delete_user_data('user_test_does_not_exist');
select count(*) from public.account_deletion_audit
where event = 'user_data_deleted';
```
Expected: function returns successfully, audit row inserted (count >= 1).

Clean up the test audit row:
```sql
delete from public.account_deletion_audit where event = 'user_data_deleted';
```

- [ ] **Step 1.4.4: Commit**

```bash
git add supabase/migrations/20260429000000_account_deletion.sql
git commit -m "feat(db): delete_user_data SECURITY DEFINER function

Single-transaction, FK-correct hard delete across 12 user-owned tables.
Idempotent on already-deleted users. Service-role only."
```

---

### Task 1.5 — pg_cron + pg_net schedule

**Files:**
- Create: `supabase/migrations/20260429000100_account_deletion_cron.sql`

> Two cron jobs: the 15-minute due-deletion processor and the nightly reconciliation. The Render URL and shared secret differ per environment; this file is a template you paste into the dashboard with your values substituted, OR you keep the SQL parameterised via `current_setting('app.cron_secret')`.

- [ ] **Step 1.5.1: Write cron SQL with placeholders**

Create `supabase/migrations/20260429000100_account_deletion_cron.sql`:

```sql
-- Schedule pg_cron jobs that ping FastAPI to process due deletions.
-- IMPORTANT: replace <RENDER_URL> with your Render service URL before applying.
-- The shared secret lives in DB config, mirrored from Render env CRON_SHARED_SECRET.
--
-- Set the secret once per environment:
--   alter database postgres set app.cron_secret = '<value-matching-render>';
-- This is readable only to superuser. We use it as the outbound bearer for
-- pg_cron → FastAPI; the destructive secret (service role) lives in Render only.

create extension if not exists pg_cron;
create extension if not exists pg_net;

-- 15-minute processor: picks up rows where scheduled_deletion_at <= now().
select cron.schedule(
    'process-account-deletions',
    '*/15 * * * *',
    $cron$
        select net.http_post(
            url := '<RENDER_URL>/internal/cron/process-deletions',
            headers := jsonb_build_object(
                'Content-Type', 'application/json',
                'X-Cron-Secret', current_setting('app.cron_secret')
            ),
            body := '{}'::jsonb,
            timeout_milliseconds := 30000
        );
    $cron$
);

-- Nightly reconciliation: re-run delete_user_data for rows stuck >1h in clerk_called.
-- This is the safety net for missed Clerk webhooks.
select cron.schedule(
    'reconcile-stuck-deletions',
    '17 3 * * *',
    $cron$
        do $body$
        declare
            r record;
        begin
            for r in
                select user_id from public.account_deletion_requests
                where status = 'clerk_called'
                  and completed_at is null
                  and clerk_called_at < now() - interval '1 hour'
            loop
                perform public.delete_user_data(r.user_id);
            end loop;
        end $body$;
    $cron$
);
```

- [ ] **Step 1.5.2: Set the cron secret in DB config**

Generate a strong secret first:
```bash
python -c 'import secrets; print(secrets.token_urlsafe(48))'
```
Copy the output. In the Supabase dashboard SQL editor:
```sql
alter database postgres set app.cron_secret = 'PASTE_GENERATED_SECRET_HERE';
```

Verify:
```sql
show app.cron_secret;
```

**Save this same value** — it goes into Render as `CRON_SHARED_SECRET` in Phase 5.

- [ ] **Step 1.5.3: Substitute `<RENDER_URL>` and apply**

Edit a copy of the SQL with your actual Render URL, then run in dashboard. (Keep the file with `<RENDER_URL>` placeholder for git.)

Verify:
```sql
select jobname, schedule, active from cron.job
where jobname in ('process-account-deletions', 'reconcile-stuck-deletions');
```
Expected: 2 rows, both active.

- [ ] **Step 1.5.4: Commit**

```bash
git add supabase/migrations/20260429000100_account_deletion_cron.sql
git commit -m "feat(db): pg_cron schedule for deletion processor + reconciliation

15-min cron pings FastAPI to process due rows; nightly job runs
delete_user_data directly for any rows stuck in clerk_called > 1h
(missed webhook recovery)."
```

---

## Phase 2 — Token + email service layer (pure)

### Task 2.1 — Pydantic models for the deletion flow

**Files:**
- Modify: `app/models/schemas.py`

- [ ] **Step 2.1.1: Add models**

Append to `app/models/schemas.py`:

```python
from datetime import datetime
from enum import StrEnum


class DeletionRequestStatus(StrEnum):
    PENDING_CONFIRMATION = "pending_confirmation"
    SCHEDULED = "scheduled"
    CANCELLED = "cancelled"
    PROCESSING = "processing"
    CLERK_CALLED = "clerk_called"
    COMPLETED = "completed"
    FAILED = "failed"


class DeletionRequestRow(BaseModel):
    """Mirror of public.account_deletion_requests."""

    id: str
    user_id: str
    email: str
    status: DeletionRequestStatus
    scheduled_deletion_at: Optional[datetime] = None
    created_at: datetime
    confirmed_at: Optional[datetime] = None
    cancelled_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    failed_at: Optional[datetime] = None
    failure_reason: Optional[str] = None
    retry_count: int = 0


class DeletionStatusResponse(BaseModel):
    """GET /account/deletion/status — what the user's settings page reads."""

    status: DeletionRequestStatus | None  # None = no active request
    scheduled_deletion_at: Optional[datetime] = None
    can_cancel: bool
```

Note: existing imports already cover `BaseModel`, `Optional`. Add `datetime` and `StrEnum` imports at the top — keep stdlib group sorted.

- [ ] **Step 2.1.2: Commit**

```bash
git add app/models/schemas.py
git commit -m "feat(schemas): add deletion request models"
```

---

### Task 2.2 — `deletion_tokens.py` with TDD

**Files:**
- Create: `app/services/deletion_tokens.py`
- Create: `tests/test_deletion_tokens.py`

- [ ] **Step 2.2.1: Write failing tests**

Create `tests/test_deletion_tokens.py`:

```python
"""Unit tests for confirmation-token primitives."""

import hashlib

import pytest

from app.services.deletion_tokens import (
    compare_tokens,
    generate_token,
    hash_token,
)


class TestGenerateToken:
    def test_returns_raw_and_hash(self):
        raw, hashed = generate_token()
        assert isinstance(raw, str)
        assert isinstance(hashed, bytes)

    def test_raw_is_url_safe(self):
        raw, _ = generate_token()
        # secrets.token_urlsafe(32) yields ~43 chars, all url-safe alphabet
        assert len(raw) >= 43
        assert all(c.isalnum() or c in "-_" for c in raw)

    def test_hash_is_sha256(self):
        raw, hashed = generate_token()
        assert hashed == hashlib.sha256(raw.encode()).digest()
        assert len(hashed) == 32

    def test_two_tokens_are_unique(self):
        a, _ = generate_token()
        b, _ = generate_token()
        assert a != b


class TestHashToken:
    def test_deterministic(self):
        assert hash_token("abc") == hash_token("abc")

    def test_different_inputs_differ(self):
        assert hash_token("abc") != hash_token("abd")


class TestCompareTokens:
    def test_matching_tokens_return_true(self):
        raw, hashed = generate_token()
        assert compare_tokens(raw, hashed) is True

    def test_wrong_token_returns_false(self):
        _, hashed = generate_token()
        assert compare_tokens("not-the-right-token", hashed) is False

    def test_empty_token_returns_false(self):
        _, hashed = generate_token()
        assert compare_tokens("", hashed) is False
```

- [ ] **Step 2.2.2: Run tests, confirm failure**

```bash
pytest tests/test_deletion_tokens.py -v
```
Expected: ImportError (module does not exist).

- [ ] **Step 2.2.3: Implement the module**

Create `app/services/deletion_tokens.py`:

```python
"""Confirmation-token primitives for account deletion.

Tokens are 32 random bytes encoded url-safe (~43 chars). Only the SHA-256
hash is stored; the raw token lives in the email link.
"""

import hashlib
import hmac
import secrets


def generate_token() -> tuple[str, bytes]:
    """Return (raw, sha256_hash). The raw token goes in the email link;
    the hash is what we persist."""
    raw = secrets.token_urlsafe(32)
    return raw, hash_token(raw)


def hash_token(raw: str) -> bytes:
    return hashlib.sha256(raw.encode()).digest()


def compare_tokens(raw: str, stored_hash: bytes) -> bool:
    """Constant-time compare. Empty input must NOT short-circuit."""
    return hmac.compare_digest(hash_token(raw), stored_hash)
```

- [ ] **Step 2.2.4: Run tests, confirm pass**

```bash
pytest tests/test_deletion_tokens.py -v
```
Expected: 7 passed.

- [ ] **Step 2.2.5: Commit**

```bash
git add app/services/deletion_tokens.py tests/test_deletion_tokens.py
git commit -m "feat(deletion): token generate/hash/compare primitives

secrets.token_urlsafe(32) raw + sha256 hash. compare_tokens uses
hmac.compare_digest for constant-time verification."
```

---

### Task 2.3 — Resend wrappers for the three deletion emails

**Files:**
- Modify: `app/services/email_service.py`
- Modify: `app/db/client.py` (add template-id Settings fields)
- Create: `tests/test_email_service_deletion.py`

- [ ] **Step 2.3.1: Add template-id Settings fields**

Modify `app/db/client.py` `Settings` class — add inside the existing class body:

```python
    # Resend template IDs for the deletion lifecycle
    resend_template_deletion_confirm: str = ""
    resend_template_deletion_scheduled: str = ""
    resend_template_deletion_completed: str = ""
```

Empty defaults so unit tests don't need them set.

- [ ] **Step 2.3.2: Write failing tests**

Create `tests/test_email_service_deletion.py`:

```python
"""Tests for the deletion email sender wrappers.

We mock `resend.Emails.send` and assert payload structure. The real
network call is never made in tests.
"""

from datetime import date
from unittest.mock import patch

from app.services.email_service import (
    send_deletion_completed,
    send_deletion_confirmation,
    send_deletion_scheduled,
)


class TestSendDeletionConfirmation:
    def test_calls_resend_with_template_and_url(self):
        with patch("app.services.email_service.resend") as r, \
             patch("app.services.email_service.get_settings") as s:
            s.return_value.resend_api_key = "k"
            s.return_value.resend_from_email = "from@x"
            s.return_value.resend_template_deletion_confirm = "tmpl-confirm"
            r.Emails.send.return_value = {"id": "msg-1"}

            ok = send_deletion_confirmation(
                to="user@x", first_name="Ada", confirm_url="https://app/x"
            )

            assert ok is True
            payload = r.Emails.send.call_args[0][0]
            assert payload["to"] == ["user@x"]
            assert payload["template"]["id"] == "tmpl-confirm"
            assert payload["template"]["variables"]["USER"] == "Ada"
            assert payload["template"]["variables"]["CONFIRM_URL"] == "https://app/x"

    def test_returns_false_on_resend_exception(self):
        with patch("app.services.email_service.resend") as r, \
             patch("app.services.email_service.get_settings") as s:
            s.return_value.resend_api_key = "k"
            s.return_value.resend_from_email = "from@x"
            s.return_value.resend_template_deletion_confirm = "tmpl-confirm"
            r.Emails.send.side_effect = RuntimeError("resend down")

            ok = send_deletion_confirmation(
                to="user@x", first_name=None, confirm_url="https://app/x"
            )

            assert ok is False


class TestSendDeletionScheduled:
    def test_payload_includes_cancel_url_and_date(self):
        with patch("app.services.email_service.resend") as r, \
             patch("app.services.email_service.get_settings") as s:
            s.return_value.resend_api_key = "k"
            s.return_value.resend_from_email = "from@x"
            s.return_value.resend_template_deletion_scheduled = "tmpl-sched"
            r.Emails.send.return_value = {"id": "msg-2"}

            ok = send_deletion_scheduled(
                to="user@x",
                first_name="Ada",
                cancel_url="https://app/cancel",
                deletion_date=date(2026, 5, 29),
            )

            assert ok is True
            payload = r.Emails.send.call_args[0][0]
            vars_ = payload["template"]["variables"]
            assert vars_["CANCEL_URL"] == "https://app/cancel"
            assert vars_["DELETION_DATE"] == "2026-05-29"


class TestSendDeletionCompleted:
    def test_uses_completed_template(self):
        with patch("app.services.email_service.resend") as r, \
             patch("app.services.email_service.get_settings") as s:
            s.return_value.resend_api_key = "k"
            s.return_value.resend_from_email = "from@x"
            s.return_value.resend_template_deletion_completed = "tmpl-done"
            r.Emails.send.return_value = {"id": "msg-3"}

            ok = send_deletion_completed(to="user@x", first_name="Ada")

            assert ok is True
            assert r.Emails.send.call_args[0][0]["template"]["id"] == "tmpl-done"
```

- [ ] **Step 2.3.3: Run, confirm fail**

```bash
pytest tests/test_email_service_deletion.py -v
```
Expected: ImportError (the three send functions don't exist yet).

- [ ] **Step 2.3.4: Implement the three sender functions**

Append to `app/services/email_service.py`:

```python
from datetime import date


def send_deletion_confirmation(
    to: str,
    first_name: str | None,
    confirm_url: str,
) -> bool:
    settings = get_settings()
    resend.api_key = settings.resend_api_key
    logger.info("Sending deletion confirmation email to %s", to)
    try:
        response = resend.Emails.send({
            "from": settings.resend_from_email,
            "to": [to],
            "template": {
                "id": settings.resend_template_deletion_confirm,
                "variables": {
                    "USER": first_name,
                    "CONFIRM_URL": confirm_url,
                },
            },
        })
        logger.info("Resend accepted confirmation email to %s: %s", to, response)
        return True
    except Exception:
        logger.exception("Failed to send deletion confirmation to %s", to)
        return False


def send_deletion_scheduled(
    to: str,
    first_name: str | None,
    cancel_url: str,
    deletion_date: date,
) -> bool:
    settings = get_settings()
    resend.api_key = settings.resend_api_key
    logger.info("Sending deletion scheduled email to %s (date=%s)", to, deletion_date)
    try:
        response = resend.Emails.send({
            "from": settings.resend_from_email,
            "to": [to],
            "template": {
                "id": settings.resend_template_deletion_scheduled,
                "variables": {
                    "USER": first_name,
                    "CANCEL_URL": cancel_url,
                    "DELETION_DATE": deletion_date.isoformat(),
                },
            },
        })
        logger.info("Resend accepted scheduled email to %s: %s", to, response)
        return True
    except Exception:
        logger.exception("Failed to send deletion scheduled email to %s", to)
        return False


def send_deletion_completed(
    to: str,
    first_name: str | None,
) -> bool:
    settings = get_settings()
    resend.api_key = settings.resend_api_key
    logger.info("Sending deletion completed email to %s", to)
    try:
        response = resend.Emails.send({
            "from": settings.resend_from_email,
            "to": [to],
            "template": {
                "id": settings.resend_template_deletion_completed,
                "variables": {
                    "USER": first_name,
                },
            },
        })
        logger.info("Resend accepted completed email to %s: %s", to, response)
        return True
    except Exception:
        logger.exception("Failed to send deletion completed email to %s", to)
        return False
```

- [ ] **Step 2.3.5: Run, confirm pass**

```bash
pytest tests/test_email_service_deletion.py -v
```
Expected: 4 passed.

- [ ] **Step 2.3.6: Commit**

```bash
git add app/db/client.py app/services/email_service.py tests/test_email_service_deletion.py
git commit -m "feat(emails): deletion lifecycle senders (confirm/scheduled/completed)

Three Resend wrappers using template IDs from Settings. Empty default
template IDs so existing tests don't need them. Errors logged + return
False — caller decides whether to fail the request."
```

---

## Phase 3 — User-facing endpoints (Clerk JWT, RLS-respecting)

### Task 3.1 — Settings + service-role client + new env vars

**Files:**
- Modify: `app/db/client.py`
- Modify: `.env.example`

> The service-role client is needed in Phase 4 (webhook + cron) but lives in `db/client.py` per CLAUDE.md. We add it now while we're touching `Settings`.

- [ ] **Step 3.1.1: Add new Settings fields**

Modify `Settings` in `app/db/client.py` (place new fields at the bottom of the class):

```python
    # Account deletion feature
    clerk_secret_key: str = ""
    supabase_service_role_key: str = ""
    cron_shared_secret: str = ""
    app_base_url: str = ""
    frontend_base_url: str = ""
    account_deletion_enabled: bool = False
    app_env: str = "production"
```

Defaults are empty/false so existing test setup keeps working without these vars set.

- [ ] **Step 3.1.2: Add `build_service_role_client()`**

Below `build_user_client` in `app/db/client.py`:

```python
def build_service_role_client() -> Client:
    """Build a Supabase client authenticated with the service role.

    Bypasses RLS. Used ONLY in webhook + cron handlers — anywhere else,
    use build_user_client(jwt). Audit: a grep for this function name in
    the codebase must yield exactly two route files.
    """
    s = get_settings()
    if not s.supabase_service_role_key:
        raise RuntimeError(
            "SUPABASE_SERVICE_ROLE_KEY not set — service-role client unavailable"
        )
    return create_client(s.supabase_url, s.supabase_service_role_key)
```

- [ ] **Step 3.1.3: Update `.env.example`**

Append to `.env.example`:

```
# ── ACCOUNT DELETION ──────────────────────────────
# Clerk Backend API admin token (Clerk dashboard → API keys → Secret keys).
# Used ONLY to call DELETE /v1/users/{id} on day 30. Keep secret.
CLERK_SECRET_KEY=

# Service-role key (Supabase dashboard → API → service_role).
# Used ONLY in /webhooks/clerk and /internal/cron/* — bypasses RLS.
# Do NOT expose to the frontend.
SUPABASE_SERVICE_ROLE_KEY=

# Shared secret between pg_cron and FastAPI. Generate with:
#   python -c 'import secrets; print(secrets.token_urlsafe(48))'
# Mirror the same value in Postgres:
#   alter database postgres set app.cron_secret = '<value>';
CRON_SHARED_SECRET=

# Base URL of THIS FastAPI service (used to build confirm/cancel links).
APP_BASE_URL=https://insights-engine.onrender.com

# Base URL of the React frontend (used as redirect target after /confirm).
FRONTEND_BASE_URL=http://localhost:5173

# Feature flag: deletion endpoints return 503 when false. Default off so
# the schema can deploy ahead of the flow.
ACCOUNT_DELETION_ENABLED=false
```

Also rename the existing `SUPABASE_SERVICE_KEY=` line in `.env.example` (line 23) — replace it with `SUPABASE_SERVICE_ROLE_KEY=` since we're standardising on that name.

- [ ] **Step 3.1.4: Commit**

```bash
git add app/db/client.py .env.example
git commit -m "feat(config): settings + service-role client for account deletion

New Settings fields default empty/false so existing tests untouched.
build_service_role_client() bypasses RLS — must only be imported by
the two server-to-server route files (webhook + cron)."
```

---

### Task 3.2 — Deletion CRUD in `db/client.py`

**Files:**
- Modify: `app/db/client.py`
- Create: `tests/test_db_deletion.py` *(optional smoke test using FakeDB)*

- [ ] **Step 3.2.1: Add CRUD functions**

Append to `app/db/client.py`:

```python
from datetime import datetime
from uuid import UUID

from app.models.schemas import DeletionRequestRow, DeletionRequestStatus


def create_deletion_request(
    ctx: UserContext,
    email: str,
    token_hash: bytes,
    token_expires_at: datetime,
) -> DeletionRequestRow:
    """Insert a pending_confirmation row. Unique partial index will reject
    a second active row for the same user — caller catches and returns 409.
    """
    response = (
        ctx.db.table("account_deletion_requests")
        .insert(
            {
                "user_id": ctx.user_id,
                "email": email,
                "status": DeletionRequestStatus.PENDING_CONFIRMATION.value,
                "confirmation_token_hash": token_hash.hex(),
                "confirmation_token_expires_at": token_expires_at.isoformat(),
            }
        )
        .execute()
    )
    if not response.data:
        raise RuntimeError("insert returned no rows")
    return DeletionRequestRow(**response.data[0])


def fetch_active_deletion_request(
    ctx: UserContext,
) -> DeletionRequestRow | None:
    """Return the user's currently-active deletion request, if any.

    'Active' = anything except cancelled/completed/failed.
    """
    response = (
        ctx.db.table("account_deletion_requests")
        .select("*")
        .eq("user_id", ctx.user_id)
        .in_(
            "status",
            [
                DeletionRequestStatus.PENDING_CONFIRMATION.value,
                DeletionRequestStatus.SCHEDULED.value,
                DeletionRequestStatus.PROCESSING.value,
                DeletionRequestStatus.CLERK_CALLED.value,
            ],
        )
        .limit(1)
        .execute()
    )
    if not response.data:
        return None
    return DeletionRequestRow(**response.data[0])


def fetch_deletion_request_by_token_hash(
    service_db: Client,
    token_hash: bytes,
) -> DeletionRequestRow | None:
    """Lookup by token hash. Bypasses RLS (service role) because the user
    clicking the email link may not have a fresh JWT.
    """
    response = (
        service_db.table("account_deletion_requests")
        .select("*")
        .eq("confirmation_token_hash", token_hash.hex())
        .eq("status", DeletionRequestStatus.PENDING_CONFIRMATION.value)
        .limit(1)
        .execute()
    )
    if not response.data:
        return None
    return DeletionRequestRow(**response.data[0])


def confirm_deletion_request(
    service_db: Client,
    request_id: str,
    scheduled_at: datetime,
) -> None:
    """Move pending_confirmation → scheduled. Service-role because the
    request originated from an unauthenticated email link click.
    """
    service_db.table("account_deletion_requests").update(
        {
            "status": DeletionRequestStatus.SCHEDULED.value,
            "scheduled_deletion_at": scheduled_at.isoformat(),
            "confirmed_at": datetime.utcnow().isoformat(),
        }
    ).eq("id", request_id).execute()


def cancel_deletion_request(ctx: UserContext) -> bool:
    """User-initiated cancel. Returns True if a row was updated.

    RLS UPDATE policy enforces user_id match AND status='cancelled' write.
    """
    response = (
        ctx.db.table("account_deletion_requests")
        .update(
            {
                "status": DeletionRequestStatus.CANCELLED.value,
                "cancelled_at": datetime.utcnow().isoformat(),
            }
        )
        .eq("user_id", ctx.user_id)
        .in_(
            "status",
            [
                DeletionRequestStatus.PENDING_CONFIRMATION.value,
                DeletionRequestStatus.SCHEDULED.value,
            ],
        )
        .execute()
    )
    return bool(response.data)


def fetch_failed_deletion_for_user(
    ctx: UserContext,
) -> DeletionRequestRow | None:
    """Used by the account-locked guard in get_user_ctx."""
    response = (
        ctx.db.table("account_deletion_requests")
        .select("id, user_id, email, status, created_at, retry_count")
        .eq("user_id", ctx.user_id)
        .eq("status", DeletionRequestStatus.FAILED.value)
        .limit(1)
        .execute()
    )
    if not response.data:
        return None
    return DeletionRequestRow(**response.data[0])
```

- [ ] **Step 3.2.2: Extend `FakeDB` in `tests/conftest.py` to support insert/update/in_**

Modify `tests/conftest.py`:

```python
class FakeQuery:
    """Chainable no-op query; returns an object with `.data` when executed.

    Mirrors the subset of the supabase-py builder that db/client.py uses:
    select, eq, in_, gte, lte, limit, insert, update, execute.
    """

    def __init__(self, rows: list[dict], inserted: list[dict] | None = None):
        self._rows = rows
        self._inserted = inserted

    def select(self, *_a: object, **_kw: object) -> "FakeQuery":
        return self

    def eq(self, *_a: object, **_kw: object) -> "FakeQuery":
        return self

    def in_(self, *_a: object, **_kw: object) -> "FakeQuery":
        return self

    def gte(self, *_a: object, **_kw: object) -> "FakeQuery":
        return self

    def lte(self, *_a: object, **_kw: object) -> "FakeQuery":
        return self

    def limit(self, *_a: object, **_kw: object) -> "FakeQuery":
        return self

    def insert(self, payload: dict, *_a: object, **_kw: object) -> "FakeQuery":
        return FakeQuery([payload | {"id": "new-id"}])

    def update(self, payload: dict, *_a: object, **_kw: object) -> "FakeQuery":
        return FakeQuery([payload])

    def execute(self) -> object:
        class _Resp:
            data = self._rows

        return _Resp()
```

This extension is backward-compatible — nothing else in the suite uses `insert/update/in_`.

- [ ] **Step 3.2.3: Run full suite, confirm no regressions**

```bash
pytest -q
```
Expected: all existing tests pass.

- [ ] **Step 3.2.4: Commit**

```bash
git add app/db/client.py tests/conftest.py
git commit -m "feat(db): deletion request CRUD + FakeDB upgrades

create/fetch_active/fetch_by_token/confirm/cancel/fetch_failed.
Service-role overload for token-hash lookup and confirm — these are
called from the unauthenticated email link path."
```

---

### Task 3.3 — `deletion_service.py` orchestration

**Files:**
- Create: `app/services/deletion_service.py`
- Create: `tests/test_deletion_service.py`

- [ ] **Step 3.3.1: Write failing tests**

Create `tests/test_deletion_service.py`:

```python
"""Unit tests for the deletion service orchestration layer."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from app.context import UserContext
from app.models.schemas import DeletionRequestStatus
from app.services.deletion_service import (
    DeletionRequestExists,
    cancel_deletion,
    confirm_deletion,
    request_deletion,
)


def _ctx() -> UserContext:
    return UserContext(user_id="user-1", db=MagicMock())


class TestRequestDeletion:
    def test_creates_row_and_schedules_email(self):
        with patch("app.services.deletion_service.fetch_active_deletion_request",
                   return_value=None), \
             patch("app.services.deletion_service.create_deletion_request") \
             as create_req, \
             patch("app.services.deletion_service.send_deletion_confirmation",
                   return_value=True) as send_email, \
             patch("app.services.deletion_service.get_settings") as s:
            s.return_value.app_base_url = "https://api"
            create_req.return_value = MagicMock(id="req-1")

            request_deletion(_ctx(), email="a@b", first_name="Ada")

            assert create_req.called
            assert send_email.called
            confirm_url = send_email.call_args.kwargs["confirm_url"]
            assert confirm_url.startswith("https://api/account/deletion/confirm?token=")

    def test_rejects_when_active_request_exists(self):
        with patch(
            "app.services.deletion_service.fetch_active_deletion_request",
            return_value=MagicMock(),
        ):
            with pytest.raises(DeletionRequestExists):
                request_deletion(_ctx(), email="a@b", first_name=None)


class TestConfirmDeletion:
    def test_marks_scheduled_and_sends_email(self):
        existing = MagicMock(
            id="req-1",
            email="a@b",
            confirmation_token_expires_at=datetime.utcnow() + timedelta(hours=1),
            status=DeletionRequestStatus.PENDING_CONFIRMATION,
        )
        service_db = MagicMock()
        with patch(
            "app.services.deletion_service.fetch_deletion_request_by_token_hash",
            return_value=existing,
        ), patch(
            "app.services.deletion_service.confirm_deletion_request"
        ) as confirm_db, patch(
            "app.services.deletion_service.send_deletion_scheduled",
            return_value=True,
        ) as send_email, patch(
            "app.services.deletion_service.get_settings"
        ) as s:
            s.return_value.frontend_base_url = "https://app"
            s.return_value.app_base_url = "https://api"

            row = confirm_deletion(service_db, raw_token="raw")

            assert row.id == "req-1"
            assert confirm_db.called
            assert send_email.called

    def test_expired_token_raises(self):
        existing = MagicMock(
            id="req-1",
            confirmation_token_expires_at=datetime.utcnow() - timedelta(seconds=1),
            status=DeletionRequestStatus.PENDING_CONFIRMATION,
        )
        from app.services.deletion_service import TokenExpired

        with patch(
            "app.services.deletion_service.fetch_deletion_request_by_token_hash",
            return_value=existing,
        ):
            with pytest.raises(TokenExpired):
                confirm_deletion(MagicMock(), raw_token="raw")

    def test_unknown_token_raises(self):
        from app.services.deletion_service import TokenNotFound

        with patch(
            "app.services.deletion_service.fetch_deletion_request_by_token_hash",
            return_value=None,
        ):
            with pytest.raises(TokenNotFound):
                confirm_deletion(MagicMock(), raw_token="raw")


class TestCancelDeletion:
    def test_returns_true_when_row_updated(self):
        with patch(
            "app.services.deletion_service.cancel_deletion_request",
            return_value=True,
        ):
            assert cancel_deletion(_ctx()) is True

    def test_returns_false_when_no_active_row(self):
        with patch(
            "app.services.deletion_service.cancel_deletion_request",
            return_value=False,
        ):
            assert cancel_deletion(_ctx()) is False
```

- [ ] **Step 3.3.2: Run, confirm fail**

```bash
pytest tests/test_deletion_service.py -v
```
Expected: ImportError.

- [ ] **Step 3.3.3: Implement the service**

Create `app/services/deletion_service.py`:

```python
"""Orchestration of the account-deletion flow.

Routes call into here. This module does not query Supabase directly —
it delegates to functions in app.db.client.
"""

from datetime import datetime, timedelta

from supabase import Client

from app.context import UserContext
from app.db.client import (
    cancel_deletion_request,
    confirm_deletion_request,
    create_deletion_request,
    fetch_active_deletion_request,
    fetch_deletion_request_by_token_hash,
    get_settings,
)
from app.models.schemas import DeletionRequestRow
from app.services.deletion_tokens import generate_token, hash_token
from app.services.email_service import (
    send_deletion_confirmation,
    send_deletion_scheduled,
)


GRACE_PERIOD_DAYS = 30
TOKEN_TTL_HOURS = 1


class DeletionRequestExists(Exception):
    """User already has an active deletion request."""


class TokenNotFound(Exception):
    """No pending request matches the supplied token."""


class TokenExpired(Exception):
    """Token matched but is past confirmation_token_expires_at."""


def request_deletion(
    ctx: UserContext,
    email: str,
    first_name: str | None,
) -> DeletionRequestRow:
    if fetch_active_deletion_request(ctx) is not None:
        raise DeletionRequestExists()

    raw, hashed = generate_token()
    expires_at = datetime.utcnow() + timedelta(hours=TOKEN_TTL_HOURS)
    row = create_deletion_request(
        ctx, email=email, token_hash=hashed, token_expires_at=expires_at
    )

    settings = get_settings()
    confirm_url = f"{settings.app_base_url}/account/deletion/confirm?token={raw}"
    send_deletion_confirmation(
        to=email, first_name=first_name, confirm_url=confirm_url
    )
    return row


def confirm_deletion(
    service_db: Client,
    raw_token: str,
) -> DeletionRequestRow:
    hashed = hash_token(raw_token)
    row = fetch_deletion_request_by_token_hash(service_db, hashed)
    if row is None:
        raise TokenNotFound()
    if row.confirmation_token_expires_at and \
            row.confirmation_token_expires_at < datetime.utcnow():
        raise TokenExpired()

    scheduled_at = datetime.utcnow() + timedelta(days=GRACE_PERIOD_DAYS)
    confirm_deletion_request(service_db, row.id, scheduled_at)

    settings = get_settings()
    cancel_url = f"{settings.frontend_base_url}/settings#cancel-deletion"
    send_deletion_scheduled(
        to=row.email,
        first_name=None,
        cancel_url=cancel_url,
        deletion_date=scheduled_at.date(),
    )
    return row


def cancel_deletion(ctx: UserContext) -> bool:
    return cancel_deletion_request(ctx)
```

- [ ] **Step 3.3.4: Run, confirm pass**

```bash
pytest tests/test_deletion_service.py -v
```
Expected: 7 passed.

- [ ] **Step 3.3.5: Commit**

```bash
git add app/services/deletion_service.py tests/test_deletion_service.py
git commit -m "feat(deletion): service-layer orchestration of request/confirm/cancel

DeletionRequestExists / TokenNotFound / TokenExpired carry intent to
the route. 30-day grace + 1h token TTL are constants here, not config."
```

---

### Task 3.4 — `account_deletion.py` routes (request, confirm, cancel, status)

**Files:**
- Create: `app/routes/account_deletion.py`
- Create: `tests/test_account_deletion_routes.py`

- [ ] **Step 3.4.1: Write failing route tests**

Create `tests/test_account_deletion_routes.py`:

```python
"""Integration tests for /account/deletion/* endpoints.

Auth is bypassed via dependency_overrides[get_user_ctx]. Service calls
are stubbed at the boundary.
"""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routes.deps import get_user_ctx
from tests.conftest import make_user_ctx


@pytest.fixture(autouse=True)
def _enable_feature():
    with patch("app.routes.account_deletion.get_settings") as s:
        s.return_value.account_deletion_enabled = True
        s.return_value.app_base_url = "https://api"
        s.return_value.frontend_base_url = "https://app"
        yield s.return_value


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_overrides():
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def _override_user(user_id: str = "user-1"):
    app.dependency_overrides[get_user_ctx] = lambda: make_user_ctx(user_id=user_id)


class TestRequestDeletion:
    def test_returns_202(self, client):
        _override_user()
        with patch("app.routes.account_deletion.request_deletion") as svc:
            svc.return_value = MagicMock()
            r = client.post(
                "/account/deletion/request",
                json={"email": "u@x", "first_name": "Ada"},
            )
        assert r.status_code == 202

    def test_409_when_already_requested(self, client):
        _override_user()
        from app.services.deletion_service import DeletionRequestExists
        with patch("app.routes.account_deletion.request_deletion",
                   side_effect=DeletionRequestExists()):
            r = client.post(
                "/account/deletion/request",
                json={"email": "u@x", "first_name": "Ada"},
            )
        assert r.status_code == 409


class TestConfirmDeletion:
    def test_redirects_to_frontend_on_success(self, client):
        with patch("app.routes.account_deletion.confirm_deletion") as svc, \
             patch("app.routes.account_deletion.build_service_role_client",
                   return_value=MagicMock()):
            svc.return_value = MagicMock()
            r = client.get(
                "/account/deletion/confirm?token=abc", follow_redirects=False
            )
        assert r.status_code in (302, 303)
        assert "https://app" in r.headers["location"]

    def test_404_on_unknown_token(self, client):
        from app.services.deletion_service import TokenNotFound
        with patch("app.routes.account_deletion.confirm_deletion",
                   side_effect=TokenNotFound()), \
             patch("app.routes.account_deletion.build_service_role_client",
                   return_value=MagicMock()):
            r = client.get(
                "/account/deletion/confirm?token=abc", follow_redirects=False
            )
        assert r.status_code == 404

    def test_410_on_expired_token(self, client):
        from app.services.deletion_service import TokenExpired
        with patch("app.routes.account_deletion.confirm_deletion",
                   side_effect=TokenExpired()), \
             patch("app.routes.account_deletion.build_service_role_client",
                   return_value=MagicMock()):
            r = client.get(
                "/account/deletion/confirm?token=abc", follow_redirects=False
            )
        assert r.status_code == 410


class TestCancelDeletion:
    def test_200_on_cancel(self, client):
        _override_user()
        with patch("app.routes.account_deletion.cancel_deletion",
                   return_value=True):
            r = client.post("/account/deletion/cancel")
        assert r.status_code == 200

    def test_404_when_no_active_row(self, client):
        _override_user()
        with patch("app.routes.account_deletion.cancel_deletion",
                   return_value=False):
            r = client.post("/account/deletion/cancel")
        assert r.status_code == 404


class TestDeletionStatus:
    def test_returns_null_when_no_request(self, client):
        _override_user()
        with patch("app.routes.account_deletion.fetch_active_deletion_request",
                   return_value=None):
            r = client.get("/account/deletion/status")
        assert r.status_code == 200
        assert r.json()["status"] is None
        assert r.json()["can_cancel"] is False

    def test_returns_status_when_scheduled(self, client):
        _override_user()
        from datetime import datetime
        from app.models.schemas import DeletionRequestStatus
        row = MagicMock()
        row.status = DeletionRequestStatus.SCHEDULED
        row.scheduled_deletion_at = datetime(2026, 5, 29)
        with patch("app.routes.account_deletion.fetch_active_deletion_request",
                   return_value=row):
            r = client.get("/account/deletion/status")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "scheduled"
        assert body["can_cancel"] is True
```

- [ ] **Step 3.4.2: Run, confirm fail**

```bash
pytest tests/test_account_deletion_routes.py -v
```
Expected: ImportError on `app.routes.account_deletion`.

- [ ] **Step 3.4.3: Implement the routes**

Create `app/routes/account_deletion.py`:

```python
"""User-facing account deletion endpoints.

POST /account/deletion/request   — Clerk JWT
GET  /account/deletion/confirm   — token only (no JWT)
POST /account/deletion/cancel    — Clerk JWT
GET  /account/deletion/status    — Clerk JWT
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, EmailStr

from app.context import UserContext
from app.db.client import (
    build_service_role_client,
    fetch_active_deletion_request,
    get_settings,
)
from app.models.schemas import DeletionRequestStatus, DeletionStatusResponse
from app.routes.deps import get_user_ctx
from app.services.deletion_service import (
    DeletionRequestExists,
    TokenExpired,
    TokenNotFound,
    cancel_deletion,
    confirm_deletion,
    request_deletion,
)

router = APIRouter(prefix="/account/deletion", tags=["account-deletion"])
logger = logging.getLogger(__name__)


def _require_feature_enabled() -> None:
    if not get_settings().account_deletion_enabled:
        raise HTTPException(
            status_code=503, detail="account deletion not yet enabled"
        )


class RequestDeletionBody(BaseModel):
    email: EmailStr
    first_name: str | None = None


@router.post("/request", status_code=202)
def post_request(
    body: RequestDeletionBody,
    ctx: Annotated[UserContext, Depends(get_user_ctx)],
) -> dict:
    _require_feature_enabled()
    try:
        request_deletion(ctx, email=body.email, first_name=body.first_name)
    except DeletionRequestExists:
        raise HTTPException(
            status_code=409, detail="active deletion request already exists"
        ) from None
    return {"status": "pending_confirmation"}


@router.get("/confirm")
def get_confirm(token: Annotated[str, Query(min_length=20, max_length=200)]) -> RedirectResponse:
    _require_feature_enabled()
    settings = get_settings()
    service_db = build_service_role_client()
    try:
        confirm_deletion(service_db, raw_token=token)
    except TokenNotFound:
        raise HTTPException(status_code=404, detail="token not found") from None
    except TokenExpired:
        raise HTTPException(status_code=410, detail="token expired") from None
    return RedirectResponse(
        url=f"{settings.frontend_base_url}/settings/deletion-confirmed",
        status_code=303,
    )


@router.post("/cancel")
def post_cancel(
    ctx: Annotated[UserContext, Depends(get_user_ctx)],
) -> dict:
    _require_feature_enabled()
    if not cancel_deletion(ctx):
        raise HTTPException(
            status_code=404, detail="no active deletion request to cancel"
        )
    return {"status": "cancelled"}


@router.get("/status", response_model=DeletionStatusResponse)
def get_status(
    ctx: Annotated[UserContext, Depends(get_user_ctx)],
) -> DeletionStatusResponse:
    _require_feature_enabled()
    row = fetch_active_deletion_request(ctx)
    if row is None:
        return DeletionStatusResponse(status=None, can_cancel=False)
    return DeletionStatusResponse(
        status=row.status,
        scheduled_deletion_at=row.scheduled_deletion_at,
        can_cancel=row.status in (
            DeletionRequestStatus.PENDING_CONFIRMATION,
            DeletionRequestStatus.SCHEDULED,
        ),
    )
```

- [ ] **Step 3.4.4: Register router in `app/main.py`**

In `app/main.py`, add to the imports:

```python
from app.routes import account_deletion as account_deletion_routes
```

And below the existing `include_router` calls:

```python
app.include_router(account_deletion_routes.router)
```

- [ ] **Step 3.4.5: Run, confirm pass**

```bash
pytest tests/test_account_deletion_routes.py -v
```
Expected: 9 passed.

- [ ] **Step 3.4.6: Commit**

```bash
git add app/routes/account_deletion.py app/main.py tests/test_account_deletion_routes.py
git commit -m "feat(routes): user-facing account deletion endpoints

POST /request (202), GET /confirm (303 redirect / 404 / 410),
POST /cancel (200/404), GET /status. All gated by
ACCOUNT_DELETION_ENABLED — endpoints 503 when off."
```

---

### Task 3.5 — Account-locked guard in `get_user_ctx`

**Files:**
- Modify: `app/routes/deps.py`
- Create: `tests/test_account_lock.py`

- [ ] **Step 3.5.1: Write failing test**

Create `tests/test_account_lock.py`:

```python
"""When a user's deletion request is in 'failed' state, all authenticated
endpoints reject with 423 Locked. The check happens in get_user_ctx."""

from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from app.routes.deps import get_user_ctx


class _Creds:
    def __init__(self, token: str) -> None:
        self.credentials = token


@pytest.fixture(autouse=True)
def _patch_jwt(jwt_secret):
    """JWKS verification short-circuits to a known sub."""
    with patch("app.routes.deps.get_jwks_client") as jwks, \
         patch("app.routes.deps.jwt.decode") as decode, \
         patch("app.routes.deps.build_user_client") as build:
        jwks.return_value.get_signing_key_from_jwt.return_value.key = "k"
        decode.return_value = {"sub": "user-locked", "exp": 9999999999}
        build.return_value = MagicMock()
        yield


def test_failed_status_raises_423():
    with patch(
        "app.routes.deps.fetch_failed_deletion_for_user",
        return_value=MagicMock(status="failed"),
    ):
        with pytest.raises(HTTPException) as exc:
            get_user_ctx(_Creds("token"))
    assert exc.value.status_code == 423


def test_no_failed_row_returns_ctx():
    with patch(
        "app.routes.deps.fetch_failed_deletion_for_user",
        return_value=None,
    ):
        ctx = get_user_ctx(_Creds("token"))
    assert ctx.user_id == "user-locked"
```

- [ ] **Step 3.5.2: Run, confirm fail**

```bash
pytest tests/test_account_lock.py -v
```
Expected: ImportError on `fetch_failed_deletion_for_user`, OR test fails because guard not yet inserted.

- [ ] **Step 3.5.3: Add the guard**

Modify `app/routes/deps.py`. After the `return UserContext(...)` line, restructure:

```python
import logging
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth.jwks import get_jwks_client
from app.context import UserContext
from app.db.client import (
    build_user_client,
    fetch_failed_deletion_for_user,
    get_settings,
)

logger = logging.getLogger(__name__)


bearer_scheme = HTTPBearer(
    auto_error=True,
    scheme_name="ClerkBearer",
    description=(
        "Clerk-issued JWT (Third-Party Auth, RS256, verified against Clerk's JWKS). "
        "Paste the token only — Swagger prepends 'Bearer '. "
        "Clerk tokens typically live ~60s; refresh from the frontend if it expires."
    ),
)


def get_user_ctx(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(bearer_scheme)],
) -> UserContext:
    settings = get_settings()
    token = credentials.credentials
    try:
        signing_key = get_jwks_client().get_signing_key_from_jwt(token).key
        payload = jwt.decode(
            token,
            signing_key,
            algorithms=["RS256"],
            audience="authenticated",
            issuer=settings.clerk_issuer,
            leeway=5,
            options={"require": ["exp", "sub", "aud", "iss"]},
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="token expired") from None
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="invalid token") from None
    except jwt.InvalidIssuerError:
        raise HTTPException(status_code=401, detail="invalid token issuer") from None
    except Exception:
        raise HTTPException(status_code=401, detail="unable to verify token") from None

    user_id = payload["sub"]
    if not user_id:
        raise HTTPException(status_code=401, detail="invalid token")
    ctx = UserContext(user_id=user_id, db=build_user_client(token))

    # Account-locked guard: deletion in 'failed' state blocks all access.
    failed = fetch_failed_deletion_for_user(ctx)
    if failed is not None:
        logger.warning(
            "Locked account access denied: user_id=%s status=%s",
            user_id[-4:],
            failed.status,
        )
        raise HTTPException(
            status_code=423,
            detail={
                "error": "account_locked",
                "message": "Account is locked due to a failed deletion. Contact support.",
            },
        )

    return ctx
```

- [ ] **Step 3.5.4: Run new + existing deps tests**

```bash
pytest tests/test_account_lock.py tests/test_deps.py -v
```
Expected: new tests pass; `test_deps.py` may already be broken on main (it patches `supabase_jwt_secret` which doesn't exist on `Settings`). **If `test_deps.py` was failing before this task, it stays failing — out of scope to fix here.** Otherwise it should still pass.

- [ ] **Step 3.5.5: Commit**

```bash
git add app/routes/deps.py tests/test_account_lock.py
git commit -m "feat(deps): account-locked guard returns 423 on failed deletion

After JWT verification, query the user's RLS-scoped client for any
account_deletion_requests row with status='failed'. Returns 423 with
account_locked detail. +1 indexed query per authenticated request."
```

---

## Phase 4 — Server-to-server endpoints (Svix + shared secret)

### Task 4.1 — `clerk_admin.py` with retry

**Files:**
- Create: `app/services/clerk_admin.py`
- Create: `tests/test_clerk_admin.py`

- [ ] **Step 4.1.1: Write failing tests**

Create `tests/test_clerk_admin.py`:

```python
"""Tests for the Clerk Backend API admin client.

Mocks httpx — no network calls. Asserts retry behaviour on 5xx and
correct header/URL construction.
"""

from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.services.clerk_admin import ClerkAPIError, delete_user


class _Resp:
    def __init__(self, status_code: int, text: str = ""):
        self.status_code = status_code
        self.text = text


@pytest.fixture(autouse=True)
def _settings():
    with patch("app.services.clerk_admin.get_settings") as s:
        s.return_value.clerk_secret_key = "sk_test_x"
        yield


class TestDeleteUser:
    def test_204_succeeds_first_try(self):
        with patch("app.services.clerk_admin.httpx.Client") as ClientCls:
            inst = ClientCls.return_value.__enter__.return_value
            inst.delete.return_value = _Resp(204)
            delete_user("user_abc")
        # Single call, no retries
        assert inst.delete.call_count == 1
        url = inst.delete.call_args[0][0]
        headers = inst.delete.call_args.kwargs["headers"]
        assert url == "https://api.clerk.com/v1/users/user_abc"
        assert headers["Authorization"] == "Bearer sk_test_x"

    def test_404_treated_as_already_deleted(self):
        with patch("app.services.clerk_admin.httpx.Client") as ClientCls:
            inst = ClientCls.return_value.__enter__.return_value
            inst.delete.return_value = _Resp(404)
            delete_user("user_abc")  # idempotent — no exception
        assert inst.delete.call_count == 1

    def test_5xx_retries_three_times_then_raises(self):
        with patch("app.services.clerk_admin.httpx.Client") as ClientCls, \
             patch("app.services.clerk_admin.time.sleep"):
            inst = ClientCls.return_value.__enter__.return_value
            inst.delete.return_value = _Resp(503, "down")
            with pytest.raises(ClerkAPIError):
                delete_user("user_abc")
        assert inst.delete.call_count == 3

    def test_4xx_other_than_404_raises_immediately(self):
        with patch("app.services.clerk_admin.httpx.Client") as ClientCls:
            inst = ClientCls.return_value.__enter__.return_value
            inst.delete.return_value = _Resp(400, "bad")
            with pytest.raises(ClerkAPIError):
                delete_user("user_abc")
        assert inst.delete.call_count == 1

    def test_network_error_retries(self):
        with patch("app.services.clerk_admin.httpx.Client") as ClientCls, \
             patch("app.services.clerk_admin.time.sleep"):
            inst = ClientCls.return_value.__enter__.return_value
            inst.delete.side_effect = httpx.ConnectError("nope")
            with pytest.raises(ClerkAPIError):
                delete_user("user_abc")
        assert inst.delete.call_count == 3
```

- [ ] **Step 4.1.2: Run, confirm fail**

```bash
pytest tests/test_clerk_admin.py -v
```
Expected: ImportError.

- [ ] **Step 4.1.3: Implement the client**

Create `app/services/clerk_admin.py`:

```python
"""Clerk Backend API admin client.

Wraps the single endpoint we call: DELETE /v1/users/{id}. Uses httpx,
3 retries with exponential backoff on 5xx and network errors. 404 is
treated as success (already deleted).
"""

import logging
import time

import httpx

from app.db.client import get_settings

logger = logging.getLogger(__name__)

CLERK_API_BASE = "https://api.clerk.com/v1"
MAX_RETRIES = 3
BASE_BACKOFF_SECONDS = 1.0


class ClerkAPIError(Exception):
    """Non-recoverable error after retry budget exhausted."""


def delete_user(clerk_user_id: str) -> None:
    """Delete a Clerk user. Idempotent: 404 is success.

    Retries on 5xx and network errors with exponential backoff
    (1s, 2s). After 3 attempts, raises ClerkAPIError.
    """
    settings = get_settings()
    headers = {
        "Authorization": f"Bearer {settings.clerk_secret_key}",
        "Content-Type": "application/json",
    }
    url = f"{CLERK_API_BASE}/users/{clerk_user_id}"

    last_error: str | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with httpx.Client(timeout=15.0) as client:
                response = client.delete(url, headers=headers)
            if response.status_code in (200, 204):
                logger.info(
                    "Clerk delete OK user=%s attempt=%d",
                    clerk_user_id[-4:],
                    attempt,
                )
                return
            if response.status_code == 404:
                logger.info(
                    "Clerk delete: user already absent user=%s",
                    clerk_user_id[-4:],
                )
                return
            if response.status_code >= 500:
                last_error = f"HTTP {response.status_code}: {response.text[:200]}"
                logger.warning(
                    "Clerk 5xx, will retry: user=%s attempt=%d %s",
                    clerk_user_id[-4:],
                    attempt,
                    last_error,
                )
            else:
                # 4xx other than 404 — non-retryable
                raise ClerkAPIError(
                    f"Clerk DELETE returned {response.status_code}: "
                    f"{response.text[:200]}"
                )
        except httpx.HTTPError as e:
            last_error = repr(e)
            logger.warning(
                "Clerk network error user=%s attempt=%d %s",
                clerk_user_id[-4:],
                attempt,
                last_error,
            )

        if attempt < MAX_RETRIES:
            time.sleep(BASE_BACKOFF_SECONDS * (2 ** (attempt - 1)))

    raise ClerkAPIError(
        f"Clerk DELETE failed after {MAX_RETRIES} attempts: {last_error}"
    )
```

- [ ] **Step 4.1.4: Run, confirm pass**

```bash
pytest tests/test_clerk_admin.py -v
```
Expected: 5 passed.

- [ ] **Step 4.1.5: Commit**

```bash
git add app/services/clerk_admin.py tests/test_clerk_admin.py
git commit -m "feat(clerk): admin DELETE /v1/users client with retry

3 attempts, 1s+2s backoff, 5xx and network errors retry, 4xx (except
404, treated as already-deleted) raises immediately. Logs only the
last 4 chars of user_id."
```

---

### Task 4.2 — `webhooks_clerk.py` with idempotency, dispatching by event type

**Files:**
- Create: `app/routes/webhooks_clerk.py`
- Modify: `app/db/client.py` (add `record_webhook_event` helper)
- Create: `tests/test_webhooks_clerk.py`

- [ ] **Step 4.2.0: Add idempotency RPC to the migration**

Append to `supabase/migrations/20260429000000_account_deletion.sql`:

```sql
-- Idempotency RPC: returns true if the svix_id is new, false if already seen.
-- Cleaner than catching unique-violation exceptions on the Python side.
create or replace function public.record_webhook_event(p_svix_id text)
returns boolean
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
    insert into public.webhook_events (svix_id) values (p_svix_id);
    return true;
exception when unique_violation then
    return false;
end;
$$;

revoke all on function public.record_webhook_event(text) from public;
grant execute on function public.record_webhook_event(text) to service_role;
```

Apply this in the Supabase dashboard SQL editor. Verify:
```sql
select public.record_webhook_event('test-msg-1');  -- expect: true
select public.record_webhook_event('test-msg-1');  -- expect: false
delete from public.webhook_events where svix_id = 'test-msg-1';
```

- [ ] **Step 4.2.1: Add idempotency helper to `db/client.py`**

Append to `app/db/client.py`:

```python
def record_webhook_event(service_db: Client, svix_id: str) -> bool:
    """Insert svix_id; return True if newly inserted, False if already seen.

    Wraps the SQL function `record_webhook_event(text)` which handles the
    unique-violation case server-side — avoids brittle exception-string
    matching on the supabase-py client.
    """
    response = service_db.rpc(
        "record_webhook_event", {"p_svix_id": svix_id}
    ).execute()
    return bool(response.data)
```

- [ ] **Step 4.2.2: Write failing webhook tests**

Create `tests/test_webhooks_clerk.py`:

```python
"""Tests for /webhooks/clerk.

Svix verification is patched. We assert dispatch by event type +
idempotency behaviour.
"""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def _settings():
    with patch("app.routes.webhooks_clerk.get_settings") as s:
        s.return_value.clerk_webhook_secret = "whsec_test"
        yield s


def _headers(svix_id: str = "msg_1") -> dict:
    return {
        "svix-id": svix_id,
        "svix-timestamp": "1700000000",
        "svix-signature": "v1,sig",
    }


@pytest.fixture
def _patch_svix_ok():
    with patch("app.routes.webhooks_clerk.Webhook") as W:
        W.return_value.verify.return_value = None
        yield W


@pytest.fixture
def _patch_service_db():
    with patch("app.routes.webhooks_clerk.build_service_role_client") as b:
        b.return_value = MagicMock()
        yield b


class TestSignatureVerification:
    def test_bad_signature_returns_400(self, client):
        from svix.webhooks import WebhookVerificationError
        with patch("app.routes.webhooks_clerk.Webhook") as W:
            W.return_value.verify.side_effect = WebhookVerificationError()
            r = client.post(
                "/webhooks/clerk",
                json={"type": "user.created", "data": {"id": "u_1"}},
                headers=_headers(),
            )
        assert r.status_code == 400


class TestUserCreatedDispatch:
    def test_calls_welcome_email(
        self, client, _patch_svix_ok, _patch_service_db
    ):
        with patch("app.routes.webhooks_clerk.record_webhook_event",
                   return_value=True), \
             patch("app.routes.webhooks_clerk.send_welcome_email") as send:
            r = client.post(
                "/webhooks/clerk",
                json={
                    "type": "user.created",
                    "data": {
                        "id": "u_1",
                        "first_name": "Ada",
                        "primary_email_address_id": "e_1",
                        "email_addresses": [
                            {"id": "e_1", "email_address": "a@b"}
                        ],
                    },
                },
                headers=_headers(),
            )
        assert r.status_code == 200
        assert send.called


class TestUserDeletedDispatch:
    def test_calls_delete_user_data_rpc_and_email(
        self, client, _patch_svix_ok, _patch_service_db
    ):
        service_db = _patch_service_db.return_value
        rpc_call = MagicMock()
        service_db.rpc.return_value.execute.return_value = rpc_call
        with patch("app.routes.webhooks_clerk.record_webhook_event",
                   return_value=True), \
             patch("app.routes.webhooks_clerk.send_deletion_completed") as send:
            r = client.post(
                "/webhooks/clerk",
                json={
                    "type": "user.deleted",
                    "data": {
                        "id": "u_1",
                        "first_name": "Ada",
                        "primary_email_address_id": "e_1",
                        "email_addresses": [
                            {"id": "e_1", "email_address": "a@b"}
                        ],
                    },
                },
                headers=_headers(),
            )
        assert r.status_code == 200
        service_db.rpc.assert_called_once_with(
            "delete_user_data", {"p_clerk_user_id": "u_1"}
        )
        assert send.called


class TestIdempotency:
    def test_replay_short_circuits(
        self, client, _patch_svix_ok, _patch_service_db
    ):
        with patch("app.routes.webhooks_clerk.record_webhook_event",
                   return_value=False), \
             patch("app.routes.webhooks_clerk.send_welcome_email") as send:
            r = client.post(
                "/webhooks/clerk",
                json={"type": "user.created", "data": {"id": "u_1"}},
                headers=_headers(),
            )
        assert r.status_code == 200
        assert not send.called  # short-circuited
```

- [ ] **Step 4.2.3: Run, confirm fail**

```bash
pytest tests/test_webhooks_clerk.py -v
```
Expected: ImportError.

- [ ] **Step 4.2.4: Implement the webhook**

Create `app/routes/webhooks_clerk.py`:

```python
"""Clerk webhook handler. Single URL, dispatches by event.type.

Svix-signature verified. Idempotency via webhook_events.svix_id.
user.created  → welcome email.
user.deleted  → delete_user_data RPC + completion email.
"""

import json
import logging
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from svix.webhooks import Webhook, WebhookVerificationError

from app.db.client import (
    Settings,
    build_service_role_client,
    get_settings,
    record_webhook_event,
)
from app.services.email_service import (
    send_deletion_completed,
    send_welcome_email,
)

router = APIRouter()
logger = logging.getLogger(__name__)


def _primary_email(data: dict) -> str | None:
    primary_id = data.get("primary_email_address_id")
    return next(
        (
            e["email_address"]
            for e in data.get("email_addresses", [])
            if e.get("id") == primary_id
        ),
        None,
    )


@router.post("/webhooks/clerk")
async def clerk_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    settings: Annotated[Settings, Depends(get_settings)],
):
    payload = await request.body()
    headers = {
        "svix-id": request.headers.get("svix-id"),
        "svix-timestamp": request.headers.get("svix-timestamp"),
        "svix-signature": request.headers.get("svix-signature"),
    }
    try:
        Webhook(settings.clerk_webhook_secret).verify(payload, headers)
    except WebhookVerificationError:
        raise HTTPException(status_code=400, detail="Invalid signature") from None

    svix_id = headers["svix-id"] or ""
    if not svix_id:
        raise HTTPException(status_code=400, detail="missing svix-id")

    service_db = build_service_role_client()
    if not record_webhook_event(service_db, svix_id):
        logger.info("Replay svix_id=%s ignored", svix_id)
        return {"status": "ok", "replay": True}

    event = json.loads(payload)
    event_type = event.get("type")
    data = event.get("data", {})
    user_id = data.get("id")
    email = _primary_email(data)
    first_name = data.get("first_name")

    logger.info(
        "Clerk webhook received: type=%s user=%s",
        event_type,
        (user_id or "")[-4:],
    )

    if event_type == "user.created":
        if email:
            background_tasks.add_task(send_welcome_email, email, first_name)
        else:
            logger.warning("user.created without primary email user=%s",
                           (user_id or "")[-4:])
    elif event_type == "user.deleted" and user_id:
        # Run destructive SQL synchronously — webhook idempotency is the
        # safety net. If this raises, Svix will retry and we're protected
        # by both webhook_events and delete_user_data idempotency.
        service_db.rpc(
            "delete_user_data", {"p_clerk_user_id": user_id}
        ).execute()
        if email:
            background_tasks.add_task(
                send_deletion_completed, email, first_name
            )

    return {"status": "ok"}
```

- [ ] **Step 4.2.5: Register router in `main.py` (don't remove emails router yet — done in 4.5)**

In `app/main.py` add:

```python
from app.routes import webhooks_clerk as webhooks_clerk_routes
```

And:

```python
app.include_router(webhooks_clerk_routes.router)
```

Keep the `emails_routes` line for now — old endpoints stay live until Phase 4.5.

- [ ] **Step 4.2.6: Run, confirm pass**

```bash
pytest tests/test_webhooks_clerk.py -v
```
Expected: 4 passed.

- [ ] **Step 4.2.7: Commit**

```bash
git add app/routes/webhooks_clerk.py app/db/client.py app/main.py tests/test_webhooks_clerk.py
git commit -m "feat(webhooks): unified /webhooks/clerk with idempotency

Svix verify, webhook_events svix_id idempotency, dispatch by
event.type. user.created → welcome email; user.deleted → RPC into
delete_user_data + completion email. Old /emails/* still live until
Phase 4.5 cutover."
```

---

### Task 4.3 — `internal_cron.py` for `/internal/cron/process-deletions`

**Files:**
- Create: `app/routes/internal_cron.py`
- Modify: `app/db/client.py` (add `fetch_due_deletions` + status update helpers)
- Create: `tests/test_internal_cron.py`

- [ ] **Step 4.3.1: Add db helpers**

Append to `app/db/client.py`:

```python
def fetch_due_deletions(
    service_db: Client, limit: int = 50
) -> list[DeletionRequestRow]:
    """Return rows where status='scheduled' AND scheduled_deletion_at <= now().

    Note: we do NOT use FOR UPDATE SKIP LOCKED via PostgREST (not exposed).
    Race protection comes from the conditional UPDATE in
    mark_deletion_processing — which atomically transitions scheduled →
    processing only for rows still in scheduled.
    """
    response = (
        service_db.table("account_deletion_requests")
        .select("*")
        .eq("status", DeletionRequestStatus.SCHEDULED.value)
        .lte("scheduled_deletion_at", datetime.utcnow().isoformat())
        .limit(limit)
        .execute()
    )
    return [DeletionRequestRow(**row) for row in response.data]


def mark_deletion_processing(
    service_db: Client, request_id: str
) -> bool:
    """Atomically claim a row: scheduled → processing.

    Returns True iff the row was claimed (no other worker grabbed it).
    """
    response = (
        service_db.table("account_deletion_requests")
        .update({"status": DeletionRequestStatus.PROCESSING.value})
        .eq("id", request_id)
        .eq("status", DeletionRequestStatus.SCHEDULED.value)
        .execute()
    )
    return bool(response.data)


def mark_deletion_clerk_called(
    service_db: Client, request_id: str
) -> None:
    service_db.table("account_deletion_requests").update(
        {
            "status": DeletionRequestStatus.CLERK_CALLED.value,
            "clerk_called_at": datetime.utcnow().isoformat(),
        }
    ).eq("id", request_id).execute()


def mark_deletion_failed(
    service_db: Client, request_id: str, reason: str
) -> None:
    service_db.table("account_deletion_requests").update(
        {
            "status": DeletionRequestStatus.FAILED.value,
            "failed_at": datetime.utcnow().isoformat(),
            "failure_reason": reason[:500],
        }
    ).eq("id", request_id).execute()


def increment_deletion_retry(service_db: Client, request_id: str) -> None:
    # Two-step: read then write. Acceptable — retry counts don't need to be
    # atomic; we only use them for backoff.
    response = (
        service_db.table("account_deletion_requests")
        .select("retry_count")
        .eq("id", request_id)
        .single()
        .execute()
    )
    current = response.data.get("retry_count", 0) if response.data else 0
    service_db.table("account_deletion_requests").update(
        {
            "retry_count": current + 1,
            "last_error_at": datetime.utcnow().isoformat(),
            "status": DeletionRequestStatus.SCHEDULED.value,  # back to queue
        }
    ).eq("id", request_id).execute()
```

- [ ] **Step 4.3.2: Write failing tests**

Create `tests/test_internal_cron.py`:

```python
"""Tests for /internal/cron/process-deletions.

Auth via X-Cron-Secret. Service-role DB and Clerk client are patched.
"""

import hmac
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app


SECRET = "test-cron-secret"


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def _settings():
    with patch("app.routes.internal_cron.get_settings") as s:
        s.return_value.cron_shared_secret = SECRET
        yield


@pytest.fixture(autouse=True)
def _service_db():
    with patch("app.routes.internal_cron.build_service_role_client") as b:
        b.return_value = MagicMock()
        yield b


def _row(id_: str = "r1", user_id: str = "u_1", email: str = "a@b"):
    return MagicMock(
        id=id_,
        user_id=user_id,
        email=email,
        scheduled_deletion_at=datetime(2026, 4, 1),
    )


class TestSecretGuard:
    def test_missing_header_is_401(self, client):
        r = client.post("/internal/cron/process-deletions")
        assert r.status_code == 401

    def test_wrong_secret_is_401(self, client):
        r = client.post(
            "/internal/cron/process-deletions",
            headers={"X-Cron-Secret": "nope"},
        )
        assert r.status_code == 401


class TestNoDueRows:
    def test_returns_200_empty(self, client):
        with patch("app.routes.internal_cron.fetch_due_deletions",
                   return_value=[]):
            r = client.post(
                "/internal/cron/process-deletions",
                headers={"X-Cron-Secret": SECRET},
            )
        assert r.status_code == 200
        assert r.json() == {"processed": 0, "failed": 0}


class TestHappyPath:
    def test_processes_one_row(self, client):
        row = _row()
        with patch("app.routes.internal_cron.fetch_due_deletions",
                   return_value=[row]), \
             patch("app.routes.internal_cron.mark_deletion_processing",
                   return_value=True), \
             patch("app.routes.internal_cron.send_deletion_completed",
                   return_value=True), \
             patch("app.routes.internal_cron.delete_user") as clerk_delete, \
             patch("app.routes.internal_cron.mark_deletion_clerk_called") as ok:
            r = client.post(
                "/internal/cron/process-deletions",
                headers={"X-Cron-Secret": SECRET},
            )
        assert r.status_code == 200
        assert r.json() == {"processed": 1, "failed": 0}
        clerk_delete.assert_called_once_with("u_1")
        assert ok.called


class TestClaimContention:
    def test_skipped_when_another_worker_claimed(self, client):
        row = _row()
        with patch("app.routes.internal_cron.fetch_due_deletions",
                   return_value=[row]), \
             patch("app.routes.internal_cron.mark_deletion_processing",
                   return_value=False), \
             patch("app.routes.internal_cron.delete_user") as clerk_delete:
            r = client.post(
                "/internal/cron/process-deletions",
                headers={"X-Cron-Secret": SECRET},
            )
        assert r.status_code == 200
        assert r.json()["processed"] == 0
        assert not clerk_delete.called


class TestClerkFailure:
    def test_marks_failed_after_clerk_error(self, client):
        from app.services.clerk_admin import ClerkAPIError
        row = _row()
        with patch("app.routes.internal_cron.fetch_due_deletions",
                   return_value=[row]), \
             patch("app.routes.internal_cron.mark_deletion_processing",
                   return_value=True), \
             patch("app.routes.internal_cron.send_deletion_completed",
                   return_value=True), \
             patch("app.routes.internal_cron.delete_user",
                   side_effect=ClerkAPIError("503")), \
             patch("app.routes.internal_cron.mark_deletion_failed") as failed:
            r = client.post(
                "/internal/cron/process-deletions",
                headers={"X-Cron-Secret": SECRET},
            )
        assert r.status_code == 200
        assert r.json()["failed"] == 1
        assert failed.called
```

- [ ] **Step 4.3.3: Run, confirm fail**

```bash
pytest tests/test_internal_cron.py -v
```
Expected: ImportError.

- [ ] **Step 4.3.4: Implement the route**

Create `app/routes/internal_cron.py`:

```python
"""Server-to-server cron entry. Called by pg_cron via pg_net.

Auth: constant-time compare on X-Cron-Secret header.
Idempotent: each run reclaims only rows still in 'scheduled'.
"""

import hmac
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException

from app.db.client import (
    Settings,
    build_service_role_client,
    fetch_due_deletions,
    get_settings,
    mark_deletion_clerk_called,
    mark_deletion_failed,
    mark_deletion_processing,
)
from app.services.clerk_admin import ClerkAPIError, delete_user
from app.services.email_service import send_deletion_completed

router = APIRouter()
logger = logging.getLogger(__name__)


def _verify_secret(header: str | None, expected: str) -> None:
    if not header or not expected:
        raise HTTPException(status_code=401, detail="unauthorised")
    if not hmac.compare_digest(header, expected):
        raise HTTPException(status_code=401, detail="unauthorised")


@router.post("/internal/cron/process-deletions")
def process_deletions(
    settings: Annotated[Settings, Depends(get_settings)],
    x_cron_secret: Annotated[str | None, Header()] = None,
) -> dict:
    _verify_secret(x_cron_secret, settings.cron_shared_secret)

    service_db = build_service_role_client()
    rows = fetch_due_deletions(service_db, limit=50)
    processed = 0
    failed = 0

    for row in rows:
        if not mark_deletion_processing(service_db, row.id):
            # Another worker (or a cancellation) raced us. Skip.
            continue

        # Send email FIRST — we'd rather notify on time even if Clerk later 5xxs
        # within this transaction; the row stays processing → reconciler.
        send_deletion_completed(to=row.email, first_name=None)

        try:
            delete_user(row.user_id)
        except ClerkAPIError as e:
            logger.exception(
                "Clerk delete failed: request=%s user=%s",
                row.id,
                row.user_id[-4:],
            )
            mark_deletion_failed(service_db, row.id, repr(e))
            failed += 1
            continue

        mark_deletion_clerk_called(service_db, row.id)
        processed += 1

    return {"processed": processed, "failed": failed}
```

- [ ] **Step 4.3.5: Register router in `main.py`**

Add:
```python
from app.routes import internal_cron as internal_cron_routes
```
And:
```python
app.include_router(internal_cron_routes.router)
```

- [ ] **Step 4.3.6: Run, confirm pass**

```bash
pytest tests/test_internal_cron.py -v
```
Expected: 7 passed.

- [ ] **Step 4.3.7: Commit**

```bash
git add app/routes/internal_cron.py app/db/client.py app/main.py tests/test_internal_cron.py
git commit -m "feat(cron): /internal/cron/process-deletions endpoint

Constant-time secret check, atomic scheduled→processing claim, send
completion email, call Clerk delete with retries, mark clerk_called.
On Clerk failure mark request failed and continue with next row."
```

---

### Task 4.4 — Run full suite to catch regressions

- [ ] **Step 4.4.1: Run all tests**

```bash
pytest -q
```
Expected: All previously-passing tests + new tests green. If `tests/test_deps.py` was already broken on `main` because it patches a non-existent `supabase_jwt_secret`, that's pre-existing and out of scope.

- [ ] **Step 4.4.2: No-commit checkpoint**

If any unexpected failures, fix in place. No commit unless changes were needed.

---

### Task 4.5 — Cut over: delete `app/routes/emails.py`, drop the import

**Files:**
- Delete: `app/routes/emails.py`
- Modify: `app/main.py`

> Both `user.created` and `user.deleted` are now handled by `webhooks_clerk.py`. Old `/emails/welcome` and `/emails/goodbye` endpoints are dead. **Hard dependency:** before merging this task, the Clerk dashboard webhook URL must already be `/webhooks/clerk` (Task 5.2.5). Otherwise incoming Clerk webhooks 404 and Svix retries until they fail — welcome and goodbye emails stop. Sequence: do Task 5.2.5 first, verify with Clerk's "Send test event", then merge this task.

- [ ] **Step 4.5.1: Delete the file**

```bash
git rm app/routes/emails.py
```

- [ ] **Step 4.5.2: Drop import + include_router from `main.py`**

In `app/main.py`, remove:
```python
from app.routes import emails as emails_routes
```
and
```python
app.include_router(emails_routes.router)
```

- [ ] **Step 4.5.3: Run all tests**

```bash
pytest -q
```
Expected: All green. No tests reference `app.routes.emails` (we never added one).

- [ ] **Step 4.5.4: Commit**

```bash
git add -u app/main.py
git commit -m "refactor(routes): remove emails.py, single /webhooks/clerk URL

webhooks_clerk.py now covers both user.created and user.deleted
with Svix idempotency. Phase 5 updates Clerk dashboard webhook URL."
```

---

## Phase 5 — Clerk + Render configuration

### Task 5.1 — Update `render.yaml` env declarations

**Files:**
- Modify: `render.yaml`

- [ ] **Step 5.1.1: Append new env vars**

Append below the existing entries (preserving indentation):

```yaml
      - key: CLERK_SECRET_KEY
        sync: false
      - key: SUPABASE_SERVICE_ROLE_KEY
        sync: false
      - key: CRON_SHARED_SECRET
        sync: false
      - key: APP_BASE_URL
        sync: false
      - key: FRONTEND_BASE_URL
        sync: false
      - key: ACCOUNT_DELETION_ENABLED
        value: "false"
      - key: APP_ENV
        value: "production"
      - key: RESEND_TEMPLATE_DELETION_CONFIRM
        sync: false
      - key: RESEND_TEMPLATE_DELETION_SCHEDULED
        sync: false
      - key: RESEND_TEMPLATE_DELETION_COMPLETED
        sync: false
```

- [ ] **Step 5.1.2: Commit**

```bash
git add render.yaml
git commit -m "feat(render): account deletion env vars (sync: false)

Feature gate defaults false; secrets declared, values set in
Render dashboard."
```

---

### Task 5.2 — Set values in Render + Clerk + Resend dashboards

> No code changes — runbook step. Track in PR description.

- [ ] **Step 5.2.1: Generate cron secret**

```bash
python -c 'import secrets; print(secrets.token_urlsafe(48))'
```
Save the output.

- [ ] **Step 5.2.2: Render dashboard**

Set:
- `CLERK_SECRET_KEY` = Clerk dashboard → API keys → Secret keys → copy.
- `SUPABASE_SERVICE_ROLE_KEY` = Supabase dashboard → Settings → API → service_role.
- `CRON_SHARED_SECRET` = output from 5.2.1.
- `APP_BASE_URL` = `https://insights-engine.onrender.com` (or your Render URL).
- `FRONTEND_BASE_URL` = your deployed frontend URL.
- `ACCOUNT_DELETION_ENABLED` = `false` (stay off until rollout).
- `APP_ENV` = `production`.
- `RESEND_TEMPLATE_DELETION_CONFIRM` / `_SCHEDULED` / `_COMPLETED` = template IDs from Resend (create them in step 5.2.4).

- [ ] **Step 5.2.3: Mirror cron secret in Postgres**

In Supabase SQL editor:
```sql
alter database postgres set app.cron_secret = 'PASTE_SAME_VALUE_AS_CRON_SHARED_SECRET';
```

- [ ] **Step 5.2.4: Create Resend templates**

In Resend dashboard, create three templates (confirm / scheduled / completed). Each uses placeholders:
- Confirm: `{{USER}}`, `{{CONFIRM_URL}}`
- Scheduled: `{{USER}}`, `{{CANCEL_URL}}`, `{{DELETION_DATE}}`
- Completed: `{{USER}}`

Copy the IDs into the matching Render env vars.

- [ ] **Step 5.2.5: Update Clerk webhook URL**

Clerk dashboard → Webhooks → edit existing endpoint → URL = `https://<render>/webhooks/clerk`. Subscribe events: `user.created`, `user.deleted`. Save signing secret into Render `CLERK_WEBHOOK_SECRET` (replace existing if value changed).

- [ ] **Step 5.2.6: Disable Account Portal**

Clerk dashboard → Configure → Account Portal → toggle off. (User cannot self-delete via Clerk's UI — only through our `/account/deletion/request`.)

- [ ] **Step 5.2.7: Smoke test webhook from dashboard**

Clerk dashboard → Webhooks → endpoint → "Send test event" → pick `user.created`. Expected response: 200. Verify in Render logs and `select count(*) from public.webhook_events` (should be ≥ 1).

- [ ] **Step 5.2.8: Smoke test cron endpoint**

```bash
curl -X POST https://<render>/internal/cron/process-deletions \
  -H "X-Cron-Secret: <CRON_SHARED_SECRET>"
```
Expected: `{"processed":0,"failed":0}` (no due rows yet).

Check pg_cron job ran:
```sql
select * from cron.job_run_details
where jobid in (select jobid from cron.job where jobname='process-account-deletions')
order by start_time desc limit 5;
```
Expected: recent rows with `succeeded`.

> No commit on this task — all dashboard work.

---

## Phase 6 — Testing strategy

### Task 6.1 — Dev fast-forward override

**Files:**
- Modify: `app/services/deletion_service.py`

- [ ] **Step 6.1.1: Add dev-only override**

In `app/services/deletion_service.py`, modify `confirm_deletion`:

```python
def confirm_deletion(
    service_db: Client,
    raw_token: str,
) -> DeletionRequestRow:
    hashed = hash_token(raw_token)
    row = fetch_deletion_request_by_token_hash(service_db, hashed)
    if row is None:
        raise TokenNotFound()
    if row.confirmation_token_expires_at and \
            row.confirmation_token_expires_at < datetime.utcnow():
        raise TokenExpired()

    settings = get_settings()
    if settings.app_env == "dev":
        # Fast-forward for local end-to-end testing. Cron picks up the row
        # at the next 15-minute tick.
        scheduled_at = datetime.utcnow() + timedelta(minutes=1)
    else:
        scheduled_at = datetime.utcnow() + timedelta(days=GRACE_PERIOD_DAYS)

    confirm_deletion_request(service_db, row.id, scheduled_at)

    cancel_url = f"{settings.frontend_base_url}/settings#cancel-deletion"
    send_deletion_scheduled(
        to=row.email,
        first_name=None,
        cancel_url=cancel_url,
        deletion_date=scheduled_at.date(),
    )
    return row
```

- [ ] **Step 6.1.2: Add test for the dev-override branch**

Append to `tests/test_deletion_service.py`:

```python
class TestDevOverride:
    def test_dev_env_uses_one_minute_window(self):
        existing = MagicMock(
            id="req-1",
            email="a@b",
            confirmation_token_expires_at=datetime.utcnow() + timedelta(hours=1),
            status=DeletionRequestStatus.PENDING_CONFIRMATION,
        )
        service_db = MagicMock()
        with patch(
            "app.services.deletion_service.fetch_deletion_request_by_token_hash",
            return_value=existing,
        ), patch(
            "app.services.deletion_service.confirm_deletion_request"
        ) as confirm_db, patch(
            "app.services.deletion_service.send_deletion_scheduled",
            return_value=True,
        ), patch(
            "app.services.deletion_service.get_settings"
        ) as s:
            s.return_value.app_env = "dev"
            s.return_value.frontend_base_url = "https://app"
            s.return_value.app_base_url = "https://api"

            confirm_deletion(service_db, raw_token="raw")

            scheduled_at = confirm_db.call_args[0][2]
            delta = scheduled_at - datetime.utcnow()
            assert delta.total_seconds() < 120  # ≈ 1 minute, not 30 days
```

- [ ] **Step 6.1.3: Run**

```bash
pytest tests/test_deletion_service.py -v
```
Expected: 8 passed.

- [ ] **Step 6.1.4: Commit**

```bash
git add app/services/deletion_service.py tests/test_deletion_service.py
git commit -m "feat(deletion): dev-only fast-forward when APP_ENV=dev

Shrinks scheduled_deletion_at to now()+1min so local end-to-end
testing doesn't require waiting 30 days. Hard-gated by APP_ENV."
```

---

### Task 6.2 — Manual webhook runbook

**Files:**
- Create: `tests/manual_webhook.md`

- [ ] **Step 6.2.1: Write the runbook**

Create `tests/manual_webhook.md`:

```markdown
# Local end-to-end webhook test

Use Svix CLI's `listen` command (or ngrok) to forward Clerk webhook
deliveries from production to your local FastAPI.

## Prerequisites

- `svix-cli` installed (`brew install svix/svix/svix-cli` or Cargo).
- Local FastAPI running at `http://localhost:8000`.
- `APP_ENV=dev` so the 30-day window collapses to 1 minute.
- All env vars from `.env.example` populated.

## Steps

1. **Forward webhook traffic to your laptop:**

   ```bash
   svix listen --forward-to http://localhost:8000/webhooks/clerk
   ```

   Copy the temporary URL printed (e.g. `https://play.svix.com/in/...`).

2. **Point Clerk at the temporary URL** (Clerk dashboard → Webhooks →
   edit URL). Don't forget to switch back to your Render URL after.

3. **Trigger the test event:**

   In Clerk dashboard → Users → pick a test user → click "Delete user".
   Expect:
   - Render-side: `INFO Clerk webhook received: type=user.deleted user=...`
   - Resend dashboard: completion email queued.
   - Supabase: row in `account_deletion_audit` with event=`user_data_deleted`
     for that user's hashed id.

4. **End-to-end via the API:**

   ```bash
   # Create a deletion request as the test user
   curl -X POST http://localhost:8000/account/deletion/request \
     -H "Authorization: Bearer <test-clerk-jwt>" \
     -H "Content-Type: application/json" \
     -d '{"email":"test@x","first_name":"Test"}'

   # Open the confirm link from the email
   # → status = scheduled, scheduled_at = now()+1min (because APP_ENV=dev)

   # Wait <2 minutes for pg_cron to fire (or call /internal/cron manually)
   curl -X POST http://localhost:8000/internal/cron/process-deletions \
     -H "X-Cron-Secret: <CRON_SHARED_SECRET>"
   ```

   Verify each side-effect: Resend email, Clerk user removed, audit row written.

## Replay test

In Svix dashboard, find the recent `user.deleted` event → "Replay".
Expected: 200 with `{"replay":true}`. No new audit rows.
```

- [ ] **Step 6.2.2: Commit**

```bash
git add tests/manual_webhook.md
git commit -m "docs(test): manual webhook runbook for local E2E"
```

---

### Task 6.3 — `delete_user_data()` function smoke test

**Files:** No code change — runbook step inside the spec.

- [ ] **Step 6.3.1: Seed a test user**

In Supabase SQL editor (use a non-prod or staging project if you have one; otherwise be careful with IDs):

```sql
insert into public.profiles (user_id) values ('user_test_purge_me');
insert into public.user_settings (user_id) values ('user_test_purge_me');
insert into public.accounts (user_id, name) values ('user_test_purge_me', 'tmp');
-- repeat for any other tables you can populate without breaking FKs
```

- [ ] **Step 6.3.2: Insert a deletion request row**

```sql
insert into public.account_deletion_requests (user_id, email, status, scheduled_deletion_at)
values ('user_test_purge_me', 'test@x', 'scheduled', now());
```

- [ ] **Step 6.3.3: Call the function**

```sql
select public.delete_user_data('user_test_purge_me');
```

- [ ] **Step 6.3.4: Verify all 12 tables are empty for the test user**

```sql
select 'transactions' as t, count(*) from public.transactions where user_id='user_test_purge_me'
union all select 'debt_payments', count(*) from public.debt_payments where user_id='user_test_purge_me'
union all select 'allocations', count(*) from public.allocations where user_id='user_test_purge_me'
union all select 'budget_archive_reports', count(*) from public.budget_archive_reports where user_id='user_test_purge_me'
union all select 'recurring_transactions', count(*) from public.recurring_transactions where user_id='user_test_purge_me'
union all select 'debts', count(*) from public.debts where user_id='user_test_purge_me'
union all select 'goals', count(*) from public.goals where user_id='user_test_purge_me'
union all select 'budgets', count(*) from public.budgets where user_id='user_test_purge_me'
union all select 'accounts', count(*) from public.accounts where user_id='user_test_purge_me'
union all select 'categories', count(*) from public.categories where user_id='user_test_purge_me' and is_system=false
union all select 'user_settings', count(*) from public.user_settings where user_id='user_test_purge_me'
union all select 'profiles', count(*) from public.profiles where user_id='user_test_purge_me';
```
Expected: all rows show `count = 0`.

```sql
select status, completed_at from public.account_deletion_requests
where user_id='user_test_purge_me';
```
Expected: status = `completed`, `completed_at` not null.

```sql
select count(*) from public.account_deletion_audit
where user_id_hash = digest('user_test_purge_me', 'sha256');
```
Expected: `count >= 1`, event = `user_data_deleted`.

- [ ] **Step 6.3.5: Idempotency — run again**

```sql
select public.delete_user_data('user_test_purge_me');
```
Expected: succeeds, no error. A second audit row may be written — acceptable.

- [ ] **Step 6.3.6: Document the run in the PR description**

No commit. Note in the PR: "Phase 6.3 smoke test passed: all 12 tables empty for test user, audit row written, idempotent re-run successful."

---

## Phase 7 — Observability + rollout

### Task 7.1 — Logging audit pass

**Files:**
- Modify: any of `app/routes/account_deletion.py`, `webhooks_clerk.py`, `internal_cron.py`, `services/clerk_admin.py`, `services/deletion_service.py` if violations found.

- [ ] **Step 7.1.1: Grep for unsafe log patterns**

```bash
grep -rn "logger\." app/routes/account_deletion.py app/routes/webhooks_clerk.py \
  app/routes/internal_cron.py app/services/clerk_admin.py \
  app/services/deletion_service.py
```

- [ ] **Step 7.1.2: Verify each log line follows these rules**

- ✅ Allowed: request id, status transitions, retry counts, masked user_id (`user_id[-4:]`), event type, audit metadata that's non-PII.
- ❌ Forbidden: full user_id, email address, first name, raw token, signed token, IP address, JWT.

If any line violates, replace with masked version. Example fix:

```python
# Bad
logger.info("Sending deletion confirmation email to %s", to)

# Better
logger.info("Sending deletion confirmation email (recipient masked)")
```

> Pragmatic compromise: the existing `email_service.py` logs `to` directly. We left those in earlier phases for parity with the existing welcome/goodbye loggers. Decide here whether to keep that parity (and document) or tighten across the board. **If tightening, mask in `email_service.py` too**.

- [ ] **Step 7.1.3: Commit if changes were made**

```bash
git add -u
git commit -m "refactor(logging): mask user identifiers in deletion paths"
```

(If no changes, skip this commit.)

---

### Task 7.2 — Rollback runbook

**Files:**
- Create: `docs/runbooks/account-deletion.md`

- [ ] **Step 7.2.1: Write the runbook**

Create `docs/runbooks/account-deletion.md`:

```markdown
# Account deletion — runbook

## Disable user-facing flow (instant)

Set Render env `ACCOUNT_DELETION_ENABLED=false`, redeploy. All four
user-facing endpoints return 503. Admin Clerk-dashboard deletions still
fire the webhook → still wipe data; this is by design.

## Pause cron processor

```sql
update cron.job set active = false where jobname = 'process-account-deletions';
```

Resume:
```sql
update cron.job set active = true where jobname = 'process-account-deletions';
```

## Cancel an in-flight deletion (operator)

```sql
update public.account_deletion_requests
set status = 'cancelled', cancelled_at = now()
where user_id = 'user_xxx' and status in ('pending_confirmation','scheduled');
```

## Manually retry a failed deletion

After fixing the root cause:
```sql
update public.account_deletion_requests
set status = 'scheduled', scheduled_deletion_at = now(), failed_at = null,
    failure_reason = null
where id = '<request-uuid>';
```
The next 15-min cron tick will pick it up.

## Post-mortem audit query

```sql
select event, occurred_at, metadata
from public.account_deletion_audit
where user_id_hash = digest('user_xxx', 'sha256')
order by occurred_at;
```

## Known failure modes

| Symptom | Cause | Fix |
|---|---|---|
| Cron runs but no rows processed | Render asleep / non-2xx | Check Render logs; pg_cron retries next tick |
| `clerk_called` rows not progressing | Webhook delivery dropped | Reconciliation cron at 03:17 nightly will recover. To force-run: `select public.delete_user_data('user_xxx')`. |
| Status stuck in `processing` | FastAPI crashed mid-flight | Manually `update ... set status = 'scheduled'` and let cron retry. |
| Mass `failed` rows | Clerk outage | Pause cron, wait for Clerk recovery, bulk requeue (see "Manually retry"). |
```

- [ ] **Step 7.2.2: Commit**

```bash
git add docs/runbooks/account-deletion.md
git commit -m "docs(runbook): account deletion ops runbook"
```

---

### Task 7.3 — Final pre-flight checklist (no commit)

- [ ] **Step 7.3.1: Run full test suite**

```bash
pytest -q
```
Expected: all green.

- [ ] **Step 7.3.2: Confirm feature flag default**

```bash
grep -A1 ACCOUNT_DELETION_ENABLED render.yaml
```
Expected: `value: "false"`.

- [ ] **Step 7.3.3: Confirm `build_service_role_client` import locations**

```bash
grep -rn "build_service_role_client" app/
```
Expected: import in **exactly two** route files (`webhooks_clerk.py`, `internal_cron.py`), plus its definition in `db/client.py`. No imports in `services/` or other routes.

- [ ] **Step 7.3.4: Confirm pg_cron jobs scheduled**

```sql
select jobname, schedule, active from cron.job
where jobname in ('process-account-deletions', 'reconcile-stuck-deletions');
```
Expected: 2 rows, both active.

- [ ] **Step 7.3.5: Manual smoke test in staging (or production with feature flag still off)**

Follow the runbook in `tests/manual_webhook.md`. Document outcomes in PR description.

- [ ] **Step 7.3.6: Flip the feature flag in production**

After 48 hours of staging stability and a successful synthetic deletion:
- Render dashboard → `ACCOUNT_DELETION_ENABLED=true` → redeploy.

---

## Acceptance criteria

- [ ] All Phase 1–4 commits land on the feature branch with passing tests.
- [ ] Phase 5 dashboard config completed and verified.
- [ ] Phase 6.3 SQL function smoke test documented and passing.
- [ ] Phase 6.2 webhook runbook successfully exercised end-to-end.
- [ ] Phase 7.3 pre-flight checklist all green.
- [ ] `ACCOUNT_DELETION_ENABLED=true` is the **last** change before users see the flow.

## Out of scope

- Frontend implementation (separate PR).
- Backup retention messaging (privacy policy update).
- Notification when reconciliation cron fires (alert only on `status='failed'`).
- Email template content polish — visual + copy refinement is owned by the email-template-redesign branch already in flight.
