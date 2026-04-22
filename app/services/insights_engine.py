"""
insights_engine.py
Pure deterministic Python functions that compute financial insights from raw
transactions.
Input: raw DB rows
Output: InsightSummary
"""

from datetime import date, timedelta
from typing import Optional

import numpy as np
import pandas as pd
from dateutil.relativedelta import relativedelta

from app.models.schemas import (
    AllocationRow,
    Anomaly,
    BudgetRow,
    CategoryBreakdown,
    FinancialTotals,
    GoalRow,
    GoalProgress,
    InsightSummary,
    InsightWindow,
    Pattern,
    TransactionRow,
)

SPIKE_THRESHOLD = 0.30
HIGH_SEVERITY_THRESHOLD = 0.50


def resolve_window(
    window: InsightWindow,
    today: date,
) -> tuple[date, date, date, date]:
    """Return (current_start, current_end, previous_start, previous_end)."""
    span_days = {
        "7d": 7,
        "15d": 15,
        "30d": 30,
        "3m": 90,
        "6m": 180,
        "12m": 365,
    }.get(window)
    if span_days is None:
        raise ValueError(f"unknown window: {window}")

    current_end = today
    current_start = today - timedelta(days=span_days)
    previous_end = current_start - timedelta(days=1)
    previous_start = previous_end - timedelta(days=span_days)
    return current_start, current_end, previous_start, previous_end


_HORIZON_DAYS: dict[InsightWindow, int] = {
    "7d": 3,
    "15d": 7,
    "30d": 7,
    "3m": 14,
    "6m": 30,
    "12m": 30,
}


def _horizon_for_window(window: InsightWindow) -> int:
    """Days of forward-looking action the deterministic signal supports."""
    return _HORIZON_DAYS[window]


_ALLOWED_WINDOWS: dict[str, set[InsightWindow]] = {
    "monthly": {"7d", "15d", "30d"},
    "yearly": {"3m", "6m", "12m"},
}


def allowed_windows_for_period(period: str) -> set[InsightWindow]:
    """Which InsightWindow values are valid for a budget with this period.

    Raises ValueError for unknown periods.
    """
    try:
        return _ALLOWED_WINDOWS[period]
    except KeyError as e:
        raise ValueError(f"unknown budget period: {period!r}") from e


def calculate_totals(transactions: list[TransactionRow]) -> FinancialTotals:
    income = sum(t.amount for t in transactions if t.type == "income")
    expenses = sum(t.amount for t in transactions if t.type == "expense")
    net = income - expenses
    savings_rate = round((net / income) * 100, 2) if income > 0 else None

    return FinancialTotals(
        total_income=income,
        total_expenses=expenses,
        net=net,
        savings_rate=savings_rate,
    )


def category_breakdown(
    transactions: list[TransactionRow],
    allocations: list[AllocationRow],
) -> list[CategoryBreakdown]:
    expenses = [t for t in transactions if t.type == "expense"]
    if not expenses:
        return []

    total_expenses = sum(t.amount for t in expenses)
    alloc_map: dict[str, float] = {a.category_id: a.amount for a in allocations}

    groups: dict[str, dict] = {}
    for t in expenses:
        key = t.category_id or "uncategorized"
        if key not in groups:
            groups[key] = {
                "category_id": key,
                "category_name": t.category_name,
                "icon": t.category_icon,
                "color": t.category_color,
                "total": 0.0,
                "count": 0,
            }
        groups[key]["total"] += t.amount
        groups[key]["count"] += 1

    result: list[CategoryBreakdown] = []
    for key, g in groups.items():
        budget_limit = alloc_map.get(key)
        budget_used_pct = None
        if budget_limit and budget_limit > 0:
            budget_used_pct = round((g["total"] / budget_limit) * 100, 2)

        result.append(
            CategoryBreakdown(
                category_id=g["category_id"],
                category_name=g["category_name"],
                icon=g["icon"],
                color=g["color"],
                total=round(g["total"], 2),
                transaction_count=g["count"],
                pct_of_total=round((g["total"] / total_expenses) * 100, 2),
                budget_limit=budget_limit,
                budget_used_pct=budget_used_pct,
            )
        )

    return sorted(result, key=lambda x: x.total, reverse=True)


