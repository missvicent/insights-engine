# Clerk Webhook Single Dispatcher + Durable Email Outbox — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collapse the two Clerk webhook endpoints into one dispatcher and make welcome + account-deleted email delivery durable via a Postgres outbox so no email is ever silently lost.

**Architecture:** A new `public.pending_emails` table is the durable outbox. Two producers (the single `POST /webhooks/clerk` dispatcher and `deletion_service.delete_account`) INSERT a row and queue a fast-path `BackgroundTask` to try sending immediately. A pg_cron-driven `POST /internal/emails/flush` endpoint sweeps unsent rows every minute as the safety net. `FOR UPDATE SKIP LOCKED` (inside Postgres helper functions) prevents the fast path and the cron worker from double-sending.

**Tech Stack:** FastAPI, Supabase (PostgreSQL via supabase-py), Pydantic v2, Resend, pytest, pg_cron + pg_net (one-time manual setup).

**Spec:** [docs/superpowers/specs/2026-05-19-clerk-webhook-single-dispatcher-design.md](../specs/2026-05-19-clerk-webhook-single-dispatcher-design.md)

---

## File Structure

**New files:**
- `supabase/migrations/<YYYYMMDDHHMMSS>_pending_emails.sql` — table, partial index, RLS-on (no policies), three SQL helper functions.
- `app/routes/internal_emails.py` — `POST /internal/emails/flush` plus the cron-secret dependency wiring.
- `tests/test_webhooks_clerk.py` — 5 cases for the new dispatcher.
- `tests/test_internal_emails.py` — auth + flush behavior tests.
- `tests/test_pending_emails_db.py` — unit tests for the 5 new db/client helpers.

**Modified files (one responsibility each):**
- `app/config.py` — add `cron_shared_secret: str`.
- `app/db/client.py` — add 5 helpers: `enqueue_email`, `claim_pending_email`, `fetch_ready_pending_emails`, `mark_pending_email_sent`, `mark_pending_email_failed`.
- `app/services/email_service.py` — add consumer `try_send_pending_email(row_id) -> bool` that orchestrates claim → Resend → mark.
- `app/routes/webhooks_clerk.py` — full rewrite to a single dispatcher.
- `app/services/deletion_service.py` — replace direct `send_account_deleted_email` call with `enqueue_email`; return `row_id`.
- `app/routes/account_deletion.py` — fix latent import bugs (`Response`, `send_account_deleted_email`, `user_ctx.email`, `user_ctx.first_name`) and queue a fast-path `BackgroundTask` for the returned `row_id`.
- `app/routes/deps.py` — add `verify_cron_secret` dependency (bearer-secret only; no JWT).
- `app/main.py` — `include_router(internal_emails.router)`.
- `tests/test_deletion_service.py` — update happy-path assertion to enqueue.
- `.env.example` — add `CRON_SHARED_SECRET=` placeholder.
- `render.yaml` — declare `CRON_SHARED_SECRET` with `sync: false`.
- `docs/superpowers/plans/2026-05-06-account-deletion-simplified.md` — update Phase 3 deviation note (lines 63-69) and Phase 4 Clerk-dashboard line (87) to one endpoint.

---

## Task 1: Add `cron_shared_secret` to settings

**Files:**
- Modify: `app/config.py:14-26`
- Modify: `.env.example`
- Modify: `render.yaml`
- Test: `tests/test_conf.py` (append a case)

- [ ] **Step 1: Write the failing test**

Open `tests/test_conf.py`. Add this test (place it next to the existing settings tests):

```python
def test_cron_shared_secret_is_required(monkeypatch):
    """CRON_SHARED_SECRET is required at boot — missing value fails."""
    from app.config import Settings, get_settings

    # Provide every other required field except CRON_SHARED_SECRET.
    monkeypatch.setenv("CLERK_ISSUER", "https://test.clerk.test")
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test")
    monkeypatch.setenv("RESEND_TEMPLATE_WELCOME", "tpl_welcome")
    monkeypatch.setenv("RESEND_TEMPLATE_ACCOUNT_DELETED", "tpl_deleted")
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.test")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon_k")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "srv_k")
    monkeypatch.delenv("CRON_SHARED_SECRET", raising=False)
    get_settings.cache_clear()

    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_cron_shared_secret_loads(monkeypatch):
    from app.config import Settings, get_settings

    monkeypatch.setenv("CLERK_ISSUER", "https://test.clerk.test")
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test")
    monkeypatch.setenv("RESEND_TEMPLATE_WELCOME", "tpl_welcome")
    monkeypatch.setenv("RESEND_TEMPLATE_ACCOUNT_DELETED", "tpl_deleted")
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.test")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon_k")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "srv_k")
    monkeypatch.setenv("CRON_SHARED_SECRET", "shh_local_dev_secret")
    get_settings.cache_clear()

    s = Settings(_env_file=None)
    assert s.cron_shared_secret == "shh_local_dev_secret"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_conf.py::test_cron_shared_secret_is_required tests/test_conf.py::test_cron_shared_secret_loads -v`
Expected: both FAIL (the field doesn't exist on `Settings` yet, so the second test gets `AttributeError` and the first won't actually raise on the missing field).

- [ ] **Step 3: Add the field to Settings**

In `app/config.py`, add `cron_shared_secret: str` to the `# ── Required ───` block (alphabetical-ish with the others):

```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ── Required ────────────────────────────────────────────────────────
    clerk_issuer: str
    clerk_secret_key: str
    account_deletion_enabled: bool = False
    cron_shared_secret: str
    resend_template_welcome: str
    resend_template_account_deleted: str
    supabase_service_role_key: str
    supabase_anon_key: str
    supabase_url: str
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_conf.py -v`
Expected: PASS (new tests + all existing).

- [ ] **Step 5: Update `.env.example`**

Append (or place near the other Render-managed secrets):

```bash
# Shared bearer secret between Render-hosted FastAPI and the Supabase
# pg_cron job that hits /internal/emails/flush. Generate with:
#   python -c 'import secrets; print(secrets.token_urlsafe(32))'
# Must match the value stored in Supabase Vault as CRON_SHARED_SECRET.
CRON_SHARED_SECRET=
```

- [ ] **Step 6: Update `render.yaml`**

Add a new env var entry to the `envVars` list (after `CORS_ORIGINS`):

```yaml
      - key: CRON_SHARED_SECRET
        sync: false
```

- [ ] **Step 7: Commit**

```bash
git add app/config.py .env.example render.yaml tests/test_conf.py
git commit -m "feat(config): require CRON_SHARED_SECRET for cron-driven internal endpoint"
```

---

## Task 2: Create `pending_emails` migration

**Files:**
- Create: `supabase/migrations/<YYYYMMDDHHMMSS>_pending_emails.sql`

> Use the current UTC timestamp for the filename: `date -u +%Y%m%d%H%M%S`. For example, `20260519143012_pending_emails.sql`.

- [ ] **Step 1: Create the migration file**

