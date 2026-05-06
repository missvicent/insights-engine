import asyncio
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool

from app.context import UserContext
from app.db.client import (
    BudgetNotFound,
    fetch_budget,
    fetch_goals,
    fetch_transactions,
)
from app.models.schemas import InsightsQuery, InsightsResponse
from app.routes.deps import get_user_ctx
from app.services.insights_engine import (
    allowed_windows_for_period,
    build_summary,
    resolve_window,
)

router = APIRouter()


@router.get("/insights", responses={404: {"description": "Budget not found"}})
async def get_insights(
    q: Annotated[InsightsQuery, Depends()],
    ctx: Annotated[UserContext, Depends(get_user_ctx)],
) -> InsightsResponse:
    try:
        budget, allocations = await run_in_threadpool(fetch_budget, ctx, q.budget_id)
    except BudgetNotFound:
        raise HTTPException(status_code=404, detail="budget not found") from None

    if q.window not in allowed_windows_for_period(budget.period):
        raise HTTPException(
            status_code=422,
            detail=f"window {q.window!r} not allowed for {budget.period!r} budget",
        )

    current_start, current_end, prev_start, prev_end = resolve_window(
        q.window, date.today()
    )
    current, previous, goals = await asyncio.gather(
        run_in_threadpool(
            fetch_transactions, ctx, current_start, current_end, q.budget_id
        ),
        run_in_threadpool(fetch_transactions, ctx, prev_start, prev_end, q.budget_id),
        run_in_threadpool(fetch_goals, ctx),
    )

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
    return InsightsResponse(summary=summary)
