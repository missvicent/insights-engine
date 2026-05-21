# Test Helpers Relocation to `conftest.py` — Design

**Date:** 2026-05-20
**Owner:** vicentbnf@gmail.com
**Status:** approved

## Problem

`pytest tests/ -q` fails at collection. Two test modules import shared
factories from the wrong module:

- `tests/test_insights_engine.py:36` — `from tests.conftest import (make_allocation, …)`
- `tests/test_insights_route.py:16`  — `from tests.conftest import make_user_ctx`

Plus six inline `from tests.conftest import make_goal` repeats inside
`tests/test_insights_engine.py` (lines 1029, 1040, 1049, 1060, 1070, 1093).

The factories actually live in `tests/test_conf.py`. The mismatch was
introduced by two commits:

- `d925ccd refactor(tests): remove conftest.py and streamline test setup` —
  removed the old `conftest.py`, factories migrated into `test_conf.py`.
- `1290c35 test: move required-env setdefault from test_conf.py to conftest.py` —
  re-added a minimal `conftest.py` for env-`setdefault` only. Imports were
  never repointed.

The collection failure blocks the Phase 3 dark-launch checkbox in
[2026-05-06-account-deletion-simplified.md](../plans/2026-05-06-account-deletion-simplified.md)
("Full test suite green locally with `ACCOUNT_DELETION_ENABLED=false`").

## Approach

Move the shared factories into `conftest.py` (the pytest-idiomatic home),
drop dead HS256 JWT helpers along the way, and rename the orphaned
`test_conf.py` so its name reflects what it actually tests.

Chosen over the minimal "repoint imports" alternative because the current
filename (`test_conf.py`) was itself the footgun that caused this bug — a
one-letter variant of `conftest.py` invites exactly this kind of import
confusion. Renaming closes the loop.

## Changes

### 1. `tests/conftest.py` — grow into the shared-helpers home

Add to the existing env-`setdefault` block:

**Imports**

```python
from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from app.context import UserContext
from app.models.schemas import (
    AllocationRow,
    BudgetRow,
    GoalRow,
    TransactionRow,
)
```

**Helpers (moved verbatim from `test_conf.py`)**

- `_uid(prefix: str) -> str`
- `make_expense(...)` → `TransactionRow`
- `make_income(...)` → `TransactionRow`
- `make_allocation(...)` → `AllocationRow`
- `make_budget(...)` → `BudgetRow`
- `make_goal(...)` → `GoalRow` *(drop the redundant inline `from app.models.schemas import GoalRow` inside the body — it shadows the module-level import for no reason)*
- `FakeQuery` class
- `FakeDB` class
- `make_user_ctx(...)` → `UserContext`

Order: env-`setdefault` block stays first (must run before any `app.*`
import), then the imports above, then helpers. The existing module
docstring still applies; extend it one line to mention helpers.

### 2. `tests/test_conf.py` → `tests/test_config.py` (rename + trim)

After the move, the file should contain only the two cron-secret tests
plus the imports they need:

- `test_cron_shared_secret_is_required(monkeypatch)`
- `test_cron_shared_secret_loads(monkeypatch)`

**Drop entirely:**

- `jwt_secret` fixture (only consumer was the dead HS256 helper below)
- HS256 `make_token` fixture — shadowed by `tests/test_deps.py:39`'s
  RS256 fixture of the same name, used by zero callers, and contradicts
  the CLAUDE.md rule "`algorithms=["RS256"]` only — never include HS256"

**Drop now-unused imports** from the top of the renamed file:

- `time`, `uuid`, `from collections.abc import Callable`, `from datetime import date`
- `from typing import Any`
- `import jwt as pyjwt`
- `from app.context import UserContext`
- `from app.models.schemas import (AllocationRow, BudgetRow, GoalRow, TransactionRow)`

`pytest` stays as a top-level import (needed for `pytest.raises`).
`monkeypatch` is a pytest fixture passed by name into the test functions
— no import required. The `import pytest` currently nested inside
`test_cron_shared_secret_is_required` (line 234) should be deleted; the
top-level `pytest` import covers it.

Use `git mv` so history follows the rename.

### 3. `tests/test_insights_engine.py` — collapse `make_goal` imports

- Line 36 multiline import block: add `make_goal` to the list, sorted
  alphabetically with the rest. Path stays `from tests.conftest import (…)`
  and resolves correctly after the move.
- Delete the six inline `from tests.conftest import make_goal` lines at
  1029, 1040, 1049, 1060, 1070, 1093. Each is the first line of its
  containing test method, indented under `class …:`; remove the line and
  the blank line that immediately follows if one exists, otherwise just
  the line itself.

### 4. `tests/test_insights_route.py` — no change

The existing `from tests.conftest import make_user_ctx` (line 16) starts
resolving once the factory moves. No edits needed in this file.

## Verification

```bash
ACCOUNT_DELETION_ENABLED=false python -m pytest tests/ -q
```

Exit code 0, no collection errors. Capture pass/fail counts in the PR
description.

Also run a quick grep sanity check:

```bash
grep -rn "from tests\." tests/   # only `from tests.conftest import …` should appear
grep -rn "test_conf"   tests/    # zero matches
grep -rn "pyjwt\|jwt_secret\|HS256" tests/test_config.py   # zero matches
```

## Out of scope

- Touching `tests/test_deps.py`'s RSA-keypair `make_token` fixture (correct
  by design, not affected by this move).
- Any other refactoring of the test suite.
- Flipping `ACCOUNT_DELETION_ENABLED=true` — that remains the last item of
  the account-deletion plan, gated on the manual end-to-end run.
