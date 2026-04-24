# Supabase JWT Auth — Design

> **Superseded 2026-04-23** — see [clerk-rs256-jwks-migration-design.md](./2026-04-23-clerk-rs256-jwks-migration-design.md). This document is kept as history; the current auth model is RS256/JWKS with Clerk, not HS256 with a shared Supabase secret.

**Date:** 2026-04-16
**Status:** Superseded by `2026-04-23-clerk-rs256-jwks-migration-design.md`

## Problem

`app/routes/deps.py:12` reads an `x-user-id` header verbatim and returns it as the current user:

```python
def get_current_user(x_user_id: Annotated[str, Header()] | None = Header(default=None)) -> str:
    if not x_user_id:
        raise HTTPException(status_code=401, detail="missing x-user-id header")
    return x_user_id
```

Meanwhile `app/db/client.py:33` builds a single cached Supabase client using the **service key**, which bypasses Row-Level Security. The net effect: any client that sends `x-user-id: <victim_user_id>` can read that user's budgets, transactions, goals, and debts. This is a classic Insecure Direct Object Reference (IDOR) — no SQL injection required, just header spoofing.

## Goal

Replace the header-stub auth with cryptographically-verified Supabase JWTs, and stop using the service key so that Postgres RLS policies enforce access as a second line of defense. The backend should never trust a `user_id` that came from the client; it must derive it from a validated token.

## Approach

**Defense in depth, two independent layers:**

1. **At the edge** — validate the Supabase-issued JWT locally (HS256 signature check using `SUPABASE_JWT_SECRET`) and extract `sub` as the `user_id`. Bad tokens never reach the database.
2. **At the database** — run every query under the user's JWT (via `postgrest.auth(token)`) so Supabase's RLS policies (already in place) reject any row the user doesn't own. The service-key client is removed entirely.

The validated `user_id` and a per-request user-scoped DB client are bundled into a single `UserContext` object that flows through the stack.

## Architecture

```
Client
  │  Authorization: Bearer <supabase-jwt>
  ▼
FastAPI route
  │
  └─ Depends(get_user_ctx) → UserContext(user_id, db)
        │
        │  • Extract bearer token from Authorization header (401 if malformed)
        │  • jwt.decode(token, SUPABASE_JWT_SECRET, algorithms=["HS256"],
        │                audience="authenticated",
        │                options={"require": ["exp", "sub", "aud"]})
        │    → 401 on invalid/expired/missing-claims
        │  • Build Supabase client, call client.postgrest.auth(token)
        │
        ▼
  fetch_*(ctx, ...) in app/db/client.py
        │  Queries include .eq("user_id", ctx.user_id) as belt-and-suspenders
        ▼
  Supabase / Postgres
        │  RLS policies enforce auth.uid() = user_id
```

**What's removed:**
- `SUPABASE_SERVICE_KEY` env var and the `get_supabase()` cached client.
- The `x-user-id` header path.

**What's added:**
- `SUPABASE_JWT_SECRET` env var (found in Supabase dashboard → Settings → API → JWT Secret).
- PyJWT dependency.
- `UserContext` dataclass, `get_user_ctx` FastAPI dependency, `build_user_client` helper.

## Components

### `app/db/client.py`

- Add `supabase_jwt_secret: str` to `Settings`.
- Remove `supabase_service_key` from `Settings`.
- Remove `get_supabase()` (the lru-cached service-key client).
- Add `build_user_client(access_token: str) -> Client`:
  ```python
  def build_user_client(access_token: str) -> Client:
      s = get_settings()
      client = create_client(s.supabase_url, s.supabase_anon_key)
      client.postgrest.auth(access_token)
      return client
  ```
  Note: we use the Supabase **anon key** (public) as the client's baseline, then attach the user's JWT via `postgrest.auth()`. The anon key alone has no RLS-granted access; the JWT is what unlocks rows.
- Add `supabase_anon_key: str` to `Settings`.
- All `fetch_*` functions change signature from `(user_id: str, ..., db: Client | None = None)` to `(ctx: UserContext, ...)`. The `.eq("user_id", ctx.user_id)` filter stays as defense-in-depth alongside RLS. The `db=None` fallback is removed — there is no longer a shared client to fall back to.
- Imports `UserContext` from `app/context.py` (see below) so the `db` layer doesn't depend on the `routes` layer.

### `app/context.py` (new)

```python
from dataclasses import dataclass
from supabase import Client

@dataclass(frozen=True)
class UserContext:
    user_id: str
    db: Client
```

### `app/routes/deps.py` (rewrite)

Replace the entire file with:

```python
import jwt
from fastapi import Header, HTTPException
from typing import Annotated

from app.context import UserContext
from app.db.client import build_user_client, get_settings


def _extract_bearer(authorization: str | None) -> str:
    if not authorization:
        raise HTTPException(401, "missing authorization header")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(401, "invalid authorization header")
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
        raise HTTPException(401, "token expired") from None
    except jwt.InvalidTokenError:
        raise HTTPException(401, "invalid token") from None

    return UserContext(user_id=payload["sub"], db=build_user_client(token))
```

### `app/routes/insights.py`

- Replace the two-dependency pattern with a single `ctx: Annotated[UserContext, Depends(get_user_ctx)]`.
- Pass `ctx` to every `fetch_*` call instead of `(user_id, ..., budget_id)`.

### `app/main.py`

- Tighten CORS: replace `allow_origins=["*"]` with a list read from the `CORS_ORIGINS` env var (already in `.env.example`). Parse as comma-separated. Keep `allow_credentials=True`, `allow_methods=["*"]`, `allow_headers=["*"]`.

### `pyproject.toml` / requirements

- Add `pyjwt` (minimum version that includes `InvalidTokenError` subclasses — any recent release is fine).

