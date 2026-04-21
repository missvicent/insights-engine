"""Production-grade tests for app/services/insights_engine.py.

Organized as one class per public engine function. Shared factories live in
tests/conftest.py.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from app.models.schemas import (
    Anomaly,
    CategoryBreakdown,
    Pattern,
)
from app.services.insights_engine import (
    HIGH_SEVERITY_THRESHOLD,
    SPIKE_THRESHOLD,
    calculate_totals,
    category_breakdown,
    compare_periods,
    compute_goal_progress,
    detect_anomalies,
    detect_budget_overspending,
    detect_category_spikes,
    detect_end_of_period_concentration,
    detect_frequent_categories,
    detect_large_single_transactions,
    detect_patterns,
    detect_weekend_spend,
    sum_expenses_by_category,
)
from tests.conftest import (
    make_allocation,
    make_budget,
    make_expense,
    make_income,
)

# Each TestXxx class is implemented in Tasks 4-15.


class TestCalculateTotals:
    def test_mixed_income_and_expenses(self):
        txs = [make_income(1000.0), make_expense(200.0), make_expense(300.0)]
        result = calculate_totals(txs)
        assert result.total_income == pytest.approx(1000.0)
        assert result.total_expenses == pytest.approx(500.0)
        assert result.net == pytest.approx(500.0)
        assert result.savings_rate == pytest.approx(50.0)

    def test_income_only(self):
        result = calculate_totals([make_income(1000.0)])
        assert result.total_expenses == 0
        assert result.savings_rate == pytest.approx(100.0)

    def test_expense_only(self):
        result = calculate_totals([make_expense(50.0)])
        assert result.total_income == 0
        assert result.net == pytest.approx(-50.0)
        assert result.savings_rate is None

    def test_empty(self):
        result = calculate_totals([])
        assert result.total_income == 0
        assert result.total_expenses == 0
        assert result.net == 0
        assert result.savings_rate is None

    def test_zero_income_returns_none_savings_rate(self):
        result = calculate_totals([make_expense(10.0)])
        assert result.savings_rate is None

    def test_negative_net_produces_negative_savings_rate(self):
        txs = [make_income(100.0), make_expense(150.0)]
        result = calculate_totals(txs)
        assert result.savings_rate == pytest.approx(-50.0)


class TestCategoryBreakdown:
    def test_empty_returns_empty(self):
        assert category_breakdown([], []) == []

    def test_income_only_returns_empty(self):
        assert category_breakdown([make_income(500.0)], []) == []

    def test_groups_by_category(self):
        txs = [
            make_expense(40.0, category_id="cat-g", category_name="Groc"),
            make_expense(60.0, category_id="cat-g", category_name="Groc"),
            make_expense(50.0, category_id="cat-r", category_name="Rent"),
        ]
        result = category_breakdown(txs, [])
        by_id = {r.category_id: r for r in result}
        assert by_id["cat-g"].total == pytest.approx(100.0)
        assert by_id["cat-g"].transaction_count == 2
        assert by_id["cat-r"].total == pytest.approx(50.0)
        assert by_id["cat-r"].transaction_count == 1

    def test_sorted_desc_by_total(self):
        txs = [
            make_expense(10.0, category_id="a", category_name="A"),
            make_expense(100.0, category_id="b", category_name="B"),
            make_expense(50.0, category_id="c", category_name="C"),
        ]
        result = category_breakdown(txs, [])
        assert [r.category_id for r in result] == ["b", "c", "a"]

    def test_pct_of_total_sums_to_100(self):
        txs = [
            make_expense(25.0, category_id="a", category_name="A"),
            make_expense(75.0, category_id="b", category_name="B"),
        ]
        result = category_breakdown(txs, [])
        assert sum(r.pct_of_total for r in result) == pytest.approx(100.0)

    def test_budget_used_pct_calculated(self):
        txs = [make_expense(50.0, category_id="cat-g", category_name="Groc")]
        allocs = [make_allocation(category_id="cat-g", amount=100.0)]
        result = category_breakdown(txs, allocs)
        assert result[0].budget_used_pct == pytest.approx(50.0)

    def test_no_allocation_yields_none_budget_pct(self):
        txs = [make_expense(50.0, category_id="cat-g", category_name="Groc")]
        result = category_breakdown(txs, [])
        assert result[0].budget_limit is None
        assert result[0].budget_used_pct is None

    def test_zero_allocation_yields_none_budget_pct(self):
        txs = [make_expense(50.0, category_id="cat-g", category_name="Groc")]
        allocs = [make_allocation(category_id="cat-g", amount=0.0)]
        result = category_breakdown(txs, allocs)
        assert result[0].budget_used_pct is None

    def test_uncategorized_bucket(self):
        txs = [make_expense(20.0, category_id=None, category_name=None)]
        result = category_breakdown(txs, [])
        assert result[0].category_id == "uncategorized"

    def test_icon_and_color_preserved(self):
        txs = [
            make_expense(
                20.0,
                category_id="cat-g",
                category_name="Groc",
                category_icon="🛒",
                category_color="#fff",
            )
        ]
        result = category_breakdown(txs, [])
        assert result[0].icon == "🛒"
        assert result[0].color == "#fff"


class TestComparePeriods:
    def test_both_empty(self):
        assert compare_periods([], []) == {
            "income_change_pct": None,
            "expenses_change_pct": None,
        }

    def test_both_increase(self):
        current = [make_income(1200.0), make_expense(600.0)]
        previous = [make_income(1000.0), make_expense(500.0)]
        result = compare_periods(current, previous)
        assert result["income_change_pct"] == pytest.approx(20.0)
        assert result["expenses_change_pct"] == pytest.approx(20.0)

    def test_both_decrease(self):
        current = [make_income(800.0), make_expense(400.0)]
        previous = [make_income(1000.0), make_expense(500.0)]
        result = compare_periods(current, previous)
        assert result["income_change_pct"] == pytest.approx(-20.0)
        assert result["expenses_change_pct"] == pytest.approx(-20.0)

    def test_previous_zero_returns_none(self):
        current = [make_expense(100.0)]
        result = compare_periods(current, [])
        assert result["expenses_change_pct"] is None

    def test_income_only_current(self):
        result = compare_periods([make_income(500.0)], [make_income(250.0)])
        assert result["income_change_pct"] == pytest.approx(100.0)
        assert result["expenses_change_pct"] is None

    def test_expense_only_current(self):
        result = compare_periods([make_expense(200.0)], [make_expense(100.0)])
        assert result["income_change_pct"] is None
        assert result["expenses_change_pct"] == pytest.approx(100.0)


class TestSumExpensesByCategory:
    def test_empty(self):
        assert sum_expenses_by_category([]) == {}

    def test_ignores_income(self):
        result = sum_expenses_by_category([make_income(500.0)])
        assert result == {}

    def test_groups_by_id(self):
        txs = [
            make_expense(40.0, category_id="a"),
            make_expense(60.0, category_id="a"),
            make_expense(50.0, category_id="b"),
        ]
        result = sum_expenses_by_category(txs)
        assert result == {"a": pytest.approx(100.0), "b": pytest.approx(50.0)}

    def test_uncategorized_bucket(self):
        txs = [make_expense(30.0, category_id=None)]
        result = sum_expenses_by_category(txs)
        assert result == {"uncategorized": pytest.approx(30.0)}

    def test_mixed_income_and_expense(self):
        txs = [make_income(1000.0), make_expense(50.0, category_id="a")]
        result = sum_expenses_by_category(txs)
        assert result == {"a": pytest.approx(50.0)}


class TestDetectCategorySpikes:
    def _tx(self, amount, *, category_id="cat-g", category_name="Groceries"):
        return make_expense(
            amount, category_id=category_id, category_name=category_name
        )

    def test_new_category(self):
        result = detect_category_spikes([self._tx(75.0)], [])
        assert len(result) == 1
        assert result[0].type == "new_category"
        assert result[0].severity == "low"
        assert result[0].amount == pytest.approx(75.0)
        assert "Groceries" in result[0].message
        assert "cat-g" not in result[0].message

    def test_category_removed_amount_is_prev_total(self):
        prev = [self._tx(200.0)]
        result = detect_category_spikes([], prev)
        assert len(result) == 1
        a = result[0]
        assert a.type == "category_removed"
        assert a.amount == pytest.approx(200.0)
        assert "Groceries" in a.message

    def test_spike_medium_severity(self):
        # 40% increase: between 30% and 50% → medium
        current = [self._tx(140.0)]
        previous = [self._tx(100.0)]
        result = detect_category_spikes(current, previous)
        assert result[0].type == "spike"
        assert result[0].severity == "medium"

    def test_spike_high_severity(self):
        # 100% increase: > 50% → high
        result = detect_category_spikes([self._tx(200.0)], [self._tx(100.0)])
        assert result[0].severity == "high"

    def test_spike_boundary_exactly_at_threshold(self):
        # change == SPIKE_THRESHOLD (0.30) → NOT a spike (strict >)
        current_amt = 100.0 * (1 + SPIKE_THRESHOLD)
        result = detect_category_spikes([self._tx(current_amt)], [self._tx(100.0)])
        assert result == []

    def test_spike_just_above_threshold(self):
        current_amt = 100.0 * (1 + SPIKE_THRESHOLD) + 0.01
        result = detect_category_spikes([self._tx(current_amt)], [self._tx(100.0)])
        assert len(result) == 1
        assert result[0].type == "spike"

    def test_high_severity_boundary(self):
        # change == HIGH_SEVERITY_THRESHOLD (0.50) → still medium (strict >)
        current_amt = 100.0 * (1 + HIGH_SEVERITY_THRESHOLD)
        result = detect_category_spikes([self._tx(current_amt)], [self._tx(100.0)])
        assert result[0].severity == "medium"

    def test_decrease_no_anomaly(self):
        result = detect_category_spikes([self._tx(50.0)], [self._tx(100.0)])
        assert result == []

    def test_no_change_no_anomaly(self):
        result = detect_category_spikes([self._tx(100.0)], [self._tx(100.0)])
        assert result == []

    def test_falls_back_to_id_when_name_missing(self):
        tx = make_expense(75.0, category_id="cat-x", category_name=None)
        result = detect_category_spikes([tx], [])
        assert result[0].category_name == "cat-x"

    def test_spike_message_contains_dollar_prev_total(self):
        result = detect_category_spikes([self._tx(200.0)], [self._tx(100.0)])
        assert "$100" in result[0].message

    def test_new_category_message_has_dollar(self):
        result = detect_category_spikes([self._tx(75.0)], [])
        assert "$75" in result[0].message


class TestDetectBudgetOverspending:
    def _tx(self, amount, *, category_id="cat-g", category_name="Groceries"):
        return make_expense(
            amount, category_id=category_id, category_name=category_name
        )

    def test_over_budget_medium(self):
        result = detect_budget_overspending(
            [self._tx(110.0)], [make_allocation(amount=100.0)]
        )
        assert len(result) == 1
        assert result[0].type == "budget_exceeded"
        assert result[0].severity == "medium"
        assert result[0].amount == pytest.approx(10.0)

    def test_over_budget_high_severity_above_120pct(self):
        result = detect_budget_overspending(
            [self._tx(130.0)], [make_allocation(amount=100.0)]
        )
        assert result[0].severity == "high"

    def test_exactly_at_120pct_is_medium(self):
        # pct > 120 is high; pct == 120 stays medium (strict >)
        result = detect_budget_overspending(
            [self._tx(120.0)], [make_allocation(amount=100.0)]
        )
        assert result[0].severity == "medium"

    def test_exactly_at_limit_no_anomaly(self):
        # total > limit is strict
        result = detect_budget_overspending(
            [self._tx(100.0)], [make_allocation(amount=100.0)]
        )
        assert result == []

    def test_under_budget_no_anomaly(self):
        result = detect_budget_overspending(
            [self._tx(50.0)], [make_allocation(amount=100.0)]
        )
        assert result == []

    def test_no_allocation_for_category_no_anomaly(self):
        result = detect_budget_overspending([self._tx(500.0)], [])
        assert result == []

    def test_uses_category_name_in_message(self):
        result = detect_budget_overspending(
            [self._tx(200.0)], [make_allocation(amount=100.0)]
        )
        assert "Groceries" in result[0].message
        assert "cat-g" not in result[0].message

    def test_multiple_categories_over_budget(self):
        txs = [
            self._tx(200.0, category_id="a", category_name="A"),
            self._tx(300.0, category_id="b", category_name="B"),
        ]
        allocs = [
            make_allocation(category_id="a", amount=100.0),
            make_allocation(category_id="b", amount=100.0),
        ]
        result = detect_budget_overspending(txs, allocs)
        assert len(result) == 2


class TestDetectLargeSingleTransactions:
    def test_fewer_than_five_expenses_returns_empty(self):
        txs = [make_expense(100.0) for _ in range(4)]
        assert detect_large_single_transactions(txs) == []

    def test_five_uniform_expenses_no_outlier(self):
        txs = [make_expense(100.0) for _ in range(5)]
        assert detect_large_single_transactions(txs) == []

    def test_single_outlier_flagged(self):
        txs = [make_expense(100.0) for _ in range(5)]
        txs.append(make_expense(5000.0, merchant="Rolex"))
        result = detect_large_single_transactions(txs)
        assert len(result) == 1
        assert result[0].type == "large_single"
        assert result[0].severity == "high"
        assert result[0].amount == pytest.approx(5000.0)

    def test_label_precedence_merchant_first(self):
        txs = [make_expense(100.0) for _ in range(5)]
        txs.append(
            make_expense(
                5000.0,
                merchant="Merch",
                description="Desc",
                category_name="Cat",
            )
        )
        result = detect_large_single_transactions(txs)
        assert "Merch" in result[0].message

    def test_label_falls_back_to_description_then_category(self):
        txs = [make_expense(100.0) for _ in range(5)]
        txs.append(
            make_expense(
                5000.0,
                merchant=None,
                description="Desc",
                category_name="Cat",
            )
        )
        result = detect_large_single_transactions(txs)
        assert "Desc" in result[0].message

    def test_income_rows_ignored(self):
        txs = [make_expense(100.0) for _ in range(5)]
        txs.append(make_income(10000.0))
        result = detect_large_single_transactions(txs)
        assert result == []

    def test_multiple_outliers(self):
        # Need enough "normal" rows that the two outliers both exceed
        # mean + 2*std(ddof=1). With 20 uniform baseline rows, std stays small.
        txs = [make_expense(100.0) for _ in range(20)]
        txs.append(make_expense(5000.0, merchant="A"))
        txs.append(make_expense(6000.0, merchant="B"))
        result = detect_large_single_transactions(txs)
        assert len(result) == 2


class TestDetectAnomalies:
    def test_empty_inputs_empty_output(self):
        assert detect_anomalies([], [], []) == []

    def test_aggregates_all_three_detectors(self):
        current = [
            make_expense(200.0, category_id="cat-g", category_name="Groc"),
        ]
        previous = [
            make_expense(50.0, category_id="cat-g", category_name="Groc"),
        ]
        allocs = [make_allocation(category_id="cat-g", amount=100.0)]
        result = detect_anomalies(current, previous, allocs)
        types = {a.type for a in result}
        assert "spike" in types
        assert "budget_exceeded" in types

    def test_order_spikes_then_budget_then_large(self):
        # 5 uniform + 1 outlier; also a new_category anomaly
        current = [
            make_expense(100.0, category_id="a", category_name="A") for _ in range(5)
        ]
        current.append(make_expense(5000.0, category_id="a", category_name="A"))
        previous = []
        result = detect_anomalies(current, previous, [])
        # new_category (from spikes) must come before large_single
        new_cat_idx = next(i for i, a in enumerate(result) if a.type == "new_category")
        large_idx = next(i for i, a in enumerate(result) if a.type == "large_single")
        assert new_cat_idx < large_idx


def _expense_df(rows):
    """Build the DataFrame shape that detect_patterns feeds its sub-detectors."""
    return pd.DataFrame(
        [
            {
                "amount": r["amount"],
                "date": pd.to_datetime(r["date"]),
                "category_name": r.get("category_name"),
                "merchant": r.get("merchant"),
            }
            for r in rows
        ]
    )


class TestDetectWeekendSpend:
    # April 2026: Sat 4, 11, 18, 25; Sun 5, 12, 19, 26

    def test_weekend_heavy_produces_pattern(self):
        rows = [
            {"amount": 300.0, "date": date(2026, 4, 4)},  # Sat
            {"amount": 300.0, "date": date(2026, 4, 5)},  # Sun
            {"amount": 50.0, "date": date(2026, 4, 6)},  # Mon
            {"amount": 50.0, "date": date(2026, 4, 7)},  # Tue
        ]
        df = _expense_df(rows)
        total = sum(r["amount"] for r in rows)
        result = detect_weekend_spend(df, total)
        assert len(result) == 1
        assert result[0].type == "weekend_spend"
        assert result[0].data["weekend_daily"] == pytest.approx(300.0)
        assert result[0].data["weekday_daily"] == pytest.approx(50.0)

    def test_ratio_below_1_5_no_pattern(self):
        rows = [
            {"amount": 100.0, "date": date(2026, 4, 4)},
            {"amount": 100.0, "date": date(2026, 4, 6)},
        ]
        df = _expense_df(rows)
        total = 200.0
        assert detect_weekend_spend(df, total) == []

    def test_weekend_under_10pct_of_total_no_pattern(self):
        # Weekend daily spike but weekend total is a tiny slice of overall
        rows = [
            {"amount": 10.0, "date": date(2026, 4, 4)},  # Sat, 1 day weekend
        ]
        # Huge weekday total to suppress the 10% rule
        for day in range(6, 30):
            rows.append({"amount": 100.0, "date": date(2026, 4, day)})
        df = _expense_df(rows)
        total = sum(r["amount"] for r in rows)
        # Single weekend day at $10 vs weekday daily ~$100 → ratio fails anyway
        # But even if we bump it, 10% rule should stop the pattern:
        result = detect_weekend_spend(df, total)
        assert result == []

    def test_no_weekend_dates_returns_empty(self):
        rows = [
            {"amount": 100.0, "date": date(2026, 4, 6)},  # Mon
            {"amount": 100.0, "date": date(2026, 4, 7)},  # Tue
        ]
        df = _expense_df(rows)
        assert detect_weekend_spend(df, 200.0) == []

    def test_no_weekday_dates_returns_empty(self):
        rows = [
            {"amount": 100.0, "date": date(2026, 4, 4)},  # Sat
            {"amount": 100.0, "date": date(2026, 4, 5)},  # Sun
        ]
        df = _expense_df(rows)
        assert detect_weekend_spend(df, 200.0) == []

    def test_ratio_formatted_to_one_decimal(self):
        rows = [
            {"amount": 600.0, "date": date(2026, 4, 4)},
            {"amount": 600.0, "date": date(2026, 4, 5)},
            {"amount": 100.0, "date": date(2026, 4, 6)},
            {"amount": 100.0, "date": date(2026, 4, 7)},
        ]
        df = _expense_df(rows)
        total = 1400.0
        result = detect_weekend_spend(df, total)
        assert "6.0×" in result[0].message

    def test_message_contains_weekend_and_weekday_dollars(self):
        rows = [
            {"amount": 300.0, "date": date(2026, 4, 4)},
            {"amount": 300.0, "date": date(2026, 4, 5)},
            {"amount": 50.0, "date": date(2026, 4, 6)},
            {"amount": 50.0, "date": date(2026, 4, 7)},
        ]
        df = _expense_df(rows)
        total = 700.0
        msg = detect_weekend_spend(df, total)[0].message
        assert "$300/day" in msg
        assert "$50/day" in msg

    def test_data_fields_rounded_to_two_decimals(self):
        rows = [
            {"amount": 333.333, "date": date(2026, 4, 4)},
            {"amount": 50.0, "date": date(2026, 4, 6)},
        ]
        df = _expense_df(rows)
        total = 383.333
        result = detect_weekend_spend(df, total)
        assert result[0].data["weekend_daily"] == pytest.approx(333.33)


class TestDetectEndOfPeriodConcentration:
    # Reuses _expense_df from Task 12 (same module).
    # April 2026: 30-day budget, last quarter = Apr 23-30 (period_length//4 = 7)

    def test_concentrated_end_of_period(self):
        rows = [
            {"amount": 10.0, "date": date(2026, 4, 1)},
            {"amount": 10.0, "date": date(2026, 4, 5)},
            {"amount": 500.0, "date": date(2026, 4, 25)},  # in last quarter
        ]
        df = _expense_df(rows)
        result = detect_end_of_period_concentration(df, date(2026, 4, 1), date(2026, 4, 30))
        assert len(result) == 1
        assert result[0].type == "end_of_period_concentration"
        assert "%" in result[0].message

    def test_even_distribution_no_pattern(self):
        rows = [{"amount": 10.0, "date": date(2026, 4, d)} for d in range(1, 29)]
        df = _expense_df(rows)
        assert detect_end_of_period_concentration(df, date(2026, 4, 1), date(2026, 4, 30)) == []

    def test_exactly_40pct_no_pattern(self):
        # total=100, end_total=40 → ratio == 0.40, strict > fails
        rows = [
            {"amount": 60.0, "date": date(2026, 4, 1)},
            {"amount": 40.0, "date": date(2026, 4, 25)},
        ]
        df = _expense_df(rows)
        assert detect_end_of_period_concentration(df, date(2026, 4, 1), date(2026, 4, 30)) == []

    def test_zero_period_length_returns_empty(self):
        # window_start == window_end → period_length == 0 → returns []
        df = _expense_df([{"amount": 10.0, "date": date(2026, 4, 1)}])
        assert detect_end_of_period_concentration(df, date(2026, 4, 1), date(2026, 4, 1)) == []

    def test_pct_formatting_no_syntax_error(self):
        # Regression: the old code had `:.1f * 100` which raised ValueError.
        rows = [
            {"amount": 10.0, "date": date(2026, 4, 1)},
            {"amount": 500.0, "date": date(2026, 4, 25)},
        ]
        df = _expense_df(rows)
        result = detect_end_of_period_concentration(df, date(2026, 4, 1), date(2026, 4, 30))
        # Message includes a percent like "98.0%"
        assert result[0].message.endswith(
            "% of spending in the last quarter of the window"
        )

    def test_timestamp_comparison_against_date(self):
        # Regression: last_quarter must be pd.Timestamp-compatible.
        # If it weren't, this would raise a TypeError.
        rows = [{"amount": 500.0, "date": date(2026, 4, 25)}]
        df = _expense_df(rows)
        result = detect_end_of_period_concentration(df, date(2026, 4, 1), date(2026, 4, 30))
        assert len(result) == 1

    def test_short_budget_period(self):
        # 3-day period: period_length // 4 == 0 → last_quarter == end_date
        rows = [
            {"amount": 10.0, "date": date(2026, 4, 1)},
            {"amount": 500.0, "date": date(2026, 4, 3)},
        ]
        df = _expense_df(rows)
        result = detect_end_of_period_concentration(df, date(2026, 4, 1), date(2026, 4, 3))
        # Only the Apr 3 row is >= Apr 3, so it hits 98%.
        assert len(result) == 1


class TestDetectFrequentCategories:
    def test_empty_df(self):
        df = _expense_df([])
        assert detect_frequent_categories(df) == []

    def test_all_category_names_none(self):
        rows = [
            {"amount": 10.0, "date": date(2026, 4, 1), "category_name": None},
            {"amount": 10.0, "date": date(2026, 4, 2), "category_name": None},
        ]
        df = _expense_df(rows)
        assert detect_frequent_categories(df) == []

    def test_returns_top_three(self):
        rows = []
        for name, count in [("A", 5), ("B", 4), ("C", 3), ("D", 2)]:
            for i in range(count):
                rows.append(
                    {
                        "amount": 10.0,
                        "date": date(2026, 4, (i % 28) + 1),
                        "category_name": name,
                    }
                )
        df = _expense_df(rows)
        result = detect_frequent_categories(df)
        names = {p.category_name for p in result}
        assert names == {"A", "B", "C"}

    def test_zero_total_skipped(self):
        # Count > 0 but all amounts zero
        rows = [
            {"amount": 0.0, "date": date(2026, 4, 1), "category_name": "A"},
            {"amount": 0.0, "date": date(2026, 4, 2), "category_name": "A"},
        ]
        df = _expense_df(rows)
        assert detect_frequent_categories(df) == []

    def test_message_contains_dollar(self):
        rows = [
            {"amount": 25.0, "date": date(2026, 4, 1), "category_name": "Groc"},
            {"amount": 25.0, "date": date(2026, 4, 2), "category_name": "Groc"},
        ]
        df = _expense_df(rows)
        result = detect_frequent_categories(df)
        assert "$50" in result[0].message
        assert "Groc" in result[0].message
        assert "2 transactions" in result[0].message

    def test_data_dict_structure(self):
        rows = [
            {"amount": 25.0, "date": date(2026, 4, 1), "category_name": "Groc"},
            {"amount": 25.0, "date": date(2026, 4, 2), "category_name": "Groc"},
        ]
        df = _expense_df(rows)
        result = detect_frequent_categories(df)
        assert result[0].data == {
            "category": "Groc",
            "count": 2,
            "total": pytest.approx(50.0),
        }

    def test_type_literal(self):
        rows = [
            {"amount": 25.0, "date": date(2026, 4, 1), "category_name": "Groc"},
        ]
        df = _expense_df(rows)
        result = detect_frequent_categories(df)
        assert result[0].type == "frequent_category"


class TestDetectPatterns:
    def test_empty_expenses(self):
        assert detect_patterns([], date(2026, 4, 1), date(2026, 4, 30)) == []

    def test_income_only_returns_empty(self):
        assert detect_patterns([make_income(500.0)], date(2026, 4, 1), date(2026, 4, 30)) == []

    def test_aggregates_from_sub_detectors(self):
        # Build a dataset that triggers all three sub-detectors:
        # - weekend heavy spending
        # - end-of-month concentration
        # - frequent category "Groc"
        txs = []
        for d in [4, 5]:  # weekends
            txs.append(
                make_expense(
                    400.0,
                    transaction_date=date(2026, 4, d),
                    category_name="Groc",
                    category_id="cat-g",
                )
            )
        for d in [6, 7]:  # weekdays, small
            txs.append(
                make_expense(
                    10.0,
                    transaction_date=date(2026, 4, d),
                    category_name="Groc",
                    category_id="cat-g",
                )
            )
        # Concentrate at end of month
        for d in [25, 26, 27]:
            txs.append(
                make_expense(
                    500.0,
                    transaction_date=date(2026, 4, d),
                    category_name="Groc",
                    category_id="cat-g",
                )
            )
        result = detect_patterns(txs, date(2026, 4, 1), date(2026, 4, 30))
        types = {p.type for p in result}
        assert "end_of_period_concentration" in types
        assert "frequent_category" in types


class TestLiteralTypeValidation:
    def test_anomaly_rejects_bad_type(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            Anomaly(type="not_a_real_type", message="x", severity="low")

    def test_anomaly_rejects_bad_severity(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            Anomaly(type="spike", message="x", severity="critical")

    def test_pattern_rejects_bad_type(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            Pattern(type="not_a_pattern")

    def test_category_breakdown_carries_transaction_count(self):
        # Belt-and-suspenders regression: the original bug was passing
        # `count=...` instead of `transaction_count=...`.
        row = CategoryBreakdown(
            category_id="x",
            category_name="X",
            total=10.0,
            transaction_count=3,
            pct_of_total=100.0,
        )
        assert row.transaction_count == 3


class TestResolveWindow:
    def test_7d(self):
        from app.services.insights_engine import resolve_window

        today = date(2026, 4, 14)
        cs, ce, ps, pe = resolve_window("7d", today)
        assert ce == today
        assert cs == date(2026, 4, 7)  # today - 7d
        assert pe == date(2026, 4, 6)
        assert ps == date(2026, 3, 30)

    def test_15d(self):
        from app.services.insights_engine import resolve_window

        today = date(2026, 4, 14)
        cs, ce, ps, pe = resolve_window("15d", today)
        assert ce == today
        assert cs == date(2026, 3, 30)  # today - 15d
        assert pe == date(2026, 3, 29)
        assert ps == date(2026, 3, 14)

    def test_30d(self):
        from app.services.insights_engine import resolve_window

        today = date(2026, 4, 14)
        cs, ce, ps, pe = resolve_window("30d", today)
        assert ce == today
        assert cs == date(2026, 3, 15)  # today - 30d
        assert pe == date(2026, 3, 14)
        assert ps == date(2026, 2, 12)

    def test_3m(self):
        from app.services.insights_engine import resolve_window

        today = date(2026, 4, 14)
        cs, ce, ps, pe = resolve_window("3m", today)
        assert ce == today
        assert cs == date(2026, 1, 14)  # today - 90d
        assert pe == date(2026, 1, 13)
        assert ps == date(2025, 10, 15)

    def test_6m(self):
        from app.services.insights_engine import resolve_window

        today = date(2026, 4, 14)
        cs, ce, ps, pe = resolve_window("6m", today)
        assert ce == today
        assert (ce - cs).days == 180
        assert (pe - ps).days == 180
        assert pe == cs - timedelta(days=1)

    def test_12m(self):
        from app.services.insights_engine import resolve_window

        today = date(2026, 4, 14)
        cs, ce, ps, pe = resolve_window("12m", today)
        assert ce == today
        assert (ce - cs).days == 365
        assert (pe - ps).days == 365
        assert pe == cs - timedelta(days=1)

    def test_unknown_window_raises(self):
        from app.services.insights_engine import resolve_window

        with pytest.raises(ValueError, match="unknown window"):
            resolve_window("nope", date(2026, 4, 14))  # type: ignore[arg-type]


class TestHorizonForWindow:
    @pytest.mark.parametrize(
        "window,expected_horizon",
        [
            ("7d", 3),
            ("15d", 7),
            ("30d", 7),
            ("3m", 14),
            ("6m", 30),
            ("12m", 30),
        ],
    )
    def test_mapping(self, window, expected_horizon):
        from app.services.insights_engine import _horizon_for_window

        assert _horizon_for_window(window) == expected_horizon


class TestAllowedWindowsForPeriod:
    def test_monthly_returns_day_windows(self):
        from app.services.insights_engine import allowed_windows_for_period

        assert allowed_windows_for_period("monthly") == {"7d", "15d", "30d"}

    def test_yearly_returns_month_windows(self):
        from app.services.insights_engine import allowed_windows_for_period

        assert allowed_windows_for_period("yearly") == {"3m", "6m", "12m"}

    def test_unknown_period_raises(self):
        from app.services.insights_engine import allowed_windows_for_period

        with pytest.raises(ValueError, match="unknown budget period"):
            allowed_windows_for_period("weekly")


class TestComputeGoalProgress:
    def test_excludes_achieved_goals(self):
        from tests.conftest import make_goal

        goals = [
            make_goal(name="Done", is_achieved=True),
            make_goal(name="Active", is_achieved=False),
        ]
        result = compute_goal_progress(goals)

        assert [g.name for g in result] == ["Active"]

    def test_target_date_none_sets_days_remaining_none_and_on_track(self):
        from tests.conftest import make_goal

        result = compute_goal_progress([make_goal(target_date=None)])

        assert len(result) == 1
        assert result[0].days_remaining is None
        assert result[0].on_track is True

    def test_past_target_date_marks_off_track(self):
        from tests.conftest import make_goal

        past = date.today() - timedelta(days=10)
        result = compute_goal_progress(
            [make_goal(target_date=past, is_achieved=False)]
        )

        assert len(result) == 1
        assert result[0].on_track is False
        assert result[0].days_remaining is not None
        assert result[0].days_remaining < 0

    def test_zero_target_amount_yields_zero_pct_no_divzero(self):
        from tests.conftest import make_goal

        result = compute_goal_progress(
            [make_goal(target_amount=0.0, current_amount=50.0)]
        )

        assert len(result) == 1
        assert result[0].progress_pct == 0.0

    def test_halfway_to_target_with_future_deadline(self):
        from tests.conftest import make_goal

        future = date.today() + timedelta(days=30)
        result = compute_goal_progress(
            [
                make_goal(
                    target_amount=1000.0,
                    current_amount=500.0,
                    target_date=future,
                )
            ]
        )

        assert len(result) == 1
        assert result[0].progress_pct == pytest.approx(50.0)
        assert result[0].on_track is True
        assert result[0].days_remaining is not None
        assert result[0].days_remaining >= 0


class TestBuildSummary:
    def test_stamps_budget_identity(self):
        from app.services.insights_engine import build_summary
        from tests.conftest import make_goal

        budget = make_budget(id="budget-xyz")
        budget_with_name = budget.model_copy(update={"name": "April 2026"})

        summary = build_summary(
            budget=budget_with_name,
            allocations=[],
            current=[make_income(1000.0), make_expense(200.0)],
            previous=[make_income(800.0), make_expense(150.0)],
            goals=[make_goal()],
            window="30d",
            window_start=date(2026, 4, 1),
            window_end=date(2026, 4, 30),
        )

        assert summary.budget_id == "budget-xyz"
        assert summary.budget_name == "April 2026"

    def test_period_label_matches_window(self):
        from app.services.insights_engine import build_summary

        summary = build_summary(
            budget=make_budget(),
            allocations=[],
            current=[],
            previous=[],
            goals=[],
            window="30d",
            window_start=date(2026, 3, 15),
            window_end=date(2026, 4, 14),
        )

        assert summary.period_label == "Mar 15 – Apr 14, 2026"

    def test_period_label_crosses_year(self):
        from app.services.insights_engine import build_summary

        summary = build_summary(
            budget=make_budget(),
            allocations=[],
            current=[],
            previous=[],
            goals=[],
            window="30d",
            window_start=date(2025, 12, 15),
            window_end=date(2026, 1, 14),
        )

        assert summary.period_label == "Dec 15, 2025 – Jan 14, 2026"

    def test_totals_and_change_pct(self):
        from app.services.insights_engine import build_summary

        summary = build_summary(
            budget=make_budget(),
            allocations=[],
            current=[make_income(1000.0), make_expense(400.0)],
            previous=[make_income(800.0), make_expense(200.0)],
            goals=[],
            window="30d",
            window_start=date(2026, 4, 1),
            window_end=date(2026, 4, 30),
        )

        assert summary.total_income == pytest.approx(1000.0)
        assert summary.total_expenses == pytest.approx(400.0)
        assert summary.net == pytest.approx(600.0)
        assert summary.income_change_pct == pytest.approx(25.0)
        assert summary.expenses_change_pct == pytest.approx(100.0)
        assert summary.transaction_count == 2

    def test_empty_inputs(self):
        from app.services.insights_engine import build_summary

        summary = build_summary(
            budget=make_budget(),
            allocations=[],
            current=[],
            previous=[],
            goals=[],
            window="30d",
            window_start=date(2026, 4, 1),
            window_end=date(2026, 4, 30),
        )

        assert summary.total_income == 0
        assert summary.total_expenses == 0
        assert summary.category_breakdown == []
        assert summary.anomalies == []
        assert summary.patterns == []
        assert summary.transaction_count == 0

    def test_monthly_window_sets_horizon(self):
        from app.services.insights_engine import build_summary

        summary = build_summary(
            budget=make_budget(),
            allocations=[],
            current=[],
            previous=[],
            goals=[],
            window="30d",
            window_start=date(2026, 3, 15),
            window_end=date(2026, 4, 14),
        )

        assert summary.next_action_horizon_days == 7

    def test_yearly_window_sets_horizon(self):
        from app.services.insights_engine import build_summary

        summary = build_summary(
            budget=make_budget(),
            allocations=[],
            current=[],
            previous=[],
            goals=[],
            window="6m",
            window_start=date(2025, 10, 17),
            window_end=date(2026, 4, 14),
        )

        assert summary.next_action_horizon_days == 30
