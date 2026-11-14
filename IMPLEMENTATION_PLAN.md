# Implementation Plan: Statement-to-Insights Finance Agent

**Assignment 4, Agentic AI** | Stack: Streamlit, Python, LangChain (LCEL), ChromaDB, pandas

---

## 1. Goal and scope

Build a Streamlit app where a user uploads a bank or UPI statement and then chats with it. Four agents parse the statement, categorise each transaction, compute exact numbers with pandas tools, and give savings advice grounded in the user's own data plus a small budgeting knowledge base.

**In scope (must have)**

- CSV statement upload, with a bundled synthetic sample statement for the demo
- Four agents: Parser, Categorizer, Analysis, Advisor
- Intent routing across transaction lookup, spending analysis and savings guidance
- Agentic RAG over two ChromaDB collections (transactions, budgeting guidance)
- pandas and calculator tools for every number shown to the user
- Dashboard tab (charts) and Chat tab, plus a trace panel that shows intent, prompts, tool calls and retrieved chunks

**Stretch (only after all seven criteria pass)**

- Text-based PDF statements via `pdfplumber`, including password-protected files
- User-set monthly budgets per category
- Downloadable insights report
- Local model toggle (Ollama)

**Out of scope**

- Scanned or image PDFs (needs OCR)
- Live bank or UPI integrations
- Investment or tax advice

---

## 2. Design principles

These four rules drive most decisions below.

1. **The LLM never does arithmetic.** Every total, average, percentage and comparison comes from a pandas tool or the calculator. The LLM chooses the tool and explains the result.
2. **Deterministic first, LLM second.** Parsing and categorising try rules first and call the LLM only for what the rules cannot handle. This is cheaper, faster and repeatable in a demo.
3. **Retrieval is a decision.** Retrieval is exposed as tools. The agent calls them when the question needs them and skips them when it does not, and the UI shows which happened.
4. **Statements are sensitive.** Embeddings run locally, identifiers are masked before any API call, and real statements never go into the repository.

---

## 3. Architecture

```
                       ┌────────────────────────────┐
  Upload CSV / PDF ──► │ Parser Agent               │  rules + LLM column mapping
                       └─────────────┬──────────────┘
                                     ▼
                       ┌────────────────────────────┐
                       │ Categorizer Agent          │  keyword rules + few-shot LLM
                       └─────────────┬──────────────┘
                                     ▼
              ┌──────────────────────┴───────────────────────┐
              ▼                                              ▼
   pandas DataFrame (session)                 ChromaDB "transactions" collection
   source of truth for numbers                one document per transaction + metadata

  ChromaDB "budget_guidance" collection  ◄── knowledge_base/*.md (built once)

  User question
       │
       ▼
  Question rewriter (uses chat memory, makes follow-ups standalone)
       │
       ▼
  Intent Router (LCEL, structured output)
       │
       ├── transaction_lookup ──► Lookup chain    tools: search_transactions, find_transactions
       ├── spending_analysis ───► Analysis Agent  tools: pandas tools, calculator
       ├── savings_guidance ────► Advisor Agent   tools: pandas tools, search_budget_guidance
       └── other ───────────────► Polite scope reply (no tools)
       │
       ▼
  Reflection check (numbers in answer must appear in tool outputs)
       │
       ▼
  Answer + trace panel
```

---

## 4. Technology choices

| Layer | Choice | Why |
|---|---|---|
| UI | Streamlit | Required. `st.chat_message`, `st.file_uploader`, tabs, expanders |
| LLM (default) | API model with tool calling, set by `LLM_PROVIDER` and `LLM_MODEL` in `.env` | Groq and Google AI Studio both have free tiers. Model names change often, so read them from config and never hard-code them |
| LLM (local option) | Ollama via `langchain-ollama` | Shows the "local model" option and keeps data on the machine |
| Orchestration | LangChain 1.x: `ChatPromptTemplate`, LCEL pipes, `RunnableBranch`, `with_structured_output`, `bind_tools`, `@tool` | Required. Needs Python 3.10 or newer |
| Vector DB | ChromaDB via `langchain-chroma` | Required |
| Embeddings | `sentence-transformers/all-MiniLM-L6-v2` via `langchain-huggingface` | Runs locally, free, no statement text leaves the laptop for embedding |
| Data tools | pandas, safe calculator (AST-based, no `eval`) | Required. Exact numbers |
| Charts | Plotly | Interactive category and trend charts |
| PDF (stretch) | `pdfplumber` | Table extraction from text PDFs, supports passwords |
| Tests | pytest | Golden totals, router accuracy, categoriser accuracy |

