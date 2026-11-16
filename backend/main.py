"""FastAPI Application for Cloud AI Day Demo: Support Agent + Simulator + BigQuery Analytics + Coach + UI."""

from __future__ import annotations

import os
import certifi

os.environ.setdefault("SSL_CERT_FILE", certifi.where())
os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .bq_analytics import CSAT_RECORDS, bq_analytics
from .coach_agent import coach_service
from .config import ROOT_DIR, settings
from .data_chat import stream_bigquery_data_chat
from .db import db
from .simulator import simulator
from .support_agent import RECENT_CHATS, RECENT_EVENTS, support_runtime


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    simulator.stop()


app = FastAPI(
    title="Self-Improving Agent Demo (BigQuery + AlloyDB + ADK)",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    message: str
    customer_email: str = "alice@example.com"
    session_id: Optional[str] = None


class RunCycleRequest(BaseModel):
    auto_publish: Optional[bool] = None


class RollbackRequest(BaseModel):
    version: str


class SimulatorControlRequest(BaseModel):
    action: str  # "start" | "stop" | "burst"
    chats_per_minute: int = 12
    burst_count: int = 14


@app.get("/api/dashboard")
async def get_dashboard_state() -> dict[str, Any]:
    kpis = bq_analytics.get_learning_curve_and_kpis()
    return {
        **kpis,
        "versions": db.list_prompt_versions(),
        "lessons": db.get_lessons(),
        "coach_cycles": db.list_coach_cycles(limit=10),
        "recent_chats": list(RECENT_CHATS)[:35],
        "recent_events": bq_analytics.get_recent_events(limit=35),
        "orders": db.list_orders(),
        "refunds": db.list_recent_refunds(minutes=180),
        "simulator": {
            "running": simulator.running,
            "chats_per_minute": simulator.chats_per_minute,
            "total_simulated": simulator.total_simulated,
        },
        "bq_plugin_active": support_runtime.bq_plugin_active,
        "auto_publish_coach": settings.auto_publish_coach,
    }


@app.post("/api/chat")
async def post_support_chat(req: ChatRequest) -> dict[str, Any]:
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty.")
    return await support_runtime.chat(
        message=req.message.strip(),
        customer_email=req.customer_email.strip() or "alice@example.com",
        session_id=req.session_id,
        record_in_feed=True,
    )


@app.post("/api/chat/reset-stage-order")
async def reset_stage_order() -> dict[str, Any]:
    """Ensure ORD-10482 (45 days old, €89) is reset to 'delivered' so Presenter can re-demo Act 1 / Act 6 anytime."""
    if db.backend_mode == "alloydb":
        with db._pg_connect() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM refunds WHERE order_id = 'ORD-10482'")
            cur.execute("UPDATE orders SET status = 'delivered' WHERE order_id = 'ORD-10482'")
            conn.commit()
    elif db._sqlite_conn:
        with db._lock:
            db._sqlite_conn.execute("DELETE FROM refunds WHERE order_id = 'ORD-10482'")
            db._sqlite_conn.execute("UPDATE orders SET status = 'delivered' WHERE order_id = 'ORD-10482'")
            db._sqlite_conn.commit()
    return {"status": "ok", "order_id": "ORD-10482"}


@app.post("/api/coach/run-cycle")
async def run_coach_cycle(req: RunCycleRequest = RunCycleRequest()) -> dict[str, Any]:
    return await coach_service.run_improvement_cycle(auto_publish=req.auto_publish)


@app.post("/api/coach/run-cycle-stream")
async def run_coach_cycle_stream(req: RunCycleRequest = RunCycleRequest()) -> StreamingResponse:
    return StreamingResponse(
        coach_service.stream_improvement_cycle(auto_publish=req.auto_publish),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/coach/toggle-auto")
async def toggle_auto_publish() -> dict[str, Any]:
    settings.auto_publish_coach = not settings.auto_publish_coach
    return {"auto_publish_coach": settings.auto_publish_coach}


@app.post("/api/versions/activate")
async def activate_or_rollback_version(req: RollbackRequest) -> dict[str, Any]:
    versions = {v["version"] for v in db.list_prompt_versions()}
    if req.version not in versions:
        raise HTTPException(status_code=404, detail=f"Version {req.version} not found.")
    active = db.activate_or_rollback(req.version, mark_others_rolled_back=True)
    return {"status": "activated", "active": active}


@app.post("/api/demo/reset-v1")
async def reset_demo_to_v1() -> dict[str, Any]:
    simulator.stop()
    RECENT_CHATS.clear()
    RECENT_EVENTS.clear()
    CSAT_RECORDS.clear()
    active = db.reset_to_v1()
    return {"status": "reset_to_v1", "active": active}


@app.post("/api/simulator/control")
async def control_simulator(req: SimulatorControlRequest) -> dict[str, Any]:
    if req.action == "start":
        simulator.start(chats_per_minute=req.chats_per_minute)
        return {"running": True, "chats_per_minute": simulator.chats_per_minute}
    if req.action == "stop":
        simulator.stop()
        return {"running": False, "chats_per_minute": simulator.chats_per_minute}
    if req.action == "burst":
        items = await simulator.run_burst(count=max(1, min(req.burst_count, 40)))
        return {"running": simulator.running, "generated": len(items)}
    raise HTTPException(status_code=400, detail=f"Unknown simulator action: {req.action}")


@app.get("/api/bigquery/scale-benchmark")
async def get_scale_benchmark() -> dict[str, Any]:
    return bq_analytics.run_scale_benchmark()


@app.get("/api/bigquery/ai-judge")
async def get_bigquery_ai_judge() -> dict[str, Any]:
    return bq_analytics.run_bigquery_ai_judge_sql()


@app.get("/api/bigquery/sql")
async def get_stage_sql() -> dict[str, str]:
    return {
        "bigquery_queries_sql": bq_analytics.get_stage_sql_queries(),
        "support_agent_snippet": (
            "bq_plugin = BigQueryAgentAnalyticsPlugin(\n"
            '    project_id=settings.project_id, dataset_id="agent_analytics", table_id="agent_events"\n'
            ")\n"
            'app = App(name="cloudshop_support", root_agent=agent, plugins=[bq_plugin])'
        ),
    }


class DataChatRequest(BaseModel):
    message: str
    history: list[dict[str, Any]] = []


@app.get("/api/health")
async def health_check() -> dict[str, Any]:
    return {
        "status": "ok",
        "project_id": settings.project_id,
        "db_mode": db.backend_mode,
        "bq_plugin_active": support_runtime.bq_plugin_active,
    }


@app.post("/api/data-chat")
async def post_bigquery_data_chat(req: DataChatRequest) -> StreamingResponse:
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty.")
    return StreamingResponse(
        stream_bigquery_data_chat(message=req.message.strip(), history=req.history),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/gcp/reload")
async def reload_gcp_credentials() -> dict[str, Any]:
    settings.reload()
    db.initialize()
    support_runtime.rebuild()
    return {
        "project_id": settings.project_id or None,
        "key_file": str(settings.key_file) if settings.key_file else None,
        "alloydb_mode": db.backend_mode,
        "bq_plugin_active": support_runtime.bq_plugin_active,
    }


# Serve production React build when deployed to Cloud Run
FRONTEND_DIST = ROOT_DIR / "frontend" / "dist"
if FRONTEND_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIST / "assets")), name="assets")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        candidate = FRONTEND_DIST / full_path
        if full_path and candidate.is_file():
            return FileResponse(str(candidate))
        return FileResponse(
            str(FRONTEND_DIST / "index.html"),
            headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
        )
