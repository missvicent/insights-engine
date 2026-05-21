# Test Helpers Relocation to `conftest.py` — Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `pytest tests/ -q` collect and run cleanly so the Phase 3 dark-launch checkbox in `2026-05-06-account-deletion-simplified.md` can be ticked.

**Architecture:** Lift the shared test factories out of `tests/test_conf.py` into `tests/conftest.py` (the pytest-idiomatic home), drop dead HS256 JWT helpers, rename the trimmed file to `tests/test_config.py`, and collapse the six redundant inline `make_goal` imports inside `tests/test_insights_engine.py`.

**Tech Stack:** Python 3.13, pytest, pydantic v2.

**Spec:** [2026-05-20-test-helpers-conftest-relocation-design.md](../specs/2026-05-20-test-helpers-conftest-relocation-design.md)

---

## File map

- **Modify** `tests/conftest.py` — extend the existing env-`setdefault` module with factories, fake-supabase classes, and `make_user_ctx`.
- **Rename** `tests/test_conf.py` → `tests/test_config.py` (`git mv`); the renamed file keeps only the two `test_cron_shared_secret_*` tests.
- **Modify** `tests/test_insights_engine.py` — add `make_goal` to the module-level import block at line 36; delete six redundant inline imports.
- **No change** to `tests/test_insights_route.py` — its existing `from tests.conftest import make_user_ctx` (line 16) starts resolving once Task 1 lands.

---

## Task 1 — Move factories and fake-supabase helpers into `conftest.py`

**Files:**
- Modify: `tests/conftest.py`

**Why first:** every broken import points at `tests.conftest`. The moment the names exist there, pytest collection unblocks regardless of whether `test_conf.py` still holds duplicates. Test the change in isolation before touching the old file.

- [ ] **Step 1: Confirm the current failure mode**

Run:

```bash
cd "/Users/nilyanvicent/Documents/Documents - Nilyan’s MacBook Pro/practice/personal-budget-api"
ACCOUNT_DELETION_ENABLED=false python -m pytest tests/ -q 2>&1 | tail -20
```

Expected: `ImportError: cannot import name 'make_allocation' from 'tests.conftest'` and `ImportError: cannot import name 'make_user_ctx' from 'tests.conftest'`. Two collection errors. No tests run.

- [ ] **Step 2: Rewrite `tests/conftest.py` to host the helpers**

Replace the whole file contents with:

