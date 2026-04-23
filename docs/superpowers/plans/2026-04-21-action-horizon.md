# Action Horizon & Budget-Scoped Windows Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an engine-computed `next_action_horizon_days` field to `InsightSummary`, reshape `InsightWindow` into period-scoped buckets (day-scale for monthly budgets, month-scale for yearly), and reject mismatched (window, budget period) combinations at the route with 422.

**Architecture:** Single source of truth for window semantics lives in `app/services/insights_engine.py` — `resolve_window`, the new `_horizon_for_window`, and `allowed_windows_for_period` all colocate there. The schema (`app/models/schemas.py`) carries the literal and the new summary field. The route (`app/routes/insights.py`) calls `allowed_windows_for_period(budget.period)` to validate `q.window` before doing any work, and passes the window enum through to `build_summary` so the engine can populate the horizon.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, pytest.

**Spec:** `docs/superpowers/specs/2026-04-21-action-horizon-design.md`

---

## File Map

| File | Change |
|---|---|
| `app/models/schemas.py` | Replace `InsightWindow` literal; add `next_action_horizon_days` to `InsightSummary`; fix `BudgetRow.period` comment |
| `app/services/insights_engine.py` | Rewrite `resolve_window` body; delete `_clamp_to_month_end`; add `_HORIZON_DAYS`, `_horizon_for_window`, `_ALLOWED_WINDOWS`, `allowed_windows_for_period`; extend `build_summary` with `window: InsightWindow` parameter |
| `app/routes/insights.py` | Validate `q.window` against `budget.period` via `allowed_windows_for_period`; pass `q.window` through to `build_summary` |
| `tests/test_insights_engine.py` | Replace `TestResolveWindow` cases; add `TestHorizonForWindow`; add `TestAllowedWindowsForPeriod`; extend `TestBuildSummary` for horizon field + pass `window` kwarg |
| `tests/test_insights_route.py` | Update happy-path to use `30d` (not `1m`); add mismatch (422) tests; add horizon-value assertion |

---

## Task 1: Reshape `InsightWindow` enum and rewrite `resolve_window`

Reshape the literal, rewrite the window resolver for the six new values, and update every test that referenced the old names. This is a single atomic change because the literal, its resolver, and every consumer are locked together — the test suite only returns green when all three are aligned.

**Files:**
- Modify: `app/models/schemas.py` (the `InsightWindow = Literal[...]` line at `:24`)
- Modify: `app/services/insights_engine.py` (`resolve_window` at `:34`, delete `_clamp_to_month_end` at `:64`)
- Modify: `tests/test_insights_engine.py` (rewrite `TestResolveWindow` at `:783`)
- Modify: `tests/test_insights_route.py` (update `window=1m` → `window=30d` in 3 places at `:54`, `:60`, `:67`)

- [ ] **Step 1: Rewrite `TestResolveWindow` with the six new cases**

Replace the entire `TestResolveWindow` class (currently at `tests/test_insights_engine.py:783-849`) with:

