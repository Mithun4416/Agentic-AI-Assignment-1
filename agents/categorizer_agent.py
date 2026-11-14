"""Categorizer Agent: keyword rules first, then a few-shot LLM pass for unknown merchants.

Only unique merchant names (already masked) are sent to the LLM, in batches of ~25,
and the merchant -> category map is cached so reruns do not call the LLM again.
"""
from __future__ import annotations

import re
from typing import Literal, Optional

import pandas as pd
from pydantic import BaseModel, Field

from config import CATEGORIES

CategoryName = Literal[tuple(CATEGORIES)]  # type: ignore[valid-type]

# (keyword regex, category). Checked against merchant, then the description. First hit wins.
RULES: list[tuple[str, str]] = [
    (r"SALARY|PAYROLL|INT\.?\s?PD|INTEREST CREDIT|DIVIDEND", "Income"),
    (r"\bRENT\b|LANDLORD|NOBROKER|NESTAWAY", "Rent"),
    (r"\bATM\b|CASH WDL|CASH WITHDRAWAL", "Cash Withdrawal"),
    (r"ZERODHA|GROWW|UPSTOX|\bSIP\b|MUTUAL FUND|\bMF\b|KUVERA|PPF|\bNPS\b|SMALLCASE", "Investments"),
    (r"SWIGGY|ZOMATO|DOMINOS|PIZZA|MCDONALD|KFC|STARBUCKS|BURGER|RESTAURANT|CAFE|COFFEE|EATSURE|HALDIRAM|DUNZO DAILY", "Food & Dining"),
    (r"BLINKIT|BIGBASKET|BBNOW|DMART|ZEPTO|INSTAMART|GROFERS|JIOMART|MORE RETAIL|RELIANCE FRESH|SPENCERS|NATURES BASKET", "Groceries"),
    (r"\bUBER\b|\bOLA\b|RAPIDO|METRO|BMRCL|DMRC|IRCTC|REDBUS|PETROL|FUEL|\bHPCL\b|\bBPCL\b|INDIAN OIL|FASTAG|NAMMA YATRI", "Transport"),
    (r"NETFLIX|SPOTIFY|PRIME VIDEO|HOTSTAR|JIOCINEMA|YOUTUBE|BOOKMYSHOW|PVR|INOX|SONYLIV|ZEE5|APPLE\.COM|GOOGLE PLAY|STEAM", "Entertainment & Subscriptions"),
    (r"AIRTEL|\bJIO\b|VODAFONE|\bVI\b|BSNL|BESCOM|ELECTRICITY|TATA POWER|ADANI|\bGAS\b|INDANE|BROADBAND|ACT FIBERNET|WATER BOARD|BILLDESK|RECHARGE", "Bills & Utilities"),
    (r"CULTFIT|CULT\.FIT|\bGYM\b|PHARMEASY|PHARMACY|APOLLO|1MG|NETMEDS|HOSPITAL|CLINIC|PRACTO|MEDPLUS|DIAGNOSTIC", "Health"),
    (r"AMAZON|FLIPKART|MYNTRA|AJIO|NYKAA|MEESHO|CROMA|RELIANCE DIGITAL|TATA CLIQ|LIFESTYLE|SHOPPERS STOP|IKEA|UNIQLO|ZARA|H&M", "Shopping"),
    (r"COURSERA|BYJU|UNACADEMY|SCHOOL|COLLEGE|UNIVERSITY|TUITION|\bFEES\b", "Education"),
]
_COMPILED = [(re.compile(p, re.I), c) for p, c in RULES]


class MerchantCategory(BaseModel):
    merchant: str = Field(description="Merchant text exactly as given")
    category: CategoryName = Field(description="One of the allowed categories")


class CategoryAssignments(BaseModel):
    items: list[MerchantCategory]


def rule_category(merchant: str, description: str = "") -> Optional[str]:
    for text in (merchant or "", description or ""):
        for rx, cat in _COMPILED:
            if rx.search(text):
                return cat
    return None


def _llm_batch(llm, batch: list[dict], trace: Optional[list]) -> dict[str, str]:
    from prompts.prompts import CATEGORIZER_PROMPT, prompt_to_trace

    lines = "\n".join(f'merchant: "{b["merchant"]}" ({b["type"]}, note "{b["note"]}")' for b in batch)
    variables = {"merchants": lines}
    if trace is not None:
        trace.append(prompt_to_trace("categorizer", CATEGORIZER_PROMPT, variables))
    chain = CATEGORIZER_PROMPT | llm.with_structured_output(CategoryAssignments)
    result: CategoryAssignments = chain.invoke(variables)
    wanted = {b["merchant"].upper(): b["merchant"] for b in batch}
    out = {}
    for item in result.items:
        key = wanted.get(item.merchant.strip().upper())
        if key and item.category in CATEGORIES:
            out[key] = item.category
    return out


def _note(description: str) -> str:
    parts = [p for p in str(description).split("/") if p.strip()]
    return parts[-1][:30] if len(parts) > 4 else ""


def categorize(df: pd.DataFrame, llm=None, cache: Optional[dict] = None,
               batch_size: int = 25, prompt_trace: Optional[list] = None) -> tuple[pd.DataFrame, dict]:
    """Fill `category` and `category_source`. Returns (df, stats).

    cache: merchant -> {"category", "source"}; updated in place (user corrections live here too).
    """
    df = df.copy()
    cache = cache if cache is not None else {}
    stats = {"rule": 0, "llm": 0, "user": 0, "fallback": 0, "llm_calls": 0, "llm_error": None}

    cats, sources, unknown = [], [], {}
    for _, r in df.iterrows():
        m = r["merchant"]
        if m in cache:
            cats.append(cache[m]["category"])
            sources.append(cache[m]["source"])
            continue
        c = rule_category(m, r["description"])
        if c:
            cats.append(c)
            sources.append("rule")
        else:
            cats.append(None)
            sources.append(None)
            unknown.setdefault(m, {"merchant": m, "type": r["type"], "note": _note(r["description"])})
    df["category"], df["category_source"] = cats, sources

    llm_map: dict[str, str] = {}
    if unknown and llm is not None:
        items = list(unknown.values())
        for i in range(0, len(items), batch_size):
            try:
                llm_map.update(_llm_batch(llm, items[i:i + batch_size], prompt_trace))
                stats["llm_calls"] += 1
            except Exception as exc:  # rate limit, parse failure: fall back, never crash
                stats["llm_error"] = str(exc)[:300]
                break
    for m in unknown:
        if m in llm_map:
            cache[m] = {"category": llm_map[m], "source": "llm"}
        else:
            # credits from unknown parties are most likely transfers
            ttype = unknown[m]["type"]
            cache.setdefault(m, {"category": "Transfers" if ttype == "credit" else "Other",
                                 "source": "fallback"})
    mask = df["category"].isna()
    df.loc[mask, "category"] = df.loc[mask, "merchant"].map(lambda m: cache[m]["category"])
    df.loc[mask, "category_source"] = df.loc[mask, "merchant"].map(lambda m: cache[m]["source"])

    for src, n in df["category_source"].value_counts().items():
        stats[src] = int(n)
    return df, stats


def apply_correction(df: pd.DataFrame, cache: dict, merchant: str, category: str) -> pd.DataFrame:
    """User correction from the Dashboard: update every row for that merchant and the cache."""
    if category not in CATEGORIES:
        raise ValueError(f"Unknown category {category}")
    df = df.copy()
    cache[merchant] = {"category": category, "source": "user"}
    sel = df["merchant"] == merchant
    df.loc[sel, "category"] = category
    df.loc[sel, "category_source"] = "user"
    return df
