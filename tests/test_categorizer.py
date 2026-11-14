import pytest

from agents.categorizer_agent import apply_correction, categorize, rule_category


def _accuracy(df, truth_rows):
    return sum(c == t.true_category for c, t in zip(df["category"], truth_rows)) / len(truth_rows)


def test_rules_only_accuracy(parsed, truth_rows):
    df, stats = categorize(parsed[0], llm=None)
    acc = _accuracy(df, truth_rows)
    assert acc >= 0.85, f"rules-only accuracy {acc:.2%}"
    assert stats["rule"] >= 0.85 * len(df)
    # every rule-categorised row must be right
    rule_rows = [(c, t) for c, s, t in zip(df["category"], df["category_source"], truth_rows) if s == "rule"]
    assert all(c == t.true_category for c, t in rule_rows)


@pytest.mark.parametrize("merchant,desc,cat", [
    ("SWIGGY", "", "Food & Dining"), ("BMRCL METRO", "", "Transport"), ("ATM", "", "Cash Withdrawal"),
    ("ACME TECHNOLOGIES", "NEFT CR-ACME-SALARY AUG", "Income"), ("CROMA ELECTRONICS", "", "Shopping"),
    ("CULTFIT", "", "Health"), ("RAMESH KUMAR RENT", "", "Rent"), ("PRIYA SHARMA", "Split", None),
])
def test_rule_category(merchant, desc, cat):
    assert rule_category(merchant, desc) == cat


def test_cache_and_correction(parsed):
    cache = {}
    df, _ = categorize(parsed[0], llm=None, cache=cache)
    df = apply_correction(df, cache, "PRIYA SHARMA", "Transfers")
    assert set(df.loc[df["merchant"] == "PRIYA SHARMA", "category"]) == {"Transfers"}
    df2, stats = categorize(parsed[0], llm=None, cache=cache)
    assert set(df2.loc[df2["merchant"] == "PRIYA SHARMA", "category_source"]) == {"user"}


@pytest.mark.llm
def test_llm_accuracy_at_least_90pct(parsed, truth_rows, llm):
    df, stats = categorize(parsed[0], llm=llm)
    assert stats["llm_error"] is None
    assert stats["llm"] > 0
    acc = _accuracy(df, truth_rows)
    assert acc >= 0.90, f"accuracy {acc:.2%}"
