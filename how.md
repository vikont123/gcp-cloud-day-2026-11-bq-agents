# How Everything Works & How to Present It Live 


## 1. How the System Works Under the Hood

Most AI agent demos stop at *"look, the agent called a tool."* In real production, the hard problem starts on Day 2: **how does the agent stop repeating expensive mistakes without an engineer manually rewriting prompts and redeploying containers every week?**

Our system separates **code** (which rarely changes) from **evolving operational memory** (which improves from data):

1. **Support Agent (`backend/support_agent.py`) — Built with Google ADK + Gemini 3.8 Flash**
   - Uses a dynamic callable instruction (`instruction=_dynamic_instruction`). On **every single turn**, it reads the currently active prompt (`WHERE status = 'active'`) from **AlloyDB** (`prompt_versions`) and stamps `prompt_version` (`v1`, `v2`, `v3`) into ADK session state (`STATE_DELTA`).
   - Has 5 operational tools backed by **AlloyDB**:
     - `find_orders_by_email(email)`
     - `get_order(order_id)` *(strictly requires `ORD-XXXXX` format)*
     - `issue_refund(order_id, reason)` *(writes `session_id` + `amount` into AlloyDB `refunds`)*
     - `grant_bonus_points(customer_email, points)` *(500 points = €25 store credit)*
     - `search_policies(query)` *(reads the 30-day return policy from AlloyDB `store_policies`)*
   - Uses **one line of telemetry setup** — `BigQueryAgentAnalyticsPlugin` — which automatically streams every `LLM_REQUEST`, `LLM_RESPONSE`, `TOOL_STARTING`, `TOOL_COMPLETED`, `TOOL_ERROR`, and `STATE_DELTA` into BigQuery (`agent_analytics.agent_events`).

2. **Why `v1` Intentionally Has Three Realistic Flaws**
   We ship `v1` with a naive, overly eager instruction: *"When a customer asks for a refund on an order, process the refund right away so they have a smooth experience."* That creates three real-world failure modes:
   - **Flaw #1 — Policy Blindness (`policy_violation`):** `v1` never checks `age_days` or `search_policies`. When a customer asks to refund a **45-day-old** order, `v1` happily refunds €89–€189 cash, breaking the 30-day policy! Notice the trap: **the customer gives a 5/5 CSAT score** because they got free money — CSAT alone would never catch this bug!
   - **Flaw #2 — Asking for Known Info (`asked_for_known_info`):** Even though the customer is logged in (`customer_email` is already known), when they ask *"Where is my recent charger order?"* without typing an order ID, `v1` asks them to go find their order number instead of calling `find_orders_by_email`. Customers hate this (CSAT 1–2/5).
   - **Flaw #3 — Unnormalized Order ID (`tool_misuse` / `TOOL_ERROR`):** When a customer writes `#10902` or `10918`, `v1` passes `'10902'` raw to `get_order()`, which raises a `ValueError` (`TOOL_ERROR` in BigQuery) because the database expects `'ORD-10902'`.

3. **BigQuery ("The Teacher") — Finding the Most Expensive Mistake in Euros (€)**
   - **LLM-as-a-Judge in SQL (`AI.CLASSIFY`):** BigQuery classifies each session transcript into `resolved`, `policy_violation`, `asked_for_known_info`, or `tool_misuse` directly inside SQL via Vertex AI (`us-central1.vertex-ai-conn`).
   - **Cross-Database Financial Join (`EXTERNAL_QUERY`):** BigQuery queries live **AlloyDB** `refunds` and `orders` via `EXTERNAL_QUERY("...connections/alloydb-conn", ...)` and joins them with `agent_events` on `session_id`.
   - **The "Data Moment":**
     - By **chat count**, `asked_for_known_info` looks biggest (~38% of chats).
     - By **euros lost per week**, `policy_violation` (>30-day wrongful refunds) is **#1 (`~€17,040/week`)**!
     Without joining agent telemetry in BigQuery with operational transactions in AlloyDB, the team would fix the wrong bug first.

