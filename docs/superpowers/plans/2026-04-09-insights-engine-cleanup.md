# Insights Engine Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring `app/services/insights_engine.py` into conformance with `CLAUDE.md` (type hints on every function) and fix a user-visible data quality issue where anomaly messages display category IDs instead of human-readable category names.

**Architecture:** Four scoped edits to a single file plus a new test file. No new modules, no signature changes to the public `anomalies()` orchestrator, no changes to callers of the engine. The category-name fix uses a per-detector local lookup built from the raw transaction list, rather than changing the shared `detect_category_totals` helper's return shape — this keeps the diff contained to the two detectors that need it.

**Tech Stack:** Python 3.12, Pydantic v2, pytest.

---

## File Structure

**Modified:**
- `app/services/insights_engine.py` — add type hints, fix indentation, refresh stale comment, fix category_name resolution in two detectors.

**Created:**
- `tests/__init__.py` — empty package marker (only if `tests/` does not already exist).
- `tests/test_insights_engine.py` — targeted tests for the category_name fix.

---

## Task 1: Add missing type hints to detector and helper functions

**Files:**
- Modify: `app/services/insights_engine.py:123-134` (`detect_category_totals`)
- Modify: `app/services/insights_engine.py:135-162` (`detect_category_spikes`)
- Modify: `app/services/insights_engine.py:164-181` (`detect_budget_overspending`)
- Modify: `app/services/insights_engine.py:183-205` (`detect_large_single_transactions`)

**Context:** `CLAUDE.md` requires "Type hints on every function signature — parameters and return type." These four functions are currently bare. No behavior change — this is pure annotation.

- [ ] **Step 1: Annotate `detect_category_totals`**

Replace the signature on line 123:

```python
def detect_category_totals(txs: list[TransactionRow]) -> dict[str, float]:
```

- [ ] **Step 2: Annotate `detect_category_spikes`**

Replace the signature on line 135:

```python
def detect_category_spikes(
    current: list[TransactionRow],
    previous: list[TransactionRow],
) -> list[Anomaly]:
```

- [ ] **Step 3: Annotate `detect_budget_overspending`**

Replace the signature on line 164:

```python
def detect_budget_overspending(
    current: list[TransactionRow],
    allocations: list[AllocationRow],
) -> list[Anomaly]:
```

- [ ] **Step 4: Annotate `detect_large_single_transactions`**

Replace the signature on line 183:

```python
def detect_large_single_transactions(
    current: list[TransactionRow],
) -> list[Anomaly]:
```

- [ ] **Step 5: Verify the module still imports cleanly**

Run: `python -c "from app.services import insights_engine; print('ok')"`
Expected: prints `ok` with no traceback.

- [ ] **Step 6: Commit**

```bash
git add app/services/insights_engine.py
git commit -m "chore(insights_engine): add type hints to anomaly detectors"
```

---

## Task 2: Fix 3-space indentation and stale comment

**Files:**
- Modify: `app/services/insights_engine.py:43-51` (indentation in `category_breakdown`)
- Modify: `app/services/insights_engine.py:124` (stale comment in `detect_category_totals`)

**Context:** Lines 44-51 of `category_breakdown` use 3-space indentation inside the `if key not in groups:` block, violating `CLAUDE.md`'s "4 spaces indentation, never tabs." The comment on line 124 says `# Rule 1: Category spikes` but the function is a shared helper used by both `detect_category_spikes` and `detect_budget_overspending`, not specifically rule 1.

- [ ] **Step 1: Fix the 3-space indent block**

Replace lines 43-51 (the `if key not in groups:` block) with properly 4-space-indented code:

```python
        if key not in groups:
            groups[key] = {
                "category_id": key,
                "category_name": t.category_name,
                "icon": t.category_icon,
                "color": t.category_color,
                "total": 0.0,
                "count": 0,
            }
```

- [ ] **Step 2: Update the stale comment**

On line 124, replace:

```python
    # Rule 1: Category spikes
```

with:

```python
    # Shared helper: sum expenses by category_id.
```

- [ ] **Step 3: Verify the module still imports cleanly**

Run: `python -c "from app.services import insights_engine; print('ok')"`
Expected: prints `ok` with no traceback.

- [ ] **Step 4: Commit**

```bash
git add app/services/insights_engine.py
git commit -m "style(insights_engine): fix indentation and refresh stale comment"
```

---

