# Google Cloud AI Day Demo — `gcloud` & `bq` Setup Runbook

**Demo:** *"Self-improving Agent: BigQuery teaches, AlloyDB remembers"*

This runbook contains every `gcloud` and `bq` command required to provision, seed, and launch the full demo in a Google Cloud project using a Service Account JSON key (`gcp-key.json`).

> [!TIP]
> **Automated One-Command Option:** Once you place your service account key at `./gcp-key.json`, you can run `./scripts/start_demo.sh` (or `python scripts/bootstrap_gcp.py --key-file ./gcp-key.json`) to execute the provisioning, database seeding, BigQuery setup, and launch automatically.

---

## 0. Authenticate with Service Account JSON Key

Place your GCP Service Account JSON key in the project root as `gcp-key.json` (git-ignored) and export environment variables:

```bash
export KEY_FILE="$(pwd)/gcp-key.json"
export GOOGLE_APPLICATION_CREDENTIALS="$KEY_FILE"
export PROJECT_ID=$(python3 -c "import json; print(json.load(open('$KEY_FILE'))['project_id'])")
export REGION="us-central1"
export BQ_LOCATION="us-central1"

# AlloyDB settings
export ALLOYDB_CLUSTER="agent-demo-cluster"
export ALLOYDB_INSTANCE="agent-demo-primary"
export ALLOYDB_DB="postgres"
export ALLOYDB_USER="postgres"
export ALLOYDB_PASSWORD="ChangeMeDemo2026!"
export VPC_NETWORK="default"

# Activate credentials in gcloud
gcloud auth activate-service-account --key-file="$KEY_FILE"
gcloud config set project "$PROJECT_ID"
gcloud config set compute/region "$REGION"
```

<details>
<summary><b>Optional: How to generate <code>gcp-key.json</code> if you don't have one yet</b></summary>

```bash
export PROJECT_ID="YOUR_PROJECT_ID"
export SA_NAME="cloud-ai-day-demo"
export SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

gcloud iam service-accounts create "$SA_NAME" \
  --display-name="Cloud AI Day Demo Runner" \
  --project="$PROJECT_ID"

for ROLE in \
  roles/alloydb.admin \
  roles/bigquery.admin \
  roles/bigquery.connectionAdmin \
  roles/aiplatform.user \
  roles/geminidataanalytics.dataAgentStatelessUser \
  roles/cloudaicompanion.user \
  roles/run.admin \
  roles/cloudbuild.builds.editor \
  roles/artifactregistry.admin \
  roles/cloudscheduler.admin \
  roles/compute.networkAdmin \
  roles/servicenetworking.networksAdmin \
  roles/iam.serviceAccountUser \
  roles/resourcemanager.projectIamAdmin; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="$ROLE" --condition=None --quiet
done

gcloud iam service-accounts keys create ./gcp-key.json \
  --iam-account="$SA_EMAIL" \
  --project="$PROJECT_ID"
```
</details>

---

## 1. Enable Required Google Cloud APIs

```bash
gcloud services enable \
  alloydb.googleapis.com \
  compute.googleapis.com \
  servicenetworking.googleapis.com \
  bigquery.googleapis.com \
  bigquerystorage.googleapis.com \
  bigqueryconnection.googleapis.com \
  aiplatform.googleapis.com \
  geminidataanalytics.googleapis.com \
  cloudaicompanion.googleapis.com \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  cloudscheduler.googleapis.com \
  --project="$PROJECT_ID"
```

---

## 2. Configure VPC Private Services Access & Provision AlloyDB

AlloyDB requires Private Services Access on the VPC network. We also enable **Inbound Public IP** on the primary instance so both Cloud Run and local development machines can connect cleanly alongside BigQuery `EXTERNAL_QUERY`.