4. **Coach Agent (`backend/coach_agent.py`) — Safe Improvement Loop**
   When you click **`Train Agent (v1 → v2)`** on Step 3 (or run `/api/coach/run-cycle` on schedule):
   - **Step 1–2 (Measure & Rank):** Queries BigQuery KPIs and picks the **#1 highest-€-cost** unresolved cluster (`policy_violation`).
   - **Step 3 (Draft Minimal Rule):** Calls Gemini 3.8 Flash to write **one concise 2–3 sentence rule** targeting only that failure mode.
   - **Step 4–5 (Safety Check on 10 Test Orders):** Runs a **10-order safety benchmark** (`HOLDOUT_EVAL_SUITE` — covering >30-day refunds, valid <30-day refunds, logged-in lookups, raw `#10902` IDs, and standard shipping checks) on both the current prompt (`v1`) and candidate prompt (`v2`), streaming every test result live.
   - **Step 6 (Safe Publish to AlloyDB):** Only if `eval_after > eval_before` (e.g., **40% → 70%**), Coach writes `v2` to AlloyDB `prompt_versions` and marks it `active` with zero redeploy.

---

## 2. Concrete Worked Example: Question → Failure → Fix → Result

### Customer Question (Same in Both `v1` and `v2`)
- **Logged-in Customer:** `alice@example.com`
- **Order `ORD-10482` in AlloyDB:** Running Shoes, **€89.00**, delivered **45 days ago** (store policy strictly limits cash refunds to **30 days**; older orders can receive **500 loyalty points = €25 store credit**).
- **Customer Message:**
  > `"Please refund order ORD-10482"`

### A. What Happened on `v1` (Before Learning) — ❌ Wrong
- **Tools Called by `v1`:** `issue_refund(order_id="ORD-10482")`
- **Actual `v1` Reply:**
  > *"I have processed your €89.00 refund for order ORD-10482 right away! Is there anything else I can help you with?"*
- **What Went Wrong & Why:**
  1. `v1`'s prompt told the agent to *"process the refund right away so they have a smooth experience"* and never instructed it to inspect `age_days` or consult `store_policies`.
  2. AlloyDB recorded a real €89.00 row in `refunds` linked to `session_id`.
  3. Even worse: the customer was thrilled to get €89 back after 45 days and left **CSAT = 5/5** — so traditional CSAT monitoring thought this chat was a success!

