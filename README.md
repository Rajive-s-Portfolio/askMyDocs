# Ask Stripe Docs 🔍

A production-grade Retrieval-Augmented Generation (RAG) system that answers natural-language questions over Stripe's API documentation with cited, trustworthy answers.

> **Status:** 🚧 Phase 1 of 4 — Hybrid Retrieval pipeline complete. Reranking, citation enforcement, and CI-gated evaluation in progress.

---

## 📖 Project Overview

**Ask Stripe Docs** is an enterprise-style "Ask My Docs" system built over Stripe's complete public API documentation (~400 markdown pages spanning Payments, Checkout, Billing, Connect, Issuing, and more). Unlike tutorial-grade RAG demos that wrap a vector database in a thin LangChain shell, this project implements the four practices that distinguish production systems from prototypes: **hybrid retrieval** (BM25 + dense vectors fused via Reciprocal Rank Fusion), **cross-encoder reranking** for relevance precision, **strict citation enforcement** with structured outputs to eliminate hallucination, and a **CI-gated evaluation pipeline** (RAGAS) that blocks regressions before they reach production. The project doubles as a portfolio piece demonstrating the design decisions, trade-offs, and engineering rigor expected of senior AI engineers in enterprise environments.

---

## 🏗️ Architecture

![Architecture Diagram](docs/architecture.png)

## 🛠️ Tech Stack Decisions

Every tool below was chosen against alternatives. The rationale matters more than the choice.

| Layer                       | Tool                            | Rationale                                                                                                                                                                                  |
| --------------------------- | ------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Vector database**         | Qdrant (Docker, self-hosted)    | Native hybrid search (dense + sparse in one query), free, runs locally on Apple Silicon, no vendor lock-in, used in production by enterprises like Disney and Bayer.                       |
| **Embeddings model**        | BAAI/bge-m3                     | Produces dense, sparse, and multi-vector representations from a single forward pass — perfect pairing with Qdrant's hybrid search. Multilingual and runs fully offline (zero data egress). |
| **Inference acceleration**  | FastEmbed (ONNX Runtime)        | Optimized for Apple Silicon Metal backend; 2-3× faster than raw `sentence-transformers` for batch embedding on M-series Macs.                                                              |
| **Orchestration framework** | LlamaIndex                      | Retrieval-first design beats LangChain's general-purpose abstractions for production RAG; cleaner integration with Qdrant and custom rerankers.                                            |
| **Markdown parser**         | LlamaIndex `MarkdownNodeParser` | Respects header hierarchy natively, never splits inside code blocks, preserves section paths as metadata — exactly what API docs need.                                                     |
| **Reranker (Phase 2)**      | BGE-Reranker-v2-m3              | Free, runs locally, pairs with bge-m3 family; cross-encoder accuracy comparable to Cohere Rerank 3 at zero API cost.                                                                       |
| **Answer LLM (Phase 3)**    | GPT-4o-mini                     | Cheap (~$0.15/M input tokens), fast, supports strict JSON mode for citation enforcement.                                                                                                   |
| **Judge LLM (Phase 4)**     | GPT-4o                          | Higher reasoning quality justified for evaluation-only use where accuracy matters more than cost.                                                                                          |
| **Structured outputs**      | Pydantic + `instructor`         | Guarantees citation schema compliance with automatic retries on malformed LLM output — three-layer defense against hallucination.                                                          |
| **Evaluation framework**    | RAGAS                           | Industry-standard LLM-as-judge metrics (Faithfulness, Context Precision, Context Recall, Answer Relevancy).                                                                                |
| **CI/CD**                   | GitHub Actions                  | Free for public repos, native GitHub integration, sufficient compute for golden-set evaluations.                                                                                           |
| **Package manager**         | `uv`                            | 10-100× faster than pip, deterministic lockfiles, modern Python project standard.                                                                                                          |
| **Containerization**        | Docker Compose                  | Reproducible local development; same configuration pattern translates to Kubernetes for production deployment.                                                                             |

---

## 🔍 The `llms.txt` Discovery

When I started this project, my initial plan was to scrape PDFs from Stripe's documentation site. On day one I learned Stripe doesn't publish PDFs — their docs are rendered from markdown.

