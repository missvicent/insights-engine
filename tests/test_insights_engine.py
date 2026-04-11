"""Tests for category_name resolution in anomaly detectors.

Scope note: these tests cover only the category_name fix. A full engine
test suite is out of scope for this plan.
"""

from datetime import date

from app.models.schemas import AllocationRow, CategoryBreakdown, TransactionRow
from app.services.insights_engine import (
    category_breakdown,
    detect_budget_overspending,
    detect_category_spikes,
)


GROCERIES_ID = "cat-groceries-uuid"
GROCERIES_NAME = "Groceries"


def _expense(
    amount: float,
    category_id: str = GROCERIES_ID,
    category_name: str | None = GROCERIES_NAME,
) -> TransactionRow:
    return TransactionRow(
        id=f"tx-{amount}",
        user_id="user-1",
        category_id=category_id,
        amount=amount,
        transaction_date=date(2026, 4, 1),
        type="expense",
        category_name=category_name,
    )


def test_category_spike_anomaly_uses_category_name_not_id():
    current = [_expense(500.0)]
    previous = [_expense(100.0)]

    anomalies = detect_category_spikes(current, previous)

    assert len(anomalies) == 1
    anomaly = anomalies[0]
    assert anomaly.category_name == GROCERIES_NAME
    assert GROCERIES_NAME in anomaly.message
    assert GROCERIES_ID not in anomaly.message


def test_new_category_anomaly_uses_category_name_not_id():
    current = [_expense(75.0)]
    previous: list[TransactionRow] = []

    anomalies = detect_category_spikes(current, previous)

    assert len(anomalies) == 1
    anomaly = anomalies[0]
    assert anomaly.type == "new_category"
    assert anomaly.category_name == GROCERIES_NAME
    assert GROCERIES_NAME in anomaly.message
    assert GROCERIES_ID not in anomaly.message


def test_budget_overspending_anomaly_uses_category_name_not_id():
    current = [_expense(250.0)]
    allocations = [
        AllocationRow(
            id="alloc-1",
            budget_id="budget-1",
            category_id=GROCERIES_ID,
            amount=100.0,
        )
    ]

    anomalies = detect_budget_overspending(current, allocations)

    assert len(anomalies) == 1
    anomaly = anomalies[0]
    assert anomaly.category_name == GROCERIES_NAME
    assert GROCERIES_NAME in anomaly.message
    assert GROCERIES_ID not in anomaly.message


def test_category_breakdown_populates_transaction_count():
    current = [
        _expense(40.0),
        _expense(60.0),
        _expense(50.0, category_id="cat-rent-uuid", category_name="Rent"),
    ]

    result = category_breakdown(current, allocations=[])

    assert len(result) == 2
    assert all(isinstance(row, CategoryBreakdown) for row in result)

    by_id = {row.category_id: row for row in result}
    assert by_id[GROCERIES_ID].transaction_count == 2
    assert by_id[GROCERIES_ID].total == 100.0
    assert by_id["cat-rent-uuid"].transaction_count == 1
    assert by_id["cat-rent-uuid"].total == 50.0
