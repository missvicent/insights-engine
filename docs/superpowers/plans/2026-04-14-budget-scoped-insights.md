# Budget-Scoped Insights Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Scope the insights engine to a single user-selected budget and time window, and expose it as `GET /insights?budget_id=…&window=…`.

**Architecture:** Replace the list-returning `fetch_budgets` with an authorized single-budget fetch. Decouple pattern-detection math from the budget's own dates by introducing a `resolve_window` helper driven by an enum (`1m|3m|6m|1y|current_year|last_year`). Add an engine orchestrator `build_summary` that stamps every `InsightSummary` with `budget_id`/`budget_name`. Expose via `GET /insights` using FastAPI `Depends()` + a Pydantic query model so the interface can later flip to a POST body with near-zero churn.

**Tech Stack:** FastAPI, Pydantic v2, supabase-py, pytest, Python 3.12.

**Spec:** `docs/superpowers/specs/2026-04-14-budget-scoped-insights-design.md`

**Conventions:**
- No automatic commits. Each task ends with a `git add` + `git commit` step that the user reviews before running, per user preference.
- Commit messages follow Conventional Commits (`feat:`, `refactor:`, `test:`, `chore:`), matching the repo's recent history.
- Line length 88, ruff-compliant.

---

## File map

| File | Role | Action |
|---|---|---|
| `app/models/schemas.py` | Pydantic models | Modify — add `InsightWindow`, `InsightsQuery`; extend `InsightSummary` |
| `app/db/client.py` | DB access | Modify — add `BudgetNotFound`, add `fetch_budget`, delete `fetch_budgets` |
| `app/services/insights_engine.py` | Pure engine | Modify — add `resolve_window`, `build_summary`; change `detect_patterns` + `detect_end_of_period_concentration` signatures |
| `app/routes/deps.py` | Shared route dependencies | Create — stub `get_current_user` |
| `app/routes/insights.py` | `/insights` route | Create |
| `app/routes/ai.py` | `/ai-insights` route (stub) | Create |
| `app/main.py` | App wiring | Modify — register routers |
| `tests/conftest.py` | Shared factories | Modify — no-op if `make_budget` already has the fields we need; otherwise add `make_goal` factory |
| `tests/test_insights_engine.py` | Engine tests | Modify — update `detect_patterns` callers; add `TestResolveWindow`, `TestBuildSummary` |

---

## Task 1: Add `InsightWindow` literal and `InsightsQuery` model

**Files:**
- Modify: `app/models/schemas.py`

- [ ] **Step 1: Add the window literal and query model**

At the top of `app/models/schemas.py`, alongside the existing `AnomalyType` / `PatternType` literal definitions (after line 22), add:

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

Then, in the "Query params" section at the bottom of the file (after the existing `InsightPeriod` class at line 211), add:

```python
class InsightsQuery(BaseModel):
    budget_id: str
    window: InsightWindow
```

- [ ] **Step 2: Verify import resolves**

Run: `python -c "from app.models.schemas import InsightWindow, InsightsQuery; print(InsightsQuery(budget_id='b', window='3m'))"`
Expected: prints `budget_id='b' window='3m'` with no error.

- [ ] **Step 3: Commit**

```bash
git add app/models/schemas.py
git commit -m "feat(schemas): add InsightWindow literal and InsightsQuery model"
```

---

## Task 2: Extend `InsightSummary` with `budget_id` and `budget_name`

**Files:**
- Modify: `app/models/schemas.py:159-183`

- [ ] **Step 1: Add the two fields at the top of `InsightSummary`**

Replace the opening of `InsightSummary` (around line 159) so it reads:

```python
class InsightSummary(BaseModel):
    """
    Core output of the insights engine.
    This is what gets sent to the AI layer — not raw transactions.
    """

    budget_id: str
    budget_name: str

    period_label: str  # e.g. "December 2025"
    total_income: float
    total_expenses: float
    net: float  # income - expenses
    savings_rate: Optional[float] = None

    # vs last period
    expenses_change_pct: Optional[float] = None
    income_change_pct: Optional[float] = None

    category_breakdown: list[CategoryBreakdown]
    anomalies: list[Anomaly]
    patterns: list[Pattern]
    goals: list[GoalProgress]
    debt: Optional[DebtSummary] = None

    # raw counts for context
    transaction_count: int
    recurring_count: int
```