```bash
# 2.1 Ensure VPC network exists (uses 'default' or creates it if absent)
gcloud compute networks describe "$VPC_NETWORK" --project="$PROJECT_ID" >/dev/null 2>&1 || \
  gcloud compute networks create "$VPC_NETWORK" --subnet-mode=auto --project="$PROJECT_ID"

# 2.2 Allocate IP range for Private Services Access
gcloud compute addresses create google-managed-services-${VPC_NETWORK} \
  --global \
  --purpose=VPC_PEERING \
  --prefix-length=16 \
  --network="$VPC_NETWORK" \
  --project="$PROJECT_ID" || true

# 2.3 Connect VPC peering to servicenetworking
gcloud services vpc-peerings connect \
  --service=servicenetworking.googleapis.com \
  --ranges=google-managed-services-${VPC_NETWORK} \
  --network="$VPC_NETWORK" \
  --project="$PROJECT_ID" || true

# 2.4 Create AlloyDB Cluster (~2-3 min)
gcloud alloydb clusters create "$ALLOYDB_CLUSTER" \
  --region="$REGION" \
  --password="$ALLOYDB_PASSWORD" \
  --network="$VPC_NETWORK" \
  --project="$PROJECT_ID"

# 2.5 Create AlloyDB Primary Instance with Inbound Public IP (~8-12 min)
MY_CIDR="$(curl -s https://ifconfig.me)/32"

gcloud alloydb instances create "$ALLOYDB_INSTANCE" \
  --cluster="$ALLOYDB_CLUSTER" \
  --region="$REGION" \
  --instance-type=PRIMARY \
  --availability-type=ZONAL \
  --cpu-count=2 \
  --assign-inbound-public-ip=ASSIGN_IPV4 \
  --authorized-external-networks="${MY_CIDR}" \
  --database-flags=password.enforce_complexity=on,alloydb.iam_authentication=on \
  --project="$PROJECT_ID"

# 2.6 Fetch AlloyDB Public & Private IPs
export ALLOYDB_PUBLIC_IP=$(gcloud alloydb instances describe "$ALLOYDB_INSTANCE" \
  --cluster="$ALLOYDB_CLUSTER" \
  --region="$REGION" \
  --project="$PROJECT_ID" \
  --format="value(publicIpAddress)")

echo "AlloyDB Public IP: $ALLOYDB_PUBLIC_IP"
```

### Seed AlloyDB Schema & Initial Flawed Prompt (`v1`)

```bash
PGPASSWORD="$ALLOYDB_PASSWORD" psql \
  -h "$ALLOYDB_PUBLIC_IP" \
  -U "$ALLOYDB_USER" \
  -d "$ALLOYDB_DB" \
  -f sql/alloydb_init.sql
```

*(If `psql` is not installed locally, `python scripts/bootstrap_gcp.py` executes `sql/alloydb_init.sql` directly over Python `psycopg2`.)*

---

## 3. Set Up BigQuery Datasets, Connections (`EXTERNAL_QUERY` + Vertex AI), and Scale Data

### 3.1 Create Datasets (`agent_analytics` and `business`)

```bash
bq --location="$BQ_LOCATION" mk --dataset "${PROJECT_ID}:agent_analytics" || true
bq --location="$BQ_LOCATION" mk --dataset "${PROJECT_ID}:business" || true
```

### 3.2 Create BigQuery → AlloyDB Federated Connection (`EXTERNAL_QUERY`)

```bash
export PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format="value(projectNumber)")

bq mk \
  --connection \
  --location="$BQ_LOCATION" \
  --project_id="$PROJECT_ID" \
  --connector_configuration "{
    \"connector_id\": \"google-alloydb\",
    \"asset\": {
      \"database\": \"${ALLOYDB_DB}\",
      \"google_cloud_resource\": \"//alloydb.googleapis.com/projects/${PROJECT_ID}/locations/${REGION}/clusters/${ALLOYDB_CLUSTER}/instances/${ALLOYDB_INSTANCE}\"
    },
    \"authentication\": {
      \"username_password\": {
        \"username\": \"${ALLOYDB_USER}\",
        \"password\": {
          \"plaintext\": \"${ALLOYDB_PASSWORD}\"
        }
      }
    }
  }" \
  alloydb-conn

# Grant BigQuery Connection Service Agent permission to connect to AlloyDB
export BQ_CONN_SA="service-${PROJECT_NUMBER}@gcp-sa-bigqueryconnection.iam.gserviceaccount.com"

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${BQ_CONN_SA}" \
  --role="roles/alloydb.client"
```

### 3.3 Create BigQuery → Vertex AI Connection (for `AI.CLASSIFY`, `AI.GENERATE`, `AI.EMBED`)