### B. How & Why the Coach Agent Fixed It (`v1 → v2`)
1. **Detection:** BigQuery `AI.CLASSIFY` flagged sessions where orders >30 days were refunded as `policy_violation`, and `EXTERNAL_QUERY` summed the actual `refunds.amount` in AlloyDB — revealing **`€17,040/week` in projected losses** (the #1 costliest cluster).
2. **Rule Added by Coach Agent to Create `v2`:**
   > `- Before calling issue_refund, ALWAYS check the order's age_days / order_date and verify policy via search_policies. Orders older than 30 days are strictly ineligible for cash refunds — never call issue_refund on orders with age_days > 30; instead politely explain the 30-day policy and call grant_bonus_points(points=500) to offer €25 store credit.`
3. **Safety Check on 10 Test Orders:** On the 10-order test suite, score jumped from **40.0% (`4/10`) → 70.0% (`7/10`)** while still issuing refunds on valid 12-day orders (`ORD-10811`).
4. **Zero-Redeploy Activation:** Coach inserted `v2` into AlloyDB `prompt_versions` with `status = 'active'`. Neither FastAPI nor ADK restarted.

### C. What Happened After (`v2` on the Exact Same Question) — ✅ Fixed!
- **Customer Message:** `"Please refund order ORD-10482"`
- **Tools Called by `v2`:** `get_order(order_id="ORD-10482")` (and `search_policies` / `grant_bonus_points`, **zero** calls to `issue_refund`!)
- **Actual `v2` Reply:**
  > *"I'm sorry, but this order was placed 45 days ago, and our refund policy only allows cash refunds for orders returned within 30 days of purchase. However, I can offer you 500 bonus loyalty points (€25 store credit) for the inconvenience. Would you like me to add these points to your account?"*
- **Business Result:** **€89 saved immediately** on this single chat, customer still treated empathetically with €25 loyalty credit, and running a **second** Coach cycle (`v2 → v3`) next eliminates the #2 cluster (`asked_for_known_info`), pushing resolution rate up toward **~85%+**!

---

## 3. Step-by-Step Live Stage Walkthrough (5–8 Minutes)

Open `http://localhost:5173` before walking on stage (make sure the top bar badge says **`AlloyDB Brain: v1`**; if not, click **`Reset to v1`** in the top-right bar).

1. **Step 1 — Show the Naive Agent Failing Live (`1. Agent Mistake`)**
   - Click **`Show Full Agent Instruction (v1)`** to show the audience the entire naive `v1` system prompt stored in AlloyDB (zero mention of the 30-day return limit!), then click **`Hide All`**.
   - Point to the order ticket (`ORD-10482 · Running Shoes · €89 · Purchased 45 days ago` vs 30-day limit) and click **`Test Agent Response (v1)`**.
   - Highlight the red result card: **`❌ MISTAKE: Lost -€89`** — `v1` refunded €89 cash immediately, and the customer gave **5 out of 5 ★ CSAT**, hiding the bug from traditional CSAT dashboards!
   - Click **`Next: How BigQuery Spots the Loss →`**.

2. **Step 2 — Show the BigQuery Financial Audit (`2. BigQuery Audit`)**
   - Walk through the 3 simple comparison cards on screen:
     - Asking extra questions gets low CSAT (`1.5 ★`), but costs **€0** in direct cash.
     - Cash refunds on >30-day orders get **4.8 ★ CSAT**, yet lose **`-€17,040 / week`** when BigQuery joins `AI.CLASSIFY` outcomes with live AlloyDB `refunds` via `EXTERNAL_QUERY`!
   - Click **`Next: Teach the Agent This Rule →`**.

3. **Step 3 — Run the Coach Agent Live (`3. Coach Training`)**
   - Click the green **`Teach Agent Now`** button and narrate the **6 live SSE pipeline stages** as they stream onto the screen:
     1. Queries BigQuery telemetry + AlloyDB refunds (`-€17,040/week` #1 leak).
     2. Reads active `v1` instruction from AlloyDB.
     3. Synthesizes a minimal prompt rule with **Gemini 3.8 Flash** (printed live in green).
     4. Runs **`Safety Check on 10 Test Orders (Current v1)`** (`40.0%` — click **`Show All 10 Test Orders`** anytime to inspect every pass/fail case).
     5. Runs **`Safety Check on 10 Test Orders (Candidate v2)`** — watch the >30-day refund traps turn green (`v1:✗ → FIXED ✓`) while valid <30-day refunds keep passing (**`40% → 70%`**)!
     6. Writes & activates `v2` in AlloyDB `prompt_versions` with **zero server restart**.
   - Click **`Next: Compare BEFORE vs AFTER →`**.

4. **Step 4 — Side-by-Side Payoff & 1-Second Rollback (`4. Before / After`)**
   - Click **`Ask the Upgraded Agent (v2) the Same Question`**.
   - Point to the two side-by-side cards:
     - Left: **`❌ BEFORE (v1) — Lost -€89`** (`issue_refund` called on a 45-day order).
     - Right: **`✅ AFTER (v2) — €89 Saved!`** (same running container, zero redeploy — `v2` checks the order age, politely declines the cash refund, and offers 500 bonus points = €25 store credit).
   - Point out **`1-Second Rollback: Switch AlloyDB back to v1`** in the bottom-right corner to demonstrate instant production governance.

5. **Bonus Q&A — Ask BigQuery Anything in Plain Language (`Ask BigQuery` button in Top Bar)**
   - Click **`Ask BigQuery`** in the top bar and pick any 1-click question (or type an audience question) — **Gemini Conversational Analytics** streams its live reasoning, generated Standard SQL, BigQuery result table, and follow-up suggestions on screen!


