# Budget-Scoped Insights — Design

**Date:** 2026-04-14
**Scope:** `app/services/insights_engine.py`, `app/db/client.py`, `app/models/schemas.py`, new `app/routes/insights.py` and `app/routes/ai.py`.
**Goal:** Close the "which budget?" design hole so the engine analyzes exactly one user-selected budget over a user-selected time window, and expose that as a `GET /insights` endpoint. No commits will be made as part of this work — user reviews diffs first.

## 1. Context

The engine was built to analyze spending against a budget, but today's interface doesn't let the caller say *which* budget. Concretely:

- `fetch_budgets(user_id)` (`app/db/client.py:73`) returns a **list** of `BudgetRow` plus a flat list of `AllocationRow` spanning every budget the user owns.
- `detect_patterns(transactions, budget)` (`app/services/insights_engine.py:268`) requires a **single** `BudgetRow` for its end-of-period math.
- `category_breakdown` (line 43) and `detect_budget_overspending` (line 206) consume the flat allocation list and silently collide category keys when two budgets allocate the same category — `alloc_map` (line 52 / 212) lets the later budget overwrite the earlier.
- `InsightSummary` (`app/models/schemas.py:159`) has no `budget_id`, so the AI layer has no idea what scope it's explaining.
- Routes (`app/routes/insights.py`, `app/routes/ai.py`) and `app/services/ai_service.py` do not yet exist, so the interface can be fixed at the source rather than patched around.

Users can own multiple concurrent budgets (e.g. a monthly April budget and a yearly 2026 budget active simultaneously). The UI will offer a budget picker plus a fixed set of time-window presets.

**Outcome:** a deterministic pipeline where `(user_id, budget_id, window) → InsightSummary tagged with that budget`, with no ambiguity about which budget's allocations were used and no silent allocation merging.

## 2. Decisions

| # | Decision | Rationale |
|---|---|---|
| 1 | Budget selection is explicit via `budget_id` | Users own multiple concurrent budgets; the UI shows a picker. No "active budget" default that guesses for them. |
| 2 | Analysis window is decoupled from the budget's own dates | The UI offers presets: `1m`, `3m`, `6m`, `1y`, `current_year`, `last_year`. Independent of whether the selected budget is monthly or yearly. |
| 3 | Transport is `GET` with query params bound via a Pydantic model using FastAPI `Depends()` | Semantically correct for a read, cacheable/bookmarkable, URL encodes the full question. The Pydantic model keeps the filter schema reusable: if filters later outgrow query params we can flip the binding to a body with near-zero code change. |
| 4 | `InsightSummary` gains non-optional `budget_id` and `budget_name` | Every summary is traceable back to the scope it analyzed. The AI layer can reference the budget by name. |
| 5 | `user_id` comes from an auth dependency, never a query param | Never trust a client-supplied `user_id`. Auth is out of scope for this work — stub as a typed dependency for now so the interface doesn't change when real auth lands. |
| 6 | `fetch_budgets` is replaced by a single-budget `fetch_budget(user_id, budget_id)` | No other callers. Authorization lives at the data boundary — query filters by both `id` and `user_id`. |
| 7 | `detect_patterns` takes explicit `window_start` / `window_end` instead of a `BudgetRow` | Pattern math is tied to the analysis window now, not to the budget's own period. Also makes the function unit-testable without constructing a synthetic `BudgetRow`. |
| 8 | Add an engine-level `build_summary` orchestrator | Routes must stay thin per CLAUDE.md. The orchestrator is where `(totals, breakdown, anomalies, patterns, goals) → InsightSummary` assembly lives, including the new `budget_id` / `budget_name` fields. |
| 9 | `ai_service.py` and `GET /ai-insights` are follow-up work | The `ai.py` route can raise `NotImplementedError` (returned as 501) until the service lands. Out of scope here — this spec covers only the non-AI pipeline. |

## 3. Interfaces

### 3.1 Schemas (`app/models/schemas.py`)

```python
InsightWindow = Literal["1m", "3m", "6m", "1y", "current_year", "last_year"]


class InsightsQuery(BaseModel):
    budget_id: str
    window: InsightWindow
```

`InsightSummary` extended:

```python
class InsightSummary(BaseModel):
    budget_id: str           # NEW
    budget_name: str         # NEW
    period_label: str
    ...  # existing fields unchanged
```

`InsightPeriod` remains as-is (unused by the new flow, harmless).

