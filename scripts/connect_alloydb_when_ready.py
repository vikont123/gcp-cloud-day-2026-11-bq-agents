#!/usr/bin/env python3
"""Background watcher: ensures AlloyDB primary instance exists, waits for READY, seeds schema, creates BQ federated connection, and hot-reloads backend."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request

import certifi
import google.auth
from google.auth.transport.requests import Request as GoogleAuthRequest
import psycopg2

os.environ.setdefault("SSL_CERT_FILE", certifi.where())
os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())

ROOT_DIR = Path(__file__).resolve().parent.parent


def run_cmd(cmd: list[str], check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, env=os.environ.copy(), text=True, capture_output=True, check=check)


def get_external_ip() -> str:
    for url in ("https://api.ipify.org", "https://ifconfig.me/ip"):
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                ip = resp.read().decode("utf-8").strip()
                if ip:
                    return ip
        except Exception:
            continue
    return ""


def ensure_bq_alloydb_connection(project_id: str, region: str, cluster: str, instance: str, password: str) -> None:
    sa_path = ROOT_DIR / "gcp-key.json"
    if sa_path.is_file():
        os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", str(sa_path))
    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    creds.refresh(GoogleAuthRequest())
    url = (
        f"https://bigqueryconnection.googleapis.com/v1/projects/{project_id}"
        f"/locations/{region}/connections?connectionId=alloydb-conn"
    )
    payload = {
        "friendlyName": "AlloyDB Operational Store",
        "description": "Federated connection from BigQuery to AlloyDB for Self-Improving Agent Demo",
        "configuration": {
            "connectorId": "google-alloydb",
            "asset": {
                "database": "postgres",
                "googleCloudResource": (
                    f"//alloydb.googleapis.com/projects/{project_id}/locations/{region}"
                    f"/clusters/{cluster}/instances/{instance}"
                ),
            },
            "authentication": {
                "usernamePassword": {
                    "username": "postgres",
                    "password": {"plaintext": password},
                }
            },
        },
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {creds.token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            print("✓ Created BigQuery AlloyDB federated connection:", resp.read().decode("utf-8")[:200])
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        if exc.code == 409 or "ALREADY_EXISTS" in body:
            print("✓ BigQuery AlloyDB federated connection alloydb-conn already exists.")
        else:
            print(f"Note creating BQ AlloyDB connection ({exc.code}): {body[:240]}")


def main() -> int:
    project_id = os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip()
    if not project_id:
        print("GOOGLE_CLOUD_PROJECT must be set.", file=sys.stderr)
        return 1
    region = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
    cluster = "agent-demo-cluster"
    instance = "agent-demo-primary"
    password = os.environ.get("ALLOYDB_PASSWORD", "ChangeMeDemo2026!")

    # Wait for cluster READY first
    for attempt in range(40):
        c_res = run_cmd(
            [
                "gcloud",
                "alloydb",
                "clusters",
                "describe",
                cluster,
                f"--region={region}",
                f"--project={project_id}",
                "--format=value(state)",
            ]
        )
        c_state = c_res.stdout.strip() if c_res.returncode == 0 else "UNKNOWN"
        if c_state == "READY":
            print(f"✓ AlloyDB cluster {cluster} is READY.")
            break
        print(f"[cluster {attempt + 1}] state={c_state}")
        time.sleep(15)

    # Ensure primary instance exists (try ZONAL cpu=2, then cpu=4 if n2-highmem-2 pool is exhausted)
    cpu_counts = [2, 4]
    my_ip = get_external_ip()
    public_ip = ""

    for cpu in cpu_counts:
        desc_res = run_cmd(
            [
                "gcloud",
                "alloydb",
                "instances",
                "describe",
                instance,
                f"--cluster={cluster}",
                f"--region={region}",
                f"--project={project_id}",
                "--format=json",
            ]
        )
        if desc_res.returncode != 0:
            print(f"Creating AlloyDB ZONAL primary instance {instance} (cpu={cpu}, authorized IP: {my_ip}/32)...")
            create_cmd = [
                "gcloud",
                "alloydb",
                "instances",
                "create",
                instance,
                f"--cluster={cluster}",
                f"--region={region}",
                "--instance-type=PRIMARY",
                "--availability-type=ZONAL",
                f"--cpu-count={cpu}",
                "--assign-inbound-public-ip=ASSIGN_IPV4",
                "--database-flags=password.enforce_complexity=on,alloydb.iam_authentication=on",
                f"--project={project_id}",
                "--async",
            ]
            if my_ip:
                create_cmd.append(f"--authorized-external-networks={my_ip}/32")
            cr = run_cmd(create_cmd)
            print("Instance create triggered:", (cr.stdout or cr.stderr).strip()[:240])

        print(f"Watching AlloyDB instance {cluster}/{instance} (cpu={cpu})...")
        not_found_streak = 0
        for attempt in range(45):
            res = run_cmd(
                [
                    "gcloud",
                    "alloydb",
                    "instances",
                    "describe",
                    instance,
                    f"--cluster={cluster}",
                    f"--region={region}",
                    f"--project={project_id}",
                    "--format=json",
                ]
            )
            if res.returncode == 0:
                not_found_streak = 0
                info = json.loads(res.stdout)
                state = info.get("state", "UNKNOWN")
                public_ip = info.get("publicIpAddress", "")
                print(f"[cpu={cpu} #{attempt + 1}] state={state} publicIp={public_ip}")
                if state == "READY" and public_ip:
                    break
            else:
                not_found_streak += 1
                print(f"[cpu={cpu} #{attempt + 1}] instance not present ({not_found_streak}/3)...")
                if attempt >= 2 and not_found_streak >= 2:
                    print(f"ZONAL cpu={cpu} rolled back; trying next cpu pool...")
                    break
            time.sleep(20)

        if public_ip:
            break

    if not public_ip:
        print("Timed out waiting for AlloyDB public IP.", file=sys.stderr)
        return 1

    print(f"AlloyDB READY at {public_ip}! Seeding sql/alloydb_init.sql...")
    sql_text = (ROOT_DIR / "sql" / "alloydb_init.sql").read_text(encoding="utf-8")
    with psycopg2.connect(
        host=public_ip,
        port=5432,
        dbname="postgres",
        user="postgres",
        password=password,
        connect_timeout=10,
    ) as conn:
        with conn.cursor() as cur:
            cur.execute(sql_text)
        conn.commit()
    print("✓ Seeded AlloyDB schema, orders, policies, and v1 prompt!")

    # Update .env with ALLOYDB_HOST
    env_path = ROOT_DIR / ".env"
    if env_path.is_file():
        lines = []
        replaced = False
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("ALLOYDB_HOST="):
                lines.append(f"ALLOYDB_HOST={public_ip}")
                replaced = True
            else:
                lines.append(line)
        if not replaced:
            lines.append(f"ALLOYDB_HOST={public_ip}")
        env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"✓ Updated {env_path} with ALLOYDB_HOST={public_ip}")

    # Create BigQuery AlloyDB federated connection
    ensure_bq_alloydb_connection(project_id, region, cluster, instance, password)

    # Grant BigQuery Connection Service Agent roles/alloydb.client
    proj_num_res = run_cmd(["gcloud", "projects", "describe", project_id, "--format=value(projectNumber)"])
    if proj_num_res.returncode == 0 and proj_num_res.stdout.strip():
        pnum = proj_num_res.stdout.strip()
        bq_sa = f"service-{pnum}@gcp-sa-bigqueryconnection.iam.gserviceaccount.com"
        run_cmd(
            [
                "gcloud",
                "projects",
                "add-iam-policy-binding",
                project_id,
                f"--member=serviceAccount:{bq_sa}",
                "--role=roles/alloydb.client",
                "--condition=None",
                "--quiet",
            ]
        )

    # Hot-reload running FastAPI server if up
    try:
        req = urllib.request.Request("http://127.0.0.1:8000/api/gcp/reload", method="POST", data=b"{}")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=10) as resp:
            print("✓ Hot-reloaded running backend:", resp.read().decode())
    except Exception as exc:
        print(f"Backend reload note: {exc}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
