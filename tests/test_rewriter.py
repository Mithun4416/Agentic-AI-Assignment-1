import pytest

from core.rewriter import rewrite_adds_period


@pytest.mark.parametrize("original,rewritten,adds", [
    # invented period on a new, unrelated question -> rejected
    ("What was that big shopping payment?", "What was that big shopping payment in the latest month?", True),
    ("What was that big shopping payment?", "What was the large shopping payment in October 2026?", True),
    ("Show my Uber rides", "Show my Uber rides in 2026-09", True),
    # no period added -> fine
    ("What was that big shopping payment?", "What was the amount of the large shopping payment in my statement?", False),
    # the user named a period -> fine
    ("How much on food last month?", "How much did I spend on Food & Dining in September 2026?", False),
    ("Food in October?", "How much did I spend on food in October 2026?", False),
    # follow-ups may inherit the earlier period
    ("And transport?", "How much did I spend on transport in October 2026?", False),
    ("What about rent?", "How much was rent in October 2026?", False),
])
def test_rewrite_adds_period(original, rewritten, adds):
    assert rewrite_adds_period(original, rewritten) is adds