### 3.2 DB layer (`app/db/client.py`)

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
```

The implementation filters `budgets` by both `id == :budget_id` **and** `user_id == :user_id` in a single query — authorization is not a separate step. Allocations are pulled with `budget_id == :budget_id`, joined to `categories(name)` as today.

`fetch_budgets` is deleted — no other callers.

`fetch_transactions` is unchanged (already takes `start` / `end`).

### 3.3 Window resolution (`app/services/insights_engine.py`)

```python
def resolve_window(
    window: InsightWindow,
    today: date,
) -> tuple[date, date, date, date]:
    """Return (current_start, current_end, previous_start, previous_end)."""
```

Rules (`current_end = today`, all ranges inclusive):

| window | current range | previous range |
|---|---|---|
| `1m` | last 30 days | 30 days before that |
| `3m` | last 90 days | 90 days before that |
| `6m` | last 180 days | 180 days before that |
| `1y` | last 365 days | 365 days before that |
| `current_year` | Jan 1 of this year → today | same day range in prior year |
| `last_year` | full prior calendar year | year before that |

`today` is a parameter so tests stay deterministic. Only the route boundary calls `date.today()`.

### 3.4 Engine changes (`app/services/insights_engine.py`)

- `detect_patterns` signature changes from `(transactions, budget)` to `(transactions, window_start, window_end)`. `detect_end_of_period_concentration` receives the same two dates instead of a `BudgetRow` — the `relativedelta(days=period_length // 4)` math is unchanged, it just uses the window dates as the period.
- `category_breakdown` and `detect_budget_overspending` are unchanged; they now receive a single budget's allocations, so the `alloc_map` collision is gone by construction.
- New orchestrator:

  ```python
  def build_summary(
      budget: BudgetRow,
      allocations: list[AllocationRow],
      current: list[TransactionRow],
      previous: list[TransactionRow],
      goals: list[GoalRow],
      window_start: date,
      window_end: date,
  ) -> InsightSummary
  ```

  Composes all engine outputs plus `budget_id=budget.id`, `budget_name=budget.name`. `period_label` is derived from the window dates (e.g. `"Mar 15 – Apr 14, 2026"`).

### 3.5 Routes (new)

`app/routes/insights.py`:

```python
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
        budget, allocations, current, previous, goals,
        current_start, current_end,
    )
    return InsightsResponse(summary=summary)
```

`get_current_user` is a stub dependency (reads a header or returns a fixed id for now) placed in `app/routes/deps.py`. Shape is stable; real auth is a future change behind that dependency.

`app/routes/ai.py` mirrors the binding pattern, runs the same pipeline, and raises `HTTPException(501, detail="AI service not implemented")` until `ai_service.py` exists.

`app/main.py` registers both routers.

**Migration note:** if filters later outgrow query params (multi-category, custom dates, merchant filters), flip `Depends()` binding → body binding and change the verb to `POST`. `InsightsQuery` itself doesn't change.

## 4. Error handling

| Case | Behavior |
|---|---|
| `budget_id` missing / not owned by user | `BudgetNotFound` → `404 {"detail": "budget not found"}` |
| Invalid `window` value | FastAPI / Pydantic → `422` automatically |
| Missing `budget_id` query param | FastAPI / Pydantic → `422` automatically |
| Empty transactions for the window | Engine returns zero totals / empty lists; no error |
| AI route hit before service lands | `501 {"detail": "AI service not implemented"}` |

## 5. Testing

Engine-only tests in `tests/test_insights_engine.py` per project conventions — no DB or AI tests.

- Update existing `detect_patterns` tests (currently lines 713, 716, 752) to pass explicit `window_start` / `window_end` instead of a `BudgetRow`.
- Add a `TestResolveWindow` class covering all six `InsightWindow` values with a fixed `today`. Include the `current_year` / `last_year` leap-year edge case (Feb 29).
- Add a `TestBuildSummary` class asserting that `budget_id` and `budget_name` propagate to the `InsightSummary`, and that `period_label` matches the window dates.
- No route / DB tests in scope; those wait until there's a stable auth boundary to mock against.

## 6. Verification

1. `pytest tests/` — all existing tests green after the `detect_patterns` signature change; new `resolve_window` and `build_summary` tests pass.
2. `uvicorn app.main:app --reload`, then:
   ```
   GET /insights?budget_id=<real-id>&window=3m
   ```
   against a seeded Supabase. Confirm the response includes `budget_id` / `budget_name` and that `category_breakdown` reflects **only** that budget's allocations (spot-check a category also allocated by another budget).
3. `GET /insights?budget_id=<another-user's-budget>&window=1m` → expect `404`.
4. Iterate through all six `window` values against the same budget; confirm `period_label`, totals, and `*_change_pct` shift as expected.

## 7. Out of scope / follow-ups

- `app/services/ai_service.py` implementation and `AIRecommendation` generation.
- Real authentication — the `get_current_user` dependency is stubbed.
- UI budget-selector integration.
- Caching of repeated `(user_id, budget_id, window)` queries.