- [ ] **Step 2: Smoke-check construction**

Run:

```bash
python -c "
from app.models.schemas import InsightSummary
s = InsightSummary(
    budget_id='b', budget_name='April', period_label='Apr 2026',
    total_income=0, total_expenses=0, net=0,
    category_breakdown=[], anomalies=[], patterns=[], goals=[],
    transaction_count=0, recurring_count=0,
)
print(s.budget_id, s.budget_name)
"
```

Expected: `b April`.

- [ ] **Step 3: Commit**

```bash
git add app/models/schemas.py
git commit -m "feat(schemas): add budget_id and budget_name to InsightSummary"
```

---

## Task 3: Add `BudgetNotFound` exception and `fetch_budget`; delete `fetch_budgets`

**Files:**
- Modify: `app/db/client.py:73-102`

- [ ] **Step 1: Add the exception and new fetcher, delete the old one**

Open `app/db/client.py`. Replace the entire `fetch_budgets` function (lines 73-102) with:

```python
class BudgetNotFound(Exception):
    """Raised when a budget_id does not exist or is not owned by the user."""


def fetch_budget(
    user_id: str,
    budget_id: str,
    db: Client | None = None,
) -> tuple[BudgetRow, list[AllocationRow]]:
    """Fetch one budget (authorized to user_id) and its allocations.

    Raises BudgetNotFound when the row is missing or not owned by the user.
    """
    if db is None:
        db = get_supabase()

    budget_response = (
        db.table("budgets")
        .select("*")
        .eq("id", budget_id)
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )

    if not budget_response.data:
        raise BudgetNotFound(budget_id)

    budget = BudgetRow(**budget_response.data[0])

    alloc_response = (
        db.table("allocations")
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

- [ ] **Step 2: Confirm no other callers referenced `fetch_budgets`**

Run: `grep -rn "fetch_budgets" app/ tests/ 2>/dev/null || echo "none"`
Expected: `none` (or no output lines beyond the deleted definition).

- [ ] **Step 3: Verify imports still resolve**

Run: `python -c "from app.db.client import fetch_budget, BudgetNotFound; print('ok')"`
Expected: `ok`.

- [ ] **Step 4: Commit**

```bash
git add app/db/client.py
git commit -m "refactor(db): replace fetch_budgets with authorized fetch_budget"
```

---

## Task 4: Add `resolve_window` helper to the engine

**Files:**
- Modify: `app/services/insights_engine.py`
- Test: `tests/test_insights_engine.py`

- [ ] **Step 1: Write the failing tests**

Append this class to `tests/test_insights_engine.py` (after the last existing `TestXxx` class):

```python
class TestResolveWindow:
    def test_1m(self):
        from app.services.insights_engine import resolve_window

        today = date(2026, 4, 14)
        cs, ce, ps, pe = resolve_window("1m", today)
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
        assert (ce - cs).days == 180
        assert (pe - ps).days == 180
        assert pe == cs - pd.Timedelta(days=1).to_pytimedelta()

    def test_1y(self):
        from app.services.insights_engine import resolve_window

        today = date(2026, 4, 14)
        cs, ce, ps, pe = resolve_window("1y", today)
        assert (ce - cs).days == 365
        assert (pe - ps).days == 365

    def test_current_year(self):
        from app.services.insights_engine import resolve_window

        today = date(2026, 4, 14)
        cs, ce, ps, pe = resolve_window("current_year", today)
        assert cs == date(2026, 1, 1)
        assert ce == today
        assert ps == date(2025, 1, 1)
        assert pe == date(2025, 4, 14)

    def test_current_year_leap_edge(self):
        from app.services.insights_engine import resolve_window

        today = date(2024, 2, 29)
        _, _, ps, pe = resolve_window("current_year", today)
        # Prior year has no Feb 29 → clamp to Feb 28
        assert pe == date(2023, 2, 28)
        assert ps == date(2023, 1, 1)

    def test_last_year(self):
        from app.services.insights_engine import resolve_window

        today = date(2026, 4, 14)
        cs, ce, ps, pe = resolve_window("last_year", today)
        assert cs == date(2025, 1, 1)
        assert ce == date(2025, 12, 31)
        assert ps == date(2024, 1, 1)
        assert pe == date(2024, 12, 31)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_insights_engine.py::TestResolveWindow -v`
Expected: all 7 tests fail with `ImportError` on `resolve_window`.

- [ ] **Step 3: Implement `resolve_window`**

In `app/services/insights_engine.py`, add the import and function. First update the imports block at the top (after line 23) to include `date` and `InsightWindow`:

```python
from datetime import date, timedelta
```

and extend the `from app.models.schemas import (...)` block to include `InsightWindow`:

```python
from app.models.schemas import (
    AllocationRow,
    Anomaly,
    BudgetRow,
    CategoryBreakdown,
    FinancialTotals,
    GoalRow,
    GoalProgress,
    InsightWindow,
    Pattern,
    TransactionRow,
)
```

Then add, just below the `HIGH_SEVERITY_THRESHOLD` constant (line 26):

```python
def resolve_window(
    window: InsightWindow,
    today: date,
) -> tuple[date, date, date, date]:
    """Return (current_start, current_end, previous_start, previous_end)."""
    if window in {"1m", "3m", "6m", "1y"}:
        span_days = {"1m": 30, "3m": 90, "6m": 180, "1y": 365}[window]
        current_end = today
        current_start = today - timedelta(days=span_days)
        previous_end = current_start - timedelta(days=1)
        previous_start = previous_end - timedelta(days=span_days)
        return current_start, current_end, previous_start, previous_end

    if window == "current_year":
        current_start = date(today.year, 1, 1)
        current_end = today
        previous_start = date(today.year - 1, 1, 1)
        previous_end = _clamp_to_month_end(today.year - 1, today.month, today.day)
        return current_start, current_end, previous_start, previous_end

    if window == "last_year":
        current_start = date(today.year - 1, 1, 1)
        current_end = date(today.year - 1, 12, 31)
        previous_start = date(today.year - 2, 1, 1)
        previous_end = date(today.year - 2, 12, 31)
        return current_start, current_end, previous_start, previous_end

    raise ValueError(f"unknown window: {window}")