## Task 3: Create test scaffolding and write failing tests for category_name fix

**Files:**
- Create: `tests/__init__.py` (only if it does not already exist)
- Create: `tests/test_insights_engine.py`

**Context:** The engine currently has no test coverage. We are adding **only** the tests required to lock in the category_name fix from Task 4 — not a full engine test suite. Before writing the tests, check whether `tests/__init__.py` exists and create it only if missing.

The two tests prove that when a `TransactionRow` carries a `category_name` (e.g. `"Groceries"`), the resulting `Anomaly.message` and `Anomaly.category_name` surface the name rather than the raw `category_id` UUID.

- [ ] **Step 1: Ensure `tests/` is a package**

Run: `ls tests/__init__.py 2>/dev/null || echo missing`
If the output is `missing`, create an empty file at `tests/__init__.py`. Otherwise skip to Step 2.

- [ ] **Step 2: Write the test file**

Create `tests/test_insights_engine.py` with the full content below:

```python
"""Tests for category_name resolution in anomaly detectors.

Scope note: these tests cover only the category_name fix. A full engine
test suite is out of scope for this plan.
"""

from datetime import date

from app.models.schemas import AllocationRow, TransactionRow
from app.services.insights_engine import (
    detect_budget_overspending,
    detect_category_spikes,
)


GROCERIES_ID = "cat-groceries-uuid"
GROCERIES_NAME = "Groceries"


def _expense(
    amount: float,
    category_id: str = GROCERIES_ID,
    category_name: str | None = GROCERIES_NAME,
) -> TransactionRow:
    return TransactionRow(
        id=f"tx-{amount}",
        user_id="user-1",
        category_id=category_id,
        amount=amount,
        transaction_date=date(2026, 4, 1),
        type="expense",
        category_name=category_name,
    )


def test_category_spike_anomaly_uses_category_name_not_id():
    current = [_expense(500.0)]
    previous = [_expense(100.0)]

    anomalies = detect_category_spikes(current, previous)

    assert len(anomalies) == 1
    anomaly = anomalies[0]
    assert anomaly.category_name == GROCERIES_NAME
    assert GROCERIES_NAME in anomaly.message
    assert GROCERIES_ID not in anomaly.message


def test_new_category_anomaly_uses_category_name_not_id():
    current = [_expense(75.0)]
    previous: list[TransactionRow] = []

    anomalies = detect_category_spikes(current, previous)

    assert len(anomalies) == 1
    anomaly = anomalies[0]
    assert anomaly.type == "new_category"
    assert anomaly.category_name == GROCERIES_NAME
    assert GROCERIES_NAME in anomaly.message
    assert GROCERIES_ID not in anomaly.message


def test_budget_overspending_anomaly_uses_category_name_not_id():
    current = [_expense(250.0)]
    allocations = [
        AllocationRow(
            id="alloc-1",
            budget_id="budget-1",
            category_id=GROCERIES_ID,
            amount=100.0,
        )
    ]

    anomalies = detect_budget_overspending(current, allocations)

    assert len(anomalies) == 1
    anomaly = anomalies[0]
    assert anomaly.category_name == GROCERIES_NAME
    assert GROCERIES_NAME in anomaly.message
    assert GROCERIES_ID not in anomaly.message
```

- [ ] **Step 3: Run the tests and verify they fail for the right reason**

Run: `pytest tests/test_insights_engine.py -v`
Expected: all three tests FAIL. The failures should be `AssertionError` on `anomaly.category_name == GROCERIES_NAME` (the current code stores the category_id there). If any test fails for a different reason — for example `ImportError`, `ValidationError`, or `TypeError` — stop and investigate before proceeding; the fix in Task 4 will not address those.

- [ ] **Step 4: Commit the failing tests**

```bash
git add tests/__init__.py tests/test_insights_engine.py
git commit -m "test(insights_engine): add failing tests for category_name in anomalies"
```

Note: if `tests/__init__.py` already existed, omit it from the `git add` line.

---

## Task 4: Resolve category_name from transactions in the two detectors

**Files:**
- Modify: `app/services/insights_engine.py:135-162` (`detect_category_spikes`)
- Modify: `app/services/insights_engine.py:164-181` (`detect_budget_overspending`)

**Context:** Both detectors currently store `category_name=cat` where `cat` is the category_id (because that is what `detect_category_totals` keys on). We resolve this by building a `category_id → category_name` lookup from the raw `current` transactions at the top of each detector. When a transaction's `category_name` is `None`, we fall back to the category_id string — so the behavior degrades gracefully rather than producing `"None"` in the message.