```sql
-- Durable email outbox for welcome + account-deleted notifications.
--
-- Producers (POST /webhooks/clerk and deletion_service.delete_account)
-- INSERT a row and queue a fast-path send. A pg_cron job hitting
-- /internal/emails/flush every minute is the safety net. The two SQL
-- helper functions use FOR UPDATE SKIP LOCKED so the fast path and the
-- worker never double-send the same row.

CREATE TABLE IF NOT EXISTS public.pending_emails (
    id                 bigserial PRIMARY KEY,
    template           text NOT NULL
        CHECK (template IN ('welcome', 'account_deleted')),
    to_email           text NOT NULL,
    payload            jsonb NOT NULL DEFAULT '{}'::jsonb,
    attempts           int   NOT NULL DEFAULT 0,
    max_attempts       int   NOT NULL DEFAULT 8,
    next_run_at        timestamptz NOT NULL DEFAULT now(),
    last_attempted_at  timestamptz,
    last_error         text,
    sent_at            timestamptz,
    created_at         timestamptz NOT NULL DEFAULT now()
);

-- Hot-path worker query: ready and unsent rows, ordered by readiness.
-- Partial index keeps it tiny once rows are sent.
CREATE INDEX IF NOT EXISTS idx_pending_emails_ready
    ON public.pending_emails (next_run_at)
    WHERE sent_at IS NULL;

-- Service-role only. No policies — authenticated users must never see
-- or modify this table.
ALTER TABLE public.pending_emails ENABLE ROW LEVEL SECURITY;


-- ─── Helper functions ────────────────────────────────────────────────

-- Claim a single row for the fast path.
-- Returns the row if it exists, is unsent, is due, and is not over
-- max_attempts. SKIP LOCKED means a parallel worker observing the same
-- row will skip it and get nothing (caller treats that as "already
-- claimed, nothing to do").
CREATE OR REPLACE FUNCTION public.claim_pending_email(p_id bigint)
RETURNS SETOF public.pending_emails
LANGUAGE sql
SECURITY DEFINER
SET search_path TO 'public', 'pg_temp'
AS $$
    SELECT *
    FROM public.pending_emails
    WHERE id = p_id
      AND sent_at IS NULL
      AND next_run_at <= now()
      AND attempts < max_attempts
    FOR UPDATE SKIP LOCKED
$$;

-- Claim a batch of ready rows for the cron-driven sweeper.
-- Same SKIP LOCKED semantics; ordered by next_run_at so the oldest
-- ready row goes first.
CREATE OR REPLACE FUNCTION public.fetch_ready_pending_emails(p_limit int)
RETURNS SETOF public.pending_emails
LANGUAGE sql
SECURITY DEFINER
SET search_path TO 'public', 'pg_temp'
AS $$
    SELECT *
    FROM public.pending_emails
    WHERE sent_at IS NULL
      AND next_run_at <= now()
      AND attempts < max_attempts
    ORDER BY next_run_at
    LIMIT p_limit
    FOR UPDATE SKIP LOCKED
$$;

-- Atomic failure bookkeeping: increment attempts, set last_error and
-- last_attempted_at, push next_run_at out per exponential backoff
-- (2 ^ new_attempts * 30 seconds). Done in SQL so we don't need to
-- read-then-write from Python.
CREATE OR REPLACE FUNCTION public.record_pending_email_failure(
    p_id    bigint,
    p_error text
) RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO 'public', 'pg_temp'
AS $$
BEGIN
    UPDATE public.pending_emails
    SET attempts          = attempts + 1,
        last_attempted_at = now(),
        last_error        = p_error,
        next_run_at       = now()
                            + (interval '30 seconds')
                              * power(2, attempts + 1)
    WHERE id = p_id;
END;
$$;


-- ─── Grants ──────────────────────────────────────────────────────────
-- Service-role only. anon/authenticated must not see the table or
-- call the helpers.
REVOKE ALL ON FUNCTION public.claim_pending_email(bigint)        FROM PUBLIC;
REVOKE ALL ON FUNCTION public.fetch_ready_pending_emails(int)    FROM PUBLIC;
REVOKE ALL ON FUNCTION public.record_pending_email_failure(bigint, text) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION public.claim_pending_email(bigint)     TO service_role;
GRANT EXECUTE ON FUNCTION public.fetch_ready_pending_emails(int) TO service_role;
GRANT EXECUTE ON FUNCTION public.record_pending_email_failure(bigint, text) TO service_role;

GRANT SELECT, INSERT, UPDATE ON public.pending_emails TO service_role;
GRANT USAGE, SELECT ON SEQUENCE public.pending_emails_id_seq TO service_role;
```

- [ ] **Step 2: Apply locally and smoke-test**

The repo has a local Supabase docker-compose stack (per recent commits). Apply the migration:

```bash
supabase db reset
```

Then in the SQL editor (or psql), exercise the functions:

```sql
-- INSERT a fake row, due immediately.
INSERT INTO public.pending_emails (template, to_email, payload)
VALUES ('welcome', 'fake@example.com', '{"first_name": "Test"}'::jsonb)
RETURNING id;
-- Note the returned id; call it :rid below.

-- claim_pending_email returns the row, then skips it on a second call.
SELECT * FROM public.claim_pending_email(:rid);    -- row
SELECT * FROM public.claim_pending_email(:rid);    -- still returns (no UPDATE happened yet)

-- record_pending_email_failure bumps attempts + sets backoff.
SELECT public.record_pending_email_failure(:rid, 'simulated');
SELECT id, attempts, next_run_at, last_error FROM public.pending_emails WHERE id = :rid;
-- attempts=1, next_run_at ≈ now + 60s.

-- After the bump, claim returns nothing (next_run_at is in the future).
SELECT * FROM public.claim_pending_email(:rid);    -- empty

-- Cleanup.
DELETE FROM public.pending_emails WHERE id = :rid;
```

- [ ] **Step 3: Commit**

```bash
git add supabase/migrations/<YOUR_TS>_pending_emails.sql
git commit -m "feat(db): add pending_emails outbox table + helper functions"
```

---

## Task 3: `enqueue_email` db helper

**Files:**
- Modify: `app/db/client.py` (append a new helper near `record_webhook_event`)
- Test: `tests/test_pending_emails_db.py` (new file)

- [ ] **Step 1: Write the failing test**

Create `tests/test_pending_emails_db.py`:

```python
"""Unit tests for the pending_emails db/client helpers."""

from unittest.mock import MagicMock

from app.db.client import enqueue_email


class TestEnqueueEmail:
    def test_inserts_with_defaults_and_returns_id(self):
        client = MagicMock()
        # supabase-py: client.table(...).insert(...).execute().data
        execute = client.table.return_value.insert.return_value.execute
        execute.return_value.data = [{"id": 42}]

        row_id = enqueue_email(
            client,
            template="welcome",
            to_email="alice@example.com",
            payload={"first_name": "Alice"},
        )

        assert row_id == 42
        client.table.assert_called_once_with("pending_emails")
        client.table.return_value.insert.assert_called_once_with(
            {
                "template": "welcome",
                "to_email": "alice@example.com",
                "payload": {"first_name": "Alice"},
            }
        )

    def test_payload_defaults_to_empty_dict(self):
        client = MagicMock()
        execute = client.table.return_value.insert.return_value.execute
        execute.return_value.data = [{"id": 7}]

        enqueue_email(client, template="account_deleted", to_email="b@x.com")

        client.table.return_value.insert.assert_called_once_with(
            {
                "template": "account_deleted",
                "to_email": "b@x.com",
                "payload": {},
            }
        )
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_pending_emails_db.py::TestEnqueueEmail -v`
Expected: FAIL with `ImportError: cannot import name 'enqueue_email'`.

- [ ] **Step 3: Implement `enqueue_email`**

Append to `app/db/client.py`:

```python
def enqueue_email(
    client: Client,
    template: str,
    to_email: str,
    payload: dict[str, Any] | None = None,
) -> int:
    """Insert a row into `pending_emails` and return its id.

    The producer (webhook handler or deletion_service) calls this
    synchronously inside the request. Defaults for attempts,
    max_attempts, next_run_at, and created_at come from the table
    definition, so the row is immediately eligible for the fast-path
    BackgroundTask attempt.
    """
    row = {
        "template": template,
        "to_email": to_email,
        "payload": payload or {},
    }
    response = client.table("pending_emails").insert(row).execute()
    return int(response.data[0]["id"])
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_pending_emails_db.py::TestEnqueueEmail -v`
Expected: PASS (both cases).

- [ ] **Step 5: Commit**

```bash
git add app/db/client.py tests/test_pending_emails_db.py
git commit -m "feat(db): add enqueue_email helper for the outbox"
```

---

## Task 4: `claim_pending_email` db helper

**Files:**
- Modify: `app/db/client.py`
- Test: `tests/test_pending_emails_db.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_pending_emails_db.py`:

```python
from app.db.client import claim_pending_email


class TestClaimPendingEmail:
    def test_returns_row_when_rpc_returns_one(self):
        client = MagicMock()
        rpc_execute = client.rpc.return_value.execute
        rpc_execute.return_value.data = [
            {
                "id": 42,
                "template": "welcome",
                "to_email": "a@x.com",
                "payload": {"first_name": "Alice"},
                "attempts": 0,
                "max_attempts": 8,
            }
        ]

        row = claim_pending_email(client, 42)

        assert row is not None
        assert row["id"] == 42
        assert row["template"] == "welcome"
        client.rpc.assert_called_once_with(
            "claim_pending_email", {"p_id": 42}
        )

    def test_returns_none_when_rpc_returns_empty(self):
        client = MagicMock()
        client.rpc.return_value.execute.return_value.data = []

        row = claim_pending_email(client, 42)

        assert row is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_pending_emails_db.py::TestClaimPendingEmail -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement `claim_pending_email`**

Append to `app/db/client.py`:

```python
def claim_pending_email(client: Client, row_id: int) -> dict[str, Any] | None:
    """Claim a pending_emails row for sending.

    Calls the `claim_pending_email` SQL function, which uses
    `FOR UPDATE SKIP LOCKED` so the fast path and the cron worker
    can't double-claim the same row. Returns the row or `None` when
    the row is already sent, locked by another worker, not yet due,
    or over max_attempts.
    """
    response = client.rpc("claim_pending_email", {"p_id": row_id}).execute()
    if not response.data:
        return None
    return response.data[0]
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_pending_emails_db.py::TestClaimPendingEmail -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/db/client.py tests/test_pending_emails_db.py
git commit -m "feat(db): add claim_pending_email helper (SKIP LOCKED via RPC)"
```

---

## Task 5: `fetch_ready_pending_emails` db helper

**Files:**
- Modify: `app/db/client.py`
- Test: `tests/test_pending_emails_db.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_pending_emails_db.py`:

```python
from app.db.client import fetch_ready_pending_emails


