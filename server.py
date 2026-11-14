"""Statement-to-Insights Finance Agent — FastAPI backend + static HTML/CSS/JS frontend.

    python server.py            (or: uvicorn server:app --port 8000)

The browser UI lives in web/. This file only exposes the agents as a small JSON API;
all the agent logic is in agents/, core/, tools/ and rag/.
"""
from __future__ import annotations

import hashlib
import threading
import uuid
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agents.advisor_agent import compute_flags
from agents.categorizer_agent import apply_correction, categorize
from agents.parser_agent import ParseError, parse_statement
from config import (CATEGORIES, DATA_DIR, NOT_EXPENSE, PROVIDERS, ROOT, default_model, default_provider,
                    try_get_llm)
from core.assistant import ask, make_context
from core.memory import update_context
from tools.pandas_tools import Analytics

WEB_DIR = ROOT / "web"
SAMPLE_PATH = DATA_DIR / "sample_statement.csv"
SPEND_CATEGORIES = [c for c in CATEGORIES if c not in NOT_EXPENSE]
MAX_UPLOAD_BYTES = 20 * 1024 * 1024

app = FastAPI(title="Statement-to-Insights Finance Agent")


# --------------------------------------------------------------------------- #
# Per-browser session state (in memory; the DataFrame never goes into a module global)
# --------------------------------------------------------------------------- #
class Session:
    def __init__(self):
        self.lock = threading.Lock()
        self.provider = default_provider() if default_provider() in PROVIDERS else PROVIDERS[0]
        self.model = default_model(self.provider)
        self.clear()

    def clear(self):
        self.df = None
        self.report = None
        self.file_hash = None
        self.file_name = None
        self.messages: list[dict] = []
        self.context: dict = {}
        self.cat_cache: dict = {}
        self.cat_stats: dict = {}
        self.cat_prompts: list = []
        self.budgets: dict = {}
        self.txn_store = None
        self.store_error = None

    def llm(self):
        return try_get_llm(self.provider, self.model)


_sessions: dict[str, Session] = {}
_sessions_lock = threading.Lock()
_guidance = {"store": None, "error": None, "tried": False}
_guidance_lock = threading.Lock()


def get_session(request: Request, response: Response) -> Session:
    sid = request.cookies.get("sid")
    with _sessions_lock:
        if not sid or sid not in _sessions:
            sid = uuid.uuid4().hex
            _sessions[sid] = Session()
            response.set_cookie("sid", sid, httponly=True, samesite="lax")
        return _sessions[sid]


def guidance_store():
    """Build the budget_guidance collection once per process (embeds knowledge_base/ if it changed)."""
    with _guidance_lock:
        if not _guidance["tried"]:
            _guidance["tried"] = True
            try:
                from rag.vectorstore import build_guidance_store

                _guidance["store"] = build_guidance_store()
            except Exception as exc:
                _guidance["error"] = str(exc)[:300]
        return _guidance["store"]


def require_statement(s: Session):
    if s.df is None:
        raise HTTPException(409, "Load a statement first.")


# --------------------------------------------------------------------------- #
# Loading a statement: Parser -> Categorizer -> vector store
# --------------------------------------------------------------------------- #
def load_statement(s: Session, data: bytes, name: str, password: Optional[str] = None):
    llm, _ = s.llm()
    try:
        df, report = parse_statement(data, name, llm=llm, password=password)
    except ParseError as exc:
        raise HTTPException(422, str(exc)) from exc
    prompts: list = []
    df, stats = categorize(df, llm=llm, cache=s.cat_cache, prompt_trace=prompts)
    file_hash = hashlib.sha1(data).hexdigest()[:16]
    s.df, s.report, s.file_hash, s.file_name = df, report, file_hash, name
    s.cat_stats, s.cat_prompts = stats, prompts
    s.messages, s.context, s.txn_store, s.store_error = [], {}, None, None
    try:
        from rag.vectorstore import build_transactions_store

        s.txn_store = build_transactions_store(df, file_hash)
    except Exception as exc:
        s.store_error = f"Vector store unavailable: {exc}"[:300]


