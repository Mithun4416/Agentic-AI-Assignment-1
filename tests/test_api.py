"""Offline tests for the FastAPI backend that serves the HTML/CSS/JS frontend (no LLM, no embeddings)."""
import pytest
from fastapi.testclient import TestClient

import server


@pytest.fixture()
def client(monkeypatch):
    # No model and no vector store: the dashboard must still work on rules alone.
    monkeypatch.setattr(server, "try_get_llm", lambda *a, **k: (None, "no model in tests"))
    import rag.vectorstore as vs

    def no_store(*a, **k):
        raise RuntimeError("embeddings disabled in tests")

    monkeypatch.setattr(vs, "build_transactions_store", no_store)
    server._sessions.clear()
    return TestClient(server.app)


def test_index_and_static_assets(client):
    r = client.get("/")
    assert r.status_code == 200 and "Statement-to-Insights" in r.text
    assert client.get("/static/styles.css").status_code == 200
    assert client.get("/static/app.js").status_code == 200


def test_state_before_statement(client):
    s = client.get("/api/state").json()
    assert s["statement"] is None and s["llm"]["ok"] is False
    assert "Food & Dining" in s["categories"] and "Income" not in s["spend_categories"]
    assert client.get("/api/dashboard").status_code == 409
    assert client.post("/api/chat", json={"question": "hi"}).status_code == 409


def test_sample_flow(client):
    s = client.post("/api/statement/sample").json()
    assert s["statement"]["rows"] == 135
    assert s["statement"]["months"] == ["2026-08", "2026-09", "2026-10"]
    assert "embeddings disabled" in s["statement"]["store_error"]  # degraded gracefully

    d = client.get("/api/dashboard").json()
    assert d["metrics"]["income"] == 255000.0
    food = next(c for c in d["breakdown"]["categories"] if c["category"] == "Food & Dining")
    assert food["amount"] > 0
    assert [t["month"] for t in d["trend"]] == ["2026-08", "2026-09", "2026-10"]
    assert d["trend"][2]["categories"]["Food & Dining"] == 8400.0
    assert any(f["kind"] == "anomaly" for f in d["flags"])

    oct_ = client.get("/api/dashboard", params={"period": "2026-10"}).json()
    assert oct_["period"] == "2026-10" and oct_["metrics"]["income"] == 85000.0

    t = client.get("/api/transactions").json()["transactions"]
    assert len(t) == 135 and "@" not in " ".join(x["description"] for x in t)


def test_category_correction_and_budgets(client):
    client.post("/api/statement/sample")
    r = client.post("/api/category", json={"merchant": "PRIYA SHARMA", "category": "Transfers"}).json()
    assert r["ok"] and r["updated"] == 2 and r["cat_stats"]["user"] == 2
    rows = [x for x in client.get("/api/transactions").json()["transactions"] if x["merchant"] == "PRIYA SHARMA"]
    assert {x["category"] for x in rows} == {"Transfers"} and {x["source"] for x in rows} == {"user"}
    assert client.post("/api/category", json={"merchant": "PRIYA SHARMA", "category": "Nope"}).status_code == 400
    assert client.post("/api/category", json={"merchant": "NOBODY", "category": "Other"}).status_code == 404

    b = client.post("/api/budgets", json={"budgets": {"Food & Dining": 7000, "Income": 5, "Rent": 0}}).json()
    assert b["budgets"] == {"Food & Dining": 7000.0}
    flags = client.get("/api/dashboard").json()["flags"]
    assert any(f["kind"] == "budget" and "₹1,400" in f["detail"] for f in flags)


def test_upload_validation_and_parse_error(client):
    assert client.post("/api/statement/upload", files={"file": ("x.exe", b"abc")}).status_code == 415
    bad = client.post("/api/statement/upload", files={"file": ("x.csv", b"Date,Narration,Debit\n01/08/2026,A,1\n")})
    assert bad.status_code == 422 and "valid transactions" in bad.json()["detail"]
    csv = ("Date,Narration,Withdrawal Amt.,Deposit Amt.\n" +
           "".join(f"0{i}/08/2026,UPI/DR/412345678901/SWIGGY/YESB/swiggy@ybl/Order,{100 * i},\n" for i in range(1, 8)))
    ok = client.post("/api/statement/upload", files={"file": ("mine.csv", csv.encode())}).json()
    assert ok["statement"]["file_name"] == "mine.csv" and ok["statement"]["rows"] == 7


def test_chat_needs_model_report_and_reset(client):
    client.post("/api/statement/sample")
    r = client.post("/api/chat", json={"question": "How much on food?"})
    assert r.status_code == 503 and "needs an LLM" in r.json()["detail"]
    rep = client.get("/api/report")
    assert rep.status_code == 200 and rep.text.startswith("# Spending insights") and "attachment" in rep.headers["content-disposition"]
    assert client.post("/api/reset").json()["statement"] is None
    assert client.get("/api/transactions").status_code == 409


def test_sessions_are_isolated(client):
    client.post("/api/statement/sample")
    other = TestClient(server.app)
    assert other.get("/api/state").json()["statement"] is None
