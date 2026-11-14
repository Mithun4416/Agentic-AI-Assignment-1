"""Statement-to-Insights Finance Agent — Streamlit entry point.

    streamlit run app.py
"""
from __future__ import annotations

import hashlib
import json

import pandas as pd
import plotly.express as px
import streamlit as st

from agents.advisor_agent import compute_flags
from agents.categorizer_agent import apply_correction, categorize
from agents.parser_agent import ParseError, parse_statement
from config import CATEGORIES, DATA_DIR, NOT_EXPENSE, PROVIDERS, default_model, default_provider, try_get_llm
from core.assistant import ask, make_context
from core.memory import update_context
from tools.pandas_tools import Analytics

st.set_page_config(page_title="Statement-to-Insights", page_icon="💸", layout="wide")

SAMPLE_PATH = DATA_DIR / "sample_statement.csv"
SPEND_CATEGORIES = [c for c in CATEGORIES if c not in NOT_EXPENSE]
SUGGESTED = [
    ("📊 Analysis", "How much did I spend on food in the latest month compared with the month before?"),
    ("🔎 Lookup", "What was that big shopping payment?"),
    ("💡 Advice", "Where am I overspending and what should I do about it?"),
    ("🔁 Follow-up", "And what about subscriptions?"),
]


# --------------------------------------------------------------------------- #
# Cached resources
# --------------------------------------------------------------------------- #
@st.cache_resource(show_spinner=False)
def cached_llm(provider: str, model: str):
    return try_get_llm(provider, model)


@st.cache_resource(show_spinner="Embedding the budgeting knowledge base (first run downloads the model)...")
def cached_guidance_store():
    from rag.vectorstore import build_guidance_store

    return build_guidance_store()


def init_state():
    defaults = {
        "df": None, "report": None, "file_hash": None, "file_name": None, "last_upload_hash": None,
        "messages": [], "context": {}, "cat_cache": {}, "cat_stats": None, "cat_prompts": [],
        "budgets": {}, "txn_store": None, "store_error": None, "pending_question": None,
    }
    for k, v in defaults.items():
        st.session_state.setdefault(k, v)


def reset_session():
    from rag.vectorstore import drop_transactions_store

    drop_transactions_store(st.session_state.get("txn_store"))
    for k in list(st.session_state.keys()):
        del st.session_state[k]
    init_state()


# --------------------------------------------------------------------------- #
# Loading a statement: Parser -> Categorizer -> vector store
# --------------------------------------------------------------------------- #
def load_statement(data: bytes, name: str, llm, password: str | None = None):
    h = hashlib.sha1(data).hexdigest()[:16]
    if h == st.session_state.file_hash:
        return
    try:
        with st.spinner("Parser Agent: reading the statement..."):
            df, report = parse_statement(data, name, llm=llm, password=password)
    except ParseError as exc:
        st.sidebar.error(str(exc))
        return
    with st.spinner("Categorizer Agent: rules first, then the LLM for unknown merchants..."):
        prompts: list = []
        df, stats = categorize(df, llm=llm, cache=st.session_state.cat_cache, prompt_trace=prompts)
    st.session_state.update(df=df, report=report, file_hash=h, file_name=name, cat_stats=stats,
                            cat_prompts=prompts, messages=[], context={}, txn_store=None, store_error=None)
    try:
        from rag.vectorstore import build_transactions_store

        with st.spinner("Embedding transactions locally into ChromaDB..."):
            st.session_state.txn_store = build_transactions_store(df, h)
    except Exception as exc:
        st.session_state.store_error = f"Vector store unavailable: {exc}"


