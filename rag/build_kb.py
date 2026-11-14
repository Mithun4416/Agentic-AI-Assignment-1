"""Embed knowledge_base/*.md into the persisted `budget_guidance` collection.

Run once before the demo (it also downloads the embedding model):
    python -m rag.build_kb
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rag.vectorstore import build_guidance_store  # noqa: E402

if __name__ == "__main__":
    store = build_guidance_store(force="--force" in sys.argv)
    print(f"budget_guidance collection ready with {store._collection.count()} chunks.")
    for doc, score in store.similarity_search_with_score("how do I spend less on food delivery", k=2):
        print(f"  {1 - score:.3f}  {doc.metadata['source']}  {doc.page_content[:70]!r}")
