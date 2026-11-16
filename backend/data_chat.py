"""Gemini Conversational Analytics (Chat with Your BigQuery Data) Async SSE Streaming Service."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Any, AsyncIterator

from google.cloud import geminidataanalytics_v1beta as gda

from .config import settings

logger = logging.getLogger(__name__)

SYSTEM_INSTRUCTION = """You are the Gemini Conversational Analytics copilot for the CloudShop Self-Improving Support Agent demo.
You have direct SQL access to four live BigQuery tables in this project:
1. `agent_analytics.agent_events`: Raw event log streamed automatically by Google ADK `BigQueryAgentAnalyticsPlugin` (`timestamp`, `event_type` such as `LLM_REQUEST`, `LLM_RESPONSE`, `TOOL_STARTING`, `TOOL_COMPLETED`, `TOOL_ERROR`, `STATE_DELTA`, `USER_MESSAGE_RECEIVED`, `AGENT_COMPLETED`, `session_id`, `agent`, `user_id`, `content`, `attributes`, `latency_ms`, `status`, `error_message`).
2. `agent_analytics.session_outcomes`: Session-level evaluations (`session_id`, `prompt_version` like `v1`/`v2`/`v3`, `outcome` in `resolved`, `policy_violation`, `asked_for_known_info`, `tool_misuse`, `tool_errors`, `turn_count`, `transcript`, `evaluated_at`).
3. `business.csat_surveys`: Customer satisfaction & financial loss table (`session_id`, `customer_email`, `prompt_version`, `scenario_type`, `score` 1-5, `comment`, `wrongful_refund_eur`, `created_at`). Joining `session_outcomes` and `csat_surveys` on `session_id` reveals that `policy_violation` gets high CSAT (~5/5) while losing large `wrongful_refund_eur`!
4. `agent_analytics.historical_events_3m`: 5,000,000-row 90-day multi-agent production telemetry (`session_id`, `timestamp`, `agent` in `cloudshop_support`, `returns_specialist`, `vip_concierge`, `event_type`, `session_outcome`, `prompt_tokens`, `completion_tokens`, `latency_ms`, `payload_json`).