# --------------------------------------------------------------------------- #
# Sidebar
# --------------------------------------------------------------------------- #
def sidebar():
    sb = st.sidebar
    sb.title("💸 Statement-to-Insights")
    sb.caption("Upload a bank/UPI statement and chat with it.")

    sb.subheader("LLM")
    provider = sb.selectbox("Provider", PROVIDERS, index=PROVIDERS.index(default_provider())
                            if default_provider() in PROVIDERS else 0)
    model = sb.text_input("Model", value=default_model(provider), help="Read from .env; edit to switch models.")
    llm, err = cached_llm(provider, model)
    if llm is not None:
        sb.success(f"Connected: **{provider}** · `{model}`")
    else:
        sb.error(f"LLM not available: {err}")
        sb.caption("The Dashboard still works with rule-based categories. Chat needs an LLM.")

    sb.subheader("Statement")
    up = sb.file_uploader("Upload CSV or text PDF", type=["csv", "pdf"])
    password = None
    if up is not None and up.name.lower().endswith(".pdf"):
        password = sb.text_input("PDF password (if any)", type="password")
    if up is not None:
        data = up.getvalue()
        uh = hashlib.sha1(data + (password or "").encode()).hexdigest()
        if uh != st.session_state.last_upload_hash:
            st.session_state.last_upload_hash = uh
            load_statement(data, up.name, llm, password)
    if sb.button("Use sample statement", type="primary", use_container_width=True):
        if not SAMPLE_PATH.exists():
            from data.generate_sample import write_csv

            write_csv(SAMPLE_PATH)
        load_statement(SAMPLE_PATH.read_bytes(), SAMPLE_PATH.name, llm)

    if st.session_state.file_name:
        sb.info(f"Loaded **{st.session_state.file_name}** · {len(st.session_state.df)} transactions")
    if st.session_state.store_error:
        sb.warning(st.session_state.store_error)

    sb.divider()
    if sb.button("Reset session", use_container_width=True,
                 help="Clears the DataFrame, chat and this session's Chroma collection"):
        reset_session()
        st.rerun()
    sb.caption("Embeddings run locally. Account numbers, UPI IDs and reference numbers are masked "
               "before anything is stored or sent to the LLM.")
    return llm, provider, model


# --------------------------------------------------------------------------- #
# Dashboard
# --------------------------------------------------------------------------- #
def inr(x: float) -> str:
    """Indian digit grouping, no paise: 255000 -> '₹2,55,000'."""
    from data.generate_sample import inr as _inr

    return "₹" + _inr(round(x)).split(".")[0]


def build_report(an: Analytics, flags: list[dict]) -> str:
    ie = an.income_vs_expense()
    br = an.category_breakdown()
    rec = an.detect_recurring()
    lines = [f"# Spending insights — {st.session_state.file_name}", "", an.data_summary(), "",
             "## Summary",
             f"- Income: {inr(ie['income'])}", f"- Expense (excl. investments): {inr(ie['expense'])}",
             f"- Invested: {inr(ie['invested'])}", f"- Savings rate: {ie['savings_rate_pct']}%", "",
             "## Spend by category", "| Category | Amount | Share |", "|---|---|---|"]
    lines += [f"| {c['category']} | {inr(c['amount'])} | {c['share_pct']}% |" for c in br.get("categories", [])]
    lines += ["", "## Alerts"] + [f"- **{f['title']}** — {f['detail']}" for f in flags]
    lines += ["", "## Subscriptions"] + [f"- {s['merchant']}: {inr(s['monthly_cost'])}/month"
                                        for s in rec["subscriptions"]]
    lines += ["", "_General budgeting guidance, not professional financial advice._"]
    return "\n".join(lines)