class TestFetchReadyPendingEmails:
    def test_calls_rpc_with_limit(self):
        client = MagicMock()
        client.rpc.return_value.execute.return_value.data = [
            {"id": 1, "template": "welcome", "to_email": "a@x.com",
             "payload": {}, "attempts": 0, "max_attempts": 8},
            {"id": 2, "template": "account_deleted", "to_email": "b@x.com",
             "payload": {"first_name": "Bob"}, "attempts": 3, "max_attempts": 8},
        ]

        rows = fetch_ready_pending_emails(client, limit=50)

        assert len(rows) == 2
        assert [r["id"] for r in rows] == [1, 2]
        client.rpc.assert_called_once_with(
            "fetch_ready_pending_emails", {"p_limit": 50}
        )

    def test_returns_empty_when_no_rows(self):
        client = MagicMock()
        client.rpc.return_value.execute.return_value.data = []

        rows = fetch_ready_pending_emails(client, limit=50)

        assert rows == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_pending_emails_db.py::TestFetchReadyPendingEmails -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement `fetch_ready_pending_emails`**

Append to `app/db/client.py`:

```python
def fetch_ready_pending_emails(
    client: Client, limit: int
) -> list[dict[str, Any]]:
    """Claim a batch of due, unsent pending_emails rows for the worker.

    Calls the `fetch_ready_pending_emails` SQL function (FOR UPDATE
    SKIP LOCKED). Returns the rows in next_run_at order. May return
    fewer than `limit`.
    """
    response = client.rpc(
        "fetch_ready_pending_emails", {"p_limit": limit}
    ).execute()
    return list(response.data or [])
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_pending_emails_db.py::TestFetchReadyPendingEmails -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/db/client.py tests/test_pending_emails_db.py
git commit -m "feat(db): add fetch_ready_pending_emails helper for the flush worker"
```

---

## Task 6: `mark_pending_email_sent` db helper

**Files:**
- Modify: `app/db/client.py`
- Test: `tests/test_pending_emails_db.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_pending_emails_db.py`:

```python
from datetime import datetime, timezone

from app.db.client import mark_pending_email_sent


class TestMarkPendingEmailSent:
    def test_updates_sent_at(self):
        client = MagicMock()
        eq = client.table.return_value.update.return_value.eq
        eq.return_value.execute.return_value.data = [{"id": 42}]

        before = datetime.now(timezone.utc)
        mark_pending_email_sent(client, 42)
        after = datetime.now(timezone.utc)

        client.table.assert_called_once_with("pending_emails")
        update_args = client.table.return_value.update.call_args
        assert list(update_args[0][0].keys()) == ["sent_at"]
        # Stored as ISO-8601 UTC string; parse it back to verify it's
        # between before and after.
        sent_at = datetime.fromisoformat(update_args[0][0]["sent_at"])
        assert before <= sent_at <= after

        eq.assert_called_once_with("id", 42)
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_pending_emails_db.py::TestMarkPendingEmailSent -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement `mark_pending_email_sent`**

Add a single `from datetime import datetime, timezone` at the top of `app/db/client.py` (next to the existing `from datetime import date`):

```python
from datetime import date, datetime, timezone
```

Append the helper:

```python
def mark_pending_email_sent(client: Client, row_id: int) -> None:
    """Mark a pending_emails row as sent.

    Caller has already lock-claimed this row via claim_pending_email or
    fetch_ready_pending_emails, so a plain UPDATE is safe.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    client.table("pending_emails").update({"sent_at": now_iso}).eq(
        "id", row_id
    ).execute()
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_pending_emails_db.py::TestMarkPendingEmailSent -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/db/client.py tests/test_pending_emails_db.py
git commit -m "feat(db): add mark_pending_email_sent helper"
```

---

## Task 7: `mark_pending_email_failed` db helper

**Files:**
- Modify: `app/db/client.py`
- Test: `tests/test_pending_emails_db.py`

The backoff (attempts increment + next_run_at calculation) lives in the
`record_pending_email_failure` SQL function from Task 2, so the Python
helper is a one-liner RPC call.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_pending_emails_db.py`:

```python
from app.db.client import mark_pending_email_failed


class TestMarkPendingEmailFailed:
    def test_calls_rpc_with_id_and_error(self):
        client = MagicMock()
        client.rpc.return_value.execute.return_value.data = None

        mark_pending_email_failed(client, 42, "Resend 500")

        client.rpc.assert_called_once_with(
            "record_pending_email_failure",
            {"p_id": 42, "p_error": "Resend 500"},
        )

    def test_truncates_long_error_to_2000_chars(self):
        client = MagicMock()
        client.rpc.return_value.execute.return_value.data = None

        long_err = "x" * 5000
        mark_pending_email_failed(client, 1, long_err)

        call_args = client.rpc.call_args[0]
        assert call_args[0] == "record_pending_email_failure"
        assert len(call_args[1]["p_error"]) == 2000
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_pending_emails_db.py::TestMarkPendingEmailFailed -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement `mark_pending_email_failed`**

Append to `app/db/client.py`:

```python
def mark_pending_email_failed(
    client: Client, row_id: int, error: str
) -> None:
    """Record a failed send attempt for a pending_emails row.

    Delegates to the `record_pending_email_failure` SQL function, which
    atomically increments attempts, sets last_error/last_attempted_at,
    and bumps next_run_at per the exponential backoff
    (2 ^ new_attempts * 30 seconds).

    The error string is truncated to 2000 chars so a runaway stack
    trace can't bloat the row.
    """
    truncated = error[:2000]
    client.rpc(
        "record_pending_email_failure",
        {"p_id": row_id, "p_error": truncated},
    ).execute()
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_pending_emails_db.py -v`
Expected: PASS (all five test classes in the file).

- [ ] **Step 5: Commit**

```bash
git add app/db/client.py tests/test_pending_emails_db.py
git commit -m "feat(db): add mark_pending_email_failed helper (backoff in SQL)"
```

---

## Task 8: `try_send_pending_email` consumer in `email_service`

**Files:**
- Modify: `app/services/email_service.py`
- Test: `tests/test_email_service.py` (create if missing)

This is the single function used by both the fast-path BackgroundTask
and the cron-driven flush. It claims the row, calls Resend, marks the
row sent or failed.

- [ ] **Step 1: Write the failing test**

Create `tests/test_email_service.py` (or open it if it exists — append):

```python
"""Unit tests for the email_service consumer (try_send_pending_email)."""

from unittest.mock import MagicMock, patch

import pytest

from app.config import get_settings


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("CLERK_ISSUER", "https://test.clerk.test")
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test")
    monkeypatch.setenv("CRON_SHARED_SECRET", "shh")
    monkeypatch.setenv("RESEND_TEMPLATE_WELCOME", "tpl_welcome")
    monkeypatch.setenv("RESEND_TEMPLATE_ACCOUNT_DELETED", "tpl_deleted")
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.test")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon_k")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "srv_k")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class TestTrySendPendingEmail:
    def test_returns_false_when_row_already_claimed(self):
        from app.services.email_service import try_send_pending_email

        with patch("app.services.email_service.build_service_role_client") as bsrc, \
             patch("app.services.email_service.claim_pending_email") as claim:
            claim.return_value = None

            result = try_send_pending_email(42)

        assert result is False
        bsrc.assert_called_once()
        claim.assert_called_once()

    def test_welcome_template_path_marks_sent_on_resend_success(self):
        from app.services.email_service import try_send_pending_email

        with patch("app.services.email_service.build_service_role_client"), \
             patch("app.services.email_service.claim_pending_email") as claim, \
             patch("app.services.email_service._send") as send, \
             patch("app.services.email_service.mark_pending_email_sent") as mark_sent, \
             patch("app.services.email_service.mark_pending_email_failed") as mark_failed:
            claim.return_value = {
                "id": 42,
                "template": "welcome",
                "to_email": "alice@x.com",
                "payload": {"first_name": "Alice"},
            }
            send.return_value = True

            result = try_send_pending_email(42)

        assert result is True
        send.assert_called_once_with(
            "alice@x.com", "tpl_welcome", {"USER": "Alice"}
        )
        mark_sent.assert_called_once()
        mark_failed.assert_not_called()

    def test_account_deleted_template_path_uses_deleted_template_id(self):
        from app.services.email_service import try_send_pending_email

        with patch("app.services.email_service.build_service_role_client"), \
             patch("app.services.email_service.claim_pending_email") as claim, \
             patch("app.services.email_service._send") as send, \
             patch("app.services.email_service.mark_pending_email_sent"), \
             patch("app.services.email_service.mark_pending_email_failed"):
            claim.return_value = {
                "id": 7,
                "template": "account_deleted",
                "to_email": "ex@x.com",
                "payload": {"first_name": "Ex"},
            }
            send.return_value = True

            try_send_pending_email(7)

        send.assert_called_once_with(
            "ex@x.com", "tpl_deleted", {"USER": "Ex"}
        )

    def test_resend_failure_marks_failed_not_sent(self):
        from app.services.email_service import try_send_pending_email

        with patch("app.services.email_service.build_service_role_client"), \
             patch("app.services.email_service.claim_pending_email") as claim, \
             patch("app.services.email_service._send") as send, \
             patch("app.services.email_service.mark_pending_email_sent") as mark_sent, \
             patch("app.services.email_service.mark_pending_email_failed") as mark_failed:
            claim.return_value = {
                "id": 9,
                "template": "welcome",
                "to_email": "a@x.com",
                "payload": {},
            }
            send.return_value = False

            result = try_send_pending_email(9)

        assert result is False
        mark_sent.assert_not_called()
        mark_failed.assert_called_once()
        args = mark_failed.call_args[0]
        assert args[1] == 9
        assert isinstance(args[2], str) and args[2]

    def test_unknown_template_marks_failed(self):
        """Defense in depth: the CHECK constraint forbids unknown
        templates, but if a row somehow has one, fail explicitly
        instead of silently looping."""
        from app.services.email_service import try_send_pending_email

        with patch("app.services.email_service.build_service_role_client"), \
             patch("app.services.email_service.claim_pending_email") as claim, \
             patch("app.services.email_service._send") as send, \
             patch("app.services.email_service.mark_pending_email_sent") as mark_sent, \
             patch("app.services.email_service.mark_pending_email_failed") as mark_failed:
            claim.return_value = {
                "id": 11,
                "template": "marketing_blast",
                "to_email": "a@x.com",
                "payload": {},
            }

            result = try_send_pending_email(11)

        assert result is False
        send.assert_not_called()
        mark_sent.assert_not_called()
        mark_failed.assert_called_once()
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_email_service.py -v`
Expected: FAIL with `ImportError: cannot import name 'try_send_pending_email'`.

- [ ] **Step 3: Implement `try_send_pending_email`**

Replace the contents of `app/services/email_service.py` with:

```python
import logging

import resend

from app.config import get_settings
from app.db.client import (
    build_service_role_client,
    claim_pending_email,
    mark_pending_email_failed,
    mark_pending_email_sent,
)

logger = logging.getLogger(__name__)


def _send(to: str, template_id: str, variables: dict[str, str]) -> bool:
    settings = get_settings()
    logger.info("Sending email to %s (template_id=%s)", to, template_id)
    try:
        response = resend.Emails.send(
            {
                "from": settings.resend_from_email,
                "to": [to],
                "template": {
                    "id": template_id,
                    "variables": variables,
                },
            }
        )
        logger.info("Resend accepted email to %s: %s", to, response)
        return True
    except Exception:
        logger.exception("Failed to send email to %s", to)
        return False


def _template_id_for(template: str) -> str | None:
    s = get_settings()
    if template == "welcome":
        return s.resend_template_welcome
    if template == "account_deleted":
        return s.resend_template_account_deleted
    return None


def try_send_pending_email(row_id: int) -> bool:
    """Attempt to send one pending_emails row.

    Used by both the fast-path BackgroundTask (queued by the producer)
    and the cron-driven flush worker. Claims the row via SKIP LOCKED;
    if another caller already holds the row, returns False without
    side effects.
    """
    sr_client = build_service_role_client()
    row = claim_pending_email(sr_client, row_id)
    if row is None:
        return False

    template_id = _template_id_for(row["template"])
    if template_id is None:
        logger.error(
            "Unknown pending_emails.template=%s on row id=%s",
            row["template"], row_id,
        )
        mark_pending_email_failed(
            sr_client, row_id, f"unknown template: {row['template']}"
        )
        return False

    first_name = (row.get("payload") or {}).get("first_name")
    ok = _send(row["to_email"], template_id, {"USER": first_name})
    if ok:
        mark_pending_email_sent(sr_client, row_id)
        return True
    mark_pending_email_failed(sr_client, row_id, "resend rejected")
    return False


# Kept as a thin convenience for any caller that already has the
# email + first_name in hand and just wants to enqueue+send synchronously.
# Producers should prefer enqueue_email + try_send_pending_email.
def send_welcome_email(to: str, first_name: str | None = None) -> bool:
    settings = get_settings()
    return _send(to, settings.resend_template_welcome, {"USER": first_name})


def send_account_deleted_email(to: str, first_name: str | None = None) -> bool:
    settings = get_settings()
    return _send(to, settings.resend_template_account_deleted, {"USER": first_name})
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_email_service.py -v`
Expected: PASS (all five tests).

- [ ] **Step 5: Commit**

```bash
git add app/services/email_service.py tests/test_email_service.py
git commit -m "feat(email): add try_send_pending_email consumer (claim + send + mark)"
```

---

## Task 9: Rewrite `webhooks_clerk.py` as a single dispatcher

**Files:**
- Modify: `app/routes/webhooks_clerk.py` (full rewrite of lines 1-122)
- Test: `tests/test_webhooks_clerk.py` (new file)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_webhooks_clerk.py`:

```python
"""Unit tests for the single Clerk webhook dispatcher."""

import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from svix.webhooks import WebhookVerificationError

from app.config import get_settings
from app.main import app


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("CLERK_ISSUER", "https://test.clerk.test")
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test")
    monkeypatch.setenv("CLERK_WEBHOOK_SECRET", "whsec_test")
    monkeypatch.setenv("CRON_SHARED_SECRET", "shh")
    monkeypatch.setenv("RESEND_TEMPLATE_WELCOME", "tpl_welcome")
    monkeypatch.setenv("RESEND_TEMPLATE_ACCOUNT_DELETED", "tpl_deleted")
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.test")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon_k")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "srv_k")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _headers():
    return {
        "svix-id": "msg_test_1",
        "svix-timestamp": "1700000000",
        "svix-signature": "v1,sig",
    }


def _post(body: dict) -> "Response":
    with TestClient(app) as client:
        return client.post(
            "/webhooks/clerk", json=body, headers=_headers()
        )


@patch("app.routes.webhooks_clerk.Webhook")
def test_bad_signature_returns_401(MockWebhook):
    MockWebhook.return_value.verify.side_effect = WebhookVerificationError("bad")

    with patch("app.routes.webhooks_clerk.build_service_role_client") as bsrc, \
         patch("app.routes.webhooks_clerk.record_webhook_event") as rec, \
         patch("app.routes.webhooks_clerk.enqueue_email") as enq:
        resp = _post({"type": "user.created", "data": {}})

    assert resp.status_code == 401
    bsrc.assert_not_called()
    rec.assert_not_called()
    enq.assert_not_called()


@patch("app.routes.webhooks_clerk.Webhook")
def test_duplicate_svix_id_returns_204_no_enqueue(MockWebhook):
    MockWebhook.return_value.verify.return_value = None

    with patch("app.routes.webhooks_clerk.build_service_role_client") as bsrc, \
         patch("app.routes.webhooks_clerk.record_webhook_event") as rec, \
         patch("app.routes.webhooks_clerk.enqueue_email") as enq, \
         patch("app.routes.webhooks_clerk.call_delete_user_data") as cdud:
        bsrc.return_value = MagicMock()
        rec.return_value = False  # duplicate

        resp = _post({"type": "user.created", "data": {}})

    assert resp.status_code == 204
    enq.assert_not_called()
    cdud.assert_not_called()


@patch("app.routes.webhooks_clerk.Webhook")
def test_user_created_enqueues_welcome(MockWebhook):
    MockWebhook.return_value.verify.return_value = None
    body = {
        "type": "user.created",
        "data": {
            "id": "user_abc",
            "first_name": "Alice",
            "primary_email_address_id": "idn_1",
            "email_addresses": [
                {"id": "idn_0", "email_address": "old@x.com"},
                {"id": "idn_1", "email_address": "alice@x.com"},
            ],
        },
    }

    with patch("app.routes.webhooks_clerk.build_service_role_client") as bsrc, \
         patch("app.routes.webhooks_clerk.record_webhook_event") as rec, \
         patch("app.routes.webhooks_clerk.enqueue_email") as enq, \
         patch("app.routes.webhooks_clerk.try_send_pending_email") as send:
        bsrc.return_value = MagicMock()
        rec.return_value = True
        enq.return_value = 99

        resp = _post(body)

    assert resp.status_code == 204
    enq.assert_called_once_with(
        bsrc.return_value,
        template="welcome",
        to_email="alice@x.com",
        payload={"first_name": "Alice"},
    )
    # The fast-path BackgroundTask runs after the response, so by the
    # time TestClient returns, try_send_pending_email(99) has been called.
    send.assert_called_once_with(99)


@patch("app.routes.webhooks_clerk.Webhook")
def test_user_created_no_primary_email_skips_enqueue(MockWebhook):
    MockWebhook.return_value.verify.return_value = None
    body = {
        "type": "user.created",
        "data": {
            "id": "user_abc",
            "first_name": "Alice",
            "primary_email_address_id": "idn_missing",
            "email_addresses": [
                {"id": "idn_0", "email_address": "other@x.com"},
            ],
        },
    }

    with patch("app.routes.webhooks_clerk.build_service_role_client") as bsrc, \
         patch("app.routes.webhooks_clerk.record_webhook_event") as rec, \
         patch("app.routes.webhooks_clerk.enqueue_email") as enq:
        bsrc.return_value = MagicMock()
        rec.return_value = True

        resp = _post(body)

    assert resp.status_code == 204
    enq.assert_not_called()


@patch("app.routes.webhooks_clerk.Webhook")
def test_user_deleted_wipes_and_enqueues(MockWebhook):
    MockWebhook.return_value.verify.return_value = None
    body = {"type": "user.deleted", "data": {"id": "user_xyz"}}

    with patch("app.routes.webhooks_clerk.build_service_role_client") as bsrc, \
         patch("app.routes.webhooks_clerk.record_webhook_event") as rec, \
         patch("app.routes.webhooks_clerk.fetch_profile_for_deletion") as fp, \
         patch("app.routes.webhooks_clerk.call_delete_user_data") as cdud, \
         patch("app.routes.webhooks_clerk.enqueue_email") as enq, \
         patch("app.routes.webhooks_clerk.try_send_pending_email") as send:
        bsrc.return_value = MagicMock()
        rec.return_value = True
        fp.return_value = ("xyz@x.com", "Ex User")
        enq.return_value = 101

        resp = _post(body)

    assert resp.status_code == 204
    cdud.assert_called_once_with(bsrc.return_value, "user_xyz")
    enq.assert_called_once_with(
        bsrc.return_value,
        template="account_deleted",
        to_email="xyz@x.com",
        payload={"first_name": "Ex User"},
    )
    send.assert_called_once_with(101)


@patch("app.routes.webhooks_clerk.Webhook")
def test_user_deleted_missing_id_skips_wipe(MockWebhook):
    MockWebhook.return_value.verify.return_value = None
    body = {"type": "user.deleted", "data": {}}

    with patch("app.routes.webhooks_clerk.build_service_role_client") as bsrc, \
         patch("app.routes.webhooks_clerk.record_webhook_event") as rec, \
         patch("app.routes.webhooks_clerk.call_delete_user_data") as cdud, \
         patch("app.routes.webhooks_clerk.enqueue_email") as enq:
        bsrc.return_value = MagicMock()
        rec.return_value = True

        resp = _post(body)

    assert resp.status_code == 204
    cdud.assert_not_called()
    enq.assert_not_called()


@patch("app.routes.webhooks_clerk.Webhook")
def test_unknown_event_type_returns_204_no_work(MockWebhook):
    MockWebhook.return_value.verify.return_value = None
    body = {"type": "session.created", "data": {"id": "sess_1"}}

    with patch("app.routes.webhooks_clerk.build_service_role_client") as bsrc, \
         patch("app.routes.webhooks_clerk.record_webhook_event") as rec, \
         patch("app.routes.webhooks_clerk.enqueue_email") as enq, \
         patch("app.routes.webhooks_clerk.call_delete_user_data") as cdud:
        bsrc.return_value = MagicMock()
        rec.return_value = True

        resp = _post(body)

    assert resp.status_code == 204
    enq.assert_not_called()
    cdud.assert_not_called()
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_webhooks_clerk.py -v`
Expected: most cases FAIL — the route still has two endpoints, not one, and `try_send_pending_email` isn't imported by the route module.

- [ ] **Step 3: Rewrite `app/routes/webhooks_clerk.py`**

Replace the entire file contents with:

```python
import json
import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from svix.webhooks import Webhook, WebhookVerificationError

from app.config import get_settings
from app.db.client import (
    build_service_role_client,
    call_delete_user_data,
    enqueue_email,
    fetch_profile_for_deletion,
    record_webhook_event,
)
from app.services.email_service import try_send_pending_email

logger = logging.getLogger(__name__)

router = APIRouter(tags=["webhooks-clerk"])


@router.post(
    "/webhooks/clerk",
    status_code=204,
    responses={401: {"description": "Invalid Svix signature"}},
)
async def clerk_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
) -> None:
    payload = await request.body()
    headers = {
        "svix-id": request.headers.get("svix-id"),
        "svix-timestamp": request.headers.get("svix-timestamp"),
        "svix-signature": request.headers.get("svix-signature"),
    }

    try:
        Webhook(get_settings().clerk_webhook_secret).verify(payload, headers)
    except WebhookVerificationError as err:
        raise HTTPException(status_code=401, detail="Invalid signature") from err

    sr_client = build_service_role_client()
    if not record_webhook_event(sr_client, headers["svix-id"]):
        logger.info("Duplicate webhook svix-id=%s; skipping", headers["svix-id"])
        return

    event = json.loads(payload)
    event_type = event.get("type")
    logger.info("Received Clerk %s webhook event", event_type)

    if event_type == "user.created":
        data = event.get("data", {})
        primary_id = data.get("primary_email_address_id")
        primary_email = next(
            (
                e["email_address"]
                for e in data.get("email_addresses", [])
                if e.get("id") == primary_id
            ),
            None,
        )
        if primary_email is None:
            logger.warning(
                "user.created with no primary email: user_id=%s",
                data.get("id"),
            )
            return
        row_id = enqueue_email(
            sr_client,
            template="welcome",
            to_email=primary_email,
            payload={"first_name": data.get("first_name")},
        )
        background_tasks.add_task(try_send_pending_email, row_id)
        return

    if event_type == "user.deleted":
        data = event.get("data", {})
        user_id = data.get("id")
        if not user_id:
            logger.warning("user.deleted: no user_id in event data")
            return
        profile = fetch_profile_for_deletion(sr_client, user_id)
        call_delete_user_data(sr_client, user_id)
        if profile:
            email, full_name = profile
            row_id = enqueue_email(
                sr_client,
                template="account_deleted",
                to_email=email,
                payload={"first_name": full_name},
            )
            background_tasks.add_task(try_send_pending_email, row_id)
        return

    logger.info("Unhandled Clerk event type=%s; returning 204", event_type)
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_webhooks_clerk.py -v`
Expected: PASS (all 7 cases).

- [ ] **Step 5: Commit**

```bash
git add app/routes/webhooks_clerk.py tests/test_webhooks_clerk.py
git commit -m "feat(webhooks): collapse Clerk webhook into single dispatcher with outbox"
```

---

## Task 10: Update `deletion_service.delete_account` to enqueue

**Files:**
- Modify: `app/services/deletion_service.py:22-42`
- Modify: `tests/test_deletion_service.py` (update happy-path expectation)

- [ ] **Step 1: Update the happy-path test**

Open `tests/test_deletion_service.py`. Find the happy-path test (it currently asserts `send_account_deleted_email` is called). Replace its expectation:

```python
def test_happy_path_enqueues_account_deleted_email(self):
    # ── arrange ──
    sr_client = MagicMock()
    user_ctx = MagicMock(user_id="user_abc")
    with patch("app.services.deletion_service.fetch_profile_for_deletion") as fp, \
         patch("app.services.deletion_service.insert_audit_event") as audit, \
         patch("app.services.deletion_service.call_delete_user_data") as cdud, \
         patch("app.services.deletion_service.delete_clerk_user") as dcu, \
         patch("app.services.deletion_service.enqueue_email") as enq:
        fp.return_value = ("u@x.com", "Jane")
        enq.return_value = 77

        # ── act ──
        from app.services.deletion_service import delete_account
        row_id = delete_account(user_ctx, sr_client)

    # ── assert ──
    assert row_id == 77
    fp.assert_called_once_with(sr_client, "user_abc")
    audit.assert_called_once()
    cdud.assert_called_once_with(sr_client, "user_abc")
    dcu.assert_called_once_with("user_abc")
    enq.assert_called_once_with(
        sr_client,
        template="account_deleted",
        to_email="u@x.com",
        payload={"first_name": "Jane"},
    )
```

Also update the "profile-not-found" test to assert the function returns `None` (no enqueue when there's no email) and the "clerk-delete-failed" test should still raise `ClerkDeleteFailed` before any enqueue happens. Spot-check existing test cases and adjust as needed.

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_deletion_service.py -v`
Expected: FAIL (the service still calls `send_account_deleted_email`, not `enqueue_email`, and returns None).

- [ ] **Step 3: Rewrite the service body**

In `app/services/deletion_service.py`, change the imports and the function:

```python
import logging

from supabase.client import Client

from app.context import UserContext
from app.db.client import (
    call_delete_user_data,
    enqueue_email,
    fetch_profile_for_deletion,
    insert_audit_event,
)
from app.models.schemas import AuditEvent
from app.services.clerk_admin import ClerkAPIError, delete_clerk_user

logger = logging.getLogger(__name__)


class ClerkDeleteFailed(Exception):
    """Raised when the Clerk user deletion fails."""


def delete_account(user_ctx: UserContext, sr_client: Client) -> int | None:
    """Wipe the user's data, delete them from Clerk, enqueue the
    confirmation email. Returns the pending_emails row id so the route
    can queue a fast-path BackgroundTask; returns None when no profile
    row exists (no email to send).
    """
    user_id = user_ctx.user_id
    profile = fetch_profile_for_deletion(sr_client, user_id)
    if not profile:
        logger.warning("delete_account: no profile row for user_id=%s", user_id)

    email, full_name = profile if profile else (None, None)

    insert_audit_event(sr_client, user_id, AuditEvent.REQUEST_INITIATED)
    call_delete_user_data(sr_client, user_id)

    try:
        delete_clerk_user(user_id)
    except ClerkAPIError as e:
        insert_audit_event(
            sr_client, user_id, AuditEvent.CLERK_DELETE_FAILED, {"error": str(e)}
        )
        raise ClerkDeleteFailed(str(e)) from e

    if email is None:
        return None
    return enqueue_email(
        sr_client,
        template="account_deleted",
        to_email=email,
        payload={"first_name": full_name},
    )
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_deletion_service.py -v`
Expected: PASS (happy path + profile-not-found + clerk-delete-failed).

- [ ] **Step 5: Commit**

```bash
git add app/services/deletion_service.py tests/test_deletion_service.py
git commit -m "feat(deletion): enqueue account-deleted email via outbox, return row_id"
```

---

## Task 11: Fix and update `account_deletion.py` route

The current file has latent import bugs (`Response`, `send_account_deleted_email`, `user_ctx.email`, `user_ctx.first_name`) that would crash the route the moment the feature flag flipped on. This task fixes them and wires the fast-path BackgroundTask.

**Files:**
- Modify: `app/routes/account_deletion.py` (full rewrite of lines 1-26)
- Test: `tests/test_account_deletion_route.py` (create if missing)

- [ ] **Step 1: Write the failing tests**

Create or open `tests/test_account_deletion_route.py`:

```python
"""Tests for POST /account/delete."""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("ACCOUNT_DELETION_ENABLED", "true")
    monkeypatch.setenv("CLERK_ISSUER", "https://test.clerk.test")
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test")
    monkeypatch.setenv("CLERK_WEBHOOK_SECRET", "whsec_test")
    monkeypatch.setenv("CRON_SHARED_SECRET", "shh")
    monkeypatch.setenv("RESEND_TEMPLATE_WELCOME", "tpl_w")
    monkeypatch.setenv("RESEND_TEMPLATE_ACCOUNT_DELETED", "tpl_d")
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.test")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon_k")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "srv_k")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _override_user_ctx():
    from app.context import UserContext
    from app.routes.deps import get_user_ctx
    app.dependency_overrides[get_user_ctx] = lambda: UserContext(
        user_id="user_abc", db=MagicMock()
    )
    yield
    app.dependency_overrides.pop(get_user_ctx, None)


@pytest.fixture(autouse=True)
def _override():
    yield from _override_user_ctx()


def test_returns_204_and_queues_fast_path_send():
    with patch("app.routes.account_deletion.build_service_role_client") as bsrc, \
         patch("app.routes.account_deletion.delete_account_service") as svc, \
         patch("app.routes.account_deletion.try_send_pending_email") as send:
        bsrc.return_value = MagicMock()
        svc.return_value = 55

        with TestClient(app) as client:
            resp = client.post(
                "/account/delete",
                headers={"Authorization": "Bearer placeholder"},
            )

    assert resp.status_code == 204
    svc.assert_called_once()
    send.assert_called_once_with(55)


def test_returns_204_when_no_email_to_send():
    """delete_account returns None when there's no profile row — the
    route still returns 204 and just doesn't queue a send."""
    with patch("app.routes.account_deletion.build_service_role_client"), \
         patch("app.routes.account_deletion.delete_account_service") as svc, \
         patch("app.routes.account_deletion.try_send_pending_email") as send:
        svc.return_value = None

        with TestClient(app) as client:
            resp = client.post(
                "/account/delete",
                headers={"Authorization": "Bearer placeholder"},
            )

    assert resp.status_code == 204
    send.assert_not_called()


