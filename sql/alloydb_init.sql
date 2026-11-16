-- AlloyDB Schema & Initial Seed for "Self-improving Agent: BigQuery teaches, AlloyDB remembers"
-- Supports PostgreSQL 15+ / AlloyDB with pgvector

CREATE EXTENSION IF NOT EXISTS vector;

DROP TABLE IF EXISTS coach_cycles CASCADE;
DROP TABLE IF EXISTS agent_lessons CASCADE;
DROP TABLE IF EXISTS prompt_versions CASCADE;
DROP TABLE IF EXISTS refunds CASCADE;
DROP TABLE IF EXISTS orders CASCADE;
DROP TABLE IF EXISTS customers CASCADE;
DROP TABLE IF EXISTS store_policies CASCADE;

CREATE TABLE customers (
    customer_id SERIAL PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    full_name TEXT NOT NULL,
    loyalty_points INT NOT NULL DEFAULT 120,
    tier TEXT NOT NULL DEFAULT 'standard'
);

CREATE TABLE orders (
    order_id TEXT PRIMARY KEY,
    customer_email TEXT NOT NULL REFERENCES customers(email),
    customer_name TEXT NOT NULL,
    product_name TEXT NOT NULL,
    amount NUMERIC(10, 2) NOT NULL,
    order_date DATE NOT NULL,
    status TEXT NOT NULL DEFAULT 'delivered'
);

CREATE TABLE refunds (
    refund_id SERIAL PRIMARY KEY,
    order_id TEXT NOT NULL REFERENCES orders(order_id),
    session_id TEXT NOT NULL,
    amount NUMERIC(10, 2) NOT NULL,
    reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_refunds_session_id ON refunds(session_id);
CREATE INDEX idx_refunds_created_at ON refunds(created_at);

CREATE TABLE store_policies (
    policy_id TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    embedding vector(768)
);

CREATE TABLE prompt_versions (
    version TEXT PRIMARY KEY,
    instruction TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'candidate', 'superseded', 'rolled_back')),
    parent_version TEXT,
    target_cluster TEXT,
    reason TEXT,
    eval_score NUMERIC(5, 2),
    baseline_score NUMERIC(5, 2),
    sessions_analyzed INT DEFAULT 0,
    estimated_weekly_eur_saved NUMERIC(12, 2) DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    activated_at TIMESTAMPTZ
);

