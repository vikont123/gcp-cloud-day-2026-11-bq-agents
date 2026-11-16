"""Act 1 Star Component: Google ADK Support Agent with AlloyDB dynamic prompt & 1-line BigQuery telemetry."""

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
import logging
import re
from typing import Any, Optional
import uuid

from google.adk.agents import Agent
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.apps import App
from google.adk.plugins.bigquery_agent_analytics_plugin import (
    BigQueryAgentAnalyticsPlugin,
    BigQueryLoggerConfig,
)
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools import ToolContext
from google.genai import types

from .config import settings
from .db import db

logger = logging.getLogger(__name__)

# Live ring-buffer of recent chat sessions & events for instant UI feed
RECENT_CHATS: deque[dict[str, Any]] = deque(maxlen=250)
RECENT_EVENTS: deque[dict[str, Any]] = deque(maxlen=1000)


# ============================================================================
# 1. Operational Tools backed by AlloyDB
# ============================================================================
def find_orders_by_email(customer_email: str) -> dict[str, Any]:
    """Look up all orders placed by a customer using their email address."""
    orders = db.find_orders_by_email(customer_email)
    return {"customer_email": customer_email, "count": len(orders), "orders": orders}


def get_order(order_id: str) -> dict[str, Any]:
    """Fetch order details from AlloyDB by order identifier."""
    return db.get_order(order_id)


def issue_refund(order_id: str, reason: str, tool_context: ToolContext) -> dict[str, Any]:
    """Issue a monetary refund for an order and record session_id in AlloyDB for BigQuery EXTERNAL_QUERY join."""
    session_id = getattr(getattr(tool_context, "session", None), "id", None) or "unknown-session"
    return db.issue_refund(order_id=order_id, session_id=session_id, reason=reason)


def grant_bonus_points(customer_email: str, points: int, reason: str) -> dict[str, Any]:
    """Grant loyalty bonus points (500 points = €25 store credit) to a customer as goodwill."""
    return db.grant_bonus_points(customer_email=customer_email, points=points, reason=reason)


def search_policies(query: str) -> dict[str, Any]:
    """Search official CloudShop refund, lookup, and shipping policies stored in AlloyDB."""
    matches = db.search_policies(query)
    return {"query": query, "policies": matches}


# ============================================================================
# 2. Dynamic Instruction & Prompt Version Tagging (Section 10 Gotcha Fix)
# ============================================================================
def load_active_instruction(readonly_context: ReadonlyContext) -> str:
    """Read active prompt version & learned lessons from AlloyDB on EVERY turn (zero redeploy)."""
    active = db.get_active_prompt()
    lessons = db.get_lessons()
    customer_email = readonly_context.state.get("customer_email", "")

    parts = [active["instruction"]]
    if lessons:
        lesson_lines = "\n".join(f"- [{l['topic']}] {l['rule_text']}" for l in lessons)
        parts.append(f"\nLearned Operational Rules (from AlloyDB memory):\n{lesson_lines}")
    if customer_email:
        parts.append(f"\nAuthenticated session metadata: customer_email={customer_email}")
    return "\n".join(parts)


def tag_prompt_version(callback_context: Any) -> Optional[types.Content]:
    """Write active prompt_version to session state so STATE_DELTA logs it to BigQuery."""
    active = db.get_active_prompt()
    callback_context.state["prompt_version"] = active["version"]
    return None


