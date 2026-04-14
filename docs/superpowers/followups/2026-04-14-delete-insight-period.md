# Follow-up: Delete the unreferenced `InsightPeriod` model

**Severity:** Minor — dead code.

## Problem

`app/models/schemas.py` defines:

```python
class InsightPeriod(BaseModel):
    year: int
    month: int
```

No route, engine function, or test references it. It was kept in the design spec as "harmless" pending a future consumer, but none exists now that the window-enum flow is the canonical one.

## Proposed fix

Delete the class, along with the `# ── Query params ──` banner if nothing else lives under it once `InsightsQuery` is the only inhabitant (keep the banner if it's still useful).

## Verification

- `grep -rn "InsightPeriod" app/ tests/` → no matches after removal.
- `pytest tests/` → still 97/97 green.
