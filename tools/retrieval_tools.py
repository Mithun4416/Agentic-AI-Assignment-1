"""Agentic RAG: retrieval exposed as tools the agent decides whether to call.

Semantic search finds WHICH transactions are relevant; pandas computes HOW MUCH.
Similarity search returns only the top k, so it must never be used for totals.
"""
from __future__ import annotations

import json
from typing import Optional

from langchain_core.tools import tool

from tools.pandas_tools import ToolInputError, resolve_category


def _where(category: Optional[str], month: Optional[str]) -> Optional[dict]:
    clauses = []
    if category:
        clauses.append({"category": category})
    if month:
        clauses.append({"month": month})
    if not clauses:
        return None
    return clauses[0] if len(clauses) == 1 else {"$and": clauses}


def make_retrieval_tools(txn_store=None, guidance_store=None) -> dict:
    """Return {'search_transactions': tool, 'search_budget_guidance': tool} for available stores."""
    tools = {}

    if txn_store is not None:
        @tool
        def search_transactions(query: str, k: int = 5, category: Optional[str] = None,
                                month: Optional[str] = None) -> str:
            """Semantic search over the user's transactions for fuzzy descriptions
            (e.g. 'that gym place', 'big electronics purchase'). Optional filters: category, month (YYYY-MM).
            Returns the top k matches only; never use it to compute totals."""
            try:
                cat = resolve_category(category)
            except ToolInputError as exc:
                return json.dumps({"error": str(exc)})
            k = max(1, min(int(k or 5), 10))
            hits = txn_store.similarity_search_with_score(query, k=k, filter=_where(cat, month))
            if not hits:
                return json.dumps({"message": "No matching transactions.", "matches": []})
            return json.dumps({"matches": [
                {"similarity": round(1 - score, 3), "txn_id": d.metadata.get("txn_id"),
                 "date": d.metadata.get("date"), "merchant": d.metadata.get("merchant"),
                 "amount": d.metadata.get("amount"), "type": d.metadata.get("type"),
                 "category": d.metadata.get("category"), "text": d.page_content}
                for d, score in hits
            ]}, ensure_ascii=False)

        tools["search_transactions"] = search_transactions

    if guidance_store is not None:
        @tool
        def search_budget_guidance(query: str, k: int = 3) -> str:
            """(k is at most 3.) Search the budgeting knowledge base (50/30/20 rule, subscriptions, food delivery, transport,
            impulse purchases, emergency fund, category limits, small UPI payments...). Returns note chunks
            with their source file for citation."""
            k = max(1, min(int(k or 3), 3))  # small k keeps prompts inside free-tier token limits
            hits = guidance_store.similarity_search_with_score(query, k=k)
            return json.dumps({"chunks": [
                {"similarity": round(1 - score, 3), "source": d.metadata.get("source"),
                 "topic": d.metadata.get("topic"), "text": d.page_content}
                for d, score in hits
            ]}, ensure_ascii=False)

        tools["search_budget_guidance"] = search_budget_guidance

    return tools
