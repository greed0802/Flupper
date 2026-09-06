import sqlite3

import pytest

from qsagent.contracts import (
    Assumption,
    AssumptionStatus,
    Discipline,
    EvidenceNode,
    EvidenceRef,
    Quantity,
    QuantityClaim,
    Unit,
)
from qsagent.storage import QSStore
from qsagent.tools import REGISTRY

HASH = "b" * 64


@pytest.fixture()
def store():
    s = QSStore(":memory:")
    yield s
    s.close()


@pytest.fixture()
def project(store):
    return store.create_project("Bunbury Outer Ring Road - Package C", client="Main Roads WA")


def ref(**kw):
    base = dict(file_hash=HASH, file_name="C-204.pdf", drawing_no="C-204",
                revision="3", sheet="04", raw_text="SW-02 DN375")
    base.update(kw)
    return EvidenceRef(**base)


class TestEvidenceContract:
    def test_bad_hash_rejected(self):
        with pytest.raises(ValueError, match="SHA-256"):
            ref(file_hash="nope")

    def test_locator_required(self):
        with pytest.raises(ValueError, match="source locator"):
            ref(raw_text=None, bbox=None)

    def test_bbox_satisfies_locator(self):
        e = ref(raw_text=None, bbox=(10.0, 20.0, 100.0, 40.0))
        assert e.bbox[2] == 100.0

    def test_sheet_or_page_required(self):
        with pytest.raises(ValueError, match="sheet or page"):
            ref(sheet=None, page=None)

    def test_citation_is_human_readable(self):
        assert "C-204" in ref().citation()
        assert "Sheet 04" in ref().citation()

    def test_claim_requires_evidence(self):
        with pytest.raises(Exception):
            QuantityClaim(project_id=1, description="Trench", method="trench.volume",
                          quantity=Quantity(value=1, unit=Unit.M3), evidence=[])


class TestProjectsAndDocuments:
    def test_document_hash_is_idempotent(self, store, project):
        a = store.add_document(project, "C-204.pdf", HASH, drawing_no="C-204")
        b = store.add_document(project, "C-204.pdf", HASH, drawing_no="C-204")
        assert a == b

    def test_hash_helpers(self, store, tmp_path):
        p = tmp_path / "x.bin"
        p.write_bytes(b"hello")
        assert store.hash_file(p) == store.hash_bytes(b"hello")


class TestKnowledgeGraph:
    def test_document_to_boq_chain(self, store, project):
        ids = []
        for node_type, label in [
            ("document", "Tender Drawings Rev 3"),
            ("drawing", "C-204 Rev 3"),
            ("element", "Stormwater Line SW-02"),
            ("quantity", "Trench excavation 72.0 m3"),
            ("rate", "Excavation $38.50/m3"),
            ("boq_line", "BOQ 3.12 Stormwater trench"),
        ]:
            ids.append(store.add_node(EvidenceNode(
                project_id=project, node_type=node_type, label=label,
                discipline=Discipline.CIVIL, ref=ref())))
        for src, dst in zip(ids, ids[1:]):
            store.link(project, src, dst, "derives")
        trace = store.trace(ids[0])
        assert [t["node_type"] for t in trace] == [
            "document", "drawing", "element", "quantity", "rate", "boq_line"]
        assert trace[-1]["depth"] == 5

    def test_neighbours_filtered_by_rel(self, store, project):
        a = store.add_node(EvidenceNode(project_id=project, node_type="drawing", label="A"))
        b = store.add_node(EvidenceNode(project_id=project, node_type="element", label="B"))
        c = store.add_node(EvidenceNode(project_id=project, node_type="element", label="C"))
        store.link(project, a, b, "derives")
        store.link(project, a, c, "supersedes")
        assert [n["label"] for n in store.neighbours(a, "derives")] == ["B"]


