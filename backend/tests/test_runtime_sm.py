import pytest
from datetime import datetime
from qsagent.storage import QSStore
from qsagent.contracts.evidence import ApprovalLevel, QuantityClaim, Quantity, Unit, ToolRun, EvidenceRef
from qsagent.runtime import AgentSession, SessionState, InvalidTransitionError, ApprovalRequiredError, ToolFailureError

def make_claim(state="m3_insitu", conv=False):
    return QuantityClaim(
        project_id=1,
        description="Test",
        quantity=Quantity(value=72.0, unit=Unit.M3),
        method="trench.volume",
        evidence=[EvidenceRef(file_hash="a"*64, file_name="f", sheet="1", raw_text="72")],
        workings=["working"],
        measurement_state=state,
        conversion_applied=conv,
    )

def make_tool(tier=1, ok=True, err=None):
    return ToolRun(
        project_id=1,
        tool_id="trench.volume",
        tier=tier,
        inputs={"depth_m": 1.0},
        outputs={"volume_m3": 72.0},
        ok=ok,
        error=err
    )

def test_complete_lifecycle():
    session = AgentSession(1)
    assert session.state == SessionState.RECEIVED
    session.plan({"steps": 1})
    assert session.state == SessionState.PLANNED
    session.start_executing()
    assert session.state == SessionState.EXECUTING
    session.record_tool_run(make_tool())
    session.start_validating()
    assert session.state == SessionState.VALIDATING
    session.start_reasoning([make_claim()])
    assert session.state == SessionState.REASONING
    session.deliver("Done", [make_claim()])
    assert session.state == SessionState.DELIVERED
    assert len(session.history) == 5

def test_invalid_received_to_executing():
    session = AgentSession(1)
    with pytest.raises(InvalidTransitionError):
        session.start_executing()
    assert session.state == SessionState.RECEIVED

def test_invalid_received_to_validating():
    session = AgentSession(1)
    with pytest.raises(InvalidTransitionError):
        session.start_validating()
    assert session.state == SessionState.RECEIVED

def test_invalid_planned_to_validating():
    session = AgentSession(1)
    session.plan({})
    with pytest.raises(InvalidTransitionError):
        session.start_validating()
    assert session.state == SessionState.PLANNED

def test_invalid_planned_to_delivered():
    session = AgentSession(1)
    session.plan({})
    with pytest.raises(InvalidTransitionError):
        session.deliver("text", [make_claim()])
    assert session.state == SessionState.PLANNED

def test_invalid_executing_to_delivered():
    session = AgentSession(1)
    session.plan({})
    session.start_executing()
    with pytest.raises(InvalidTransitionError):
        session.deliver("text", [make_claim()])
    assert session.state == SessionState.EXECUTING

def test_invalid_validating_to_delivered():
    session = AgentSession(1)
    session.plan({})
    session.start_executing()
    session.record_tool_run(make_tool())
    session.start_validating()
    with pytest.raises(InvalidTransitionError):
        session.deliver("text", [make_claim()])
    assert session.state == SessionState.VALIDATING

def test_invalid_delivered_terminal():
    session = AgentSession(1)
    session.plan({})
    session.start_executing()
    session.record_tool_run(make_tool())
    session.start_validating()
    session.start_reasoning([make_claim()])
    session.deliver("!", [make_claim()])
    with pytest.raises(InvalidTransitionError):
        session.plan({})
    assert session.state == SessionState.DELIVERED

def test_replan_allowed():
    session = AgentSession(1)
    session.plan({})
    session.plan({})
    assert session.state == SessionState.PLANNED
    assert [r.action for r in session.history] == ["plan", "plan"]

def test_tool_failure_raises():
    session = AgentSession(1)
    session.plan({})
    session.start_executing()
    run = make_tool(ok=False, err="timeout")
    with pytest.raises(ToolFailureError) as exc:
        session.record_tool_run(run)
    assert exc.value.run == run
    assert session.state == SessionState.EXECUTING

def test_tool_failure_then_restart():
    session = AgentSession(1)
    session.plan({})
    session.start_executing()
    with pytest.raises(ToolFailureError):
        session.record_tool_run(make_tool(ok=False, err="timeout"))
    session.restart("tool failed")
    assert session.state == SessionState.RECEIVED
    actions = [r.action for r in session.history]
    assert actions == ["plan", "start_executing", "restart"]

def test_no_tool_runs_blocks_validating():
    session = AgentSession(1)
    session.plan({})
    session.start_executing()
    with pytest.raises(InvalidTransitionError, match="at least one"):
        session.start_validating()

def test_checkmate_blocks_reasoning():
    session = AgentSession(1)
    session.plan({})
    session.start_executing()
    session.record_tool_run(make_tool())
    session.start_validating()
    bad_claim = make_claim(state="UNRESOLVED", conv=True)
    with pytest.raises(InvalidTransitionError, match="failed validation"):
        session.start_reasoning([bad_claim])
    assert session.state == SessionState.VALIDATING

