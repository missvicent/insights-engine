"""Shared test factories for the insights engine suite.

Every factory takes keyword-only overrides with sensible defaults, so tests
override only the fields they care about.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from datetime import date
from typing import Any, Optional

import jwt as pyjwt
import pytest

from app.models.schemas import (
    AllocationRow,
    BudgetRow,
    GoalRow,
    TransactionRow,
)


def _uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def make_expense(
    amount: float = 10.0,
    *,
    category_id: Optional[str] = "cat-g",
    category_name: Optional[str] = "Groceries",
    category_icon: Optional[str] = None,
    category_color: Optional[str] = None,
    transaction_date: date = date(2026, 4, 1),
    merchant: Optional[str] = None,
    description: Optional[str] = None,
    id: Optional[str] = None,
    user_id: str = "user-1",
) -> TransactionRow:
    return TransactionRow(
        id=id or _uid("tx"),
        user_id=user_id,
        category_id=category_id,
        amount=amount,
        transaction_date=transaction_date,
        type="expense",
        merchant=merchant,
        description=description,
        category_name=category_name,
        category_icon=category_icon,
        category_color=category_color,
    )


def make_income(
    amount: float = 1000.0,
    *,
    transaction_date: date = date(2026, 4, 1),
    id: Optional[str] = None,
    user_id: str = "user-1",
) -> TransactionRow:
    return TransactionRow(
        id=id or _uid("tx"),
        user_id=user_id,
        category_id="cat-salary",
        amount=amount,
        transaction_date=transaction_date,
        type="income",
        category_name="Salary",
    )


def make_allocation(
    *,
    category_id: str = "cat-g",
    amount: float = 100.0,
    budget_id: str = "budget-1",
    id: Optional[str] = None,
    alert_threshold: int = 80,
) -> AllocationRow:
    return AllocationRow(
        id=id or _uid("alloc"),
        budget_id=budget_id,
        category_id=category_id,
        amount=amount,
        alert_threshold=alert_threshold,
    )


def make_budget(
    *,
    start_date: date = date(2026, 4, 1),
    end_date: date = date(2026, 4, 30),
    amount: float = 5000.0,
    id: Optional[str] = None,
    user_id: str = "user-1",
) -> BudgetRow:
    return BudgetRow(
        id=id or _uid("budget"),
        user_id=user_id,
        name="April 2026",
        period="monthly",
        amount=amount,
        start_date=start_date,
        end_date=end_date,
    )


def make_goal(
    *,
    name: str = "Emergency fund",
    target_amount: float = 1000.0,
    current_amount: float = 250.0,
    target_date: Optional[date] = None,
    is_achieved: bool = False,
    id: Optional[str] = None,
) -> "GoalRow":
    from app.models.schemas import GoalRow

    return GoalRow(
        id=id or _uid("goal"),
        name=name,
        target_amount=target_amount,
        current_amount=current_amount,
        target_date=target_date,
        is_achieved=is_achieved,
    )


@pytest.fixture
def jwt_secret() -> str:
    return "test-secret-do-not-use-in-prod"


@pytest.fixture
def make_token(jwt_secret: str) -> Callable[..., str]:
    """Build a signed JWT with sensible defaults, overrideable per-test."""

    def _make(
        claims: dict[str, Any] | None = None,
        secret: str | None = None,
        algorithm: str = "HS256",
        exp_delta: int = 3600,
        audience: str | None = "authenticated",
        sub: str | None = "test-user",
        omit: tuple[str, ...] = (),
    ) -> str:
        now = int(time.time())
        payload: dict[str, Any] = {
            "iat": now,
            "exp": now + exp_delta,
            "sub": sub,
            "aud": audience,
        }
        if claims:
            payload.update(claims)
        for key in omit:
            payload.pop(key, None)
        return pyjwt.encode(payload, secret or jwt_secret, algorithm=algorithm)

    return _make