**Versions:** do not pin versions from memory. Create a virtual environment, install the packages below, confirm the app runs, then run `pip freeze > requirements.txt` so the submission is reproducible.

```
streamlit pandas plotly python-dotenv pydantic pytest
langchain langchain-core langchain-chroma chromadb
langchain-huggingface sentence-transformers
langchain-groq            # or langchain-google-genai
langchain-ollama          # optional, local model
pdfplumber                # stretch
```

---

## 5. Project structure

```
finance-agent/
├── app.py                      # Streamlit entry point
├── .env.example                # LLM_PROVIDER, LLM_MODEL, API key names (no real keys)
├── requirements.txt
├── README.md                   # setup, run, criteria mapping, prompt list
├── config.py                   # get_llm(), get_embeddings(), categories, thresholds
├── agents/
│   ├── parser_agent.py
│   ├── categorizer_agent.py
│   ├── analysis_agent.py
│   └── advisor_agent.py
├── core/
│   ├── router.py               # intent schema, router chain, RunnableBranch
│   ├── rewriter.py             # follow-up question rewriter
│   ├── tool_loop.py            # shared tool-calling loop with trace capture
│   ├── reflection.py           # number-grounding check
│   └── memory.py               # chat history helpers
├── tools/
│   ├── pandas_tools.py         # all analysis tools
│   ├── calculator.py
│   └── retrieval_tools.py      # search_transactions, search_budget_guidance
├── rag/
│   ├── vectorstore.py          # Chroma client, collection build and reset
│   └── build_kb.py             # embeds knowledge_base/ once
├── prompts/
│   └── prompts.py              # every system and user prompt, named constants
├── knowledge_base/             # 8 to 12 short markdown notes
├── data/
│   ├── generate_sample.py      # synthetic 3-month statement
│   └── sample_statement.csv
└── tests/
    ├── test_parser.py
    ├── test_tools.py
    ├── test_router.py
    ├── test_categorizer.py
    └── router_eval.csv         # 30 labelled questions
```

---

## 6. Data model

**Canonical transaction schema** (every statement is converted to this):

| Column | Type | Notes |
|---|---|---|
| `txn_id` | str | Stable hash of date, description, amount and row number |
| `date` | datetime | Parsed with `dayfirst=True` |
| `description` | str | Raw narration, identifiers masked |
| `merchant` | str | Cleaned name, e.g. `SWIGGY` |
| `amount` | float | Always positive |
| `type` | str | `debit` or `credit` |
| `balance` | float or null | If present in the statement |
| `category` | str | From the Categorizer Agent |
| `category_source` | str | `rule` or `llm`, shown in the UI |

**Categories:** Food & Dining, Groceries, Transport, Shopping, Bills & Utilities, Rent, Entertainment & Subscriptions, Health, Education, Transfers, Cash Withdrawal, Investments, Income, Other.

**Sample data.** `generate_sample.py` writes three months of realistic UPI-style rows with patterns planted on purpose, so the demo has known answers:

- Food & Dining rises about 40% in month three
- Three recurring subscriptions on fixed dates
- One unusually large Shopping transaction
- Salary credit on the first of each month

The planted values double as golden numbers for the tests.

---

## 7. Agent design

### 7.1 Parser Agent

**Input:** uploaded file. **Output:** DataFrame in the canonical schema plus a parse report (rows read, rows dropped, reasons).

