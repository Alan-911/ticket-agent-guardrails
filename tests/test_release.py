"""Approval and release gate (R4, R7, R11). The agent is not involved at all here."""
import pytest

from conftest import CARDS_SOURCES, CARDS_SPECS, SECRET
from ticket_core.approval import TOKEN_TTL_SECONDS, ApprovalService
from ticket_core.errors import Denied
from ticket_core.release import LocalHotFolder, ReleaseGate

NOW = 1_795_000_000.0


@pytest.fixture
def pipeline(store, session, tmp_path):
    clock = {"t": NOW}
    approvals = ApprovalService(store, SECRET, clock=lambda: clock["t"])
    gate = ReleaseGate(store, LocalHotFolder(tmp_path), SECRET, clock=lambda: clock["t"])
    _, tools = session("E-001-benign-cards")
    tid = tools.save_draft_ticket(CARDS_SPECS, CARDS_SOURCES)["ticket_id"]
    return store, approvals, gate, tid, clock, tmp_path


def approve(approvals, tid):
    approvals.mark_reviewed(tid, "est-rosa")
    token = approvals.send_proof(tid, "est-rosa")
    approvals.record_approval(token, "dana@brightleaf-dental.example")
    return token


def test_happy_path_reaches_the_right_hot_folder(pipeline):
    store, approvals, gate, tid, _, root = pipeline
    token = approve(approvals, tid)
    assert approvals.outbox[-1]["to"] == "dana@brightleaf-dental.example"   # address on file
    path = gate.release(tid, token)
    assert (root / "fiery").exists() and path.endswith(f"{tid}-v1.json")
    assert store.ticket(tid).status.value == "RELEASED"


def test_R4_cannot_release_without_approval(pipeline):
    store, approvals, gate, tid, _, _ = pipeline
    approvals.mark_reviewed(tid, "est-rosa")
    token = approvals.send_proof(tid, "est-rosa")
    with pytest.raises(Denied) as e:
        gate.release(tid, token)
    assert e.value.control == "release-gate"


def test_R4_non_estimator_cannot_review(pipeline):
    _, approvals, _, tid, _, _ = pipeline
    with pytest.raises(Denied) as e:
        approvals.mark_reviewed(tid, "ticket-agent")
    assert e.value.control == "estimator-auth"


def test_R11_approval_must_come_from_address_on_file(pipeline):
    _, approvals, _, tid, _, _ = pipeline
    approvals.mark_reviewed(tid, "est-rosa")
    token = approvals.send_proof(tid, "est-rosa")
    with pytest.raises(Denied) as e:
        approvals.record_approval(token, "billing@quick-pay.example")
    assert e.value.control == "approver-on-file"


def test_R11_approve_then_swap_via_supported_edit_voids_approval(pipeline):
    store, approvals, gate, tid, _, _ = pipeline
    token = approve(approvals, tid)
    store.update_specs(tid, {"quantity": 50000}, actor="csr-bob")
    with pytest.raises(Denied):
        gate.release(tid, token)


def test_R11_approve_then_swap_via_direct_db_tamper_is_caught(pipeline):
    store, approvals, gate, tid, _, _ = pipeline
    token = approve(approvals, tid)
    store.ticket(tid).specs["quantity"] = 50000        # bypasses update_specs entirely
    with pytest.raises(Denied) as e:
        gate.release(tid, token)
    assert e.value.control == "spec-hash-binding"


def test_R11_price_tamper_after_approval_is_caught(pipeline):
    store, approvals, gate, tid, _, _ = pipeline
    token = approve(approvals, tid)
    store.ticket(tid).price["total_cents"] = 1
    with pytest.raises(Denied) as e:
        gate.release(tid, token)
    assert e.value.control == "spec-hash-binding"


def test_R11_forged_and_expired_tokens_rejected(pipeline):
    _, approvals, gate, tid, clock, _ = pipeline
    token = approve(approvals, tid)
    body, sig = token.rsplit(".", 1)
    with pytest.raises(Denied):
        gate.release(tid, body + "." + "0" * len(sig))
    clock["t"] = NOW + TOKEN_TTL_SECONDS + 1
    with pytest.raises(Denied) as e:
        gate.release(tid, token)
    assert "expired" in e.value.reason


def test_R7_tainted_ticket_cannot_skip_human_review(pipeline):
    store, approvals, gate, tid, _, _ = pipeline
    token = approve(approvals, tid)
    store.ticket(tid).reviewed_by = None                # simulate a skipped review
    with pytest.raises(Denied) as e:
        gate.release(tid, token)
    assert e.value.rule == "R7"


def test_R9_blocking_flags_stop_the_proof(store, session):
    approvals = ApprovalService(store, SECRET, clock=lambda: NOW)
    _, tools = session("E-003-ambiguous")
    tid = tools.save_draft_ticket({"product": "flyers"}, {"product": "We need flyers"})["ticket_id"]
    approvals.mark_reviewed(tid, "est-kwame")
    with pytest.raises(Denied) as e:
        approvals.send_proof(tid, "est-kwame")
    assert e.value.control == "blocking-flags"


def test_routing_is_deterministic(store, session):
    from ticket_core.release import route
    from ticket_core.schema import Ticket
    mk = lambda p, q: Ticket("T-x", "C-1", "e", {"product": p, "quantity": q}, None)
    assert route(mk("banners", 2)) == "caldera"
    assert route(mk("brochures", 5000)) == "prinergy"
    assert route(mk("brochures", 500)) == "fiery"
    assert route(mk("business_cards", 100000)) == "fiery"
