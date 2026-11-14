"""Advisor Agent (savings_guidance): deterministic flags + agentic retrieval + reflection."""
from __future__ import annotations

import re
from typing import Optional

from config import MOM_MIN_BASE, MOM_RISE_THRESHOLD, NEEDS, WANTS
from core.reflection import reflect
from core.tool_loop import run_tool_loop
from prompts.prompts import ADVISOR_DISCLAIMER, ADVISOR_PROMPT
from tools.pandas_tools import Analytics

ADVISOR_TOOLS = {"get_total_spend", "top_merchants", "monthly_trend", "find_transactions", "calculator"}


def compute_flags(an: Analytics, budgets: Optional[dict] = None) -> list[dict]:
    """Overspending flags computed in pandas before any LLM runs. Shared with the Dashboard."""
    flags: list[dict] = []
    df = an.df
    months = an.months

    # 1. 50/30/20 split over the whole statement
    ie = an.income_vs_expense()
    income = ie["income"]
    span = f"over the whole statement ({len(months)} months, {months[0]} to {months[-1]})" if months else ""
    if income > 0:
        exp = an._expense(df)
        needs = exp[exp["category"].isin(NEEDS)]["amount"].sum()
        wants = exp[exp["category"].isin(WANTS)]["amount"].sum()
        needs_pct, wants_pct = needs / income * 100, wants / income * 100
        if wants_pct > 30:
            flags.append({"kind": "50_30_20", "severity": "warning", "title": "Wants above 30% of income",
                          "detail": f"Wants (food, shopping, entertainment) are {wants_pct:.1f}% of income "
                                    f"(₹{wants:,.0f} of ₹{income:,.0f}); the 50/30/20 guideline is 30%."})
        if needs_pct > 50:
            flags.append({"kind": "50_30_20", "severity": "warning", "title": "Needs above 50% of income",
                          "detail": f"Needs are {needs_pct:.1f}% of income (₹{needs:,.0f}); guideline is 50%."})
        sr = ie["savings_rate_pct"]
        if sr is not None and sr < 20:
            flags.append({"kind": "savings_rate", "severity": "warning", "title": "Savings rate below 20%",
                          "detail": f"Savings rate is {sr:.1f}% (income ₹{income:,.0f}, expense ₹{ie['expense']:,.0f})."})
        monthly_income = income / max(len(months), 1)
        sr_val = sr if sr is not None else 0.0

        def verdict(ok: bool) -> str:
            return "within guideline" if ok else "OUTSIDE guideline"

        flags.append({"kind": "split", "severity": "info", "title": "Your 50/30/20 split",
                      "detail": f"{span[0].upper() + span[1:]}: needs {needs_pct:.1f}% of income "
                                f"(guideline ≤50%, {verdict(needs_pct <= 50)}), wants {wants_pct:.1f}% "
                                f"(guideline ≤30%, {verdict(wants_pct <= 30)}), actual savings rate {sr_val:.1f}% "
                                f"(guideline ≥20%, {verdict(sr_val >= 20)}). "
                                f"Total income ₹{income:,.0f} (about ₹{monthly_income:,.0f} per month), "
                                f"expenses ₹{ie['expense']:,.0f}, left after expenses ₹{ie['net_after_expense']:,.0f}."})

    # 2. Month-over-month rises in the latest month
    if len(months) >= 2:
        prev, last = months[-2], months[-1]
        exp = an._expense(df)
        p = exp[exp["month"] == prev].groupby("category")["amount"].sum()
        l = exp[exp["month"] == last].groupby("category")["amount"].sum()
        for cat, cur in l.items():
            base = p.get(cat, 0.0)
            if base >= MOM_MIN_BASE and (cur - base) / base > MOM_RISE_THRESHOLD:
                flags.append({"kind": "mom_rise", "severity": "warning", "category": cat,
                              "title": f"{cat} up {((cur - base) / base * 100):.1f}% in {last}",
                              "detail": f"{cat}: ₹{base:,.0f} in {prev} → ₹{cur:,.0f} in {last} "
                                        f"(+₹{cur - base:,.0f}, +{(cur - base) / base * 100:.1f}%)."})

    # 3. Subscriptions
    rec = an.detect_recurring()
    if rec["subscriptions"]:
        names = ", ".join(f"{s['merchant']} ₹{s['monthly_cost']:,.0f}" for s in rec["subscriptions"])
        flags.append({"kind": "subscriptions", "severity": "info",
                      "title": f"Subscriptions: ₹{rec['subscriptions_monthly_total']:,.0f}/month",
                      "detail": f"{names}. That is ₹{rec['subscriptions_yearly_total']:,.0f} a year."})

    # 4. Anomalies
    for a in an.detect_anomalies().get("anomalies", []):
        flags.append({"kind": "anomaly", "severity": "warning", "category": a["category"],
                      "title": f"Unusually large {a['category']} payment",
                      "detail": f"₹{a['amount']:,.0f} at {a['merchant']} on {a['date']} "
                                f"({a['times_median']:.1f}x the median single {a['category']} transaction of "
                                f"₹{a['category_median']:,.0f})."})

    # 5. User budgets (latest month)
    if budgets and months:
        last = months[-1]
        exp = an._expense(df)
        spent = exp[exp["month"] == last].groupby("category")["amount"].sum()
        for cat, limit in budgets.items():
            if limit and spent.get(cat, 0) > limit:
                flags.append({"kind": "budget", "severity": "warning", "category": cat,
                              "title": f"{cat} over budget in {last}",
                              "detail": f"Spent ₹{spent[cat]:,.0f} against a budget of ₹{limit:,.0f} "
                                        f"(over by ₹{spent[cat] - limit:,.0f})."})
    return flags


