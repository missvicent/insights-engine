# Clerk Lifecycle Emails Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Send a transactional welcome email when a user signs up via Clerk and a goodbye email when they delete their account, while wiping their Supabase-side data on deletion.

**Architecture:** Welcome is triggered by the `user.created` Svix-signed webhook (email is in the payload). Goodbye is triggered by an authenticated `DELETE /me` endpoint that reads the user's email from a custom JWT claim, sends the email, wipes their Supabase rows, then calls Clerk's Backend API to delete the user. The `user.deleted` webhook does data cleanup only (no email) using a service-role Supabase client, so admin-initiated deletes still cascade.

**Tech Stack:** FastAPI, Python 3.12, Supabase (PostgREST + RLS), Clerk (third-party auth, RS256 JWTs), Resend (email), Jinja2 (templates), `svix` Python SDK (webhook signature verification), `httpx` (Clerk Backend API), `pytest`.

**Working agreement:**
- Each task labels every step with **[Owner: user]** (you write it) or **[Owner: Claude]** (I write it).
- For TDD steps the order is: Claude writes the failing test → Claude runs it (FAIL) → user implements code → Claude runs the test (PASS) → user commits.
- For non-test work (migrations, env files, dependency bumps, route registration) the user owns all steps.

**Conventions referenced from the existing codebase:**
- Test factories live in `tests/conftest.py` (`make_token`, `FakeDB`, `make_user_ctx`).
- DB queries live exclusively in `app/db/client.py`.
- Routes import `get_user_ctx` from `app/routes/deps.py`.
- Type hints on every function. PEP 8. snake_case. Max line length 88.
- No bare `except:`. Imports grouped stdlib → third-party → local.

---

## File structure

### Files to create

| Path | Responsibility |
|---|---|
| `app/routes/webhooks.py` | `POST /webhooks/clerk` handler. Verifies Svix signature, dedupes by `svix-id`, dispatches to welcome / cleanup. |
| `app/routes/me.py` | `DELETE /me` handler. Sends goodbye email, wipes Supabase data, deletes user in Clerk. |
| `app/services/email_service.py` | Jinja2 environment + `send_welcome` / `send_goodbye` Resend wrapper. |
| `app/services/clerk_admin.py` | `delete_clerk_user(user_id)` — `httpx` wrapper around Clerk Backend API. |
| `app/templates/emails/welcome.html` | Welcome HTML template (Jinja2). |
| `app/templates/emails/welcome.txt` | Welcome plain-text template. |
| `app/templates/emails/account_deleted.html` | Goodbye HTML template. |
| `app/templates/emails/account_deleted.txt` | Goodbye plain-text template. |
| `migrations/2026-04-24-webhook-events.sql` | DDL for `webhook_events` table. Applied manually via Supabase SQL editor. |
| `tests/test_email_service.py` | Tests for template rendering + Resend payload + failure path. |
| `tests/test_webhook_routes.py` | Tests for the webhook handler (signature, dedup, dispatch, errors). |
| `tests/test_me_route.py` | Tests for `DELETE /me` (happy path + error paths). |
| `tests/test_clerk_admin.py` | Tests for `delete_clerk_user`. |
| `tests/test_db_record_webhook_event.py` | Tests for the idempotency primitive. |
| `tests/test_db_delete_user_data.py` | Tests for the cascading data-wipe helper. |

### Files to modify

| Path | What changes |
|---|---|
| `app/db/client.py` | Add new `Settings` fields; `build_service_client()`; `record_webhook_event(db, svix_id, event_type)`; `delete_user_data(db, user_id)`. |
| `app/models/schemas.py` | Add `ClerkEmailAddress`, `ClerkUserCreatedData`, `ClerkUserDeletedData`, `ClerkUserCreatedEnvelope`, `ClerkUserDeletedEnvelope`, `ClerkWebhookEnvelope` (discriminated union type alias). |
| `app/context.py` | Extend `UserContext` with `email: str` and `first_name: str \| None`. |
| `app/routes/deps.py` | Extract `email` and `first_name` claims from the verified JWT into `UserContext`; require `email`. |
| `app/main.py` | Register the two new routers. |
| `.env.example` | Add `CLERK_WEBHOOK_SECRET`, `CLERK_SECRET_KEY`, `RESEND_API_KEY`, `EMAIL_FROM_ADDRESS`, `APP_NAME`, `SUPABASE_SERVICE_ROLE_KEY`. |
| `requirements.txt` | Add `svix`, `resend`, `jinja2`. |
| `tests/conftest.py` | Extend `make_user_ctx` to accept `email` / `first_name`; add a `make_clerk_envelope` helper used by webhook tests. |

---

## Task 0: Add dependencies and update env

**Files:**
- Modify: `requirements.txt`
- Modify: `.env.example`

- [ ] **Step 0.1: Add libraries to `requirements.txt`** — [Owner: user]

Append to `requirements.txt` (keep alphabetical-ish order and pin versions):

```
jinja2==3.1.4
resend==2.4.0
svix==1.40.0
```

- [ ] **Step 0.2: Install** — [Owner: user]

```bash
pip install -r requirements.txt
```

Expected: three new packages installed without conflicts.

- [ ] **Step 0.3: Add env vars to `.env.example`** — [Owner: user]

Append to `.env.example`:

```
# Resend (email provider)
RESEND_API_KEY=re_xxx
EMAIL_FROM_ADDRESS=onboarding@resend.dev   # use a verified custom domain in prod
APP_NAME=Finance Insights

# Clerk Backend API (used by DELETE /me to delete the user server-side)
CLERK_SECRET_KEY=sk_test_xxx

# Clerk webhooks (Svix)
CLERK_WEBHOOK_SECRET=whsec_xxx

# Supabase service-role key (only used by the user.deleted webhook handler)
SUPABASE_SERVICE_ROLE_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI...
```

- [ ] **Step 0.4: Commit** — [Owner: user]

```bash
git add requirements.txt .env.example
git commit -m "chore(deps): add svix, resend, jinja2; document new env vars"
```

---

## Task 1: Extend Settings with the new env vars

**Files:**
- Modify: `app/db/client.py`

- [ ] **Step 1.1: Add fields to `Settings`** — [Owner: user]

Replace the `Settings` class in `app/db/client.py` with:

```python
class Settings(BaseSettings):
    supabase_url: str
    supabase_anon_key: str
    supabase_service_role_key: str
    clerk_issuer: str
    clerk_jwks_url: str | None = None
    clerk_secret_key: str
    clerk_webhook_secret: str
    ai_model: str
    resend_api_key: str
    email_from_address: str
    app_name: str = "Finance Insights"

    class Config:
        env_file = ".env"
        extra = "ignore"
```

- [ ] **Step 1.2: Smoke-check that the app still boots** — [Owner: user]

