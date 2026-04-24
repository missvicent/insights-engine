# Action Horizon & Budget-Scoped Windows — Design

**Date:** 2026-04-21
**Scope:** `app/models/schemas.py`, `app/services/insights_engine.py`, `app/routes/insights.py`, `tests/test_insights_engine.py`, `tests/test_insights_route.py`.
**Goal:** Close the window/horizon mismatch between the deterministic engine and the AI layer by making the action horizon an engine-computed field on `InsightSummary`, and reshape `InsightWindow` so the windows surfaced per budget match that budget's period.

## 1. Context

`InsightSummary` is the sole input the AI layer will receive. Today the summary carries the analysis window as a formatted `period_label`, but nothing tells the LLM *how far into the future* its recommendations should reach. Without that, the LLM guesses — and its guess routinely mismatches the user's selected window (a one-week trend recommending year-long changes, or a year-long trend recommending tomorrow's action).

Two shape problems also exist today in `InsightWindow` (`app/models/schemas.py:24`):

- The enum mixes day-scale and year-scale presets (`1m`, `3m`, `6m`, `1y`, `current_year`, `last_year`) without regard to which budget they apply to. The UI wants different presets for monthly versus yearly budgets; the current enum forces a single bag.
- `current_year` / `last_year` are retrospective snapshots that don't serve the insights/action flow — they're reporting, not recommending.

`BudgetRow.period` is commented as `'monthly' | 'daily'`. The actual values are `'monthly' | 'yearly'`. The comment has been wrong since the schema was introduced.

**Outcome:** `InsightSummary` gains a non-optional `next_action_horizon_days: int`. `InsightWindow` is reshaped into two period-scoped buckets (day-scale for monthly budgets, month-scale for yearly). The route rejects windows that don't belong to the requested budget's period.

## 2. Decisions

| # | Decision | Rationale |
|---|---|---|
| 1 | Add `next_action_horizon_days: int` to `InsightSummary`, engine-populated, non-optional | The engine already owns window semantics. Putting the horizon in the schema forces the LLM to echo it rather than guess. Non-optional because every summary has exactly one window and one horizon. |
| 2 | Reshape `InsightWindow` to `"7d" \| "15d" \| "30d" \| "3m" \| "6m" \| "12m"` | Day-scale for monthly budgets, month-scale for yearly. Drops `1m` (replaced by `30d`), `1y` (replaced by `12m`), and both retrospective windows (`current_year`, `last_year`) which don't serve the action flow. |
| 3 | Windows are budget-period-scoped; cross-bucket combinations (e.g. `7d` on a yearly budget) are rejected at the route with **422** | Prevents mismatched prescriptions. Validation at the route keeps the engine pure and makes the failure mode visible in the API contract. |
| 4 | Horizon mapping lives in the engine, not the route | The engine already owns `resolve_window`; window → horizon is the same kind of logic. One module is the single source of truth for window semantics. |
| 5 | `build_summary` gains a `window: InsightWindow` parameter | The engine needs the enum to compute the horizon. Passing the enum rather than a precomputed int keeps the mapping inside the engine (see decision 4). |
| 6 | New helper `allowed_windows_for_period(period) -> set[InsightWindow]` | Route-side validation needs a lookup. Exposed from the engine so the truth about "which windows belong to which period" lives next to `resolve_window` and the horizon map. |
| 7 | Breaking change on `InsightWindow` is acceptable | Frontend is in-house and updated in lockstep. API is not public. Call out in the PR description. |
| 8 | Fix `BudgetRow.period` comment to `'monthly' \| 'yearly'` | Trivial, but the wrong comment materially misled the window-bucket design work. |
| 9 | AI prompt work is out of scope | No `ai_service.py` exists yet. When it lands, the prompt will reference `summary.next_action_horizon_days` directly. |

## 3. Horizon mapping

| Window | Horizon (days) | Rationale |
|---|---|---|
| `7d` | 3 | ~half the window. A 7-day signal can't credibly prescribe a week of action; 3 days keeps the action proportional to the evidence. |
| `15d` | 7 | ~half the window. "This week" is the natural action unit. |
| `30d` | 7 | ~¼ the window, capped at one week. Monthly data is short-term enough to expect action *this* week — before the next pay cycle or bill run. |
| `3m` | 14 | ~1/6 the window. Long enough signal that action shifts from "this week" to "next paycheck"; two weeks matches a bi-weekly pay cycle. |
| `6m` | 30 | Saturation: one budget cycle. Half-year trends warrant "adjust next month", not micro-tweaks. |
| `12m` | 30 | Same saturation point. Year-long trends also act at the monthly cycle. |

Governing principle: the horizon matches the decision cycle the signal can legitimately inform. Short windows get a fraction of the window; long windows saturate at one budget cycle (30 days) because action beyond that isn't tracked at the individual level.

## 4. Interfaces

### 4.1 Schemas (`app/models/schemas.py`)

Replace `InsightWindow`:

```python
InsightWindow = Literal["7d", "15d", "30d", "3m", "6m", "12m"]
```

Extend `InsightSummary`:

```python
class InsightSummary(BaseModel):
    ...  # existing fields unchanged
    next_action_horizon_days: int  # NEW — engine-computed from window
```

Fix `BudgetRow.period` comment:

```python
period: str  # 'monthly' | 'yearly'
```

`InsightsQuery` itself is unchanged in shape — it already has `window: InsightWindow`. The literal change propagates automatically via Pydantic validation.