def _clamp_to_month_end(year: int, month: int, day: int) -> date:
    """Build a date, clamping day to the last valid day of that month.

    Handles Feb 29 → Feb 28 when moving into a non-leap year.
    """
    import calendar

    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(day, last_day))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_insights_engine.py::TestResolveWindow -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add app/services/insights_engine.py tests/test_insights_engine.py
git commit -m "feat(engine): add resolve_window helper"
```

---

## Task 5: Change `detect_patterns` / `detect_end_of_period_concentration` signatures

**Files:**
- Modify: `app/services/insights_engine.py:268-355`
- Modify: `tests/test_insights_engine.py` (existing pattern tests)

- [ ] **Step 1: Update existing pattern tests to new signature**

In `tests/test_insights_engine.py`, find every call to `detect_patterns(...)` and `detect_end_of_period_concentration(...)`. Replace the `BudgetRow` argument with two explicit `date` arguments.

Specifically, search for these call sites and rewrite:

```python
# OLD
detect_patterns([], make_budget())
# NEW
detect_patterns([], date(2026, 4, 1), date(2026, 4, 30))

# OLD
detect_patterns([make_income(500.0)], make_budget())
# NEW
detect_patterns([make_income(500.0)], date(2026, 4, 1), date(2026, 4, 30))