def dashboard():
    df = st.session_state.df
    if df is None:
        st.info("👈 Upload a statement or click **Use sample statement** to begin.")
        return
    an = Analytics(df)
    report = st.session_state.report

    with st.expander("Parse & categorisation report", expanded=False):
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Rows read", report.rows_read)
        c2.metric("Valid", report.rows_valid)
        c3.metric("Dropped", report.rows_dropped)
        c4.metric("Header row", report.header_row)
        st.write("**Column mapping** (source: " + report.mapping_source + ")", report.column_mapping)
        if report.drop_reasons:
            st.write("**Drop reasons**", dict(report.drop_reasons))
        stats = st.session_state.cat_stats or {}
        st.write(f"**Categorizer:** {stats.get('rule', 0)} rows by rule, {stats.get('llm', 0)} by LLM, "
                 f"{stats.get('fallback', 0)} fallback, {stats.get('user', 0)} corrected by you "
                 f"({stats.get('llm_calls', 0)} LLM batch call(s)).")
        if stats.get("llm_error"):
            st.warning(f"LLM categorisation failed, rules + fallback used: {stats['llm_error']}")
        for p in ([report.prompt_trace] if report.prompt_trace else []) + st.session_state.cat_prompts:
            st.markdown(f"**Prompt: `{p['name']}`**")
            st.code(p["system"], language="text")
            st.code(p["user"], language="text")

    months = an.months
    period = st.selectbox("Period", ["All months"] + months, index=0)
    start = None if period == "All months" else period
    ie = an.income_vs_expense(start, start)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Income", inr(ie["income"]))
    c2.metric("Spend (excl. investments)", inr(ie["expense"]))
    c3.metric("Net", inr(ie["net_after_expense"]))
    c4.metric("Savings rate", f"{ie['savings_rate_pct']:.1f}%" if ie["savings_rate_pct"] is not None else "—")

    left, right = st.columns(2)
    br = an.category_breakdown(start, start)
    if br.get("categories"):
        bdf = pd.DataFrame(br["categories"])
        fig = px.pie(bdf, names="category", values="amount", hole=0.55, title="Spend by category")
        fig.update_traces(textposition="inside", textinfo="percent+label")
        fig.update_layout(showlegend=False, margin=dict(t=50, b=10, l=10, r=10))
        left.plotly_chart(fig, use_container_width=True)
    exp = an._expense(an.df)
    trend = exp.groupby(["month", "category"])["amount"].sum().reset_index()
    fig2 = px.bar(trend, x="month", y="amount", color="category", title="Monthly spend by category")
    fig2.update_layout(margin=dict(t=50, b=10, l=10, r=10), legend_title=None, xaxis_title=None,
                       yaxis_title="₹")
    right.plotly_chart(fig2, use_container_width=True)

    with st.expander("Monthly budgets (optional)"):
        st.caption("Set a limit per category; the Advisor and alerts compare the latest month against it.")
        cols = st.columns(4)
        for i, cat in enumerate(SPEND_CATEGORIES):
            val = cols[i % 4].number_input(cat, min_value=0, step=500,
                                           value=int(st.session_state.budgets.get(cat, 0)), key=f"budget_{cat}")
            if val:
                st.session_state.budgets[cat] = val
            else:
                st.session_state.budgets.pop(cat, None)

    st.subheader("Alerts")
    flags = compute_flags(an, st.session_state.budgets)
    for f in flags:
        (st.warning if f["severity"] == "warning" else st.info)(f"**{f['title']}** — {f['detail']}")

    st.download_button("⬇️ Download insights report (Markdown)", build_report(an, flags),
                       file_name="insights_report.md", mime="text/markdown")

    st.subheader("Transactions")
    st.caption("Change a category to correct it; the correction applies to every row from that merchant "
               "and is remembered. `category_source` shows whether a rule, the LLM, or you set it.")
    view = df[["date", "merchant", "amount", "type", "category", "category_source", "description"]].copy()
    edited = st.data_editor(
        view, use_container_width=True, height=380, hide_index=True, key="txn_editor",
        column_config={
            "date": st.column_config.DateColumn("date", format="YYYY-MM-DD"),
            "amount": st.column_config.NumberColumn("amount", format="₹%.2f"),
            "category": st.column_config.SelectboxColumn("category", options=CATEGORIES, required=True),
        },
        disabled=["date", "merchant", "amount", "type", "category_source", "description"],
    )
    changed = edited.index[edited["category"] != view["category"]]
    if len(changed):
        new_df = df
        for i in changed:
            new_df = apply_correction(new_df, st.session_state.cat_cache, df.loc[i, "merchant"],
                                      edited.loc[i, "category"])
        merchants = set(df.loc[changed, "merchant"])
        if st.session_state.txn_store is not None:
            from rag.vectorstore import upsert_transactions

            upsert_transactions(st.session_state.txn_store, new_df[new_df["merchant"].isin(merchants)])
        st.session_state.df = new_df
        stats = st.session_state.cat_stats or {}
        for src in ("rule", "llm", "fallback", "user"):
            stats[src] = int((new_df["category_source"] == src).sum())
        del st.session_state["txn_editor"]
        st.rerun()


