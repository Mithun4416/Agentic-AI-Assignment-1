import csv
import time
from pathlib import Path

import pytest

from core.router import keyword_intent, make_router_chain

EVAL = [(r["question"], r["intent"]) for r in csv.DictReader((Path(__file__).parent / "router_eval.csv").open(encoding="utf-8"))]


def test_eval_set_has_30_labelled_questions():
    assert len(EVAL) == 30
    assert {i for _, i in EVAL} == {"transaction_lookup", "spending_analysis", "savings_guidance", "other"}


def test_keyword_fallback_is_a_reasonable_backup():
    correct = sum(keyword_intent(q).intent == i for q, i in EVAL)
    assert correct >= 20, f"keyword fallback got {correct}/30"


def _invoke_patiently(chain, inputs, attempts: int = 4):
    """Free tiers rate-limit a 30-question burst per minute; wait it out so this measures accuracy, not quota."""
    for i in range(attempts):
        try:
            return chain.invoke(inputs)
        except Exception as exc:
            msg = str(exc).lower()
            if "per day" in msg or "perday" in msg:
                pytest.skip("the model's daily free-tier quota is used up; rerun tomorrow or with another provider")
            if i == attempts - 1 or not any(s in msg for s in ("429", "rate", "resource_exhausted", "quota")):
                raise
            time.sleep(20 * (i + 1))


@pytest.mark.llm
def test_llm_router_accuracy(llm):
    chain = make_router_chain(llm)
    wrong = []
    for q, expected in EVAL:
        got = _invoke_patiently(chain, {"question": q}).intent
        if got != expected:
            wrong.append((q, expected, got))
    assert len(EVAL) - len(wrong) >= 27, f"misrouted: {wrong}"
