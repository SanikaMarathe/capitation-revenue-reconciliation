-- DuckDB schema. Money is DECIMAL(12,2), never floating point.
-- Data classes per ARCHITECTURE.md §10.2 are noted in comments.

-- ===== Layer 1: raw =====
CREATE TABLE IF NOT EXISTS source_documents (
    document_id VARCHAR PRIMARY KEY,
    filename VARCHAR NOT NULL,
    format VARCHAR NOT NULL,
    received_date DATE NOT NULL,
    sha256 VARCHAR NOT NULL UNIQUE,         -- idempotent ingestion
    extraction_status VARCHAR NOT NULL DEFAULT 'pending'
);

-- ===== Layer 2: validated =====
-- C1 direct identifiers live ONLY here. Read via storage/phi.py.
CREATE TABLE IF NOT EXISTS members (
    member_id VARCHAR PRIMARY KEY,
    first_name VARCHAR NOT NULL,
    last_name VARCHAR NOT NULL,
    dob DATE NOT NULL
);

CREATE TABLE IF NOT EXISTS enrollment_spans (
    span_id VARCHAR PRIMARY KEY,
    member_id VARCHAR NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE,
    region VARCHAR NOT NULL,
    category VARCHAR NOT NULL,
    source VARCHAR NOT NULL,
    received_date DATE NOT NULL             -- knowledge time
);

CREATE TABLE IF NOT EXISTS rate_versions (
    version_id VARCHAR PRIMARY KEY,
    kind VARCHAR NOT NULL,                  -- full | amendment
    published_date DATE NOT NULL,
    source_document_id VARCHAR NOT NULL,
    status VARCHAR NOT NULL,                -- draft | approved | rejected | superseded
    approved_by VARCHAR,
    approved_at TIMESTAMP,                  -- knowledge time
    notes VARCHAR DEFAULT ''
);

CREATE TABLE IF NOT EXISTS rates (
    version_id VARCHAR NOT NULL,
    region VARCHAR NOT NULL,
    category VARCHAR NOT NULL,
    effective_from DATE NOT NULL,           -- 1st of month
    effective_to DATE,                      -- last month covered, inclusive; NULL = open
    pmpm DECIMAL(12,2) NOT NULL,
    source_ref VARCHAR NOT NULL,
    PRIMARY KEY (version_id, region, category, effective_from)
);

