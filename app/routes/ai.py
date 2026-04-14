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