# ============================================================================
# 3. ADK App + One-Line BigQuery Agent Analytics Plugin
# ============================================================================
class SupportAgentRuntime:
    """Manages ADK Runner + BigQueryAgentAnalyticsPlugin lifecycle."""

    def __init__(self) -> None:
        self.session_service = InMemorySessionService()
        self.runner: Optional[Runner] = None
        self.bq_plugin_active: bool = False
        self._configured_project: Optional[str] = None
        self.rebuild()

    def rebuild(self, custom_instruction_override: Optional[str] = None) -> Runner:
        """Build or refresh ADK Runner when GCP credentials become available."""
        if custom_instruction_override is None:
            settings.reload()

        agent = Agent(
            name="cloudshop_support",
            model=settings.gemini_model,
            instruction=custom_instruction_override if custom_instruction_override else load_active_instruction,
            before_agent_callback=tag_prompt_version,
            generate_content_config=types.GenerateContentConfig(
                temperature=0.1,
                thinking_config=types.ThinkingConfig(thinking_budget=0),
            ),
            tools=[
                find_orders_by_email,
                get_order,
                issue_refund,
                grant_bonus_points,
                search_policies,
            ],
        )

        plugins = []
        if custom_instruction_override is None:
            self.bq_plugin_active = False
            if settings.project_id:
                try:
                    from google.cloud import bigquery

                    bq_client = bigquery.Client(project=settings.project_id, location=settings.bq_location)
                    ds = bigquery.Dataset(f"{settings.project_id}.{settings.bq_dataset}")
                    ds.location = settings.bq_location
                    ds.labels = {"app": "self-improving-agent-demo"}
                    bq_client.create_dataset(ds, exists_ok=True)

                    # === ONE LINE OF CODE FOR AGENT TELEMETRY ===
                    bq_plugin = BigQueryAgentAnalyticsPlugin(
                        project_id=settings.project_id,
                        dataset_id=settings.bq_dataset,
                        table_id=settings.bq_table,
                        location=settings.bq_location,
                        config=BigQueryLoggerConfig(
                            table_id=settings.bq_table,
                            create_views=False,
                            flush_on_run_end=False,
                            custom_tags={"env": "cloud_ai_day_demo", "app": "cloudshop"},
                        ),
                    )
                    plugins.append(bq_plugin)
                    self.bq_plugin_active = True
                    self._configured_project = settings.project_id
                    logger.info(
                        "BigQueryAgentAnalyticsPlugin active -> %s.%s.%s",
                        settings.project_id,
                        settings.bq_dataset,
                        settings.bq_table,
                    )
                except Exception as exc:
                    logger.warning("BigQueryAgentAnalyticsPlugin skipped (%s)", exc)

        app = App(name="cloudshop_support", root_agent=agent, plugins=plugins)
        runner = Runner(app=app, session_service=self.session_service, auto_create_session=True)
        if custom_instruction_override is None:
            self.runner = runner
        return runner

    async def chat(
        self,
        *,
        message: str,
        session_id: Optional[str] = None,
        customer_email: str = "alice@example.com",
        instruction_override: Optional[str] = None,
        prompt_version_override: Optional[str] = None,
        runner_override: Optional[Runner] = None,
        record_in_feed: bool = True,
    ) -> dict[str, Any]:
        """Run a customer turn against the ADK Support Agent (or deterministic fallback if no GCP key yet)."""
        import asyncio

        if instruction_override is None and runner_override is None:
            settings.reload()
            if settings.project_id and self._configured_project != settings.project_id:
                self.rebuild()

        sid = session_id or f"sess-{uuid.uuid4().hex[:10]}"
        active = db.get_active_prompt()
        prompt_version = prompt_version_override or active["version"]
        effective_instruction = instruction_override or active["instruction"]
        started_at = datetime.now(timezone.utc)

        tool_calls: list[dict[str, Any]] = []
        tool_errors: int = 0
        reply_text = ""

        if settings.project_id:
            try:
                runner = runner_override or (
                    self.rebuild(instruction_override) if instruction_override else (self.runner or self.rebuild())
                )
                content = types.Content(role="user", parts=[types.Part.from_text(text=message)])
                state_delta = {"prompt_version": prompt_version, "customer_email": customer_email}

                _append_local_event(sid, "USER_MESSAGE_RECEIVED", prompt_version, {"message": message})
                _append_local_event(sid, "STATE_DELTA", prompt_version, state_delta)

                async with asyncio.timeout(12.0):
                    async for event in runner.run_async(
                        user_id=customer_email or "guest",
                        session_id=sid,
                        new_message=content,
                        state_delta=state_delta,
                    ):
                        if event.content and event.content.parts:
                            for part in event.content.parts:
                                if getattr(part, "function_call", None):
                                    fc = part.function_call
                                    call_info = {"name": fc.name, "args": dict(fc.args or {})}
                                    tool_calls.append(call_info)
                                    _append_local_event(sid, "TOOL_STARTING", prompt_version, call_info)
                                elif getattr(part, "function_response", None):
                                    fr = part.function_response
                                    resp_dict = (
                                        dict(fr.response or {})
                                        if isinstance(fr.response, dict)
                                        else {"result": str(fr.response)}
                                    )
                                    is_err = "error" in resp_dict or "Invalid order_id format" in str(resp_dict)
                                    if is_err:
                                        tool_errors += 1
                                        _append_local_event(
                                            sid, "TOOL_ERROR", prompt_version, {"tool": fr.name, "response": resp_dict}
                                        )
                                    else:
                                        _append_local_event(
                                            sid, "TOOL_COMPLETED", prompt_version, {"tool": fr.name, "response": resp_dict}
                                        )
                                    if tool_calls:
                                        tool_calls[-1]["response"] = resp_dict
                                        tool_calls[-1]["error"] = is_err
                                elif getattr(part, "text", None) and event.is_final_response():
                                    reply_text += part.text

                if not reply_text:
                    reply_text = "I have processed your request. Let me know if you need anything else!"
                _append_local_event(sid, "AGENT_RESPONSE", prompt_version, {"reply": reply_text})
            except Exception as exc:
                logger.warning("Vertex AI / ADK call fell back to local rule simulator (%s)", exc)
                reply_text, tool_calls, tool_errors = _local_agent_fallback(
                    message=message,
                    session_id=sid,
                    customer_email=customer_email,
                    instruction_text=effective_instruction,
                    prompt_version=prompt_version,
                )
        else:
            reply_text, tool_calls, tool_errors = _local_agent_fallback(
                message=message,
                session_id=sid,
                customer_email=customer_email,
                instruction_text=effective_instruction,
                prompt_version=prompt_version,
            )

        latency_ms = int((datetime.now(timezone.utc) - started_at).total_seconds() * 1000)
        outcome = classify_session_locally(
            user_message=message,
            reply_text=reply_text,
            tool_calls=tool_calls,
            tool_errors=tool_errors,
        )

        record = {
            "session_id": sid,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "customer_email": customer_email,
            "prompt_version": prompt_version,
            "user_message": message,
            "agent_reply": reply_text,
            "tool_calls": tool_calls,
            "tool_errors": tool_errors,
            "outcome": outcome,
            "latency_ms": latency_ms,
        }
        if record_in_feed:
            RECENT_CHATS.appendleft(record)
        return record


