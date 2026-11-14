"""Wires the agents into the routed LCEL pipeline and runs one chat turn."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnableLambda

from agents.advisor_agent import run_advisor
from agents.analysis_agent import run_analysis, run_lookup
from core.memory import history_messages
from core.router import build_pipeline
from core.tool_loop import new_trace, text_of
from prompts.prompts import SCOPE_PROMPT, prompt_to_trace
from tools.pandas_tools import Analytics, make_pandas_tools


@dataclass
class AgentContext:
    llm: object
    analytics: Analytics
    pandas_tools: list
    retrieval_tools: dict = field(default_factory=dict)
    budgets: Optional[dict] = None


def make_context(llm, df, retrieval_tools: Optional[dict] = None, budgets: Optional[dict] = None) -> AgentContext:
    an = Analytics(df)
    return AgentContext(llm=llm, analytics=an, pandas_tools=make_pandas_tools(an),
                        retrieval_tools=retrieval_tools or {}, budgets=budgets)


def run_scope(x: dict) -> dict:
    trace, llm = x["trace"], x["ctx"].llm
    trace["handler"] = "Scope reply (no tools, no retrieval)"
    variables = {"question": x["question"]}
    trace["prompts"].append(prompt_to_trace("scope_reply", SCOPE_PROMPT, variables))
    answer = text_of((SCOPE_PROMPT | llm | StrOutputParser()).invoke(variables))
    trace["reflection"] = {"verdict": "skipped (no figures on this route)", "code_check_flags": [],
                           "llm_used": False, "notes": ""}
    return {"answer": answer}


def _safe(fn):
    def wrapped(x):
        try:
            return fn(x)
        except Exception as exc:
            x["trace"]["errors"].append(f"{type(exc).__name__}: {exc}"[:500])
            msg = str(exc).lower()
            if "per day" in msg or "perday" in msg or "daily" in msg:
                hint = ("This model's daily free quota is used up. Switch to another provider or model on the "
                        "Overview page, or try again tomorrow.")
            elif "rate" in msg and "limit" in msg or "429" in msg or "resource_exhausted" in msg:
                hint = "The LLM provider's rate limit was hit. Wait a few seconds and try again, or switch provider."
            elif "402" in msg or "usage credits" in msg or "payment" in msg or "billing" in msg:
                hint = ("This provider account needs credits or a paid plan before it will answer. "
                        "Switch to another provider on the Overview page.")
            elif "unauthorized" in msg or "401" in msg or "api key" in msg:
                hint = "The LLM provider rejected the API key. Check the key in .env."
            elif "timed out" in msg or "timeout" in msg:
                hint = "The model took too long to answer. Try again, or switch to a faster model."
            else:
                hint = "Something went wrong while answering. Try rephrasing, or check the trace for details."
            return {"answer": f"⚠️ {hint}"}
    return RunnableLambda(wrapped)


HANDLERS = {
    "transaction_lookup": _safe(run_lookup),
    "spending_analysis": _safe(run_analysis),
    "savings_guidance": _safe(run_advisor),
    "other": _safe(run_scope),
}


def ask(ctx: AgentContext, question: str, chat_messages: list[dict], context: Optional[dict] = None) -> tuple[str, dict]:
    """Run one turn. Returns (answer, trace)."""
    trace = new_trace(question)
    pipeline = build_pipeline(ctx.llm, HANDLERS)
    state = {
        "question": question,
        "history": history_messages(chat_messages),
        "context": context or {},
        "data_summary": ctx.analytics.data_summary(),
        "ctx": ctx,
        "trace": trace,
    }
    try:
        result = pipeline.invoke(state)
        answer = result["answer"]
    except Exception as exc:
        trace["errors"].append(f"{type(exc).__name__}: {exc}"[:500])
        answer = "⚠️ The assistant could not answer this question. Check the trace for details."
    trace["retrieval_used"] = bool(trace["retrieval"])
    return answer, trace