This is a local, per-detector change. `detect_category_totals` is not touched; its return shape stays `dict[str, float]`.

- [ ] **Step 1: Update `detect_category_spikes` to resolve names**

Replace the entire body of `detect_category_spikes` (lines 135-162) with:

```python
def detect_category_spikes(
    current: list[TransactionRow],
    previous: list[TransactionRow],
) -> list[Anomaly]:
    current_cats = detect_category_totals(current)
    previous_cats = detect_category_totals(previous)
    name_by_id: dict[str, str] = {
        t.category_id: t.category_name
        for t in current
        if t.category_name
    }
    result: list[Anomaly] = []

    for cat, current_total in current_cats.items():
        display_name = name_by_id.get(cat, cat)
        prev_total = previous_cats.get(cat, 0)
        if prev_total == 0:
            result.append(Anomaly(
                type="new_category",
                category_name=display_name,
                message=(
                    f"New spending in {display_name} - "
                    f"${current_total:.0f} this month (not previously tracked)"
                ),
                severity="low",
                amount=current_total,
            ))
        else:
            change = (current_total - prev_total) / prev_total
            if change > 0.30:
                severity = "high" if change > 0.50 else "medium"
                result.append(Anomaly(
                    type="spike",
                    category_name=display_name,
                    message=(
                        f"Spending in {display_name} increased by "
                        f"{change * 100:.0f}% this month "
                        f"(vs {prev_total:.0f} last month)"
                    ),
                    severity=severity,
                    amount=current_total,
                ))
    return result
```

- [ ] **Step 2: Update `detect_budget_overspending` to resolve names**

Replace the entire body of `detect_budget_overspending` (lines 164-181) with:

```python
def detect_budget_overspending(
    current: list[TransactionRow],
    allocations: list[AllocationRow],
) -> list[Anomaly]:
    result: list[Anomaly] = []
    current_cats = detect_category_totals(current)
    alloc_map = {a.category_id: a.amount for a in allocations if a.category_id}
    name_by_id: dict[str, str] = {
        t.category_id: t.category_name
        for t in current
        if t.category_name
    }

    for cat, total in current_cats.items():
        limit = alloc_map.get(cat, 0)
        if limit and total > limit:
            display_name = name_by_id.get(cat, cat)
            pct = total / limit * 100
            result.append(Anomaly(
                type="budget_exceeded",
                category_name=display_name,
                message=(
                    f"'{display_name}' budget exceeded by {pct:.0f}% of "
                    f"budget (${total:.0f} of ${limit:.0f})"
                ),
                severity="high" if pct > 120 else "medium",
                amount=total - limit,
            ))
    return result
```

- [ ] **Step 3: Run the targeted tests and verify they pass**

Run: `pytest tests/test_insights_engine.py -v`
Expected: all three tests PASS.

- [ ] **Step 4: Run the full test suite to confirm nothing else broke**

Run: `pytest -v`
Expected: all tests pass. (If `tests/test_insights_engine.py` is the only test file in the repo, this is equivalent to Step 3.)

- [ ] **Step 5: Verify the module still imports cleanly**

Run: `python -c "from app.services import insights_engine; print('ok')"`
Expected: prints `ok` with no traceback.

- [ ] **Step 6: Commit**

```bash
git add app/services/insights_engine.py
git commit -m "fix(insights_engine): show category name not id in anomaly messages"
```

---

## Out of scope — follow-up items

These were spotted during planning but are intentionally excluded from this plan. Each is its own future task.

1. **`category_breakdown` crash bug.** Line 73 passes `count=g["count"]` to `CategoryBreakdown(...)`, but `CategoryBreakdown` (schemas.py:91) declares `transaction_count: int`. This will raise a Pydantic `ValidationError` the first time `category_breakdown` is called with any expenses. Fix is a one-line rename but it needs its own test; file a separate plan.
2. **`compare_periods` returns `-> dict`.** Works, but is the only untyped-model return in the engine. A `PeriodComparison` Pydantic model would match the rest of the file.
3. **Full engine test coverage.** `calculate_totals`, `category_breakdown`, `compare_periods`, and `detect_large_single_transactions` have zero test coverage. `CLAUDE.md` says "Engine functions must have unit tests." Worth its own dedicated plan.