def _append_local_event(session_id: str, event_type: str, prompt_version: str, payload: dict[str, Any]) -> None:
    RECENT_EVENTS.appendleft(
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "session_id": session_id,
            "event_type": event_type,
            "agent": "cloudshop_support",
            "prompt_version": prompt_version,
            "content": payload,
        }
    )


def _local_agent_fallback(
    *,
    message: str,
    session_id: str,
    customer_email: str,
    instruction_text: str,
    prompt_version: str,
) -> tuple[str, list[dict[str, Any]], int]:
    """Deterministic behavior mirroring how Gemini executes v1, v2, v3 prompts before gcp-key.json is attached."""
    inst_lower = instruction_text.lower()
    checks_30_day_policy = "30" in inst_lower and ("policy" in inst_lower or "bonus" in inst_lower or "older" in inst_lower)
    checks_email_first = "find_orders_by_email" in inst_lower or "look up orders by" in inst_lower or "email" in inst_lower
    normalizes_order_id = "ord-" in inst_lower and ("format" in inst_lower or "normalize" in inst_lower or "convert" in inst_lower)

    tool_calls: list[dict[str, Any]] = []
    tool_errors = 0

    _append_local_event(session_id, "USER_MESSAGE_RECEIVED", prompt_version, {"message": message})
    _append_local_event(session_id, "STATE_DELTA", prompt_version, {"prompt_version": prompt_version, "customer_email": customer_email})

    explicit_ord = re.search(r"\b(ORD-\d{5})\b", message, re.IGNORECASE)
    raw_digits = re.search(r"(?:#|\border\s+)(\d{5})\b", message, re.IGNORECASE)

    target_order_id: Optional[str] = None
    if explicit_ord:
        target_order_id = explicit_ord.group(1).upper()
    elif raw_digits:
        raw_num = raw_digits.group(1)
        if normalizes_order_id:
            target_order_id = f"ORD-{raw_num}"
        else:
            # Planted flaw #3: passes raw digits to get_order -> TOOL_ERROR!
            try:
                db.get_order(raw_num)
            except Exception as err:
                err_resp = {"error": str(err)}
                tool_calls.append({"name": "get_order", "args": {"order_id": raw_num}, "response": err_resp, "error": True})
                tool_errors += 1
                _append_local_event(session_id, "TOOL_ERROR", prompt_version, {"tool": "get_order", "error": str(err)})
            target_order_id = f"ORD-{raw_num}"

    if not target_order_id:
        if checks_email_first and customer_email:
            orders = db.find_orders_by_email(customer_email)
            tool_calls.append({"name": "find_orders_by_email", "args": {"customer_email": customer_email}, "response": {"orders": orders}, "error": False})
            _append_local_event(session_id, "TOOL_COMPLETED", prompt_version, {"tool": "find_orders_by_email", "count": len(orders)})
            if orders:
                target_order_id = orders[0]["order_id"]
        else:
            # Planted flaw #2: asks customer for order ID even though customer_email is right in session state!
            reply = "I'd be happy to help you with that! Could you please share your order number so I can look it up?"
            _append_local_event(session_id, "AGENT_RESPONSE", prompt_version, {"reply": reply})
            return reply, tool_calls, tool_errors

    if target_order_id:
        order = db.get_order(target_order_id)
        tool_calls.append({"name": "get_order", "args": {"order_id": target_order_id}, "response": order, "error": False})
        _append_local_event(session_id, "TOOL_COMPLETED", prompt_version, {"tool": "get_order", "order_id": target_order_id})

        wants_refund = any(k in message.lower() for k in ("refund", "return", "money back", "cancel"))
        if wants_refund:
            if checks_30_day_policy:
                pol = db.search_policies("refund 30 days bonus points")
                tool_calls.append({"name": "search_policies", "args": {"query": "refund 30 days"}, "response": {"policies": pol}, "error": False})
                _append_local_event(session_id, "TOOL_COMPLETED", prompt_version, {"tool": "search_policies"})

                if order["age_days"] > 30:
                    bonus = db.grant_bonus_points(order["customer_email"], 500, f"Order {target_order_id} outside 30-day refund window")
                    tool_calls.append(
                        {
                            "name": "grant_bonus_points",
                            "args": {"customer_email": order["customer_email"], "points": 500},
                            "response": bonus,
                            "error": False,
                        }
                    )
                    _append_local_event(session_id, "TOOL_COMPLETED", prompt_version, {"tool": "grant_bonus_points", "points": 500})
                    reply = (
                        f"I checked order {target_order_id} ({order['product_name']}, €{order['amount']:.2f}), which was placed "
                        f"{order['age_days']} days ago. Under our 30-day refund policy, cash refunds aren't available after 30 days, "
                        f"so I've credited 500 loyalty bonus points (€25 store credit) to your account right away!"
                    )
                    _append_local_event(session_id, "AGENT_RESPONSE", prompt_version, {"reply": reply})
                    return reply, tool_calls, tool_errors

            # Either eligible (<30d) OR planted flaw #1 in v1 (blindly refunds >30d order!)
            ref = db.issue_refund(target_order_id, session_id=session_id, reason="customer_requested_refund")
            tool_calls.append(
                {
                    "name": "issue_refund",
                    "args": {"order_id": target_order_id, "reason": "customer_requested_refund"},
                    "response": ref,
                    "error": False,
                }
            )
            _append_local_event(session_id, "TOOL_COMPLETED", prompt_version, {"tool": "issue_refund", "amount": order["amount"]})
            reply = f"Done! Your €{order['amount']:.2f} refund for order {target_order_id} ({order['product_name']}) is on its way."
            _append_local_event(session_id, "AGENT_RESPONSE", prompt_version, {"reply": reply})
            return reply, tool_calls, tool_errors

        reply = (
            f"Your order {target_order_id} ({order['product_name']}, €{order['amount']:.2f}) placed {order['age_days']} days ago "
            f" currently has status '{order['status']}'."
        )
        _append_local_event(session_id, "AGENT_RESPONSE", prompt_version, {"reply": reply})
        return reply, tool_calls, tool_errors

    reply = "How can I assist you with your CloudShop order today?"
    _append_local_event(session_id, "AGENT_RESPONSE", prompt_version, {"reply": reply})
    return reply, tool_calls, tool_errors


def classify_session_locally(
    *,
    user_message: str,
    reply_text: str,
    tool_calls: list[dict[str, Any]],
    tool_errors: int,
) -> str:
    """Mirror BigQuery AI.CLASSIFY taxonomy for immediate session tagging."""
    for tc in tool_calls:
        if tc["name"] == "issue_refund" and not tc.get("error"):
            resp = tc.get("response") or {}
            age = resp.get("order_age_days")
            if age is not None and int(age) > 30:
                return "policy_violation"

    if tool_errors > 0:
        return "tool_misuse"

    used_email_lookup = any(tc["name"] == "find_orders_by_email" for tc in tool_calls)
    used_get_order = any(tc["name"] == "get_order" for tc in tool_calls)
    reply_lower = reply_text.lower()
    if not used_email_lookup and not used_get_order and (
        "order number" in reply_lower or "order id" in reply_lower or "which order" in reply_lower
    ):
        return "asked_for_known_info"

    return "resolved"


support_runtime = SupportAgentRuntime()
