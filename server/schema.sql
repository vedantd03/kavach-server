-- kavach-server schema (contract 1A.1). No column holds extracted text, snippets or raw values.
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS devices (
    device_id      TEXT PRIMARY KEY,
    last_seen      TEXT,
    files_scanned  INTEGER NOT NULL DEFAULT 0,
    agent_version  TEXT
);

CREATE TABLE IF NOT EXISTS findings (
    finding_id          TEXT PRIMARY KEY,
    device_id           TEXT NOT NULL,
    scan_id             TEXT,
    file_path           TEXT NOT NULL,
    file_hash           TEXT NOT NULL,
    folder_class        TEXT NOT NULL,
    file_type           TEXT NOT NULL,
    location            TEXT NOT NULL,
    finding_kind        TEXT NOT NULL,
    category            TEXT NOT NULL,
    pii_type            TEXT,
    doc_type            TEXT NOT NULL,
    masked_value        TEXT,
    value_hash          TEXT,
    holder              TEXT NOT NULL,
    masked_in_source    INTEGER NOT NULL,
    confidence          REAL NOT NULL,
    decided_by          TEXT NOT NULL,
    reason              TEXT NOT NULL,
    llm_suggested_tier  TEXT,
    sensitivity_tier    TEXT NOT NULL,
    tier_reason         TEXT NOT NULL,
    policy_version      TEXT NOT NULL,
    risk_score          REAL NOT NULL,
    risk_band           TEXT NOT NULL,
    detected_at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_findings_device ON findings(device_id);
CREATE INDEX IF NOT EXISTS idx_findings_scan ON findings(scan_id);
CREATE INDEX IF NOT EXISTS idx_findings_risk ON findings(risk_score DESC);

-- One row per LLM/OCR call. key_index only, never the key. attempts-1 = key rotations.
CREATE TABLE IF NOT EXISTS traces (
    trace_id        TEXT PRIMARY KEY,
    ts              TEXT NOT NULL,
    endpoint        TEXT NOT NULL,   -- verify | classify | ocr
    model           TEXT,
    key_index       INTEGER,
    latency_ms      INTEGER,
    tokens_in       INTEGER,
    tokens_out      INTEGER,
    items           INTEGER,
    decisions_json  TEXT,            -- counts only, e.g. {"sensitive": 3, "not": 2}
    device_id       TEXT,
    status          TEXT,            -- ok | cached | error
    attempts        INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS actions (
    action_id    TEXT PRIMARY KEY,
    device_id    TEXT NOT NULL,
    finding_id   TEXT NOT NULL,
    file_path    TEXT NOT NULL,
    action_type  TEXT NOT NULL,
    status       TEXT NOT NULL,
    approver     TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    UNIQUE (finding_id, action_type)
);

CREATE TABLE IF NOT EXISTS audit (
    audit_id      TEXT PRIMARY KEY,
    ts            TEXT NOT NULL,
    actor         TEXT NOT NULL,
    event         TEXT NOT NULL,
    action_id     TEXT,
    finding_id    TEXT,
    details_json  TEXT
);

CREATE TABLE IF NOT EXISTS scans (
    scan_id            TEXT PRIMARY KEY,
    device_id          TEXT NOT NULL,
    command_id         TEXT,
    status             TEXT NOT NULL,   -- queued|delivered|running|completed|failed|rejected
    roots_json         TEXT NOT NULL,
    force              INTEGER NOT NULL DEFAULT 0,
    created_at         TEXT NOT NULL,
    delivered_at       TEXT,
    started_at         TEXT,
    completed_at       TEXT,
    reject_reason      TEXT,
    error              TEXT,
    files_discovered   INTEGER NOT NULL DEFAULT 0,
    files_done         INTEGER NOT NULL DEFAULT 0,
    files_unscannable  INTEGER NOT NULL DEFAULT 0,
    files_skipped      INTEGER NOT NULL DEFAULT 0,
    files_failed       INTEGER NOT NULL DEFAULT 0,
    findings           INTEGER NOT NULL DEFAULT 0,
    crawl_complete     INTEGER NOT NULL DEFAULT 0,
    duration_ms        INTEGER
);
CREATE INDEX IF NOT EXISTS idx_scans_device ON scans(device_id, created_at);

CREATE TABLE IF NOT EXISTS commands (
    command_id     TEXT PRIMARY KEY,
    device_id      TEXT NOT NULL,
    type           TEXT NOT NULL,
    payload_json   TEXT NOT NULL,
    status         TEXT NOT NULL,   -- pending|delivered|accepted|rejected
    created_at     TEXT NOT NULL,
    delivered_at   TEXT,
    acked_at       TEXT,
    reject_reason  TEXT
);
CREATE INDEX IF NOT EXISTS idx_commands_device ON commands(device_id, status, created_at);
