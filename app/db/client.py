from datetime import date
from functools import lru_cache

from pydantic_settings import BaseSettings
from supabase import Client, create_client

from app.context import UserContext
from app.models.schemas import (
    AllocationRow,
    BudgetRow,
    DebtRow,
    GoalRow,
    RecurringRow,
    TransactionRow,
)


class Settings(BaseSettings):
    supabase_url: str
    supabase_anon_key: str
    supabase_jwt_secret: str
    ai_model: str

    class Config:
        env_file = ".env"
        extra = "ignore"


@lru_cache
def get_settings() -> Settings:
    return Settings()


def build_user_client(access_token: str) -> Client:
    """Build a per-request Supabase client authenticated as the end user.

    The anon key is the client's baseline (public, no RLS grants), and the
    user's JWT is attached via postgrest.auth so every query runs under
    auth.uid() and RLS enforces row-level access.
    """
    s = get_settings()
    client = create_client(s.supabase_url, s.supabase_anon_key)
    client.postgrest.auth(access_token)
    return client


def fetch_transactions(
    ctx: UserContext,
    start: date,
    end: date,
    budget_id: str | None = None,
) -> list[TransactionRow]:
    """Fetch transactions for `ctx.user_id` between `start` and `end` (inclusive).

    When `budget_id` is provided, results are scoped to that budget.
    RLS enforces ownership; the explicit user_id filter is belt-and-suspenders.
    """
    query = (
        ctx.db.table("transactions")
        .select("*, categories(name, icon, color)")
        .eq("user_id", ctx.user_id)
        .gte("transaction_date", start.isoformat())
        .lte("transaction_date", end.isoformat())
    )
    if budget_id is not None:
        query = query.eq("budget_id", budget_id)
    response = query.execute()

    rows = []
    for row in response.data:
        cat = row.pop("categories", None) or {}
        rows.append(
            TransactionRow(
                **row,
                category_name=cat.get("name"),
                category_icon=cat.get("icon"),
                category_color=cat.get("color"),
            )
        )
    return rows


class BudgetNotFound(Exception):
    """Raised when a budget_id does not exist or is not owned by the user."""


def fetch_budget(
    ctx: UserContext,
    budget_id: str,
) -> tuple[BudgetRow, list[AllocationRow]]:
    """Fetch one budget (authorized to ctx.user_id) and its allocations.

    Raises BudgetNotFound when the row is missing or not owned by the user.
    """
    budget_response = (
        ctx.db.table("budgets")
        .select("*")
        .eq("id", budget_id)
        .eq("user_id", ctx.user_id)
        .limit(1)
        .execute()
    )

    if not budget_response.data:
        raise BudgetNotFound(budget_id)

    budget = BudgetRow(**budget_response.data[0])

    alloc_response = (
        ctx.db.table("allocations")
        .select("*, categories(name)")
        .eq("budget_id", budget.id)
        .execute()
    )

    allocations: list[AllocationRow] = []
    for alloc in alloc_response.data:
        cat = alloc.pop("categories", None) or {}
        allocations.append(AllocationRow(**alloc, category_name=cat.get("name")))

    return budget, allocations


def fetch_goals(ctx: UserContext) -> list[GoalRow]:
    response = (
        ctx.db.table("goals")
        .select("id, name, target_amount, current_amount, target_date, is_achieved")
        .eq("user_id", ctx.user_id)
        .eq("is_achieved", False)
        .execute()
    )
    return [GoalRow(**row) for row in response.data]


def fetch_debt(ctx: UserContext) -> list[DebtRow]:
    response = (
        ctx.db.table("debts")
        .select(
            "id, name, type, current_balance, interest_rate, minimum_payment, is_active"
        )
        .eq("user_id", ctx.user_id)
        .execute()
    )
    return [DebtRow(**row) for row in response.data]


def fetch_recurring(ctx: UserContext) -> list[RecurringRow]:
    response = (
        ctx.db.table("recurring_transactions")
        .select(
            "id, name, type, amount, frequency, next_occurrence, is_active, is_paused"
        )
        .eq("user_id", ctx.user_id)
        .eq("is_active", True)
        .execute()
    )
    return [RecurringRow(**row) for row in response.data]
