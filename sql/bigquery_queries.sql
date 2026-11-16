-- ============================================================================
-- Google Cloud AI Day — Stage SQL Script (BigQuery Studio)
-- Replace `PROJECT_ID` and `REGION` (default: us-central1) if running manually
-- ============================================================================

-- ----------------------------------------------------------------------------
-- ACT 2: "One line of code, and I already have data"
-- Inspect fresh telemetry streamed by BigQueryAgentAnalyticsPlugin
-- ----------------------------------------------------------------------------
SELECT
  timestamp,
  session_id,
  event_type,
  agent,
  JSON_VALUE(attributes, '$.state_delta.prompt_version') AS prompt_version,
  SUBSTR(TO_JSON_STRING(content), 1, 180) AS content_preview
FROM `PROJECT_ID.agent_analytics.agent_events`
WHERE timestamp > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 15 MINUTE)
ORDER BY timestamp DESC
LIMIT 30;


-- ----------------------------------------------------------------------------
-- ACT 3: "These aren't just logs, it's a dataset"
-- Scan 3 months of multi-agent production history (5,000,000 events) in ~1s
-- ----------------------------------------------------------------------------
SELECT
  agent,
  COUNT(*) AS total_events,
  COUNT(DISTINCT session_id) AS total_sessions,
  COUNTIF(event_type = 'TOOL_ERROR') AS tool_errors,
  ROUND(COUNTIF(session_outcome = 'resolved') / COUNT(*), 3) AS resolution_rate,
  ROUND(AVG(latency_ms), 0) AS avg_latency_ms,
   ROUND(SUM(prompt_tokens + completion_tokens) / 1e6, 1) AS total_million_tokens
FROM `PROJECT_ID.agent_analytics.historical_events_3m`
GROUP BY agent
ORDER BY total_events DESC;


-- ----------------------------------------------------------------------------
-- ACT 4A: LLM-as-a-Judge right inside SQL (`AI.CLASSIFY`)
-- Classify fresh support sessions into outcome categories
-- ----------------------------------------------------------------------------
WITH sessions AS (
  SELECT
    session_id,
    COALESCE(
      MAX(JSON_VALUE(attributes, '$.state_delta.prompt_version')),
      'v1'
    ) AS prompt_version,
    COUNTIF(event_type = 'TOOL_ERROR') AS tool_errors,
    STRING_AGG(
      IF(
        event_type IN ('USER_MESSAGE_RECEIVED', 'TOOL_STARTING', 'TOOL_COMPLETED', 'TOOL_ERROR', 'AGENT_RESPONSE'),
        CONCAT(event_type, ': ', SUBSTR(TO_JSON_STRING(content), 1, 500)),
        NULL
      ),
      '\n' ORDER BY timestamp
    ) AS transcript
  FROM `PROJECT_ID.agent_analytics.agent_events`
  WHERE timestamp > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 MINUTE)
  GROUP BY session_id
  HAVING COUNTIF(event_type = 'AGENT_RESPONSE') > 0
)
SELECT
  session_id,
  prompt_version,
  tool_errors,
  AI.CLASSIFY(
    CONCAT(
      'Classify how this e-commerce customer support chat ended. ',
      'Note: refunds on orders older than 30 days are policy_violation; ',
      'asking the user for an order ID when email is already known is asked_for_known_info; ',
      'TOOL_ERROR on get_order due to unnormalized ID is tool_misuse.\n\n',
      transcript
    ),
    categories => [
      ('resolved', 'Handled properly: valid <30d refund, or >30d order politely offered bonus points, or inquiry answered accurately without unnecessary questions.'),
      ('policy_violation', 'Agent issued a monetary refund for an order older than 30 days instead of enforcing the 30-day policy.'),
      ('asked_for_known_info', 'Agent asked the customer for their order ID instead of looking up orders by customer email first.'),
      ('tool_misuse', 'Agent passed a malformed order ID (missing ORD- prefix) causing TOOL_ERROR on get_order.'),
      ('unnecessary_escalation', 'Agent escalated to a human unnecessarily.')
    ],
    connection_id => 'PROJECT_ID.REGION.vertex-ai-conn',
    endpoint => 'gemini-3.8-flash'
  ) AS outcome
FROM sessions;


-- ----------------------------------------------------------------------------
-- ACT 4B: "Most common != Most expensive"
-- Join BigQuery session outcomes + CSAT with live AlloyDB refunds via EXTERNAL_QUERY
-- ----------------------------------------------------------------------------
WITH alloydb_refunds AS (
  SELECT *
  FROM EXTERNAL_QUERY(
    'PROJECT_ID.REGION.alloydb-conn',
    '''SELECT
         r.session_id,
         r.order_id,
         r.amount,
         (CURRENT_DATE - o.order_date) AS order_age_days,
         (CURRENT_DATE - o.order_date) > 30 AS policy_violation
       FROM refunds r
       JOIN orders o USING (order_id)
       WHERE r.created_at > NOW() - INTERVAL '60 minutes' '''
  )
)
SELECT
  s.outcome AS problem_cluster,
  COUNT(DISTINCT s.session_id) AS affected_sessions,
  ROUND(AVG(c.score), 2) AS avg_csat,
  ROUND(SUM(IF(r.policy_violation, r.amount, 0)), 2) AS direct_refund_loss_eur,
  -- Extrapolate to weekly production volume (15 chats/min)
  ROUND(
    SUM(IF(r.policy_violation, r.amount, 0)) * 48
    + COUNT(DISTINCT IF(c.score <= 2, s.session_id, NULL)) * 12.50,
    0
  ) AS projected_weekly_cost_eur
FROM `PROJECT_ID.agent_analytics.session_outcomes` AS s
LEFT JOIN `PROJECT_ID.business.csat_surveys` AS c USING (session_id)
LEFT JOIN alloydb_refunds AS r USING (session_id)
WHERE s.outcome != 'resolved'
GROUP BY s.outcome
ORDER BY projected_weekly_cost_eur DESC, affected_sessions DESC;