# OLD
result = detect_patterns(txs, make_budget())
# NEW
result = detect_patterns(txs, date(2026, 4, 1), date(2026, 4, 30))
```

For `detect_end_of_period_concentration` callers, the call changes from passing a `BudgetRow` to passing two dates in the same positions. Replace `make_budget()` (or any `BudgetRow` variable) with `date(2026, 4, 1), date(2026, 4, 30)` matching that budget's start/end.

If any test explicitly constructs a `BudgetRow` with non-default dates and passes it to `detect_patterns`, preserve those exact dates in the two new arguments.

- [ ] **Step 2: Run tests to verify they fail against the current engine**

Run: `pytest tests/test_insights_engine.py::TestDetectPatterns tests/test_insights_engine.py::TestDetectEndOfPeriodConcentration -v`
Expected: failures (signature mismatch — engine still expects `BudgetRow`).

- [ ] **Step 3: Update `detect_patterns` signature**

In `app/services/insights_engine.py`, replace the existing `detect_patterns` function (lines 268-293) with:

```python
def detect_patterns(
    transactions: list[TransactionRow],
    window_start: date,
    window_end: date,
) -> list[Pattern]:
    expenses = [t for t in transactions if t.type == "expense"]
    if not expenses:
        return []

    df = pd.DataFrame(
        [
            {
                "amount": t.amount,
                "date": pd.to_datetime(t.transaction_date),
                "category_name": t.category_name,
                "merchant": t.merchant,
            }
            for t in expenses
        ]
    )

    total_expenses = sum(t.amount for t in expenses)

    return [
        *detect_weekend_spend(df, total_expenses),
        *detect_end_of_period_concentration(df, window_start, window_end),
        *detect_frequent_categories(df),
    ]
