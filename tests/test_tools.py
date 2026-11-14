import json

import pytest

from data.generate_sample import BIG_PURCHASE, FOOD_TOTALS, SALARY, SUBSCRIPTIONS
from tools.calculator import CalculatorError, safe_calculate
from tools.pandas_tools import Analytics, make_pandas_tools


@pytest.fixture(scope="module")
def an(labelled_df):
    return Analytics(labelled_df)


def _truth_total(rows, cat=None, month=None, debit=True):
    s = 0.0
    for r in rows:
        if month and r.day.month != month:
            continue
        if cat and r.true_category != cat:
            continue
        s += (r.debit or 0) if debit else (r.credit or 0)
    return round(s, 2)


# ---------------------------------------------------------------- golden numbers
@pytest.mark.parametrize("month", [8, 9, 10])
def test_food_total_per_month(an, month):
    out = an.get_total_spend("food", f"2026-{month:02d}")
    assert out["total_spend"] == FOOD_TOTALS[month]
    assert out["category"] == "Food & Dining"


def test_compare_periods_food_rise_40pct(an):
    out = an.compare_periods("2026-09", "2026-10", "Food & Dining")
    assert out["total_a"] == 6000.0 and out["total_b"] == 8400.0
    assert out["change"] == 2400.0
    assert out["change_pct"] == 40.0


def test_compare_periods_relative_words(an):
    out = an.compare_periods("previous month", "latest month", "food")
    assert out["period_a"] == "2026-09" and out["period_b"] == "2026-10"


def test_recurring_subscriptions(an):
    out = an.detect_recurring()
    names = {s["merchant"] for s in out["subscriptions"]}
    assert names == set(SUBSCRIPTIONS)
    assert out["subscriptions_monthly_total"] == sum(a for _, a in SUBSCRIPTIONS.values())  # 2,267
    assert out["subscriptions_yearly_total"] == 2267 * 12
    other = {s["merchant"] for s in out["other_recurring_payments"]}
    assert "RAMESH KUMAR RENT" in other


def test_anomaly_is_croma_only(an):
    out = an.detect_anomalies()
    assert [a["merchant"] for a in out["anomalies"]] == ["CROMA ELECTRONICS"]
    assert out["anomalies"][0]["amount"] == BIG_PURCHASE[3]


def test_income_vs_expense(an, truth_rows):
    out = an.income_vs_expense()
    assert out["income"] == SALARY * 3
    exp = sum(r.debit or 0 for r in truth_rows if r.true_category not in ("Investments",))
    assert out["expense"] == pytest.approx(exp)
    assert out["invested"] == 15000.0
    assert out["savings_rate_pct"] == pytest.approx(round((SALARY * 3 - exp) / (SALARY * 3) * 100, 2))


def test_category_breakdown_sums_to_total(an, truth_rows):
    out = an.category_breakdown("2026-10")
    assert sum(c["amount"] for c in out["categories"]) == pytest.approx(out["total_spend"])
    assert sum(c["share_pct"] for c in out["categories"]) == pytest.approx(100, abs=0.1)
    food = next(c for c in out["categories"] if c["category"] == "Food & Dining")
    assert food["amount"] == 8400.0


def test_monthly_trend_food(an):
    out = an.monthly_trend("food")
    assert [m["total"] for m in out["months"]] == [6000.0, 6000.0, 8400.0]
    assert out["months"][2]["change_pct_vs_prev"] == 40.0


def test_monthly_trend_matches_truth(an, truth_rows):
    out = an.monthly_trend("Transport")
    for m in out["months"]:
        assert m["total"] == _truth_total(truth_rows, "Transport", int(m["month"][-2:]))


def test_top_merchants(an):
    out = an.top_merchants(3)
    assert out["top_merchants"][0]["merchant"] == "RAMESH KUMAR RENT"
    assert out["top_merchants"][0]["total"] == 66000.0


def test_find_transactions_filters(an):
    out = an.find_transactions(min_amount=30000, txn_type="debit")
    assert out["count"] == 1 and out["transactions"][0]["merchant"] == "CROMA ELECTRONICS"
    gym = an.find_transactions(merchant="cultfit", start_date="2026-09")
    assert gym["count"] == 1 and gym["transactions"][0]["date"] == "2026-09-15"
    biggest = an.find_transactions(category="Shopping", txn_type="debit", sort_by="amount")
    assert biggest["transactions"][0]["merchant"] == "CROMA ELECTRONICS"
    assert biggest["sorted_by"].startswith("amount")
    none = an.find_transactions(merchant="does-not-exist")
    assert none["count"] == 0 and "message" in none


def test_bare_month_name_maps_to_statement_year(an):
    out = an.get_total_spend("food", "October")
    assert out["total_spend"] == 8400.0


def test_tools_return_json_and_errors_not_exceptions(an):
    tools = {t.name: t for t in make_pandas_tools(an)}
    assert len(tools) == 10
    ok = json.loads(tools["get_total_spend"].invoke({"category": "Food & Dining", "start_date": "2026-10"}))
    assert ok["total_spend"] == 8400.0
    bad = json.loads(tools["get_total_spend"].invoke({"category": "Spaceships"}))
    assert "error" in bad and "Valid categories" in bad["error"]
    bad_date = json.loads(tools["compare_periods"].invoke({"period_a": "blah", "period_b": "2026-10"}))
    assert "error" in bad_date
    calc = json.loads(tools["calculator"].invoke({"expression": "2267 * 12"}))
    assert calc["result"] == 27204


def test_data_summary(an):
    s = an.data_summary()
    assert "2026-10" in s and "latest month: 2026-10" in s


# ---------------------------------------------------------------- calculator
@pytest.mark.parametrize("expr,val", [
    ("1 + 2 * 3", 7), ("(8400 - 6000) / 6000 * 100", 40), ("2,267 * 12", 27204),
    ("-5 + 10", 5), ("2 ** 10", 1024), ("₹500 + ₹250", 750), ("10 % 3", 1),
])
def test_calculator_valid(expr, val):
    assert safe_calculate(expr) == pytest.approx(val)


@pytest.mark.parametrize("expr", [
    "__import__('os').system('dir')", "open('x')", "a + 1", "().__class__", "[1, 2]",
    "lambda: 1", "1 if 1 else 2", "", "2 ** 1000", "1/0", "True + 1",
])
def test_calculator_rejects(expr):
    with pytest.raises(CalculatorError):
        safe_calculate(expr)
