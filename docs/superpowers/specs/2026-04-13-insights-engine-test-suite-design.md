# Insights Engine — Production-Grade Test Suite Design

**Date:** 2026-04-13
**Scope:** `app/services/insights_engine.py` (pure functions only)
**Goal:** Branch + edge-case coverage ("top-company grade") plus inline fixes for
defects surfaced while writing tests. No commits will be made as part of this
work — user reviews diffs first.

## 1. Context

The engine owns all deterministic financial calculations that feed the AI layer
and the `/insights` route. Existing tests cover only the recent
`category_name` fix (4 tests). The remaining ~12 public functions are untested.
A read-through surfaced multiple defects (format-spec syntax error, `None`
returns spread with `*`, mixed `date`/`pd.Timestamp` comparisons, misleading
names, convention drift). These were fixed *before* writing the test suite,
so tests assert correct behavior, not legacy bugs.

## 2. Fixes landed prior to tests

| # | Item | Fix |
|---|---|---|
| 1 | `detect_end_of_period_concentration` return `None` | returns `[]` |
| 2 | `detect_frequent_categories` return `None` | returns `[]` |
| 3 | Mixed `date` / `pd.Timestamp` comparison | wrap `last_quarter` in `pd.Timestamp(...)` |
| 4 | `category_removed` amount was `current_total` (≈0) | use `prev_total` |
| 5 | Missing `$` on spike message `prev_total` | added |
| 6 | `detect_end_of_period_concentration` pct format bug (`:.1f * 100`) | `(pct:.1f)` with `pct = ratio * 100` |
| 7 | `np.std(..., ddof=0)` underestimates for small n | `ddof=1` |
| 8 | `"weekend_spender"` vs schema `"weekend_spend"` | canonical `"weekend_spend"` |
| 9 | Import grouping (local before third-party) | stdlib → third-party → local |
| 10 | `anomalies()` not verb+noun | renamed `detect_anomalies` |
| 11 | `detect_category_totals` (doesn't detect) | renamed `sum_expenses_by_category` |
| 12 | `detect_end_of_period_concentration` return type lied | `list[Pattern]` |
| 13 | Lines > 88 chars | reformatted via ruff |
| 14 | Trailing whitespace | stripped by ruff |
| 15 | `TransactionRow.category_id: str` (required) contradicted `or "uncategorized"` fallback | made `Optional[str]` |
| 17 | `Anomaly.type`, `Pattern.type` bare `str` | `Literal` aliases (`AnomalyType`, `PatternType`) |
| — | `app/db/client.py` had `from tkinter import N` (unused GUI import) | removed by ruff |
| — | `Pattern.message` missing from schema — silently dropped by Pydantic v2 | added field |

Dropped per user: #16 (orchestrator `build_insight_summary` — user will author).

## 3. Tooling

- **Ruff** installed in venv; `pyproject.toml` configured (line-length 88,
  py3.12, rules `E F I W`). Format-on-save enabled via user's IDE extension.
- **Pytest** installed in venv; no new plugins required.
- `requirements-dev.txt` (new) will pin `ruff` and `pytest`.

## 4. Test structure

```
tests/
├── conftest.py              # shared factories (fixture module)
└── test_insights_engine.py  # all engine tests, grouped by class per function
```

### 4.1 conftest factories

```python
def make_expense(amount=10.0, *, category_id="cat-1", category_name="Groceries",
                 transaction_date=date(2026, 4, 1), merchant=None,
                 description=None, category_icon=None, category_color=None,
                 id=None) -> TransactionRow: ...
def make_income(amount=1000.0, *, transaction_date=date(2026, 4, 1),
                id=None) -> TransactionRow: ...
def make_allocation(*, category_id="cat-1", amount=100.0,
                    budget_id="budget-1", id=None) -> AllocationRow: ...
def make_budget(*, start_date=date(2026, 4, 1), end_date=date(2026, 4, 30),
                amount=5000.0, id=None) -> BudgetRow: ...
```

Every factory: keyword-only overrides, sensible defaults, auto-generated `id`
if not supplied (UUID4 string). Tests override only what they care about.

### 4.2 Test classes (one per public function)

Roughly 80 tests total. Each class covers:

- **Happy path** — basic correct behavior
- **Empty input** — `[]` → `[]` or neutral summary
- **Branch coverage** — every `if/elif/else` in the function under test
- **Boundary values** — exactly at threshold (`SPIKE_THRESHOLD`, `>= 5`
  expenses for large-single, `> 0.40` for end-of-month, `* 1.5` weekend ratio)
- **`None`/optional handling** — missing `category_id`, `category_name`,
  `merchant`, `description`, budget `start_date`/`end_date`
- **Float precision** — all float assertions use `pytest.approx`
- **Sorting/ordering** — where the function returns a sorted list
- **Type-validated fields** — one test per Literal alias rejecting bad values

### 4.3 Specific test outlines

**`TestCalculateTotals` (6):** mixed, income-only, expense-only, empty,
zero-income → `savings_rate=None`, negative net (expenses > income) →
negative `savings_rate`.

**`TestCategoryBreakdown` (10):** happy path with 2+ categories, sorted desc
by total, empty, only-income input, no allocation → `budget_used_pct=None`,
allocation amount zero → `None`, allocation > spend → pct < 100,
uncategorized transactions grouped, icon/color carried through, pct_of_total
sums to 100.

**`TestComparePeriods` (6):** both increase, both decrease, previous=0 →
`None`, empty both → `(None, None)`, income-only, expense-only.

**`TestDetectAnomalies` (3):** aggregates outputs from all three detectors;
empty → `[]`; order preserved (spikes, then budget, then large).

**`TestSumExpensesByCategory` (5):** ignores income rows, groups by id,
uncategorized bucket, empty → `{}`, multiple categories.

**`TestDetectCategorySpikes` (12):**
- new_category (no previous spending)
- category_removed (previous > 0, current ≈ 0)
- spike just above `SPIKE_THRESHOLD` → medium
- spike above `HIGH_SEVERITY_THRESHOLD` → high
- spike exactly at threshold (boundary: `> 0.30` excludes 0.30)
- decrease → no anomaly
- no-change → no anomaly
- uses `category_name` in message, not `category_id`
- falls back to `category_id` when `category_name` missing
- `category_removed` amount is `prev_total` (regression for fix #4)
- spike message has `$` on prev_total (regression for fix #5)
- spike severity=medium for 0.31 < change ≤ 0.50

**`TestDetectBudgetOverspending` (8):** happy path, exactly at limit →
no anomaly, pct > 120 → high severity, pct ≤ 120 → medium, under-limit →
no anomaly, no allocation for category → no anomaly, uses `category_name`
in message, multiple categories over budget.

**`TestDetectLargeSingleTransactions` (7):** < 5 expenses → `[]`, exactly 5
with no outliers → `[]`, one clear outlier flagged, label precedence
(merchant > description > category_name > "uncategorized"), `ddof=1` applied
(regression for fix #7), income rows ignored in stats, multiple outliers.

**`TestDetectPatterns` (3):** empty expenses → `[]`, aggregates from three
detectors, passes correct frame to each.

**`TestDetectWeekendSpend` (8):** weekend > 1.5× weekday AND > 10% of total →
pattern, weekend ≤ 1.5× → no pattern, weekend < 10% of total → no pattern,
weekend_dates=0 → `[]`, weekday_dates=0 → `[]`, ratio formatting (`:.1f`),
data fields populated, message contains weekend and weekday daily figures.

**`TestDetectEndOfPeriodConcentration` (7):** no budget dates → `[]`,
end_total / total > 0.40 → pattern, exactly 0.40 → no pattern (strict `>`),
no spending → `[]`, pct format correct (regression for fix #6 — no
`:.1f * 100` syntax error), `last_quarter` comparison works against
`pd.Timestamp` (regression for fix #3), short budget period (period_length < 4).

**`TestDetectFrequentCategories` (7):** empty df → `[]`, all `category_name`
None → `[]`, top 3 returned, total > 0 required, message format with `$`,
`data` dict structure, ties in count (stable order).

## 5. Out of scope

- Orchestrator `build_insight_summary` (user authoring separately).
- Integration tests against Supabase.
- `ai_service.py` and route tests.
- Hypothesis / property-based testing.
- Coverage reporting CI gate.

## 6. Acceptance

- All ~80 tests pass via `venv/bin/python -m pytest`.
- `ruff check` and `ruff format --check` clean on `app/` and `tests/`.
- No new commits; user reviews the diff and commits on their own cadence.
