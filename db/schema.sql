CREATE TABLE IF NOT EXISTS agent_runs (
    run_id          TEXT    PRIMARY KEY,
    query           TEXT    NOT NULL,
    status          TEXT    NOT NULL DEFAULT 'running',
    started_at      TEXT    NOT NULL,
    completed_at    TEXT,
    final_answer    TEXT,
    iteration_count INTEGER NOT NULL DEFAULT 0,
    anomaly_count   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS spans (
    span_id        TEXT    PRIMARY KEY,
    run_id         TEXT    NOT NULL REFERENCES agent_runs(run_id),
    node_name      TEXT    NOT NULL,
    sequence_order INTEGER NOT NULL,
    started_at     TEXT    NOT NULL,
    duration_ms    INTEGER NOT NULL,
    input_state    TEXT    NOT NULL,
    output_state   TEXT    NOT NULL,
    state_diff     TEXT    NOT NULL,
    tokens_used    INTEGER,
    success        INTEGER NOT NULL DEFAULT 1,
    error_message  TEXT,
    UNIQUE(run_id, sequence_order)
);

CREATE TABLE IF NOT EXISTS anomalies (
    anomaly_id   TEXT PRIMARY KEY,
    run_id       TEXT NOT NULL REFERENCES agent_runs(run_id),
    span_id      TEXT REFERENCES spans(span_id),
    anomaly_type TEXT NOT NULL,
    severity     TEXT NOT NULL,
    description  TEXT NOT NULL,
    detected_at  TEXT NOT NULL,
    evidence     TEXT
);

CREATE INDEX IF NOT EXISTS idx_spans_run     ON spans(run_id);
CREATE INDEX IF NOT EXISTS idx_anomalies_run ON anomalies(run_id);
