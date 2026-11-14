import re

from agents.advisor_agent import compute_flags, flags_text, validate_citations
from tools.pandas_tools import Analytics

RETRIEVED = ["food_delivery.md", "category_limits.md"]


def test_valid_citation_kept():
    text = "Cook at home. *(source: food_delivery.md)*\nNext."
    out, removed = validate_citations(text, RETRIEVED)
    assert "(source: food_delivery.md)" in out and removed == []


def test_invented_citation_removed():
    text = "Trim subscriptions. *(source: subscriptions.md)*\nSet limits (source: category_limits.md)."
    out, removed = validate_citations(text, RETRIEVED)
    assert "subscriptions.md" not in out
    assert "(source: category_limits.md)" in out
    assert removed == ["subscriptions.md"]


def test_mixed_citation_keeps_only_retrieved():
    out, removed = validate_citations("Do X (sources: food_delivery.md, made_up.md).", RETRIEVED)
    assert "(source: food_delivery.md)" in out and "made_up" not in out and removed == ["made_up.md"]


def test_model_sources_line_removed():
    out, _ = validate_citations("Advice here.\n\n*Sources: food_delivery.md, category_limits.md*", RETRIEVED)
    assert out == "Advice here."


def test_flags_on_sample(labelled_df):
    flags = compute_flags(Analytics(labelled_df))
    kinds = [f["kind"] for f in flags]
    assert "anomaly" in kinds and "subscriptions" in kinds and "mom_rise" in kinds
    text = flags_text(flags)
    assert "whole statement (3 months" in text and "₹85,000 per month" in text
    assert re.search(r"wants \d+\.\d% \(guideline ≤30%, within guideline\)", text)
    assert re.search(r"savings rate \d+\.\d% \(guideline ≥20%, within guideline\)", text)
    assert "Food & Dining up 40.0%" in text


def test_budget_flag(labelled_df):
    flags = compute_flags(Analytics(labelled_df), budgets={"Food & Dining": 7000})
    b = [f for f in flags if f["kind"] == "budget"]
    assert len(b) == 1 and "₹8,400" in b[0]["detail"] and "₹1,400" in b[0]["detail"]