def compare_periods(
    current: list[TransactionRow],
    previous: list[TransactionRow],
) -> dict:
    """Positive = more spent/earned, negative = less spent/earned."""

    def totals(txs: list[TransactionRow]) -> tuple[float, float]:
        return (
            sum(t.amount for t in txs if t.type == "income"),
            sum(t.amount for t in txs if t.type == "expense"),
        )

    current_income, current_expenses = totals(current)
    previous_income, previous_expenses = totals(previous)

    def pct_change(current: float, previous: float) -> float | None:
        if previous == 0:
            return None
        return round(((current - previous) / previous) * 100, 2)

    return {
        "income_change_pct": pct_change(current_income, previous_income),
        "expenses_change_pct": pct_change(current_expenses, previous_expenses),
    }


def detect_anomalies(
    current: list[TransactionRow],
    previous: list[TransactionRow],
    allocations: list[AllocationRow],
) -> list[Anomaly]:
    return [
        *detect_category_spikes(current, previous),
        *detect_budget_overspending(current, allocations),
        *detect_large_single_transactions(current),
    ]


def sum_expenses_by_category(txs: list[TransactionRow]) -> dict[str, float]:
    """Shared helper: sum expenses by category_id."""
    d: dict[str, float] = {}
    for t in txs:
        if t.type != "expense":
            continue
        key = t.category_id or "uncategorized"
        d[key] = d.get(key, 0.0) + t.amount
    return d


def _category_display_by_id(
    transactions: list[TransactionRow],
) -> dict[str, tuple[Optional[str], Optional[str], Optional[str]]]:
    """Map category_id → (name, icon, color) over expense transactions."""
    return {
        t.category_id: (t.category_name, t.category_icon, t.category_color)
        for t in transactions
        if t.category_id and t.type == "expense"
    }


def detect_category_spikes(
    current: list[TransactionRow],
    previous: list[TransactionRow],
) -> list[Anomaly]:
    current_cats = sum_expenses_by_category(current)
    previous_cats = sum_expenses_by_category(previous)
    all_cats = set(current_cats.keys()) | set(previous_cats.keys())
    display = _category_display_by_id([*current, *previous])
    result: list[Anomaly] = []

    for cat in all_cats:
        name, icon, color = display.get(cat, (None, None, None))
        display_name = name or cat
        current_total = current_cats.get(cat, 0)
        prev_total = previous_cats.get(cat, 0)
        if prev_total < 0.01:
            result.append(
                Anomaly(
                    type="new_category",
                    category_name=display_name,
                    icon=icon,
                    color=color,
                    message=(
                        f"New spending in {display_name} - "
                        f"${current_total:.0f} this month (not previously tracked)"
                    ),
                    severity="low",
                    amount=current_total,
                )
            )
        elif prev_total > 0 and current_total < 0.01:
            result.append(
                Anomaly(
                    type="category_removed",
                    category_name=display_name,
                    icon=icon,
                    color=color,
                    message=(
                        f"Spending in {display_name} was removed from the budget "
                        f"this month; ${prev_total:.0f} was removed from the budget"
                    ),
                    severity="low",
                    amount=prev_total,
                )
            )
        else:
            change = (current_total - prev_total) / prev_total
            if change > SPIKE_THRESHOLD:
                severity = "high" if change > HIGH_SEVERITY_THRESHOLD else "medium"
                result.append(
                    Anomaly(
                        type="spike",
                        category_name=display_name,
                        icon=icon,
                        color=color,
                        message=(
                            f"Spending in {display_name} increased by "
                            f"{change * 100:.0f}% this month "
                            f"(vs ${prev_total:.0f} last month)"
                        ),
                        severity=severity,
                        amount=current_total,
                    )
                )
    return result