```bash
bq mk \
  --connection \
  --connection_type=CLOUD_RESOURCE \
  --location="$BQ_LOCATION" \
  --project_id="$PROJECT_ID" \
  vertex-ai-conn

export VERTEX_CONN_SA=$(bq show --format=json --connection "${PROJECT_ID}.${BQ_LOCATION}.vertex-ai-conn" | \
  python3 -c "import sys, json; print(json.load(sys.stdin)['cloudResource']['serviceAccountId'])")

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${VERTEX_CONN_SA}" \
  --role="roles/aiplatform.user"
```

### 3.4 Initialize BigQuery Tables & 3-Month Synthetic Scale Dataset

```bash
bq query --location="$BQ_LOCATION" --use_legacy_sql=false < sql/bigquery_init.sql
```

---

## 4. Run Locally (Connected to Live BigQuery + AlloyDB + Vertex AI)

```bash
./scripts/start_demo.sh
```
Opens:
- **Interactive 4-Step Stage Wizard & Ask BigQuery Mode:** `http://localhost:5173` (or `http://localhost:8080` in single-container mode)
- **FastAPI Backend & OpenAPI Docs:** `http://localhost:8000/docs`

---

## 5. Deploy to Cloud Run + Cloud Scheduler (Production Stage Setup)

```bash
# 5.1 Deploy Support Agent + Simulator + Web UI to Cloud Run Service
gcloud run deploy self-improving-agent-demo \
  --source=. \
  --region="$REGION" \
  --project="$PROJECT_ID" \
  --network="$VPC_NETWORK" \
  --vpc-egress=private-ranges-only \
  --allow-unauthenticated \
  --set-env-vars="GOOGLE_CLOUD_PROJECT=${PROJECT_ID},GOOGLE_CLOUD_LOCATION=global,GCP_REGION=${REGION},GOOGLE_GENAI_USE_VERTEXAI=TRUE,BQ_DATASET=agent_analytics,BQ_BUSINESS_DATASET=business,BQ_LOCATION=${BQ_LOCATION},ALLOYDB_DB=${ALLOYDB_DB},ALLOYDB_USER=${ALLOYDB_USER},ALLOYDB_PASSWORD=${ALLOYDB_PASSWORD}"

export DEMO_URL=$(gcloud run services describe self-improving-agent-demo \
  --region="$REGION" \
  --project="$PROJECT_ID" \
  --format="value(status.url)")

# 5.2 Create Cloud Scheduler Job to trigger Coach improvement cycle every 3 minutes
gcloud scheduler jobs create http coach-improvement-cycle \
  --location="$REGION" \
  --schedule="*/3 * * * *" \
  --uri="${DEMO_URL}/api/coach/run-cycle" \
  --http-method=POST \
  --attempt-deadline=180s \
  --project="$PROJECT_ID"

echo "Demo is live at: $DEMO_URL"
```

---

## 6. Stage Cheat-Sheet (4-Step Guided Wizard + Ask BigQuery)

| Step | Action on Screen | Where |
|---|---|---|
| **Step 1** | Click **`Show Full Agent Instruction (v1)`** to show the naive AlloyDB prompt, then click **`Test Agent Response (v1)`** on 45-day order `ORD-10482` → `v1` wrongly refunds €89 while CSAT stays `5/5 ★` | **`1. Agent Mistake`** |
| **Step 2** | Click **`Run BigQuery Cost Analysis`** → `AI.CLASSIFY` + `EXTERNAL_QUERY` joins BigQuery traces with AlloyDB `refunds`, revealing **-€17,040 / week** lost to >30-day refunds | **`2. BigQuery Audit`** |
| **Step 3** | Click **`Train Agent (v1 → v2)`** → live 6-stage SSE stream drafts minimal rule, runs **Safety Check on 10 Test Orders** (`40% → 70%`, expandable row-by-row), and activates `v2` in AlloyDB with **0 redeploys** | **`3. Coach Training`** |
| **Step 4** | Click **`Test Upgraded Agent (v2)`** on the exact same order `ORD-10482` → side-by-side `v1` (-€89) vs `v2` (€89 saved + 500 loyalty points) | **`4. Before / After`** |
| **Bonus** | Click **`Ask BigQuery`** in the top bar → Gemini Data Analytics Conversational Analytics API writes SQL & renders live charts from natural-language questions | **`Ask BigQuery` Mode** |