```

- [ ] **Step 4: Update `detect_end_of_period_concentration` signature**

In the same file, replace the existing `detect_end_of_period_concentration` (lines 331-355) with:

```python
def detect_end_of_period_concentration(
    df: pd.DataFrame,
    window_start: date,
    window_end: date,
) -> list[Pattern]:
    period_length = (window_end - window_start).days
    if period_length <= 0:
        return []

    last_quarter = pd.Timestamp(
        window_end - relativedelta(days=period_length // 4)
    )

    end_total = df[df["date"] >= last_quarter]["amount"].sum()
    total = df["amount"].sum()

    if total > 0 and end_total / total > 0.40:
        pct = (end_total / total) * 100
        return [
            Pattern(
                type="end_of_period_concentration",
                message=(f"{pct:.1f}% of spending in the last quarter of the month"),
                data={},
            )
        ]

    return []
```

- [ ] **Step 5: Run the full engine test suite**

Run: `pytest tests/test_insights_engine.py -v`
Expected: all tests pass (the edited patterns tests plus every pre-existing test).

- [ ] **Step 6: Commit**

```bash
git add app/services/insights_engine.py tests/test_insights_engine.py
git commit -m "refactor(engine): detect_patterns takes explicit window dates"
```

---

## Task 6: Add `build_summary` orchestrator

**Files:**
- Modify: `app/services/insights_engine.py`
- Modify: `tests/test_insights_engine.py`
- Modify: `tests/conftest.py`

- [ ] **Step 1: Add a `make_goal` factory**

Append to `tests/conftest.py`:

```python
def make_goal(
    *,
    name: str = "Emergency fund",
    target_amount: float = 1000.0,
    current_amount: float = 250.0,
    target_date: Optional[date] = None,
    is_achieved: bool = False,
    id: Optional[str] = None,
) -> "GoalRow":
    from app.models.schemas import GoalRow

    return GoalRow(
        id=id or _uid("goal"),
        name=name,
        target_amount=target_amount,
        current_amount=current_amount,
        target_date=target_date,
        is_achieved=is_achieved,
    )
```

- [ ] **Step 2: Write failing tests for `build_summary`**

Append this class to `tests/test_insights_engine.py`:

```python
class TestBuildSummary:
    def test_stamps_budget_identity(self):
        from app.services.insights_engine import build_summary
        from tests.conftest import make_goal

        budget = make_budget(id="budget-xyz")
        budget_with_name = budget.model_copy(update={"name": "April 2026"})

        summary = build_summary(
            budget=budget_with_name,
            allocations=[],
            current=[make_income(1000.0), make_expense(200.0)],
            previous=[make_income(800.0), make_expense(150.0)],
            goals=[make_goal()],
            window_start=date(2026, 4, 1),
            window_end=date(2026, 4, 30),
        )

        assert summary.budget_id == "budget-xyz"
        assert summary.budget_name == "April 2026"

    def test_period_label_matches_window(self):
        from app.services.insights_engine import build_summary

        summary = build_summary(
            budget=make_budget(),
            allocations=[],
            current=[],
            previous=[],
            goals=[],
            window_start=date(2026, 3, 15),
            window_end=date(2026, 4, 14),
        )

        assert summary.period_label == "Mar 15 – Apr 14, 2026"

    def test_totals_and_change_pct(self):
        from app.services.insights_engine import build_summary

        summary = build_summary(
            budget=make_budget(),
            allocations=[],
            current=[make_income(1000.0), make_expense(400.0)],
            previous=[make_income(800.0), make_expense(200.0)],
            goals=[],
            window_start=date(2026, 4, 1),
            window_end=date(2026, 4, 30),
        )

        assert summary.total_income == pytest.approx(1000.0)
        assert summary.total_expenses == pytest.approx(400.0)
        assert summary.net == pytest.approx(600.0)
        assert summary.income_change_pct == pytest.approx(25.0)
        assert summary.expenses_change_pct == pytest.approx(100.0)
        assert summary.transaction_count == 2

    def test_empty_inputs(self):
        from app.services.insights_engine import build_summary

        summary = build_summary(
            budget=make_budget(),
            allocations=[],
            current=[],
            previous=[],
            goals=[],
            window_start=date(2026, 4, 1),
            window_end=date(2026, 4, 30),
        )

        assert summary.total_income == 0
        assert summary.total_expenses == 0
        assert summary.category_breakdown == []
        assert summary.anomalies == []
        assert summary.patterns == []
        assert summary.transaction_count == 0
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `pytest tests/test_insights_engine.py::TestBuildSummary -v`
Expected: all 4 tests fail with `ImportError` on `build_summary`.

- [ ] **Step 4a: Repair the broken `compute_goal_progress` function**

The current `compute_goal_progress` in `app/services/insights_engine.py` (around line 393) was committed as a stub — it computes a local variable but returns `None`. Replace its body entirely so the function reads:

```python
def compute_goal_progress(goals: list[GoalRow]) -> list[GoalProgress]:
    today = date.today()
    result: list[GoalProgress] = []
    for goal in goals:
        if goal.is_achieved:
            continue

        progress_pct = 0.0
        if goal.target_amount > 0:
            progress_pct = round(
                (goal.current_amount / goal.target_amount) * 100, 2
            )

        days_remaining: int | None = None
        on_track = True
        if goal.target_date is not None:
            days_remaining = (goal.target_date - today).days
            # On track iff (percent complete) >= (percent of time elapsed).
            # If the deadline has passed and the goal isn't achieved, not on track.
            if days_remaining < 0:
                on_track = False

        result.append(
            GoalProgress(
                goal_id=goal.id,
                name=goal.name,
                target_amount=goal.target_amount,
                current_amount=goal.current_amount,
                progress_pct=progress_pct,
                days_remaining=days_remaining,
                on_track=on_track,
            )
        )
    return result
```

- [ ] **Step 4b: Implement `build_summary`**

In `app/services/insights_engine.py`, at the bottom of the file (after `compute_goal_progress`), add:

```python
def build_summary(
    budget: BudgetRow,
    allocations: list[AllocationRow],
    current: list[TransactionRow],
    previous: list[TransactionRow],
    goals: list[GoalRow],
    window_start: date,
    window_end: date,
) -> "InsightSummary":
    from app.models.schemas import InsightSummary

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
    )


def _format_period_label(start: date, end: date) -> str:
    """Human-readable window label, e.g. 'Mar 15 – Apr 14, 2026'."""
    if start.year == end.year:
        return f"{start.strftime('%b %-d')} – {end.strftime('%b %-d, %Y')}"
    return f"{start.strftime('%b %-d, %Y')} – {end.strftime('%b %-d, %Y')}"
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_insights_engine.py::TestBuildSummary -v`
Expected: 4 passed.

- [ ] **Step 6: Run the full suite to catch regressions**

Run: `pytest tests/`
Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add app/services/insights_engine.py tests/test_insights_engine.py tests/conftest.py
git commit -m "feat(engine): add build_summary orchestrator"
```

---

## Task 7: Add `get_current_user` stub dependency

**Files:**
- Create: `app/routes/deps.py`

- [ ] **Step 1: Create the file**

Create `app/routes/deps.py` with:

```python
"""Shared FastAPI dependencies for the routes layer.

`get_current_user` is a stub until real authentication lands. It reads the
`x-user-id` header and returns it verbatim. Routes call this via `Depends(...)`
so swapping in a real auth implementation is a one-file change.
"""

from fastapi import Header, HTTPException


def get_current_user(x_user_id: str | None = Header(default=None)) -> str:
    if not x_user_id:
        raise HTTPException(status_code=401, detail="missing x-user-id header")
    return x_user_id
```

- [ ] **Step 2: Smoke-check the import**

Run: `python -c "from app.routes.deps import get_current_user; print('ok')"`
Expected: `ok`.

- [ ] **Step 3: Commit**

```bash
git add app/routes/deps.py
git commit -m "feat(routes): add stub get_current_user dependency"
```

---

## Task 8: Add `GET /insights` route

**Files:**
- Create: `app/routes/insights.py`

- [ ] **Step 1: Create the route**

Create `app/routes/insights.py` with:

```python
from datetime import date

from fastapi import APIRouter, Depends, HTTPException

from app.db.client import (
    BudgetNotFound,
    fetch_budget,
    fetch_goals,
    fetch_transactions,
)
from app.models.schemas import InsightsQuery, InsightsResponse
from app.routes.deps import get_current_user
from app.services.insights_engine import build_summary, resolve_window

router = APIRouter()


@router.get("/insights", response_model=InsightsResponse)
def get_insights(
    q: InsightsQuery = Depends(),
    user_id: str = Depends(get_current_user),
) -> InsightsResponse:
    try:
        budget, allocations = fetch_budget(user_id, q.budget_id)
    except BudgetNotFound:
        raise HTTPException(status_code=404, detail="budget not found")

    current_start, current_end, prev_start, prev_end = resolve_window(
        q.window, date.today()
    )
    current = fetch_transactions(user_id, current_start, current_end)
    previous = fetch_transactions(user_id, prev_start, prev_end)
    goals = fetch_goals(user_id)

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

- [ ] **Step 2: Smoke-check the import**

Run: `python -c "from app.routes.insights import router; print([r.path for r in router.routes])"`
Expected: `['/insights']`.

- [ ] **Step 3: Commit**

```bash
git add app/routes/insights.py
git commit -m "feat(routes): add GET /insights"
```

---

## Task 9: Add `GET /ai-insights` stub route

**Files:**
- Create: `app/routes/ai.py`

- [ ] **Step 1: Create the stub route**

Create `app/routes/ai.py` with:

```python
from datetime import date

from fastapi import APIRouter, Depends, HTTPException

from app.db.client import (
    BudgetNotFound,
    fetch_budget,
    fetch_goals,
    fetch_transactions,
)
from app.models.schemas import InsightsQuery
from app.routes.deps import get_current_user
from app.services.insights_engine import build_summary, resolve_window

router = APIRouter()


@router.get("/ai-insights")
def get_ai_insights(
    q: InsightsQuery = Depends(),
    user_id: str = Depends(get_current_user),
):
    """Stub: runs the deterministic pipeline, then 501s until ai_service exists."""
    try:
        budget, allocations = fetch_budget(user_id, q.budget_id)
    except BudgetNotFound:
        raise HTTPException(status_code=404, detail="budget not found")

    current_start, current_end, prev_start, prev_end = resolve_window(
        q.window, date.today()
    )
    current = fetch_transactions(user_id, current_start, current_end)
    previous = fetch_transactions(user_id, prev_start, prev_end)
    goals = fetch_goals(user_id)

    _summary = build_summary(
        budget=budget,
        allocations=allocations,
        current=current,
        previous=previous,
        goals=goals,
        window_start=current_start,
        window_end=current_end,
    )
    raise HTTPException(status_code=501, detail="AI service not implemented")
```

- [ ] **Step 2: Smoke-check the import**

Run: `python -c "from app.routes.ai import router; print([r.path for r in router.routes])"`
Expected: `['/ai-insights']`.

- [ ] **Step 3: Commit**

```bash
git add app/routes/ai.py
git commit -m "feat(routes): add GET /ai-insights stub"
```

---

## Task 10: Register routers in `app/main.py`

**Files:**
- Modify: `app/main.py`

- [ ] **Step 1: Wire the routers**

Replace the contents of `app/main.py` with:

```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routes import ai as ai_routes
from app.routes import insights as insights_routes

app = FastAPI(title="finance-insights-engine")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(insights_routes.router)
app.include_router(ai_routes.router)
```

- [ ] **Step 2: Verify the app boots and exposes both routes**

Run: `python -c "from app.main import app; print(sorted({r.path for r in app.routes}))"`
Expected output contains `/insights` and `/ai-insights`.

- [ ] **Step 3: Commit**

```bash
git add app/main.py
git commit -m "feat(app): register insights and ai-insights routers"
```

---

## Task 11: End-to-end verification

No code changes — human + live Supabase checks described in the spec.

- [ ] **Step 1: Run the full engine test suite**

Run: `pytest tests/`
Expected: all tests pass.

- [ ] **Step 2: Boot the API**

Run: `uvicorn app.main:app --reload`
Expected: server starts on `http://127.0.0.1:8000`, no startup errors.

- [ ] **Step 3: Happy-path request**

Run (in another terminal, substituting a real budget id):

```bash
curl -s -H "x-user-id: <real-user-id>" \
  "http://127.0.0.1:8000/insights?budget_id=<real-budget-id>&window=3m" | jq .
```

Expected: JSON response where `summary.budget_id` matches the request and `summary.budget_name` is the budget's name. `summary.category_breakdown` reflects **only** that budget's allocations.

- [ ] **Step 4: Authorization check**

Run with a `budget_id` owned by a different user (or one that does not exist):

```bash
curl -s -o /dev/null -w "%{http_code}\n" \
  -H "x-user-id: <real-user-id>" \
  "http://127.0.0.1:8000/insights?budget_id=<other-users-budget-id>&window=1m"
```

Expected: `404`.

- [ ] **Step 5: Missing header check**

Run: `curl -s -o /dev/null -w "%{http_code}\n" "http://127.0.0.1:8000/insights?budget_id=x&window=1m"`
Expected: `401`.

- [ ] **Step 6: Invalid window check**

Run: `curl -s -o /dev/null -w "%{http_code}\n" -H "x-user-id: u" "http://127.0.0.1:8000/insights?budget_id=x&window=nope"`
Expected: `422`.

- [ ] **Step 7: AI route still stubs out**

Run:

```bash
curl -s -o /dev/null -w "%{http_code}\n" \
  -H "x-user-id: <real-user-id>" \
  "http://127.0.0.1:8000/ai-insights?budget_id=<real-budget-id>&window=1m"
```

Expected: `501`.

- [ ] **Step 8: Window-coverage spot check**

Call `GET /insights` with each of `1m`, `3m`, `6m`, `1y`, `current_year`, `last_year` against the same budget. Confirm `summary.period_label` changes as expected and `summary.*_change_pct` fields populate (may be `null` when the previous window has zero baseline — that is fine).

No commit for this task — verification only.
