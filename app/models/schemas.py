from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel

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

InsightWindow = Literal[
    "1m",
    "3m",
    "6m",
    "1y",
    "current_year",
    "last_year",
]


class TransactionRow(BaseModel):
    id: str
    user_id: str
    budget_id: Optional[str] = None
    account_id: Optional[str] = None
    category_id: Optional[str] = None
    amount: float
    description: Optional[str] = None
    is_recurring: bool = False
    merchant: Optional[str] = None
    note: Optional[str] = None
    notes: Optional[str] = None
    tags: Optional[list[str]] = None
    transaction_date: date
    type: str  # 'income' | 'expense'

    # joined fields
    category_name: Optional[str] = None
    category_icon: Optional[str] = None
    category_color: Optional[str] = None


class CategoryRow(BaseModel):
    id: str
    name: str
    category_type: str  # 'income' | 'expense'
    icon: Optional[str] = None
    color: Optional[str] = None


class BudgetRow(BaseModel):
    id: str
    user_id: str
    name: str
    period: str  # 'monthly'| 'daily'
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
    category_name: Optional[str] = None


class GoalRow(BaseModel):
    id: str
    name: str
    target_amount: float
    current_amount: float = 0
    target_date: Optional[date] = None
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
    savings_rate: Optional[float] = None


class CategoryBreakdown(BaseModel):
    category_id: str
    category_name: Optional[str] = None
    icon: Optional[str] = None
    color: Optional[str] = None
    total: float
    transaction_count: int
    pct_of_total: float
    budget_limit: Optional[float] = None
    budget_used_pct: Optional[float] = None  # 0-100, None if no budget set


class Anomaly(BaseModel):
    type: AnomalyType
    category_name: Optional[str] = None
    message: str
    severity: Literal["low", "medium", "high"]
    amount: Optional[float] = None


class Pattern(BaseModel):
    type: PatternType
    category_name: Optional[str] = None
    message: Optional[str] = None
    data: Optional[dict] = None


class GoalProgress(BaseModel):
    goal_id: str
    name: str
    target_amount: float
    current_amount: float = 0
    progress_pct: float = 0
    days_remaining: Optional[int] = None
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
    savings_rate: Optional[float] = None  # net/income * 100, or 0 if no income

    # vs last period
    expenses_change_pct: Optional[float] = None  # None if no previous period data
    income_change_pct: Optional[float] = None

    category_breakdown: list[CategoryBreakdown]
    anomalies: list[Anomaly]
    patterns: list[Pattern]
    goals: list[GoalProgress]
    debt: Optional[DebtSummary] = None

    # raw counts for context
    transaction_count: int
    recurring_count: int

    # ── API response shapes ───────────────────────────────────────────────────────


class InsightsResponse(BaseModel):
    """GET /insights — no AI, fast"""

    summary: InsightSummary


class AIRecommendation(BaseModel):
    insights: str
    problems: str
    recommendations: str
    one_action: str  # the single most impactful thing to do


class AIInsightsResponse(BaseModel):
    """GET /ai-insights — includes AI layer"""

    summary: InsightSummary
    ai: AIRecommendation


# ── Query params ──────────────────────────────────────────────────────────────
class InsightsQuery(BaseModel):
    budget_id: str
    window: InsightWindow