1. Read the file. CSV: `pd.read_csv` with header-row detection (bank exports often have several lines of account details above the table). PDF (stretch): `pdfplumber` `extract_tables`, with a password field in the UI.
2. Map columns. Try a synonym table first (`Narration`, `Particulars`, `Transaction Remarks` map to `description`; `Withdrawal Amt`, `Debit` map to debit; and so on). If any required column is still unmapped, call the LLM with the header row and three sample rows and ask for a JSON mapping via `with_structured_output`.
3. Normalise. Strip Indian-format commas (`1,23,456.78`), handle `Dr`/`Cr` suffixes, handle one signed amount column or separate debit and credit columns, parse dates day-first.
4. Clean merchants. Regex over UPI narrations such as `UPI/DR/123456789012/SWIGGY/YESB/...` to pull out the merchant name.
5. Mask identifiers. Replace account numbers, UPI reference numbers and UPI IDs with placeholders before the text is stored or sent anywhere.
6. Validate. Drop rows with no date or amount, report them, and stop with a clear message if fewer than five valid rows remain.

**Prompt technique:** zero-shot with a strict output schema.

### 7.2 Categorizer Agent

**Input:** parsed DataFrame. **Output:** `category` and `category_source` columns.

1. Rules pass. A keyword dictionary maps known merchants to categories (`SWIGGY`, `ZOMATO` to Food & Dining; `UBER`, `OLA`, `METRO` to Transport; and so on). This usually covers most rows.
2. LLM pass. Collect the **unique** merchants that the rules missed and send them in batches of about 25 with a few-shot prompt. Output is a structured list of `{merchant, category}` limited to the fixed category list.
3. Cache the merchant-to-category map in session state so reruns do not call the LLM again.
4. The Dashboard tab lets the user correct a category, and the correction updates the cache.

**Prompt technique:** few-shot (six to eight labelled examples covering tricky cases such as person-to-person transfers and ATM withdrawals).

### 7.3 Analysis Agent

**Input:** standalone question. **Output:** answer with exact figures and, where useful, a small table.

A tool-calling loop: the LLM receives the question plus tool descriptions, calls one or more tools, reads the results and writes the answer. Tools (all `@tool` functions over the session DataFrame):

| Tool | Returns |
|---|---|
| `get_total_spend(category, start_date, end_date)` | Total debit amount |
| `category_breakdown(start_date, end_date)` | Amount and share per category |
| `monthly_trend(category)` | Month-by-month totals |
| `top_merchants(n, category, start_date, end_date)` | Top merchants by spend |
| `compare_periods(period_a, period_b, category)` | Absolute and percentage change |
| `find_transactions(merchant, min_amount, max_amount, start_date, end_date)` | Matching rows (exact filters) |
| `detect_recurring()` | Likely subscriptions and their monthly cost |
| `detect_anomalies()` | Transactions far above the category norm |
| `income_vs_expense(start_date, end_date)` | Income, expense, net, savings rate |
| `calculator(expression)` | Safe arithmetic for anything left over |

Every tool validates its arguments, returns a compact JSON string, and returns a readable message (not an exception) when no rows match.

**Prompt technique:** ReAct-style tool loop with a short Chain-of-Thought instruction ("first state which figures you need, then call tools, then answer using only tool results").

### 7.4 Advisor Agent

**Input:** standalone question. **Output:** overspending flags plus three to five specific savings actions, with sources.

1. Deterministic flags, computed in pandas before the LLM runs:
   - Category share against the guideline in the knowledge base (for example the 50/30/20 split)
   - Month-over-month rise above a threshold (default 20%)
   - Subscriptions total from `detect_recurring`
   - Anomalies from `detect_anomalies`
   - Spend against user budgets, if set (stretch)
2. Agentic retrieval. The agent may call `search_budget_guidance` for relevant notes and pandas tools for supporting figures.
3. Draft advice that names the user's actual categories, merchants and amounts.
4. Reflection pass (section 10).

Every answer on this route ends with a one-line note that it is general budgeting guidance, not professional financial advice.

**Prompt technique:** retrieval-grounded generation plus reflection.

---

## 8. Intent detection and routing

**Schema** (Pydantic, used with `with_structured_output`):