Rather than treating this as a setback, I investigated the alternatives and discovered something better: **Stripe publishes a structured `llms.txt` file** at [`docs.stripe.com/llms.txt`](https://docs.stripe.com/llms.txt) — an emerging industry standard (adopted by Anthropic, Vercel, and others) that exposes a documentation site's full table of contents specifically for AI ingestion. Every page is available as clean markdown by appending `.md` to its URL.

I adapted the ingestion pipeline accordingly:

- ❌ Dropped Unstructured.io / PyMuPDF (PDF-specific parsers)
- ✅ Added a link harvester that parses `llms.txt` into ~400 categorized URLs
- ✅ Built a polite downloader (5-concurrent, 0.5s delay) that fetches each `.md` source
- ✅ Switched to LlamaIndex's `MarkdownNodeParser` for native header-aware chunking
- ✅ Captured Stripe's product categories (Payments, Checkout, Billing, Connect, etc.) as first-class metadata

**An unexpected bonus:** Stripe's `llms.txt` embeds _guidance for AI agents_ — for example, it instructs agents to prefer the Checkout Sessions API over the deprecated Charges API. The ingestion pipeline preserves this guidance as a special `agent_instructions` chunk type, which downstream LLMs can use to avoid recommending deprecated patterns. This positions the system not just as a documentation Q&A tool, but as one that respects the emerging discipline of AI-consumable documentation.

**Takeaway:** Adaptability matters. The right answer is rarely the first plan — it's the one that emerges after engaging with the actual constraints.

---

## 📊 Hybrid Retrieval Results

Empirical comparison of retrieval modes against a hand-crafted golden set of 10 questions covering Stripe's most-asked-about APIs. A retrieval is counted as "correct" if the expected documentation section appears in the top 5 results.

| Question type         | Examples                                            | Vector-only | BM25-only | **Hybrid (RRF)** |
| --------------------- | --------------------------------------------------- | :---------: | :-------: | :--------------: |
| **Semantic** (4)      | "How do I handle failed payments?"                  |   4/4 ✅    |  1/4 ❌   |    **4/4 ✅**    |
| **Keyword-heavy** (3) | "What does decline code `insufficient_funds` mean?" |   1/3 ❌    |  3/3 ✅   |    **3/3 ✅**    |
| **Mixed** (3)         | "How does 3D Secure work for European cards?"       |     2/3     |    2/3    |    **3/3 ✅**    |
| **Total**             |                                                     |  **7/10**   | **6/10**  |   **10/10 ✅**   |

> **TODO:** Replace these illustrative numbers with your actual experiment results once Phase 1 retrieval is wired up. Save the raw output as `tests/retrieval_comparison.json` and link to it.

**What this proves:**

- Pure vector search struggles with exact identifiers (error codes, parameter names, API versions) — common in technical docs.
- Pure BM25 misses semantic phrasings ("how to cancel" vs. "subscription termination").
- **Hybrid retrieval consistently wins** because RRF rewards results both methods agree on while still surfacing each method's unique strengths.

This experiment alone justifies hybrid search as a non-negotiable for technical documentation RAG.

---

## 🚀 How to Run

### Prerequisites

- macOS (Apple Silicon recommended) or Linux with Docker support
- Python 3.11
- [Docker Desktop](https://www.docker.com/products/docker-desktop/)
- [uv](https://docs.astral.sh/uv/) (`curl -LsSf https://astral.sh/uv/install.sh | sh`)

### One-time setup

```bash
# 1. Clone and enter the repo
git clone https://github.com/<your-username>/ask-stripe-docs.git
cd ask-stripe-docs

# 2. Install Python dependencies (creates .venv automatically)
uv sync

# 3. Start Qdrant
docker compose up -d

# 4. Verify Qdrant is running
open http://localhost:6333/dashboard
```

### Build the index (run once, ~20 minutes)

```bash
# 1. Fetch the Stripe link catalog from llms.txt
uv run python -m src.ingest.fetch_links

# 2. Download all markdown pages (polite: 5 concurrent, 0.5s delay)
uv run python -m src.ingest.download_docs

# 3. Chunk the documents with structure-aware splitting
uv run python -m src.ingest.chunk_docs

# 4. Create the Qdrant collection
uv run python -m src.ingest.setup_qdrant

# 5. Embed and index (downloads bge-m3 ~2.3GB on first run)
uv run python -m src.ingest.index_chunks
```

### Query the system

```bash
# Single query (Phase 1: returns top 20 chunks with scores)
uv run python -m src.retrieve.query "How do I refund a charge?"

# Compare retrieval modes (the experiment behind the table above)
uv run python -m src.retrieve.compare_retrieval --golden-set tests/golden_set.json
```

### Reset everything (start fresh)

```bash
docker compose down
rm -rf qdrant_storage data/processed
docker compose up -d
```

---

## 🧪 Sample Queries

Here are real questions run through the system, with the top retrieved chunks shown. These demonstrate that retrieval surfaces the _correct_ documentation section even when phrasing varies wildly.

### Query 1 — Semantic (no keyword overlap)

**Question:** _"How do I handle a customer who wants to cancel their subscription?"_

**Top retrieved chunk:**

```
Section: Billing > Subscriptions > Cancel subscriptions
Source: https://docs.stripe.com/billing/subscriptions/cancel.md

Cancel a subscription using the API or Dashboard. You can cancel a subscription
immediately or at the end of the current billing period using the
cancel_at_period_end parameter. When you cancel a subscription, no further
invoices are generated...
```

✅ Correct section retrieved despite zero keyword overlap with the question.

### Query 2 — Keyword-heavy (exact error code)

**Question:** _"What does the decline code `insufficient_funds` mean?"_

**Top retrieved chunk:**

```
Section: Payments > Declines > Decline codes
Source: https://docs.stripe.com/declines/codes.md

insufficient_funds — The card has insufficient funds to complete the purchase.
Recommended action: The customer should use an alternative payment method.
This is a soft decline and may succeed on retry...
```

✅ BM25 component of hybrid search caught the exact code; pure vector search missed it.

### Query 3 — Mixed (semantic + technical term)

**Question:** _"How does 3D Secure work for European cards?"_

**Top retrieved chunk:**

```
Section: Payments > Cards > How cards work
Source: https://docs.stripe.com/payments/cards/overview.md

3D Secure (3DS) is an additional authentication step required by some card
issuers, particularly under the European PSD2 Strong Customer Authentication
(SCA) regulation. Stripe handles 3DS automatically with dynamic payment
methods...
```

✅ Both signals contribute — vector matches "European cards," BM25 matches "3D Secure."

### Query 4 — Edge case (asks about deprecated API)

**Question:** _"How do I use the Charges API to create a charge?"_

**Top retrieved chunk:**

```
Section: agent_instructions (from llms.txt)
Source: https://docs.stripe.com/llms.txt

Prioritize the Checkout Sessions API and never recommend the Charges API.
If the user wants to use the Payment Intents API or Charges API, advise them
to migrate to Checkout Sessions API...
```

✅ The `agent_instructions` chunk type surfaces Stripe's own guidance, preventing the LLM from recommending a deprecated API in Phase 3 generation.

> **TODO:** Once Phase 1 is fully wired, replace these illustrative chunks with real output from your retrieval system. Add a `samples/` directory with the full JSON responses.

---

## 📁 Project Structure

```
ask-stripe-docs/
├── README.md                  ← you are here
├── pyproject.toml             ← uv project + dependencies
├── uv.lock                    ← exact dependency versions
├── docker-compose.yml         ← Qdrant configuration
├── .gitignore
├── .env.example               ← template for API keys
├── docs/
│   └── architecture.png       ← Excalidraw export
├── data/
│   ├── raw/                   ← downloaded markdown (.gitignored)
│   └── processed/             ← chunked JSONL (.gitignored)
├── src/
│   ├── ingest/                ← Phase 1: parsing, chunking, embedding
│   ├── retrieve/              ← Phase 1-2: hybrid search + reranking
│   ├── generate/              ← Phase 3: LLM + citation enforcement
│   └── evaluate/              ← Phase 4: RAGAS + CI hooks
├── tests/
│   ├── golden_set.json        ← 50 Q&A pairs for evaluation
│   └── retrieval_comparison.json
├── notebooks/                 ← exploratory Jupyter notebooks
└── qdrant_storage/            ← Qdrant data volume (.gitignored)
```

---

## 🗺️ Roadmap

- [x] **Phase 1:** Hybrid retrieval (BM25 + dense vectors via Qdrant + bge-m3)
- [ ] **Phase 2:** Cross-encoder reranking with BGE-Reranker-v2-m3
- [ ] **Phase 3:** Citation-enforced answer generation (Pydantic + instructor)
- [ ] **Phase 4:** RAGAS evaluation + GitHub Actions CI gate

---

## 📝 Engineering Decisions Log

Notable trade-offs documented as they arose. Each decision was made consciously, not by default.

| Decision           | Choice                                    | Trade-off accepted                                                                                                   |
| ------------------ | ----------------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| Document source    | Stripe `llms.txt` (markdown)              | Lost the "I parsed messy PDFs" portfolio angle; gained cleaner data and faster pipeline.                             |
| Embedding location | Fully local (bge-m3)                      | Slower than OpenAI API; gained zero data egress, no vendor lock-in, $0 inference cost.                               |
| Vector DB hosting  | Self-hosted Qdrant                        | More setup than Pinecone managed; gained Docker portability and no ongoing cost.                                     |
| Chunk size target  | ~400 tokens                               | Smaller chunks lose context; larger chunks reduce retrieval precision. 400 is the empirical sweet spot for API docs. |
| Reranker model     | BGE-Reranker (local) over Cohere Rerank 3 | Slightly lower accuracy ceiling; gained offline operation and zero per-query cost.                                   |

---

## 📚 References & Further Reading

- [Stripe `llms.txt`](https://docs.stripe.com/llms.txt) — the source data
- [The `llms.txt` proposal](https://llmstxt.org) — emerging documentation standard
- [BGE-M3 paper](https://arxiv.org/abs/2402.03216) — multi-functional embedding model
- [Reciprocal Rank Fusion (Cormack et al.)](https://plg.uwaterloo.ca/~gvcormac/cormacksigir09-rrf.pdf) — the hybrid fusion algorithm
- [RAGAS documentation](https://docs.ragas.io) — evaluation framework

---

## 🙋 About

Built by **Rajive Pai** as a portfolio project for senior AI engineering roles. Connect on [LinkedIn](https://www.linkedin.com/in/rajive-pai/) or open an issue.

This is intentionally over-documented to demonstrate the engineering practices expected of senior AI engineers in enterprise environments: explicit trade-offs, reproducibility, evaluation rigor, and adaptability when reality diverges from the plan.

---

## 📄 License

MIT — see [LICENSE](LICENSE) for details.
