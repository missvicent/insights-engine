# Supabase JWT Auth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the spoofable `x-user-id` header stub with verified Supabase JWTs and swap the service-key Supabase client for per-request user-scoped clients, so RLS enforces access as a second line of defense.

**Architecture:** A single FastAPI dependency `get_user_ctx` extracts the bearer token, verifies the HS256 signature locally using `SUPABASE_JWT_SECRET`, and returns a frozen `UserContext(user_id, db)` where `db` is a Supabase client with the user's JWT attached via `postgrest.auth(token)`. All `fetch_*` functions shrink from `(user_id, ..., db=None)` to `(ctx, ...)`. The service-key client and env var are removed.

**Tech Stack:** FastAPI, Pydantic v2, supabase-py, PyJWT (new), pytest, Python 3.12.

**Spec:** `docs/superpowers/specs/2026-04-16-supabase-jwt-auth-design.md`

**Conventions:**
- No automatic commits. Each task ends with a `git add` + `git commit` step that the user reviews before running, per user preference.
- Commit messages follow Conventional Commits (`feat:`, `refactor:`, `test:`, `chore:`, `fix:`), matching the repo's recent history.
- Line length 88, ruff-compliant.
- Tests never hit real Supabase; they use locally-minted JWTs and an in-memory `FakeDB`.

---

## File map

| File | Role | Action |
|---|---|---|
| `requirements.txt` | Runtime deps | Modify — add `pyjwt` |
| `app/db/client.py` | DB access + Settings | Modify — add `supabase_jwt_secret` + `supabase_anon_key`, add `build_user_client`, migrate all `fetch_*` to `UserContext`, remove `get_supabase` + `supabase_service_key` |
| `app/context.py` | Shared request context | Create — `UserContext` dataclass |
| `app/routes/deps.py` | Route dependencies | Rewrite — `get_user_ctx` with JWT validation |
| `app/routes/insights.py` | `/insights` route | Modify — use `get_user_ctx`, pass `ctx` to `fetch_*` |
| `app/main.py` | App wiring | Modify — read `CORS_ORIGINS` from env instead of `["*"]` |
| `.env.example` | Env template | Modify — remove service key, add JWT secret + anon key |
| `scripts/dev_token.py` | Dev helper | Create — mint local HS256 token for `curl` testing |
| `tests/conftest.py` | Shared factories | Modify — add `jwt_secret`, `make_token`, `FakeDB`, `make_user_ctx` helpers |
| `tests/test_deps.py` | Auth dep tests | Create |
| `tests/test_insights_route.py` | Route integration tests | Create |

---

## Task 1: Add PyJWT dependency and new Settings fields (additive)

**Why this is first:** The JWT dependency and `Settings` fields are prerequisites for every later task. We keep `supabase_service_key` and `get_supabase()` alive for now so the tree stays runnable; they're removed in Task 8.

**Files:**
- Modify: `requirements.txt`
- Modify: `app/db/client.py:17-24` (Settings class)

- [ ] **Step 1: Add PyJWT to requirements**

Append to `requirements.txt`:

```
pyjwt==2.9.0
```

Final file:

```
fastapi==0.115.0
uvicorn[standard]==0.30.6
pydantic==2.9.2
pydantic-settings==2.5.2
supabase==2.9.0
pandas==2.2.3
litellm==1.55.0
python-dotenv==1.0.1
httpx==0.27.2
pyjwt==2.9.0
```

- [ ] **Step 2: Install**

Run: `pip install -r requirements.txt`
Expected: `Successfully installed pyjwt-2.9.0` (or already satisfied).

- [ ] **Step 3: Verify import**

Run: `python -c "import jwt; print(jwt.__version__)"`
Expected: `2.9.0`.

- [ ] **Step 4: Add Settings fields**

Edit `app/db/client.py`. Replace the `Settings` class (lines 17-24) with:

```python
class Settings(BaseSettings):
    supabase_url: str
    supabase_service_key: str  # still used by get_supabase(); removed in Task 8
    supabase_anon_key: str
    supabase_jwt_secret: str

    class Config:
        env_file = ".env"
        extra = "ignore"
```