### 4.2 Engine (`app/services/insights_engine.py`)

**`resolve_window`** — replace the body to handle the new literals. `current_end = today`; all ranges inclusive.

| window | current span | previous span |
|---|---|---|
| `7d` | last 7 days | 7 days before that |
| `15d` | last 15 days | 15 days before that |
| `30d` | last 30 days | 30 days before that |
| `3m` | last 90 days | 90 days before that |
| `6m` | last 180 days | 180 days before that |
| `12m` | last 365 days | 365 days before that |

The `current_year` / `last_year` / `_clamp_to_month_end` branches are removed; none are reachable under the new enum. `today` remains a parameter so tests stay deterministic.

**New module-level mapping and helper:**

```python
_HORIZON_DAYS: dict[InsightWindow, int] = {
    "7d": 3, "15d": 7, "30d": 7,
    "3m": 14, "6m": 30, "12m": 30,
}


def _horizon_for_window(window: InsightWindow) -> int:
    return _HORIZON_DAYS[window]
```

**New public helper:**

```python
_ALLOWED_WINDOWS: dict[str, set[InsightWindow]] = {
    "monthly": {"7d", "15d", "30d"},
    "yearly":  {"3m", "6m", "12m"},
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

**`build_summary`** — add `window: InsightWindow` parameter and populate the new field:

```python
def build_summary(
    budget: BudgetRow,
    allocations: list[AllocationRow],
    current: list[TransactionRow],
    previous: list[TransactionRow],
    goals: list[GoalRow],
    window: InsightWindow,       # NEW
    window_start: date,
    window_end: date,
) -> InsightSummary:
    ...
    return InsightSummary(
        ...  # existing fields unchanged
        next_action_horizon_days=_horizon_for_window(window),
    )
```

Parameter order: `window` immediately precedes `window_start` so callers read "window, then the resolved dates for that window" left-to-right.

### 4.3 Route (`app/routes/insights.py`)

After `fetch_budget`, validate before doing any further work:

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
```

Then pass `q.window` through to `build_summary`:

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

The route remains thin: fetch, validate scope, resolve window, fetch data, build summary, return.

## 5. Error handling

| Case | Behavior |
|---|---|
| Invalid `window` value (not in the literal) | FastAPI / Pydantic → 422 automatically |
| `window` valid but mismatched to budget period (e.g. `7d` on yearly) | Route raises 422 with `detail` naming the mismatch |
| Unknown `budget.period` in the DB row | `allowed_windows_for_period` raises `ValueError`. This is a data integrity problem, not a user error — let it surface as 500. |
| `budget_id` missing / not owned by user | `BudgetNotFound` → 404 (unchanged) |
| Empty transactions for the window | Engine returns zero totals / empty lists; horizon field still populated. |

## 6. Testing

### `tests/test_insights_engine.py`

- **`TestResolveWindow`** — replace the current year-based cases. One test per new literal (`7d`, `15d`, `30d`, `3m`, `6m`, `12m`) using a fixed `today`. Assert both current and previous spans.
- **`TestHorizonForWindow`** — one parametrized test over the six windows asserting the mapping table.
- **`TestAllowedWindowsForPeriod`** — assert monthly returns `{"7d", "15d", "30d"}`, yearly returns `{"3m", "6m", "12m"}`, unknown period raises `ValueError`.
- **`TestBuildSummary`** — extend existing tests to assert `next_action_horizon_days` is populated. Cover at least one monthly (`30d → 7`) and one yearly (`6m → 30`) case.

### `tests/test_insights_route.py`

- Happy-path: existing tests updated to use new window literals (e.g. `1m` → `30d`, `1y` → `12m`).
- New: `GET /insights?budget_id=<monthly>&window=6m` → 422.
- New: `GET /insights?budget_id=<yearly>&window=7d` → 422.
- New: response includes `next_action_horizon_days` with the expected value for the requested window.

No DB changes. No new fixtures beyond setting `budget.period` where needed.

## 7. Verification

1. `pytest tests/` — all green. New tests above pass; existing tests updated for the new window literals pass without further changes.
2. `uvicorn app.main:app --reload`, then against a seeded Supabase:
   - `GET /insights?budget_id=<monthly-id>&window=30d` → 200, response includes `next_action_horizon_days: 7`.
   - `GET /insights?budget_id=<monthly-id>&window=6m` → 422 with mismatch detail.
   - `GET /insights?budget_id=<yearly-id>&window=12m` → 200, `next_action_horizon_days: 30`.
   - `GET /insights?budget_id=<yearly-id>&window=7d` → 422.
3. Confirm the returned `period_label` still matches the resolved window dates (no regression from the `resolve_window` rewrite).

## 8. Migration / blast radius

- **Breaking:** any client sending `1m`, `1y`, `current_year`, or `last_year` will now get 422. Frontend is in-house and gets updated in the same PR cycle. API is not public. Call out in the PR description.
- **No DB schema change.** `budgets.period` values already use `'monthly' | 'yearly'`; only the code comment was wrong.
- **No AI service change** — `ai_service.py` does not yet exist. When it lands, the prompt reads `summary.next_action_horizon_days` directly.

## 9. Out of scope / follow-ups

- Writing the AI prompt that consumes the horizon field.
- Frontend UI updates to expose the new window literals (separate PR, coordinated).
- Any change to `AIRecommendation` or the `/ai-insights` route.
- Caching or memoization of `(budget_id, window)` responses.
