---
status: open
severity: minor
resolved_date:
resolved_by:
---

# Follow-up: Rename `end_of_period_concentration` → `end_of_period_concentration`

**Severity:** Minor — cosmetic UX bug, pre-existing (not introduced by this feature).

## Problem

For a `1y` or `6m` window, the `detect_end_of_period_concentration` pattern still emits:

> "90.0% of spending in the last quarter of the month"

The math (`period_length // 4` of the window) is correct, but the wording is wrong — it isn't a month and hasn't been since `detect_patterns` was decoupled from `BudgetRow` in Task 5.

## Proposed fix

Rename everything — function, literal, message — to reflect that this is about the last ¼ of the analysis window.

1. `app/services/insights_engine.py`
   - Rename `detect_end_of_period_concentration` → `detect_end_of_period_concentration`.
   - Update the import / call inside `detect_patterns`.
   - Change the emitted message:
     ```python
     message=f"{pct:.1f}% of spending in the last quarter of the window"
     ```

2. `app/models/schemas.py`
   - Update `PatternType` literal: `"end_of_period_concentration"` → `"end_of_period_concentration"`.
   - **This is a client-facing breaking change** — any stored response data or front-end code keying off `"end_of_period_concentration"` will need to be migrated. Coordinate with the front-end before shipping.

3. `tests/test_insights_engine.py`
   - Rename `TestDetectEndOfPeriodConcentration` → `TestDetectEndOfPeriodConcentration`.
   - Update any string assertions that referenced "of the month".

## Verification

- `grep -rn "end_of_period_concentration" app/ tests/` → zero matches after the rename.
- `pytest tests/` → 97/97 green.
- A request with a 1y window and concentrated spend in the final 3 months should produce the new message.

## Notes

- Can be done alongside any other client-visible change that's already timing in.
- Safe to defer if the front-end isn't being updated right now.
