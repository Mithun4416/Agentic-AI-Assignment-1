"""Exact analysis tools over the session DataFrame.

`Analytics` holds plain Python methods (tested without any LLM). `make_pandas_tools`
wraps them as LangChain `@tool`s that return compact JSON strings. The LLM only picks
a tool and its typed arguments; it never writes or runs pandas code.
"""
from __future__ import annotations

import json
import re
from typing import Optional

import pandas as pd
from langchain_core.tools import tool

from config import ANOMALY_MULTIPLIER, CATEGORIES, NOT_EXPENSE
from tools.calculator import CalculatorError, safe_calculate

CATEGORY_ALIASES = {
    "food": "Food & Dining", "dining": "Food & Dining", "eating out": "Food & Dining",
    "restaurants": "Food & Dining", "food delivery": "Food & Dining",
    "grocery": "Groceries", "groceries": "Groceries",
    "travel": "Transport", "transport": "Transport", "commute": "Transport", "cab": "Transport", "cabs": "Transport",
    "shopping": "Shopping",
    "bills": "Bills & Utilities", "utilities": "Bills & Utilities",
    "rent": "Rent", "housing": "Rent",
    "entertainment": "Entertainment & Subscriptions", "subscriptions": "Entertainment & Subscriptions",
    "subscription": "Entertainment & Subscriptions", "streaming": "Entertainment & Subscriptions",
    "health": "Health", "medical": "Health", "fitness": "Health", "gym": "Health",
    "education": "Education", "courses": "Education",
    "transfers": "Transfers", "transfer": "Transfers",
    "cash": "Cash Withdrawal", "atm": "Cash Withdrawal",
    "investments": "Investments", "investment": "Investments", "sip": "Investments",
    "income": "Income", "salary": "Income", "other": "Other",
}

_NO_EXPENSE_CATS = NOT_EXPENSE
SUBSCRIPTION_EXCLUDE = {"Rent", "Investments", "Bills & Utilities", "Transfers", "Income", "Cash Withdrawal"}


class ToolInputError(ValueError):
    pass


def _r(x) -> float:
    return round(float(x), 2)


def resolve_category(category: Optional[str]) -> Optional[str]:
    if category is None or str(category).strip().lower() in {"", "all", "any", "none", "null"}:
        return None
    c = str(category).strip()
    for cat in CATEGORIES:
        if c.lower() == cat.lower():
            return cat
    if c.lower() in CATEGORY_ALIASES:
        return CATEGORY_ALIASES[c.lower()]
    for cat in CATEGORIES:
        if c.lower() in cat.lower():
            return cat
    raise ToolInputError(f"Unknown category '{category}'. Valid categories: {', '.join(CATEGORIES)}.")