def flags_text(flags: list[dict]) -> str:
    if not flags:
        return "- No overspending flags."
    return "\n".join(f"- [{f['severity']}] {f['title']}: {f['detail']}" for f in flags)


def run_advisor(x: dict) -> dict:
    ctx, trace = x["ctx"], x["trace"]
    trace["handler"] = "Advisor Agent (flags + pandas tools + search_budget_guidance)"
    flags = compute_flags(ctx.analytics, ctx.budgets)
    ftext = flags_text(flags)
    trace["flags"] = flags
    # The flags already cover the split, trends, subscriptions and anomalies, so the Advisor gets a
    # smaller tool set: fewer tool schemas per call keeps it inside free-tier token-per-minute limits.
    tools = [t for t in ctx.pandas_tools if t.name in ADVISOR_TOOLS]
    if "search_budget_guidance" in ctx.retrieval_tools:
        tools.append(ctx.retrieval_tools["search_budget_guidance"])
    variables = {"question": x["question"], "data_summary": ctx.analytics.data_summary(), "flags": ftext}
    draft = run_tool_loop(ctx.llm, ADVISOR_PROMPT, variables, tools, trace, "advisor_agent")
    sources = [c["result"] for c in trace["tool_calls"]] + [ftext, variables["data_summary"]]
    answer = reflect(ctx.llm, x["question"], draft, sources, trace)

    retrieved = sorted({c.get("source") for r in trace["retrieval"] if r["collection"] == "budget_guidance"
                        for c in r["chunks"] if c.get("source")})
    answer, removed = validate_citations(answer, retrieved)
    if trace.get("reflection") is not None:
        trace["reflection"]["removed_citations"] = removed
    if retrieved:
        answer += "\n\n**Sources consulted:** " + ", ".join(f"`{s}`" for s in retrieved)
    answer += "\n\n" + ADVISOR_DISCLAIMER
    return {"answer": answer}


_INLINE_CITATION = re.compile(r"[ \t]*[*_]?\((?:sources|source|see):?\s*([^)]*?\.md[^)]*)\)[*_]?", re.I)
_SOURCES_LINE = re.compile(r"^[ \t]*[*_]*\s*sources?(?: consulted)?\s*:.*$\n?", re.I | re.M)


def validate_citations(answer: str, retrieved: list[str]) -> tuple[str, list[str]]:
    """Keep inline '(source: x.md)' citations only for notes actually retrieved this turn.

    Model-written 'Sources: ...' lines are removed; the caller appends a list built from retrieval.
    """
    removed: list[str] = []

    def fix(m: re.Match) -> str:
        names = [n.strip(" `*_") for n in re.split(r"[,;]| and ", m.group(1)) if n.strip(" `*_")]
        bad = [n for n in names if n not in retrieved]
        if not bad:
            return m.group(0)
        removed.extend(bad)
        good = [n for n in names if n in retrieved]
        return f" (source: {', '.join(good)})" if good else ""

    answer = _INLINE_CITATION.sub(fix, answer)
    answer = _SOURCES_LINE.sub("", answer).rstrip()
    return answer, removed
