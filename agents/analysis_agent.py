"""Analysis Agent (spending_analysis) and the Lookup chain (transaction_lookup).

Both use the shared ReAct tool loop. Numbers come only from pandas tools / calculator;
the reflection pass checks the draft against the tool outputs.
"""
from __future__ import annotations

from core.reflection import reflect
from core.tool_loop import run_tool_loop
from prompts.prompts import ANALYSIS_PROMPT, LOOKUP_PROMPT


def run_analysis(x: dict) -> dict:
    ctx, trace = x["ctx"], x["trace"]
    trace["handler"] = "Analysis Agent (pandas tools + calculator)"
    variables = {"question": x["question"], "data_summary": ctx.analytics.data_summary()}
    draft = run_tool_loop(ctx.llm, ANALYSIS_PROMPT, variables, ctx.pandas_tools, trace, "analysis_agent")
    sources = [c["result"] for c in trace["tool_calls"]] + [variables["data_summary"]]
    answer = reflect(ctx.llm, x["question"], draft, sources, trace)
    return {"answer": answer}


def run_lookup(x: dict) -> dict:
    ctx, trace = x["ctx"], x["trace"]
    trace["handler"] = "Lookup chain (find_transactions + search_transactions)"
    tools = [t for t in ctx.pandas_tools if t.name == "find_transactions"]
    if "search_transactions" in ctx.retrieval_tools:
        tools.append(ctx.retrieval_tools["search_transactions"])
    variables = {"question": x["question"], "data_summary": ctx.analytics.data_summary()}
    draft = run_tool_loop(ctx.llm, LOOKUP_PROMPT, variables, tools, trace, "lookup_agent")
    sources = [c["result"] for c in trace["tool_calls"]] + [variables["data_summary"]]
    answer = reflect(ctx.llm, x["question"], draft, sources, trace)
    return {"answer": answer}
