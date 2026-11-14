"""Follow-up question rewriter: turns 'and last month?' into a standalone question."""
from __future__ import annotations

import re

from langchain_core.output_parsers import StrOutputParser

from core.memory import format_context
from core.tool_loop import text_of
from prompts.prompts import REWRITER_PROMPT, prompt_to_trace


_MONTHS = ("january|february|march|april|may|june|july|august|september|october|november|december|"
           "jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec")
_PERIOD = re.compile(
    rf"\b({_MONTHS})\b|\b20\d\d(-\d\d)?\b|\b(latest|previous|last|this|current|prior|same|next)\s+(month|week|year)\b"
    r"|\bmonth before\b|\bthen\b|\bthat (month|week|period)\b",
    re.I,
)
# Follow-ups that legitimately inherit the previous question's period ("and transport?", "what about rent?")
_FOLLOW_UP = re.compile(r"^\s*(and|also|what about|how about|same for|and what about)\b", re.I)


def _periods(text: str) -> set[str]:
    return {m.group(0).lower() for m in _PERIOD.finditer(text or "")}


def rewrite_adds_period(original: str, rewritten: str) -> bool:
    """True when the rewrite invents a time period the user did not mention or clearly refer back to."""
    if _FOLLOW_UP.search(original) or _periods(original):
        return False
    return bool(_periods(rewritten))


def make_rewriter_chain(llm):
    """LCEL: prompt -> model -> string parser."""
    return REWRITER_PROMPT | llm | StrOutputParser()


def rewrite_question(llm, x: dict) -> str:
    """x has question, history, context, data_summary, trace. Skips the LLM when there is no history."""
    question, history, trace = x["question"], x.get("history") or [], x["trace"]
    if not history:
        trace["rewritten"] = question
        return question
    variables = {"question": question, "history": history, "context": format_context(x.get("context")),
                 "data_summary": x.get("data_summary", "")}
    trace["prompts"].append(prompt_to_trace("question_rewriter", REWRITER_PROMPT, variables))
    try:
        out = text_of(make_rewriter_chain(llm).invoke(variables)).strip().strip('"')
    except Exception as exc:
        trace["errors"].append(f"rewriter failed, using original question: {exc}"[:300])
        out = question
    out = out or question
    if out != question and rewrite_adds_period(question, out):
        # Guardrail: weaker models carry a month over from earlier turns into an unrelated question,
        # which silently narrows the search. Keep the user's own wording instead.
        trace.setdefault("guardrails", []).append(
            f"Rewrite rejected: it added a time period the user did not mention (“{out}”)."[:300])
        out = question
    trace["rewritten"] = out
    return out
