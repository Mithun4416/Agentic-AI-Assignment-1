"""ChromaDB collections: per-upload `transactions_<hash>` and a persisted `budget_guidance`."""
from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

import pandas as pd

from config import CHROMA_DIR, KB_DIR, get_embeddings

GUIDANCE_COLLECTION = "budget_guidance"
_SPACE = {"hnsw:space": "cosine"}


@lru_cache(maxsize=1)
def get_client():
    import chromadb
    from chromadb.config import Settings

    CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(CHROMA_DIR), settings=Settings(anonymized_telemetry=False))


def _store(name: str):
    from langchain_chroma import Chroma

    return Chroma(collection_name=name, embedding_function=get_embeddings(), client=get_client(),
                  collection_metadata=_SPACE)


def _drop(name: str) -> None:
    try:
        get_client().delete_collection(name)
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# Transactions
# --------------------------------------------------------------------------- #
def transactions_collection_name(file_hash: str) -> str:
    return f"transactions_{file_hash[:16]}"


# Plain-language words per category, appended to each document so fuzzy queries
# ("that gym place") match merchant names the embedding model has never seen ("CULTFIT").
CATEGORY_HINTS = {
    "Food & Dining": "food restaurant cafe coffee meal delivery dining",
    "Groceries": "groceries supermarket vegetables household essentials",
    "Transport": "transport cab taxi ride metro travel commute fuel",
    "Shopping": "shopping purchase online store electronics clothes gadgets",
    "Bills & Utilities": "bill utility electricity phone mobile internet recharge",
    "Rent": "rent house flat landlord housing",
    "Entertainment & Subscriptions": "entertainment subscription streaming music movies",
    "Health": "health fitness gym workout medical pharmacy medicines doctor",
    "Education": "education course learning class fees books",
    "Transfers": "transfer person friend family split payment",
    "Cash Withdrawal": "cash withdrawal atm",
    "Investments": "investment sip mutual fund stocks savings",
    "Income": "income salary credit received",
    "Other": "",
}


def txn_document(r) -> str:
    return (f"{r['date']:%Y-%m-%d} {r['merchant']} {r['description']} {r['type']} "
            f"₹{r['amount']:,.2f} [{r['category']}: {CATEGORY_HINTS.get(r['category'], '')}]")


def txn_metadata(r) -> dict:
    return {
        "txn_id": r["txn_id"],
        "date": f"{r['date']:%Y-%m-%d}",
        "date_ts": int(pd.Timestamp(r["date"]).timestamp()),
        "month": f"{r['date']:%Y-%m}",
        "amount": float(r["amount"]),
        "category": r["category"],
        "type": r["type"],
        "merchant": r["merchant"],
    }


def build_transactions_store(df: pd.DataFrame, file_hash: str):
    """Build (or reuse) the collection for this upload.

    Named by the file hash plus a fingerprint of the categories, so Streamlit reruns never insert
    duplicates and a re-categorised statement never reuses documents with stale categories.
    """
    cats = hashlib.sha1("|".join(df["txn_id"] + ":" + df["category"].astype(str)).encode()).hexdigest()[:6]
    name = transactions_collection_name(file_hash)[:-6] + cats
    store = _store(name)
    if store._collection.count() != len(df):
        _drop(name)
        store = _store(name)
        rows = df.to_dict("records")
        store.add_texts(texts=[txn_document(r) for r in rows], metadatas=[txn_metadata(r) for r in rows],
                        ids=[r["txn_id"] for r in rows])
    return store


def upsert_transactions(store, df_rows: pd.DataFrame) -> None:
    """Re-embed rows after a user category correction."""
    rows = df_rows.to_dict("records")
    if rows:
        store.add_texts(texts=[txn_document(r) for r in rows], metadatas=[txn_metadata(r) for r in rows],
                        ids=[r["txn_id"] for r in rows])


def drop_transactions_store(store) -> None:
    """Delete this session's transactions collection (used by Reset session)."""
    if store is not None:
        _drop(store._collection.name)


# --------------------------------------------------------------------------- #
# Budget guidance knowledge base
# --------------------------------------------------------------------------- #
def _kb_files(kb_dir: Path = KB_DIR) -> list[Path]:
    return sorted(kb_dir.glob("*.md"))


def kb_fingerprint(kb_dir: Path = KB_DIR) -> str:
    h = hashlib.sha1()
    for p in _kb_files(kb_dir):
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest()[:16]


def chunk_markdown(path: Path) -> list[tuple[str, str]]:
    """Split a note into (topic, text) chunks on '## ' headings, each prefixed with the note title."""
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    title = lines[0].lstrip("# ").strip() if lines and lines[0].startswith("#") else path.stem
    chunks, current, heading = [], [], "Overview"
    for line in lines[1:]:
        if line.startswith("## "):
            if "".join(current).strip():
                chunks.append((heading, "\n".join(current).strip()))
            heading, current = line[3:].strip(), []
        else:
            current.append(line)
    if "".join(current).strip():
        chunks.append((heading, "\n".join(current).strip()))
    return [(title, f"{title} — {h}\n{body}") for h, body in chunks]


def build_guidance_store(force: bool = False):
    """Embed knowledge_base/*.md once; rebuild only when the notes change."""
    fp = kb_fingerprint()
    store = _store(GUIDANCE_COLLECTION)
    existing = store._collection.get(limit=1, include=["metadatas"])
    current_fp = existing["metadatas"][0].get("kb_hash") if existing["metadatas"] else None
    if force or current_fp != fp:
        _drop(GUIDANCE_COLLECTION)
        store = _store(GUIDANCE_COLLECTION)
        texts, metas, ids = [], [], []
        for p in _kb_files():
            for i, (topic, chunk) in enumerate(chunk_markdown(p)):
                texts.append(chunk)
                metas.append({"source": p.name, "topic": topic, "kb_hash": fp})
                ids.append(f"{p.stem}:{i}")
        if texts:
            store.add_texts(texts=texts, metadatas=metas, ids=ids)
    return store