Guidelines:
- Always write clean Standard SQL against these BigQuery tables to answer the user's question with exact numbers.
- Always include a clean GitHub-Flavored Markdown table directly in your text response whenever presenting multi-row breakdowns or comparisons.
- Format numbers, percentages, euro amounts (€), and markdown headings clearly and concisely.
"""


async def stream_bigquery_data_chat(message: str, history: list[dict[str, Any]]) -> AsyncIterator[str]:
    """Asynchronously stream Server-Sent Events (`THOUGHT`, `SQL`, `FINAL_RESPONSE`, `SUGGESTION`) without blocking asyncio."""
    # Flush HTTP headers immediately through Vite / proxies
    yield ": stream-open\n\n"

    project_id = settings.project_id
    if not project_id:
        err = {"type": "FINAL_RESPONSE", "content": "⚠️ `GOOGLE_CLOUD_PROJECT` is not configured."}
        yield f"data: {json.dumps(err)}\n\n"
        yield "data: [DONE]\n\n"
        return

    inline_context = {
        "system_instruction": SYSTEM_INSTRUCTION,
        "datasource_references": {
            "bq": {
                "table_references": [
                    {"project_id": project_id, "dataset_id": settings.bq_dataset, "table_id": settings.bq_table},
                    {"project_id": project_id, "dataset_id": settings.bq_dataset, "table_id": "session_outcomes"},
                    {"project_id": project_id, "dataset_id": settings.bq_dataset, "table_id": "historical_events_3m"},
                    {"project_id": project_id, "dataset_id": settings.bq_business_dataset, "table_id": "csat_surveys"},
                ]
            }
        },
    }

    client_history: list[Any] = []
    for msg in history:
        role = msg.get("role")
        content = (msg.get("content") or "").strip()
        if not content:
            continue
        if role == "user":
            client_history.append(gda.Message(user_message=gda.UserMessage(text=content)))
        elif role == "model":
            client_history.append(
                gda.Message(system_message=gda.SystemMessage(text=gda.TextMessage(parts=[content])))
            )

    chat_request = gda.ChatRequest(
        parent=f"projects/{project_id}/locations/global",
        messages=client_history + [gda.Message(user_message=gda.UserMessage(text=message))],
        inline_context=inline_context,
    )

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[str | None] = asyncio.Queue()

    def _worker() -> None:
        seen_sql: set[str] = set()
        emitted_final = False
        ended_with_newline = True
        try:
            client = gda.DataChatServiceClient()
            response_stream = client.chat(request=chat_request, timeout=90.0)
            for chunk in response_stream:
                sys_msg = chunk.system_message
                if not sys_msg:
                    continue

                if getattr(sys_msg, "data", None):
                    gen_sql = (getattr(sys_msg.data, "generated_sql", "") or "").strip()
                    if gen_sql and gen_sql not in seen_sql:
                        seen_sql.add(gen_sql)
                        evt = f"data: {json.dumps({'type': 'SQL', 'content': gen_sql})}\n\n"
                        loop.call_soon_threadsafe(queue.put_nowait, evt)

                if getattr(sys_msg, "suggestions", None):
                    for s in sys_msg.suggestions:
                        title = getattr(s, "title", "") or str(s)
                        if title.strip():
                            evt = f"data: {json.dumps({'type': 'SUGGESTION', 'content': title.strip()})}\n\n"
                            loop.call_soon_threadsafe(queue.put_nowait, evt)

                if getattr(sys_msg, "text", None) and sys_msg.text.parts:
                    raw_type = getattr(sys_msg.text, "text_type", None)
                    if raw_type is None:
                        raw_type = getattr(sys_msg, "text_type", None)
                    type_name = getattr(raw_type, "name", str(raw_type)) if raw_type is not None else ""
                    type_int = int(raw_type) if isinstance(raw_type, int) or hasattr(raw_type, "value") else -1

                    parts = [p for p in sys_msg.text.parts if p]
                    if not parts:
                        continue

                    if "FOLLOWUP" in type_name or "UNSPECIFIED" in type_name or type_int in (0, 4):
                        for suggestion in parts:
                            clean_s = suggestion.strip()
                            if clean_s:
                                evt = f"data: {json.dumps({'type': 'SUGGESTION', 'content': clean_s})}\n\n"
                                loop.call_soon_threadsafe(queue.put_nowait, evt)
                    elif "THOUGHT" in type_name or "PROGRESS" in type_name or type_int in (2, 3):
                        thought_line = " — ".join(p.strip() for p in parts if p.strip())
                        if thought_line:
                            evt = f"data: {json.dumps({'type': 'THOUGHT', 'content': thought_line})}\n\n"
                            loop.call_soon_threadsafe(queue.put_nowait, evt)
                    else:
                        text_content = "\n\n".join(parts)
                        if emitted_final and not ended_with_newline:
                            text_content = "\n\n" + text_content
                        emitted_final = True
                        ended_with_newline = text_content.endswith("\n")
                        evt = f"data: {json.dumps({'type': 'FINAL_RESPONSE', 'content': text_content})}\n\n"
                        loop.call_soon_threadsafe(queue.put_nowait, evt)
        except Exception as exc:
            logger.exception("Gemini Data Analytics chat error")
            err_payload = {
                "type": "FINAL_RESPONSE",
                "content": f"\n\n**Gemini Data Analytics Error**: `{exc}`",
            }
            loop.call_soon_threadsafe(queue.put_nowait, f"data: {json.dumps(err_payload)}\n\n")
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, "data: [DONE]\n\n")
            loop.call_soon_threadsafe(queue.put_nowait, None)

    threading.Thread(target=_worker, daemon=True).start()

    while True:
        item = await queue.get()
        if item is None:
            break
        yield item
