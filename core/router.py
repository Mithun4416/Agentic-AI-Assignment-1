"""Intent detection and routing, built with LCEL.

    router_chain = ROUTER_PROMPT | llm.with_structured_output(Intent)

    full_chain = (
        RunnablePassthrough.assign(question=rewriter)          # standalone question
        | RunnablePassthrough.assign(routing=router)            # Intent(intent, reason)
        | RunnablePassthrough.assign(intent=lambda x: x["routing"].intent)
        | RunnableBranch(... one handler per intent ..., fallback)
    )

If structured output fails to parse, a keyword heuristic decides so the app never
crashes mid-demo.
"""
from __future__ import annotations

import re
from typing import Literal

from langchain_core.runnables import RunnableBranch, RunnableLambda, RunnablePassthrough
from pydantic import BaseModel, Field

from core.rewriter import rewrite_question
from prompts.prompts import ROUTER_PROMPT, prompt_to_trace

INTENTS = ["transaction_lookup", "spending_analysis", "savings_guidance", "other"]


class Intent(BaseModel):
    intent: Literal["transaction_lookup", "spending_analysis", "savings_guidance", "other"]
    reason: str = Field(description="One short sentence explaining the choice")


_SAVINGS = re.compile(r"\b(save|saving|savings tips?|cut|reduce|lower|overspend\w*|budget\w*|advice|advise|"
                      r"should i|too much|afford|tips?|spend less|wasting|recommend\w*)\b", re.I)
_ANALYSIS = re.compile(r"\b(how much|total|compare|comparison|trend|average|avg|breakdown|top \d*|most|"
                       r"percent\w*|share|savings rate|income|expenses?|net|recurring|subscriptions?|"
                       r"per month|monthly|biggest categor\w*|month over month|increase|rise|change)\b", re.I)
_LOOKUP = re.compile(r"\b(what was that|which|when did|show me|find|payment to|paid to|list|"
                     r"transactions?|did i pay|that payment|that charge|details of|look ?up)\b", re.I)
_FINANCE = re.compile(r"\b(spen[dt]|spending|paid|pay|payment|money|rupees|₹|rs|merchant|category|"
                      r"food|rent|shopping|transport|uber|swiggy|zomato|bill|salary)\b", re.I)


def keyword_intent(question: str) -> Intent:
    q = question or ""
    if _SAVINGS.search(q) and not re.search(r"savings rate", q, re.I):
        return Intent(intent="savings_guidance", reason="Keyword fallback: asks for advice or ways to save.")
    if _ANALYSIS.search(q):
        return Intent(intent="spending_analysis", reason="Keyword fallback: asks for totals, trends or comparisons.")
    if _LOOKUP.search(q):
        return Intent(intent="transaction_lookup", reason="Keyword fallback: asks about specific transactions.")
    if _FINANCE.search(q):
        return Intent(intent="spending_analysis", reason="Keyword fallback: mentions spending.")
    return Intent(intent="other", reason="Keyword fallback: not about the statement.")


def make_router_chain(llm):
    """prompt -> model with structured output (Pydantic Intent)."""
    return ROUTER_PROMPT | llm.with_structured_output(Intent)


def route(llm, x: dict) -> Intent:
    trace = x["trace"]
    variables = {"question": x["question"]}
    trace["prompts"].append(prompt_to_trace("intent_router", ROUTER_PROMPT, variables))
    try:
        result = make_router_chain(llm).invoke(variables)
        if not isinstance(result, Intent):
            raise ValueError("router returned no structured output")
        trace["router_source"] = "llm (structured output)"
    except Exception as exc:
        trace["errors"].append(f"router structured output failed, keyword fallback used: {exc}"[:300])
        result = keyword_intent(x["question"])
        trace["router_source"] = "keyword fallback"
    trace["intent"], trace["reason"] = result.intent, result.reason
    return result


def build_pipeline(llm, handlers: dict):
    """handlers: intent -> Runnable taking the state dict and returning {'answer': ...}."""
    branch = RunnableBranch(
        (lambda x: x["intent"] == "transaction_lookup", handlers["transaction_lookup"]),
        (lambda x: x["intent"] == "spending_analysis", handlers["spending_analysis"]),
        (lambda x: x["intent"] == "savings_guidance", handlers["savings_guidance"]),
        handlers["other"],
    )
    return (
        RunnablePassthrough.assign(question=RunnableLambda(lambda x: rewrite_question(llm, x)))
        | RunnablePassthrough.assign(routing=RunnableLambda(lambda x: route(llm, x)))
        | RunnablePassthrough.assign(intent=lambda x: x["routing"].intent)
        | branch
    )