def statement_info(s: Session) -> Optional[dict]:
    if s.df is None:
        return None
    an = Analytics(s.df)
    prompts = ([s.report.prompt_trace] if s.report.prompt_trace else []) + s.cat_prompts
    return {
        "file_name": s.file_name,
        "rows": int(len(s.df)),
        "months": an.months,
        "date_min": f"{s.df['date'].min():%Y-%m-%d}",
        "date_max": f"{s.df['date'].max():%Y-%m-%d}",
        "report": s.report.as_dict(),
        "cat_stats": s.cat_stats,
        "prompts": prompts,
        "store_error": s.store_error,
    }


def state_payload(s: Session) -> dict:
    llm, err = s.llm()
    return {
        "llm": {"provider": s.provider, "model": s.model, "ok": llm is not None, "error": err},
        "providers": PROVIDERS,
        "categories": CATEGORIES,
        "spend_categories": SPEND_CATEGORIES,
        "statement": statement_info(s),
        "messages": s.messages,
        "budgets": s.budgets,
    }


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #
class LLMChoice(BaseModel):
    provider: str
    model: str = ""


class CategoryFix(BaseModel):
    merchant: str
    category: str


class Budgets(BaseModel):
    budgets: dict[str, float]


class Question(BaseModel):
    question: str


@app.get("/api/state")
def api_state(request: Request, response: Response):
    return state_payload(get_session(request, response))


@app.post("/api/llm")
def api_llm(choice: LLMChoice, request: Request, response: Response):
    s = get_session(request, response)
    if choice.provider not in PROVIDERS:
        raise HTTPException(400, f"Unknown provider. Use one of {PROVIDERS}.")
    with s.lock:
        s.provider = choice.provider
        s.model = choice.model.strip() or default_model(choice.provider)
    return state_payload(s)


@app.post("/api/statement/sample")
def api_sample(request: Request, response: Response):
    s = get_session(request, response)
    if not SAMPLE_PATH.exists():
        from data.generate_sample import write_csv

        write_csv(SAMPLE_PATH)
    with s.lock:
        load_statement(s, SAMPLE_PATH.read_bytes(), SAMPLE_PATH.name)
    return state_payload(s)


@app.post("/api/statement/upload")
def api_upload(request: Request, response: Response, file: UploadFile = File(...),
               password: str = Form("")):
    s = get_session(request, response)
    name = (file.filename or "statement.csv").replace("\\", "/").split("/")[-1]
    if not name.lower().endswith((".csv", ".pdf")):
        raise HTTPException(415, "Upload a CSV or a text-based PDF statement.")
    data = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "File is larger than 20 MB.")
    with s.lock:
        load_statement(s, data, name, password or None)
    return state_payload(s)


@app.get("/api/dashboard")
def api_dashboard(request: Request, response: Response, period: str = ""):
    s = get_session(request, response)
    require_statement(s)
    an = Analytics(s.df)
    start = period if period in an.months else None
    exp = an._expense(an.df)
    by_month = exp.groupby(["month", "category"])["amount"].sum()
    trend = [{"month": m,
              "total": round(float(exp.loc[exp["month"] == m, "amount"].sum()), 2),
              "categories": {c: round(float(v), 2) for (mm, c), v in by_month.items() if mm == m}}
             for m in an.months]
    return {
        "period": start or "all",
        "months": an.months,
        "metrics": an.income_vs_expense(start, start),
        "breakdown": an.category_breakdown(start, start),
        "trend": trend,
        "flags": compute_flags(an, s.budgets),
        "recurring": an.detect_recurring(),
    }


@app.get("/api/transactions")
def api_transactions(request: Request, response: Response):
    s = get_session(request, response)
    require_statement(s)
    rows = [{"txn_id": r.txn_id, "date": f"{r.date:%Y-%m-%d}", "merchant": r.merchant, "amount": float(r.amount),
             "type": r.type, "category": r.category, "source": r.category_source, "description": r.description}
            for r in s.df.itertuples()]
    return {"transactions": rows}


@app.post("/api/category")
def api_category(fix: CategoryFix, request: Request, response: Response):
    s = get_session(request, response)
    require_statement(s)
    if fix.category not in CATEGORIES:
        raise HTTPException(400, "Unknown category.")
    with s.lock:
        if not (s.df["merchant"] == fix.merchant).any():
            raise HTTPException(404, "Merchant not found.")
        s.df = apply_correction(s.df, s.cat_cache, fix.merchant, fix.category)
        if s.txn_store is not None:
            try:
                from rag.vectorstore import upsert_transactions

                upsert_transactions(s.txn_store, s.df[s.df["merchant"] == fix.merchant])
            except Exception as exc:
                s.store_error = f"Vector store update failed: {exc}"[:300]
        for src in ("rule", "llm", "fallback", "user"):
            s.cat_stats[src] = int((s.df["category_source"] == src).sum())
    return {"ok": True, "updated": int((s.df["merchant"] == fix.merchant).sum()), "cat_stats": s.cat_stats}