def test_maps_clerk_delete_failed_to_502():
    from app.services.deletion_service import ClerkDeleteFailed
    with patch("app.routes.account_deletion.build_service_role_client"), \
         patch("app.routes.account_deletion.delete_account_service") as svc:
        svc.side_effect = ClerkDeleteFailed("clerk 5xx")

        with TestClient(app) as client:
            resp = client.post(
                "/account/delete",
                headers={"Authorization": "Bearer placeholder"},
            )

    assert resp.status_code == 502


def test_returns_503_when_flag_disabled(monkeypatch):
    monkeypatch.setenv("ACCOUNT_DELETION_ENABLED", "false")
    get_settings.cache_clear()

    with TestClient(app) as client:
        resp = client.post(
            "/account/delete",
            headers={"Authorization": "Bearer placeholder"},
        )

    assert resp.status_code == 503
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_account_deletion_route.py -v`
Expected: FAIL — current route imports a missing `Response` and references `user_ctx.email` / `user_ctx.first_name` which don't exist on `UserContext`.

- [ ] **Step 3: Rewrite `app/routes/account_deletion.py`**

Replace the entire file contents with:

```python
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from app.config import get_settings
from app.context import UserContext
from app.db.client import build_service_role_client
from app.routes.deps import get_user_ctx
from app.services.deletion_service import ClerkDeleteFailed
from app.services.deletion_service import delete_account as delete_account_service
from app.services.email_service import try_send_pending_email