With a `.env` file populated (use placeholders for values you don't have keys for yet — they only need to be non-empty):

```bash
python -c "from app.db.client import get_settings; print(get_settings().app_name)"
```

Expected: prints `Finance Insights`. If a `ValidationError` fires for a missing field, fill that field in `.env`.

- [ ] **Step 1.3: Commit** — [Owner: user]

```bash
git add app/db/client.py
git commit -m "feat(settings): add resend, clerk webhook, and service-role config"
```

---

## Task 2: Migration — create `webhook_events` table

**Files:**
- Create: `migrations/2026-04-24-webhook-events.sql`

- [ ] **Step 2.1: Write the SQL** — [Owner: user]

Create `migrations/2026-04-24-webhook-events.sql` with:

```sql
-- Idempotency log for Svix-signed webhooks (Clerk).
-- Service-role only; no RLS policies.
create table if not exists webhook_events (
    svix_id     text primary key,
    event_type  text not null,
    received_at timestamptz not null default now()
);

comment on table webhook_events is
    'Dedup log for Clerk/Svix webhooks. svix_id is the dedup key.';
```

- [ ] **Step 2.2: Apply via Supabase SQL editor** — [Owner: user]

Open Supabase Dashboard → SQL Editor → paste the file → run. Verify in Table Editor that `webhook_events` exists with the three columns.

- [ ] **Step 2.3: Commit** — [Owner: user]

```bash
git add migrations/2026-04-24-webhook-events.sql
git commit -m "feat(db): add webhook_events idempotency table"
```

---

## Task 3: Pydantic schemas for the Clerk webhook payload

**Files:**
- Modify: `app/models/schemas.py`
- Test: `tests/test_clerk_envelope_schema.py` (new)

- [ ] **Step 3.1: Write the failing test** — [Owner: Claude]

Create `tests/test_clerk_envelope_schema.py`:

```python
"""Parsing tests for the Clerk webhook envelope schemas."""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from app.models.schemas import (
    ClerkUserCreatedEnvelope,
    ClerkUserDeletedEnvelope,
    ClerkWebhookEnvelope,
)


_adapter = TypeAdapter(ClerkWebhookEnvelope)


def _user_created_payload() -> dict:
    return {
        "type": "user.created",
        "data": {
            "id": "user_2abc",
            "first_name": "Ada",
            "last_name": "Lovelace",
            "primary_email_address_id": "email_1",
            "email_addresses": [
                {
                    "id": "email_1",
                    "email_address": "ada@example.com",
                    "verification": {"status": "verified"},
                },
                {
                    "id": "email_2",
                    "email_address": "ada+work@example.com",
                    "verification": {"status": "unverified"},
                },
            ],
        },
    }


def _user_deleted_payload() -> dict:
    return {
        "type": "user.deleted",
        "data": {"id": "user_2abc", "deleted": True, "object": "user"},
    }


class TestParseEnvelope:
    def test_user_created_parses_to_created_envelope(self):
        env = _adapter.validate_python(_user_created_payload())
        assert isinstance(env, ClerkUserCreatedEnvelope)
        assert env.data.id == "user_2abc"
        assert env.data.first_name == "Ada"
        assert env.data.primary_email_address_id == "email_1"
        assert len(env.data.email_addresses) == 2

    def test_primary_email_helper(self):
        env = _adapter.validate_python(_user_created_payload())
        assert env.data.primary_email() == "ada@example.com"

    def test_primary_email_falls_back_to_first_when_id_missing(self):
        payload = _user_created_payload()
        payload["data"]["primary_email_address_id"] = "email_doesnotexist"
        env = _adapter.validate_python(payload)
        assert env.data.primary_email() == "ada@example.com"

    def test_user_deleted_parses_to_deleted_envelope(self):
        env = _adapter.validate_python(_user_deleted_payload())
        assert isinstance(env, ClerkUserDeletedEnvelope)
        assert env.data.id == "user_2abc"

    def test_user_deleted_does_not_parse_as_created(self):
        # Regression: without a discriminated union, the deleted payload
        # could be ambiguously matched against ClerkUserCreatedData.
        env = _adapter.validate_python(_user_deleted_payload())
        assert not isinstance(env, ClerkUserCreatedEnvelope)

    def test_unknown_event_type_rejected(self):
        with pytest.raises(ValidationError):
            _adapter.validate_python(
                {"type": "session.created", "data": {"id": "x"}}
            )
```

- [ ] **Step 3.2: Run the failing test** — [Owner: Claude]

```bash
pytest tests/test_clerk_envelope_schema.py -v
```

Expected: import error — `ClerkWebhookEnvelope` does not exist yet.

- [ ] **Step 3.3: Implement the schemas** — [Owner: user]

At the top of `app/models/schemas.py`, ensure these imports are present (add what's missing):

```python
from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, Field
```

Then append to `app/models/schemas.py`:

```python
# ── Clerk webhook payloads ────────────────────────────────────────────────────


class ClerkEmailAddress(BaseModel):
    id: str
    email_address: str
    verification: Optional[dict] = None


class ClerkUserCreatedData(BaseModel):
    id: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    primary_email_address_id: Optional[str] = None
    email_addresses: list[ClerkEmailAddress] = []

    def primary_email(self) -> Optional[str]:
        """Return the primary email address, or the first available one."""
        if self.primary_email_address_id:
            for ea in self.email_addresses:
                if ea.id == self.primary_email_address_id:
                    return ea.email_address
        return self.email_addresses[0].email_address if self.email_addresses else None


class ClerkUserDeletedData(BaseModel):
    id: str
    deleted: bool = True


class ClerkUserCreatedEnvelope(BaseModel):
    type: Literal["user.created"]
    data: ClerkUserCreatedData


class ClerkUserDeletedEnvelope(BaseModel):
    type: Literal["user.deleted"]
    data: ClerkUserDeletedData


# Discriminated union: Pydantic uses the `type` field to pick the variant,
# eliminating any ambiguity between the two payload shapes.
ClerkWebhookEnvelope = Annotated[
    Union[ClerkUserCreatedEnvelope, ClerkUserDeletedEnvelope],
    Field(discriminator="type"),
]
```

Note: `ClerkWebhookEnvelope` is a *type alias*, not a class — callers parse it via `TypeAdapter(ClerkWebhookEnvelope).validate_python(...)` or `validate_json(...)`. The webhook route uses `TypeAdapter` accordingly.

- [ ] **Step 3.4: Run the test** — [Owner: Claude]

```bash
pytest tests/test_clerk_envelope_schema.py -v
```

Expected: 5 passed.

- [ ] **Step 3.5: Commit** — [Owner: user]

```bash
git add app/models/schemas.py tests/test_clerk_envelope_schema.py
git commit -m "feat(schemas): add Clerk webhook envelope models"
```

---

## Task 4: Extend `UserContext` with `email` and `first_name`

**Files:**
- Modify: `app/context.py`
- Modify: `tests/conftest.py`

- [ ] **Step 4.1: Update `UserContext`** — [Owner: user]

Edit `app/context.py` (read it first to preserve any existing fields/imports, then add `email` and `first_name`). Final shape:

```python
from dataclasses import dataclass
from typing import Any


@dataclass
class UserContext:
    user_id: str
    email: str
    db: Any  # supabase.Client; typed Any to avoid import cycle
    first_name: str | None = None
```

- [ ] **Step 4.2: Update `make_user_ctx` in `tests/conftest.py`** — [Owner: user]

Replace the existing `make_user_ctx` at the bottom of `tests/conftest.py` with:

```python
def make_user_ctx(
    user_id: str = "user-1",
    email: str = "user-1@example.com",
    first_name: str | None = "Ada",
    tables: dict | None = None,
) -> UserContext:
    return UserContext(
        user_id=user_id,
        email=email,
        first_name=first_name,
        db=FakeDB(tables),
    )
```

- [ ] **Step 4.3: Run the existing test suite to catch regressions** — [Owner: Claude]

```bash
pytest -x
```

Expected: existing tests pass. If `test_deps.py` breaks because of the existing Clerk RS256 migration, that's pre-existing — note it but do not fix here.

- [ ] **Step 4.4: Commit** — [Owner: user]

```bash
git add app/context.py tests/conftest.py
git commit -m "feat(context): carry email and first_name on UserContext"
```

---

## Task 5: `get_user_ctx` — read `email` and `first_name` claims

**Files:**
- Modify: `app/routes/deps.py`
- Test: `tests/test_deps_claims.py` (new — to avoid touching the partially-stale `test_deps.py`)

- [ ] **Step 5.1: Write the failing test** — [Owner: Claude]

Create `tests/test_deps_claims.py`:

```python
"""Tests for the new email / first_name claim extraction in get_user_ctx.

These tests bypass real RS256 verification by patching jwt.decode and the
JWKS client, focusing only on claim-to-UserContext mapping.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app.routes.deps import get_user_ctx


class _FakeDB:
    pass


def _creds(token: str = "fake.token.value") -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


@pytest.fixture(autouse=True)
def _patch_settings():
    with patch("app.routes.deps.get_settings") as m:
        m.return_value.clerk_issuer = "https://test.clerk.dev"
        yield


@pytest.fixture(autouse=True)
def _patch_jwks():
    with patch("app.routes.deps.get_jwks_client") as m:
        m.return_value.get_signing_key_from_jwt.return_value.key = "unused"
        yield


@pytest.fixture(autouse=True)
def _patch_build_user_client():
    with patch("app.routes.deps.build_user_client", return_value=_FakeDB()):
        yield


def _decode_returns(payload: dict):
    return patch("app.routes.deps.jwt.decode", return_value=payload)


class TestEmailClaim:
    def test_email_and_first_name_populated(self):
        with _decode_returns(
            {
                "sub": "user_42",
                "email": "ada@example.com",
                "first_name": "Ada",
            }
        ):
            ctx = get_user_ctx(_creds())
        assert ctx.user_id == "user_42"
        assert ctx.email == "ada@example.com"
        assert ctx.first_name == "Ada"

    def test_first_name_optional(self):
        with _decode_returns(
            {"sub": "user_42", "email": "ada@example.com"}
        ):
            ctx = get_user_ctx(_creds())
        assert ctx.first_name is None

    def test_missing_email_claim_is_401(self):
        with _decode_returns({"sub": "user_42"}):
            with pytest.raises(HTTPException) as exc:
                get_user_ctx(_creds())
        assert exc.value.status_code == 401
        assert "email" in exc.value.detail.lower()

    def test_empty_email_claim_is_401(self):
        with _decode_returns({"sub": "user_42", "email": ""}):
            with pytest.raises(HTTPException) as exc:
                get_user_ctx(_creds())
        assert exc.value.status_code == 401
```

- [ ] **Step 5.2: Run the failing test** — [Owner: Claude]

```bash
pytest tests/test_deps_claims.py -v
```

Expected: 4 failures — `UserContext` missing `email` or `email` validation not yet present.

- [ ] **Step 5.3: Update `get_user_ctx`** — [Owner: user]

In `app/routes/deps.py`, locate the block that reads `payload["sub"]` and replace from there to the end of the function with:

```python
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="invalid token")

    email = payload.get("email")
    if not email:
        raise HTTPException(
            status_code=401,
            detail="token missing required 'email' claim",
        )
    first_name = payload.get("first_name") or None

    return UserContext(
        user_id=user_id,
        email=email,
        first_name=first_name,
        db=build_user_client(token),
    )
```

- [ ] **Step 5.4: Run the test** — [Owner: Claude]

```bash
pytest tests/test_deps_claims.py -v
```

Expected: 4 passed.

- [ ] **Step 5.5: Commit** — [Owner: user]

```bash
git add app/routes/deps.py tests/test_deps_claims.py
git commit -m "feat(auth): extract email and first_name from Clerk JWT claims"
```

---

## Task 6: `db.record_webhook_event` — idempotency primitive

**Files:**
- Modify: `app/db/client.py`
- Test: `tests/test_db_record_webhook_event.py` (new)

- [ ] **Step 6.1: Write the failing test** — [Owner: Claude]

Create `tests/test_db_record_webhook_event.py`:

```python
"""Tests for the webhook idempotency insert."""

from __future__ import annotations

from app.db.client import record_webhook_event


class _Resp:
    def __init__(self, data, error=None):
        self.data = data
        self.error = error


class _InsertChain:
    """Captures inserts and simulates Supabase 23505 unique violations."""

    def __init__(self, existing_ids: set[str]):
        self.existing_ids = existing_ids
        self.last_inserted: dict | None = None

    def insert(self, row: dict):
        self.last_inserted = row
        outer = self

        class _Exec:
            def execute(self_inner):
                if row["svix_id"] in outer.existing_ids:
                    # supabase-py raises a generic exception with PGRST/code info
                    raise _UniqueViolation()
                outer.existing_ids.add(row["svix_id"])
                return _Resp([row])

        return _Exec()


class _UniqueViolation(Exception):
    """Stand-in for supabase-py's 23505 unique-violation surface."""

    code = "23505"


class _FakeDB:
    def __init__(self, existing: set[str] | None = None):
        self.chain = _InsertChain(existing or set())

    def table(self, name: str):
        assert name == "webhook_events"
        return self.chain


class TestRecordWebhookEvent:
    def test_inserts_new_event_returns_true(self):
        db = _FakeDB()
        ok = record_webhook_event(db, "evt_1", "user.created")
        assert ok is True
        assert db.chain.last_inserted == {
            "svix_id": "evt_1",
            "event_type": "user.created",
        }

    def test_duplicate_returns_false(self):
        db = _FakeDB(existing={"evt_1"})
        ok = record_webhook_event(db, "evt_1", "user.created")
        assert ok is False
```

- [ ] **Step 6.2: Run the failing test** — [Owner: Claude]

```bash
pytest tests/test_db_record_webhook_event.py -v
```

Expected: import error — `record_webhook_event` does not exist.

- [ ] **Step 6.3: Implement** — [Owner: user]

Append to `app/db/client.py`:

```python
def build_service_client() -> Client:
    """Return a Supabase client bound to the service-role key.

    RLS is bypassed by this client. Use it ONLY in code paths where the
    request itself authenticates via another mechanism (e.g. Svix-signed
    webhooks) and we therefore have no JWT to attach.
    """
    s = get_settings()
    return create_client(s.supabase_url, s.supabase_service_role_key)


def record_webhook_event(db: Client, svix_id: str, event_type: str) -> bool:
    """Record a webhook event for idempotency.

    Returns True if this is the first time we've seen `svix_id`.
    Returns False if the event was already recorded (duplicate redelivery).
    Any other error propagates.
    """
    try:
        db.table("webhook_events").insert(
            {"svix_id": svix_id, "event_type": event_type}
        ).execute()
        return True
    except Exception as e:  # supabase-py wraps Postgres errors
        if getattr(e, "code", None) == "23505" or "23505" in str(e):
            return False
        raise
```

- [ ] **Step 6.4: Run the test** — [Owner: Claude]

```bash
pytest tests/test_db_record_webhook_event.py -v
```

Expected: 2 passed.

- [ ] **Step 6.5: Commit** — [Owner: user]

```bash
git add app/db/client.py tests/test_db_record_webhook_event.py
git commit -m "feat(db): add record_webhook_event idempotency primitive"
```

---

## Task 7: `db.delete_user_data` — cascade through user-owned tables

**Files:**
- Modify: `app/db/client.py`
- Test: `tests/test_db_delete_user_data.py` (new)

- [ ] **Step 7.1: Write the failing test** — [Owner: Claude]

Create `tests/test_db_delete_user_data.py`:

```python
"""Tests for the user-data cleanup helper."""

from __future__ import annotations

from app.db.client import delete_user_data

EXPECTED_TABLES = [
    "transactions",
    "allocations",
    "budgets",
    "goals",
    "debts",
    "recurring_transactions",
]


class _DeleteRecorder:
    """Mimics db.table(name).delete().eq(col, val).execute()."""

    def __init__(self, log: list[tuple[str, str, str]], table: str):
        self.log = log
        self.table = table

    def delete(self):
        return self

    def eq(self, col: str, val: str):
        self.col = col
        self.val = val
        return self

    def execute(self):
        self.log.append((self.table, self.col, self.val))

        class _R:
            data = []

        return _R()


class _FakeDB:
    def __init__(self):
        self.log: list[tuple[str, str, str]] = []

    def table(self, name: str):
        return _DeleteRecorder(self.log, name)


class TestDeleteUserData:
    def test_deletes_in_dependency_order(self):
        db = _FakeDB()
        delete_user_data(db, "user_42")
        tables_hit = [entry[0] for entry in db.log]
        assert tables_hit == EXPECTED_TABLES

    def test_filters_by_user_id(self):
        db = _FakeDB()
        delete_user_data(db, "user_42")
        for table, col, val in db.log:
            assert col == "user_id"
            assert val == "user_42"

    def test_idempotent_on_second_call(self):
        db = _FakeDB()
        delete_user_data(db, "user_42")
        delete_user_data(db, "user_42")
        # Same number of calls per invocation; no error raised.
        assert len(db.log) == 2 * len(EXPECTED_TABLES)
```

Note: `allocations` is filtered by `user_id` *only if* the `allocations` table has a `user_id` column. Allocations are owned by a budget. If the schema does **not** carry `user_id` on `allocations`, the implementation must instead delete allocations whose `budget_id` belongs to the user's budgets — a different test. **Resolve in Step 7.2 by inspecting the schema before implementing.** If allocations are budget-scoped only, replace the `allocations` entry in `EXPECTED_TABLES` with a tuple-marker and update the test accordingly.

- [ ] **Step 7.2: Verify the schema and adjust if needed** — [Owner: user]

In Supabase Dashboard → Table Editor → `allocations`. If `user_id` is not a column, we cannot filter `allocations` directly by user. In that case:
1. Edit the implementation in Step 7.3 to delete allocations *before* budgets via `delete().in_("budget_id", <list of user's budget ids>)`. The query is: fetch the user's budget ids, then delete allocations referencing them, then delete the budgets.
2. Update the test in Step 7.1 to assert that ordering and the `in_("budget_id", ...)` shape instead of the simple `eq("user_id", ...)` for allocations.

- [ ] **Step 7.3: Run the failing test** — [Owner: Claude]

```bash
pytest tests/test_db_delete_user_data.py -v
```

Expected: import error — `delete_user_data` does not exist.

- [ ] **Step 7.4: Implement** — [Owner: user]

Append to `app/db/client.py` (this version assumes `allocations.user_id` exists; if Step 7.2 found otherwise, swap to the `in_("budget_id", …)` variant):

```python
USER_OWNED_TABLES = (
    "transactions",
    "allocations",
    "budgets",
    "goals",
    "debts",
    "recurring_transactions",
)


def delete_user_data(db: Client, user_id: str) -> None:
    """Delete every row owned by `user_id` across the user-owned tables.

    Idempotent: a no-op on tables that have already been cleared.
    Order matters when foreign keys lack ON DELETE CASCADE — child rows are
    deleted before their parents.
    """
    for table in USER_OWNED_TABLES:
        db.table(table).delete().eq("user_id", user_id).execute()
```

- [ ] **Step 7.5: Run the test** — [Owner: Claude]

```bash
pytest tests/test_db_delete_user_data.py -v
```

Expected: 3 passed.

- [ ] **Step 7.6: Commit** — [Owner: user]

```bash
git add app/db/client.py tests/test_db_delete_user_data.py
git commit -m "feat(db): add delete_user_data cascade for account deletion"
```

---

## Task 8: `clerk_admin.delete_clerk_user` — Backend API wrapper

**Files:**
- Create: `app/services/clerk_admin.py`
- Test: `tests/test_clerk_admin.py`

- [ ] **Step 8.1: Write the failing test** — [Owner: Claude]

Create `tests/test_clerk_admin.py`:

```python
"""Tests for the Clerk Backend API wrapper."""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest

from app.services.clerk_admin import ClerkDeleteFailed, delete_clerk_user


@pytest.fixture(autouse=True)
def _patch_settings():
    with patch("app.services.clerk_admin.get_settings") as m:
        m.return_value.clerk_secret_key = "sk_test_xxx"
        yield


def _mock_response(status_code: int, json_body: dict | None = None) -> httpx.Response:
    request = httpx.Request("DELETE", "https://api.clerk.com/v1/users/x")
    return httpx.Response(
        status_code=status_code, request=request, json=json_body or {}
    )


class TestDeleteClerkUser:
    def test_calls_correct_url_with_auth_header(self):
        with patch("app.services.clerk_admin.httpx.delete") as m:
            m.return_value = _mock_response(200, {"deleted": True})
            delete_clerk_user("user_2abc")
        m.assert_called_once()
        call = m.call_args
        assert call.args[0] == "https://api.clerk.com/v1/users/user_2abc"
        assert call.kwargs["headers"]["Authorization"] == "Bearer sk_test_xxx"
        assert call.kwargs["timeout"] == 10

    def test_non_2xx_raises(self):
        with patch("app.services.clerk_admin.httpx.delete") as m:
            m.return_value = _mock_response(500, {"errors": [{"message": "boom"}]})
            with pytest.raises(ClerkDeleteFailed):
                delete_clerk_user("user_2abc")

    def test_404_also_raises(self):
        with patch("app.services.clerk_admin.httpx.delete") as m:
            m.return_value = _mock_response(404, {})
            with pytest.raises(ClerkDeleteFailed):
                delete_clerk_user("user_does_not_exist")
```

- [ ] **Step 8.2: Run the failing test** — [Owner: Claude]

```bash
pytest tests/test_clerk_admin.py -v
```

Expected: import error — module does not exist.

- [ ] **Step 8.3: Implement** — [Owner: user]

Create `app/services/clerk_admin.py`:

```python
"""Thin wrapper around Clerk's Backend API.

Used by DELETE /me to delete the user server-side after sending the goodbye
email. We use httpx directly rather than the Clerk SDK to keep the dependency
surface tiny — we only need one endpoint.

Reference: https://clerk.com/docs/reference/backend-api/tag/Users#operation/DeleteUser
"""

import logging

import httpx

from app.db.client import get_settings


logger = logging.getLogger(__name__)


class ClerkDeleteFailed(Exception):
    """Raised when Clerk's Backend API does not return a 2xx for a delete."""


def delete_clerk_user(user_id: str) -> None:
    """Delete a user via Clerk's Backend API. Raises on non-2xx."""
    settings = get_settings()
    response = httpx.delete(
        f"https://api.clerk.com/v1/users/{user_id}",
        headers={"Authorization": f"Bearer {settings.clerk_secret_key}"},
        timeout=10,
    )
    if response.status_code >= 300:
        logger.error(
            "Clerk delete failed (user_id=%s, status=%s, body=%s)",
            user_id, response.status_code, response.text,
        )
        raise ClerkDeleteFailed(
            f"clerk DELETE /users/{user_id} returned {response.status_code}"
        )
```

- [ ] **Step 8.4: Run the test** — [Owner: Claude]

```bash
pytest tests/test_clerk_admin.py -v
```

Expected: 3 passed.

- [ ] **Step 8.5: Commit** — [Owner: user]

```bash
git add app/services/clerk_admin.py tests/test_clerk_admin.py
git commit -m "feat(clerk): add Backend API wrapper for user deletion"
```

---

## Task 9: Email templates

**Files:**
- Create: `app/templates/emails/welcome.html`
- Create: `app/templates/emails/welcome.txt`
- Create: `app/templates/emails/account_deleted.html`
- Create: `app/templates/emails/account_deleted.txt`

- [ ] **Step 9.1: Create the welcome HTML** — [Owner: user]

`app/templates/emails/welcome.html`:

```html
<!doctype html>
<html lang="en">
  <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; color: #111; max-width: 560px; margin: 0 auto; padding: 24px;">
    <h1 style="font-size: 22px; margin-bottom: 16px;">Welcome to {{ app_name }}</h1>
    <p>Hi {{ first_name | default("there") }},</p>
    <p>
      Thanks for signing up for {{ app_name }}. You can now connect your accounts,
      track your budgets, and get personalized insights about your finances.
    </p>
    <p>If you ever have questions, just reply to this email — we read every one.</p>
    <p style="margin-top: 32px;">— The {{ app_name }} team</p>
  </body>
</html>
```

- [ ] **Step 9.2: Create the welcome text** — [Owner: user]

`app/templates/emails/welcome.txt`:

```
Welcome to {{ app_name }}

Hi {{ first_name | default("there") }},

Thanks for signing up for {{ app_name }}. You can now connect your accounts,
track your budgets, and get personalized insights about your finances.

If you ever have questions, just reply to this email — we read every one.

— The {{ app_name }} team
```

- [ ] **Step 9.3: Create the goodbye HTML** — [Owner: user]

`app/templates/emails/account_deleted.html`:

```html
<!doctype html>
<html lang="en">
  <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; color: #111; max-width: 560px; margin: 0 auto; padding: 24px;">
    <h1 style="font-size: 22px; margin-bottom: 16px;">Your account has been deleted</h1>
    <p>Hi {{ first_name | default("there") }},</p>
    <p>
      We've deleted your {{ app_name }} account and the data associated with it.
      If this wasn't you, please contact us right away by replying to this email.
    </p>
    <p>Thanks for trying {{ app_name }}.</p>
    <p style="margin-top: 32px;">— The {{ app_name }} team</p>
  </body>
</html>
```

- [ ] **Step 9.4: Create the goodbye text** — [Owner: user]

`app/templates/emails/account_deleted.txt`:

```
Your account has been deleted

Hi {{ first_name | default("there") }},

We've deleted your {{ app_name }} account and the data associated with it.
If this wasn't you, please contact us right away by replying to this email.

Thanks for trying {{ app_name }}.

— The {{ app_name }} team
```

- [ ] **Step 9.5: Commit** — [Owner: user]

```bash
git add app/templates/emails/
git commit -m "feat(email): add welcome and account-deleted templates"
```

---

## Task 10: `email_service` — Resend wrapper

**Files:**
- Create: `app/services/email_service.py`
- Test: `tests/test_email_service.py`

- [ ] **Step 10.1: Write the failing test** — [Owner: Claude]

Create `tests/test_email_service.py`:

```python
"""Tests for the email service: template rendering + Resend payload + failure path."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services.email_service import send_goodbye, send_welcome


@pytest.fixture(autouse=True)
def _patch_settings():
    with patch("app.services.email_service.get_settings") as m:
        m.return_value.resend_api_key = "re_test"
        m.return_value.email_from_address = "noreply@example.com"
        m.return_value.app_name = "Finance Insights"
        yield


@pytest.fixture
def captured_send():
    with patch("app.services.email_service.resend.Emails.send") as m:
        m.return_value = {"id": "msg_1"}
        yield m


class TestSendWelcome:
    def test_sends_with_correct_payload(self, captured_send):
        ok = send_welcome("ada@example.com", "Ada")
        assert ok is True
        captured_send.assert_called_once()
        payload = captured_send.call_args.args[0]
        assert payload["from"] == "noreply@example.com"
        assert payload["to"] == ["ada@example.com"]
        assert payload["subject"] == "Welcome to Finance Insights"
        assert "Hi Ada" in payload["html"]
        assert "Hi Ada" in payload["text"]

    def test_first_name_defaults_when_none(self, captured_send):
        send_welcome("ada@example.com", None)
        payload = captured_send.call_args.args[0]
        assert "Hi there" in payload["html"]
        assert "Hi there" in payload["text"]

    def test_returns_false_on_resend_failure(self):
        with patch("app.services.email_service.resend.Emails.send") as m:
            m.side_effect = Exception("network down")
            ok = send_welcome("ada@example.com", "Ada")
        assert ok is False


class TestSendGoodbye:
    def test_sends_with_correct_payload(self, captured_send):
        ok = send_goodbye("ada@example.com", "Ada")
        assert ok is True
        payload = captured_send.call_args.args[0]
        assert payload["subject"] == "Your Finance Insights account has been deleted"
        assert "Hi Ada" in payload["html"]

    def test_returns_false_on_resend_failure(self):
        with patch("app.services.email_service.resend.Emails.send") as m:
            m.side_effect = Exception("nope")
            ok = send_goodbye("ada@example.com", None)
        assert ok is False
```

- [ ] **Step 10.2: Run the failing test** — [Owner: Claude]

```bash
pytest tests/test_email_service.py -v
```

Expected: import error — module does not exist.

- [ ] **Step 10.3: Implement** — [Owner: user]

Create `app/services/email_service.py`:

```python
"""Resend + Jinja2 email pipeline.

Two public functions: send_welcome and send_goodbye. Both render an HTML +
plain-text template pair and post to Resend. Both return True on success and
False on failure — they never raise. The caller decides whether a failure is
fatal (DELETE /me proceeds anyway; the welcome webhook just logs).
"""

import logging
from pathlib import Path

import resend
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.db.client import get_settings


logger = logging.getLogger(__name__)


_TEMPLATE_DIR = Path(__file__).parent.parent / "templates" / "emails"

_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=select_autoescape(["html"]),
    trim_blocks=True,
    lstrip_blocks=True,
)


def _render(name: str, **vars) -> str:
    return _env.get_template(name).render(**vars)


def _send(
    to: str,
    subject: str,
    html: str,
    text: str,
) -> bool:
    settings = get_settings()
    resend.api_key = settings.resend_api_key
    try:
        resend.Emails.send(
            {
                "from": settings.email_from_address,
                "to": [to],
                "subject": subject,
                "html": html,
                "text": text,
            }
        )
        return True
    except Exception as e:
        logger.error("Resend send failed (to=%s, subject=%r): %s", to, subject, e)
        return False


def send_welcome(to: str, first_name: str | None) -> bool:
    settings = get_settings()
    ctx = {"first_name": first_name, "app_name": settings.app_name}
    return _send(
        to=to,
        subject=f"Welcome to {settings.app_name}",
        html=_render("welcome.html", **ctx),
        text=_render("welcome.txt", **ctx),
    )


def send_goodbye(to: str, first_name: str | None) -> bool:
    settings = get_settings()
    ctx = {"first_name": first_name, "app_name": settings.app_name}
    return _send(
        to=to,
        subject=f"Your {settings.app_name} account has been deleted",
        html=_render("account_deleted.html", **ctx),
        text=_render("account_deleted.txt", **ctx),
    )
```

- [ ] **Step 10.4: Run the test** — [Owner: Claude]

```bash
pytest tests/test_email_service.py -v
```

Expected: 5 passed.

- [ ] **Step 10.5: Commit** — [Owner: user]

```bash
git add app/services/email_service.py tests/test_email_service.py
git commit -m "feat(email): Resend + Jinja2 send_welcome and send_goodbye"
```

---

## Task 11: `POST /webhooks/clerk` route

**Files:**
- Create: `app/routes/webhooks.py`
- Test: `tests/test_webhook_routes.py`

- [ ] **Step 11.1: Write the failing test** — [Owner: Claude]

Create `tests/test_webhook_routes.py`:

```python
"""Integration-flavoured tests for the Clerk webhook handler.

Strategy: build a real Svix signature using the SDK against a test secret so
the endpoint's verification path runs end-to-end. Resend, Clerk admin, and
Supabase are mocked at module boundaries.
"""

from __future__ import annotations

import json
import time
import uuid
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from svix.webhooks import Webhook

from app.routes import webhooks as webhooks_module


WEBHOOK_SECRET = "whsec_" + "a" * 32


def _user_created_body(user_id: str = "user_2abc") -> bytes:
    return json.dumps(
        {
            "type": "user.created",
            "data": {
                "id": user_id,
                "first_name": "Ada",
                "primary_email_address_id": "em_1",
                "email_addresses": [
                    {"id": "em_1", "email_address": "ada@example.com"},
                ],
            },
        }
    ).encode("utf-8")


def _user_deleted_body(user_id: str = "user_2abc") -> bytes:
    return json.dumps(
        {"type": "user.deleted", "data": {"id": user_id, "deleted": True}}
    ).encode("utf-8")


def _sign(body: bytes, secret: str = WEBHOOK_SECRET) -> dict[str, str]:
    msg_id = f"msg_{uuid.uuid4().hex}"
    timestamp = str(int(time.time()))
    signature = Webhook(secret).sign(msg_id, int(timestamp), body)
    return {
        "svix-id": msg_id,
        "svix-timestamp": timestamp,
        "svix-signature": signature,
        "content-type": "application/json",
    }


@pytest.fixture
def client(monkeypatch):
    """A TestClient with an app exposing only the webhook router and patched seams."""
    monkeypatch.setattr(
        "app.routes.webhooks.get_settings",
        lambda: type(
            "S", (), {"clerk_webhook_secret": WEBHOOK_SECRET}
        )(),
    )
    monkeypatch.setattr(
        "app.routes.webhooks.build_service_client",
        lambda: object(),  # we won't reach DB calls because record is patched
    )
    app = FastAPI()
    app.include_router(webhooks_module.router)
    return TestClient(app)


class TestUserCreated:
    def test_happy_path_calls_send_welcome(self, client):
        body = _user_created_body()
        with (
            patch(
                "app.routes.webhooks.record_webhook_event", return_value=True
            ) as record,
            patch(
                "app.routes.webhooks.send_welcome", return_value=True
            ) as welcome,
            patch("app.routes.webhooks.delete_user_data") as wipe,
        ):
            r = client.post("/webhooks/clerk", content=body, headers=_sign(body))
        assert r.status_code == 200
        record.assert_called_once()
        welcome.assert_called_once_with("ada@example.com", "Ada")
        wipe.assert_not_called()

    def test_email_failure_still_returns_200(self, client):
        body = _user_created_body()
        with (
            patch("app.routes.webhooks.record_webhook_event", return_value=True),
            patch("app.routes.webhooks.send_welcome", return_value=False),
        ):
            r = client.post("/webhooks/clerk", content=body, headers=_sign(body))
        assert r.status_code == 200


class TestUserDeleted:
    def test_happy_path_wipes_data(self, client):
        body = _user_deleted_body()
        with (
            patch("app.routes.webhooks.record_webhook_event", return_value=True),
            patch("app.routes.webhooks.delete_user_data") as wipe,
            patch("app.routes.webhooks.send_welcome") as welcome,
        ):
            r = client.post("/webhooks/clerk", content=body, headers=_sign(body))
        assert r.status_code == 200
        wipe.assert_called_once()
        # second positional arg is the user id
        assert wipe.call_args.args[1] == "user_2abc"
        welcome.assert_not_called()


class TestSignature:
    def test_invalid_signature_returns_401(self, client):
        body = _user_created_body()
        bad = _sign(body, secret="whsec_" + "z" * 32)
        with patch("app.routes.webhooks.record_webhook_event") as record:
            r = client.post("/webhooks/clerk", content=body, headers=bad)
        assert r.status_code == 401
        record.assert_not_called()


class TestIdempotency:
    def test_duplicate_svix_id_short_circuits(self, client):
        body = _user_created_body()
        with (
            patch(
                "app.routes.webhooks.record_webhook_event", return_value=False
            ),
            patch("app.routes.webhooks.send_welcome") as welcome,
            patch("app.routes.webhooks.delete_user_data") as wipe,
        ):
            r = client.post("/webhooks/clerk", content=body, headers=_sign(body))
        assert r.status_code == 200
        welcome.assert_not_called()
        wipe.assert_not_called()


class TestMalformed:
    def test_payload_missing_required_fields_returns_400(self, client):
        # Valid signature, garbage payload
        body = json.dumps({"type": "user.created", "data": {}}).encode("utf-8")
        with patch("app.routes.webhooks.record_webhook_event", return_value=True):
            r = client.post("/webhooks/clerk", content=body, headers=_sign(body))
        assert r.status_code == 400
```

- [ ] **Step 11.2: Run the failing test** — [Owner: Claude]

```bash
pytest tests/test_webhook_routes.py -v
```

Expected: import error — `app.routes.webhooks` does not exist.

- [ ] **Step 11.3: Implement the route** — [Owner: user]

Create `app/routes/webhooks.py`:

```python
"""Clerk webhook receiver.

POST /webhooks/clerk
- Verifies the Svix signature against CLERK_WEBHOOK_SECRET.
- Records the svix-id for idempotency. Duplicate redeliveries short-circuit.
- Dispatches user.created (welcome email) and user.deleted (data wipe).

Auth: Svix signature only — there is no JWT. The handler uses a service-role
Supabase client (RLS bypass) for all DB calls along this path.
"""

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import TypeAdapter, ValidationError
from svix.webhooks import Webhook, WebhookVerificationError

from app.db.client import (
    build_service_client,
    delete_user_data,
    get_settings,
    record_webhook_event,
)
from app.models.schemas import (
    ClerkUserCreatedEnvelope,
    ClerkUserDeletedEnvelope,
    ClerkWebhookEnvelope,
)
from app.services.email_service import send_welcome


logger = logging.getLogger(__name__)


router = APIRouter()

_envelope_adapter = TypeAdapter(ClerkWebhookEnvelope)


@router.post("/webhooks/clerk")
async def clerk_webhook(request: Request) -> dict:
    raw = await request.body()
    settings = get_settings()

    try:
        Webhook(settings.clerk_webhook_secret).verify(raw, dict(request.headers))
    except WebhookVerificationError:
        raise HTTPException(status_code=401, detail="invalid signature") from None

    svix_id = request.headers.get("svix-id", "")
    if not svix_id:
        raise HTTPException(status_code=400, detail="missing svix-id header")

    try:
        envelope = _envelope_adapter.validate_json(raw)
    except ValidationError as e:
        logger.warning("malformed Clerk webhook payload: %s", e)
        raise HTTPException(status_code=400, detail="malformed payload") from None

    db = build_service_client()

    if not record_webhook_event(db, svix_id, envelope.type):
        return {"status": "duplicate"}

    if isinstance(envelope, ClerkUserCreatedEnvelope):
        email = envelope.data.primary_email()
        if not email:
            logger.warning(
                "user.created has no email address (user_id=%s)", envelope.data.id
            )
            return {"status": "ok", "note": "no_email"}
        ok = send_welcome(email, envelope.data.first_name)
        if not ok:
            logger.warning(
                "welcome email send failed for user_id=%s", envelope.data.id
            )
        return {"status": "ok"}

    if isinstance(envelope, ClerkUserDeletedEnvelope):
        delete_user_data(db, envelope.data.id)
        return {"status": "ok"}

    return {"status": "ignored"}  # unreachable given the discriminated union
```

- [ ] **Step 11.4: Run the test** — [Owner: Claude]

```bash
pytest tests/test_webhook_routes.py -v
```

Expected: 6 passed.

- [ ] **Step 11.5: Commit** — [Owner: user]

```bash
git add app/routes/webhooks.py tests/test_webhook_routes.py
git commit -m "feat(webhooks): add POST /webhooks/clerk handler"
```

---

## Task 12: `DELETE /me` route

**Files:**
- Create: `app/routes/me.py`
- Test: `tests/test_me_route.py`

- [ ] **Step 12.1: Write the failing test** — [Owner: Claude]

Create `tests/test_me_route.py`:

```python
"""Tests for DELETE /me — the user-initiated account deletion endpoint."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.context import UserContext
from app.routes import me as me_module
from app.routes.deps import get_user_ctx
from app.services.clerk_admin import ClerkDeleteFailed


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(me_module.router)

    def _override():
        return UserContext(
            user_id="user_42",
            email="ada@example.com",
            first_name="Ada",
            db=object(),
        )

    app.dependency_overrides[get_user_ctx] = _override
    return TestClient(app)


class TestDeleteMe:
    def test_happy_path(self, client):
        with (
            patch(
                "app.routes.me.send_goodbye", return_value=True
            ) as goodbye,
            patch("app.routes.me.delete_user_data") as wipe,
            patch("app.routes.me.delete_clerk_user") as clerk_del,
        ):
            r = client.delete("/me")
        assert r.status_code == 204
        goodbye.assert_called_once_with("ada@example.com", "Ada")
        wipe.assert_called_once()
        clerk_del.assert_called_once_with("user_42")

    def test_email_failure_still_proceeds(self, client):
        with (
            patch("app.routes.me.send_goodbye", return_value=False),
            patch("app.routes.me.delete_user_data") as wipe,
            patch("app.routes.me.delete_clerk_user") as clerk_del,
        ):
            r = client.delete("/me")
        assert r.status_code == 204
        wipe.assert_called_once()
        clerk_del.assert_called_once()

    def test_data_cleanup_failure_blocks_clerk_delete(self, client):
        with (
            patch("app.routes.me.send_goodbye", return_value=True),
            patch(
                "app.routes.me.delete_user_data",
                side_effect=Exception("supabase exploded"),
            ),
            patch("app.routes.me.delete_clerk_user") as clerk_del,
        ):
            r = client.delete("/me")
        assert r.status_code == 500
        clerk_del.assert_not_called()

    def test_clerk_delete_failure_returns_500(self, client):
        with (
            patch("app.routes.me.send_goodbye", return_value=True),
            patch("app.routes.me.delete_user_data"),
            patch(
                "app.routes.me.delete_clerk_user",
                side_effect=ClerkDeleteFailed("nope"),
            ),
        ):
            r = client.delete("/me")
        assert r.status_code == 500
```

- [ ] **Step 12.2: Run the failing test** — [Owner: Claude]

```bash
pytest tests/test_me_route.py -v
```

Expected: import error — `app.routes.me` does not exist.

- [ ] **Step 12.3: Implement the route** — [Owner: user]

Create `app/routes/me.py`:

```python
"""Account self-management routes."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response

from app.context import UserContext
from app.db.client import delete_user_data
from app.routes.deps import get_user_ctx
from app.services.clerk_admin import ClerkDeleteFailed, delete_clerk_user
from app.services.email_service import send_goodbye


logger = logging.getLogger(__name__)


router = APIRouter()


@router.delete("/me", status_code=204)
def delete_me(
    ctx: Annotated[UserContext, Depends(get_user_ctx)],
) -> Response:
    """Delete the authenticated user's account and all their data.

    Order:
      1. Send goodbye email (best-effort; failure does not block).
      2. Wipe Supabase data (must succeed).
      3. Delete the user in Clerk (must succeed).

    Why this order: the email needs the email address from the JWT before any
    deletion happens. Data is wiped before Clerk deletion so a partial failure
    leaves no orphan rows owned by a user that no longer exists.
    """
    if not send_goodbye(ctx.email, ctx.first_name):
        logger.warning("goodbye email failed for user_id=%s", ctx.user_id)

    try:
        delete_user_data(ctx.db, ctx.user_id)
    except Exception as e:
        logger.exception(
            "delete_user_data failed for user_id=%s; aborting delete: %s",
            ctx.user_id, e,
        )
        raise HTTPException(
            status_code=500, detail="failed to delete user data"
        ) from None

    try:
        delete_clerk_user(ctx.user_id)
    except ClerkDeleteFailed as e:
        logger.error(
            "clerk delete failed for user_id=%s; data already wiped: %s",
            ctx.user_id, e,
        )
        raise HTTPException(
            status_code=500,
            detail="failed to delete clerk user; data was wiped — please retry",
        ) from None

    return Response(status_code=204)
```

- [ ] **Step 12.4: Run the test** — [Owner: Claude]

```bash
pytest tests/test_me_route.py -v
```

Expected: 4 passed.

- [ ] **Step 12.5: Commit** — [Owner: user]

```bash
git add app/routes/me.py tests/test_me_route.py
git commit -m "feat(me): add DELETE /me account-deletion endpoint"
```

---

## Task 13: Wire routers in `main.py`

**Files:**
- Modify: `app/main.py`

- [ ] **Step 13.1: Register the new routers** — [Owner: user]

Edit `app/main.py`:

```python
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routes import ai as ai_routes
from app.routes import insights as insights_routes
from app.routes import me as me_routes
from app.routes import webhooks as webhook_routes


def _parse_origins(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [o.strip() for o in raw.split(",") if o.strip()]


app = FastAPI(
    title="finance-insights-engine",
    description="A financial insights engine that uses AI to analyze transactions and provide insights.",
    version="0.1.0",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_parse_origins(os.getenv("CORS_ORIGINS")),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(insights_routes.router)
app.include_router(ai_routes.router)
app.include_router(me_routes.router)
app.include_router(webhook_routes.router)
```

- [ ] **Step 13.2: Boot the app** — [Owner: user]

```bash
uvicorn app.main:app --reload --port 8000
```

Then in another terminal:

```bash
curl -s http://localhost:8000/openapi.json | python -c "import json,sys; print('\n'.join(sorted(json.load(sys.stdin)['paths'].keys())))"
```

Expected output includes `/me` and `/webhooks/clerk`.

- [ ] **Step 13.3: Run the full test suite** — [Owner: Claude]

```bash
pytest -v
```

Expected: all new tests pass; pre-existing tests pass (modulo any pre-existing breakage in `test_deps.py` from the unrelated Clerk migration).

- [ ] **Step 13.4: Commit** — [Owner: user]

```bash
git add app/main.py
git commit -m "feat(main): register me and webhook routers"
```

---

## Task 14: End-to-end smoke test (manual)

**Files:**
- None — operator actions only.

- [ ] **Step 14.1: Configure Clerk Dashboard** — [Owner: user]

1. **Sessions → Customize session token**: add the claims block:
   ```json
   {
     "email": "{{user.primary_email_address}}",
     "first_name": "{{user.first_name}}"
   }
   ```
2. **Webhooks → Add endpoint**:
   - URL: `https://<your-deployed-host>/webhooks/clerk`
   - Subscribed events: `user.created`, `user.deleted`
   - Copy the signing secret and put it in `.env` as `CLERK_WEBHOOK_SECRET`.
3. **API Keys**: copy the Backend API secret into `.env` as `CLERK_SECRET_KEY`.

- [ ] **Step 14.2: Verify Resend domain** — [Owner: user]

In Resend Dashboard → Domains, add and verify the domain you'll send from. Update `EMAIL_FROM_ADDRESS` in `.env` accordingly. Skip if continuing to use `onboarding@resend.dev` while developing.

- [ ] **Step 14.3: Test welcome email end-to-end** — [Owner: user]

1. In your Clerk staging env, sign up with a real email you control.
2. Check the inbox — welcome email arrives within ~30s.
3. Check `webhook_events` in Supabase — one row for the `user.created` `svix-id`.

If no email arrives:
- Tail the FastAPI logs for "Resend send failed" or "welcome email send failed".
- Check Resend Dashboard → Logs.

- [ ] **Step 14.4: Test goodbye + cleanup end-to-end** — [Owner: user]

1. Sign in as the same user; mint a JWT (use `scripts/dev_token.py` if applicable, or read it from Clerk).
2. `curl -X DELETE -H "Authorization: Bearer <token>" https://<your-host>/me` — expect `204`.
3. Inbox: goodbye email arrives.
4. Supabase: rows for that `user_id` are gone across `transactions`, `budgets`, `allocations`, `goals`, `debts`, `recurring_transactions`.
5. Clerk Dashboard: user no longer exists.
6. `webhook_events` table: a `user.deleted` row appears (the webhook fired after Clerk deletion).

- [ ] **Step 14.5: Test idempotency** — [Owner: user]

In Clerk Dashboard → Webhooks → your endpoint → recent deliveries → click "Resend" on a `user.created` delivery.
- The endpoint returns 200.
- No duplicate email is sent (logs show short-circuit on duplicate `svix-id`).

---

## Self-review notes

Before declaring this plan complete, the following items were checked against the spec:

- **Welcome flow** — Tasks 3, 9, 10, 11.
- **Goodbye flow (`DELETE /me`)** — Tasks 4, 5, 7, 8, 9, 10, 12.
- **`user.deleted` webhook cleanup** — Tasks 6, 7, 11.
- **Idempotency (`webhook_events` table + dedup)** — Tasks 2, 6, 11.
- **Service-role client for webhook path** — Task 6 (`build_service_client`), Task 11 (used).
- **Email-failure-does-not-block-deletion in `DELETE /me`** — Task 12, test `test_email_failure_still_proceeds`.
- **Data-cleanup-failure-blocks-Clerk-delete** — Task 12, test `test_data_cleanup_failure_blocks_clerk_delete`.
- **Missing `email` claim → 401** — Task 5, test `test_missing_email_claim_is_401`.
- **Clerk Dashboard config (session-token customization, webhook subscription, API keys)** — Task 14.
- **Out-of-scope items** (in-app inbox, budget alerts, cron) — explicitly absent from all tasks.
