---
status: resolved
severity: important
resolved_date: 2026-04-14
resolved_by: 5991750
---

# Follow-up: Stop running the pipeline in the `/ai-insights` stub

**Severity:** Important — wasteful and a debugging footgun once live traffic hits the endpoint.

## Problem

`app/routes/ai.py::get_ai_insights` currently runs `fetch_budget`, two `fetch_transactions`, `fetch_goals`, and `build_summary` — assigns the result to `_summary` — then unconditionally raises `HTTPException(501)`.

Every real request does four DB round-trips and a full engine computation to produce output that is immediately discarded. Harmless in dev, but when the front-end is pointed at this endpoint it burns DB quota and masks latency issues.

## Proposed fix

Cleanest: **don't register the route until `ai_service.py` exists**.

1. Delete `app/routes/ai.py` entirely.
2. Remove the `ai_routes` import and `app.include_router(ai_routes.router)` line from `app/main.py`.
3. A request to `/ai-insights` then returns FastAPI's built-in 404 — accurate and zero-cost.
4. When the AI service is implemented, re-add the route with the real body.

Alternative (keep the endpoint, short-circuit it):

```python
@router.get("/ai-insights")
def get_ai_insights(
    q: InsightsQuery = Depends(),
    user_id: str = Depends(get_current_user),
):
    raise HTTPException(status_code=501, detail="AI service not implemented")
```

Remove the imports that become unused (`fetch_budget`, `fetch_transactions`, `fetch_goals`, `build_summary`, `resolve_window`, `BudgetNotFound`, `date`).

## Recommendation

Go with the **delete-and-unregister** option. The stub's only purpose was to prove the pipeline wires together, and the engine tests already prove that.

## Verification

- `curl http://localhost:8000/ai-insights` → `404` (not `501`).
- `GET /insights` still works as before.
- When `ai_service.py` lands, the real `/ai-insights` goes in alongside it.
