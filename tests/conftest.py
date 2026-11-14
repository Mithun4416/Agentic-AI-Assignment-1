import os
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agents.parser_agent import parse_statement  # noqa: E402
from data.generate_sample import build_rows, write_csv  # noqa: E402

SAMPLE = ROOT / "data" / "sample_statement.csv"


def pytest_configure(config):
    config.addinivalue_line("markers", "llm: needs a configured LLM (skipped when offline)")
    config.addinivalue_line("markers", "embeddings: needs the local embedding model")


@pytest.fixture(scope="session")
def sample_bytes() -> bytes:
    if not SAMPLE.exists():
        write_csv(SAMPLE)
    return SAMPLE.read_bytes()


@pytest.fixture(scope="session")
def truth_rows():
    return build_rows()


@pytest.fixture(scope="session")
def parsed(sample_bytes):
    df, report = parse_statement(sample_bytes, "sample_statement.csv")
    return df, report


@pytest.fixture(scope="session")
def labelled_df(parsed, truth_rows):
    """Parsed sample with the planted (true) categories, so tool tests do not depend on the categoriser."""
    df = parsed[0].copy()
    assert len(df) == len(truth_rows)
    df["category"] = [r.true_category for r in truth_rows]
    df["category_source"] = "truth"
    return df


@pytest.fixture(scope="session")
def llm():
    from config import try_get_llm

    model, err = try_get_llm()
    if model is None or os.getenv("SKIP_LLM_TESTS"):
        pytest.skip(f"LLM not available: {err}")
    return model
