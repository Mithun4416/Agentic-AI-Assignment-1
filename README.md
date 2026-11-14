# Statement-to-Insights Finance Agent

**Assignment 4, Agentic AI.** A web app (HTML/CSS/JS frontend on a FastAPI backend) where you upload a bank or UPI statement and chat with it.
Four agents parse the statement, categorise each transaction, compute exact numbers with pandas tools,
and give savings advice grounded in your own data plus a small budgeting knowledge base.

Stack: HTML/CSS/JS · FastAPI · LangChain 1.x (LCEL) · ChromaDB · local sentence-transformers embeddings · pandas.

---

## Setup

Requires Python 3.10+ (developed on 3.12).

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows  (macOS/Linux: source .venv/bin/activate)
pip install -r requirements.txt
copy .env.example .env            # macOS/Linux: cp .env.example .env
```

Edit `.env`:

| Variable | Meaning |
|---|---|
| `LLM_PROVIDER` | `groq`, `google` or `ollama` |
| `LLM_MODEL` | Model name for that provider. Check the provider's current model list; names change often |
| `GROQ_API_KEY` / `GOOGLE_API_KEY` | Key for your provider (both have free tiers) |
| `OLLAMA_MODEL` | Local model, if you use Ollama |

Tested with Groq `openai/gpt-oss-120b` (Nov 2026; `llama-3.3-70b-versatile` had been retired by then) and with
Google `gemini-3.5-flash-lite`. Each provider reads its own model from `GROQ_MODEL`, `GOOGLE_MODEL` or
`OLLAMA_MODEL`, so you can switch provider on the Overview page without editing `.env`. On Google's free tier,
`gemini-3.5-flash` allows only 20 requests a day (a single advice question uses 4–6), so the lite model is the default there.
For Ollama's cloud, set `OLLAMA_API_KEY` to the full key (`<id>.<secret>`); leave it empty to use a local Ollama.
Groq's free tier allows about **8,000 tokens per minute**, so asking many questions back to back can slow
answers while the limit refills. `LLM_REASONING_EFFORT=low` (the default) and the Advisor's reduced tool set keep
each question small. In the demo, give each question a few seconds before asking the next.

Build the knowledge base once (this also downloads the ~90 MB embedding model):

```bash
python -m rag.build_kb
```

Run:

```bash
python server.py
```

Then open http://localhost:8000 and click **Use sample statement**. The sample is synthetic (`data/generate_sample.py`), with planted
patterns so the demo has known answers.

Without an API key, the Dashboard still works (rule-based categories, every chart and alert). The Chat tab needs an LLM.

## Tests

```bash
pytest                        # offline tests: parser, tools, calculator, categoriser rules, router fallback, RAG, pipeline, advisor, API
```

Tests marked `llm` (router accuracy ≥ 27/30, categoriser accuracy ≥ 90%) run automatically when `.env` has a working
LLM, and are skipped otherwise. `tests/test_pipeline.py` drives the full routed LCEL pipeline with a scripted fake model,
so the wiring is tested without network access.

---

## Architecture

```
  Upload CSV / PDF ──► Parser Agent        rules + LLM column-mapping fallback, identifier masking
                            │
                            ▼
                     Categorizer Agent     keyword rules + few-shot LLM for unknown merchants (cached)
                            │
              ┌─────────────┴──────────────┐
              ▼                            ▼
   pandas DataFrame (session)     ChromaDB transactions_<hash>     ChromaDB budget_guidance
   source of truth for numbers    one doc per transaction          knowledge_base/*.md chunks

  User question
       ▼
  Question rewriter  (chat memory → standalone question)
       ▼
  Intent router      (ROUTER_PROMPT | llm.with_structured_output(Intent), keyword fallback)
       ▼
  RunnableBranch
       ├── transaction_lookup ─► Lookup chain    find_transactions + search_transactions
       ├── spending_analysis ──► Analysis Agent  10 pandas tools + calculator
       ├── savings_guidance ───► Advisor Agent   deterministic flags + pandas tools + search_budget_guidance
       └── other ──────────────► Scope reply     no tools
       ▼
  Reflection         (code check: every number must appear in tool outputs; LLM critique only if flagged)
       ▼
  Answer + Agent trace (intent, prompts, tool calls, retrieval yes/no + chunks, reflection verdict)
```

**Design rules**

1. **The LLM never does arithmetic.** Every figure comes from a pandas tool or the AST-based calculator.
2. **Deterministic first.** Parsing and categorising use rules; the LLM handles only what rules cannot.
3. **Retrieval is a decision.** Retrieval is exposed as tools; the agent calls them only when needed, and the trace shows it.
4. **Statements are sensitive.** Local embeddings; account numbers, UPI IDs and reference numbers masked before storage or any LLM call.

> **Semantic search finds *which* transactions are relevant; pandas computes *how much*.**
> Similarity search returns only the top k, so it is never used to compute a total.

## Project layout

```
server.py                  FastAPI backend: JSON API over the agents, serves web/
web/                       frontend: index.html, styles.css, app.js (no framework, no build step)
streamlit_app.py           the earlier Streamlit UI, kept as a fallback (streamlit run streamlit_app.py)
config.py                  get_llm(), get_embeddings(), categories, thresholds
agents/                    parser_agent, categorizer_agent, analysis_agent (+ lookup), advisor_agent
core/                      router (LCEL + RunnableBranch), rewriter, tool_loop (ReAct), reflection, memory, assistant
tools/                     pandas_tools (10 tools), calculator (safe AST), retrieval_tools (agentic RAG)
rag/                       vectorstore (Chroma collections), build_kb
prompts/prompts.py         every system and user prompt as a named ChatPromptTemplate
knowledge_base/            10 original budgeting notes
data/                      generate_sample.py, sample_statement.csv
tests/                     pytest suite + router_eval.csv (30 labelled questions)
```

## Assignment criteria mapping

| # | Criterion | Where it is | How to see it |
|---|---|---|---|
| 1 | LLM integration | `config.get_llm()`, used by every agent | Overview page shows provider and model; any chat answer |
| 2 | Interactive GUI | `web/` (HTML/CSS/JS) served by `server.py` | Upload, charts, editable category table, chat with suggested questions |
| 3 | Prompt engineering | `prompts/prompts.py`: zero-shot, few-shot, chain of thought, ReAct, reflection | Agent trace → **Prompts** tab shows the system and user prompt of every LLM call; **Reflection** tab shows the verdict |
| 4 | LangChain core components | `ChatPromptTemplate`, `MessagesPlaceholder`, LCEL pipes, `RunnablePassthrough.assign`, `RunnableBranch`, `with_structured_output`, `bind_tools`, `@tool`, `StrOutputParser` | `core/router.py` (`build_pipeline`) |
| 5 | Agentic RAG | `tools/retrieval_tools.py`: retrieval as tools the agent chooses to call | Ask one question that retrieves and one that does not; compare **Retrieval used: yes/no** |
| 6 | Intent detection and routing | `core/router.py`: Pydantic `Intent`, router chain, `RunnableBranch` | Trace header shows intent, reason and handler |
| 7 | Vector database | ChromaDB `transactions_<hash>` and `budget_guidance` (`rag/vectorstore.py`) | Trace → **Retrieval** tab shows collection, query, chunks and similarity |

## Prompts

All in `prompts/prompts.py`, each with a separate system and user message.

| Prompt | Technique | Used by |
|---|---|---|
| `PARSER_PROMPT` | Zero-shot + strict schema | Parser Agent, only when the synonym table cannot map columns |
| `CATEGORIZER_PROMPT` | Few-shot (9 labelled examples) | Categorizer Agent, unknown merchants in batches of 25 |
| `REWRITER_PROMPT` | Zero-shot + chat history | Follow-up questions |
| `ROUTER_PROMPT` | Few-shot + structured output | Intent router |
| `ANALYSIS_PROMPT` | Chain of thought + ReAct tool loop | Analysis Agent |
| `LOOKUP_PROMPT` | ReAct + agentic retrieval | Lookup chain |
| `ADVISOR_PROMPT` | Retrieval-grounded generation + ReAct | Advisor Agent |
| `REFLECTION_PROMPT` | Reflection / self-critique | Answers whose numbers fail the code check |
| `SCOPE_PROMPT` | Zero-shot | Greetings and off-topic questions |

## Sample data: planted facts

| Fact | Value |
|---|---|
| Salary | ₹85,000 on the 1st of Aug, Sep, Oct 2026 |
| Food & Dining | ₹6,000 → ₹6,000 → ₹8,400 (+40% in October) |
| Subscriptions | Netflix ₹649 (7th), Spotify ₹119 (12th), Cult.fit ₹1,499 (15th): ₹2,267/month |
| Anomaly | Croma Electronics ₹34,999 on 22 Sep 2026 |
| LLM-categorised rows | Person-to-person transfers (Priya, Rohan, Ankit, Arjun), Chaayos, Decathlon, Udemy |

## Demo script (about five minutes)

1. Open http://localhost:8000 and click **Use sample statement**. Show the Dashboard and the parse report.
2. Open the transactions table: point out a `rule` row (SWIGGY) and an `llm` row (CHAAYOS or PRIYA SHARMA).
3. Ask **"How much did I spend on food in the latest month compared with the month before?"**
   Trace: intent `spending_analysis`, tool `compare_periods`, retrieval **no**.
4. Ask **"What was that big shopping payment?"**
   Trace: intent `transaction_lookup`, retrieval **yes** on the transactions collection.
5. Ask **"Where am I overspending and what should I do about it?"**
   Trace: intent `savings_guidance`, pandas tools + guidance retrieval, cited note, reflection verdict.
6. Follow up with **"And what about subscriptions?"** to show memory and the rewriter.
7. Open `prompts/prompts.py` and `core/router.py` to show the prompts and the LCEL chain.

## Stretch features included

- Text-based PDF statements via `pdfplumber`, including password-protected files (a password field appears when you pick a PDF)
- Monthly budgets per category (Dashboard → *Monthly budgets*), used by alerts and the Advisor
- Downloadable Markdown insights report
- Provider and model switch on the Overview page, including local Ollama

## Privacy and safety

- Embeddings run locally; statement text is never sent out for embedding.
- The Parser masks account numbers, card numbers, UPI IDs and reference numbers before storage or any LLM call.
- The LLM sees only merchant names (categorising) and tool results (answering), never the full statement.
- `.env` and real statements (`*.pdf`, `uploads/`) are git-ignored. Only the synthetic sample is in the repo.
- The calculator parses with `ast` and allows only numbers and arithmetic operators. Tools take typed arguments; the LLM never runs pandas code.
- System prompts tell the agents to treat transaction descriptions as data and ignore any instructions in them.

Advice from the app is general budgeting guidance, not professional financial advice.

## Frontend

`web/` is plain HTML, CSS and JavaScript with no framework and no build step. `server.py` serves it and exposes
the agents as a small JSON API (`/api/state`, `/api/statement/sample`, `/api/statement/upload`, `/api/dashboard`,
`/api/transactions`, `/api/category`, `/api/budgets`, `/api/chat`, `/api/report`, `/api/reset`).

- Palette: Almond Cream `#EDE0D4`, Desert Sand `#E6CCB2`, Tan `#DDB892`, Faded Copper `#B08968`,
  Coffee Bean `#7F5539`, Toffee Brown `#9C6644`. Ferrari red `#FF2800` is used only for buttons and the small
  square indicator.
- Charts are single-hue bars (sorted category bars, monthly bars with a category filter) with hover tooltips,
  so they need no extra colours outside the palette.
- All model output is HTML-escaped before the small Markdown renderer runs.
- Session state (DataFrame, chat, budgets) is kept in server memory per browser cookie; restarting the server clears it.
