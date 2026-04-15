from datetime import date
from typing import Annotated

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


@router.get("/insights", responses={404: {"description": "Budget not found"}})
def get_insights(
    q: Annotated[InsightsQuery, Depends()],
    user_id: Annotated[str, Depends(get_current_user)],
) -> InsightsResponse:
    try:
        budget, allocations = fetch_budget(user_id, q.budget_id)
    except BudgetNotFound:
        raise HTTPException(status_code=404, detail="budget not found") from None

    current_start, current_end, prev_start, prev_end = resolve_window(
        q.window, date.today()
    )
    budget_id = q.budget_id
    current = fetch_transactions(user_id, current_start, current_end, budget_id)
    previous = fetch_transactions(user_id, prev_start, prev_end, budget_id)
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
