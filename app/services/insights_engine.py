"""
insights_engine.py
Pure deterministic Python functions that compute financial insights from raw transactions.
Input: raw DB rows
Output: InsightSummary
"""

import numpy as np
from app.models.schemas import TransactionRow, InsightSummary, CategoryBreakdown, AllocationRow, Anomaly

def calculate_totals(transactions: list[TransactionRow]) -> InsightSummary:
    income = sum(t.amount for t in transactions if t.type == "income")
    expenses = sum(t.amount for t in transactions if t.type == "expense")
    net = income - expenses
    savings_rate = round((net / income) * 100, 2) if income > 0 else None

    return InsightSummary(
        total_income=income,
        total_expenses=expenses,
        net=net,
        savings_rate=savings_rate
    )


def category_breakdown(
    transactions: list[TransactionRow], 
    allocations: list[AllocationRow]) -> list[CategoryBreakdown]:

    expenses = [t for t in transactions if t.type == "expense"]
    if not expenses:
        return []
    
    total_expenses = sum(t.amount for t in expenses)

    alloc_map: dict[str, float] = {a.category_id: a.amount for a in allocations}

    #Group by category

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

    result = []

    for key, g in groups.items():
        budget_limit = alloc_map.get(key)
        budget_used_pct = None

        if budget_limit and budget_limit > 0:
            budget_used_pct = round((g["total"] / budget_limit) * 100, 2)

        
        result.append(CategoryBreakdown(
            category_id=g["category_id"],
            category_name=g["category_name"],
            icon=g["icon"],
            color=g["color"],
            total=round(g["total"], 2),
            count=g["count"],
            pct_of_total=round((g["total"] / total_expenses) * 100, 2),
            budget_limit=budget_limit,
            budget_used_pct=budget_used_pct
        ))

    return sorted(result, key=lambda x: x.total, reverse=True)


def compare_periods(
    current: list[TransactionRow],
    previous: list[TransactionRow]
) -> dict:
    
    """
    Positive = more spent/earned, negative = less spent/earned
    """

    def totals(txs):
        return (
            sum(t.amount for t in txs if t.type == "income"),
            sum(t.amount for t in txs if t.type == "expense")
        )

    current_income, current_expenses = totals(current)
    previous_income, previous_expenses = totals(previous)

    def pct_change(current, previous):
        if previous == 0:
            return None
        return round(((current - previous) / previous) * 100, 2)

    return {
        "income_change_pct": pct_change(current_income, previous_income),
        "expenses_change_pct": pct_change(current_expenses, previous_expenses)
    }


def anomalies(
    current: list[TransactionRow],
    previous: list[TransactionRow],
    allocations: list[AllocationRow]
    ) -> list[Anomaly]:

    return [
        *detect_category_spikes(current, previous),
        *detect_budget_overspending(current, allocations),
        *detect_large_single_transactions(current),
    ]


def detect_category_totals(txs: list[TransactionRow]) -> dict[str, float]:
    # Shared helper: sum expenses by category_id.
    d = {}
    for t in txs:
        if t.type != "expense":
            continue
        key = t.category_id or "uncategorized"
        if key not in d:
            d[key] = 0
        d[key] += t.amount
    return d

def detect_category_spikes(
    current: list[TransactionRow],
    previous: list[TransactionRow],
) -> list[Anomaly]:
    current_cats = detect_category_totals(current)
    previous_cats = detect_category_totals(previous)
    result = []

    for cat, current_total in current_cats.items():
        prev_total = previous_cats.get(cat, 0)
        if prev_total == 0:
            result.append(Anomaly(
                type="new_category",
                category_name=cat,
                message=f"New spending in {cat} - ${current_total:.0f} this month (not previously tracked)",
                severity="low",
                amount=current_total,
            ))
        else:
            change =((current_total - prev_total) / prev_total) 
            if change > 0.30:
                severity = "high" if change > 0.50 else "medium"
                result.append(Anomaly(
                    type="spike",
                    category_name=cat,
                    message=f"Spending in {cat} increased by {change*100:.0f}% this month (vs {prev_total:.0f} last month)",
                    severity=severity,
                    amount=current_total,
                ))
    return result


def detect_budget_overspending(
    current: list[TransactionRow],
    allocations: list[AllocationRow],
) -> list[Anomaly]:
    # Rule 2: Budget overspending
    result = []
    current_cats = detect_category_totals(current)
    alloc_map = {a.category_id: a.amount for a in allocations if a.category_id}
    for cat, total in current_cats.items():
        limit = alloc_map.get(cat, 0)
        if limit and total > limit:
            pct = total / limit * 100
            result.append(Anomaly(
                type="budget_exceeded",
                category_name=cat,
                message=f"'{cat}' budget exceeded by {pct:.0f}% of budget (${total:.0f} of ${limit:.0f})",
                severity="high" if pct > 120 else "medium",
                amount=total - limit,
            ))
    return result


def detect_large_single_transactions(
    current: list[TransactionRow],
) -> list[Anomaly]:
    # Rule 3: Large single transactions
    result = []
    expenses = [t for t in current if t.type == "expense"]
    if not expenses:
        return []
    
    if len(expenses) >= 5:
        mean = np.mean([t.amount for t in expenses])
        std = np.std([t.amount for t in expenses])
        threshold = mean + 2 * std
        outliers = [t for t in current if t.type == "expense" and t.amount > threshold]
        for t in outliers:
            label = t.merchant or t.description or t.category_name or "uncategorized"
            result.append(Anomaly(
                type="large_single",
                category_name=t.category_name,
                message=f"Unusually large single transaction: ${t.amount:.0f} in {label}",
                severity="high",
                amount=t.amount,
            ))
    return result