# --------------------------------------------------------------------------- #
# Chat
# --------------------------------------------------------------------------- #
def _pretty(s: str, limit: int = 2500) -> str:
    try:
        s = json.dumps(json.loads(s), indent=2, ensure_ascii=False)
    except (json.JSONDecodeError, TypeError):
        pass
    return s if len(s) <= limit else s[:limit] + "\n... (truncated)"


def render_trace(trace: dict):
    df = st.session_state.df
    if df is not None and trace.get("matched_txn_ids") and trace.get("intent") == "transaction_lookup":
        rows = df[df["txn_id"].isin(trace["matched_txn_ids"])][["date", "merchant", "amount", "type", "category"]]
        if len(rows):
            st.caption("Transactions the agent retrieved (the answer above names the relevant one)")
            st.dataframe(rows, hide_index=True, use_container_width=True,
                         column_config={"date": st.column_config.DateColumn(format="YYYY-MM-DD"),
                                        "amount": st.column_config.NumberColumn(format="₹%.2f")})
    with st.expander("🧭 Agent trace"):
        used = "yes" if trace.get("retrieval") else "no"
        st.markdown(
            f"**Intent:** `{trace.get('intent')}` ({trace.get('router_source')}) — {trace.get('reason')}  \n"
            f"**Handler:** {trace.get('handler')}  \n"
            f"**Retrieval used:** **{used}**  \n"
            f"**Original question:** {trace.get('question')}  \n"
            f"**Rewritten (standalone) question:** {trace.get('rewritten')}"
        )
        t_prompts, t_tools, t_rag, t_reflect = st.tabs(["Prompts", "Tool calls", "Retrieval", "Reflection"])
        with t_prompts:
            for p in trace.get("prompts", []):
                st.markdown(f"**`{p['name']}`** — system prompt")
                st.code(p["system"], language="text")
                st.markdown("user prompt")
                st.code(p["user"], language="text")
        with t_tools:
            steps = [s for s in trace.get("steps", []) if s.get("thought")]
            for s in steps:
                st.markdown(f"*Step {s['step']} thought:* {s['thought']}")
            if not trace.get("tool_calls"):
                st.write("No tools called.")
            for c in trace.get("tool_calls", []):
                st.markdown(f"**Step {c['step']}:** `{c['tool']}({json.dumps(c['args'], ensure_ascii=False)})`")
                st.code(_pretty(c["result"]), language="json")
        with t_rag:
            if not trace.get("retrieval"):
                st.write("Retrieval used: **no**. The agent did not need semantic search for this question.")
            for r in trace.get("retrieval", []):
                st.markdown(f"**Collection:** `{r['collection']}` · **query:** “{r['query']}”")
                if r["chunks"]:
                    cols = ["similarity", "source", "topic", "text"] if r["collection"] == "budget_guidance" \
                        else ["similarity", "date", "merchant", "amount", "category", "text"]
                    st.dataframe(pd.DataFrame(r["chunks"])[[c for c in cols if c in r["chunks"][0]]],
                                 hide_index=True, use_container_width=True)
        with t_reflect:
            ref = trace.get("reflection") or {}
            st.markdown(f"**Verdict:** {ref.get('verdict', '—')}  \n"
                        f"**Numbers not found in tool outputs (code check):** "
                        f"{', '.join(ref.get('code_check_flags', [])) or 'none'}  \n"
                        f"**LLM critique used:** {ref.get('llm_used', False)}  \n"
                        f"**Notes:** {ref.get('notes', '')}")
        if trace.get("errors"):
            st.warning("\n".join(trace["errors"]))


