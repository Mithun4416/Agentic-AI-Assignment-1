"""Chat history helpers and a small 'last mentioned' context used by the rewriter."""
from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage

from config import HISTORY_TURNS


def history_messages(messages: list[dict], turns: int = HISTORY_TURNS) -> list:
    """Convert st.session_state.messages (role/content dicts) to LangChain messages, last N turns."""
    out = []
    for m in messages[-turns * 2:]:
        if m["role"] == "user":
            out.append(HumanMessage(content=m["content"]))
        elif m["role"] == "assistant":
            out.append(AIMessage(content=m["content"][:1500]))
    return out


def update_context(context: dict, trace: dict) -> dict:
    """Remember the last category, merchant and period the agents used."""
    ctx = dict(context or {})
    for call in trace.get("tool_calls", []):
        args = call.get("args") or {}
        if args.get("category"):
            ctx["category"] = args["category"]
        if args.get("merchant"):
            ctx["merchant"] = args["merchant"]
        for key in ("start_date", "period_b", "month"):
            if args.get(key):
                ctx["period"] = args[key]
    if trace.get("intent"):
        ctx["last_intent"] = trace["intent"]
    return ctx


def format_context(context: dict) -> str:
    if not context:
        return "none"
    return "; ".join(f"{k}={v}" for k, v in context.items())