```python
class TestResolveWindow:
    def test_7d(self):
        from app.services.insights_engine import resolve_window

        today = date(2026, 4, 14)
        cs, ce, ps, pe = resolve_window("7d", today)
        assert ce == today
        assert cs == date(2026, 4, 7)  # today - 7d
        assert pe == date(2026, 4, 6)
        assert ps == date(2026, 3, 30)

    def test_15d(self):
        from app.services.insights_engine import resolve_window

        today = date(2026, 4, 14)
        cs, ce, ps, pe = resolve_window("15d", today)
        assert ce == today
        assert cs == date(2026, 3, 30)  # today - 15d
        assert pe == date(2026, 3, 29)
        assert ps == date(2026, 3, 14)

    def test_30d(self):
        from app.services.insights_engine import resolve_window

        today = date(2026, 4, 14)
        cs, ce, ps, pe = resolve_window("30d", today)
        assert ce == today
        assert cs == date(2026, 3, 15)  # today - 30d
        assert pe == date(2026, 3, 14)
        assert ps == date(2026, 2, 12)

    def test_3m(self):
        from app.services.insights_engine import resolve_window

        today = date(2026, 4, 14)
        cs, ce, ps, pe = resolve_window("3m", today)
        assert ce == today
        assert cs == date(2026, 1, 14)  # today - 90d
        assert pe == date(2026, 1, 13)
        assert ps == date(2025, 10, 15)

    def test_6m(self):
        from app.services.insights_engine import resolve_window

        today = date(2026, 4, 14)
        cs, ce, ps, pe = resolve_window("6m", today)
        assert ce == today
        assert (ce - cs).days == 180
        assert (pe - ps).days == 180
        assert pe == cs - timedelta(days=1)

    def test_12m(self):
        from app.services.insights_engine import resolve_window

        today = date(2026, 4, 14)
        cs, ce, ps, pe = resolve_window("12m", today)
        assert ce == today
        assert (ce - cs).days == 365
        assert (pe - ps).days == 365
        assert pe == cs - timedelta(days=1)

    def test_unknown_window_raises(self):
        from app.services.insights_engine import resolve_window

        with pytest.raises(ValueError, match="unknown window"):
            resolve_window("nope", date(2026, 4, 14))  # type: ignore[arg-type]
```

Note: the `pd` import used by the previous `test_6m` is no longer needed in this class — `timedelta` is already imported at the top of the test file (line 9).

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_insights_engine.py::TestResolveWindow -v`

Expected: the `7d`, `15d`, `30d`, `12m` tests FAIL with `ValueError: unknown window: '7d'` (etc.) raised by the existing `resolve_window` body (which only accepts `1m`, `3m`, `6m`, `1y`, `current_year`, `last_year`). The `3m` and `6m` tests may pass (those literals still exist today).

- [ ] **Step 3: Replace `InsightWindow` literal in schemas**

In `app/models/schemas.py`, replace the current literal at line 24:

```python
InsightWindow = Literal[
    "1m",
    "3m",
    "6m",
    "1y",
    "current_year",
    "last_year",
]
```

with:

```python
InsightWindow = Literal["7d", "15d", "30d", "3m", "6m", "12m"]
```

- [ ] **Step 4: Rewrite `resolve_window` body and delete `_clamp_to_month_end`**

In `app/services/insights_engine.py`, replace the body of `resolve_window` (lines 34-61) with:

```python
def resolve_window(
    window: InsightWindow,
    today: date,
) -> tuple[date, date, date, date]:
    """Return (current_start, current_end, previous_start, previous_end)."""
    span_days = {
        "7d": 7,
        "15d": 15,
        "30d": 30,
        "3m": 90,
        "6m": 180,
        "12m": 365,
    }.get(window)
    if span_days is None:
        raise ValueError(f"unknown window: {window}")

    current_end = today
    current_start = today - timedelta(days=span_days)
    previous_end = current_start - timedelta(days=1)
    previous_start = previous_end - timedelta(days=span_days)
    return current_start, current_end, previous_start, previous_end
```

Then delete `_clamp_to_month_end` (the helper at lines 64-70) — it has no remaining callers. Also remove the `import calendar` at line 9 (only `_clamp_to_month_end` used it).

- [ ] **Step 5: Update existing route tests to use the new literals**

In `tests/test_insights_route.py`, change three `window=1m` query params to `window=30d`:

- Line 54: `resp = client.get("/insights?budget_id=budget-1&window=30d")`
- Line 60: `resp = client.get("/insights?budget_id=budget-1&window=30d")`
- Line 67: `resp = client.get("/insights?budget_id=missing&window=30d")`

- [ ] **Step 6: Run the full test suite to verify everything passes**

Run: `pytest tests/ -v`

Expected: all tests PASS. `TestResolveWindow` now exercises the six new cases. `TestGetInsights` passes with the new literal. No other tests should have referenced the dropped values.

If any other test fails with a Pydantic validation error mentioning the old literals, fix it by substituting the equivalent new literal (`1m` → `30d`, `1y` → `12m`; drop any `current_year` / `last_year` cases since they have no replacement).

- [ ] **Step 7: Commit**

```bash
git add app/models/schemas.py app/services/insights_engine.py tests/test_insights_engine.py tests/test_insights_route.py
git commit -m "$(cat <<'EOF'
refactor(window): reshape InsightWindow into period-scoped literals