class TestAssumptions:
    def test_ids_increment(self, store, project):
        assert store.next_assumption_id(project) == "A-01"
        store.upsert_assumption(Assumption(
            id="A-01", project_id=project, statement="100mm Type A bedding assumed"))
        assert store.next_assumption_id(project) == "A-02"

    def test_lifecycle_sets_resolved_at(self, store, project):
        a = Assumption(id="A-01", project_id=project,
                       statement="Rock excavation below RL 12.5",
                       status=AssumptionStatus.CONFIRMED)
        assert a.resolved_at is not None
        store.upsert_assumption(a)
        assert store.assumptions(project)[0]["status"] == "CONFIRMED"

    def test_assumed_cannot_be_pre_resolved(self, project):
        from datetime import datetime, timezone
        with pytest.raises(ValueError, match="cannot have resolved_at"):
            Assumption(id="A-01", project_id=project,
                       statement="Bedding type assumed",
                       resolved_at=datetime.now(timezone.utc))

    def test_bad_id_format_rejected(self, project):
        with pytest.raises(Exception):
            Assumption(id="ASSUMPTION1", project_id=project,
                       statement="Bedding type assumed")

    def test_upsert_updates_in_place(self, store, project):
        a = Assumption(id="A-01", project_id=project, statement="Type A bedding")
        store.upsert_assumption(a)
        a2 = a.model_copy(update={"status": AssumptionStatus.CONFIRMED,
                                  "impact_delta_aud": -4200.0})
        store.upsert_assumption(Assumption(**a2.model_dump()))
        rows = store.assumptions(project)
        assert len(rows) == 1 and rows[0]["impact_delta_aud"] == -4200.0


class TestClaimsAndReplay:
    def test_claim_round_trip(self, store, project):
        cid = store.save_claim(QuantityClaim(
            project_id=project, description="SW-02 trench excavation",
            quantity=Quantity(value=72.0, unit=Unit.M3), method="trench.volume",
            evidence=[ref()], workings=["0.6 x 1.2 x 100 = 72.000"]))
        row = store.conn.execute(
            "SELECT * FROM quantity_claims WHERE claim_id=?", (cid,)).fetchone()
        assert row["value"] == 72.0 and row["unit"] == "m3"

    def test_run_replays_to_identical_outputs(self, store, project):
        run = REGISTRY.run("trench.volume", project, length_m=100, width_mm=600, depth_m=1.2)
        rid = store.save_run(run)
        payload = store.replay_inputs(rid)
        replay = REGISTRY.run(payload["tool_id"], project, **payload["inputs"])
        assert replay.outputs == run.outputs

    def test_unknown_run_raises(self, store):
        with pytest.raises(KeyError):
            store.replay_inputs(999)


class TestAuditJournal:
    def test_chain_verifies(self, store, project):
        store.journal(project, actor="qs", action="test.a")
        store.journal(project, actor="qs", action="test.b")
        assert store.verify_journal() is True

    def test_updates_are_blocked(self, store, project):
        store.journal(project, actor="qs", action="test.a")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            store.conn.execute("UPDATE audit_journal SET action='hacked'")

    def test_deletes_are_blocked(self, store, project):
        store.journal(project, actor="qs", action="test.a")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            store.conn.execute("DELETE FROM audit_journal")

    def test_tampering_breaks_the_chain(self, store, project):
        store.journal(project, actor="qs", action="test.a")
        store.conn.execute("DROP TRIGGER audit_journal_no_update")
        store.conn.execute("UPDATE audit_journal SET action='hacked' WHERE seq=1")
        assert store.verify_journal() is False

    def test_every_write_is_journalled(self, store, project):
        store.add_document(project, "C-204.pdf", HASH)
        store.save_run(REGISTRY.run("trench.volume", project, length_m=10,
                                    width_mm=600, depth_m=1.0))
        actions = [r["action"] for r in store.journal_entries(project)]
        assert actions == ["project.create", "document.add", "tool.run"]

    def test_approval_level_recorded(self, store, project):
        store.journal(project, actor="user", action="boq.write", approval="CONFIRM")
        assert store.journal_entries(project)[-1]["approval"] == "CONFIRM"
