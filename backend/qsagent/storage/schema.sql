-- Flupper QS Agent Platform - single-store SQLite schema.
-- Every table is append-friendly; the audit journal is strictly append-only
-- and enforced by triggers so runs can be deterministically replayed.
--
-- ingest_key (added Phase 2): deterministic sha256(file_hash|sheet|row|column)
-- that enables INSERT ... ON CONFLICT(ingest_key) DO UPDATE so that
-- re-ingesting one source file converges rather than deleting unrelated rows.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS projects (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL UNIQUE,
    client       TEXT,
    tender_no    TEXT,
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE IF NOT EXISTS documents (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id   INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    file_name    TEXT NOT NULL,
    file_hash    TEXT NOT NULL,
    media_type   TEXT,
    discipline   TEXT NOT NULL DEFAULT 'UNKNOWN',
    drawing_no   TEXT,
    revision     TEXT,
    title        TEXT,
    page_count   INTEGER,
    ingested_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE (project_id, file_hash)
);
CREATE INDEX IF NOT EXISTS idx_documents_drawing ON documents(project_id, drawing_no, revision);

CREATE TABLE IF NOT EXISTS evidence_nodes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id   INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    node_type    TEXT NOT NULL CHECK (node_type IN
                   ('document','drawing','element','quantity','rate','boq_line','assumption')),
    label        TEXT NOT NULL,
    discipline   TEXT NOT NULL DEFAULT 'UNKNOWN',
    ingest_key   TEXT,   -- sha256(file_hash|sheet|row_index); NULL for non-machine rows
                         -- Uniqueness enforced by ux_nodes_ingest_key (partial, WHERE NOT NULL)
    file_hash    TEXT,
    drawing_no   TEXT,
    revision     TEXT,
    sheet        TEXT,
    page         INTEGER,
    zone         TEXT,
    bbox         TEXT,
    raw_text     TEXT,
    payload      TEXT NOT NULL DEFAULT '{}',
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_nodes_project_type ON evidence_nodes(project_id, node_type);
-- Note: ux_nodes_ingest_key is expressly created in db.py _apply_column_migrations() 
-- to prevent 'no such column' errors when executing this script on legacy databases.

-- Directed relationships form the knowledge graph:
-- Document -> Drawing -> Element -> Quantity -> Rate -> BOQ Line
CREATE TABLE IF NOT EXISTS evidence_edges (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id   INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    src_id       INTEGER NOT NULL REFERENCES evidence_nodes(id) ON DELETE CASCADE,
    dst_id       INTEGER NOT NULL REFERENCES evidence_nodes(id) ON DELETE CASCADE,
    rel          TEXT NOT NULL,
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE (src_id, dst_id, rel)
);
CREATE INDEX IF NOT EXISTS idx_edges_src ON evidence_edges(src_id);
CREATE INDEX IF NOT EXISTS idx_edges_dst ON evidence_edges(dst_id);

CREATE TABLE IF NOT EXISTS assumptions (
    id               TEXT NOT NULL,
    project_id       INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    statement        TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'ASSUMED'
                       CHECK (status IN ('ASSUMED','CONFIRMED','REJECTED','SUPERSEDED')),
    rationale        TEXT,
    impact_delta_aud REAL,
    impact_value     REAL,
    impact_unit      TEXT,
    evidence         TEXT NOT NULL DEFAULT '[]',
    raised_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    resolved_at      TEXT,
    PRIMARY KEY (project_id, id)
);

CREATE TABLE IF NOT EXISTS quantity_claims (
    claim_id     TEXT PRIMARY KEY,
    project_id   INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    ingest_key   TEXT,   -- sha256(file_hash|sheet|row_index|col_header); NULL for manual
                         -- Uniqueness enforced by ux_claims_ingest_key (partial, WHERE NOT NULL)
    description  TEXT NOT NULL,
    value        REAL NOT NULL,
    unit         TEXT NOT NULL,
    measurement_state TEXT,  -- 'bulked','banked','compressed','m3_insitu','UNRESOLVED'; NULL=untracked
    method       TEXT NOT NULL,
    evidence     TEXT NOT NULL,
    assumptions  TEXT NOT NULL DEFAULT '[]',
    workings     TEXT NOT NULL DEFAULT '[]',
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    CHECK (json_array_length(evidence) >= 1)
);
-- Note: ux_claims_ingest_key is expressly created in db.py _apply_column_migrations() 

CREATE TABLE IF NOT EXISTS tool_runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id   INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    tool_id      TEXT NOT NULL,
    tier         INTEGER NOT NULL CHECK (tier BETWEEN 1 AND 3),
    inputs       TEXT NOT NULL,
    outputs      TEXT NOT NULL DEFAULT '{}',
    workings     TEXT NOT NULL DEFAULT '[]',
    warnings     TEXT NOT NULL DEFAULT '[]',
    ok           INTEGER NOT NULL DEFAULT 1,
    error        TEXT,
    started_at   TEXT NOT NULL,
    duration_ms  REAL
);
CREATE INDEX IF NOT EXISTS idx_runs_project ON tool_runs(project_id, tool_id);

-- Append-only journal. Each entry chains to the previous by hash, giving a
-- tamper-evident audit trail across the whole project.
CREATE TABLE IF NOT EXISTS audit_journal (
    seq          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id   INTEGER REFERENCES projects(id) ON DELETE CASCADE,
    actor        TEXT NOT NULL,
    action       TEXT NOT NULL,
    subject      TEXT,
    approval     TEXT NOT NULL DEFAULT 'SAFE' CHECK (approval IN ('SAFE','REVIEW','CONFIRM')),
    payload      TEXT NOT NULL DEFAULT '{}',
    prev_hash    TEXT,
    entry_hash   TEXT NOT NULL,
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TRIGGER IF NOT EXISTS audit_journal_no_update
BEFORE UPDATE ON audit_journal
BEGIN
    SELECT RAISE(ABORT, 'audit_journal is append-only');
END;

CREATE TRIGGER IF NOT EXISTS audit_journal_no_delete
BEFORE DELETE ON audit_journal
BEGIN
    SELECT RAISE(ABORT, 'audit_journal is append-only');
END;

CREATE TABLE IF NOT EXISTS checkmate_results (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id   INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    subject      TEXT NOT NULL,
    passed       INTEGER NOT NULL,
    findings     TEXT NOT NULL DEFAULT '[]',
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