class Analytics:
    def __init__(self, df: pd.DataFrame):
        self.df = df.copy()
        self.df["date"] = pd.to_datetime(self.df["date"])
        self.df["month"] = self.df["date"].dt.strftime("%Y-%m")

    # ----------------------------------------------------------------- helpers
    @property
    def months(self) -> list[str]:
        return sorted(self.df["month"].unique())

    def _month_bounds(self, ym: str) -> tuple[pd.Timestamp, pd.Timestamp]:
        p = pd.Period(ym, freq="M")
        return p.start_time.normalize(), p.end_time.normalize()

    def _parse_month(self, text: str) -> Optional[str]:
        t = text.strip().lower()
        months = self.months
        if t in {"latest", "latest month", "this month", "current month", "last month in statement"}:
            return months[-1]
        if t in {"previous", "previous month", "last month", "prior month", "month before"}:
            return months[-2] if len(months) > 1 else months[-1]
        if re.fullmatch(r"\d{4}-\d{2}", t):
            return t
        try:
            p = pd.Period(pd.to_datetime(text, format="mixed"), freq="M")
            # bare month names ("Oct") parse to the current year; map to the statement year
            if not re.search(r"\d{4}", text):
                for m in reversed(months):
                    if m.endswith(f"-{p.month:02d}"):
                        return m
            return str(p)
        except (ValueError, TypeError):
            return None

    def _bounds(self, start_date: Optional[str], end_date: Optional[str]):
        start = end = None
        if start_date:
            s = str(start_date).strip()
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
                start = pd.Timestamp(s)
            else:
                ym = self._parse_month(s)
                if not ym:
                    raise ToolInputError(f"Could not understand start_date '{start_date}'. Use YYYY-MM-DD or YYYY-MM.")
                start = self._month_bounds(ym)[0]
                if not end_date:
                    end = self._month_bounds(ym)[1]
        if end_date:
            e = str(end_date).strip()
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", e):
                end = pd.Timestamp(e)
            else:
                ym = self._parse_month(e)
                if not ym:
                    raise ToolInputError(f"Could not understand end_date '{end_date}'. Use YYYY-MM-DD or YYYY-MM.")
                end = self._month_bounds(ym)[1]
        return start, end

    def _period(self, period: str) -> tuple[pd.Timestamp, pd.Timestamp, str]:
        if period is None or not str(period).strip():
            raise ToolInputError("A period is required, e.g. '2026-10' or '2026-09-01:2026-09-30'.")
        p = str(period).strip()
        if ":" in p:
            a, b = p.split(":", 1)
            s, e = self._bounds(a, b)
            return s, e, p
        ym = self._parse_month(p)
        if not ym:
            raise ToolInputError(f"Could not understand period '{period}'. Use YYYY-MM.")
        s, e = self._month_bounds(ym)
        return s, e, ym

    def _filter(self, category=None, start_date=None, end_date=None, txn_type: Optional[str] = "debit",
                start=None, end=None) -> pd.DataFrame:
        d = self.df
        cat = resolve_category(category)
        if cat:
            d = d[d["category"] == cat]
        if start is None and end is None:
            start, end = self._bounds(start_date, end_date)
        if start is not None:
            d = d[d["date"] >= start]
        if end is not None:
            d = d[d["date"] <= end]
        if txn_type:
            d = d[d["type"] == txn_type]
        return d

    def _expense(self, d: pd.DataFrame) -> pd.DataFrame:
        return d[(d["type"] == "debit") & (~d["category"].isin(_NO_EXPENSE_CATS))]

    def data_summary(self) -> str:
        d = self.df
        cats = sorted(d["category"].dropna().unique())
        return (
            f"{len(d)} transactions from {d['date'].min():%Y-%m-%d} to {d['date'].max():%Y-%m-%d}. "
            f"Months: {', '.join(self.months)} (latest month: {self.months[-1]}"
            + (f", previous month: {self.months[-2]}" if len(self.months) > 1 else "")
            + f"). Categories present: {', '.join(cats)}. Currency: INR."
        )

    # ----------------------------------------------------------------- tools
    def get_total_spend(self, category=None, start_date=None, end_date=None) -> dict:
        d = self._filter(category, start_date, end_date)
        if not resolve_category(category):
            d = self._expense(d)
        start, end = self._bounds(start_date, end_date)
        return {
            "category": resolve_category(category) or "All spending (excl. investments)",
            "start_date": f"{start:%Y-%m-%d}" if start is not None else f"{self.df['date'].min():%Y-%m-%d}",
            "end_date": f"{end:%Y-%m-%d}" if end is not None else f"{self.df['date'].max():%Y-%m-%d}",
            "total_spend": _r(d["amount"].sum()),
            "transaction_count": int(len(d)),
        }

    def category_breakdown(self, start_date=None, end_date=None) -> dict:
        d = self._filter(None, start_date, end_date)
        d = self._expense(d)
        total = d["amount"].sum()
        if total == 0:
            return {"message": "No spending in that period."}
        g = d.groupby("category")["amount"].agg(["sum", "count"]).sort_values("sum", ascending=False)
        return {
            "total_spend": _r(total),
            "categories": [
                {"category": c, "amount": _r(row["sum"]), "share_pct": _r(row["sum"] / total * 100),
                 "count": int(row["count"])}
                for c, row in g.iterrows()
            ],
        }

    def monthly_trend(self, category=None) -> dict:
        d = self._filter(category)
        if not resolve_category(category):
            d = self._expense(d)
        g = d.groupby("month")["amount"].sum().reindex(self.months, fill_value=0.0)
        rows = []
        prev = None
        for m, v in g.items():
            row = {"month": m, "total": _r(v)}
            if prev:
                row["change_pct_vs_prev"] = _r((v - prev) / prev * 100)
            rows.append(row)
            prev = v
        return {"category": resolve_category(category) or "All spending (excl. investments)", "months": rows}

    def top_merchants(self, n: int = 5, category=None, start_date=None, end_date=None) -> dict:
        n = max(1, min(int(n or 5), 25))
        d = self._filter(category, start_date, end_date)
        if not resolve_category(category):
            d = self._expense(d)
        if d.empty:
            return {"message": "No matching spending."}
        g = d.groupby("merchant")["amount"].agg(["sum", "count"]).sort_values("sum", ascending=False).head(n)
        return {"top_merchants": [
            {"merchant": m, "total": _r(r["sum"]), "transactions": int(r["count"])} for m, r in g.iterrows()
        ]}

    def compare_periods(self, period_a: str, period_b: str, category=None) -> dict:
        sa, ea, la = self._period(period_a)
        sb, eb, lb = self._period(period_b)
        cat = resolve_category(category)

        def tot(s, e):
            d = self._filter(cat, start=s, end=e)
            if not cat:
                d = self._expense(d)
            return float(d["amount"].sum())

        a, b = tot(sa, ea), tot(sb, eb)
        out = {"category": cat or "All spending (excl. investments)", "period_a": la, "total_a": _r(a),
               "period_b": lb, "total_b": _r(b), "change": _r(b - a)}
        out["change_pct"] = _r((b - a) / a * 100) if a else None
        out["note"] = "change = total_b - total_a; change_pct is relative to period_a"
        return out

    def find_transactions(self, merchant=None, min_amount=None, max_amount=None, start_date=None,
                          end_date=None, category=None, txn_type=None, limit: int = 20,
                          sort_by: str = "date") -> dict:
        d = self._filter(category, start_date, end_date, txn_type=txn_type if txn_type in ("debit", "credit") else None)
        if merchant:
            pat = re.escape(str(merchant).strip())
            d = d[d["merchant"].str.contains(pat, case=False, na=False)
                  | d["description"].str.contains(pat, case=False, na=False)]
        if min_amount is not None:
            d = d[d["amount"] >= float(min_amount)]
        if max_amount is not None:
            d = d[d["amount"] <= float(max_amount)]
        if d.empty:
            return {"message": "No transactions match those filters.", "count": 0}
        limit = max(1, min(int(limit or 20), 50))
        by_amount = str(sort_by or "date").lower() == "amount"
        d = d.sort_values("amount", ascending=False, kind="stable") if by_amount else d.sort_values("date", kind="stable")
        return {
            "count": int(len(d)),
            "sorted_by": "amount (largest first)" if by_amount else "date",
            "total_of_matches": _r(d["amount"].sum()),
            "shown": int(min(limit, len(d))),
            "transactions": [
                {"txn_id": r.txn_id, "date": f"{r.date:%Y-%m-%d}", "merchant": r.merchant, "amount": _r(r.amount),
                 "type": r.type, "category": r.category}
                for r in d.head(limit).itertuples()
            ],
        }

    def detect_recurring(self) -> dict:
        d = self.df[self.df["type"] == "debit"]
        n_months = len(self.months)
        min_months = min(3, n_months) if n_months >= 2 else 2
        recurring = []
        for merchant, g in d.groupby("merchant"):
            months = g["month"].nunique()
            if months < min_months or len(g) != months:
                continue
            mean = g["amount"].mean()
            cv = g["amount"].std(ddof=0) / mean if mean else 1
            day_spread = g["date"].dt.day.max() - g["date"].dt.day.min()
            if cv <= 0.05 and day_spread <= 3:
                recurring.append({
                    "merchant": merchant, "category": g["category"].iloc[0],
                    "monthly_cost": _r(mean), "day_of_month": int(g["date"].dt.day.median()),
                    "months_seen": int(months),
                })
        recurring.sort(key=lambda x: -x["monthly_cost"])
        subs = [r for r in recurring if r["category"] not in SUBSCRIPTION_EXCLUDE]
        return {
            "subscriptions": subs,
            "subscriptions_monthly_total": _r(sum(s["monthly_cost"] for s in subs)),
            "subscriptions_yearly_total": _r(sum(s["monthly_cost"] for s in subs) * 12),
            "other_recurring_payments": [r for r in recurring if r["category"] in SUBSCRIPTION_EXCLUDE],
        }

    def detect_anomalies(self, multiplier: float = ANOMALY_MULTIPLIER) -> dict:
        d = self._expense(self.df)
        out = []
        for cat, g in d.groupby("category"):
            if len(g) < 3:
                continue
            med = g["amount"].median()
            thr = multiplier * med
            for r in g[(g["amount"] > thr) & (g["amount"] >= 1000)].itertuples():
                out.append({"txn_id": r.txn_id, "date": f"{r.date:%Y-%m-%d}", "merchant": r.merchant,
                            "category": cat, "amount": _r(r.amount), "category_median": _r(med),
                            "times_median": _r(r.amount / med)})
        out.sort(key=lambda x: -x["amount"])
        if not out:
            return {"message": "No unusually large transactions found.", "anomalies": []}
        return {"anomalies": out, "rule": f"amount > {multiplier:g}x the category median"}

    def income_vs_expense(self, start_date=None, end_date=None) -> dict:
        d = self._filter(None, start_date, end_date, txn_type=None)
        credits = d[d["type"] == "credit"]
        income = credits[credits["category"] == "Income"]["amount"].sum()
        other_credits = credits[credits["category"] != "Income"]["amount"].sum()
        expense = self._expense(d)["amount"].sum()
        invested = d[(d["type"] == "debit") & (d["category"] == "Investments")]["amount"].sum()
        return {
            "income": _r(income),
            "other_credits": _r(other_credits),
            "expense": _r(expense),
            "invested": _r(invested),
            "net_after_expense": _r(income - expense),
            "savings_rate_pct": _r((income - expense) / income * 100) if income else None,
            "note": "expense excludes investments; savings rate = (income - expense) / income",
        }