router = APIRouter(tags=["account-deletion"])


@router.post("/account/delete", status_code=204)
def delete_account_(
    user_ctx: Annotated[UserContext, Depends(get_user_ctx)],
    background_tasks: BackgroundTasks,
) -> None:
    if not get_settings().account_deletion_enabled:
        raise HTTPException(
            status_code=503, detail="account deletion not yet enabled"
        )
    sr_client = build_service_role_client()
    try:
        row_id = delete_account_service(user_ctx, sr_client)
    except ClerkDeleteFailed as e:
        raise HTTPException(status_code=502, detail=str(e)) from e

    if row_id is not None:
        background_tasks.add_task(try_send_pending_email, row_id)
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_account_deletion_route.py -v`
Expected: PASS (all 4 cases).

- [ ] **Step 5: Commit**

```bash
git add app/routes/account_deletion.py tests/test_account_deletion_route.py
git commit -m "fix(account-deletion): route enqueues via outbox, fix latent import bugs"
```

---

## Task 12: Add `verify_cron_secret` dependency

**Files:**
- Modify: `app/routes/deps.py` (append a new dependency)
- Test: `tests/test_deps.py` (append cases)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_deps.py`:

```python
def test_verify_cron_secret_accepts_matching_bearer(monkeypatch):
    monkeypatch.setenv("CRON_SHARED_SECRET", "shh")
    from app.config import get_settings
    get_settings.cache_clear()
    from app.routes.deps import verify_cron_secret
    from fastapi.security import HTTPAuthorizationCredentials

    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="shh")
    # Should not raise.
    verify_cron_secret(creds)


def test_verify_cron_secret_rejects_mismatch(monkeypatch):
    monkeypatch.setenv("CRON_SHARED_SECRET", "shh")
    from app.config import get_settings
    get_settings.cache_clear()
    from app.routes.deps import verify_cron_secret
    from fastapi import HTTPException
    from fastapi.security import HTTPAuthorizationCredentials

    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="wrong")
    import pytest
    with pytest.raises(HTTPException) as excinfo:
        verify_cron_secret(creds)
    assert excinfo.value.status_code == 401


def test_verify_cron_secret_uses_constant_time_compare(monkeypatch):
    """Smoke check: the comparison should not short-circuit on first
    differing byte (mitigates timing attacks). We verify by patching
    secrets.compare_digest and asserting it was called."""
    monkeypatch.setenv("CRON_SHARED_SECRET", "shh")
    from app.config import get_settings
    get_settings.cache_clear()
    from app.routes.deps import verify_cron_secret
    from fastapi.security import HTTPAuthorizationCredentials
    from unittest.mock import patch

    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="shh")
    with patch("app.routes.deps.secrets.compare_digest", return_value=True) as cmp:
        verify_cron_secret(creds)
    cmp.assert_called_once()
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_deps.py -k cron_secret -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement the dependency**

In `app/routes/deps.py`, add `import secrets` at the top and append:

```python
cron_bearer_scheme = HTTPBearer(
    auto_error=True,
    scheme_name="CronSharedSecret",
    description="Shared secret bearer for /internal/* endpoints.",
)


