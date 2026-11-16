"""Operational Store Layer: AlloyDB (PostgreSQL) primary with seamless SQLite fallback while provisioning."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
import logging
import re
import sqlite3
import threading
from typing import Any, Optional

import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2.pool import ThreadedConnectionPool

from .config import ROOT_DIR, settings

logger = logging.getLogger(__name__)

ORDER_ID_PATTERN = re.compile(r"^ORD-\d{5}$")

INITIAL_V1_INSTRUCTION = (
    "You are a friendly and fast customer support agent for CloudShop.\n"
    "Your top priority is keeping customers happy and resolving requests quickly.\n"
    "- You have tools to look up orders, issue refunds, grant bonus points, and search store policies.\n"
    "- When a customer asks for a refund on an order, process the refund right away so they have a smooth experience.\n"
    "- Keep responses concise, polite, and helpful."
)

SEED_CUSTOMERS = [
    ("alice@example.com", "Alice Meyer", 240, "gold"),
    ("marco@example.com", "Marco Rossi", 80, "standard"),
    ("sofia@example.com", "Sofia Lindqvist", 510, "platinum"),
    ("lukas@example.com", "Lukas Weber", 150, "standard"),
    ("elena@example.com", "Elena Petrova", 320, "gold"),
    ("david@example.com", "David Chen", 90, "standard"),
    ("claire@example.com", "Claire Dubois", 410, "gold"),
    ("jonas@example.com", "Jonas Nielsen", 60, "standard"),
    ("hannah@example.com", "Hannah Fischer", 195, "standard"),
    ("mateo@example.com", "Mateo Garcia", 275, "gold"),
]

# Relative age in days from today so ORD-10482 is ALWAYS 45 days old!
SEED_ORDERS = [
    ("ORD-10482", "alice@example.com", "Alice Meyer", "ANC Wireless Headphones", 89.00, 45, "delivered"),
    ("ORD-10391", "marco@example.com", "Marco Rossi", "Mechanical Keyboard Pro", 145.00, 52, "delivered"),
    ("ORD-10415", "sofia@example.com", "Sofia Lindqvist", "Smart Espresso Machine", 210.00, 49, "delivered"),
    ("ORD-10440", "lukas@example.com", "Lukas Weber", "4K USB-C Travel Monitor", 189.00, 41, "delivered"),
    ("ORD-10467", "elena@example.com", "Elena Petrova", "Ergonomic Desk Lamp", 95.00, 38, "delivered"),
    ("ORD-10478", "david@example.com", "David Chen", "Portable NVMe SSD 2TB", 160.00, 36, "delivered"),
    ("ORD-10495", "claire@example.com", "Claire Dubois", "Running Smartwatch GPS", 229.00, 34, "delivered"),
    ("ORD-10510", "jonas@example.com", "Jonas Nielsen", "Bluetooth Speaker Mini", 79.00, 33, "delivered"),
    ("ORD-10811", "hannah@example.com", "Hannah Fischer", "USB-C GaN Charger 100W", 64.00, 12, "delivered"),
    ("ORD-10845", "mateo@example.com", "Mateo Garcia", "Wireless Vertical Mouse", 119.00, 9, "delivered"),
    ("ORD-10890", "alice@example.com", "Alice Meyer", "Laptop Sleeve 14-inch", 49.00, 6, "delivered"),
    ("ORD-10902", "marco@example.com", "Marco Rossi", "Webcam 4K AutoFocus", 135.00, 14, "delivered"),
    ("ORD-10918", "sofia@example.com", "Sofia Lindqvist", "Noise-Isolating Earbuds", 112.00, 18, "delivered"),
    ("ORD-10934", "lukas@example.com", "Lukas Weber", "Smart Air Purifier Compact", 175.00, 5, "shipped"),
    ("ORD-10950", "elena@example.com", "Elena Petrova", "Insulated Travel Mug", 38.00, 8, "delivered"),
]

SEED_POLICIES = [
    (
        "POL-REFUND-30D",
        "refunds",
        "30-Day Refund Window & Goodwill Bonus Points",
        "Monetary refunds to the customer's original payment method are strictly allowed ONLY within 30 calendar days "
        "of order_date. Always check order_date / age_days before calling issue_refund. If age_days > 30, DO NOT call "
        "issue_refund; instead politely explain the 30-day refund limit and offer 500 loyalty bonus points (€25 store "
        "credit) via grant_bonus_points.",
    ),
    (
        "POL-ORDER-EMAIL",
        "lookup",
        "Proactive Order Lookup by Customer Email",
        "If the customer's email is provided in the conversation or session context, call find_orders_by_email "
        "immediately instead of asking the customer to look up or type their order ID.",
    ),
    (
        "POL-ORDER-FORMAT",
        "formatting",
        "Order ID Normalization (ORD-XXXXX)",
        "All order IDs in get_order and issue_refund strictly follow the uppercase format ORD-XXXXX (5 digits, e.g. "
        "ORD-10482). If a customer gives raw digits like '10482' or '#10482' or 'ord 10482', normalize it to "
        "'ORD-10482' before calling any order tool.",
    ),
    (
        "POL-SHIPPING",
        "shipping",
        "In-Transit Order Inquiries",
        "Orders with status 'shipped' normally arrive within 3-5 business days. Refunds cannot be issued while an item "
        "is in transit unless lost after 14 days.",
    ),
]


class OperationalDB:
    """Unified interface to AlloyDB (PostgreSQL) with automatic local SQLite fallback."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._using_alloydb: bool = False
        self._pg_pool: Optional[ThreadedConnectionPool] = None
        self._sqlite_conn: Optional[sqlite3.Connection] = None
        self.initialize()

    @property
    def backend_mode(self) -> str:
        return "alloydb" if self._using_alloydb else "sqlite_mirror"

    @contextmanager
    def _pg_connect(self):
        if self._pg_pool is None:
            conn = psycopg2.connect(
                host=settings.alloydb_host,
                port=settings.alloydb_port,
                dbname=settings.alloydb_db,
                user=settings.alloydb_user,
                password=settings.alloydb_password,
                connect_timeout=2,
                cursor_factory=RealDictCursor,
            )
            try:
                yield conn
            finally:
                conn.close()
            return

        try:
            conn = self._pg_pool.getconn()
        except (psycopg2.OperationalError, psycopg2.InterfaceError) as exc:
            logger.warning("AlloyDB pool unreachable (%s); failing over to instant local mirror.", exc)
            self._using_alloydb = False
            raise
        discard = False
        try:
            if conn.closed:
                self._pg_pool.putconn(conn, close=True)
                conn = self._pg_pool.getconn()
            yield conn
        except (psycopg2.OperationalError, psycopg2.InterfaceError) as exc:
            discard = True
            logger.warning("AlloyDB socket dropped (%s); failing over to instant local mirror.", exc)
            self._using_alloydb = False
            raise
        except Exception:
            try:
                if not conn.closed:
                    conn.rollback()
            except Exception:
                discard = True
            raise
        finally:
            try:
                if self._pg_pool is not None:
                    self._pg_pool.putconn(conn, close=discard or bool(conn.closed))
            except Exception:
                pass

    def initialize(self) -> None:
        settings.reload()
        # Always keep warm local SQLite mirror initialized for instant zero-downtime failover
        self._init_sqlite()
        if settings.alloydb_host:
            try:
                if self._pg_pool is not None:
                    try:
                        self._pg_pool.closeall()
                    except Exception:
                        pass
                self._pg_pool = ThreadedConnectionPool(
                    minconn=1,
                    maxconn=12,
                    host=settings.alloydb_host,
                    port=settings.alloydb_port,
                    dbname=settings.alloydb_db,
                    user=settings.alloydb_user,
                    password=settings.alloydb_password,
                    connect_timeout=2,
                    cursor_factory=RealDictCursor,
                )
                with self._pg_connect() as conn:
                    with conn.cursor() as cur:
                        cur.execute("SELECT 1 FROM information_schema.tables WHERE table_name = 'prompt_versions'")
                        exists = cur.fetchone()
                        if not exists:
                            sql_path = ROOT_DIR / "sql" / "alloydb_init.sql"
                            cur.execute(sql_path.read_text(encoding="utf-8"))
                    conn.commit()
                self._using_alloydb = True
                logger.info("Connected to live AlloyDB pool at %s", settings.alloydb_host)
            except Exception as exc:
                logger.warning("AlloyDB not yet reachable (%s); using local SQLite mirror.", exc)
                self._pg_pool = None
                self._using_alloydb = False

    def _init_sqlite(self) -> None:
        settings.sqlite_fallback_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(settings.sqlite_fallback_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        self._sqlite_conn = conn
        with self._lock:
            cur = conn.cursor()
            cur.executescript(
                """
                CREATE TABLE IF NOT EXISTS customers (
                    customer_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT UNIQUE NOT NULL,
                    full_name TEXT NOT NULL,
                    loyalty_points INTEGER NOT NULL DEFAULT 120,
                    tier TEXT NOT NULL DEFAULT 'standard'
                );
                CREATE TABLE IF NOT EXISTS orders (
                    order_id TEXT PRIMARY KEY,
                    customer_email TEXT NOT NULL,
                    customer_name TEXT NOT NULL,
                    product_name TEXT NOT NULL,
                    amount REAL NOT NULL,
                    order_date TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'delivered'
                );
                CREATE TABLE IF NOT EXISTS refunds (
                    refund_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    amount REAL NOT NULL,
                    reason TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS store_policies (
                    policy_id TEXT PRIMARY KEY,
                    category TEXT NOT NULL,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS prompt_versions (
                    version TEXT PRIMARY KEY,
                    instruction TEXT NOT NULL,
                    status TEXT NOT NULL,
                    parent_version TEXT,
                    target_cluster TEXT,
                    reason TEXT,
                    eval_score REAL,
                    baseline_score REAL,
                    sessions_analyzed INTEGER DEFAULT 0,
                    estimated_weekly_eur_saved REAL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    activated_at TEXT
                );
                CREATE TABLE IF NOT EXISTS agent_lessons (
                    lesson_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    version_added TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    rule_text TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS coach_cycles (
                    cycle_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    active_version_before TEXT NOT NULL,
                    candidate_version TEXT,
                    top_cluster TEXT,
                    cluster_sessions INTEGER DEFAULT 0,
                    cluster_cost_eur REAL DEFAULT 0,
                    eval_before REAL,
                    eval_after REAL,
                    decision TEXT NOT NULL,
                    summary TEXT,
                    diff_added TEXT
                );
                """
            )
            cur.execute("SELECT COUNT(*) AS cnt FROM prompt_versions")
            if cur.fetchone()["cnt"] == 0:
                self._seed_sqlite_locked(cur)
            conn.commit()

    def _seed_sqlite_locked(self, cur: sqlite3.Cursor) -> None:
        today = date.today()
        now_iso = datetime.now(timezone.utc).isoformat()
        cur.executemany(
            "INSERT OR IGNORE INTO customers (email, full_name, loyalty_points, tier) VALUES (?, ?, ?, ?)",
            SEED_CUSTOMERS,
        )
        for oid, email, name, prod, amt, days_ago, st in SEED_ORDERS:
            odate = (today - timedelta(days=days_ago)).isoformat()
            cur.execute(
                "INSERT OR REPLACE INTO orders (order_id, customer_email, customer_name, product_name, amount, order_date, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (oid, email, name, prod, amt, odate, st),
            )
        cur.executemany(
            "INSERT OR REPLACE INTO store_policies (policy_id, category, title, content) VALUES (?, ?, ?, ?)",
            SEED_POLICIES,
        )
        cur.execute(
            "INSERT OR REPLACE INTO prompt_versions "
            "(version, instruction, status, parent_version, target_cluster, reason, eval_score, baseline_score, sessions_analyzed, estimated_weekly_eur_saved, created_at, activated_at) "
            "VALUES (?, ?, 'active', NULL, 'initial_release', 'Initial developer prompt shipped to production (v1 baseline).', 40.0, 40.0, 0, 0.0, ?, ?)",
            ("v1", INITIAL_V1_INSTRUCTION, now_iso, now_iso),
        )

    # ------------------------------------------------------------------
    # Prompt Version & Lesson Management
    # ------------------------------------------------------------------
    def get_active_prompt(self) -> dict[str, Any]:
        if self._using_alloydb:
            with self._pg_connect() as conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT version, instruction, status, parent_version, target_cluster, reason, "
                    "FLOAT8(eval_score) AS eval_score, FLOAT8(baseline_score) AS baseline_score, "
                    "sessions_analyzed, FLOAT8(estimated_weekly_eur_saved) AS estimated_weekly_eur_saved, "
                    "created_at, activated_at "
                    "FROM prompt_versions WHERE status = 'active' ORDER BY activated_at DESC NULLS LAST LIMIT 1"
                )
                row = cur.fetchone()
                if row:
                    return self._serialize_row(dict(row))

        with self._lock:
            assert self._sqlite_conn is not None
            cur = self._sqlite_conn.cursor()
            cur.execute("SELECT * FROM prompt_versions WHERE status = 'active' ORDER BY created_at DESC LIMIT 1")
            row = cur.fetchone()
            if row:
                return dict(row)
        return {"version": "v1", "instruction": INITIAL_V1_INSTRUCTION, "status": "active", "eval_score": 40.0}

    def get_lessons(self) -> list[dict[str, Any]]:
        if self._using_alloydb:
            with self._pg_connect() as conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT lesson_id, version_added, topic, rule_text, created_at "
                    "FROM agent_lessons ORDER BY lesson_id ASC"
                )
                return [self._serialize_row(dict(r)) for r in cur.fetchall()]
        with self._lock:
            assert self._sqlite_conn is not None
            cur = self._sqlite_conn.cursor()
            cur.execute("SELECT * FROM agent_lessons ORDER BY lesson_id ASC")
            return [dict(r) for r in cur.fetchall()]

    def list_prompt_versions(self) -> list[dict[str, Any]]:
        if self._using_alloydb:
            with self._pg_connect() as conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT version, instruction, status, parent_version, target_cluster, reason, "
                    "FLOAT8(eval_score) AS eval_score, FLOAT8(baseline_score) AS baseline_score, "
                    "sessions_analyzed, FLOAT8(estimated_weekly_eur_saved) AS estimated_weekly_eur_saved, "
                    "created_at, activated_at "
                    "FROM prompt_versions ORDER BY created_at ASC"
                )
                return [self._serialize_row(dict(r)) for r in cur.fetchall()]
        with self._lock:
            assert self._sqlite_conn is not None
            cur = self._sqlite_conn.cursor()
            cur.execute("SELECT * FROM prompt_versions ORDER BY created_at ASC")
            return [dict(r) for r in cur.fetchall()]

    def list_coach_cycles(self, limit: int = 20) -> list[dict[str, Any]]:
        if self._using_alloydb:
            with self._pg_connect() as conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT cycle_id, started_at, completed_at, active_version_before, candidate_version, "
                    "top_cluster, cluster_sessions, FLOAT8(cluster_cost_eur) AS cluster_cost_eur, "
                    "FLOAT8(eval_before) AS eval_before, FLOAT8(eval_after) AS eval_after, decision, summary, diff_added "
                    "FROM coach_cycles ORDER BY cycle_id DESC LIMIT %s",
                    (limit,),
                )
                return [self._serialize_row(dict(r)) for r in cur.fetchall()]
        with self._lock:
            assert self._sqlite_conn is not None
            cur = self._sqlite_conn.cursor()
            cur.execute("SELECT * FROM coach_cycles ORDER BY cycle_id DESC LIMIT ?", (limit,))
            return [dict(r) for r in cur.fetchall()]

    def next_version_name(self) -> str:
        versions = self.list_prompt_versions()
        nums = [1]
        for v in versions:
            m = re.match(r"^v(\d+)$", str(v["version"]))
            if m:
                nums.append(int(m.group(1)))
        return f"v{max(nums) + 1}"

    def record_new_version_and_cycle(
        self,
        *,
        version: str,
        instruction: str,
        parent_version: str,
        target_cluster: str,
        reason: str,
        eval_score: float,
        baseline_score: float,
        sessions_analyzed: int,
        estimated_weekly_eur_saved: float,
        publish_immediately: bool,
        rule_added: str,
        summary: str,
    ) -> dict[str, Any]:
        now_iso = datetime.now(timezone.utc).isoformat()
        status = "active" if publish_immediately else "candidate"
        decision = "published" if publish_immediately else "pending_approval"

        if self._using_alloydb:
            with self._pg_connect() as conn, conn.cursor() as cur:
                if publish_immediately:
                    cur.execute("UPDATE prompt_versions SET status = 'superseded' WHERE status = 'active'")
                cur.execute(
                    """
                    INSERT INTO prompt_versions
                    (version, instruction, status, parent_version, target_cluster, reason,
                     eval_score, baseline_score, sessions_analyzed, estimated_weekly_eur_saved, activated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        version,
                        instruction,
                        status,
                        parent_version,
                        target_cluster,
                        reason,
                        eval_score,
                        baseline_score,
                        sessions_analyzed,
                        estimated_weekly_eur_saved,
                        datetime.now(timezone.utc) if publish_immediately else None,
                    ),
                )
                if publish_immediately and rule_added:
                    cur.execute(
                        "INSERT INTO agent_lessons (version_added, topic, rule_text) VALUES (%s, %s, %s)",
                        (version, target_cluster, rule_added),
                    )
                cur.execute(
                    """
                    INSERT INTO coach_cycles
                    (completed_at, active_version_before, candidate_version, top_cluster,
                     cluster_sessions, cluster_cost_eur, eval_before, eval_after, decision, summary, diff_added)
                    VALUES (NOW(), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        parent_version,
                        version,
                        target_cluster,
                        sessions_analyzed,
                        estimated_weekly_eur_saved,
                        baseline_score,
                        eval_score,
                        decision,
                        summary,
                        rule_added,
                    ),
                )
                conn.commit()
        else:
            with self._lock:
                assert self._sqlite_conn is not None
                cur = self._sqlite_conn.cursor()
                if publish_immediately:
                    cur.execute("UPDATE prompt_versions SET status = 'superseded' WHERE status = 'active'")
                cur.execute(
                    """
                    INSERT INTO prompt_versions
                    (version, instruction, status, parent_version, target_cluster, reason,
                     eval_score, baseline_score, sessions_analyzed, estimated_weekly_eur_saved, created_at, activated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        version,
                        instruction,
                        status,
                        parent_version,
                        target_cluster,
                        reason,
                        eval_score,
                        baseline_score,
                        sessions_analyzed,
                        estimated_weekly_eur_saved,
                        now_iso,
                        now_iso if publish_immediately else None,
                    ),
                )
                if publish_immediately and rule_added:
                    cur.execute(
                        "INSERT INTO agent_lessons (version_added, topic, rule_text, created_at) VALUES (?, ?, ?, ?)",
                        (version, target_cluster, rule_added, now_iso),
                    )
                cur.execute(
                    """
                    INSERT INTO coach_cycles
                    (started_at, completed_at, active_version_before, candidate_version, top_cluster,
                     cluster_sessions, cluster_cost_eur, eval_before, eval_after, decision, summary, diff_added)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        now_iso,
                        now_iso,
                        parent_version,
                        version,
                        target_cluster,
                        sessions_analyzed,
                        estimated_weekly_eur_saved,
                        baseline_score,
                        eval_score,
                        decision,
                        summary,
                        rule_added,
                    ),
                )
                self._sqlite_conn.commit()

        return self.get_active_prompt()

    def activate_or_rollback(self, target_version: str, mark_others_rolled_back: bool = True) -> dict[str, Any]:
        now_iso = datetime.now(timezone.utc).isoformat()
        other_status = "rolled_back" if mark_others_rolled_back else "superseded"
        if self._using_alloydb:
            with self._pg_connect() as conn, conn.cursor() as cur:
                cur.execute("UPDATE prompt_versions SET status = %s WHERE status = 'active'", (other_status,))
                cur.execute(
                    "UPDATE prompt_versions SET status = 'active', activated_at = NOW() WHERE version = %s",
                    (target_version,),
                )
                conn.commit()
        else:
            with self._lock:
                assert self._sqlite_conn is not None
                cur = self._sqlite_conn.cursor()
                cur.execute("UPDATE prompt_versions SET status = ? WHERE status = 'active'", (other_status,))
                cur.execute(
                    "UPDATE prompt_versions SET status = 'active', activated_at = ? WHERE version = ?",
                    (now_iso, target_version),
                )
                self._sqlite_conn.commit()
        return self.get_active_prompt()

    def reset_to_v1(self) -> dict[str, Any]:
        """Reset AlloyDB / mirror back to fresh v1 state for stage replay."""
        today = date.today()
        if self._using_alloydb:
            with self._pg_connect() as conn, conn.cursor() as cur:
                cur.execute("TRUNCATE TABLE refunds, agent_lessons, coach_cycles RESTART IDENTITY CASCADE")
                cur.execute("DELETE FROM prompt_versions WHERE version != 'v1'")
                cur.execute(
                    "UPDATE prompt_versions SET status = 'active', instruction = %s, activated_at = NOW() WHERE version = 'v1'",
                    (INITIAL_V1_INSTRUCTION,),
                )
                for oid, email, name, prod, amt, days_ago, st in SEED_ORDERS:
                    cur.execute(
                        "UPDATE orders SET status = %s, order_date = CURRENT_DATE - %s::int WHERE order_id = %s",
                        (st, days_ago, oid),
                    )
                conn.commit()
        else:
            with self._lock:
                assert self._sqlite_conn is not None
                cur = self._sqlite_conn.cursor()
                cur.execute("DELETE FROM refunds")
                cur.execute("DELETE FROM agent_lessons")
                cur.execute("DELETE FROM coach_cycles")
                cur.execute("DELETE FROM prompt_versions")
                self._seed_sqlite_locked(cur)
                self._sqlite_conn.commit()
        return self.get_active_prompt()

    # ------------------------------------------------------------------
    # Support Agent Operational Tools on AlloyDB
    # ------------------------------------------------------------------
    def find_orders_by_email(self, email: str) -> list[dict[str, Any]]:
        clean_email = email.strip().lower()
        today = date.today()
        if self._using_alloydb:
            with self._pg_connect() as conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT order_id, customer_email, customer_name, product_name, "
                    "FLOAT8(amount) AS amount, order_date, (CURRENT_DATE - order_date) AS age_days, status "
                    "FROM orders WHERE LOWER(customer_email) = %s ORDER BY order_date DESC",
                    (clean_email,),
                )
                return [self._serialize_row(dict(r)) for r in cur.fetchall()]
        with self._lock:
            assert self._sqlite_conn is not None
            cur = self._sqlite_conn.cursor()
            cur.execute(
                "SELECT * FROM orders WHERE LOWER(customer_email) = ? ORDER BY order_date DESC",
                (clean_email,),
            )
            rows = []
            for r in cur.fetchall():
                d = dict(r)
                odate = date.fromisoformat(d["order_date"])
                d["age_days"] = (today - odate).days
                rows.append(d)
            return rows

    def get_order(self, order_id: str) -> dict[str, Any]:
        # Strictly require ORD-XXXXX format so unnormalized IDs trigger TOOL_ERROR in v1/v2!
        trimmed = order_id.strip()
        if not ORDER_ID_PATTERN.match(trimmed):
            raise ValueError(
                f"Invalid order_id format '{order_id}'. Expected strict uppercase format 'ORD-XXXXX' (5 digits, e.g., ORD-10482)."
            )

        today = date.today()
        if self._using_alloydb:
            with self._pg_connect() as conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT order_id, customer_email, customer_name, product_name, "
                    "FLOAT8(amount) AS amount, order_date, (CURRENT_DATE - order_date) AS age_days, status "
                    "FROM orders WHERE order_id = %s",
                    (trimmed,),
                )
                row = cur.fetchone()
                if not row:
                    raise ValueError(f"Order {trimmed} not found.")
                return self._serialize_row(dict(row))

        with self._lock:
            assert self._sqlite_conn is not None
            cur = self._sqlite_conn.cursor()
            cur.execute("SELECT * FROM orders WHERE order_id = ?", (trimmed,))
            row = cur.fetchone()
            if not row:
                raise ValueError(f"Order {trimmed} not found.")
            d = dict(row)
            d["age_days"] = (today - date.fromisoformat(d["order_date"])).days
            return d

    def issue_refund(self, order_id: str, session_id: str, reason: str = "customer_request") -> dict[str, Any]:
        order = self.get_order(order_id)
        if order["status"] == "refunded":
            return {
                "status": "already_refunded",
                "order_id": order["order_id"],
                "amount_eur": order["amount"],
                "order_age_days": order["age_days"],
            }

        now_iso = datetime.now(timezone.utc).isoformat()
        if self._using_alloydb:
            with self._pg_connect() as conn, conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO refunds (order_id, session_id, amount, reason) VALUES (%s, %s, %s, %s) RETURNING refund_id",
                    (order["order_id"], session_id, order["amount"], reason),
                )
                refund_id = cur.fetchone()["refund_id"]
                cur.execute("UPDATE orders SET status = 'refunded' WHERE order_id = %s", (order["order_id"],))
                conn.commit()
        else:
            with self._lock:
                assert self._sqlite_conn is not None
                cur = self._sqlite_conn.cursor()
                cur.execute(
                    "INSERT INTO refunds (order_id, session_id, amount, reason, created_at) VALUES (?, ?, ?, ?, ?)",
                    (order["order_id"], session_id, order["amount"], reason, now_iso),
                )
                refund_id = cur.lastrowid
                cur.execute("UPDATE orders SET status = 'refunded' WHERE order_id = ?", (order["order_id"],))
                self._sqlite_conn.commit()

        return {
            "status": "refund_issued",
            "refund_id": refund_id,
            "order_id": order["order_id"],
            "amount_eur": float(order["amount"]),
            "order_date": str(order["order_date"]),
            "order_age_days": int(order["age_days"]),
            "session_id": session_id,
        }

    def grant_bonus_points(self, customer_email: str, points: int = 500, reason: str = "goodwill_credit") -> dict[str, Any]:
        clean_email = customer_email.strip().lower()
        pts = max(50, min(points, 2000))
        if self._using_alloydb:
            with self._pg_connect() as conn, conn.cursor() as cur:
                cur.execute(
                    "UPDATE customers SET loyalty_points = loyalty_points + %s WHERE LOWER(email) = %s "
                    "RETURNING email, full_name, loyalty_points",
                    (pts, clean_email),
                )
                row = cur.fetchone()
                conn.commit()
                if row:
                    return {
                        "status": "bonus_points_granted",
                        "email": row["email"],
                        "points_added": pts,
                        "new_balance": row["loyalty_points"],
                        "store_credit_equivalent_eur": round(pts * 0.05, 2),
                        "reason": reason,
                    }
        else:
            with self._lock:
                assert self._sqlite_conn is not None
                cur = self._sqlite_conn.cursor()
                cur.execute(
                    "UPDATE customers SET loyalty_points = loyalty_points + ? WHERE LOWER(email) = ?",
                    (pts, clean_email),
                )
                self._sqlite_conn.commit()
                cur.execute("SELECT email, full_name, loyalty_points FROM customers WHERE LOWER(email) = ?", (clean_email,))
                row = cur.fetchone()
                if row:
                    return {
                        "status": "bonus_points_granted",
                        "email": row["email"],
                        "points_added": pts,
                        "new_balance": row["loyalty_points"],
                        "store_credit_equivalent_eur": round(pts * 0.05, 2),
                        "reason": reason,
                    }
        return {
            "status": "bonus_points_granted",
            "email": clean_email,
            "points_added": pts,
            "store_credit_equivalent_eur": round(pts * 0.05, 2),
            "reason": reason,
        }

    def search_policies(self, query: str) -> list[dict[str, Any]]:
        q_tokens = {t for t in re.findall(r"[a-z0-9]+", query.lower()) if len(t) > 2}
        if self._using_alloydb:
            with self._pg_connect() as conn, conn.cursor() as cur:
                cur.execute("SELECT policy_id, category, title, content FROM store_policies")
                policies = [dict(r) for r in cur.fetchall()]
        else:
            with self._lock:
                assert self._sqlite_conn is not None
                cur = self._sqlite_conn.cursor()
                cur.execute("SELECT policy_id, category, title, content FROM store_policies")
                policies = [dict(r) for r in cur.fetchall()]

        scored = []
        for p in policies:
            haystack = f"{p['category']} {p['title']} {p['content']}".lower()
            score = sum(2 if tok in p["title"].lower() else 1 for tok in q_tokens if tok in haystack)
            scored.append((score, p))
        scored.sort(key=lambda item: item[0], reverse=True)
        top = [p for sc, p in scored if sc > 0]
        return top[:3] if top else policies[:3]

    def list_recent_refunds(self, minutes: int = 120) -> list[dict[str, Any]]:
        today = date.today()
        if self._using_alloydb:
            with self._pg_connect() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT r.refund_id, r.order_id, r.session_id, FLOAT8(r.amount) AS amount,
                           r.reason, r.created_at, o.order_date,
                           (CURRENT_DATE - o.order_date) AS order_age_days,
                           (CURRENT_DATE - o.order_date) > 30 AS policy_violation
                    FROM refunds r
                    JOIN orders o USING (order_id)
                    WHERE r.created_at > NOW() - (%s || ' minutes')::interval
                    ORDER BY r.created_at DESC
                    """,
                    (str(minutes),),
                )
                return [self._serialize_row(dict(r)) for r in cur.fetchall()]

        with self._lock:
            assert self._sqlite_conn is not None
            cur = self._sqlite_conn.cursor()
            cur.execute(
                """
                SELECT r.refund_id, r.order_id, r.session_id, r.amount, r.reason, r.created_at, o.order_date
                FROM refunds r
                JOIN orders o ON r.order_id = o.order_id
                ORDER BY r.refund_id DESC
                LIMIT 200
                """
            )
            results = []
            for row in cur.fetchall():
                d = dict(row)
                age = (today - date.fromisoformat(d["order_date"])).days
                d["order_age_days"] = age
                d["policy_violation"] = age > 30
                results.append(d)
            return results

    def list_orders(self) -> list[dict[str, Any]]:
        today = date.today()
        if self._using_alloydb:
            with self._pg_connect() as conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT order_id, customer_email, customer_name, product_name, "
                    "FLOAT8(amount) AS amount, order_date, (CURRENT_DATE - order_date) AS age_days, status "
                    "FROM orders ORDER BY order_date DESC"
                )
                return [self._serialize_row(dict(r)) for r in cur.fetchall()]
        with self._lock:
            assert self._sqlite_conn is not None
            cur = self._sqlite_conn.cursor()
            cur.execute("SELECT * FROM orders ORDER BY order_date DESC")
            out = []
            for r in cur.fetchall():
                d = dict(r)
                d["age_days"] = (today - date.fromisoformat(d["order_date"])).days
                out.append(d)
            return out

    @staticmethod
    def _serialize_row(row: dict[str, Any]) -> dict[str, Any]:
        for k, v in list(row.items()):
            if isinstance(v, (datetime, date)):
                row[k] = v.isoformat()
        return row


db = OperationalDB()
