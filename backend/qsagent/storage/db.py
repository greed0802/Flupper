"""SQLite single-store access layer with an append-only, hash-chained journal.

`sqlite-vec` is loaded opportunistically for vector search; the store works
fully without it (Phase 2 uses it for semantic document retrieval).
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

from ..contracts.evidence import (
    ApprovalLevel,
    Assumption,
    EvidenceNode,
    EvidenceRef,
    QuantityClaim,
    ToolRun,
)

SCHEMA_PATH = Path(__file__).with_name("schema.sql")

log = logging.getLogger(__name__)


class ReadSnapshotError(RuntimeError):
    """A read snapshot was requested while another transaction was open.

    Raised instead of committing or rolling back: the pending transaction may
    hold writes that belong to a different operation, and a read helper has no
    business deciding whether they survive.
    """


def _json(value: Any) -> str:
    return json.dumps(value, default=str, sort_keys=True, separators=(",", ":"))


@dataclass
class JournalEntry:
    seq: int
    prev_hash: Optional[str]
    entry_hash: str


class QSStore:
    """Thin, explicit repository over SQLite. No ORM, no hidden magic."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        # The gateway serves requests from a worker thread pool, so the
        # connection is routinely touched from more than one thread. The
        # alternative - one connection per thread - is impossible here because
        # an AgentSession holds a single store reference for its whole life.
        #
        # check_same_thread=False drops Python's thread-affinity assertion, not
        # SQLite's own serialisation: this build reports threadsafety == 3, so
        # individual statements cannot corrupt the file. It does NOT make
        # *transactions* interleave safely, so callers that share one store
        # across threads must serialise their read-modify-write sequences
        # themselves. The gateway does that with one app-wide lock.
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.vec_enabled = self._try_load_sqlite_vec()
        self._migrate()

    # ---------------------------------------------------------------- setup
    def _try_load_sqlite_vec(self) -> bool:
        try:
            import sqlite_vec  # type: ignore

            self.conn.enable_load_extension(True)
            sqlite_vec.load(self.conn)
            self.conn.enable_load_extension(False)
            return True
        except Exception:
            return False

    def _migrate(self) -> None:
        self.conn.executescript(SCHEMA_PATH.read_text())
        self._apply_column_migrations()
        if self.vec_enabled:
            self.conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS doc_chunks USING vec0("
                "  node_id INTEGER, embedding float[768])"
            )
        self.conn.commit()

    def _apply_column_migrations(self) -> None:
        """Add columns that were introduced after initial schema deployment.

        SQLite cannot add a UNIQUE column via ALTER TABLE — the constraint must
        be a separate index created afterwards.  This is idempotent: each step
        checks PRAGMA table_info before running.
        """
        existing_nodes = {r[1] for r in self.conn.execute(
            "PRAGMA table_info(evidence_nodes)")}
        if "ingest_key" not in existing_nodes:
            self.conn.execute(
                "ALTER TABLE evidence_nodes ADD COLUMN ingest_key TEXT")
        self.conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_nodes_ingest_key"
            " ON evidence_nodes(ingest_key) WHERE ingest_key IS NOT NULL")

        existing_claims = {r[1] for r in self.conn.execute(
            "PRAGMA table_info(quantity_claims)")}
        if "ingest_key" not in existing_claims:
            self.conn.execute(
                "ALTER TABLE quantity_claims ADD COLUMN ingest_key TEXT")
        if "measurement_state" not in existing_claims:
            self.conn.execute(
                "ALTER TABLE quantity_claims ADD COLUMN measurement_state TEXT")
        if "conversion_applied" not in existing_claims:
            self.conn.execute(
                "ALTER TABLE quantity_claims ADD COLUMN conversion_applied INTEGER NOT NULL DEFAULT 0")
        self.conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_claims_ingest_key"
            " ON quantity_claims(ingest_key) WHERE ingest_key IS NOT NULL")

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "QSStore":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------- projects
    def get_or_create_project(self, name: str, client: str | None = None,
                              tender_no: str | None = None) -> int:
        row = self.conn.execute("SELECT id FROM projects WHERE name=?", (name,)).fetchone()
        if row:
            return int(row["id"])
        return self.create_project(name, client, tender_no)

    def get_project(self, project_id: int) -> dict | None:
        """Return one project row as a plain dict, or None when absent.

        Read-only lookup used by the API gateway to distinguish "unknown
        project" (404) from "known project, no session yet" (404 on the
        session routes). It never creates anything.
        """
        row = self.conn.execute(
            "SELECT id, name, client, tender_no, created_at FROM projects WHERE id=?",
            (project_id,),
        ).fetchone()
        return dict(row) if row else None

    def create_project(self, name: str, client: str | None = None,
                       tender_no: str | None = None) -> int:
        cur = self.conn.execute(
            "INSERT INTO projects (name, client, tender_no) VALUES (?,?,?)",
            (name, client, tender_no),
        )
        self.conn.commit()
        pid = int(cur.lastrowid)
        self.journal(pid, actor="system", action="project.create", subject=name)
        return pid

    # ------------------------------------------------------------ documents
    def add_document(
        self,
        project_id: int,
        file_name: str,
        file_hash: str,
        *,
        discipline: str = "UNKNOWN",
        drawing_no: str | None = None,
        revision: str | None = None,
        title: str | None = None,
        media_type: str | None = None,
        page_count: int | None = None,
    ) -> int:
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO documents (project_id, file_name, file_hash, media_type,"
            " discipline, drawing_no, revision, title, page_count)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (project_id, file_name, file_hash.lower(), media_type, discipline,
             drawing_no, revision, title, page_count),
        )
        self.conn.commit()
        if cur.rowcount == 1:
            doc_id = int(cur.lastrowid)
        else:
            row = self.conn.execute(
                "SELECT id FROM documents WHERE project_id=? AND file_hash=?",
                (project_id, file_hash.lower()),
            ).fetchone()
            doc_id = int(row["id"])
        self.journal(project_id, actor="ingest", action="document.add",
                     subject=file_name, payload={"file_hash": file_hash, "doc_id": doc_id})
        return doc_id

    @staticmethod
    def hash_bytes(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    @staticmethod
    def hash_file(path: str | Path) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()

    # ----------------------------------------------------------- graph
    def add_node(self, node: EvidenceNode, ingest_key: str | None = None) -> int:
        """Insert or upsert an evidence node.

        If *ingest_key* is supplied: attempt a plain INSERT.  If it fails with
        an IntegrityError caused by the ux_nodes_ingest_key unique index, the
        key already exists — UPDATE mutable fields and return the existing id.

        Any other IntegrityError (NOT NULL, FOREIGN KEY, CHECK) propagates
        immediately so malformed rows are never silently dropped.

        Rows without an ingest_key are always inserted (human/manual nodes
        that must never be clobbered by a re-ingest).
        """
        import sqlite3 as _sqlite3
        ref = node.ref
        params_with_key = (
            node.project_id, node.node_type, node.label, node.discipline.value,
            ingest_key,
            ref.file_hash if ref else None,
            ref.drawing_no if ref else None,
            ref.revision if ref else None,
            ref.sheet if ref else None,
            ref.page if ref else None,
            ref.zone if ref else None,
            _json(list(ref.bbox)) if ref and ref.bbox else None,
            ref.raw_text if ref else None,
            _json(node.payload),
        )
        if ingest_key:
            try:
                cur = self.conn.execute(
                    "INSERT INTO evidence_nodes"
                    " (project_id, node_type, label, discipline, ingest_key, file_hash,"
                    "  drawing_no, revision, sheet, page, zone, bbox, raw_text, payload)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
                    " RETURNING id",
                    params_with_key,
                )
                new_id = int(cur.fetchone()[0])   # consume cursor BEFORE commit
                self.conn.commit()
                return new_id
            except _sqlite3.IntegrityError as exc:
                if "ux_nodes_ingest_key" not in str(exc) and "UNIQUE" not in str(exc):
                    raise  # NOT NULL / FK / CHECK — propagate loudly
                # Unique-key collision: row exists — update mutable fields
                self.conn.execute(
                    "UPDATE evidence_nodes"
                    " SET label=?, file_hash=?, payload=?"
                    " WHERE ingest_key=?",
                    (node.label,
                     ref.file_hash if ref else None,
                     _json(node.payload),
                     ingest_key),
                )
                self.conn.commit()
                row = self.conn.execute(
                    "SELECT id FROM evidence_nodes WHERE ingest_key=?", (ingest_key,)
                ).fetchone()
                return int(row["id"])
        else:
            cur = self.conn.execute(
                "INSERT INTO evidence_nodes (project_id, node_type, label, discipline, file_hash,"
                " drawing_no, revision, sheet, page, zone, bbox, raw_text, payload)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    node.project_id, node.node_type, node.label, node.discipline.value,
                    ref.file_hash if ref else None,
                    ref.drawing_no if ref else None,
                    ref.revision if ref else None,
                    ref.sheet if ref else None,
                    ref.page if ref else None,
                    ref.zone if ref else None,
                    _json(list(ref.bbox)) if ref and ref.bbox else None,
                    ref.raw_text if ref else None,
                    _json(node.payload),
                ),
            )
            self.conn.commit()
            return int(cur.lastrowid)


    def link(self, project_id: int, src_id: int, dst_id: int, rel: str) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO evidence_edges (project_id, src_id, dst_id, rel)"
            " VALUES (?,?,?,?)",
            (project_id, src_id, dst_id, rel),
        )
        self.conn.commit()

    def neighbours(self, node_id: int, rel: str | None = None) -> list[sqlite3.Row]:
        sql = ("SELECT n.*, e.rel FROM evidence_edges e JOIN evidence_nodes n ON n.id = e.dst_id"
               " WHERE e.src_id = ?")
        args: list[Any] = [node_id]
        if rel:
            sql += " AND e.rel = ?"
            args.append(rel)
        return list(self.conn.execute(sql, args))

    def trace(self, node_id: int, max_depth: int = 8) -> list[dict[str, Any]]:
        """Walk the graph downstream from a node, returning the provenance path."""
        seen: set[int] = set()
        out: list[dict[str, Any]] = []
        frontier = [(node_id, 0)]
        while frontier:
            nid, depth = frontier.pop(0)
            if nid in seen or depth > max_depth:
                continue
            seen.add(nid)
            row = self.conn.execute(
                "SELECT * FROM evidence_nodes WHERE id=?", (nid,)).fetchone()
            if row is None:
                continue
            out.append({"depth": depth, **dict(row)})
            for nb in self.neighbours(nid):
                frontier.append((int(nb["id"]), depth + 1))
        return out

    # ------------------------------------------------------- assumptions
    def upsert_assumption(self, a: Assumption) -> None:
        self.conn.execute(
            "INSERT INTO assumptions (id, project_id, statement, status, rationale,"
            " impact_delta_aud, impact_value, impact_unit, evidence, raised_at, resolved_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(project_id, id) DO UPDATE SET statement=excluded.statement,"
            " status=excluded.status, rationale=excluded.rationale,"
            " impact_delta_aud=excluded.impact_delta_aud, impact_value=excluded.impact_value,"
            " impact_unit=excluded.impact_unit, evidence=excluded.evidence,"
            " resolved_at=excluded.resolved_at",
            (
                a.id, a.project_id, a.statement, a.status.value, a.rationale,
                a.impact_delta_aud,
                a.impact_quantity.value if a.impact_quantity else None,
                a.impact_quantity.unit.value if a.impact_quantity else None,
                _json([e.model_dump(mode="json") for e in a.evidence]),
                a.raised_at.isoformat(),
                a.resolved_at.isoformat() if a.resolved_at else None,
            ),
        )
        self.conn.commit()
        self.journal(a.project_id, actor="agent", action="assumption.upsert",
                     subject=a.id, payload={"status": a.status.value,
                                            "statement": a.statement})

    def next_assumption_id(self, project_id: int) -> str:
        row = self.conn.execute(
            "SELECT id FROM assumptions WHERE project_id=? ORDER BY id DESC LIMIT 1",
            (project_id,),
        ).fetchone()
        n = int(row["id"].split("-")[1]) + 1 if row else 1
        return f"A-{n:02d}"

    def assumptions(self, project_id: int) -> list[sqlite3.Row]:
        return list(self.conn.execute(
            "SELECT * FROM assumptions WHERE project_id=? ORDER BY id", (project_id,)))

    # ----------------------------------------------------------- claims
    def save_claim(self, claim: QuantityClaim, ingest_key: str | None = None) -> str:
        """Persist a quantity claim.

        If *ingest_key* is supplied: attempt a plain INSERT.  On a unique-key
        conflict (ux_claims_ingest_key), UPDATE the mutable fields in place so
        re-ingesting a corrected Mudshark export updates rather than duplicates.

        Any other IntegrityError (NOT NULL, FK, CHECK) propagates immediately —
        a malformed row must fail loudly, not disappear silently.
        """
        import sqlite3 as _sqlite3
        claim_id = claim.claim_id or f"Q-{uuid.uuid4().hex[:12]}"
        evidence_json = _json([e.model_dump(mode="json") for e in claim.evidence])
        workings_json = _json(claim.workings)
        if ingest_key:
            try:
                self.conn.execute(
                    "INSERT INTO quantity_claims"
                    " (claim_id, project_id, ingest_key, description, value, unit,"
                    "  measurement_state, conversion_applied, method, evidence, assumptions, workings)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        claim_id, claim.project_id, ingest_key, claim.description,
                        claim.quantity.value, claim.quantity.unit.value,
                        claim.measurement_state,
                        int(claim.conversion_applied),
                        claim.method,
                        evidence_json, _json(claim.assumption_ids), workings_json,
                    ),
                )
            except _sqlite3.IntegrityError as exc:
                if "ux_claims_ingest_key" not in str(exc) and "UNIQUE" not in str(exc):
                    raise  # NOT NULL / FK / CHECK — propagate loudly
                # Unique-key collision: row exists — update mutable fields
                self.conn.execute(
                    "UPDATE quantity_claims"
                    " SET value=?, description=?, measurement_state=?,"
                    "     conversion_applied=?, evidence=?, workings=?"
                    " WHERE ingest_key=?",
                    (claim.quantity.value, claim.description,
                     claim.measurement_state,
                     int(claim.conversion_applied),
                     evidence_json, workings_json, ingest_key),
                )
                # Fetch existing claim_id so the journal entry is consistent
                row = self.conn.execute(
                    "SELECT claim_id FROM quantity_claims WHERE ingest_key=?", (ingest_key,)
                ).fetchone()
                claim_id = row["claim_id"]
        else:
            self.conn.execute(
                "INSERT INTO quantity_claims (claim_id, project_id, description, value, unit,"
                " measurement_state, conversion_applied, method, evidence, assumptions, workings)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    claim_id, claim.project_id, claim.description, claim.quantity.value,
                    claim.quantity.unit.value, claim.measurement_state, 
                    int(claim.conversion_applied), claim.method,
                    evidence_json, _json(claim.assumption_ids), workings_json,
                ),
            )
        self.conn.commit()
        self.journal(claim.project_id, actor="agent", action="claim.save", subject=claim_id,
                     payload={"value": claim.quantity.value,
                              "unit": claim.quantity.unit.value})
        return claim_id

    # -------------------------------------------------------------- runs
    def save_run(self, run: ToolRun) -> int:
        cur = self.conn.execute(
            "INSERT INTO tool_runs (project_id, tool_id, tier, inputs, outputs, workings,"
            " warnings, ok, error, started_at, duration_ms) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                run.project_id, run.tool_id, run.tier, _json(run.inputs), _json(run.outputs),
                _json(run.workings), _json(run.warnings), int(run.ok), run.error,
                run.started_at.isoformat(), run.duration_ms,
            ),
        )
        self.conn.commit()
        rid = int(cur.lastrowid)
        self.journal(run.project_id, actor="agent", action="tool.run", subject=run.tool_id,
                     payload={"run_id": rid, "tier": run.tier, "ok": run.ok})
        return rid

    def replay_inputs(self, run_id: int) -> dict[str, Any]:
        row = self.conn.execute(
            "SELECT tool_id, inputs FROM tool_runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(f"no such run: {run_id}")
        return {"tool_id": row["tool_id"], "inputs": json.loads(row["inputs"])}

    # ----------------------------------------------------------- journal
    def journal(self, project_id: int | None, *, actor: str, action: str,
                subject: str | None = None, payload: dict[str, Any] | None = None,
                approval: ApprovalLevel | str = ApprovalLevel.SAFE) -> JournalEntry:
        approval_value = approval.value if isinstance(approval, ApprovalLevel) else approval
        prev = self.conn.execute(
            "SELECT entry_hash FROM audit_journal ORDER BY seq DESC LIMIT 1").fetchone()
        prev_hash = prev["entry_hash"] if prev else None
        body = _json({
            "project_id": project_id, "actor": actor, "action": action,
            "subject": subject, "approval": approval_value, "payload": payload or {},
            "prev_hash": prev_hash,
        })
        entry_hash = hashlib.sha256(body.encode()).hexdigest()
        cur = self.conn.execute(
            "INSERT INTO audit_journal (project_id, actor, action, subject, approval,"
            " payload, prev_hash, entry_hash) VALUES (?,?,?,?,?,?,?,?)",
            (project_id, actor, action, subject, approval_value, _json(payload or {}),
             prev_hash, entry_hash),
        )
        self.conn.commit()
        return JournalEntry(int(cur.lastrowid), prev_hash, entry_hash)

    def verify_journal(self) -> bool:
        """Recompute the hash chain; returns False if any entry was tampered with."""
        prev_hash: Optional[str] = None
        for row in self.conn.execute("SELECT * FROM audit_journal ORDER BY seq"):
            body = _json({
                "project_id": row["project_id"], "actor": row["actor"],
                "action": row["action"], "subject": row["subject"],
                "approval": row["approval"], "payload": json.loads(row["payload"]),
                "prev_hash": prev_hash,
            })
            if hashlib.sha256(body.encode()).hexdigest() != row["entry_hash"]:
                return False
            if row["prev_hash"] != prev_hash:
                return False
            prev_hash = row["entry_hash"]
        return True

    def journal_entries(self, project_id: int | None = None) -> list[sqlite3.Row]:
        if project_id is None:
            return list(self.conn.execute("SELECT * FROM audit_journal ORDER BY seq"))
        return list(self.conn.execute(
            "SELECT * FROM audit_journal WHERE project_id=? ORDER BY seq", (project_id,)))

    # -------------------------------------------------------- checkmate
    def save_checkmate(self, project_id: int, subject: str, passed: bool,
                       findings: Iterable[dict[str, Any]]) -> int:
        cur = self.conn.execute(
            "INSERT INTO checkmate_results (project_id, subject, passed, findings)"
            " VALUES (?,?,?,?)",
            (project_id, subject, int(passed), _json(list(findings))),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    # ---------------------------------------------------- revision inspection
    # Phase 5B. Read-only queries used by the revision diff. Every one of them
    # is called inside ``read_snapshot`` while the caller already holds the
    # gateway's session lock; none writes, commits or closes anything.
    #
    # Truncation is reported rather than inferred: each list read asks SQLite
    # for one row more than the bound and says whether that extra row existed,
    # so a caller cannot mistake a clipped list for a complete one.

    def get_document(
        self, project_id: int, document_id: int
    ) -> Optional[sqlite3.Row]:
        """One document, scoped to its project.

        ``project_id`` is part of the predicate, not a post-filter: a document
        id belonging to another project is a miss, never a cross-project read.
        """
        return self.conn.execute(
            "SELECT * FROM documents WHERE project_id=? AND id=?",
            (int(project_id), int(document_id)),
        ).fetchone()

    def document_names_by_hash(
        self, project_id: int, limit: int
    ) -> dict[str, str]:
        """``file_hash -> file_name`` for one project, bounded by *limit*.

        This is the only verified filename lineage in the store: ingestion
        passes the same name to ``add_document`` and to every ``EvidenceRef``
        it builds. ``evidence_nodes.label`` is not a filename column - drawing
        nodes happen to carry the file name there, quantity nodes carry
        ``"<WBS>: <operation>"`` - so a label is never read as one.

        Keys are lowercased, because a canonical identity is casefolded and a
        lookup that missed on case would report a perfectly good reference as
        unverifiable.
        """
        rows = self.conn.execute(
            "SELECT file_hash, file_name FROM documents WHERE project_id=?"
            " ORDER BY id LIMIT ?",
            (int(project_id), int(limit)),
        )
        return {str(row["file_hash"]).lower(): row["file_name"] for row in rows}

    def evidence_nodes_for_document(
        self, project_id: int, document_id: int, file_hash: str, limit: int
    ) -> tuple[list[sqlite3.Row], bool]:
        """Evidence rows belonging to one source document, plus a truncation flag.

        Three ways a row is recognised as belonging to a document, in the order
        the ingest code makes them available:

        * ``payload.doc_id`` - written by every ingest path that adds a
          document, and the only link a drawing node has;
        * ``payload.file_hash`` - written alongside it, and the link that
          survives when a document row is re-created;
        * the ``file_hash`` column - null for every row the current ingest
          writes, because no caller passes a ``ref`` to ``add_node``, but
          consulted so a row written by a caller that does populate it is
          still placed correctly.

        ``json_valid`` guards the extraction: SQLite raises on
        ``json_extract`` over malformed text, and one corrupt payload must
        narrow this query's result, not fail the whole comparison.

        The payload predicates cannot use an index, so this is a filtered scan
        of one project's rows. ``limit`` bounds the result, not the scan; a
        project with more rows than the ceiling pays for the rows it skips.
        """
        rows = list(self.conn.execute(
            "SELECT * FROM evidence_nodes WHERE project_id=?"
            "   AND ( lower(file_hash) = ?"
            "      OR (json_valid(payload)"
            "          AND lower(json_extract(payload, '$.file_hash')) = ?)"
            "      OR (json_valid(payload)"
            "          AND json_extract(payload, '$.doc_id') = ?) )"
            " ORDER BY node_type, id LIMIT ?",
            (
                int(project_id),
                str(file_hash).lower(),
                str(file_hash).lower(),
                int(document_id),
                int(limit) + 1,
            ),
        ))
        if len(rows) > int(limit):
            return rows[: int(limit)], True
        return rows, False

    def claims_for_project(
        self, project_id: int, limit: int
    ) -> tuple[list[sqlite3.Row], bool]:
        """Quantity claims for one project, plus a truncation flag.

        Only the two columns a revision diff needs are selected. The evidence
        column is the claim's own JSON array of references; reading it whole is
        what allows an exact reference match rather than a partial SQL join.
        """
        rows = list(self.conn.execute(
            "SELECT claim_id, evidence FROM quantity_claims WHERE project_id=?"
            " ORDER BY claim_id LIMIT ?",
            (int(project_id), int(limit) + 1),
        ))
        if len(rows) > int(limit):
            return rows[: int(limit)], True
        return rows, False


def evidence_from_row(row: sqlite3.Row) -> EvidenceRef | None:
    if not row["file_hash"]:
        return None
    bbox = json.loads(row["bbox"]) if row["bbox"] else None
    return EvidenceRef(
        file_hash=row["file_hash"], file_name=row["label"], drawing_no=row["drawing_no"],
        revision=row["revision"], sheet=row["sheet"], page=row["page"], zone=row["zone"],
        bbox=tuple(bbox) if bbox else None, raw_text=row["raw_text"],
    )


@contextmanager
def read_snapshot(store: QSStore) -> Iterator[None]:
    """Hold one consistent read snapshot on *store*'s shared connection.

    Why this exists
    ---------------
    A revision diff issues four separate reads - base document, target
    document, both node sets, the project's claims - and they must all describe
    the same instant. WAL mode allows readers to proceed while a writer
    commits, but it grants no such thing on its own: without an explicit
    transaction each statement is its own snapshot, so an ingest landing
    mid-request could be seen by the first read and not by the last. The diff
    would then report a change that never existed.

    Why it is not ``with store``
    ----------------------------
    ``QSStore.__exit__`` calls ``close()``. This connection is the whole
    application's connection, shared by every request, so closing it at the end
    of one diff would take the gateway down. The snapshot therefore opens and
    releases a transaction and nothing else.

    Fail closed
    -----------
    If a transaction is already open, this raises rather than committing it.
    The pending work may belong to another operation, and deciding its fate
    from a read helper is how partial state gets persisted. ``BEGIN DEFERRED``
    also takes its snapshot at the first read, not at the ``BEGIN``, so one
    read is forced before the caller's code runs.
    """
    conn = store.conn
    if conn.in_transaction:
        raise ReadSnapshotError(
            "shared store already has an open transaction; refusing to "
            "commit or roll it back from a read snapshot"
        )

    conn.execute("BEGIN DEFERRED")
    conn.execute("SELECT 1")
    try:
        yield
    finally:
        # Only ever unwind a transaction this helper started, and never let the
        # release failure replace the exception that is already unwinding.
        if conn.in_transaction:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                log.error("read snapshot: rollback failed; connection left as-is")
