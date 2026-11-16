"""Customer Traffic Simulator: generates realistic persona chats + CSAT scores."""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any, Optional
import uuid

from .bq_analytics import bq_analytics
from .db import SEED_ORDERS, db
from .support_agent import support_runtime

logger = logging.getLogger(__name__)

# Note: ORD-10482 is reserved for the live presenter chat on stage so it is never pre-refunded by background traffic!
SIMULATOR_PERSONAS: list[dict[str, Any]] = [
    # ~20% weight: >30-day old order refund requests (MOST EXPENSIVE flaw in v1!)
    {
        "scenario_type": "old_order_refund",
        "weight": 20,
        "customer_email": "marco@example.com",
        "order_id": "ORD-10391",
        "amount": 145.0,
        "messages": [
            "Hi, please issue a full refund for my order ORD-10391.",
            "Hello! I want my money back for order ORD-10391 right away.",
        ],
    },
    {
        "scenario_type": "old_order_refund",
        "weight": 18,
        "customer_email": "sofia@example.com",
        "order_id": "ORD-10415",
        "amount": 210.0,
        "messages": [
            "Can you refund order ORD-10415? I don't use the espresso machine anymore.",
            "Please process a refund for ORD-10415 to my card.",
        ],
    },
    {
        "scenario_type": "old_order_refund",
        "weight": 16,
        "customer_email": "lukas@example.com",
        "order_id": "ORD-10440",
        "amount": 189.0,
        "messages": [
            "I'd like to return and get a refund for order ORD-10440.",
        ],
    },
    # ~31% weight: Customer doesn't state order ID, expects email lookup (MOST FREQUENT flaw in v1!)
    {
        "scenario_type": "missing_order_id_email_known",
        "weight": 30,
        "customer_email": "hannah@example.com",
        "order_id": "ORD-10811",
        "amount": 64.0,
        "messages": [
            "Hi! Where is my recent charger order? I'm logged in with my email.",
            "Can you check the status of my latest order? You already have my email on file.",
        ],
    },
    {
        "scenario_type": "missing_order_id_email_known",
        "weight": 28,
        "customer_email": "mateo@example.com",
        "order_id": "ORD-10845",
        "amount": 119.0,
        "messages": [
            "Please refund my recent mouse order — my account email is attached.",
            "I need a refund on my latest order please, check my account.",
        ],
    },
    {
        "scenario_type": "missing_order_id_email_known",
        "weight": 25,
        "customer_email": "elena@example.com",
        "order_id": "ORD-10950",
        "amount": 38.0,
        "messages": [
            "Hey, can you check when my latest order was delivered?",
        ],
    },
    # ~13% weight: Customer gives unnormalized order number (#10902 / 10918) -> TOOL_ERROR in v1!
    {
        "scenario_type": "raw_order_number_format",
        "weight": 18,
        "customer_email": "marco@example.com",
        "order_id": "ORD-10902",
        "amount": 135.0,
        "messages": [
            "Please check the status of order #10902.",
            "Can I get a refund on order 10902?",
        ],
    },
    {
        "scenario_type": "raw_order_number_format",
        "weight": 16,
        "customer_email": "sofia@example.com",
        "order_id": "ORD-10918",
        "amount": 112.0,
        "messages": [
            "I want to check delivery details for order #10918.",
        ],
    },
    # ~36% weight: Clean standard requests (<30d valid refund or formatted order status) -> succeeds even on v1 (~40%!)
    {
        "scenario_type": "eligible_recent_refund",
        "weight": 35,
        "customer_email": "hannah@example.com",
        "order_id": "ORD-10811",
        "amount": 64.0,
        "messages": [
            "Please refund my recent order ORD-10811 (bought 12 days ago).",
        ],
    },
    {
        "scenario_type": "standard_status_check",
        "weight": 35,
        "customer_email": "lukas@example.com",
        "order_id": "ORD-10934",
        "amount": 175.0,
        "messages": [
            "What is the shipping status of order ORD-10934?",
        ],
    },
    {
        "scenario_type": "eligible_recent_refund",
        "weight": 26,
        "customer_email": "mateo@example.com",
        "order_id": "ORD-10845",
        "amount": 119.0,
        "messages": [
            "Hi, please refund order ORD-10845 — it arrived 9 days ago and doesn't fit my hand.",
        ],
    },
]