Replaces "1m | 3m | 6m | 1y | current_year | last_year" with
"7d | 15d | 30d | 3m | 6m | 12m". Day-scale windows will pair with
monthly budgets, month-scale with yearly; validation follows in a
subsequent commit. resolve_window rewritten accordingly;
_clamp_to_month_end deleted (no remaining callers).

Breaking change: clients sending the old literals will now get 422.
Frontend updates in lockstep.
EOF
)"
```

---

## Task 2: Fix `BudgetRow.period` docstring

One-line comment correction. The DB stores `'monthly' | 'yearly'`; the comment has always said `'monthly' | 'daily'`.

**Files:**
- Modify: `app/models/schemas.py` (line 68)

- [ ] **Step 1: Update the comment**

In `app/models/schemas.py` line 68, change:

```python
    period: str  # 'monthly'| 'daily'
```

to:

```python
    period: str  # 'monthly' | 'yearly'
```

- [ ] **Step 2: Run tests to confirm no regressions**

Run: `pytest tests/ -v`

Expected: all pass (comment change only).

- [ ] **Step 3: Commit**

```bash
git add app/models/schemas.py
git commit -m "docs(schemas): correct BudgetRow.period values in comment"
```

---

## Task 3: Add `_horizon_for_window` helper

Engine-owned mapping from window to action horizon in days. Pure function, trivially testable.

**Files:**
- Modify: `app/services/insights_engine.py` (new helper after `resolve_window`)
- Modify: `tests/test_insights_engine.py` (new `TestHorizonForWindow` class)

- [ ] **Step 1: Write the failing test**

Add this class to `tests/test_insights_engine.py`, immediately after `TestResolveWindow`:

```python
class TestHorizonForWindow:
    @pytest.mark.parametrize(
        "window,expected_horizon",
        [
            ("7d", 3),
            ("15d", 7),
            ("30d", 7),
            ("3m", 14),
            ("6m", 30),
            ("12m", 30),
        ],
    )
    def test_mapping(self, window, expected_horizon):
        from app.services.insights_engine import _horizon_for_window

        assert _horizon_for_window(window) == expected_horizon
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_insights_engine.py::TestHorizonForWindow -v`

Expected: FAIL with `ImportError: cannot import name '_horizon_for_window'`.

- [ ] **Step 3: Implement the helper**

In `app/services/insights_engine.py`, add the following immediately after `resolve_window` (and before `calculate_totals`):

```python
_HORIZON_DAYS: dict[InsightWindow, int] = {
    "7d": 3,
    "15d": 7,
    "30d": 7,
    "3m": 14,
    "6m": 30,
    "12m": 30,
}


def _horizon_for_window(window: InsightWindow) -> int:
    """Days of forward-looking action the deterministic signal supports."""
    return _HORIZON_DAYS[window]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_insights_engine.py::TestHorizonForWindow -v`

Expected: 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/insights_engine.py tests/test_insights_engine.py
git commit -m "feat(engine): add _horizon_for_window mapping"
```

---

## Task 4: Add `allowed_windows_for_period` helper

Engine-owned mapping from budget period to its allowed windows, used by the route for 422 validation.

**Files:**
- Modify: `app/services/insights_engine.py` (new helper after `_horizon_for_window`)
- Modify: `tests/test_insights_engine.py` (new `TestAllowedWindowsForPeriod` class)