@app.post("/api/budgets")
def api_budgets(body: Budgets, request: Request, response: Response):
    s = get_session(request, response)
    with s.lock:
        s.budgets = {c: float(v) for c, v in body.budgets.items() if c in SPEND_CATEGORIES and v and v > 0}
    return {"budgets": s.budgets}


@app.post("/api/chat")
def api_chat(body: Question, request: Request, response: Response):
    s = get_session(request, response)
    require_statement(s)
    question = body.question.strip()
    if not question:
        raise HTTPException(400, "Ask a question.")
    llm, err = s.llm()
    if llm is None:
        raise HTTPException(503, f"Chat needs an LLM. {err}")
    from tools.retrieval_tools import make_retrieval_tools

    with s.lock:
        tools = make_retrieval_tools(txn_store=s.txn_store, guidance_store=guidance_store())
        ctx = make_context(llm, s.df, retrieval_tools=tools, budgets=s.budgets)
        answer, trace = ask(ctx, question, s.messages, s.context)
        if trace.get("matched_txn_ids") and trace.get("intent") == "transaction_lookup":
            hit = s.df[s.df["txn_id"].isin(trace["matched_txn_ids"])]
            trace["matched_rows"] = [
                {"date": f"{r.date:%Y-%m-%d}", "merchant": r.merchant, "amount": float(r.amount),
                 "type": r.type, "category": r.category} for r in hit.itertuples()]
        s.messages.append({"role": "user", "content": question})
        s.messages.append({"role": "assistant", "content": answer, "trace": trace})
        s.context = update_context(s.context, trace)
    return {"answer": answer, "trace": trace}


@app.get("/api/report", response_class=PlainTextResponse)
def api_report(request: Request, response: Response):
    s = get_session(request, response)
    require_statement(s)
    an = Analytics(s.df)
    ie, br, rec = an.income_vs_expense(), an.category_breakdown(), an.detect_recurring()
    flags = compute_flags(an, s.budgets)
    lines = [f"# Spending insights — {s.file_name}", "", an.data_summary(), "", "## Summary",
             f"- Income: ₹{ie['income']:,.0f}", f"- Expense (excl. investments): ₹{ie['expense']:,.0f}",
             f"- Invested: ₹{ie['invested']:,.0f}", f"- Savings rate: {ie['savings_rate_pct']}%", "",
             "## Spend by category", "| Category | Amount | Share |", "|---|---|---|"]
    lines += [f"| {c['category']} | ₹{c['amount']:,.0f} | {c['share_pct']}% |" for c in br.get("categories", [])]
    lines += ["", "## Alerts"] + [f"- **{f['title']}** — {f['detail']}" for f in flags]
    lines += ["", "## Subscriptions"] + [f"- {x['merchant']}: ₹{x['monthly_cost']:,.0f}/month"
                                        for x in rec["subscriptions"]]
    lines += ["", "_General budgeting guidance, not professional financial advice._"]
    return PlainTextResponse("\n".join(lines), media_type="text/markdown; charset=utf-8",
                             headers={"Content-Disposition": 'attachment; filename="insights_report.md"'})


@app.post("/api/reset")
def api_reset(request: Request, response: Response):
    s = get_session(request, response)
    with s.lock:
        try:
            from rag.vectorstore import drop_transactions_store

            drop_transactions_store(s.txn_store)
        except Exception:
            pass
        s.clear()
    return state_payload(s)


# --------------------------------------------------------------------------- #
# Static frontend
# --------------------------------------------------------------------------- #
@app.get("/")
def index():
    return FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-cache"})


class NoCacheStatic(StaticFiles):
    """Always revalidate CSS/JS so an edited frontend is never served stale."""

    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        resp.headers["Cache-Control"] = "no-cache"
        return resp


app.mount("/static", NoCacheStatic(directory=WEB_DIR), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