```python
"""Session-wide pytest setup and shared test factories.

Two responsibilities:

1. Seed harmless defaults for every required Settings field BEFORE any
   `app.*` module is imported. `app.main` constructs `Settings()` at
   import time (for CORS configuration), so test modules that import
   `app.main` would fail collection if any required env var were unset.
   Doing this in `conftest.py` guarantees it runs before pytest imports
   any test module, regardless of collection order or invocation.
2. Provide the shared factory functions (`make_expense`, `make_income`,
   `make_allocation`, `make_budget`, `make_goal`), the fake-Supabase
   pair (`FakeQuery`, `FakeDB`), and `make_user_ctx` — used across
   `test_insights_engine.py`, `test_insights_route.py`, and any future
   service-layer test that needs a `UserContext` with a stubbed DB.
"""

from __future__ import annotations

import os
import uuid
from datetime import date

os.environ.setdefault("CLERK_ISSUER", "https://test.clerk.test")
os.environ.setdefault("CLERK_SECRET_KEY", "sk_test")
os.environ.setdefault("CRON_SHARED_SECRET", "shh_test_secret")
os.environ.setdefault("RESEND_TEMPLATE_WELCOME", "tpl_welcome")
os.environ.setdefault("RESEND_TEMPLATE_ACCOUNT_DELETED", "tpl_deleted")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.test")
os.environ.setdefault("SUPABASE_ANON_KEY", "test-anon-key")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "srv_test")
os.environ.setdefault("CORS_ORIGINS", "")

from app.context import UserContext  # noqa: E402  — must follow setdefault block
from app.models.schemas import (  # noqa: E402
    AllocationRow,
    BudgetRow,
    GoalRow,
    TransactionRow,
)


def _uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def make_expense(
    amount: float = 10.0,
    *,
    category_id: str | None = "cat-g",
    category_name: str | None = "Groceries",
    category_icon: str | None = None,
    category_color: str | None = None,
    transaction_date: date = date(2026, 4, 1),
    merchant: str | None = None,
    description: str | None = None,
    id: str | None = None,
    user_id: str = "user-1",
) -> TransactionRow:
    return TransactionRow(
        id=id or _uid("tx"),
        user_id=user_id,
        category_id=category_id,
        amount=amount,
        transaction_date=transaction_date,
        type="expense",
        merchant=merchant,
        description=description,
        category_name=category_name,
        category_icon=category_icon,
        category_color=category_color,
    )


def make_income(
    amount: float = 1000.0,
    *,
    transaction_date: date = date(2026, 4, 1),
    id: str | None = None,
    user_id: str = "user-1",
) -> TransactionRow:
    return TransactionRow(
        id=id or _uid("tx"),
        user_id=user_id,
        category_id="cat-salary",
        amount=amount,
        transaction_date=transaction_date,
        type="income",
        category_name="Salary",
    )


def make_allocation(
    *,
    category_id: str = "cat-g",
    amount: float = 100.0,
    budget_id: str = "budget-1",
    id: str | None = None,
    alert_threshold: int = 80,
) -> AllocationRow:
    return AllocationRow(
        id=id or _uid("alloc"),
        budget_id=budget_id,
        category_id=category_id,
        amount=amount,
        alert_threshold=alert_threshold,
    )


def make_budget(
    *,
    start_date: date = date(2026, 4, 1),
    end_date: date = date(2026, 4, 30),
    amount: float = 5000.0,
    id: str | None = None,
    user_id: str = "user-1",
) -> BudgetRow:
    return BudgetRow(
        id=id or _uid("budget"),
        user_id=user_id,
        name="April 2026",
        period="monthly",
        amount=amount,
        start_date=start_date,
        end_date=end_date,
    )


def make_goal(
    *,
    name: str = "Emergency fund",
    target_amount: float = 1000.0,
    current_amount: float = 250.0,
    target_date: date | None = None,
    is_achieved: bool = False,
    id: str | None = None,
) -> GoalRow:
    return GoalRow(
        id=id or _uid("goal"),
        name=name,
        target_amount=target_amount,
        current_amount=current_amount,
        target_date=target_date,
        is_achieved=is_achieved,
    )


class FakeQuery:
    """Chainable no-op query; returns an object with `.data` when executed.

    Mirrors the subset of the supabase-py builder that db/client.py uses:
    select, eq, gte, lte, limit, execute. Filters are recorded but ignored;
    the caller seeds rows per (schema, table).
    """

    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def select(self, *_a: object, **_kw: object) -> FakeQuery:
        return self

    def eq(self, *_a: object, **_kw: object) -> FakeQuery:
        return self

    def gte(self, *_a: object, **_kw: object) -> FakeQuery:
        return self

    def lte(self, *_a: object, **_kw: object) -> FakeQuery:
        return self

    def limit(self, *_a: object, **_kw: object) -> FakeQuery:
        return self

    def order(self, *_a: object, **_kw: object) -> FakeQuery:
        return self

    def execute(self) -> object:
        class _Resp:
            data = self._rows

        return _Resp()


class FakeDB:
    """Minimal stand-in for a Supabase client. `table(name)` returns a
    FakeQuery over whatever rows the test seeded for that table."""

    def __init__(self, tables: dict[str, list[dict]] | None = None) -> None:
        self._tables = tables or {}

    def table(self, name: str) -> FakeQuery:
        return FakeQuery(self._tables.get(name, []))


def make_user_ctx(
    user_id: str = "user-1", tables: dict[str, list[dict]] | None = None
) -> UserContext:
    return UserContext(user_id=user_id, db=FakeDB(tables))
```

Notes baked into the code above:

- `from __future__ import annotations` keeps `FakeQuery`'s self-referential return type valid without runtime evaluation.
- The `app.*` imports sit AFTER the `os.environ.setdefault` block (E402 silenced via `# noqa: E402`) — order matters because `app.main` reads env at import time.
- `make_goal` no longer has the redundant inline `from app.models.schemas import GoalRow` that shadowed the module-level import.

- [ ] **Step 3: Run the suite and verify collection unblocks**

Run:

```bash
ACCOUNT_DELETION_ENABLED=false python -m pytest tests/ -q 2>&1 | tail -30
```

Expected: pytest collects without `ImportError`. All tests should pass (or report their normal pass/fail counts unrelated to the import bug). Capture the exact numbers — they'll be the baseline for Task 4.

If any test fails for a NEW reason (e.g. duplicate fixture warning, factory mismatch), stop and diagnose before continuing.

