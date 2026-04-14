# Follow-up: Implement `compute_goal_progress` logic + tests

**Severity:** Minor — the function currently returns `[]`; no caller asserts its content yet, so nothing is broken.

## Current state

```python
def compute_goal_progress(goals: list[GoalRow]) -> list[GoalProgress]:
    return []
```

`build_summary` calls this and puts the result into `InsightSummary.goals`. The API therefore always returns an empty goals list.

## Proposed logic

For each goal where `is_achieved == False`, emit a `GoalProgress` with:
- `goal_id`, `name`, `target_amount`, `current_amount` — straight passthrough.
- `progress_pct` — `round(current_amount / target_amount * 100, 2)`, or `0.0` if `target_amount <= 0`.
- `days_remaining` — `(target_date - today).days` when `target_date is not None`, else `None`.
- `on_track` — `True` by default. `False` if `target_date` has passed (`days_remaining < 0`) and the goal isn't achieved. Optional enhancement: `on_track = progress_pct >= (elapsed_pct_of_time)` — needs a goal start-date, which the current schema doesn't carry.

Skeleton:

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

## Tests — add `TestComputeGoalProgress` to `tests/test_insights_engine.py`

Cover at minimum:
- `is_achieved=True` goal → excluded from output.
- Goal with `target_date=None` → `days_remaining=None`, `on_track=True`.
- Goal with `target_date` in the past and not achieved → `on_track=False`, `days_remaining < 0`.
- Goal with `target_amount=0` → `progress_pct=0.0`, no `ZeroDivisionError`.
- Goal 50% of the way to target with a future deadline → `progress_pct=50.0`, `on_track=True`.

Use `make_goal` (already in `tests/conftest.py`). Inject `today` if you want deterministic `days_remaining` assertions — currently the function reads `date.today()` directly, so either patch it or assert with inequalities.

## Verification

- `pytest tests/test_insights_engine.py::TestComputeGoalProgress -v` → all cases pass.
- `pytest tests/` → full suite green.
- Spot-check against live Supabase: a goal with a known `current_amount / target_amount` reports the correct `progress_pct`.