- [ ] **Step 1: Write the failing test**

Add this class to `tests/test_insights_engine.py`, immediately after `TestHorizonForWindow`:

```python
class TestAllowedWindowsForPeriod:
    def test_monthly_returns_day_windows(self):
        from app.services.insights_engine import allowed_windows_for_period

        assert allowed_windows_for_period("monthly") == {"7d", "15d", "30d"}

    def test_yearly_returns_month_windows(self):
        from app.services.insights_engine import allowed_windows_for_period

        assert allowed_windows_for_period("yearly") == {"3m", "6m", "12m"}

    def test_unknown_period_raises(self):
        from app.services.insights_engine import allowed_windows_for_period

        with pytest.raises(ValueError, match="unknown budget period"):
            allowed_windows_for_period("weekly")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_insights_engine.py::TestAllowedWindowsForPeriod -v`

Expected: FAIL with `ImportError: cannot import name 'allowed_windows_for_period'`.

- [ ] **Step 3: Implement the helper**

In `app/services/insights_engine.py`, add immediately after `_horizon_for_window`:

```python
_ALLOWED_WINDOWS: dict[str, set[InsightWindow]] = {
    "monthly": {"7d", "15d", "30d"},
    "yearly": {"3m", "6m", "12m"},
}


def allowed_windows_for_period(period: str) -> set[InsightWindow]:
    """Which InsightWindow values are valid for a budget with this period.

    Raises ValueError for unknown periods.
    """
    try:
        return _ALLOWED_WINDOWS[period]
    except KeyError as e:
        raise ValueError(f"unknown budget period: {period!r}") from e
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_insights_engine.py::TestAllowedWindowsForPeriod -v`

Expected: 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/insights_engine.py tests/test_insights_engine.py
git commit -m "feat(engine): add allowed_windows_for_period helper"
```

---

## Task 5: Add `next_action_horizon_days` field and thread window through `build_summary`

Extend `InsightSummary` with the new required field, add `window: InsightWindow` to `build_summary`, populate the field from `_horizon_for_window`, and update the one route caller plus existing `TestBuildSummary` tests to pass the new argument.

This bundles together because `next_action_horizon_days` is non-optional — the schema change, engine change, route change, and test updates must ship atomically to keep the test suite green.

**Files:**
- Modify: `app/models/schemas.py` (`InsightSummary` at `:168`)
- Modify: `app/services/insights_engine.py` (`build_summary` at `:470`)
- Modify: `app/routes/insights.py` (the `build_summary(...)` call at `:39`)
- Modify: `tests/test_insights_engine.py` (`TestBuildSummary` at `:916` — add horizon assertions, add `window` kwarg to existing cases)

- [ ] **Step 1: Write the failing tests**

Add these two test methods to `TestBuildSummary` in `tests/test_insights_engine.py` (at the end of the class, around line 1005):

```python
    def test_monthly_window_sets_horizon(self):
        from app.services.insights_engine import build_summary

        summary = build_summary(
            budget=make_budget(),
            allocations=[],
            current=[],
            previous=[],
            goals=[],
            window="30d",
            window_start=date(2026, 3, 15),
            window_end=date(2026, 4, 14),
        )

        assert summary.next_action_horizon_days == 7

    def test_yearly_window_sets_horizon(self):
        from app.services.insights_engine import build_summary

        summary = build_summary(
            budget=make_budget(),
            allocations=[],
            current=[],
            previous=[],
            goals=[],
            window="6m",
            window_start=date(2025, 10, 17),
            window_end=date(2026, 4, 14),
        )

        assert summary.next_action_horizon_days == 30
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_insights_engine.py::TestBuildSummary -v`

Expected: the two new tests FAIL with `TypeError: build_summary() got an unexpected keyword argument 'window'`. The existing tests still pass.

- [ ] **Step 3: Add `next_action_horizon_days` field to `InsightSummary`**

In `app/models/schemas.py`, extend the `InsightSummary` class (at `:168`). Add this field at the bottom of the class body, just before the `# ── API response shapes ──` separator comment:

```python
    next_action_horizon_days: int
```

The class should now end like this:

```python
    # raw counts for context
    transaction_count: int
    recurring_count: int

    next_action_horizon_days: int
```

- [ ] **Step 4: Extend `build_summary` to take `window` and populate the field**

In `app/services/insights_engine.py`, modify `build_summary` (at `:470`). Update the signature and the `InsightSummary(...)` call:

```python
def build_summary(
    budget: BudgetRow,
    allocations: list[AllocationRow],
    current: list[TransactionRow],
    previous: list[TransactionRow],
    goals: list[GoalRow],
    window: InsightWindow,
    window_start: date,
    window_end: date,
) -> InsightSummary:
    totals = calculate_totals(current)
    change = compare_periods(current, previous)
    breakdown = category_breakdown(current, allocations)
    anomalies = detect_anomalies(current, previous, allocations)
    patterns = detect_patterns(current, window_start, window_end)
    goal_progress = compute_goal_progress(goals)

    return InsightSummary(
        budget_id=budget.id,
        budget_name=budget.name,
        period_label=_format_period_label(window_start, window_end),
        total_income=totals.total_income,
        total_expenses=totals.total_expenses,
        net=totals.net,
        savings_rate=totals.savings_rate,
        income_change_pct=change["income_change_pct"],
        expenses_change_pct=change["expenses_change_pct"],
        category_breakdown=breakdown,
        anomalies=anomalies,
        patterns=patterns,
        goals=goal_progress,
        debt=None,
        transaction_count=len(current),
        recurring_count=sum(1 for t in current if t.is_recurring),
        next_action_horizon_days=_horizon_for_window(window),
    )
```

- [ ] **Step 5: Update the route to pass `window` through**

In `app/routes/insights.py`, modify the `build_summary(...)` call (at `:39`) to include `window=q.window`:

```python
    summary = build_summary(
        budget=budget,
        allocations=allocations,
        current=current,
        previous=previous,
        goals=goals,
        window=q.window,
        window_start=current_start,
        window_end=current_end,
    )
```

- [ ] **Step 6: Update existing `TestBuildSummary` tests to pass `window` kwarg**

In `tests/test_insights_engine.py`, the existing `TestBuildSummary` tests (`test_stamps_budget_identity`, `test_period_label_matches_window`, `test_period_label_crosses_year`, `test_totals_and_change_pct`, `test_empty_inputs`) all call `build_summary` without `window`. Add `window="30d"` to each `build_summary(...)` invocation.

For example, `test_stamps_budget_identity` at line 924 becomes:

```python
        summary = build_summary(
            budget=budget_with_name,
            allocations=[],
            current=[make_income(1000.0), make_expense(200.0)],
            previous=[make_income(800.0), make_expense(150.0)],
            goals=[make_goal()],
            window="30d",
            window_start=date(2026, 4, 1),
            window_end=date(2026, 4, 30),
        )
```