- [ ] **Step 4: Commit**

```bash
cd "/Users/nilyanvicent/Documents/Documents - Nilyan’s MacBook Pro/practice/personal-budget-api"
git add tests/conftest.py
git commit -m "$(cat <<'EOF'
test(conftest): host shared factories and fake-supabase helpers

Moves make_expense / make_income / make_allocation / make_budget /
make_goal, the FakeQuery + FakeDB pair, and make_user_ctx into
conftest.py so `from tests.conftest import ...` resolves correctly.
Unblocks collection of test_insights_engine.py and test_insights_route.py.

The duplicates still live in tests/test_conf.py for one commit; the next
commit removes them.
EOF
)"
```

---

## Task 2 — Rename `test_conf.py` → `test_config.py` and trim it to only the cron-secret tests

**Files:**
- Rename: `tests/test_conf.py` → `tests/test_config.py`
- Modify (post-rename): `tests/test_config.py`

**Why:** the filename `test_conf.py` was the trip-wire that caused these imports to drift in the first place — one letter off from `conftest.py`. Renaming closes the loop. While the file is open, drop the dead HS256 helpers (shadowed by `tests/test_deps.py:39`'s RS256 fixture, used by zero callers, contradicts the CLAUDE.md "RS256 only" rule).

- [ ] **Step 1: `git mv` the file so history follows**

```bash
cd "/Users/nilyanvicent/Documents/Documents - Nilyan’s MacBook Pro/practice/personal-budget-api"
git mv tests/test_conf.py tests/test_config.py
```

Verify:

```bash
git status
```

Expected: one renamed file, `tests/test_conf.py -> tests/test_config.py`.

- [ ] **Step 2: Replace the contents of `tests/test_config.py` with the two cron-secret tests only**

Overwrite the file with exactly:

```python
"""Tests for required-env-var enforcement in `app.config.Settings`.

Each test re-seeds the env via `monkeypatch` and clears the `Settings`
cache so it reads the per-test environment cleanly. These are the only
tests that exercise Settings boot-time validation; everything else
relies on the defaults seeded in `tests/conftest.py`.
"""

import pytest
from pydantic import ValidationError


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

What this drops compared to the pre-rename file:

- All factories (`_uid`, `make_*`, `FakeQuery`, `FakeDB`, `make_user_ctx`) — already in `conftest.py` as of Task 1.
- The `jwt_secret` fixture and the HS256 `make_token` fixture — dead code.
- Now-unused imports: `time`, `uuid`, `Callable`, `date`, `Any`, `pyjwt`, `UserContext`, `AllocationRow`, `BudgetRow`, `GoalRow`, `TransactionRow`.
- The nested `import pytest` inside `test_cron_shared_secret_is_required` — replaced by the top-level `import pytest`.

- [ ] **Step 3: Run the suite to confirm the trim didn't break anything**

```bash
ACCOUNT_DELETION_ENABLED=false python -m pytest tests/test_config.py -v
```

Expected: 2 passed (`test_cron_shared_secret_is_required`, `test_cron_shared_secret_loads`).

Then the full suite again:

```bash
ACCOUNT_DELETION_ENABLED=false python -m pytest tests/ -q 2>&1 | tail -10
```

Expected: same pass count as Task 1 step 3.

- [ ] **Step 4: Commit**

```bash
git add tests/test_conf.py tests/test_config.py
git commit -m "$(cat <<'EOF'
test(config): rename test_conf.py to test_config.py and drop dead HS256 helpers

The old filename was one letter off from conftest.py and caused exactly
the import drift this branch is fixing. Renamed for clarity; the file
now contains only the two cron-secret boot-validation tests.

Removed the unused HS256 make_token / jwt_secret fixtures (shadowed by
the RS256 fixture in tests/test_deps.py and contradicting the
"RS256 only" rule in CLAUDE.md) and the imports that supported them.
EOF
)"
```

---

## Task 3 — Collapse the six redundant inline `make_goal` imports in `test_insights_engine.py`

**Files:**
- Modify: `tests/test_insights_engine.py` (line 36 import block + lines 1029, 1040, 1049, 1060, 1070, 1093)

**Why:** these inline imports are an artifact of the same drift. Now that the module-level import resolves, they're redundant.

- [ ] **Step 1: Add `make_goal` to the module-level import block**

The block currently reads:

```python
from tests.conftest import (
    make_allocation,
    make_budget,
    make_expense,
    make_income,
)
```

Edit it to insert `make_goal` between `make_expense` and `make_income` (alphabetical order):

```python
from tests.conftest import (
    make_allocation,
    make_budget,
    make_expense,
    make_goal,
    make_income,
)
```

- [ ] **Step 2: Delete the six redundant inline imports**

Each of the following lines is a `from tests.conftest import make_goal` statement that is now redundant. Delete each line in place (leave surrounding code untouched):

- `tests/test_insights_engine.py:1029`
- `tests/test_insights_engine.py:1040`
- `tests/test_insights_engine.py:1049`
- `tests/test_insights_engine.py:1060`
- `tests/test_insights_engine.py:1070`
- `tests/test_insights_engine.py:1093`

(Line numbers will shift as you delete from the top down. Delete in REVERSE order — 1093 first, then 1070, etc. — so earlier line numbers stay valid.)

Verify zero matches after the deletes:

```bash
grep -n "from tests.conftest import make_goal" tests/test_insights_engine.py
```

Expected: no output.

- [ ] **Step 3: Run the engine tests**

```bash
ACCOUNT_DELETION_ENABLED=false python -m pytest tests/test_insights_engine.py -v 2>&1 | tail -30
```

Expected: every test that previously passed continues to pass. If any test errors with `NameError: name 'make_goal' is not defined`, an inline import was deleted from a test whose containing class somehow shadowed the module-level name — re-add `make_goal` to the module-level block (or the relevant nested scope) and re-run.

- [ ] **Step 4: Commit**

```bash
git add tests/test_insights_engine.py
git commit -m "$(cat <<'EOF'
test(insights): collapse six inline make_goal imports into the module-level block

