-- Migration 001: time_runs table for the Time primitive.
-- Applied lazily on first open via PRAGMA user_version check.

CREATE TABLE IF NOT EXISTS time_runs (
    op_id        TEXT    PRIMARY KEY,
    op_kind      TEXT    NOT NULL,
    started_at   REAL    NOT NULL,
    finished_at  REAL,
    status       TEXT,
    hint_sec     INTEGER,
    progress_pct REAL,
    current_step TEXT
);

CREATE INDEX IF NOT EXISTS idx_time_runs_kind_finished
    ON time_runs (op_kind, finished_at);

CREATE INDEX IF NOT EXISTS idx_time_runs_kind_status
    ON time_runs (op_kind, status);