```python
class Intent(BaseModel):
    intent: Literal["transaction_lookup", "spending_analysis", "savings_guidance", "other"]
    reason: str  # one short sentence, shown in the trace panel
```

**Flow** (all LCEL):

```python
router_chain = ROUTER_PROMPT | llm.with_structured_output(Intent)

branch = RunnableBranch(
    (lambda x: x["intent"] == "transaction_lookup", lookup_chain),
    (lambda x: x["intent"] == "spending_analysis", analysis_chain),
    (lambda x: x["intent"] == "savings_guidance", advisor_chain),
    fallback_chain,
)

full_chain = (
    RunnablePassthrough.assign(question=rewriter_chain)
    | RunnablePassthrough.assign(intent=router_chain | (lambda r: r.intent))
    | branch
)
```

| Intent | Example | Handler |
|---|---|---|
| `transaction_lookup` | "What was that payment to the gym last month?" | Lookup chain |
| `spending_analysis` | "How much did I spend on food in October?" | Analysis Agent |
| `savings_guidance` | "Where am I overspending and how do I cut it?" | Advisor Agent |
| `other` | "What's the weather?" | Scope reply |

The router prompt carries a few labelled examples per intent. If structured output fails to parse, fall back to a keyword heuristic so the app never crashes mid-demo.

---

## 9. Agentic RAG

**Two collections**

| Collection | Documents | Metadata | Built |
|---|---|---|---|
| `transactions_<session>` | One per transaction: `"{date} {merchant} {description} {type} ₹{amount} [{category}]"` | `txn_id`, `date_ts` (int), `amount`, `category`, `type`, `month` | On each upload |
| `budget_guidance` | Chunks of `knowledge_base/*.md` | `source` file, `topic` | Once, persisted |

**Knowledge base.** Write 8 to 12 short notes yourself (150 to 300 words each) so there are no copyright or sourcing issues: the 50/30/20 rule, emergency fund basics, subscription audits, reducing food-delivery spend, transport cost tips, the envelope method, impulse-purchase rules, student budgeting, tracking small UPI payments, setting category limits.

**What makes it agentic.** Retrieval is two tools, `search_transactions(query, k, category, month)` and `search_budget_guidance(query, k)`. The model decides per question:

| Question | Retrieval? | Why |
|---|---|---|
| "Total food spend in October" | No | A pandas tool gives the exact answer |
| "That payment to the gym place" | Yes, transactions | Fuzzy description match |
| "How do I cut food delivery spend?" | Yes, guidance (plus pandas for figures) | Needs budgeting notes |
| "Thanks!" | No | Nothing to ground |

The trace panel prints `Retrieval used: yes/no`, the query, and the chunks returned. That makes the decision visible to the evaluator.

**Rule to state in the README:** semantic search finds *which* transactions are relevant; pandas computes *how much*. Similarity search returns the top k only, so it must never be used to compute a total.

**Grounding.** Advice cites the guidance note it used (`source` metadata), and lookups show the matched rows in a table.

---

## 10. Prompt engineering

All prompts live in `prompts/prompts.py` as named constants. Each is a `ChatPromptTemplate` with a separate **system** message and **user** message, and the trace panel shows both for every call. This satisfies "clearly identify the system and user prompts used".

| Technique | Where | How |
|---|---|---|
| Zero-shot | Parser column mapping, question rewriter | Instruction plus schema |
| Few-shot | Categorizer, intent router | Labelled examples in the prompt |
| Chain of Thought | Analysis Agent | "List the figures you need, then call tools, then answer" |
| ReAct | Analysis and Advisor tool loops | Thought, tool call, observation, repeat, capped at five steps |
| Reflection | Advisor and Analysis answers | Second pass checks the draft against tool outputs |

**Reflection check, two layers**

1. Code check: extract every number from the draft and confirm each one appears in the tool outputs for that turn (allowing for rounding and formatting). Any number that does not match is flagged.
2. LLM critique: a short prompt receives the draft, the tool outputs and the flags, then returns either `approved` or a corrected answer. One revision at most.