def chat(llm):
    if st.session_state.df is None:
        st.info("Load a statement first.")
        return
    if llm is None:
        st.error("Chat needs an LLM. Add your API key and model to `.env` (see `.env.example`), "
                 "or choose Ollama in the sidebar.")
        return

    cols = st.columns(len(SUGGESTED))
    for col, (label, q) in zip(cols, SUGGESTED):
        if col.button(label, help=q, use_container_width=True):
            st.session_state.pending_question = q

    # History lives in a container created before the input, so new messages render above the input box.
    history = st.container()
    typed = st.chat_input("Ask about your spending...")
    with history:
        for m in st.session_state.messages:
            with st.chat_message(m["role"]):
                st.markdown(m["content"])
                if m.get("trace"):
                    render_trace(m["trace"])

    question = typed or st.session_state.pending_question
    st.session_state.pending_question = None
    if not question:
        return

    with history:
        with st.chat_message("user"):
            st.markdown(question)
        ctx = make_context(llm, st.session_state.df, retrieval_tools=_retrieval_tools(),
                           budgets=st.session_state.budgets)
        with st.chat_message("assistant"):
            with st.spinner("Thinking: rewrite → route → agent → tools → reflection..."):
                answer, trace = ask(ctx, question, st.session_state.messages, st.session_state.context)
            st.markdown(answer)
            render_trace(trace)
    st.session_state.messages.append({"role": "user", "content": question})
    st.session_state.messages.append({"role": "assistant", "content": answer, "trace": trace})
    st.session_state.context = update_context(st.session_state.context, trace)


def _retrieval_tools() -> dict:
    from tools.retrieval_tools import make_retrieval_tools

    try:
        guidance = cached_guidance_store()
    except Exception as exc:
        st.warning(f"Knowledge base unavailable: {exc}")
        guidance = None
    return make_retrieval_tools(txn_store=st.session_state.txn_store, guidance_store=guidance)


# --------------------------------------------------------------------------- #
# About
# --------------------------------------------------------------------------- #
ABOUT = """
### How it works
Upload → **Parser Agent** (rules + LLM column mapping) → **Categorizer Agent** (keyword rules + few-shot LLM)
→ pandas DataFrame (source of truth for numbers) + ChromaDB `transactions_<hash>` collection.

Each question → **rewriter** (memory) → **intent router** (LCEL + structured output) → one of
**Lookup chain**, **Analysis Agent**, **Advisor Agent** or a scope reply → **reflection** number check → answer + trace.

> Semantic search finds *which* transactions are relevant; pandas computes *how much*.
> Similarity search returns only the top k, so it is never used to compute a total.

### Assignment criteria
| # | Criterion | Where it is shown |
|---|---|---|
| 1 | LLM integration | `config.get_llm()`; sidebar shows provider and model; every agent uses it |
| 2 | Interactive GUI | Streamlit Dashboard and Chat tabs: upload, charts, editable table, chat |
| 3 | Prompt engineering | `prompts/prompts.py`: zero-shot, few-shot, chain of thought, ReAct, reflection. Trace → Prompts tab shows system and user prompts |
| 4 | LangChain core components | `ChatPromptTemplate`, LCEL pipes, `RunnableBranch`, `RunnablePassthrough`, `with_structured_output`, `bind_tools`, `@tool` (`core/router.py`) |
| 5 | Agentic RAG | Retrieval is two tools the agent chooses to call; trace shows *Retrieval used: yes/no* |
| 6 | Intent detection and routing | Router chain + `RunnableBranch`; trace shows intent, reason and handler |
| 7 | Vector database | ChromaDB `transactions_<hash>` and `budget_guidance` collections; trace → Retrieval tab shows chunks and similarity |

### Privacy
Embeddings run locally (`all-MiniLM-L6-v2`). Account numbers, UPI IDs and reference numbers are masked by the
Parser. The LLM only sees merchant names (for categorising) and tool results (for answering), never the full statement.
"""


def main():
    init_state()
    llm, provider, model = sidebar()
    tab_dash, tab_chat, tab_about = st.tabs(["📈 Dashboard", "💬 Chat", "ℹ️ About"])
    with tab_dash:
        dashboard()
    with tab_chat:
        chat(llm)
    with tab_about:
        st.markdown(ABOUT)


main()
