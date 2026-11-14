"""Vector DB and grounding checks. Needs the local embedding model (downloaded once)."""
import json

import pytest

from core.reflection import ungrounded_numbers

# ------------------------------------------------------------- grounding (offline)


def test_grounding_check_passes_supported_numbers():
    tool = json.dumps({"total_a": 6000.0, "total_b": 8400.0, "change": 2400.0, "change_pct": 40.0})
    draft = "Food rose from ₹6,000 in September to ₹8,400 in October 2026, up ₹2,400 (40%)."
    assert ungrounded_numbers(draft, [tool]) == []


def test_grounding_check_flags_invented_numbers():
    tool = json.dumps({"total": 6000.0})
    draft = "You spent ₹6,000, which is ₹1,234 more than average."
    assert ungrounded_numbers(draft, [tool]) == ["1,234"]


# ------------------------------------------------------------- vector store
@pytest.fixture(scope="module")
def stores(labelled_df, tmp_path_factory):
    pytest.importorskip("langchain_chroma")
    import config

    config.CHROMA_DIR = tmp_path_factory.mktemp("chroma")
    from rag import vectorstore

    vectorstore.CHROMA_DIR = config.CHROMA_DIR
    vectorstore.get_client.cache_clear()
    try:
        txn = vectorstore.build_transactions_store(labelled_df, "testhash0123456789")
        kb = vectorstore.build_guidance_store(force=True)
    except Exception as exc:  # no network for first model download
        pytest.skip(f"embedding model unavailable: {exc}")
    return txn, kb


@pytest.mark.embeddings
def test_collections_built_once(stores, labelled_df):
    from rag import vectorstore

    txn, kb = stores
    assert txn._collection.count() == len(labelled_df)
    again = vectorstore.build_transactions_store(labelled_df, "testhash0123456789")
    assert again._collection.count() == len(labelled_df)  # no duplicates on rerun
    assert again._collection.name == txn._collection.name
    recat = labelled_df.copy()
    recat.loc[0, "category"] = "Other"
    assert vectorstore.build_transactions_store(recat, "testhash0123456789")._collection.name != txn._collection.name
    assert kb._collection.count() >= 10


@pytest.mark.embeddings
@pytest.mark.parametrize("query,expected_merchant", [
    ("that payment to the gym place", "CULTFIT"),
    ("big electronics purchase", "CROMA ELECTRONICS"),
    ("music streaming subscription", "SPOTIFY"),
    ("house rent to landlord", "RAMESH KUMAR RENT"),
    ("metro ride", "BMRCL METRO"),
])
def test_transaction_search(stores, query, expected_merchant):
    from tools.retrieval_tools import make_retrieval_tools

    t = make_retrieval_tools(txn_store=stores[0])["search_transactions"]
    out = json.loads(t.invoke({"query": query, "k": 5}))
    assert expected_merchant in [m["merchant"] for m in out["matches"]]


@pytest.mark.embeddings
@pytest.mark.parametrize("query,source", [
    ("how do I cut food delivery spending", "food_delivery.md"),
    ("too many subscriptions", "subscription_audit.md"),
    ("what share of income should go to needs and wants", "50_30_20_rule.md"),
    ("cab rides are expensive", "transport_costs.md"),
    ("I keep buying gadgets on sale", "impulse_purchases.md"),
])
def test_guidance_search(stores, query, source):
    from tools.retrieval_tools import make_retrieval_tools

    t = make_retrieval_tools(guidance_store=stores[1])["search_budget_guidance"]
    out = json.loads(t.invoke({"query": query, "k": 3}))
    assert source in [c["source"] for c in out["chunks"]]
