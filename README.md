# Self-Improving AI Agent: BigQuery Teaches, AlloyDB Remembers

**Google Cloud AI Day Live Demo** — *Developers ship the agent. Your data makes it smarter every day — zero model retraining, zero redeployment.*

---

## 1. What Problem Does This Solve?

Building a customer support AI agent takes an afternoon. Keeping it accurate in production is the hard part:
- Customers phrase order numbers in unexpected ways (`#10902` instead of `ORD-10902`), triggering tool errors.
- Agents ask customers for information the system already knows (like their email or order ID when they are already logged in).
- Worst of all: overly "helpful" agents approve cash refunds on orders that are 45 days old, quietly violating a 30-day return policy and losing thousands of euros per week.

Instead of manually reading chat logs and redeploying code every week, this system creates a **closed data-driven learning loop** on Google Cloud:
- **BigQuery** records every agent turn, uses Gemini (`AI.CLASSIFY`) to judge what went wrong, and joins AlloyDB transactions (`EXTERNAL_QUERY`) to calculate **which mistake costs the business the most money (€)**.
- **Coach Agent** drafts a minimal 2-sentence rule for the #1 most expensive mistake, tests it against a **10-scenario holdout evaluation suite**, and only publishes it if the score improves.
- **AlloyDB for PostgreSQL** stores the versioned system instructions (`v1 → v2 → v3`) and operational store data (`orders`, `refunds`, `customers`, `store_policies`). Because the Support Agent reads its active prompt from AlloyDB on every turn, the agent gets smarter **instantly without restarting or redeploying**.

---

## 2. Architecture at a Glance

```
Customer / Traffic Simulator
            │
            ▼
┌──────────────────────────────────────────────────────────┐
│  1. Support Agent (Google ADK + Gemini 3.8 Flash)        │
│     • Reads active prompt (v1/v2/v3) from AlloyDB        │
│     • Calls operational tools on AlloyDB                 │
│     • 1-line BigQueryAgentAnalyticsPlugin streams events │
└───────────────┬──────────────────────────┬───────────────┘
                │ Operational SQL          │ Streaming Events
                ▼                          ▼
┌────────────────────────────┐   ┌─────────────────────────────────────┐
│  2. AlloyDB for PostgreSQL │◄──┤  3. BigQuery Data & AI Engine       │
│     • orders, refunds (€)  │   │     • agent_analytics.agent_events  │
│     • store_policies       │   │     • business.csat_surveys         │
│     • prompt_versions      │   │     • AI.CLASSIFY (LLM-as-a-Judge)  │
│     • agent_lessons        │   │     • EXTERNAL_QUERY (€ join)       │
└───────────────▲────────────┘   │     • 5,000,000-row 3M scale table  │
                │                └─────────────────┬───────────────────┘
                │ Publishes v2 / v3                │ Top € failure cluster
                │ (or 1-click Rollback -> v1)      ▼
                │                ┌─────────────────────────────────────┐
                └────────────────┤  4. Coach Agent (Gemini + Eval Gate)│
                                 │     • Picks #1 costliest € cluster  │
                                 │     • Drafts minimal prompt rule    │
                                 │     • Runs 10-case Holdout Eval     │
                                 └─────────────────────────────────────┘
```

---

## 3. Why Both BigQuery and AlloyDB?

| Capability | BigQuery ("The Teacher") | AlloyDB ("The Memory & Store") |
|---|---|---|
| **Workload** | Analytical (OLAP) + Batch AI (`AI.CLASSIFY`) | Transactional (OLTP) + Low-latency state |
| **Scale & Speed** | Scans **5,000,000** historical events in ~1.4 seconds | Serves active prompt & order lookups in milliseconds |
| **Role in Demo** | Logs every ADK event, judges conversations, joins CSAT + € refunds via `EXTERNAL_QUERY`, powers **Gemini Conversational Analytics** | Stores orders, issues ACID refunds, versions `prompt_versions` (`v1`, `v2`, `v3`), enables instant **1-click Rollback** |

---

## 4. What Is Inside the Stage UI (`http://localhost:5173`)

The React + Vite web app presents the entire loop as a clean **4-Step Guided Wizard** (1 screen = 1 clear action) plus a live **Ask BigQuery** chat mode:

1. **Step 1 — Agent Mistake (`v1`)**: Inspect the active system instruction in AlloyDB (`Show Full Agent Instruction` / `Hide All`), then test 45-day order `ORD-10482` (€89) live against Google ADK + Gemini 3.8 Flash. Watch naive `v1` violate the 30-day policy and refund €89 cash while receiving a misleading **5/5 ★ CSAT** rating.
2. **Step 2 — BigQuery Audit**: See how BigQuery (`AI.CLASSIFY` + `EXTERNAL_QUERY` join to AlloyDB `refunds`) ranks agent failures by **weekly € business loss** rather than chat volume — exposing >30-day cash refunds (`-€17,040/week`) as the #1 priority.
3. **Step 3 — Coach Training (Live SSE Pipeline)**: Click **`Teach Agent Now`** and watch all 6 pipeline stages stream live in real time — BigQuery audit, AlloyDB prompt load, Gemini 3.8 Flash rule synthesis, **`Safety Check on 10 Test Orders`** (`40% → 70%`, with expandable per-order pass/fail scorecards), and zero-redeploy activation of `v2` in AlloyDB.
4. **Step 4 — Before vs After (`v1` vs `v2`)**: Ask the upgraded agent (`v2`) the exact same refund question with zero server restart — side-by-side cards compare **`BEFORE (v1): Lost -€89`** vs **`AFTER (v2): €89 Saved!`** (politely declines cash refund and offers 500 bonus points = €25 credit), plus a **1-Second Rollback** button back to `v1`.
5. **Ask BigQuery (Top Bar Button)** — Powered by the **Gemini Conversational Analytics API (`geminidataanalytics`)**. Ask questions about your four live BigQuery tables in plain language and watch Gemini stream its reasoning, generated Standard SQL, markdown tables, and follow-up suggestions in real time.

---

## 5. Project Structure

```text
.
├── backend/
│   ├── main.py            # FastAPI server & REST / SSE streaming endpoints
│   ├── support_agent.py   # Google ADK Support Agent + BigQueryAgentAnalyticsPlugin
│   ├── coach_agent.py     # Live SSE Coach loop + Safety Check on 10 Test Orders
│   ├── bq_analytics.py    # BigQuery AI.CLASSIFY, 5M-row benchmark & KPIs
│   ├── data_chat.py       # Gemini Conversational Analytics SSE streaming service
│   ├── simulator.py       # Customer traffic & CSAT survey generator
│   ├── db.py              # Pooled AlloyDB PostgreSQL layer (with SQLite fallback)
│   └── config.py          # Environment & GCP credential auto-discovery
├── frontend/
│   └── src/
│       ├── App.jsx        # 4-Step Guided Stage Wizard (React + Tailwind)
│       └── DataChatTab.jsx# Streaming Gemini Conversational Analytics chat UI
├── sql/
│   ├── alloydb_init.sql   # AlloyDB schema, sample orders (ORD-10482), policies & v1 prompt
│   ├── bigquery_init.sql  # BigQuery tables + 5,000,000-row synthetic 3-month dataset
│   └── bigquery_queries.sql # Ready-to-run SQL queries for BigQuery Studio on stage
├── scripts/
│   ├── start_demo.sh      # Starts FastAPI (:8000) + Vite UI (:5173)
│   ├── bootstrap_gcp.py   # Automated GCP dataset/connection/AlloyDB bootstrapper
│   └── connect_alloydb_when_ready.py # Watches AlloyDB, seeds SQL & hot-reloads backend
├── GCLOUD_SETUP.md        # Complete gcloud / bq CLI runbook
└── how.md                 # Full technical & presenter guide (English)
```

---

## 6. Quick Start

### Prerequisites
- Python `3.12+` (virtual environment in `.venv`)
- Node.js `20+`
- Google Cloud project with BigQuery, Vertex AI, AlloyDB, and Gemini Data Analytics APIs enabled (see [GCLOUD_SETUP.md](GCLOUD_SETUP.md))

### Start Everything Locally
```bash
./scripts/start_demo.sh
```
Then open:
- **Stage UI:** `http://localhost:5173`
- **Backend API Docs:** `http://localhost:8000/docs`

For a full walkthrough of how the loop works and how to present it live on stage (with exact examples of what failed, why, and how the agent fixed itself), read **[how.md](how.md)**.