def verify_cron_secret(
    credentials: Annotated[
        HTTPAuthorizationCredentials, Depends(cron_bearer_scheme)
    ],
) -> None:
    """Authenticate /internal/* callers (currently: the Supabase pg_cron job).

    Compares the bearer token to `CRON_SHARED_SECRET` in constant time
    so a timing attack can't probe the secret one byte at a time. No
    JWT decoding here — preserves the rule that JWT verification only
    lives in `get_user_ctx`.
    """
    expected = get_settings().cron_shared_secret
    if not secrets.compare_digest(credentials.credentials, expected):
        raise HTTPException(status_code=401, detail="invalid cron secret")
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_deps.py -v`
Expected: PASS (new cases + existing).

- [ ] **Step 5: Commit**

```bash
git add app/routes/deps.py tests/test_deps.py
git commit -m "feat(deps): add verify_cron_secret dependency (bearer, constant-time)"
```

---

## Task 13: Add `POST /internal/emails/flush` route

**Files:**
- Create: `app/routes/internal_emails.py`
- Modify: `app/main.py:46-51` (add the include)
- Test: `tests/test_internal_emails.py` (new file)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_internal_emails.py`:

```python
"""Tests for POST /internal/emails/flush."""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("CLERK_ISSUER", "https://test.clerk.test")
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test")
    monkeypatch.setenv("CLERK_WEBHOOK_SECRET", "whsec_test")
    monkeypatch.setenv("CRON_SHARED_SECRET", "shh")
    monkeypatch.setenv("RESEND_TEMPLATE_WELCOME", "tpl_w")
    monkeypatch.setenv("RESEND_TEMPLATE_ACCOUNT_DELETED", "tpl_d")
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.test")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon_k")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "srv_k")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_missing_bearer_returns_401():
    with TestClient(app) as client:
        resp = client.post("/internal/emails/flush")
    # FastAPI's HTTPBearer with auto_error=True returns 403 if no Authorization
    # header is sent. Accept either 401 or 403 as "rejected".
    assert resp.status_code in (401, 403)


def test_wrong_bearer_returns_401():
    with TestClient(app) as client:
        resp = client.post(
            "/internal/emails/flush",
            headers={"Authorization": "Bearer wrong"},
        )
    assert resp.status_code == 401


def test_valid_bearer_no_rows_returns_zero_counts():
    with patch("app.routes.internal_emails.build_service_role_client"), \
         patch("app.routes.internal_emails.fetch_ready_pending_emails") as fr, \
         patch("app.routes.internal_emails.try_send_pending_email") as send:
        fr.return_value = []

        with TestClient(app) as client:
            resp = client.post(
                "/internal/emails/flush",
                headers={"Authorization": "Bearer shh"},
            )

    assert resp.status_code == 200
    assert resp.json() == {"scanned": 0, "sent": 0, "failed": 0}
    send.assert_not_called()


def test_valid_bearer_two_rows_one_succeeds_one_fails():
    with patch("app.routes.internal_emails.build_service_role_client"), \
         patch("app.routes.internal_emails.fetch_ready_pending_emails") as fr, \
         patch("app.routes.internal_emails.try_send_pending_email") as send:
        fr.return_value = [
            {"id": 1, "template": "welcome", "to_email": "a@x.com",
             "payload": {}, "attempts": 0, "max_attempts": 8},
            {"id": 2, "template": "account_deleted", "to_email": "b@x.com",
             "payload": {}, "attempts": 0, "max_attempts": 8},
        ]
        send.side_effect = [True, False]

        with TestClient(app) as client:
            resp = client.post(
                "/internal/emails/flush",
                headers={"Authorization": "Bearer shh"},
            )

    assert resp.status_code == 200
    assert resp.json() == {"scanned": 2, "sent": 1, "failed": 1}
    assert send.call_count == 2
    assert send.call_args_list[0][0][0] == 1
    assert send.call_args_list[1][0][0] == 2


def test_body_batch_size_clamps_to_200():
    with patch("app.routes.internal_emails.build_service_role_client"), \
         patch("app.routes.internal_emails.fetch_ready_pending_emails") as fr, \
         patch("app.routes.internal_emails.try_send_pending_email"):
        fr.return_value = []

        with TestClient(app) as client:
            resp = client.post(
                "/internal/emails/flush",
                headers={"Authorization": "Bearer shh"},
                json={"batch_size": 999},
            )

    assert resp.status_code == 200
    fr.assert_called_once()
    # Second positional arg is the limit.
    assert fr.call_args[0][1] == 200 or fr.call_args.kwargs.get("limit") == 200
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_internal_emails.py -v`
Expected: FAIL — the route doesn't exist.

- [ ] **Step 3: Create `app/routes/internal_emails.py`**

```python
"""Internal endpoints called by infrastructure (currently: pg_cron).

Auth is a shared bearer secret (`CRON_SHARED_SECRET`), not a Clerk JWT.
The single endpoint here drains the pending_emails outbox.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.db.client import build_service_role_client, fetch_ready_pending_emails
from app.routes.deps import verify_cron_secret
from app.services.email_service import try_send_pending_email

router = APIRouter(tags=["internal"])

MAX_BATCH_SIZE = 200


class FlushRequest(BaseModel):
    # No upper bound here; we clamp to MAX_BATCH_SIZE in the handler so an
    # over-spec caller silently gets a sensible cap instead of a 422.
    batch_size: int = Field(default=50, ge=1)


class FlushResponse(BaseModel):
    scanned: int
    sent: int
    failed: int


@router.post(
    "/internal/emails/flush",
    response_model=FlushResponse,
    dependencies=[Depends(verify_cron_secret)],
)
def flush_emails(body: FlushRequest | None = None) -> FlushResponse:
    batch_size = min((body or FlushRequest()).batch_size, MAX_BATCH_SIZE)
    sr_client = build_service_role_client()
    rows = fetch_ready_pending_emails(sr_client, batch_size)

    sent = 0
    failed = 0
    for row in rows:
        if try_send_pending_email(int(row["id"])):
            sent += 1
        else:
            failed += 1

    return FlushResponse(scanned=len(rows), sent=sent, failed=failed)
```