- [ ] **Step 5: Update local `.env`**

Add `SUPABASE_ANON_KEY=<your anon key>` and `SUPABASE_JWT_SECRET=<your jwt secret>` to your local `.env`. Find these in Supabase dashboard → Settings → API. (The `.env.example` update itself is Task 10.)

- [ ] **Step 6: Verify the app still boots**

Run: `python -c "from app.db.client import get_settings; s = get_settings(); print(bool(s.supabase_jwt_secret), bool(s.supabase_anon_key))"`
Expected: `True True`. If you see a pydantic `ValidationError`, your `.env` is missing one of the new fields.

- [ ] **Step 7: Commit**

```bash
git add requirements.txt app/db/client.py
git commit -m "chore(deps): add pyjwt and supabase anon/jwt settings"
```

---

## Task 2: Create `UserContext` module

**Files:**
- Create: `app/context.py`

- [ ] **Step 1: Write the module**

Create `app/context.py`:

```python
"""Request-scoped handle bundling a verified user id with a user-scoped DB client.

`UserContext` is the shared currency between `routes/` and `db/`: the route
dependency builds it from a validated JWT, and every `fetch_*` function takes
it as its first argument.
"""

from dataclasses import dataclass

from supabase import Client


@dataclass(frozen=True)
class UserContext:
    user_id: str
    db: Client
```

- [ ] **Step 2: Smoke-test construction**

