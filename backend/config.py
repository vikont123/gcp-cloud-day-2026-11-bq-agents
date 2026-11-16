"""Configuration & automatic GCP Service Account JSON detection."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

import certifi
from dotenv import load_dotenv

os.environ.setdefault("SSL_CERT_FILE", certifi.where())
os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")


def discover_service_account_json() -> Optional[Path]:
    """Find a GCP service-account JSON key in env or workspace root."""
    env_cred = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if env_cred and Path(env_cred).is_file():
        return Path(env_cred).resolve()

    preferred = [
        ROOT_DIR / "gcp-key.json",
        ROOT_DIR / "service-account.json",
        ROOT_DIR / "credentials.json",
    ]
    for candidate in preferred:
        if candidate.is_file():
            return candidate.resolve()

    for candidate in sorted(ROOT_DIR.glob("*.json")):
        if candidate.name in {"package.json", "package-lock.json", "tsconfig.json"}:
            continue
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
            if isinstance(payload, dict) and payload.get("type") == "service_account" and payload.get("project_id"):
                return candidate.resolve()
        except Exception:
            continue
    return None


class Settings:
    """Runtime settings dynamically refreshed when gcp-key.json arrives."""

    def __init__(self) -> None:
        self.reload()

    def reload(self) -> None:
        load_dotenv(ROOT_DIR / ".env", override=True)
        self.key_file: Optional[Path] = discover_service_account_json()
        key_project: Optional[str] = None
        if self.key_file:
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(self.key_file)
            try:
                data = json.loads(self.key_file.read_text(encoding="utf-8"))
                key_project = data.get("project_id")
            except Exception:
                pass

        env_file = ROOT_DIR / ".env"
        has_explicit_env = env_file.is_file() or bool(os.environ.get("K_SERVICE"))

        if self.key_file or has_explicit_env:
            self.project_id: str = (
                key_project
                or os.environ.get("GCP_PROJECT_ID")
                or os.environ.get("GOOGLE_CLOUD_PROJECT")
                or ""
            )
        else:
            self.project_id = ""

        self.region: str = os.environ.get("GCP_REGION", "us-central1")
        self.bq_location: str = os.environ.get("BQ_LOCATION", "us-central1")
        self.bq_dataset: str = os.environ.get("BQ_DATASET", "agent_analytics")
        self.bq_table: str = os.environ.get("BQ_TABLE", "agent_events")
        self.bq_business_dataset: str = os.environ.get("BQ_BUSINESS_DATASET", "business")
        self.bq_alloydb_conn: str = os.environ.get("BQ_ALLOYDB_CONN", "alloydb-conn")
        self.bq_vertex_conn: str = os.environ.get("BQ_VERTEX_CONN", "vertex-ai-conn")

        self.gemini_model: str = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")
        self.gemini_location: str = os.environ.get("GEMINI_LOCATION", "global")

        if self.project_id:
            os.environ["GOOGLE_CLOUD_PROJECT"] = self.project_id
            os.environ["GOOGLE_CLOUD_LOCATION"] = self.gemini_location
            os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "TRUE")

        self.alloydb_host: str = os.environ.get("ALLOYDB_HOST", "")
        self.alloydb_port: int = int(os.environ.get("ALLOYDB_PORT", "5432"))
        self.alloydb_db: str = os.environ.get("ALLOYDB_DB", "postgres")
        self.alloydb_user: str = os.environ.get("ALLOYDB_USER", "postgres")
        self.alloydb_password: str = os.environ.get("ALLOYDB_PASSWORD", "ChangeMeDemo2026!")

        self.auto_publish_coach: bool = os.environ.get("AUTO_PUBLISH_COACH", "true").lower() == "true"
        self.sqlite_fallback_path: Path = ROOT_DIR / "data" / "alloydb_mirror.sqlite"


settings = Settings()
