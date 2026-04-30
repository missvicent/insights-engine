from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field

AnomalyType = Literal[
    "spike",
    "new_category",
    "category_removed",
    "budget_exceeded",
    "large_single",
    "new_recurring",
]

PatternType = Literal[
    "weekend_spend",
    "end_of_period_concentration",
    "frequent_category",
    "nightly_spend",
    "weekly_spend",
    "recurring_growth",
]

InsightWindow = Literal["7d", "15d", "30d", "3m", "6m", "12m"]


class TransactionRow(BaseModel):
    id: str
    user_id: str
    budget_id: str | None = None
    account_id: str | None = None
    category_id: str | None = None
    amount: float
    description: str | None = None
    is_recurring: bool = False
    merchant: str | None = None
    notes: str | None = None
    tags: list[str] | None = None
    transaction_date: date
    type: str  # 'income' | 'expense'

    # joined fields
    category_name: str | None = None
    category_icon: str | None = None
    category_color: str | None = None


class CategoryRow(BaseModel):
    id: str
    name: str
    category_type: str  # 'income' | 'expense'
    icon: str | None = None
    color: str | None = None


class BudgetRow(BaseModel):
    id: str
    user_id: str
    name: str
    period: str  # 'monthly' | 'yearly'
    amount: float
    start_date: date
    end_date: date
    is_active: bool = True


class AllocationRow(BaseModel):
    id: str
    budget_id: str
    category_id: str
    amount: float
    alert_threshold: int = 80
    category_name: str | None = None


class GoalRow(BaseModel):
    id: str
    name: str
    target_amount: float
    current_amount: float = 0
    target_date: date | None = None
    is_achieved: bool = False


class DebtRow(BaseModel):
    id: str
    name: str
    type: str
    current_balance: float
    interest_rate: float
    minimum_payment: float
    is_active: bool = True


class RecurringRow(BaseModel):
    id: str
    name: str
    type: str  # 'income' | 'expense'
    amount: float
    frequency: str
    next_occurrence: date
    is_active: bool = True
    is_paused: bool = False


class FinancialTotals(BaseModel):
    """Lightweight totals — produced by calculate_totals, consumed by the
    orchestrator that assembles InsightSummary."""

    total_income: float
    total_expenses: float
    net: float
    savings_rate: float | None = None


class PeriodComparison(BaseModel):
    """Output of `compare_periods`. None when there's no prior-period
    baseline to compare against (i.e. previous total was 0)."""

    income_change_pct: float | None = None
    expenses_change_pct: float | None = None


class CategoryBreakdown(BaseModel):
    category_id: str
    category_name: str | None = None
    icon: str | None = None
    color: str | None = None
    total: float
    transaction_count: int
    pct_of_total: float
    budget_limit: float | None = None
    budget_used_pct: float | None = None  # 0-100, None if no budget set


class Anomaly(BaseModel):
    id: str
    type: AnomalyType
    category_name: str | None = None
    icon: str | None = None
    color: str | None = None
    message: str
    severity: Literal["low", "medium", "high"]
    amount: float | None = None


class Pattern(BaseModel):
    id: str
    type: PatternType
    category_name: str | None = None
    message: str | None = None
    data: dict[str, Any] | None = None


class GoalProgress(BaseModel):
    goal_id: str
    name: str
    target_amount: float
    current_amount: float = 0
    progress_pct: float = 0
    days_remaining: int | None = None
    on_track: bool


class DebtSummary(BaseModel):
    total_debt: float
    monthly_obligations: float  # sum of all debts' minimum payments
    highest_rate: float
    debt_names: list[str]


class InsightSummary(BaseModel):
    """
    Core output of the insights engine.
    This is what gets sent to the AI layer — not raw transactions.
    """

    budget_id: str
    budget_name: str

    period_label: str  # e.g. "December 2025"
    total_income: float
    total_expenses: float
    net: float  # income - expenses
    savings_rate: float | None = None  # net/income * 100, or 0 if no income

    # vs last period
    expenses_change_pct: float | None = None  # None if no previous period data
    income_change_pct: float | None = None

    category_breakdown: list[CategoryBreakdown]
    anomalies: list[Anomaly]
    patterns: list[Pattern]
    goals: list[GoalProgress]
    debt: DebtSummary | None = None

    # raw counts for context
    transaction_count: int
    recurring_count: int

    next_action_horizon_days: int

    # ── API response shapes ───────────────────────────────────────────────────────


class InsightsResponse(BaseModel):
    """GET /insights — no AI, fast"""

    summary: InsightSummary


class AIRecommendation(BaseModel):
    """A non-empty string in every field is part of the contract: empty
    output from the model is treated as a validation failure so the route
    falls back rather than serving a blank recommendation."""

    insights: str = Field(min_length=1)
    problems: str = Field(min_length=1)
    recommendations: str = Field(min_length=1)
    one_action: str = Field(min_length=1)


class AIInsightsResponse(BaseModel):
    """GET /ai-insights — includes AI layer"""

    summary: InsightSummary
    ai: AIRecommendation


# ── Query params ──────────────────────────────────────────────────────────────
class InsightsQuery(BaseModel):
    budget_id: str
    window: InsightWindow
