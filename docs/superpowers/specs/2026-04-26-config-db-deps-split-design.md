# Config / DB / Deps Split — Design

**Date:** 2026-04-26
**Status:** Draft
**Scope:** Internal refactor of `app/db/client.py` and `app/routes/deps.py` to separate concerns. No behavior change, no public-API change beyond import paths.

## Motivation

`app/db/client.py` currently does three unrelated jobs:

1. **Settings** — `Settings` Pydantic class + cached `get_settings()` factory.
2. **DB connection factory** — `build_user_client(access_token)`.
3. **DB queries** — six `fetch_*` functions.

This forces unrelated callers to import from `db.client`. Notably, `app/auth/jwks.py` and `app/services/ai_service.py` both pull `get_settings` from `db.client`, even though neither needs the Supabase client. Co-locating settings with DB code also creates a latent import-time dependency: any module that wants config has to import the supabase SDK transitively.

Separately, `app/routes/deps.py` is a FastAPI dependency module, but `routes/` is otherwise reserved for route handlers. Keeping `deps.py` in `routes/` muddles that boundary.

## Goals

- Split `app/db/client.py` so `Settings` lives in its own module.
- Promote `routes/deps.py` to `app/deps.py` so `routes/` only contains route handlers.
- Fix two latent bugs in the existing `Settings` class while moving it.
- Update all import sites and tests.

## Non-goals

- No conversion to FastAPI `Depends(get_settings)`. `get_settings` stays as a direct call at every site (it's already an `lru_cache` singleton; tests already use `patch(...)` for overrides).
- No splitting of the six `fetch_*` query functions — they stay in `app/db/client.py`.
- No move of `app/auth/jwks.py` — stays at its current path.
- No backward-compatibility shims (no re-exports from old paths). Internal code only; just update imports.
- No renames of `UserContext`, `BudgetNotFound`, `build_user_client`, `get_user_ctx`, or any function.

## Target structure

```
app/
├── config.py          # NEW: Settings + get_settings
├── context.py         # unchanged: UserContext
├── deps.py            # NEW: bearer_scheme + get_user_ctx
├── auth/
│   └── jwks.py        # unchanged location; import path of get_settings updates
├── db/
│   ├── __init__.py
│   └── client.py      # build_user_client + fetch_* (Settings stripped out)
├── routes/
│   ├── ai.py
│   └── insights.py    # (deps.py removed from this dir)
├── services/
│   ├── ai_service.py
│   └── insights_engine.py
├── models/
│   └── schemas.py
└── main.py
```

## Public API per module

### `app/config.py` (new)

```python
class Settings(BaseSettings): ...

@lru_cache
def get_settings() -> Settings: ...
```

### `app/db/client.py` (post-split)

```python
def build_user_client(access_token: str) -> Client: ...

class BudgetNotFound(Exception): ...

def fetch_transactions(ctx, start, end, budget_id=None) -> list[TransactionRow]: ...
def fetch_budget(ctx, budget_id) -> tuple[BudgetRow, list[AllocationRow]]: ...
def fetch_goals(ctx) -> list[GoalRow]: ...
def fetch_debt(ctx) -> list[DebtRow]: ...
def fetch_recurring(ctx) -> list[RecurringRow]: ...
```

`build_user_client` calls `get_settings` via `from app.config import get_settings`.

### `app/deps.py` (new, moved from `app/routes/deps.py`)

```python
bearer_scheme = HTTPBearer(...)

def get_user_ctx(credentials) -> UserContext: ...
```

Imports updated to `from app.config import get_settings`. Other internals unchanged.

### `app/auth/jwks.py` (unchanged behavior)

Single line change: `from app.db.client import get_settings` → `from app.config import get_settings`.

### `app/context.py`

Untouched.

## Bug fixes folded into the move

While extracting `Settings` from `db/client.py`, fix two latent issues:

1. **Missing import.** Current code uses `SettingsConfigDict(...)` on line 19 but never imports it. It works only because the duplicate inner `class Config:` provides a fallback. Fix: add `from pydantic_settings import BaseSettings, SettingsConfigDict`.
2. **Duplicate config style.** Lines 19 and 30-32 both configure `env_file=".env", extra="ignore"`. The inner `class Config:` is the pydantic v1 style; the project is on pydantic v2. Drop the inner `Config` class; keep `model_config = SettingsConfigDict(...)`.

After fixes, the `Settings` class in `app/config.py` looks like:

```python
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    anthropic_api_key: str | None = None
    clerk_issuer: str
    supabase_anon_key: str
    supabase_url: str
    resend_api_key: str | None = None

    ai_model: str = "anthropic/claude-haiku-4-5-20251001"
    clerk_jwks_url: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

## Import rewrite map

| File | Old | New |
|---|---|---|
| `app/auth/jwks.py` | `from app.db.client import get_settings` | `from app.config import get_settings` |
| `app/services/ai_service.py` | `from app.db.client import get_settings` | `from app.config import get_settings` |
| `app/routes/insights.py` | `from app.routes.deps import get_user_ctx` | `from app.deps import get_user_ctx` |
| `app/routes/ai.py` | `from app.routes.deps import get_user_ctx` | `from app.deps import get_user_ctx` |
| `tests/test_insights_route.py` | `from app.routes.deps import get_user_ctx` | `from app.deps import get_user_ctx` |
| `tests/test_deps.py` | `from app.routes.deps import get_user_ctx` | `from app.deps import get_user_ctx` |

`app/routes/insights.py` and `app/routes/ai.py` already import `fetch_*` from `app.db.client` — those imports stay as-is.

## Test patch path change

`tests/test_deps.py:33` currently does:

```python
with patch("app.routes.deps.get_settings") as m:
```

Becomes:

```python
with patch("app.deps.get_settings") as m:
```

Same mechanic (patches the local binding in the deps module); only the dotted path changes.

## File operations

1. **Create** `app/config.py` with the cleaned-up `Settings` + `get_settings`.
2. **Edit** `app/db/client.py`: remove the `Settings` class, the `get_settings` function, and the `from pydantic_settings import BaseSettings` import (all unused after the move). Keep `from app.context import UserContext` and the schema imports. Add `from app.config import get_settings` for use in `build_user_client`.
3. **Move** `app/routes/deps.py` → `app/deps.py` (`git mv`), update its `from app.db.client import build_user_client, get_settings` to two imports: `from app.db.client import build_user_client` and `from app.config import get_settings`.
4. **Edit** `app/auth/jwks.py`: update single import.
5. **Edit** `app/services/ai_service.py`: update single import.
6. **Edit** `app/routes/insights.py`: update `get_user_ctx` import path.
7. **Edit** `app/routes/ai.py`: update `get_user_ctx` import path.
8. **Edit** `tests/test_insights_route.py`: update `get_user_ctx` import path.
9. **Edit** `tests/test_deps.py`: update `get_user_ctx` import path AND the `patch(...)` dotted path.

## Verification

- `pytest` — entire suite must pass with no behavior change.
- `python -c "import app.main"` — sanity-check imports resolve.
- `grep -rn "app.db.client.*get_settings\|app.routes.deps" app tests` — should return no matches after the change.

## Risk and rollback

Risk is low: pure rename/move with no logic edits. Rollback is `git revert` of the single PR. The two `Settings` bug fixes are the only behavior-touching changes; both are observable at app boot if broken (the app fails to construct `Settings` and 500s before serving any request).