### `.env.example`

- Remove: `SUPABASE_SERVICE_KEY`.
- Add: `SUPABASE_JWT_SECRET`, `SUPABASE_ANON_KEY`.

## Data flow (one request)

1. Client sends `GET /insights?budget_id=…&window=1m` with `Authorization: Bearer <jwt>`.
2. FastAPI resolves `get_user_ctx`:
   - Extracts bearer token from the header.
   - `jwt.decode` verifies signature (HS256), expiry, audience (`"authenticated"`), and presence of `exp`/`sub`/`aud`.
   - Builds a Supabase client with the anon key, attaches the user's JWT via `postgrest.auth(token)`.
   - Returns `UserContext(user_id=payload["sub"], db=client)`.
3. Route calls `fetch_budget(ctx, q.budget_id)`, `fetch_transactions(ctx, …)`, `fetch_goals(ctx)`.
4. Each query hits PostgREST carrying the user's JWT. RLS enforces ownership; the explicit `.eq("user_id", ctx.user_id)` filter is a second layer.
5. `build_summary` runs over the fetched rows (engine is unchanged and unaware of auth).
6. `InsightsResponse` returned.

## Error handling

All auth failures return a neutral `detail` string and never leak decode details to the client. Internally we can log the exception class (not the token, not the claims).

| Condition | Status | `detail` |
|---|---|---|
| `Authorization` header missing | 401 | `missing authorization header` |
| Header not `Bearer <token>` (wrong scheme, empty token) | 401 | `invalid authorization header` |
| `jwt.ExpiredSignatureError` | 401 | `token expired` |
| Any other `jwt.InvalidTokenError` (bad signature, wrong audience, missing required claims) | 401 | `invalid token` |
| Token valid, budget missing or not owned (`BudgetNotFound`) | 404 | `budget not found` |
| Any other DB error | 500 | `internal error` (FastAPI default) |

**Rules:**
- Never log or echo raw tokens or `Authorization` headers.
- Do not catch `Exception` in `get_user_ctx` — unexpected errors should 500, not masquerade as 401.
- Missing `SUPABASE_JWT_SECRET`, `SUPABASE_ANON_KEY`, or `SUPABASE_URL` are boot-time failures (pydantic-settings raises); the app refuses to start without them.

## Testing

### `tests/test_deps.py` (new)

Unit test `get_user_ctx` using real PyJWT-minted tokens against a test secret — no mocking of `jwt.decode`.

| Case | Expectation |
|---|---|
| Valid token | Returns `UserContext` with matching `user_id` |
| Missing `Authorization` header | 401 `missing authorization header` |
| Header not `Bearer <token>` | 401 `invalid authorization header` |
| Token signed with wrong secret | 401 `invalid token` |
| Token `exp` in the past | 401 `token expired` |
| Token `aud` != `"authenticated"` | 401 `invalid token` |
| Token missing `sub` claim | 401 `invalid token` |

Fixtures:
- `jwt_secret` — fixed test secret string.
- `make_token(claims: dict = None, secret: str | None = None, exp_delta: int = 3600) -> str` — helper that builds tokens with sane defaults (`aud="authenticated"`, `sub="test-user"`, `exp=now+1h`) and lets each test override.

The `build_user_client` call inside `get_user_ctx` needs to be mockable in these tests (so they don't try to reach Supabase). Approach: patch `app.routes.deps.build_user_client` to return a sentinel `FakeDB` so `UserContext.db` is populated but not real.

### `tests/test_insights_route.py` (new)

Use FastAPI's `TestClient` with dependency overrides (`app.dependency_overrides[get_user_ctx] = lambda: UserContext(user_id="u1", db=FakeDB(...))`). `FakeDB` mimics the chain `db.table(...).select(...).eq(...).gte(...).lte(...).execute()` and returns canned rows.

| Case | Expectation |
|---|---|
| Override provides a valid ctx | 200, body matches `InsightsResponse` shape |
| No override, no `Authorization` header | 401 |
| `FakeDB` returns no budget row | 404 `budget not found` |

### `tests/test_insights_engine.py`

Unchanged. Engine functions never took `user_id` or `db`; the auth change does not ripple into them.

### Not tested (assumptions)

- RLS policy correctness is a Supabase concern, verified in the dashboard / SQL, not Python tests.
- PyJWT's own decode behavior is not re-tested.

## Assumptions

- Supabase Auth is already in use on the frontend; every legitimate request arrives with a Supabase-issued JWT.
- RLS is enabled on all user-scoped tables (`transactions`, `budgets`, `allocations`, `goals`, `debts`, `recurring_transactions`) with policies that compare `auth.uid()` to `user_id`.
- Supabase issues HS256-signed JWTs (the default; changing to RS256 would require swapping `algorithms` and using the public JWKS instead of a shared secret — out of scope).
- `categories` is shared reference data and either has a public RLS policy or is not user-scoped; this spec does not alter it.

## Out of scope

- Token refresh / rotation — clients manage that via the Supabase JS SDK.
- Admin / cron paths that need service-key access — none exist today; if added later, they get their own module and env var.
- RS256 / JWKS-based validation.
- Rate limiting, audit logging.
- Cookie-based session auth.

## Migration notes

- Any existing tests or local scripts that send `x-user-id` will break and must be updated to send a bearer token.
- Include a small dev helper `scripts/dev_token.py` that mints a local HS256 token with `SUPABASE_JWT_SECRET` for manual `curl` testing. Minted tokens will not pass Supabase's server-side checks in production — they're strictly for the local backend.
- The developer's `.env` needs `SUPABASE_JWT_SECRET` and `SUPABASE_ANON_KEY` added; `SUPABASE_SERVICE_KEY` can be removed.