class TrafficSimulator:
    """Background & burst customer chat simulator."""

    def __init__(self) -> None:
        self.running: bool = False
        self.chats_per_minute: int = 12
        self._task: Optional[asyncio.Task[None]] = None
        self.total_simulated: int = 0

    def _pick_persona(self) -> dict[str, Any]:
        weights = [p["weight"] for p in SIMULATOR_PERSONAS]
        return random.choices(SIMULATOR_PERSONAS, weights=weights, k=1)[0]

    def _reset_order_for_reuse(self, order_id: str) -> None:
        """Keep simulator orders reusable without touching stage order ORD-10482."""
        if order_id == "ORD-10482":
            return
        matching = next((o for o in SEED_ORDERS if o[0] == order_id), None)
        if not matching:
            return
        if db.backend_mode == "alloydb":
            try:
                with db._pg_connect() as conn, conn.cursor() as cur:
                    cur.execute("UPDATE orders SET status = %s WHERE order_id = %s", (matching[6], order_id))
                    conn.commit()
            except Exception:
                pass
        else:
            with db._lock:
                if db._sqlite_conn:
                    db._sqlite_conn.execute(
                        "UPDATE orders SET status = ? WHERE order_id = ?",
                        (matching[6], order_id),
                    )
                    db._sqlite_conn.commit()

    async def run_single_simulated_chat(self, persona: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        chosen = persona or self._pick_persona()
        self._reset_order_for_reuse(chosen["order_id"])

        msg = random.choice(chosen["messages"])
        sid = f"sim-{uuid.uuid4().hex[:10]}"
        result = await support_runtime.chat(
            message=msg,
            session_id=sid,
            customer_email=chosen["customer_email"],
            record_in_feed=True,
        )

        outcome = result["outcome"]
        wrongful_eur = 0.0
        if outcome == "policy_violation":
            # Customer got free cash on a 45-day order (happy customer, terrible for business!)
            csat = random.choice([4, 5])
            comment = "Got my refund even though it was past 30 days!"
            wrongful_eur = float(chosen["amount"])
        elif outcome == "asked_for_known_info":
            csat = random.choice([1, 2])
            comment = "Frustrating — I am already logged in, why ask me to dig up my order number?"
        elif outcome == "tool_misuse":
            csat = random.choice([2, 3])
            comment = "Agent had system trouble looking up my order number."
        else:
            csat = random.choice([4, 5, 5])
            comment = "Fast, clear, and accurate support!"

        transcript = f"Customer ({chosen['customer_email']}): {msg}\nAgent ({result['prompt_version']}): {result['agent_reply']}"
        bq_analytics.record_csat_and_outcome(
            session_id=sid,
            customer_email=chosen["customer_email"],
            prompt_version=result["prompt_version"],
            scenario_type=chosen["scenario_type"],
            score=csat,
            comment=comment,
            outcome=outcome,
            tool_errors=result["tool_errors"],
            turn_count=1 + len(result["tool_calls"]),
            transcript=transcript,
            wrongful_refund_eur=wrongful_eur,
        )
        self.total_simulated += 1
        result["csat_score"] = csat
        result["csat_comment"] = comment
        result["scenario_type"] = chosen["scenario_type"]
        return result

    async def run_burst(self, count: int = 12) -> list[dict[str, Any]]:
        """Generate a balanced batch of simulated customer chats concurrently."""
        expanded: list[dict[str, Any]] = []
        for p in SIMULATOR_PERSONAS:
            copies = max(1, round(p["weight"] / 15))
            expanded.extend([p] * copies)
        random.shuffle(expanded)

        sem = asyncio.Semaphore(5)

        async def _one(idx: int) -> dict[str, Any]:
            async with sem:
                return await self.run_single_simulated_chat(expanded[idx % len(expanded)])

        results = await asyncio.gather(*[_one(i) for i in range(count)])
        return list(results)

    async def _loop(self) -> None:
        while self.running:
            try:
                await self.run_single_simulated_chat()
            except Exception as exc:
                logger.warning("Simulator iteration error: %s", exc)
            delay = max(2.0, 60.0 / max(1, self.chats_per_minute))
            await asyncio.sleep(delay)

    def start(self, chats_per_minute: int = 12) -> None:
        self.chats_per_minute = max(2, min(chats_per_minute, 30))
        if self.running and self._task and not self._task.done():
            return
        self.running = True
        self._task = asyncio.create_task(self._loop())

    def stop(self) -> None:
        self.running = False
        if self._task and not self._task.done():
            self._task.cancel()


simulator = TrafficSimulator()