def _json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


def _safe(fn, *args, **kwargs) -> str:
    try:
        return _json(fn(*args, **kwargs))
    except (ToolInputError, CalculatorError, ValueError, TypeError) as exc:
        return _json({"error": str(exc)})


def make_pandas_tools(an: Analytics) -> list:
    """Build @tool functions bound to one session's Analytics object."""

    @tool
    def get_total_spend(category: Optional[str] = None, start_date: Optional[str] = None,
                        end_date: Optional[str] = None) -> str:
        """Total debit amount for an optional category and date range.
        Dates: YYYY-MM-DD, or YYYY-MM for a whole month. Omit category for all spending."""
        return _safe(an.get_total_spend, category, start_date, end_date)

    @tool
    def category_breakdown(start_date: Optional[str] = None, end_date: Optional[str] = None) -> str:
        """Amount, share (%) and count of spending per category for an optional date range (YYYY-MM or YYYY-MM-DD)."""
        return _safe(an.category_breakdown, start_date, end_date)

    @tool
    def monthly_trend(category: Optional[str] = None) -> str:
        """Month-by-month spending totals with % change vs the previous month, for one category or all spending."""
        return _safe(an.monthly_trend, category)

    @tool
    def top_merchants(n: int = 5, category: Optional[str] = None, start_date: Optional[str] = None,
                      end_date: Optional[str] = None) -> str:
        """Top n merchants by total spend, optionally within a category and date range."""
        return _safe(an.top_merchants, n, category, start_date, end_date)

    @tool
    def compare_periods(period_a: str, period_b: str, category: Optional[str] = None) -> str:
        """Compare spending between two periods. Each period is YYYY-MM, or 'YYYY-MM-DD:YYYY-MM-DD'.
        Returns both totals, absolute change and % change (period_b relative to period_a)."""
        return _safe(an.compare_periods, period_a, period_b, category)

    @tool
    def find_transactions(merchant: Optional[str] = None, min_amount: Optional[float] = None,
                          max_amount: Optional[float] = None, start_date: Optional[str] = None,
                          end_date: Optional[str] = None, category: Optional[str] = None,
                          txn_type: Optional[str] = None, sort_by: str = "date") -> str:
        """Find transactions with exact filters: merchant text (substring), amount range, dates,
        category, and txn_type ('debit' or 'credit'). sort_by is 'date' (default) or 'amount'
        (largest first; use it for "big"/"largest" questions). Returns matching rows."""
        return _safe(an.find_transactions, merchant, min_amount, max_amount, start_date, end_date,
                     category, txn_type, 20, sort_by)

    @tool
    def detect_recurring() -> str:
        """Detect recurring payments (same merchant, similar amount, same day each month).
        Returns subscriptions with monthly and yearly totals, plus other recurring payments like rent."""
        return _safe(an.detect_recurring)

    @tool
    def detect_anomalies() -> str:
        """Find transactions far above their category's normal (median) amount."""
        return _safe(an.detect_anomalies)

    @tool
    def income_vs_expense(start_date: Optional[str] = None, end_date: Optional[str] = None) -> str:
        """Income, expense (excluding investments), amount invested, net, and savings rate for a date range."""
        return _safe(an.income_vs_expense, start_date, end_date)

    @tool
    def calculator(expression: str) -> str:
        """Safe arithmetic on numbers only, e.g. '(8400 - 6000) / 6000 * 100' or '2267 * 12'."""
        try:
            return _json({"expression": expression, "result": safe_calculate(expression)})
        except CalculatorError as exc:
            return _json({"error": str(exc)})

    return [get_total_spend, category_breakdown, monthly_trend, top_merchants, compare_periods,
            find_transactions, detect_recurring, detect_anomalies, income_vs_expense, calculator]
