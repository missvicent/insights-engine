---
status: resolved
severity: trivial
resolved_date: 2026-04-14
resolved_by: nily
---

# Follow-up: Fix the off-by-one typo in the plan doc

**Severity:** Trivial — documentation only, no code impact.

## Problem

`docs/superpowers/plans/2026-04-14-budget-scoped-insights.md`, Task 4, Step 1, the `test_3m` test case shows:

```python
assert ps == date(2025, 10, 16)  # WRONG in the plan text
```

The arithmetic is `date(2026, 1, 13) - timedelta(days=90) == date(2025, 10, 15)`. The committed test file (`tests/test_insights_engine.py`) asserts `date(2025, 10, 15)` — correct. Only the plan document has the wrong value.

## Proposed fix

Edit the plan text: change `date(2025, 10, 16)` → `date(2025, 10, 15)` in the `test_3m` example.

## Verification

- `grep "10, 16" docs/superpowers/plans/2026-04-14-budget-scoped-insights.md` → zero matches after the edit.
- No code change, no test change.