def test_checkmate_blocks_delivery():
    session = AgentSession(1)
    session.plan({})
    session.start_executing()
    session.record_tool_run(make_tool())
    session.start_validating()
    session.start_reasoning([make_claim()])
    bad_claim = make_claim(state="UNRESOLVED", conv=True)
    with pytest.raises(InvalidTransitionError, match="failed delivery"):
        session.deliver("t", [bad_claim])
    assert session.state == SessionState.REASONING

def test_approval_required_for_tier3():
    session = AgentSession(1)
    session.plan({})
    session.start_executing()
    with pytest.raises(ApprovalRequiredError):
        session.record_tool_run(make_tool(tier=3))
    assert session.state == SessionState.EXECUTING

def test_approve_unblocks_tier3():
    session = AgentSession(1)
    session.plan({})
    session.start_executing()
    session.approve("tool_trench.volume", ApprovalLevel.CONFIRM)
    session.record_tool_run(make_tool(tier=3))
    assert session.state == SessionState.EXECUTING

def test_approval_cleared_on_restart():
    session = AgentSession(1)
    session.plan({})
    session.start_executing()
    session.approve("tool_trench.volume", ApprovalLevel.CONFIRM)
    session.restart("change mind")
    session.plan({})
    session.start_executing()
    with pytest.raises(ApprovalRequiredError):
        session.record_tool_run(make_tool(tier=3))

def test_history_audit_completeness():
    session = AgentSession(1)
    session.plan({})
    session.start_executing()
    session.record_tool_run(make_tool())
    session.start_validating()
    session.start_reasoning([make_claim()])
    session.deliver("x", [make_claim()])
    assert len(session.history) == 5
    for r in session.history:
        assert isinstance(r.timestamp, datetime)
        assert r.from_state is not None
        assert r.to_state is not None
        assert r.payload is not None

def test_journal_written_to_store():
    store = QSStore(":memory:")
    pid = store.get_or_create_project("T")
    session = AgentSession(pid, store=store)
    session.plan({})
    session.start_executing()
    session.approve("tool_trench.volume", ApprovalLevel.CONFIRM)
    session.record_tool_run(make_tool(tier=3))
    session.start_validating()
    session.start_reasoning([make_claim()])
    session.deliver("y", [make_claim()])
    
    assert len(session.history) == 5
    for r in session.history:
        assert r.journal_seq > 0
        
    res = list(store.conn.execute("SELECT * FROM audit_journal"))
    seq_map = {r["seq"]: r["action"] for r in res}
    assert len(res) >= len(session.history)
    for r in session.history:
        assert r.journal_seq in seq_map
        assert seq_map[r.journal_seq] == r.action

    actions = [r["action"] for r in res]
    assert actions.count("approve") == 1
    assert actions.count("record_tool_run") == 1

def test_restart_from_validating():
    session = AgentSession(1)
    session.plan({})
    session.start_executing()
    session.record_tool_run(make_tool())
    session.start_validating()
    session.restart("need more tools")
    assert session.state == SessionState.EXECUTING

def test_restart_from_reasoning():
    session = AgentSession(1)
    session.plan({})
    session.start_executing()
    session.record_tool_run(make_tool())
    session.start_validating()
    session.start_reasoning([make_claim()])
    session.restart("found a bug")
    assert session.state == SessionState.VALIDATING

def test_record_tool_run_invalid_state():
    session = AgentSession(1)
    # State is RECEIVED
    with pytest.raises(InvalidTransitionError, match="Must be EXECUTING"):
        session.record_tool_run(make_tool())
    
    session.plan({"steps": 1})
    session.start_executing()
    session.record_tool_run(make_tool())
    session.start_validating()
    # State is VALIDATING
    with pytest.raises(InvalidTransitionError, match="Must be EXECUTING"):
        session.record_tool_run(make_tool())

def test_review_cannot_authorize_confirm():
    session = AgentSession(1)
    session.plan({})
    session.start_executing()
    # Approve as REVIEW
    session.approve("tool_trench.volume", ApprovalLevel.REVIEW)
    # Tier 3 requires CONFIRM
    with pytest.raises(ApprovalRequiredError, match="CONFIRM approval"):
        session.record_tool_run(make_tool(tier=3))

def test_approve_invalid_state():
    session = AgentSession(1)
    session.plan({})
    session.start_executing()
    session.record_tool_run(make_tool())
    session.start_validating()
    session.start_reasoning([make_claim()])
    session.deliver("done", [make_claim()])
    # State is DELIVERED
    with pytest.raises(InvalidTransitionError, match="after delivery"):
        session.approve("anything", ApprovalLevel.REVIEW)

def test_has_failed_tools():
    session = AgentSession(1)
    session.plan({})
    session.start_executing()
    assert session.has_failed_tools is False

    session.record_tool_run(make_tool(ok=True))
    assert session.has_failed_tools is False

    # A failing run is recorded before ToolFailureError is raised.
    with pytest.raises(ToolFailureError):
        session.record_tool_run(make_tool(ok=False, err="boom"))
    assert session.has_failed_tools is True