def detect_budget_overspending(
    current: list[TransactionRow],
    allocations: list[AllocationRow],
) -> list[Anomaly]:
    result: list[Anomaly] = []
    current_cats = sum_expenses_by_category(current)
    alloc_map = {a.category_id: a.amount for a in allocations if a.category_id}
    display = _category_display_by_id(current)

    for cat, total in current_cats.items():
        limit = alloc_map.get(cat, 0)
        if limit and total > limit:
            name, icon, color = display.get(cat, (None, None, None))
            display_name = name or cat
            pct = total / limit * 100
            result.append(
                Anomaly(
                    type="budget_exceeded",
                    category_name=display_name,
                    icon=icon,
                    color=color,
                    message=(
                        f"'{display_name}' budget exceeded by {pct:.0f}% of "
                        f"budget (${total:.0f} of ${limit:.0f})"
                    ),
                    severity="high" if pct > 120 else "medium",
                    amount=total - limit,
                )
            )
    return result


def detect_large_single_transactions(
    current: list[TransactionRow],
) -> list[Anomaly]:
    result: list[Anomaly] = []
    expenses = [t for t in current if t.type == "expense"]
    if len(expenses) < 5:
        return []

    amounts = [t.amount for t in expenses]
    mean = float(np.mean(amounts))
    std = float(np.std(amounts, ddof=1))
    threshold = mean + 2 * std
    outliers = [t for t in expenses if t.amount > threshold]
    for t in outliers:
        label = t.merchant or t.description or t.category_name or "uncategorized"
        result.append(
            Anomaly(
                type="large_single",
                category_name=t.category_name,
                icon=t.category_icon,
                color=t.category_color,
                message=(
                    f"Unusually large single transaction: ${t.amount:.0f} in {label}"
                ),
                severity="high",
                amount=t.amount,
            )
        )
    return result


def detect_patterns(
    transactions: list[TransactionRow],
    window_start: date,
    window_end: date,
) -> list[Pattern]:
    expenses = [t for t in transactions if t.type == "expense"]
    if not expenses:
        return []

    df = pd.DataFrame(
        [
            {
                "amount": t.amount,
                "date": pd.to_datetime(t.transaction_date),
                "category_name": t.category_name,
                "merchant": t.merchant,
            }
            for t in expenses
        ]
    )

    total_expenses = sum(t.amount for t in expenses)

    return [
        *detect_weekend_spend(df, total_expenses),
        *detect_end_of_period_concentration(df, window_start, window_end),
        *detect_frequent_categories(df),
    ]


def detect_weekend_spend(df: pd.DataFrame, total_expenses: float) -> list[Pattern]:
    df = df.copy()
    df["dow"] = df["date"].dt.dayofweek
    df["is_weekend"] = df["dow"].isin([5, 6])

    weekend_total = df[df["is_weekend"]]["amount"].sum()
    weekday_total = df[~df["is_weekend"]]["amount"].sum()
    weekend_dates = df[df["is_weekend"]]["date"].dt.date.nunique()
    weekday_dates = df[~df["is_weekend"]]["date"].dt.date.nunique()

    if weekend_dates == 0 or weekday_dates == 0:
        return []

    weekend_daily = weekend_total / weekend_dates
    weekday_daily = weekday_total / weekday_dates

    if weekend_daily > weekday_daily * 1.5 and weekend_total > total_expenses * 0.1:
        ratio = weekend_daily / weekday_daily
        return [
            Pattern(
                type="weekend_spend",
                message=(
                    f"You spend {ratio:.1f}× more per day on weekends "
                    f"(${weekend_daily:.0f}/day) than weekdays "
                    f"(${weekday_daily:.0f}/day)"
                ),
                data={
                    "weekend_daily": round(weekend_daily, 2),
                    "weekday_daily": round(weekday_daily, 2),
                },
            )
        ]
    return []