Apply the same `window="30d",` insertion to all five existing `build_summary(...)` calls in `TestBuildSummary` (they're between lines 924 and 998). Place `window="30d",` immediately before `window_start=...` each time.

- [ ] **Step 7: Run the full test suite**

Run: `pytest tests/ -v`

Expected: all tests PASS. In particular:
- `TestBuildSummary` — all 7 methods pass (5 existing + 2 new)
- `TestGetInsights` — still passes; the route now passes `window` internally
- Pydantic will error on any `InsightSummary(...)` call elsewhere that forgets `next_action_horizon_days`. The only such construction site is `build_summary`, which is now fixed. If any other test constructs `InsightSummary` directly, update it to include `next_action_horizon_days=7` (or any int).

- [ ] **Step 8: Commit**

```bash
git add app/models/schemas.py app/services/insights_engine.py app/routes/insights.py tests/test_insights_engine.py
git commit -m "$(cat <<'EOF'
feat(engine): add next_action_horizon_days to InsightSummary

The engine now computes the action horizon from the analysis window
(7d→3, 15d→7, 30d→7, 3m→14, 6m→30, 12m→30). The AI layer will
consume this field instead of guessing a horizon from the period
label. build_summary gains a window: InsightWindow parameter and
populates the field via _horizon_for_window. Route passes q.window
through.
EOF
)"
```

---

## Task 6: Validate window against budget period at the route

The `/insights` route must reject (window, budget period) mismatches with a 422. Happy-path route tests also gain an assertion on `next_action_horizon_days`.

**Files:**
- Modify: `app/routes/insights.py` (insert validation between `fetch_budget` and `resolve_window`)
- Modify: `tests/test_insights_route.py` (new 422 tests + horizon-value assertion + yearly-budget fixture)

- [ ] **Step 1: Write the failing tests**

In `tests/test_insights_route.py`, add a yearly-budget factory after `_budget_row` (around line 30):

```python
def _yearly_budget_row(user_id: str = "user-1") -> dict:
    return {
        "id": "budget-yearly",
        "user_id": user_id,
        "name": "2026",
        "period": "yearly",
        "amount": 60000.0,
        "start_date": date(2026, 1, 1).isoformat(),
        "end_date": date(2026, 12, 31).isoformat(),
        "is_active": True,
    }
```

Then add these methods to `TestGetInsights` (at the end of the class, around line 69):

```python
    def test_monthly_window_on_yearly_budget_is_422(self, client):
        app.dependency_overrides[get_user_ctx] = lambda: make_user_ctx(
            tables={
                "budgets": [_yearly_budget_row()],
                "allocations": [],
                "transactions": [],
                "goals": [],
            }
        )
        resp = client.get("/insights?budget_id=budget-yearly&window=7d")
        assert resp.status_code == 422
        assert "yearly" in resp.json()["detail"]
        assert "'7d'" in resp.json()["detail"]

    def test_yearly_window_on_monthly_budget_is_422(self, client):
        app.dependency_overrides[get_user_ctx] = lambda: make_user_ctx(
            tables={
                "budgets": [_budget_row()],
                "allocations": [],
                "transactions": [],
                "goals": [],
            }
        )
        resp = client.get("/insights?budget_id=budget-1&window=6m")
        assert resp.status_code == 422
        assert "monthly" in resp.json()["detail"]
        assert "'6m'" in resp.json()["detail"]

    def test_response_carries_horizon_for_monthly(self, client):
        app.dependency_overrides[get_user_ctx] = lambda: make_user_ctx(
            tables={
                "budgets": [_budget_row()],
                "allocations": [],
                "transactions": [],
                "goals": [],
            }
        )
        resp = client.get("/insights?budget_id=budget-1&window=30d")
        assert resp.status_code == 200
        assert resp.json()["summary"]["next_action_horizon_days"] == 7

    def test_response_carries_horizon_for_yearly(self, client):
        app.dependency_overrides[get_user_ctx] = lambda: make_user_ctx(
            tables={
                "budgets": [_yearly_budget_row()],
                "allocations": [],
                "transactions": [],
                "goals": [],
            }
        )
        resp = client.get("/insights?budget_id=budget-yearly&window=6m")
        assert resp.status_code == 200
        assert resp.json()["summary"]["next_action_horizon_days"] == 30
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_insights_route.py -v`

Expected:
- `test_monthly_window_on_yearly_budget_is_422` FAILS — the route currently lets any window through, so the response is 200 (not 422).
- `test_yearly_window_on_monthly_budget_is_422` FAILS for the same reason.
- `test_response_carries_horizon_for_monthly` and `test_response_carries_horizon_for_yearly` PASS (Task 5 already wired the field through).

- [ ] **Step 3: Add the validation to the route**

In `app/routes/insights.py`, update the imports to include `allowed_windows_for_period`:

```python
from app.services.insights_engine import (
    allowed_windows_for_period,
    build_summary,
    resolve_window,
)
```

Then in `get_insights`, insert the validation immediately after `fetch_budget` succeeds and before `resolve_window`:

```python
    try:
        budget, allocations = fetch_budget(ctx, q.budget_id)
    except BudgetNotFound:
        raise HTTPException(status_code=404, detail="budget not found") from None

    if q.window not in allowed_windows_for_period(budget.period):
        raise HTTPException(
            status_code=422,
            detail=f"window {q.window!r} not allowed for {budget.period!r} budget",
        )

    current_start, current_end, prev_start, prev_end = resolve_window(
        q.window, date.today()
    )
```

- [ ] **Step 4: Run the route tests to verify they pass**

Run: `pytest tests/test_insights_route.py -v`

Expected: all `TestGetInsights` methods PASS, including the four new ones.

- [ ] **Step 5: Commit**

```bash
git add app/routes/insights.py tests/test_insights_route.py
git commit -m "$(cat <<'EOF'
feat(routes): reject (window, budget.period) mismatches with 422

The /insights route now calls allowed_windows_for_period(budget.period)
and rejects any q.window outside that set with a 422 naming the
mismatch. Monthly budgets accept only day-scale windows (7d/15d/30d);
yearly budgets accept only month-scale (3m/6m/12m).
EOF
)"
```

---

## Task 7: Full-suite verification

Final sanity check against the committed spec.

**Files:** none (verification only).

- [ ] **Step 1: Run the full test suite**

Run: `pytest tests/ -v`

Expected: all tests PASS. Count the new tests:
- `TestResolveWindow`: 7 (6 window cases + 1 unknown-window case)
- `TestHorizonForWindow`: 6 (parametrized)
- `TestAllowedWindowsForPeriod`: 3
- `TestBuildSummary`: 7 (5 existing + 2 new horizon assertions)
- `TestGetInsights`: 7 (3 existing + 4 new: 2×422 + 2×horizon assertion)

- [ ] **Step 2: Smoke-test the route against a live server**

Spec section 7 calls for these checks. Run `uvicorn app.main:app --reload` in one shell, then from another:

```bash
# Seed a JWT with scripts/mint_jwt.py (or equivalent). Substitute real budget ids.
curl -s -H "Authorization: Bearer $JWT" \
  "http://127.0.0.1:8000/insights?budget_id=<monthly-id>&window=30d" | jq '.summary.next_action_horizon_days'
# Expected: 7

curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer $JWT" \
  "http://127.0.0.1:8000/insights?budget_id=<monthly-id>&window=6m"
# Expected: 422

curl -s -H "Authorization: Bearer $JWT" \
  "http://127.0.0.1:8000/insights?budget_id=<yearly-id>&window=12m" | jq '.summary.next_action_horizon_days'
# Expected: 30

curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer $JWT" \
  "http://127.0.0.1:8000/insights?budget_id=<yearly-id>&window=7d"
# Expected: 422
```

If no seeded yearly budget exists in the local DB, skip that pair and note it in the PR description.

- [ ] **Step 3: Confirm PR-ready state**

Run: `git log --oneline $(git merge-base HEAD main)..HEAD`

Expected: six commits in order:
1. `refactor(window): reshape InsightWindow into period-scoped literals`
2. `docs(schemas): correct BudgetRow.period values in comment`
3. `feat(engine): add _horizon_for_window mapping`
4. `feat(engine): add allowed_windows_for_period helper`
5. `feat(engine): add next_action_horizon_days to InsightSummary`
6. `feat(routes): reject (window, budget.period) mismatches with 422`

No task 7 commit — this step is verification only.