Run: `python -c "from app.context import UserContext; c = UserContext(user_id='u1', db=None); print(c.user_id)"`
Expected: `u1` (the `db=None` is only for this smoke test — we'd never construct it this way in real code).

- [ ] **Step 3: Commit**

```bash
git add app/context.py
git commit -m "feat(context): add UserContext dataclass"
```

---

## Task 3: Add `build_user_client` helper (additive, `get_supabase` untouched)

**Files:**
- Modify: `app/db/client.py` — insert a new function after `get_supabase()` (after line 36)

- [ ] **Step 1: Add the helper**

Edit `app/db/client.py`. After the existing `get_supabase()` function (end at line 36), insert:

```python
def build_user_client(access_token: str) -> Client:
    """Build a per-request Supabase client authenticated as the end user.

    The anon key is the client's baseline (public, no RLS grants), and the
    user's JWT is attached via postgrest.auth so every query runs under
    auth.uid() and RLS enforces row-level access.
    """
    s = get_settings()
    client = create_client(s.supabase_url, s.supabase_anon_key)
    client.postgrest.auth(access_token)
    return client
```

- [ ] **Step 2: Smoke-test that it constructs (no network call)**

Run: `python -c "from app.db.client import build_user_client; c = build_user_client('fake.token.here'); print(type(c).__name__)"`
Expected: `Client` (or `SyncClient` depending on supabase-py internals). No network call is made — `postgrest.auth()` only sets a header.

- [ ] **Step 3: Commit**

```bash
git add app/db/client.py
git commit -m "feat(db): add build_user_client for per-request user-scoped Supabase client"
```

---

## Task 4: Write failing JWT validation tests

**TDD gate:** These tests must fail (ImportError or similar) before Task 5 writes the implementation.

**Files:**
- Modify: `tests/conftest.py` — add `jwt_secret` and `make_token` fixtures
- Create: `tests/test_deps.py`

- [ ] **Step 1: Add fixtures to `conftest.py`**

Append to `tests/conftest.py` (after the existing factories):

```python
import time
from typing import Any

import jwt as pyjwt
import pytest


@pytest.fixture
def jwt_secret() -> str:
    return "test-secret-do-not-use-in-prod"


@pytest.fixture
def make_token(jwt_secret: str):
    """Build a signed JWT with sensible defaults, overrideable per-test."""

    def _make(
        claims: dict[str, Any] | None = None,
        secret: str | None = None,
        algorithm: str = "HS256",
        exp_delta: int = 3600,
        audience: str | None = "authenticated",
        sub: str | None = "test-user",
        omit: tuple[str, ...] = (),
    ) -> str:
        now = int(time.time())
        payload: dict[str, Any] = {
            "iat": now,
            "exp": now + exp_delta,
            "sub": sub,
            "aud": audience,
        }
        if claims:
            payload.update(claims)
        for key in omit:
            payload.pop(key, None)
        return pyjwt.encode(payload, secret or jwt_secret, algorithm=algorithm)

    return _make
```

- [ ] **Step 2: Write the test file**

Create `tests/test_deps.py`:

```python
"""Unit tests for app/routes/deps.py.

We mint real JWTs against a test secret — no mocking of jwt.decode. The
Supabase client returned inside UserContext is replaced with a sentinel so
tests don't hit the network.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import HTTPException

from app.routes.deps import get_user_ctx


class _FakeDB:
    """Sentinel stand-in for the Supabase client."""


@pytest.fixture(autouse=True)
def _patch_build_user_client(jwt_secret):
    """Stop get_user_ctx from building a real Supabase client in tests."""
    with patch("app.routes.deps.build_user_client", return_value=_FakeDB()):
        yield


@pytest.fixture(autouse=True)
def _patch_settings(jwt_secret):
    """Point get_user_ctx at the test JWT secret."""
    with patch("app.routes.deps.get_settings") as m:
        m.return_value.supabase_jwt_secret = jwt_secret
        yield


def _call(header_value: str | None):
    return get_user_ctx(authorization=header_value)


class TestGetUserCtx:
    def test_valid_token_returns_user_context(self, make_token):
        token = make_token(sub="user-42")
        ctx = _call(f"Bearer {token}")
        assert ctx.user_id == "user-42"
        assert isinstance(ctx.db, _FakeDB)

    def test_missing_header_is_401(self):
        with pytest.raises(HTTPException) as exc:
            _call(None)
        assert exc.value.status_code == 401
        assert exc.value.detail == "missing authorization header"

    def test_wrong_scheme_is_401(self, make_token):
        token = make_token()
        with pytest.raises(HTTPException) as exc:
            _call(f"Basic {token}")
        assert exc.value.status_code == 401
        assert exc.value.detail == "invalid authorization header"

    def test_bearer_without_token_is_401(self):
        with pytest.raises(HTTPException) as exc:
            _call("Bearer ")
        assert exc.value.status_code == 401
        assert exc.value.detail == "invalid authorization header"

    def test_wrong_secret_is_401(self, make_token):
        token = make_token(secret="not-the-real-secret")
        with pytest.raises(HTTPException) as exc:
            _call(f"Bearer {token}")
        assert exc.value.status_code == 401
        assert exc.value.detail == "invalid token"

    def test_expired_token_is_401(self, make_token):
        token = make_token(exp_delta=-10)
        with pytest.raises(HTTPException) as exc:
            _call(f"Bearer {token}")
        assert exc.value.status_code == 401
        assert exc.value.detail == "token expired"

    def test_wrong_audience_is_401(self, make_token):
        token = make_token(audience="service_role")
        with pytest.raises(HTTPException) as exc:
            _call(f"Bearer {token}")
        assert exc.value.status_code == 401
        assert exc.value.detail == "invalid token"

    def test_missing_sub_is_401(self, make_token):
        token = make_token(omit=("sub",))
        with pytest.raises(HTTPException) as exc:
            _call(f"Bearer {token}")
        assert exc.value.status_code == 401
        assert exc.value.detail == "invalid token"
```

- [ ] **Step 3: Run tests — verify they fail**

Run: `pytest tests/test_deps.py -v`
Expected: `ImportError` on `from app.routes.deps import get_user_ctx` — the function doesn't exist yet. That's the TDD red state we want.

- [ ] **Step 4: Commit**

```bash
git add tests/conftest.py tests/test_deps.py
git commit -m "test(deps): add failing tests for get_user_ctx JWT validation"
```

---

## Task 5: Implement `get_user_ctx` (tests go green)

**Files:**
- Rewrite: `app/routes/deps.py`

- [ ] **Step 1: Rewrite `deps.py`**

Replace the entire contents of `app/routes/deps.py` with:

```python
"""FastAPI dependencies for the routes layer.

`get_user_ctx` is the single edge-of-system auth dependency: it verifies a
Supabase-issued JWT and bundles the verified user id with a per-request
user-scoped Supabase client.
"""

from typing import Annotated

import jwt
from fastapi import Header, HTTPException

from app.context import UserContext
from app.db.client import build_user_client, get_settings


def _extract_bearer(authorization: str | None) -> str:
    if not authorization:
        raise HTTPException(status_code=401, detail="missing authorization header")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="invalid authorization header")
    return token


def get_user_ctx(
    authorization: Annotated[str | None, Header()] = None,
) -> UserContext:
    token = _extract_bearer(authorization)
    try:
        payload = jwt.decode(
            token,
            get_settings().supabase_jwt_secret,
            algorithms=["HS256"],
            audience="authenticated",
            options={"require": ["exp", "sub", "aud"]},
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="token expired") from None
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="invalid token") from None

    return UserContext(user_id=payload["sub"], db=build_user_client(token))
```

Note: the previous `get_current_user` function is gone. The `insights.py` route still imports it — that's addressed in Task 7. Between Task 5 and Task 7 the app will not boot; tests run green in isolation but `uvicorn` would fail with `ImportError`. Keep Tasks 6+7 adjacent.

- [ ] **Step 2: Run the deps tests — verify they pass**

Run: `pytest tests/test_deps.py -v`
Expected: 8 passed.

- [ ] **Step 3: Commit**

```bash
git add app/routes/deps.py
git commit -m "feat(deps): implement get_user_ctx with Supabase JWT validation"
```

---

## Task 6: Write failing route integration test (with `FakeDB`)

**TDD gate:** This test must fail before Task 7 rewires the route + `fetch_*` signatures.

**Files:**
- Modify: `tests/conftest.py` — add `FakeDB` and `make_user_ctx` helpers
- Create: `tests/test_insights_route.py`

- [ ] **Step 1: Add `FakeDB` and `make_user_ctx` to `conftest.py`**

Append to `tests/conftest.py`:

```python
from app.context import UserContext


class FakeQuery:
    """Chainable no-op query; returns an object with `.data` when executed.

    Mirrors the subset of the supabase-py builder that db/client.py uses:
    select, eq, gte, lte, limit, execute. Filters are recorded but ignored;
    the caller seeds rows per (schema, table).
    """

    def __init__(self, rows: list[dict]):
        self._rows = rows

    def select(self, *_a, **_kw): return self
    def eq(self, *_a, **_kw): return self
    def gte(self, *_a, **_kw): return self
    def lte(self, *_a, **_kw): return self
    def limit(self, *_a, **_kw): return self

    def execute(self):
        class _Resp:
            data = self._rows
        return _Resp()


class FakeDB:
    """Minimal stand-in for a Supabase client. `table(name)` returns a
    FakeQuery over whatever rows the test seeded for that table."""

    def __init__(self, tables: dict[str, list[dict]] | None = None):
        self._tables = tables or {}

    def table(self, name: str) -> FakeQuery:
        return FakeQuery(self._tables.get(name, []))


def make_user_ctx(user_id: str = "user-1", tables: dict | None = None) -> UserContext:
    return UserContext(user_id=user_id, db=FakeDB(tables))
```

- [ ] **Step 2: Write the route test**

Create `tests/test_insights_route.py`:

```python
"""Integration tests for GET /insights.

We override the `get_user_ctx` dependency with a UserContext wrapping a
FakeDB, so the route runs end-to-end without touching real Supabase.
"""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routes.deps import get_user_ctx
from tests.conftest import make_user_ctx


def _budget_row(user_id: str = "user-1") -> dict:
    return {
        "id": "budget-1",
        "user_id": user_id,
        "name": "April 2026",
        "period": "monthly",
        "amount": 5000.0,
        "start_date": date(2026, 4, 1).isoformat(),
        "end_date": date(2026, 4, 30).isoformat(),
        "is_active": True,
    }


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


class TestGetInsights:
    def test_returns_200_with_valid_ctx(self, client):
        app.dependency_overrides[get_user_ctx] = lambda: make_user_ctx(
            tables={
                "budgets": [_budget_row()],
                "allocations": [],
                "transactions": [],
                "goals": [],
            }
        )
        resp = client.get("/insights?budget_id=budget-1&window=1m")
        assert resp.status_code == 200
        body = resp.json()
        assert body["summary"]["budget_id"] == "budget-1"

    def test_missing_authorization_is_401(self, client):
        resp = client.get("/insights?budget_id=budget-1&window=1m")
        assert resp.status_code == 401

    def test_budget_not_found_is_404(self, client):
        app.dependency_overrides[get_user_ctx] = lambda: make_user_ctx(
            tables={"budgets": []}
        )
        resp = client.get("/insights?budget_id=missing&window=1m")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "budget not found"
```

- [ ] **Step 3: Run — verify failure**

Run: `pytest tests/test_insights_route.py -v`
Expected: All tests error out. Likely `ImportError` on `app.main` (because `routes/insights.py` still references the now-deleted `get_current_user` from Task 5), or a `TypeError` on `fetch_*` receiving a `UserContext` when it expects `(user_id, ...)`. Either failure is acceptable — Task 7 fixes both.

- [ ] **Step 4: Commit**

```bash
git add tests/conftest.py tests/test_insights_route.py
git commit -m "test(insights): add failing route integration tests with FakeDB"
```

---

## Task 7: Migrate `fetch_*` signatures and rewire the route (tests go green)

This is the largest task. It's a single atomic change because the route and the DB layer move together.

**Files:**
- Modify: `app/db/client.py` — rewrite all `fetch_*` function signatures to take `UserContext`
- Modify: `app/routes/insights.py` — use `get_user_ctx`, pass `ctx` through

- [ ] **Step 1: Migrate `fetch_transactions`**

In `app/db/client.py`, add at the top (with the other local imports):

```python
from app.context import UserContext
```

Replace the `fetch_transactions` function (currently lines 39-79) with:

```python
def fetch_transactions(
    ctx: UserContext,
    start: date,
    end: date,
    budget_id: str | None = None,
) -> list[TransactionRow]:
    """Fetch transactions for `ctx.user_id` between `start` and `end` (inclusive).

    When `budget_id` is provided, results are scoped to that budget.
    RLS enforces ownership; the explicit user_id filter is belt-and-suspenders.
    """
    query = (
        ctx.db.table("transactions")
        .select("*, categories(name, icon, color)")
        .eq("user_id", ctx.user_id)
        .gte("transaction_date", start.isoformat())
        .lte("transaction_date", end.isoformat())
    )
    if budget_id is not None:
        query = query.eq("budget_id", budget_id)
    response = query.execute()

    rows = []
    for row in response.data:
        cat = row.pop("categories", None) or {}
        rows.append(
            TransactionRow(
                **row,
                category_name=cat.get("name"),
                category_icon=cat.get("icon"),
                category_color=cat.get("color"),
            )
        )
    return rows
```

- [ ] **Step 2: Migrate `fetch_budget`**

Replace the `fetch_budget` function (currently lines 86-124) with:

```python
def fetch_budget(
    ctx: UserContext,
    budget_id: str,
) -> tuple[BudgetRow, list[AllocationRow]]:
    """Fetch one budget (authorized to ctx.user_id) and its allocations.

    Raises BudgetNotFound when the row is missing or not owned by the user.
    """
    budget_response = (
        ctx.db.table("budgets")
        .select("*")
        .eq("id", budget_id)
        .eq("user_id", ctx.user_id)
        .limit(1)
        .execute()
    )

    if not budget_response.data:
        raise BudgetNotFound(budget_id)

    budget = BudgetRow(**budget_response.data[0])

    alloc_response = (
        ctx.db.table("allocations")
        .select("*, categories(name)")
        .eq("budget_id", budget.id)
        .execute()
    )

    allocations: list[AllocationRow] = []
    for alloc in alloc_response.data:
        cat = alloc.pop("categories", None) or {}
        allocations.append(AllocationRow(**alloc, category_name=cat.get("name")))

    return budget, allocations
```

- [ ] **Step 3: Migrate `fetch_goals`, `fetch_debt`, `fetch_recurring`**

Replace the three remaining `fetch_*` functions (currently lines 127-181) with:

```python
def fetch_goals(ctx: UserContext) -> list[GoalRow]:
    response = (
        ctx.db.table("goals")
        .select("id, name, target_amount, current_amount, target_date, is_achieved")
        .eq("user_id", ctx.user_id)
        .eq("is_achieved", False)
        .execute()
    )
    return [GoalRow(**row) for row in response.data]


def fetch_debt(ctx: UserContext) -> list[DebtRow]:
    response = (
        ctx.db.table("debts")
        .select(
            "id, name, type, current_balance, interest_rate, minimum_payment, is_active"
        )
        .eq("user_id", ctx.user_id)
        .execute()
    )
    return [DebtRow(**row) for row in response.data]


def fetch_recurring(ctx: UserContext) -> list[RecurringRow]:
    response = (
        ctx.db.table("recurring_transactions")
        .select(
            "id, name, type, amount, frequency, next_occurrence, is_active, is_paused"
        )
        .eq("user_id", ctx.user_id)
        .eq("is_active", True)
        .execute()
    )
    return [RecurringRow(**row) for row in response.data]
```

- [ ] **Step 4: Rewrite `routes/insights.py`**

Replace the entire contents of `app/routes/insights.py` with:

```python
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.context import UserContext
from app.db.client import (
    BudgetNotFound,
    fetch_budget,
    fetch_goals,
    fetch_transactions,
)
from app.models.schemas import InsightsQuery, InsightsResponse
from app.routes.deps import get_user_ctx
from app.services.insights_engine import build_summary, resolve_window


router = APIRouter()


@router.get("/insights", responses={404: {"description": "Budget not found"}})
def get_insights(
    q: Annotated[InsightsQuery, Depends()],
    ctx: Annotated[UserContext, Depends(get_user_ctx)],
) -> InsightsResponse:
    try:
        budget, allocations = fetch_budget(ctx, q.budget_id)
    except BudgetNotFound:
        raise HTTPException(status_code=404, detail="budget not found") from None

    current_start, current_end, prev_start, prev_end = resolve_window(
        q.window, date.today()
    )
    budget_id = q.budget_id
    current = fetch_transactions(ctx, current_start, current_end, budget_id)
    previous = fetch_transactions(ctx, prev_start, prev_end, budget_id)
    goals = fetch_goals(ctx)

    summary = build_summary(
        budget=budget,
        allocations=allocations,
        current=current,
        previous=previous,
        goals=goals,
        window_start=current_start,
        window_end=current_end,
    )
    return InsightsResponse(summary=summary)
```

- [ ] **Step 5: Run the full test suite**

Run: `pytest -v`
Expected: all tests pass — engine tests unaffected, `test_deps.py` still green, `test_insights_route.py` now green.

- [ ] **Step 6: Smoke-test the app boots**

Run: `python -c "from app.main import app; print(app.title)"`
Expected: `finance-insights-engine`. If you see an `ImportError`, a stale reference to `get_current_user` is still in the tree — grep and fix.

- [ ] **Step 7: Commit**

```bash
git add app/db/client.py app/routes/insights.py
git commit -m "refactor(auth): migrate fetch_* and /insights route to UserContext"
```

---

## Task 8: Remove the service-key client and env field

**Files:**
- Modify: `app/db/client.py` — delete `get_supabase`, remove `supabase_service_key` from `Settings`

- [ ] **Step 1: Confirm no remaining callers**

Run: `grep -rn "get_supabase\|supabase_service_key\|SUPABASE_SERVICE_KEY" app/ tests/ scripts/ 2>/dev/null || true`
Expected: no matches in `app/` or `tests/`. If there are any, stop and migrate them — do not proceed.

- [ ] **Step 2: Delete `get_supabase` and the field**

Edit `app/db/client.py`. Replace the current `Settings` class and the `get_supabase` function (roughly lines 17-36) with:

```python
class Settings(BaseSettings):
    supabase_url: str
    supabase_anon_key: str
    supabase_jwt_secret: str

    class Config:
        env_file = ".env"
        extra = "ignore"


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

The module should no longer reference `supabase_service_key` anywhere. The `from supabase import Client, create_client` import stays (used by `build_user_client`).

- [ ] **Step 3: Run full test suite**

Run: `pytest -v`
Expected: all tests still pass.

- [ ] **Step 4: Smoke-test boot**

Run: `python -c "from app.db.client import get_settings; s = get_settings(); print(s.supabase_url[:10])"`
Expected: first 10 chars of your Supabase URL. No `ValidationError`.

- [ ] **Step 5: Commit**

```bash
git add app/db/client.py
git commit -m "chore(db): remove service-key client and SUPABASE_SERVICE_KEY"
```

---

## Task 9: Tighten CORS

**Files:**
- Modify: `app/main.py`

- [ ] **Step 1: Rewrite `main.py`**

Replace the entire contents of `app/main.py` with:

```python
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routes import insights as insights_routes


def _parse_origins(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [o.strip() for o in raw.split(",") if o.strip()]


app = FastAPI(title="finance-insights-engine")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_parse_origins(os.getenv("CORS_ORIGINS")),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(insights_routes.router)
```

`CORS_ORIGINS` is already in `.env.example` as a comma-separated list (e.g. `http://localhost:3000,http://localhost:5173`) — no template change needed here.

- [ ] **Step 2: Run full test suite**

Run: `pytest -v`
Expected: all tests still pass. `TestClient` does not exercise CORS, so this is mostly a smoke check.

- [ ] **Step 3: Manual spot-check (optional but recommended)**

Start the app: `uvicorn app.main:app --reload`
From another shell: `curl -i -X OPTIONS http://localhost:8000/insights -H "Origin: http://localhost:3000" -H "Access-Control-Request-Method: GET"`
Expected: `access-control-allow-origin: http://localhost:3000` in the response headers (assuming `CORS_ORIGINS` includes it).
From another shell with a bad origin: `curl -i -X OPTIONS http://localhost:8000/insights -H "Origin: https://evil.example" -H "Access-Control-Request-Method: GET"`
Expected: no `access-control-allow-origin` header.

- [ ] **Step 4: Commit**

```bash
git add app/main.py
git commit -m "fix(cors): restrict origins to CORS_ORIGINS env instead of wildcard"
```

---

## Task 10: Update `.env.example`

**Files:**
- Modify: `.env.example`

- [ ] **Step 1: Rewrite**

Replace the entire contents of `.env.example` with:

```
SUPABASE_URL=
SUPABASE_ANON_KEY=
SUPABASE_JWT_SECRET=
AI_MODEL=
ANTHROPIC_API_KEY=
APP_ENV=development
CORS_ORIGINS=http://localhost:3000,http://localhost:5173
```

`SUPABASE_SERVICE_KEY` is gone; `SUPABASE_ANON_KEY` and `SUPABASE_JWT_SECRET` replace it.

- [ ] **Step 2: Commit**

```bash
git add .env.example
git commit -m "chore(env): swap SUPABASE_SERVICE_KEY for anon key + jwt secret"
```

---

## Task 11: Add `scripts/dev_token.py` helper

**Files:**
- Create: `scripts/dev_token.py`

- [ ] **Step 1: Create the script**

```bash
mkdir -p scripts
```

Create `scripts/dev_token.py`:

```python
"""Mint a local HS256 JWT for manual `curl` testing of the insights API.

Reads SUPABASE_JWT_SECRET from .env. The resulting token is signed with the
same secret the backend validates against, so it passes local auth — but it
is NOT a real Supabase-issued token and will not work against production.

Usage:
    python scripts/dev_token.py                 # sub=dev-user, 1h expiry
    python scripts/dev_token.py --sub abc-123   # specific user
    python scripts/dev_token.py --exp 7200      # 2h expiry

Then:
    curl -H "Authorization: Bearer $(python scripts/dev_token.py)" \\
        "http://localhost:8000/insights?budget_id=...&window=1m"
"""

from __future__ import annotations

import argparse
import sys
import time

import jwt
from dotenv import dotenv_values


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sub", default="dev-user", help="JWT sub claim (user id)")
    parser.add_argument("--exp", type=int, default=3600, help="Seconds until expiry")
    args = parser.parse_args()

    env = dotenv_values(".env")
    secret = env.get("SUPABASE_JWT_SECRET")
    if not secret:
        print("error: SUPABASE_JWT_SECRET not set in .env", file=sys.stderr)
        return 1

    now = int(time.time())
    payload = {
        "iat": now,
        "exp": now + args.exp,
        "sub": args.sub,
        "aud": "authenticated",
    }
    print(jwt.encode(payload, secret, algorithm="HS256"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Verify it runs**

Run: `python scripts/dev_token.py --sub test-user`
Expected: a single-line JWT on stdout (starts with `eyJ`). Decoding it at jwt.io with your secret should show `sub=test-user`, `aud=authenticated`, and an `exp` ~1 hour out.

- [ ] **Step 3: End-to-end smoke test against the running app (optional)**

In one shell: `uvicorn app.main:app --reload`
In another:

```bash
TOKEN=$(python scripts/dev_token.py --sub <a-real-user-id-in-your-supabase>)
curl -s "http://localhost:8000/insights?budget_id=<a-real-budget-id>&window=1m" \
    -H "Authorization: Bearer $TOKEN" | head -c 500
```

Expected: a JSON `InsightsResponse`, or a 404 if the budget id doesn't exist for that user. Note: because this token is locally-signed, it will NOT be accepted by Supabase's own auth servers — it only works against our backend, which intentionally validates with the shared secret. Rows returned depend on your RLS policies accepting the `sub` claim.

- [ ] **Step 4: Commit**

```bash
git add scripts/dev_token.py
git commit -m "chore(dev): add local JWT minting script for curl testing"
```

---

## Post-implementation verification

- [ ] **Step 1: Full test suite passes**

Run: `pytest -v`
Expected: all tests pass, including new `test_deps.py` (8 tests) and `test_insights_route.py` (3 tests).

- [ ] **Step 2: Linter clean**

Run: `ruff check app/ tests/ scripts/`
Expected: no issues.

- [ ] **Step 3: No dead references**

Run: `grep -rn "x-user-id\|SUPABASE_SERVICE_KEY\|get_supabase\|get_current_user" app/ tests/ scripts/ 2>/dev/null || true`
Expected: no matches. All four should be fully removed.

- [ ] **Step 4: App boots with real `.env`**

Run: `uvicorn app.main:app --reload`
Expected: starts on `http://127.0.0.1:8000`, no `ValidationError`, no `ImportError`. `Ctrl+C` to stop.

- [ ] **Step 5: 401 without token**

Run: `curl -i http://localhost:8000/insights?budget_id=x&window=1m`
Expected: `HTTP/1.1 401 Unauthorized`, body `{"detail":"missing authorization header"}`.

- [ ] **Step 6: 200 with a dev token (against real Supabase with a real user/budget)**

See Task 11 Step 3 for the exact command.
Expected: `200` JSON, or `404` if the budget doesn't exist for that user. RLS in Supabase is exercised at this step.

---

## Spec coverage self-check

| Spec section | Task(s) |
|---|---|
| Problem / IDOR via `x-user-id` | Resolved by Task 5 (rewrite deps) + Task 7 (remove old dep from routes) |
| Architecture: edge JWT validation | Task 5 |
| Architecture: RLS + user-scoped client | Task 3 (`build_user_client`) + Task 7 (route uses it) |
| Architecture: service key removed | Task 8 |
| Components: `Settings` changes | Task 1 (add) + Task 8 (remove service key) |
| Components: `UserContext` in `app/context.py` | Task 2 |
| Components: `build_user_client` | Task 3 |
| Components: `deps.py` rewrite | Task 5 |
| Components: `fetch_*` signature migration | Task 7 |
| Components: route update | Task 7 |
| Components: CORS tightening | Task 9 |
| Components: PyJWT dep | Task 1 |
| Components: `.env.example` update | Task 10 |
| Error handling table | Task 4 tests cover every row |
| Testing: `test_deps.py` | Task 4 |
| Testing: `test_insights_route.py` | Task 6 |
| Testing: engine tests unchanged | Verified in Task 7 full run |
| Migration: dev_token script | Task 11 |
| Migration: `.env` changes | Task 1 Step 5 (local) + Task 10 (template) |
