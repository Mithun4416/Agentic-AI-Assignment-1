"""Offline end-to-end test of the routed LCEL pipeline using a scripted fake chat model."""
from __future__ import annotations

from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableLambda

from core.assistant import ask, make_context
from core.reflection import ReflectionVerdict
from core.router import Intent


class ScriptedLLM(BaseChatModel):
    """Returns queued AIMessages for plain/tool calls and queued objects for structured output."""
    replies: list = []
    structured: list = []

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs: Any) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=self.replies.pop(0))])

    def bind_tools(self, tools, **kwargs):
        return self

    def with_structured_output(self, schema, **kwargs):
        return RunnableLambda(lambda _: self.structured.pop(0))


def test_analysis_route_end_to_end(labelled_df):
    llm = ScriptedLLM(
        replies=[
            AIMessage(content="I need October food spend.", tool_calls=[{
                "name": "get_total_spend", "args": {"category": "Food & Dining", "start_date": "2026-10"},
                "id": "call_1"}]),
            AIMessage(content="You spent ₹8,400 on Food & Dining in October 2026."),
        ],
        structured=[Intent(intent="spending_analysis", reason="asks for a total")],
    )
    ctx = make_context(llm, labelled_df)
    answer, trace = ask(ctx, "How much did I spend on food in October?", [])
    assert "8,400" in answer
    assert trace["intent"] == "spending_analysis"
    assert trace["handler"].startswith("Analysis Agent")
    assert trace["tool_calls"][0]["tool"] == "get_total_spend"
    assert '"total_spend": 8400.0' in trace["tool_calls"][0]["result"]
    assert trace["retrieval_used"] is False
    assert trace["reflection"]["verdict"] == "approved"
    names = [p["name"] for p in trace["prompts"]]
    assert names == ["intent_router", "analysis_agent"]
    assert "Never calculate numbers yourself" in trace["prompts"][1]["system"]


def test_reflection_revises_invented_number(labelled_df):
    llm = ScriptedLLM(
        replies=[
            AIMessage(content="", tool_calls=[{"name": "get_total_spend",
                                               "args": {"category": "food", "start_date": "2026-10"}, "id": "c1"}]),
            AIMessage(content="You spent ₹8,400, about ₹9,999 more than usual."),
        ],
        structured=[
            Intent(intent="spending_analysis", reason="total"),
            ReflectionVerdict(approved=False, revised_answer="You spent ₹8,400 on food in October 2026.",
                              notes="9,999 is not supported"),
        ],
    )
    answer, trace = ask(make_context(llm, labelled_df), "Food spend in October?", [])
    assert answer == "You spent ₹8,400 on food in October 2026."
    assert trace["reflection"]["code_check_flags"] == ["9,999"]
    assert trace["reflection"]["verdict"] == "revised"


def test_router_failure_falls_back_to_keywords(labelled_df):
    class Broken(ScriptedLLM):
        def with_structured_output(self, schema, **kwargs):
            def boom(_):
                raise ValueError("bad json")
            return RunnableLambda(boom)

    llm = Broken(replies=[AIMessage(content="Happy to help with your statement!")])
    answer, trace = ask(make_context(llm, labelled_df), "Thanks!", [])
    assert trace["router_source"] == "keyword fallback"
    assert trace["intent"] == "other"
    assert answer == "Happy to help with your statement!"


def test_follow_up_is_rewritten(labelled_df):
    llm = ScriptedLLM(
        replies=[
            AIMessage(content="What subscriptions do I pay for and how much per month?"),  # rewriter
            AIMessage(content="", tool_calls=[{"name": "detect_recurring", "args": {}, "id": "c1"}]),
            AIMessage(content="Your subscriptions total ₹2,267 a month."),
        ],
        structured=[Intent(intent="spending_analysis", reason="recurring payments")],
    )
    history = [{"role": "user", "content": "Where am I overspending?"},
               {"role": "assistant", "content": "Food & Dining is up 40%."}]
    answer, trace = ask(make_context(llm, labelled_df), "And what about subscriptions?", history)
    assert trace["rewritten"] == "What subscriptions do I pay for and how much per month?"
    assert trace["prompts"][0]["name"] == "question_rewriter"
    assert "2,267" in answer and trace["reflection"]["verdict"] == "approved"