Now that the module-level `from tests.conftest import (...)` resolves
(see prior commit), the per-method inline imports are redundant. One
import statement at the top is enough.
EOF
)"
```

---

## Task 4 — Final verification

**Files:** none modified.

- [ ] **Step 1: Full suite, dark-launch env**

```bash
cd "/Users/nilyanvicent/Documents/Documents - Nilyan’s MacBook Pro/practice/personal-budget-api"
ACCOUNT_DELETION_ENABLED=false python -m pytest tests/ -q
```

Expected: exit code 0, no collection errors. Record the pass count.

- [ ] **Step 2: Grep sanity checks**

```bash
# Only conftest imports should remain in tests/.
grep -rn "from tests\." tests/

# The old filename must be gone.
grep -rn "test_conf[^i]" tests/

# Dead HS256 helpers must be gone from the renamed file.
grep -nE "pyjwt|jwt_secret|HS256" tests/test_config.py
```

Expected:
- First grep: only `from tests.conftest import ...` (no `from tests.test_conf`, no `from tests.test_config`).
- Second grep: no output (`test_conf` no longer appears in any test source).
- Third grep: no output.

- [ ] **Step 3: Tick the Phase 3 checkbox in the account-deletion plan**

Open `docs/superpowers/plans/2026-05-06-account-deletion-simplified.md` and flip:

- Line 60: `- [ ] Run full suite green with account_deletion_enabled=False (dark launch).` → `- [x]` and update the note to point at this branch's commit hash.
- Line 82 (Outstanding verification): `- [ ] Full test suite green locally with ACCOUNT_DELETION_ENABLED=false.` → `- [x]` with the pass count from Step 1.

- [ ] **Step 4: Commit the plan update**

```bash
git add docs/superpowers/plans/2026-05-06-account-deletion-simplified.md
git commit -m "$(cat <<'EOF'
docs(plan): mark account-deletion Phase 3 suite-green checkboxes done

Full pytest suite collects and runs clean with ACCOUNT_DELETION_ENABLED=false
after the conftest relocation. Only Phase 4 (manual end-to-end + flag flip)
remains.
EOF
)"
```

---

## Notes for the executor

- Run pytest from the repo root, not from `tests/`. `pytest.ini` / `pyproject.toml` discovery and the `tests.conftest` import path both assume root-relative invocation.
- If `pytest` cannot find `tests.conftest` as an importable module, check that `tests/` has no stale `__pycache__` referencing the old `test_conf.py` — `find tests -name __pycache__ -exec rm -rf {} +` if needed.
- Don't be tempted to also fix unrelated test failures you may notice in Task 1 Step 3. Capture the pass count, move on. Any pre-existing red tests are out of scope for this branch.
