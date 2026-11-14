"""Shared ReAct-style tool-calling loop with trace capture.

Thought -> tool call(s) -> observation -> repeat, capped at MAX_TOOL_STEPS.
Every prompt, tool call, tool result and retrieval is written into the trace dict
so the UI can show exactly what the agent did.
"""
from __future__ import annotations

import json
import time
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from config import MAX_TOOL_STEPS
from prompts.prompts import prompt_to_trace

RETRIEVAL_TOOLS = {"search_transactions", "search_budget_guidance"}


def new_trace(question: str) -> dict:
    return {
        "question": question,
        "rewritten": None,
        "intent": None,
        "reason": None,
        "router_source": None,
        "handler": None,
        "prompts": [],
        "steps": [],
        "tool_calls": [],
        "retrieval": [],
        "reflection": None,
        "matched_txn_ids": [],
        "guardrails": [],
        "errors": [],
    }


def text_of(content: Any) -> str:
    """Normalise model content (str or list of parts) to plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for p in content:
            if isinstance(p, str):
                parts.append(p)
            elif isinstance(p, dict) and p.get("type") == "text":
                parts.append(p.get("text", ""))
        return "".join(parts)
    return str(content or "")


def _record_retrieval(trace: dict, name: str, args: dict, result: str) -> None:
    try:
        parsed = json.loads(result)
    except json.JSONDecodeError:
        parsed = {"raw": result}
    chunks = parsed.get("matches") or parsed.get("chunks") or []
    trace["retrieval"].append({
        "tool": name,
        "collection": "transactions" if name == "search_transactions" else "budget_guidance",
        "query": args.get("query"),
        "args": args,
        "chunks": chunks,
    })


def _collect_txn_ids(trace: dict, result: str) -> None:
    try:
        parsed = json.loads(result)
    except json.JSONDecodeError:
        return
    for row in (parsed.get("transactions") or []) + (parsed.get("matches") or []):
        tid = row.get("txn_id")
        if tid and tid not in trace["matched_txn_ids"]:
            trace["matched_txn_ids"].append(tid)


def run_tool_loop(llm, prompt, variables: dict, tools: list, trace: dict, name: str,
                  max_steps: int = MAX_TOOL_STEPS) -> str:
    """Run the agent until it answers without calling tools. Returns the final answer text."""
    trace["prompts"].append(prompt_to_trace(name, prompt, variables))
    messages = list(prompt.format_messages(**variables))
    by_name = {t.name: t for t in tools}
    bound = llm.bind_tools(tools)

    for step in range(1, max_steps + 1):
        t0 = time.perf_counter()
        ai: AIMessage = bound.invoke(messages)
        secs = round(time.perf_counter() - t0, 1)
        messages.append(ai)
        thought = text_of(ai.content).strip()
        if not ai.tool_calls:
            trace["steps"].append({"step": step, "thought": None, "final": True, "llm_seconds": secs})
            return thought
        trace["steps"].append({"step": step, "thought": thought or None, "llm_seconds": secs,
                               "actions": [tc["name"] for tc in ai.tool_calls]})
        for tc in ai.tool_calls:
            tname, args = tc["name"], tc.get("args") or {}
            tool_obj = by_name.get(tname)
            if tool_obj is None:
                result = json.dumps({"error": f"Unknown tool '{tname}'. Available: {list(by_name)}"})
            else:
                try:
                    result = tool_obj.invoke(args)
                except Exception as exc:  # validation errors go back to the model, not the user
                    result = json.dumps({"error": f"{type(exc).__name__}: {exc}"})
            result = result if isinstance(result, str) else json.dumps(result, default=str)
            trace["tool_calls"].append({"step": step, "tool": tname, "args": args, "result": result})
            if tname in RETRIEVAL_TOOLS:
                _record_retrieval(trace, tname, args, result)
            if tname in ("find_transactions", "search_transactions"):
                _collect_txn_ids(trace, result)
            messages.append(ToolMessage(content=result, tool_call_id=tc["id"], name=tname))

    # Step cap reached: ask for an answer from what we have, without tools.
    messages.append(HumanMessage(content="Tool budget reached. Answer now using only the tool results above."))
    final = text_of(llm.invoke(messages).content).strip()
    trace["steps"].append({"step": max_steps + 1, "thought": "step cap reached", "final": True})
    return final


def tool_outputs_text(trace: dict) -> str:
    return "\n".join(f"[{c['tool']}] {c['result']}" for c in trace["tool_calls"])