def detect_end_of_period_concentration(
    df: pd.DataFrame,
    window_start: date,
    window_end: date,
) -> list[Pattern]:
    period_length = (window_end - window_start).days
    if period_length <= 0:
        return []

    last_quarter = pd.Timestamp(
        window_end - relativedelta(days=period_length // 4)
    )

    end_total = df[df["date"] >= last_quarter]["amount"].sum()
    total = df["amount"].sum()

    if total > 0 and end_total / total > 0.40:
        pct = (end_total / total) * 100
        return [
            Pattern(
                type="end_of_period_concentration",
                message=(f"{pct:.1f}% of spending in the last quarter of the window"),
                data={},
            )
        ]

    return []


def detect_frequent_categories(df: pd.DataFrame) -> list[Pattern]:
    if df.empty:
        return []

    most_frequent = df["category_name"].dropna().value_counts().nlargest(3)
    if most_frequent.empty:
        return []

    top_names = most_frequent.index.tolist()
    spend_by_category = (
        df[df["category_name"].isin(top_names)].groupby("category_name")["amount"].sum()
    )

    patterns: list[Pattern] = []
    for category, count in most_frequent.items():
        total = spend_by_category.get(category, 0)
        if total > 0:
            patterns.append(
                Pattern(
                    category_name=category,
                    type="frequent_category",
                    message=(
                        f"You spend ${total:.0f} on {category} ({count} transactions)"
                    ),
                    data={
                        "category": category,
                        "count": int(count),
                        "total": float(total),
                    },
                )
            )

    return patterns


def compute_goal_progress(goals: list[GoalRow]) -> list[GoalProgress]:
    today = date.today()
    result: list[GoalProgress] = []
    for goal in goals:
        if goal.is_achieved:
            continue
        progress_pct = 0.0
        if goal.target_amount > 0:
            progress_pct = round(
                (goal.current_amount / goal.target_amount) * 100, 2
            )
        days_remaining: int | None = None
        on_track = True
        if goal.target_date is not None:
            days_remaining = (goal.target_date - today).days
            if days_remaining < 0:
                on_track = False

        result.append(GoalProgress(
            goal_id=goal.id,
            name=goal.name,
            target_amount=goal.target_amount,
            current_amount=goal.current_amount,
            progress_pct=progress_pct,
            days_remaining=days_remaining,
            on_track=on_track,
        ))
    return result

def build_summary(
    budget: BudgetRow,
    allocations: list[AllocationRow],
    current: list[TransactionRow],
    previous: list[TransactionRow],
    goals: list[GoalRow],
    window: InsightWindow,
    window_start: date,
    window_end: date,
) -> InsightSummary:
    totals = calculate_totals(current)
    change = compare_periods(current, previous)
    breakdown = category_breakdown(current, allocations)
    anomalies = detect_anomalies(current, previous, allocations)
    patterns = detect_patterns(current, window_start, window_end)
    goal_progress = compute_goal_progress(goals)

    return InsightSummary(
        budget_id=budget.id,
        budget_name=budget.name,
        period_label=_format_period_label(window_start, window_end),
        total_income=totals.total_income,
        total_expenses=totals.total_expenses,
        net=totals.net,
        savings_rate=totals.savings_rate,
        income_change_pct=change["income_change_pct"],
        expenses_change_pct=change["expenses_change_pct"],
        category_breakdown=breakdown,
        anomalies=anomalies,
        patterns=patterns,
        goals=goal_progress,
        debt=None,
        transaction_count=len(current),
        recurring_count=sum(1 for t in current if t.is_recurring),
        next_action_horizon_days=_horizon_for_window(window),
    )


def _format_period_label(start: date, end: date) -> str:
    """Human-readable window label, e.g. 'Mar 15 – Apr 14, 2026'."""
    if start.year == end.year:
        return (
            f"{start.strftime('%b')} {start.day} – "
            f"{end.strftime('%b')} {end.day}, {end.year}"
        )
    return (
        f"{start.strftime('%b')} {start.day}, {start.year} – "
        f"{end.strftime('%b')} {end.day}, {end.year}"
    )
