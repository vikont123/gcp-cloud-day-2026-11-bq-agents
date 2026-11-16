-- BigQuery Initialization Script for "Self-improving Agent: BigQuery teaches, AlloyDB remembers"
-- Creates business & analytics tables + 3-month synthetic production history for Act 3

CREATE TABLE IF NOT EXISTS `business.csat_surveys` (
  session_id STRING NOT NULL,
  customer_email STRING,
  prompt_version STRING,
  scenario_type STRING,
  score INT64 NOT NULL,
  comment STRING,
  wrongful_refund_eur NUMERIC,
  created_at TIMESTAMP NOT NULL
)
PARTITION BY DATE(created_at)
CLUSTER BY prompt_version, scenario_type;

CREATE TABLE IF NOT EXISTS `agent_analytics.session_outcomes` (
  session_id STRING NOT NULL,
  prompt_version STRING,
  outcome STRING NOT NULL,
  tool_errors INT64,
  turn_count INT64,
  transcript STRING,
  evaluated_at TIMESTAMP NOT NULL
)
PARTITION BY DATE(evaluated_at)
CLUSTER BY prompt_version, outcome;

-- Act 3 ("3 months in production"): Synthetic 5,000,000-event telemetry table generated natively in BigQuery SQL
CREATE OR REPLACE TABLE `agent_analytics.historical_events_3m`
PARTITION BY DATE(timestamp)
CLUSTER BY agent, event_type
AS
WITH base_sessions AS (
  SELECT
    s_id,
    TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL MOD(s_id * 17, 90 * 24 * 60) MINUTE) AS session_ts,
    CASE
      WHEN MOD(s_id, 10) < 6 THEN 'cloudshop_support'
      WHEN MOD(s_id, 10) < 9 THEN 'returns_specialist'
      ELSE 'vip_concierge'
    END AS agent,
    CASE
      WHEN MOD(s_id, 100) < 42 THEN 'resolved'
      WHEN MOD(s_id, 100) < 73 THEN 'asked_for_known_info'
      WHEN MOD(s_id, 100) < 91 THEN 'policy_violation'
      ELSE 'tool_misuse'
    END AS session_outcome
  FROM UNNEST(GENERATE_ARRAY(1, 500000)) AS s_id
),
event_steps AS (
  SELECT step_idx, event_type
  FROM UNNEST([
    STRUCT(1 AS step_idx, 'USER_MESSAGE_RECEIVED' AS event_type),
    STRUCT(2, 'AGENT_STARTING'),
    STRUCT(3, 'STATE_DELTA'),
    STRUCT(4, 'LLM_REQUEST'),
    STRUCT(5, 'LLM_RESPONSE'),
    STRUCT(6, 'TOOL_STARTING'),
    STRUCT(7, 'TOOL_COMPLETED'),
    STRUCT(8, 'LLM_REQUEST'),
    STRUCT(9, 'LLM_RESPONSE'),
    STRUCT(10, 'AGENT_RESPONSE')
  ])
)
SELECT
  CONCAT('hist-sess-', CAST(b.s_id AS STRING)) AS session_id,
  TIMESTAMP_ADD(b.session_ts, INTERVAL e.step_idx * 2 SECOND) AS timestamp,
  b.agent,
  IF(b.session_outcome = 'tool_misuse' AND e.step_idx = 7, 'TOOL_ERROR', e.event_type) AS event_type,
  b.session_outcome,
  180 + MOD(b.s_id * e.step_idx, 950) AS prompt_tokens,
  45 + MOD(b.s_id + e.step_idx * 13, 220) AS completion_tokens,
  120 + MOD(b.s_id * 7 + e.step_idx * 31, 1400) AS latency_ms,
  CONCAT(
    '{"prompt_version":"v1","step":', CAST(e.step_idx AS STRING),
    ',"outcome_hint":"', b.session_outcome,
    '","summary":"Synthetic production telemetry event for scale benchmark"}'
  ) AS payload_json
FROM base_sessions AS b
CROSS JOIN event_steps AS e;
