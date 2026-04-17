"""Integration tests for GET /insights.

We override the `get_user_ctx` dependency with a UserContext wrapping a
FakeDB, so the route runs end-to-end without touching real Supabase.
"""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routes.deps import get_user_ctx
from tests.conftest import make_user_ctx


def _budget_row(user_id: str = "user-1") -> dict:
    return {
        "id": "budget-1",
        "user_id": user_id,
        "name": "April 2026",
        "period": "monthly",
        "amount": 5000.0,
        "start_date": date(2026, 4, 1).isoformat(),
        "end_date": date(2026, 4, 30).isoformat(),
        "is_active": True,
    }


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


class TestGetInsights:
    def test_returns_200_with_valid_ctx(self, client):
        app.dependency_overrides[get_user_ctx] = lambda: make_user_ctx(
            tables={
                "budgets": [_budget_row()],
                "allocations": [],
                "transactions": [],
                "goals": [],
            }
        )
        resp = client.get("/insights?budget_id=budget-1&window=1m")
        assert resp.status_code == 200
        body = resp.json()
        assert body["summary"]["budget_id"] == "budget-1"

    def test_missing_authorization_is_401(self, client):
        resp = client.get("/insights?budget_id=budget-1&window=1m")
        assert resp.status_code == 401

    def test_budget_not_found_is_404(self, client):
        app.dependency_overrides[get_user_ctx] = lambda: make_user_ctx(
            tables={"budgets": []}
        )
        resp = client.get("/insights?budget_id=missing&window=1m")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "budget not found"