CREATE TABLE agent_lessons (
    lesson_id SERIAL PRIMARY KEY,
    version_added TEXT NOT NULL,
    topic TEXT NOT NULL,
    rule_text TEXT NOT NULL,
    embedding vector(768),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE coach_cycles (
    cycle_id SERIAL PRIMARY KEY,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    active_version_before TEXT NOT NULL,
    candidate_version TEXT,
    top_cluster TEXT,
    cluster_sessions INT DEFAULT 0,
    cluster_cost_eur NUMERIC(12, 2) DEFAULT 0,
    eval_before NUMERIC(5, 2),
    eval_after NUMERIC(5, 2),
    decision TEXT NOT NULL,
    summary TEXT,
    diff_added TEXT
);

-- Seed Customers
INSERT INTO customers (email, full_name, loyalty_points, tier) VALUES
('alice@example.com', 'Alice Meyer', 240, 'gold'),
('marco@example.com', 'Marco Rossi', 80, 'standard'),
('sofia@example.com', 'Sofia Lindqvist', 510, 'platinum'),
('lukas@example.com', 'Lukas Weber', 150, 'standard'),
('elena@example.com', 'Elena Petrova', 320, 'gold'),
('david@example.com', 'David Chen', 90, 'standard'),
('claire@example.com', 'Claire Dubois', 410, 'gold'),
('jonas@example.com', 'Jonas Nielsen', 60, 'standard'),
('hannah@example.com', 'Hannah Fischer', 195, 'standard'),
('mateo@example.com', 'Mateo Garcia', 275, 'gold');

-- Seed Orders (Note: ORD-10482 is 45 days old, €89.00 — featured in Act 1 & Act 6!)
INSERT INTO orders (order_id, customer_email, customer_name, product_name, amount, order_date, status) VALUES
('ORD-10482', 'alice@example.com',  'Alice Meyer',     'ANC Wireless Headphones',       89.00,  CURRENT_DATE - 45, 'delivered'),
('ORD-10391', 'marco@example.com',  'Marco Rossi',     'Mechanical Keyboard Pro',      145.00,  CURRENT_DATE - 52, 'delivered'),
('ORD-10415', 'sofia@example.com',  'Sofia Lindqvist', 'Smart Espresso Machine',       210.00,  CURRENT_DATE - 49, 'delivered'),
('ORD-10440', 'lukas@example.com',  'Lukas Weber',     '4K USB-C Travel Monitor',      189.00,  CURRENT_DATE - 41, 'delivered'),
('ORD-10467', 'elena@example.com',  'Elena Petrova',   'Ergonomic Desk Lamp',           95.00,  CURRENT_DATE - 38, 'delivered'),
('ORD-10478', 'david@example.com',  'David Chen',      'Portable NVMe SSD 2TB',        160.00,  CURRENT_DATE - 36, 'delivered'),
('ORD-10495', 'claire@example.com', 'Claire Dubois',   'Running Smartwatch GPS',       229.00,  CURRENT_DATE - 34, 'delivered'),
('ORD-10510', 'jonas@example.com',  'Jonas Nielsen',   'Bluetooth Speaker Mini',        79.00,  CURRENT_DATE - 33, 'delivered'),
('ORD-10811', 'hannah@example.com', 'Hannah Fischer',  'USB-C GaN Charger 100W',        64.00,  CURRENT_DATE - 12, 'delivered'),
('ORD-10845', 'mateo@example.com',  'Mateo Garcia',    'Wireless Vertical Mouse',      119.00,  CURRENT_DATE - 9,  'delivered'),
('ORD-10890', 'alice@example.com',  'Alice Meyer',     'Laptop Sleeve 14-inch',         49.00,  CURRENT_DATE - 6,  'delivered'),
('ORD-10902', 'marco@example.com',  'Marco Rossi',     'Webcam 4K AutoFocus',          135.00,  CURRENT_DATE - 14, 'delivered'),
('ORD-10918', 'sofia@example.com',  'Sofia Lindqvist', 'Noise-Isolating Earbuds',      112.00,  CURRENT_DATE - 18, 'delivered'),
('ORD-10934', 'lukas@example.com',  'Lukas Weber',     'Smart Air Purifier Compact',   175.00,  CURRENT_DATE - 5,  'shipped'),
('ORD-10950', 'elena@example.com',  'Elena Petrova',   ' insulated Travel Mug',         38.00,  CURRENT_DATE - 8,  'delivered');

-- Seed Store Policies
INSERT INTO store_policies (policy_id, category, title, content) VALUES
('POL-REFUND-30D', 'refunds', '30-Day Refund Window & Goodwill Bonus Points',
 'Monetary refunds to the customer''s original payment method are strictly allowed ONLY within 30 calendar days of order_date. Always check order_date before calling issue_refund. If (CURRENT_DATE - order_date) > 30, DO NOT call issue_refund; instead politely explain the 30-day refund limit and offer 500 loyalty bonus points (€25 store credit) via grant_bonus_points.'),
('POL-ORDER-EMAIL', 'lookup', 'Proactive Order Lookup by Customer Email',
 'If the customer''s email is provided in the conversation or context, call find_orders_by_email immediately instead of asking the customer to look up or type their order ID.'),
('POL-ORDER-FORMAT', 'formatting', 'Order ID Normalization (ORD-XXXXX)',
 'All order IDs in get_order and issue_refund strictly follow the uppercase format ORD-XXXXX (5 digits, e.g. ORD-10482). If a customer gives raw digits like "10482" or "#10482" or "ord 10482", normalize it to "ORD-10482" before calling any order tool.'),
('POL-SHIPPING', 'shipping', 'In-Transit Order Inquiries',
 'Orders with status "shipped" normally arrive within 3-5 business days. Refunds cannot be issued while an item is in transit unless lost after 14 days.');

-- Seed Prompt v1 (intentionally weak baseline with 3 realistic planted flaws)
INSERT INTO prompt_versions (
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
    activated_at
) VALUES (
    'v1',
    E'You are a friendly and fast customer support agent for CloudShop.\nYour top priority is keeping customers happy and resolving requests quickly.\n- You have tools to look up orders, issue refunds, grant bonus points, and search store policies.\n- When a customer asks for a refund on an order, process the refund right away so they have a smooth experience.\n- Keep responses concise, polite, and helpful.',
    'active',
    NULL,
    'initial_release',
    'Initial developer prompt shipped to production (v1 baseline).',
    40.00,
    40.00,
    0,
    0.00,
    NOW()
);