**Example system prompt (Analysis Agent)**

```
You are the Analysis Agent in a personal finance assistant.
You answer questions about the user's own bank or UPI transactions.

Rules:
1. Never calculate numbers yourself. Call a tool for every figure.
2. First state briefly which figures you need. Then call tools. Then answer.
3. Use only figures that appear in tool results. If a tool returns no data, say so.
4. Amounts are in Indian rupees. Format them as ₹12,345.
5. Keep the answer under 120 words unless the user asks for detail.
```

---

## 11. Conversation memory

- `st.session_state.messages` holds the chat history. The last six turns go into prompts through `MessagesPlaceholder`.
- The **question rewriter** turns follow-ups into standalone questions before routing ("and last month?" becomes "How much did I spend on Food & Dining in September 2026?").
- `st.session_state.context` keeps the last category, merchant and period mentioned, as a fallback when the rewriter is unsure.

---

## 12. User interface

**Sidebar:** file upload, "Use sample statement" button, LLM provider display, "Reset session" button (clears the DataFrame, chat and the session's Chroma collection).

**Tab 1, Dashboard**
- Metric tiles: total income, total spend, net, savings rate
- Spend by category (bar or donut) and monthly trend (line)
- Alert cards from the deterministic overspending flags
- Editable transaction table with category correction

**Tab 2, Chat**
- `st.chat_input` and message history
- Four suggested question chips, one per intent, for the demo
- Under each answer, an "Agent trace" expander: detected intent and reason, rewritten question, system and user prompts, tool calls with arguments and results, retrieval yes/no with chunks, reflection verdict

**Tab 3, About**
- The seven criteria and where each one appears in the app

**Streamlit specifics**
- `@st.cache_resource` for the embedding model, Chroma client and LLM client
- Keep the DataFrame in `st.session_state`, never in a module global
- Name the transactions collection with a hash of the uploaded file so reruns do not insert duplicates

---

## 13. Privacy and safety

- Local embeddings, so statement text is not sent out for embedding
- Mask account numbers, UPI IDs and reference numbers in the Parser, before storage and before any LLM call
- Send the LLM only what a step needs: merchant names for categorising, tool results for answering. Never the full statement
- API keys in `.env`, loaded with `python-dotenv`, with `.env` and any real statement in `.gitignore`
- Demo and repository use only the synthetic statement
- Safe calculator: parse with `ast` and allow only numbers and arithmetic operators
- Tools take typed arguments. The LLM never writes or runs pandas code directly
- Treat statement text as data. System prompts say to ignore any instructions found inside transaction descriptions

---

## 14. Build phases

Estimated effort is about 22 to 28 hours. Each phase ends with something runnable.

| Phase | Work | Done when | Est. |
|---|---|---|---|
| 0. Setup | Repo, venv, `.env`, `config.get_llm()`, hello-world LCEL chain | A prompt-to-answer chain runs from the terminal | 1.5 h |
| 1. Data | `generate_sample.py`, Parser rules, canonical schema, parser tests | Sample CSV loads into the canonical DataFrame and tests pass | 3 h |
| 2. Categorise | Keyword rules, few-shot LLM pass, cache | At least 90% of sample rows match their planted category | 2.5 h |
| 3. Tools | All pandas tools, calculator, golden-number tests | Every tool test passes without an LLM | 3.5 h |
| 4. Vector DB | Embeddings, both collections, knowledge-base notes, retrieval tools | Similarity search returns sensible hits for five test queries | 3 h |
| 5. Routing | Intent schema, router, rewriter, `RunnableBranch` | At least 27 of 30 router eval questions correct | 2.5 h |
| 6. Agents | Tool loop, Analysis Agent, Advisor Agent, reflection | Ten scripted questions give correct, grounded answers | 4.5 h |
| 7. UI | Dashboard, Chat, trace panel, reset | Full flow works from upload to answer in the browser | 3.5 h |
| 8. Harden | Error handling, rate-limit retries, empty states, README, demo rehearsal | A clean run of the demo script twice in a row | 2.5 h |
| 9. Stretch | PDF parsing, budgets, report export, Ollama toggle | Optional | as time allows |

Build order matters: phases 1 to 3 need no LLM at all, so the numeric core is solid before any prompt work begins.

---

## 15. Testing

| Area | Test | Target |
|---|---|---|
| Parser | Sample CSV, a CSV with separate debit and credit columns, a CSV with junk header lines, Indian number formats | All parse to the same schema |
| Tools | Each tool against planted golden numbers | Exact match |
| Calculator | Valid expressions, plus rejection of names, calls and attribute access | All pass |
| Categorizer | Planted labels in the sample | 90% or better |
| Router | `router_eval.csv`, 30 labelled questions | 90% or better |
| Agentic RAG | Five questions that should retrieve, five that should not | Decision correct in at least 8 of 10 |
| Grounding | Ten answers through the number check | No ungrounded numbers |
| End to end | Demo script | Two clean runs |

Tool and parser tests run without network access. LLM-dependent tests are marked so they can be skipped when offline.

---

## 16. Acceptance criteria mapping

| # | Criterion | Where it is shown | How to demonstrate |
|---|---|---|---|
| 1 | LLM integration | `config.get_llm()`, used by every agent | Sidebar shows provider and model; any chat answer |
| 2 | Interactive GUI | Streamlit Dashboard and Chat tabs | Upload, charts, chat |
| 3 | Prompt engineering | `prompts/prompts.py`; few-shot, CoT, ReAct, reflection | Trace expander shows system and user prompts and the reflection verdict |
| 4 | LangChain core components | `ChatPromptTemplate`, LCEL pipes, `RunnableBranch`, structured output | Open `core/router.py`; point to the prompt → model → parser pipe |
| 5 | Agentic RAG | Retrieval as tools the agent chooses to call | Ask one question that retrieves and one that does not; compare traces |
| 6 | Intent detection and routing | Router chain and branch | Ask three questions, one per intent; trace shows intent and handler |
| 7 | Vector database | Two ChromaDB collections | Trace shows retrieved chunks and similarity results |

---

## 17. Demo script (about five minutes)

1. Click "Use sample statement". Show the parse report and the Dashboard.
2. Point out a row categorised by rule and one categorised by the LLM.
3. Ask: **"How much did I spend on food in the latest month compared with the month before?"** Open the trace: intent `spending_analysis`, tool `compare_periods`, retrieval not used.
4. Ask: **"What was that big shopping payment?"** Trace: intent `transaction_lookup`, retrieval used on the transactions collection.
5. Ask: **"Where am I overspending and what should I do about it?"** Trace: intent `savings_guidance`, pandas tools plus guidance retrieval, cited note, reflection approved.
6. Follow up with **"And what about subscriptions?"** to show memory and the rewriter.
7. Open `prompts/prompts.py` and `core/router.py` briefly to show the system and user prompts and the LCEL chain.

---

## 18. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Free-tier rate limits during the demo | Rules-first categorising, merchant-level batching and caching, retry with backoff, Ollama as a backup |
| Model name retired or renamed | Model set in `.env`, checked at startup with a clear error |
| Model picks the wrong tool or bad arguments | Precise tool descriptions, typed arguments, validation errors returned to the model, five-step cap |
| Invented numbers | Tools-only arithmetic plus the number-grounding check |
| Messy real statements | CSV first, synonym table, LLM column mapping fallback, clear parse report |
| Duplicate vectors after Streamlit reruns | Collection named by file hash, built once per upload |
| First run downloads the embedding model | Run once before the demo so it is cached |
| Scope creep | Stretch items start only after section 16 passes in full |

---

## 19. Submission checklist

- [ ] App starts with `streamlit run app.py` from a clean clone
- [ ] `.env.example` present, no keys or real statements committed
- [ ] `requirements.txt` frozen from the working environment
- [ ] README covers setup, architecture diagram, criteria mapping, prompt list
- [ ] All seven criteria shown in the demo script
- [ ] Tests pass
- [ ] Demo rehearsed twice, with the backup model tested once