CREATE TABLE IF NOT EXISTS remits (
    remit_id VARCHAR PRIMARY KEY,
    payment_date DATE NOT NULL,
    total_amount DECIMAL(12,2) NOT NULL,
    line_count INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS remittance_lines (
    line_id VARCHAR PRIMARY KEY,
    remit_id VARCHAR NOT NULL,
    payment_date DATE NOT NULL,
    coverage_month DATE NOT NULL,
    member_id VARCHAR NOT NULL,
    region VARCHAR NOT NULL,
    category VARCHAR NOT NULL,
    amount DECIMAL(12,2) NOT NULL,
    line_type VARCHAR NOT NULL,
    reference VARCHAR DEFAULT ''
);

CREATE TABLE IF NOT EXISTS bank_deposits (
    deposit_id VARCHAR PRIMARY KEY,
    deposit_date DATE NOT NULL,
    amount DECIMAL(12,2) NOT NULL,
    payer VARCHAR NOT NULL,
    reference VARCHAR DEFAULT ''
);

-- ===== Layer 3: computed =====
CREATE TABLE IF NOT EXISTS periods (
    coverage_month DATE PRIMARY KEY,
    status VARCHAR NOT NULL,                -- open | closed
    closed_at TIMESTAMP,
    closed_by VARCHAR
);

CREATE TABLE IF NOT EXISTS close_snapshots (
    coverage_month DATE NOT NULL,
    member_id VARCHAR NOT NULL,
    region VARCHAR NOT NULL,
    category VARCHAR NOT NULL,
    span_id VARCHAR NOT NULL,
    rate_version_id VARCHAR NOT NULL,
    pmpm DECIMAL(12,2) NOT NULL,
    snapshot_at TIMESTAMP NOT NULL,
    PRIMARY KEY (coverage_month, member_id)
);

CREATE TABLE IF NOT EXISTS bank_links (
    deposit_id VARCHAR PRIMARY KEY,         -- a deposit is used once
    remit_id VARCHAR NOT NULL,
    linked_by VARCHAR NOT NULL,             -- system (unique reference) | human
    linked_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS variances (
    variance_id VARCHAR PRIMARY KEY,
    run_id VARCHAR NOT NULL,
    coverage_month DATE NOT NULL,
    member_id VARCHAR,
    level VARCHAR NOT NULL,
    expected DECIMAL(12,2) NOT NULL,
    remitted DECIMAL(12,2) NOT NULL,
    banked DECIMAL(12,2),
    delta DECIMAL(12,2) NOT NULL,
    features JSON,
    observed_type VARCHAR NOT NULL,
    possible_cause_rule VARCHAR,
    rule_strength VARCHAR,
    possible_cause_llm JSON,
    llm_confidence DOUBLE,
    confirmed_cause VARCHAR,
    explanation VARCHAR DEFAULT '',
    evidence_refs JSON,
    recommended_action VARCHAR,
    grounded BOOLEAN,
    group_id VARCHAR,
    status VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS variance_decisions (
    decision_id VARCHAR PRIMARY KEY,
    variance_id VARCHAR,
    group_id VARCHAR,
    decision VARCHAR NOT NULL,
    final_category VARCHAR NOT NULL,
    reviewer VARCHAR NOT NULL,
    notes VARCHAR DEFAULT '',
    ts TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS adjustments (
    adjustment_id VARCHAR PRIMARY KEY,
    trigger VARCHAR NOT NULL,
    trigger_ref VARCHAR NOT NULL,
    status VARCHAR NOT NULL,                -- proposed | rejected | posted
    total DECIMAL(12,2) NOT NULL,
    proposed_by VARCHAR NOT NULL,
    memo VARCHAR DEFAULT '',
    memo_grounded BOOLEAN,
    created_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS adjustment_lines (
    adjustment_id VARCHAR NOT NULL,
    member_id VARCHAR NOT NULL,
    coverage_month DATE NOT NULL,
    region VARCHAR NOT NULL,
    category VARCHAR NOT NULL,
    previously_booked DECIMAL(12,2) NOT NULL,
    new_expected DECIMAL(12,2) NOT NULL,
    old_version_id VARCHAR NOT NULL,
    new_version_id VARCHAR NOT NULL,
    delta DECIMAL(12,2) NOT NULL,
    PRIMARY KEY (adjustment_id, member_id, coverage_month)
);

CREATE TABLE IF NOT EXISTS ledger_entries (
    entry_id VARCHAR PRIMARY KEY,
    period DATE NOT NULL,
    account VARCHAR NOT NULL,
    debit DECIMAL(12,2) NOT NULL,
    credit DECIMAL(12,2) NOT NULL,
    memo VARCHAR NOT NULL,
    source VARCHAR NOT NULL,
    adjustment_id VARCHAR,
    posting_key VARCHAR NOT NULL,           -- unique per posting line -> idempotent retries
    approved_by VARCHAR NOT NULL,
    approved_at TIMESTAMP NOT NULL,
    UNIQUE (posting_key)
);

-- ===== Cross-cutting =====
CREATE TABLE IF NOT EXISTS audit_log (
    seq BIGINT PRIMARY KEY,
    event_id VARCHAR NOT NULL UNIQUE,
    ts TIMESTAMP NOT NULL,
    actor_type VARCHAR NOT NULL,
    actor_id VARCHAR NOT NULL,
    action VARCHAR NOT NULL,
    entity VARCHAR NOT NULL,
    entity_id VARCHAR NOT NULL,
    payload JSON,
    prev_hash VARCHAR NOT NULL,
    hash VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS llm_calls (
    call_id VARCHAR PRIMARY KEY,
    agent VARCHAR NOT NULL,
    task VARCHAR NOT NULL,
    model VARCHAR NOT NULL,
    prompt_hash VARCHAR NOT NULL,
    cache_hit BOOLEAN NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    latency_ms INTEGER NOT NULL,
    cost_usd DECIMAL(12,6) NOT NULL,
    schema_valid BOOLEAN NOT NULL,
    ts TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS app_state (     -- demo clock and other UI state
    key VARCHAR PRIMARY KEY,
    value VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS close_runs (
    run_id VARCHAR PRIMARY KEY,
    coverage_month DATE NOT NULL,
    as_of TIMESTAMP NOT NULL,
    last_completed_step VARCHAR,
    started_at TIMESTAMP NOT NULL,
    finished_at TIMESTAMP,
    priced_fingerprint VARCHAR               -- what was reviewed; sign-off must book exactly this
);
ALTER TABLE close_runs ADD COLUMN IF NOT EXISTS priced_fingerprint VARCHAR;   -- upgrade older demo databases
