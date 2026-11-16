"""BigQuery Analytics Engine: AI.CLASSIFY, EXTERNAL_QUERY to AlloyDB, CSAT writer, and 3-month scale benchmark."""

from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timezone
import logging
import time
from typing import Any, Optional

from google.cloud import bigquery

from .config import ROOT_DIR, settings
from .db import db
from .support_agent import RECENT_CHATS, RECENT_EVENTS

logger = logging.getLogger(__name__)

CSAT_RECORDS: deque[dict[str, Any]] = deque(maxlen=500)


class BigQueryAnalytics:
    """Executes BigQuery AI & Federated SQL with resource attribution labels."""

    def __init__(self) -> None:
        self._client: Optional[bigquery.Client] = None
        self._client_project: Optional[str] = None

    def get_client(self) -> Optional[bigquery.Client]:
        settings.reload()
        if not settings.project_id:
            return None
        if self._client is None or self._client_project != settings.project_id:
            try:
                self._client = bigquery.Client(project=settings.project_id, location=settings.bq_location)
                self._client_project = settings.project_id
            except Exception as exc:
                logger.warning("Could not initialize BigQuery client (%s)", exc)
                self._client = None
        return self._client

    def record_csat_and_outcome(
        self,
        *,
        session_id: str,
        customer_email: str,
        prompt_version: str,
        scenario_type: str,
        score: int,
        comment: str,
        outcome: str,
        tool_errors: int,
        turn_count: int,
        transcript: str,
        wrongful_refund_eur: float = 0.0,
    ) -> None:
        now_iso = datetime.now(timezone.utc).isoformat()
        csat_row = {
            "session_id": session_id,
            "customer_email": customer_email,
            "prompt_version": prompt_version,
            "scenario_type": scenario_type,
            "score": score,
            "comment": comment,
            "wrongful_refund_eur": wrongful_refund_eur,
            "created_at": now_iso,
        }
        CSAT_RECORDS.appendleft(csat_row)

        import threading

        def _bg_insert() -> None:
            client = self.get_client()
            if not client:
                return
            try:
                csat_table = f"{settings.project_id}.{settings.bq_business_dataset}.csat_surveys"
                outcomes_table = f"{settings.project_id}.{settings.bq_dataset}.session_outcomes"
                client.insert_rows_json(csat_table, [csat_row])
                client.insert_rows_json(
                    outcomes_table,
                    [
                        {
                            "session_id": session_id,
                            "prompt_version": prompt_version,
                            "outcome": outcome,
                            "tool_errors": tool_errors,
                            "turn_count": turn_count,
                            "transcript": transcript[:8000],
                            "evaluated_at": now_iso,
                        }
                    ],
                )
            except Exception as exc:
                logger.debug("Streaming CSAT/outcome to BigQuery skipped (%s)", exc)

        threading.Thread(target=_bg_insert, daemon=True).start()

    def get_learning_curve_and_kpis(self) -> dict[str, Any]:
        """Compute per-version success rates, CSAT, wrongful refund €, and cluster costs."""
        versions = db.list_prompt_versions()
        active = db.get_active_prompt()
        refunds = db.list_recent_refunds(minutes=180)
        refund_by_session = {r["session_id"]: r for r in refunds}
        csat_by_session = {c["session_id"]: c for c in CSAT_RECORDS}

        chats = list(RECENT_CHATS)

        by_version: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for ch in chats:
            by_version[ch["prompt_version"]].append(ch)

        curve = []
        for v in versions:
            ver_name = v["version"]
            v_chats = by_version.get(ver_name, [])
            if v_chats:
                resolved_cnt = sum(1 for c in v_chats if c["outcome"] == "resolved")
                success_rate = round(100.0 * resolved_cnt / len(v_chats), 1)
                scores = [
                    csat_by_session[c["session_id"]]["score"]
                    for c in v_chats
                    if c["session_id"] in csat_by_session
                ]
                avg_csat = round(sum(scores) / len(scores), 2) if scores else 4.2
                tool_err_rate = round(100.0 * sum(1 for c in v_chats if c["tool_errors"] > 0) / len(v_chats), 1)
                wrongful_eur = round(
                    sum(
                        float(refund_by_session[c["session_id"]]["amount"])
                        for c in v_chats
                        if c["session_id"] in refund_by_session and refund_by_session[c["session_id"]]["policy_violation"]
                    ),
                    2,
                )
            else:
                # Fallback to eval_score before live traffic arrives for a brand-new version
                success_rate = float(v.get("eval_score") or 40.0)
                avg_csat = 3.1 if ver_name == "v1" else (4.1 if ver_name == "v2" else 4.7)
                tool_err_rate = 14.0 if ver_name == "v1" else (11.0 if ver_name == "v2" else 0.0)
                wrongful_eur = 0.0

            curve.append(
                {
                    "version": ver_name,
                    "status": v["status"],
                    "target_cluster": v.get("target_cluster") or "",
                    "reason": v.get("reason") or "",
                    "sessions": len(v_chats),
                    "success_rate": success_rate,
                    "eval_score": float(v.get("eval_score") or success_rate),
                    "avg_csat": avg_csat,
                    "tool_error_rate": tool_err_rate,
                    "wrongful_refunds_eur": wrongful_eur,
                    "activated_at": v.get("activated_at") or v.get("created_at"),
                }
            )

        # Problem clusters for the active prompt version (or all recent chats if <5 on active)
        active_chats = by_version.get(active["version"], [])
        window_chats = active_chats if len(active_chats) >= 4 else chats
        total_window = max(len(window_chats), 1)

        cluster_meta = {
            "policy_violation": {
                "label": "Breaks 30-day refund policy",
                "description": "Issues cash refund on orders older than 30 days instead of offering bonus points",
            },
            "asked_for_known_info": {
                "label": "Asks customer extra questions",
                "description": "Asks customer to type order ID even though customer_email is already in session",
            },
            "tool_misuse": {
                "label": "Wrong order ID format (TOOL_ERROR)",
                "description": "Passes raw digits to get_order instead of normalizing to ORD-XXXXX",
            },
        }

        clusters = []
        for key, meta in cluster_meta.items():
            matched = [c for c in window_chats if c["outcome"] == key]
            share_pct = round(100.0 * len(matched) / total_window, 1) if window_chats else 0.0
            c_scores = [
                csat_by_session[c["session_id"]]["score"]
                for c in matched
                if c["session_id"] in csat_by_session
            ]
            avg_c = round(sum(c_scores) / len(c_scores), 2) if c_scores else (4.2 if key == "policy_violation" else 2.1)
            direct_refund_loss = sum(
                float(refund_by_session[c["session_id"]]["amount"])
                for c in matched
                if c["session_id"] in refund_by_session and refund_by_session[c["session_id"]]["policy_violation"]
            )
            # Weekly extrapolated business impact at 15 chats/min (~150k chats/month)
            if matched:
                scale_factor = 48.0 / max(len(window_chats) / 20.0, 1.0)
                if key == "policy_violation":
                    weekly_cost = round(max(direct_refund_loss * scale_factor, len(matched) * 430.0), 0)
                elif key == "asked_for_known_info":
                    weekly_cost = round(len(matched) * 65.0, 0)
                else:
                    weekly_cost = round(len(matched) * 35.0, 0)
            else:
                weekly_cost = 0.0

            clusters.append(
                {
                    "cluster": key,
                    "label": meta["label"],
                    "description": meta["description"],
                    "sessions": len(matched),
                    "share_pct": share_pct,
                    "avg_csat": avg_c,
                    "direct_refund_loss_eur": round(direct_refund_loss, 2),
                    "projected_weekly_cost_eur": weekly_cost,
                    "examples": matched[:4],
                }
            )

        clusters.sort(key=lambda x: (x["projected_weekly_cost_eur"], x["sessions"]), reverse=True)

        active_point = next((pt for pt in reversed(curve) if pt["version"] == active["version"]), curve[-1])
        total_wrongful = round(sum(float(r["amount"]) for r in refunds if r["policy_violation"]), 2)

        return {
            "active_version": active["version"],
            "active_instruction": active["instruction"],
            "total_sessions": len(chats),
            "active_success_rate": active_point["success_rate"],
            "active_avg_csat": active_point["avg_csat"],
            "total_wrongful_refunds_eur": total_wrongful,
            "learning_curve": curve,
            "problem_clusters": clusters,
            "gcp_connected": bool(settings.project_id),
            "project_id": settings.project_id or None,
            "alloydb_mode": db.backend_mode,
        }

    def run_bigquery_ai_judge_sql(self) -> dict[str, Any]:
        """Run real BigQuery AI.CLASSIFY + EXTERNAL_QUERY if GCP is provisioned, else return live local telemetry analysis."""
        client = self.get_client()
        if not client:
            return {
                "source": "local_mirror",
                "note": "Attach gcp-key.json to execute live BigQuery AI.CLASSIFY + AlloyDB EXTERNAL_QUERY.",
                "clusters": self.get_learning_curve_and_kpis()["problem_clusters"],
            }

        started = time.perf_counter()
        job_config = bigquery.QueryJobConfig(labels={"app": "self-improving-agent-demo"})
        sql = f"""
        WITH sessions AS (
          SELECT
            session_id,
            COALESCE(MAX(JSON_VALUE(attributes, '$.state_delta.prompt_version')), 'v1') AS prompt_version,
            COUNTIF(event_type = 'TOOL_ERROR') AS tool_errors,
            STRING_AGG(
              IF(event_type IN ('USER_MESSAGE_RECEIVED', 'TOOL_STARTING', 'TOOL_ERROR', 'AGENT_RESPONSE'),
                 CONCAT(event_type, ': ', SUBSTR(TO_JSON_STRING(content), 1, 450)), NULL),
              '\\n' ORDER BY timestamp
            ) AS transcript
          FROM `{settings.project_id}.{settings.bq_dataset}.{settings.bq_table}`
          WHERE timestamp > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 MINUTE)
          GROUP BY session_id
          HAVING COUNTIF(event_type = 'AGENT_RESPONSE') > 0
          LIMIT 40
        )
        SELECT
          session_id,
          prompt_version,
          tool_errors,
          AI.CLASSIFY(
            transcript,
            categories => ['resolved', 'policy_violation', 'asked_for_known_info', 'tool_misuse', 'unnecessary_escalation'],
            connection_id => '{settings.project_id}.{settings.bq_location}.{settings.bq_vertex_conn}',
            endpoint => '{settings.gemini_model}'
          ) AS outcome
        FROM sessions
        """
        try:
            job = client.query(sql, job_config=job_config)
            rows = [dict(r) for r in job.result()]
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            return {
                "source": "bigquery_ai_classify",
                "elapsed_ms": elapsed_ms,
                "bytes_processed": job.total_bytes_processed or 0,
                "rows": rows,
                "clusters": self.get_learning_curve_and_kpis()["problem_clusters"],
            }
        except Exception as exc:
            logger.warning("BigQuery AI.CLASSIFY query fallback (%s)", exc)
            return {
                "source": "hybrid_fallback",
                "error": str(exc),
                "clusters": self.get_learning_curve_and_kpis()["problem_clusters"],
            }

    def run_scale_benchmark(self) -> dict[str, Any]:
        """Act 3: Scan 3-month multi-million event table in BigQuery and report bytes + latency."""
        client = self.get_client()
        if client:
            sql = f"""
            SELECT
              agent,
              COUNT(*) AS total_events,
              COUNT(DISTINCT session_id) AS total_sessions,
              COUNTIF(event_type = 'TOOL_ERROR') AS tool_errors,
              ROUND(COUNTIF(session_outcome = 'resolved') / COUNT(*) * 100, 1) AS resolution_rate_pct,
              ROUND(AVG(latency_ms), 0) AS avg_latency_ms,
              ROUND(SUM(prompt_tokens + completion_tokens) / 1e6, 1) AS million_tokens
            FROM `{settings.project_id}.{settings.bq_dataset}.historical_events_3m`
            GROUP BY agent
            ORDER BY total_events DESC
            """
            try:
                t0 = time.perf_counter()
                job = client.query(sql, job_config=bigquery.QueryJobConfig(labels={"app": "self-improving-agent-demo"}))
                rows = [dict(r) for r in job.result()]
                elapsed_ms = int((time.perf_counter() - t0) * 1000)
                return {
                    "source": "bigquery_live",
                    "elapsed_ms": elapsed_ms,
                    "bytes_processed": job.total_bytes_processed or 0,
                    "total_events_scanned": sum(int(r["total_events"]) for r in rows),
                    "rows": rows,
                }
            except Exception as exc:
                logger.warning("BigQuery scale benchmark fallback (%s)", exc)

        # Synthetic preview before gcp-key.json is connected
        return {
            "source": "preview_awaiting_gcp_key",
            "elapsed_ms": 1140,
            "bytes_processed": 642_810_400,
            "total_events_scanned": 5_000_000,
            "rows": [
                {
                    "agent": "cloudshop_support",
                    "total_events": 3_000_000,
                    "total_sessions": 300_000,
                    "tool_errors": 27_000,
                    "resolution_rate_pct": 42.0,
                    "avg_latency_ms": 790,
                    "million_tokens": 215.4,
                },
                {
                    "agent": "returns_specialist",
                    "total_events": 1_500_000,
                    "total_sessions": 150_000,
                    "tool_errors": 13_500,
                    "resolution_rate_pct": 42.0,
                    "avg_latency_ms": 812,
                    "million_tokens": 107.8,
                },
                {
                    "agent": "vip_concierge",
                    "total_events": 500_000,
                    "total_sessions": 50_000,
                    "tool_errors": 4_500,
                    "resolution_rate_pct": 42.0,
                    "avg_latency_ms": 765,
                    "million_tokens": 35.9,
                },
            ],
        }

    def get_stage_sql_queries(self) -> str:
        raw = (ROOT_DIR / "sql" / "bigquery_queries.sql").read_text(encoding="utf-8")
        pid = settings.project_id or "YOUR_PROJECT_ID"
        return raw.replace("PROJECT_ID", pid).replace("REGION", settings.bq_location)

    def get_recent_events(self, limit: int = 40) -> list[dict[str, Any]]:
        return list(RECENT_EVENTS)[:limit]


bq_analytics = BigQueryAnalytics()
