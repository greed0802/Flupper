"""Test that _apply_column_migrations handles pre-ingest_key databases correctly."""
import sqlite3
import pytest
from pathlib import Path
from qsagent.storage.db import QSStore, SCHEMA_PATH


OLD_SCHEMA = """\
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    client TEXT,
    tender_no TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE TABLE IF NOT EXISTS evidence_nodes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    node_type  TEXT NOT NULL,
    label      TEXT NOT NULL,
    discipline TEXT NOT NULL DEFAULT 'UNKNOWN',
    file_hash  TEXT,
    drawing_no TEXT, revision TEXT, sheet TEXT, page INTEGER,
    zone TEXT, bbox TEXT, raw_text TEXT,
    payload TEXT NOT NULL DEFAULT '{}'
    -- NOTE: no ingest_key column
);
CREATE TABLE IF NOT EXISTS quantity_claims (
    claim_id   TEXT PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    description TEXT NOT NULL,
    value REAL NOT NULL,
    unit TEXT NOT NULL,
    method TEXT NOT NULL,
    evidence TEXT NOT NULL,
    assumptions TEXT NOT NULL DEFAULT '[]',
    workings TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    CHECK (json_array_length(evidence) >= 1)
    -- NOTE: no ingest_key column
);
"""


def test_migration_adds_ingest_key_to_existing_db(tmp_path):
    """Open a DB built from the old schema (no ingest_key), migrate it,
    then perform a successful UPSERT — proving the column was added live."""
    db_path = tmp_path / "old.db"

    # ── 1. create a DB WITH the old schema (no ingest_key columns) ────────
    conn_old = sqlite3.connect(str(db_path))
    conn_old.executescript(OLD_SCHEMA)
    conn_old.execute("INSERT INTO projects (name) VALUES ('old')")
    conn_old.commit()
    # confirm no ingest_key before migration
    cols_before = {r[1] for r in conn_old.execute("PRAGMA table_info(evidence_nodes)")}
    assert "ingest_key" not in cols_before, "Pre-condition: old schema has no ingest_key"
    conn_old.close()

    # ── 2. re-open with QSStore — migration must happen ───────────────────
    store = QSStore(db_path)
    cols_after_nodes = {r[1] for r in store.conn.execute(
        "PRAGMA table_info(evidence_nodes)")}
    cols_after_claims = {r[1] for r in store.conn.execute(
        "PRAGMA table_info(quantity_claims)")}
    assert "ingest_key" in cols_after_nodes, "Migration must add ingest_key to evidence_nodes"
    assert "ingest_key" in cols_after_claims, "Migration must add ingest_key to quantity_claims"

    # ── 3. unique index must be present ───────────────────────────────────
    indexes = {r[1] for r in store.conn.execute("PRAGMA index_list(evidence_nodes)")}
    assert "ux_nodes_ingest_key" in indexes

    # ── 4. perform a real upsert to prove it works end-to-end ─────────────
    from qsagent.ingest.cli import _ingest_masterfile
    project_id = store.get_or_create_project("Test Migration")
    masterfile_dir = Path(__file__).parent / "fixtures" / "master"
    _ingest_masterfile(store, project_id, masterfile_dir)

    c1 = store.conn.execute("SELECT COUNT(*) FROM quantity_claims").fetchone()[0]
    s1 = store.conn.execute("SELECT SUM(value) FROM quantity_claims").fetchone()[0] or 0.0
    assert c1 > 0
    assert s1 > 0

    # Re-ingest must not duplicate
    _ingest_masterfile(store, project_id, masterfile_dir)
    c2 = store.conn.execute("SELECT COUNT(*) FROM quantity_claims").fetchone()[0]
    s2 = store.conn.execute("SELECT SUM(value) FROM quantity_claims").fetchone()[0] or 0.0
    assert c1 == c2
    assert s1 == s2
