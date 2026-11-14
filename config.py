"""Central configuration: LLM factory, embeddings factory, categories, thresholds."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

# --------------------------------------------------------------------------- #
# Categories and thresholds
# --------------------------------------------------------------------------- #
CATEGORIES: list[str] = [
    "Food & Dining",
    "Groceries",
    "Transport",
    "Shopping",
    "Bills & Utilities",
    "Rent",
    "Entertainment & Subscriptions",
    "Health",
    "Education",
    "Transfers",
    "Cash Withdrawal",
    "Investments",
    "Income",
    "Other",
]

# 50/30/20 buckets used by the Advisor's deterministic flags
NEEDS = {"Groceries", "Transport", "Bills & Utilities", "Rent", "Health", "Education"}
WANTS = {"Food & Dining", "Shopping", "Entertainment & Subscriptions"}
NOT_EXPENSE = {"Investments", "Income"}  # debits here count as saving, not spending

MOM_RISE_THRESHOLD = 0.20  # flag a category if it rises more than 20% month over month
MOM_MIN_BASE = 500.0  # ignore rises from a tiny base
ANOMALY_MULTIPLIER = 3.0  # transaction > 3x the category median
MAX_TOOL_STEPS = 5
REQUEST_TIMEOUT = float(os.getenv("LLM_TIMEOUT_SECONDS", "60"))  # a hung provider must not freeze the app
HISTORY_TURNS = 6

KB_DIR = ROOT / "knowledge_base"
DATA_DIR = ROOT / "data"
CHROMA_DIR = ROOT / os.getenv("CHROMA_DIR", "chroma_db")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")

PROVIDERS = ["groq", "google", "ollama"]


class LLMConfigError(RuntimeError):
    """Raised when the LLM cannot be created; the message is shown in the UI."""


def default_provider() -> str:
    return os.getenv("LLM_PROVIDER", "groq").strip().lower()


def default_model(provider: str | None = None) -> str:
    """Model for a provider: <PROVIDER>_MODEL (e.g. GOOGLE_MODEL), else LLM_MODEL for the default provider."""
    provider = (provider or default_provider()).lower()
    specific = os.getenv(f"{provider.upper()}_MODEL", "").strip()
    if specific:
        return specific
    return os.getenv("LLM_MODEL", "").strip() if provider == default_provider() else ""


@lru_cache(maxsize=8)
def get_llm(provider: str | None = None, model: str | None = None, temperature: float = 0.0):
    """Return a LangChain chat model for the configured provider.

    Model names are read from the environment and never hard-coded, because
    providers rename and retire models often.
    """
    provider = (provider or default_provider()).lower()
    model = model or default_model(provider)
    if not model:
        raise LLMConfigError(
            f"No model set for provider '{provider}'. Set LLM_MODEL (or OLLAMA_MODEL) in .env."
        )

    if provider == "groq":
        if not os.getenv("GROQ_API_KEY"):
            raise LLMConfigError("GROQ_API_KEY is missing from .env.")
        from langchain_groq import ChatGroq

        kwargs = {}
        # Reasoning models (gpt-oss, qwen) spend tokens "thinking"; low effort keeps answers fast and
        # stays inside free-tier tokens-per-minute limits. Tool choice and grounding do the real work.
        if any(tag in model.lower() for tag in ("gpt-oss", "qwen")):
            kwargs["reasoning_effort"] = os.getenv("LLM_REASONING_EFFORT", "low")
        return ChatGroq(model=model, temperature=temperature, max_retries=3, timeout=REQUEST_TIMEOUT, **kwargs)

    if provider == "google":
        if not os.getenv("GOOGLE_API_KEY"):
            raise LLMConfigError("GOOGLE_API_KEY is missing from .env.")
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(model=model, temperature=temperature, max_retries=2, timeout=REQUEST_TIMEOUT)

    if provider == "ollama":
        from langchain_ollama import ChatOllama

        # With OLLAMA_API_KEY this talks to Ollama's cloud; without it, to a local Ollama server.
        api_key = os.getenv("OLLAMA_API_KEY", "").strip()
        base_url = os.getenv("OLLAMA_BASE_URL", "").strip() or (
            "https://ollama.com" if api_key else "http://localhost:11434")
        client_kwargs: dict = {"timeout": REQUEST_TIMEOUT}
        if api_key:
            client_kwargs["headers"] = {"Authorization": f"Bearer {api_key}"}
        kwargs = {"client_kwargs": client_kwargs}
        return ChatOllama(model=model, temperature=temperature, base_url=base_url, **kwargs)

    raise LLMConfigError(f"Unknown LLM_PROVIDER '{provider}'. Use one of {PROVIDERS}.")


def try_get_llm(provider: str | None = None, model: str | None = None):
    """Return (llm, error_message). Never raises."""
    try:
        return get_llm(provider, model), None
    except LLMConfigError as exc:
        return None, str(exc)
    except Exception as exc:  # import errors, bad base URL, etc.
        return None, f"Could not create the LLM client: {exc}"


@lru_cache(maxsize=1)
def get_embeddings():
    """Local sentence-transformers embeddings; statement text never leaves the machine."""
    from langchain_huggingface import HuggingFaceEmbeddings

    return HuggingFaceEmbeddings(
        model_name=EMBEDDING_MODEL,
        encode_kwargs={"normalize_embeddings": True},
    )