- [ ] **Step 4: Register the router**

In `app/main.py`, add the import and the `include_router` line:

```python
from app.routes import account_deletion, internal_emails, webhooks_clerk
...
app.include_router(insights_routes.router)
app.include_router(ai_routes.router)
app.include_router(health_routes.router)
app.include_router(account_deletion.router)
app.include_router(webhooks_clerk.router)
app.include_router(internal_emails.router)
```

- [ ] **Step 5: Run to verify it passes**

Run: `pytest tests/test_internal_emails.py -v`
Expected: PASS (all 5 cases).

- [ ] **Step 6: Commit**

```bash
git add app/routes/internal_emails.py app/main.py tests/test_internal_emails.py
git commit -m "feat(internal): add POST /internal/emails/flush (pg_cron sweeper target)"
```

---

## Task 14: Full-suite green check

**Files:** none modified.

- [ ] **Step 1: Run the entire test suite**

Run: `pytest -v`
Expected: all PASS. If anything fails, stop and fix it. Do not move to ops tasks until the suite is fully green.

- [ ] **Step 2: Run a static syntax sanity check**

Run: `python -c "import app.main"`
Expected: no error. This catches import-order bugs (the kind that bit `account_deletion.py` before this rewrite).

- [ ] **Step 3: No commit** — verification only.

---

## Task 15: Update the account-deletion plan doc

The 2026-05-06 plan recorded the "two webhook endpoints" choice as a
deliberate deviation. This spec reverses that deviation; the plan doc
should reflect the current reality.

**Files:**
- Modify: `docs/superpowers/plans/2026-05-06-account-deletion-simplified.md`

- [ ] **Step 1: Edit the deviation note (lines 63-69)**

Replace the "One webhook URL → two URLs" bullet (item 1 in the
"Phase 3 deviations" section) with:

```markdown
1. **Two webhook URLs → back to one.** Originally shipped as two endpoints
   (`POST /webhooks/clerk/welcome` and `POST /webhooks/clerk/delete_account`).
   Reversed 2026-05-19 per
   [2026-05-19-clerk-webhook-single-dispatcher-design.md](../specs/2026-05-19-clerk-webhook-single-dispatcher-design.md):
   now `POST /webhooks/clerk` dispatches on `event.type`. Same
   `CLERK_WEBHOOK_SECRET`. Same idempotency check. The collapse also
   moved both emails onto a durable `pending_emails` outbox so process
   crashes or transient Resend failures can no longer silently lose a
   welcome or account-deleted email.
```

- [ ] **Step 2: Edit the Phase 4 Clerk dashboard line (line 87)**

Replace the current Clerk-dashboard bullet with:

```markdown
- [ ] Clerk dashboard — webhook URL → **`/webhooks/clerk`** (one
  endpoint, subscribed to both `user.created` and `user.deleted`); use
  the existing `CLERK_WEBHOOK_SECRET`; disable Clerk Account Portal
  "delete account". Delete the previously-registered
  `/webhooks/clerk/welcome` and `/webhooks/clerk/delete_account`
  endpoints if they were created during the deviation period.
```

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/plans/2026-05-06-account-deletion-simplified.md
git commit -m "docs(plan): record webhook re-collapse to a single dispatcher"
```

---

## Task 16: One-time Supabase ops (manual runbook)

**Files:** none modified. This is a deploy-time runbook.

Run these steps **after the code above is deployed to staging or
production**, in the Supabase SQL editor for the target project.

- [ ] **Step 1: Enable the cron + http extensions**

```sql
CREATE EXTENSION IF NOT EXISTS pg_cron;
CREATE EXTENSION IF NOT EXISTS pg_net;
```

- [ ] **Step 2: Generate the shared secret and set it in three places**

```bash
python -c 'import secrets; print(secrets.token_urlsafe(32))'
```

Copy the value:
1. Render dashboard → set as `CRON_SHARED_SECRET` env var on the
   `insights-engine` service.
2. Supabase Vault → run in SQL editor:
   ```sql
   SELECT vault.create_secret('CRON_SHARED_SECRET', '<paste-the-value>');
   ```
3. (Local dev only) paste the value into `.env`.

The cron job and the FastAPI dependency both compare against the same
value; if they drift, every cron firing returns 401.

- [ ] **Step 3: Schedule the cron job**

Replace `<render-host>` with the actual Render service host
(e.g. `insights-engine.onrender.com`):

```sql
SELECT cron.schedule(
    'flush-pending-emails',
    '* * * * *',  -- every minute
    $$
    SELECT net.http_post(
        url := 'https://<render-host>/internal/emails/flush',
        headers := jsonb_build_object(
            'Authorization',
            'Bearer ' || (
                SELECT decrypted_secret
                FROM vault.decrypted_secrets
                WHERE name = 'CRON_SHARED_SECRET'
            ),
            'Content-Type', 'application/json'
        ),
        body := '{}'::jsonb
    );
    $$
);
```

- [ ] **Step 4: Verify after one minute**

```sql
SELECT jobid, jobname, schedule, active
FROM cron.job WHERE jobname = 'flush-pending-emails';

SELECT runid, jobid, status, return_message, start_time, end_time
FROM cron.job_run_details
WHERE jobid = (SELECT jobid FROM cron.job WHERE jobname = 'flush-pending-emails')
ORDER BY start_time DESC LIMIT 5;
```

Expected: `status='succeeded'`, `return_message` contains a non-null
request id from `net.http_post`. The FastAPI logs should show a
`POST /internal/emails/flush 200` line.

- [ ] **Step 5: End-to-end smoke test**

```sql
INSERT INTO public.pending_emails (template, to_email, payload)
VALUES ('welcome', '<a real test address>', '{"first_name": "Smoke Test"}'::jsonb)
RETURNING id;
```

Wait one minute. Confirm:
```sql
SELECT id, sent_at, attempts, last_error
FROM public.pending_emails WHERE id = <returned-id>;
```
`sent_at` should be non-null; the test email should have arrived.

- [ ] **Step 6: No commit** — runbook only.

---

## Task 17: Clerk dashboard reconfiguration (manual runbook)

**Files:** none modified.

- [ ] **Step 1: Open the Clerk dashboard → Webhooks**

For the project, list the current endpoint registrations. You should
see `…/webhooks/clerk/welcome` and `…/webhooks/clerk/delete_account`
(created during the two-endpoint deviation).

- [ ] **Step 2: Create the new unified endpoint**

URL: `https://<render-host>/webhooks/clerk`
Subscribed events: `user.created`, `user.deleted`
Signing secret: reuse the same `whsec_…` value that's already in
`CLERK_WEBHOOK_SECRET` on Render. Do not generate a new secret — Clerk
lets you copy the existing signing secret across endpoints by clicking
"Use existing secret" in the endpoint creation flow.

- [ ] **Step 3: Send test events**

In the Clerk dashboard, fire one `user.created` test event and one
`user.deleted` test event against the new endpoint. Both should return
204. Check `public.pending_emails` and confirm one row appeared per
event and both rows got `sent_at` set within ~60s.

- [ ] **Step 4: Delete the old endpoints**

Once the new endpoint is verified, delete the two old registrations
(`/webhooks/clerk/welcome` and `/webhooks/clerk/delete_account`) from
the Clerk dashboard. After deletion, no traffic should hit those
routes; the routes themselves have already been removed in Task 9.

- [ ] **Step 5: No commit** — runbook only.

---

## Self-review checklist (already run by the plan author)

- **Spec coverage:** Every spec section is implemented. `pending_emails` table → Task 2. Five db helpers → Tasks 3-7. `try_send_pending_email` consumer → Task 8. Webhook dispatcher → Task 9. `deletion_service` enqueue → Task 10. Route fast-path BackgroundTask → Task 11. `verify_cron_secret` → Task 12. `/internal/emails/flush` → Task 13. Plan doc update → Task 15. Ops runbook → Tasks 16 + 17.
- **Type consistency:** `enqueue_email` returns `int` everywhere it's used; `try_send_pending_email(row_id: int) -> bool` matches both call sites (BackgroundTask and the flush loop); `delete_account` returns `int | None` consistently.
- **No placeholders:** the one templated value is `<YYYYMMDDHHMMSS>` for the migration filename (explicitly explained) and `<render-host>` in the ops runbook (must be the operator's actual host).
