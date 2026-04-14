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
