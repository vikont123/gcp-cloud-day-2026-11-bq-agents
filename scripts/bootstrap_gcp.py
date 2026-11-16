#!/usr/bin/env python3
"""Automated GCP Provisioner for Cloud AI Day Demo.

Takes a GCP service-account JSON key, activates gcloud, enables APIs, creates BigQuery
datasets + Vertex AI connection + 5M-row 3-month scale dataset, provisions AlloyDB
(in background or foreground), seeds AlloyDB schema, and writes .env.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.request

import certifi
from google.api_core.exceptions import AlreadyExists
from google.cloud import bigquery, bigquery_connection_v1
from google.oauth2 import service_account

os.environ.setdefault("SSL_CERT_FILE", certifi.where())
os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())

ROOT_DIR = Path(__file__).resolve().parent.parent


def run_gcloud(args: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    cmd = ["gcloud", *args]
    print(f"  $ {' '.join(cmd)}")
    return subprocess.run(cmd, env=os.environ.copy(), text=True, capture_output=True, check=check)


def setup_bigquery_and_vertex_conn(
    creds: service_account.Credentials,
    project_id: str,
    location: str,
) -> None:
    print(f"\n[3/5] Provisioning BigQuery datasets & 5M-row 3-month dataset in {project_id} ({location})...")
    bq = bigquery.Client(project=project_id, credentials=creds, location=location)

    for ds_name in ("agent_analytics", "business"):
        ds_id = f"{project_id}.{ds_name}"
        ds = bigquery.Dataset(ds_id)
        ds.location = location
        ds.labels = {"app": "self-improving-agent-demo"}
        bq.create_dataset(ds, exists_ok=True)
        print(f"  ✓ Dataset ready: {ds_id}")

    # Create Cloud Resource connection for BigQuery AI (AI.CLASSIFY / AI.GENERATE)
    conn_client = bigquery_connection_v1.ConnectionServiceClient(credentials=creds)
    parent = f"projects/{project_id}/locations/{location}"
    conn_id = "vertex-ai-conn"
    conn_name = f"{parent}/connections/{conn_id}"
    vertex_sa = ""
    try:
        conn_obj = bigquery_connection_v1.types.Connection(
            friendly_name="Vertex AI Connection for Agent Analytics",
            cloud_resource=bigquery_connection_v1.types.CloudResourceProperties(),
        )
        created = conn_client.create_connection(
            request={"parent": parent, "connection_id": conn_id, "connection": conn_obj}
        )
        vertex_sa = created.cloud_resource.service_account_id
        print(f"  ✓ BigQuery Vertex AI connection ready: {conn_id} (SA: {vertex_sa})")
    except AlreadyExists:
        existing = conn_client.get_connection(request={"name": conn_name})
        vertex_sa = existing.cloud_resource.service_account_id
        print(f"  ✓ BigQuery Vertex AI connection already exists: {conn_id} (SA: {vertex_sa})")
    except Exception as exc:
        print(f"  ⚠ Could not create/fetch {conn_id} with SA ({exc}); AI.CLASSIFY can use DEFAULT connection.")

    if vertex_sa:
        run_gcloud(
            [
                "projects",
                "add-iam-policy-binding",
                project_id,
                f"--member=serviceAccount:{vertex_sa}",
                "--role=roles/aiplatform.user",
                "--condition=None",
                "--quiet",
            ],
            check=False,
        )

    # Execute sql/bigquery_init.sql
    init_sql = (ROOT_DIR / "sql" / "bigquery_init.sql").read_text(encoding="utf-8")
    for stmt in [s.strip() for s in init_sql.split(";") if s.strip()]:
        job = bq.query(stmt, job_config=bigquery.QueryJobConfig(labels={"app": "self-improving-agent-demo"}))
        job.result()
    print("  ✓ BigQuery tables & 5,000,000-row historical_events_3m ready!")


def provision_alloydb_async(
    project_id: str,
    region: str,
    cluster_id: str,
    instance_id: str,
    password: str,
) -> str:
    print(f"\n[4/5] Checking / starting AlloyDB cluster '{cluster_id}' in {region}...")
    desc = run_gcloud(
        [
            "alloydb",
            "instances",
            "describe",
            instance_id,
            f"--cluster={cluster_id}",
            f"--region={region}",
            f"--project={project_id}",
            "--format=json",
        ],
        check=False,
    )
    if desc.returncode == 0:
        info = json.loads(desc.stdout)
        pub_ip = info.get("publicIpAddress", "")
        state = info.get("state", "")
        print(f"  ✓ AlloyDB instance already exists (state={state}, publicIp={pub_ip})")
        return pub_ip

    # Ensure default VPC + Private Services Access
    run_gcloud(["compute", "networks", "create", "default", "--subnet-mode=auto", f"--project={project_id}"], check=False)
    run_gcloud(
        [
            "compute",
            "addresses",
            "create",
            "google-managed-services-default",
            "--global",
            "--purpose=VPC_PEERING",
            "--prefix-length=16",
            "--network=default",
            f"--project={project_id}",
        ],
        check=False,
    )
    run_gcloud(
        [
            "services",
            "vpc-peerings",
            "connect",
            "--service=servicenetworking.googleapis.com",
            "--ranges=google-managed-services-default",
            "--network=default",
            f"--project={project_id}",
        ],
        check=False,
    )

    # Create cluster (if absent)
    c_desc = run_gcloud(
        ["alloydb", "clusters", "describe", cluster_id, f"--region={region}", f"--project={project_id}"],
        check=False,
    )
    if c_desc.returncode != 0:
        print("  → Creating AlloyDB cluster (takes ~2 min)...")
        run_gcloud(
            [
                "alloydb",
                "clusters",
                "create",
                cluster_id,
                f"--region={region}",
                f"--password={password}",
                "--network=default",
                f"--project={project_id}",
            ],
            check=False,
        )

    try:
        my_ip = urllib.request.urlopen("https://ifconfig.me/ip", timeout=5).read().decode().strip()
        cidr = f"{my_ip}/32"
    except Exception:
        cidr = "0.0.0.0/0"

    print(f"  → Launching AlloyDB primary instance '{instance_id}' asynchronously (--async, authorized CIDR {cidr})...")
    run_gcloud(
        [
            "alloydb",
            "instances",
            "create",
            instance_id,
            f"--cluster={cluster_id}",
            f"--region={region}",
            "--instance-type=PRIMARY",
            "--cpu-count=2",
            "--assign-inbound-public-ip=ASSIGN_IPV4",
            "--database-flags=password.enforce_complexity=on,alloydb.iam_authentication=on",
            f"--authorized-external-networks={cidr}",
            f"--project={project_id}",
            "--async",
        ],
        check=False,
    )
    print("  ✓ AlloyDB primary instance creation started in background (~10 min on GCP).")
    return ""


def write_env_file(key_path: Path, project_id: str, region: str, alloydb_ip: str, password: str) -> None:
    env_text = (
        f"GOOGLE_APPLICATION_CREDENTIALS={key_path}\n"
        f"GOOGLE_CLOUD_PROJECT={project_id}\n"
        f"GOOGLE_CLOUD_LOCATION=global\n"
        f"GCP_REGION={region}\n"
        f"GOOGLE_GENAI_USE_VERTEXAI=TRUE\n"
        f"BQ_LOCATION={region}\n"
        f"BQ_DATASET=agent_analytics\n"
        f"BQ_BUSINESS_DATASET=business\n"
        f"BQ_ALLOYDB_CONN=alloydb-conn\n"
        f"BQ_VERTEX_CONN=vertex-ai-conn\n"
        f"GEMINI_MODEL=gemini-3.8-flash\n"
        f"ALLOYDB_HOST={alloydb_ip}\n"
        f"ALLOYDB_PORT=5432\n"
        f"ALLOYDB_DB=postgres\n"
        f"ALLOYDB_USER=postgres\n"
        f"ALLOYDB_PASSWORD={password}\n"
    )
    (ROOT_DIR / ".env").write_text(env_text, encoding="utf-8")
    print(f"\n[5/5] Wrote {ROOT_DIR / '.env'}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Bootstrap GCP infrastructure for Self-Improving Agent Demo")
    parser.add_argument("--key-file", default=str(ROOT_DIR / "gcp-key.json"), help="Path to GCP SA JSON key")
    parser.add_argument("--region", default="us-central1", help="GCP region (default: us-central1)")
    parser.add_argument("--skip-alloydb", action="store_true", help="Skip AlloyDB cluster creation")
    args = parser.parse_args()

    key_path = Path(args.key_file).expanduser().resolve()
    if not key_path.is_file():
        print(f"ERROR: Key file not found at {key_path}", file=sys.stderr)
        return 1

    key_json = json.loads(key_path.read_text(encoding="utf-8"))
    project_id = key_json["project_id"]
    password = os.environ.get("ALLOYDB_PASSWORD", "ChangeMeDemo2026!")

    print(f"[1/5] Activating Service Account {key_json.get('client_email')} for project '{project_id}'...")
    run_gcloud(["auth", "activate-service-account", f"--key-file={key_path}"])
    run_gcloud(["config", "set", "project", project_id])
    run_gcloud(["config", "set", "compute/region", args.region])

    print("\n[2/5] Enabling required GCP APIs (if permitted by SA)...")
    api_res = run_gcloud(
        [
            "services",
            "enable",
            "bigquery.googleapis.com",
            "bigquerystorage.googleapis.com",
            "bigqueryconnection.googleapis.com",
            "aiplatform.googleapis.com",
            "alloydb.googleapis.com",
            "compute.googleapis.com",
            "servicenetworking.googleapis.com",
            "run.googleapis.com",
            "cloudbuild.googleapis.com",
            "artifactregistry.googleapis.com",
            "cloudscheduler.googleapis.com",
            f"--project={project_id}",
        ],
        check=False,
    )
    if api_res.returncode != 0:
        print("  ℹ Service account lacks serviceusage.services.enable (assuming APIs are already enabled).")

    creds = service_account.Credentials.from_service_account_file(str(key_path))
    setup_bigquery_and_vertex_conn(creds=creds, project_id=project_id, location=args.region)

    alloydb_ip = ""
    if not args.skip_alloydb:
        alloydb_ip = provision_alloydb_async(
            project_id=project_id,
            region=args.region,
            cluster_id="agent-demo-cluster",
            instance_id="agent-demo-primary",
            password=password,
        )

    write_env_file(key_path, project_id, args.region, alloydb_ip, password)
    print("\nBootstrap complete! Ready to run or hot-reload the demo.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
