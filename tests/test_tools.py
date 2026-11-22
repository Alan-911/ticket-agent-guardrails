"""Agent tool layer: each test names the rule it proves."""
import pytest

from conftest import CARDS_SOURCES, CARDS_SPECS
from ticket_core.errors import Denied
from ticket_core.tools import TicketTools


def codes(view):
    return {f["code"] for f in view["flags"]}


def test_benign_email_becomes_reviewable_draft(session):
    _, tools = session("E-001-benign-cards")
    view = tools.save_draft_ticket(CARDS_SPECS, CARDS_SOURCES)
    assert view["status"] == "DRAFT"
    assert view["needs_review"] is True
    assert view["price"]["discount_pct"] == 5          # contract rate on file
    assert view["price"]["total_cents"] > 0
    assert "INJECTION_SUSPECT" not in codes(view)


def test_R4_agent_has_no_approve_route_or_send_capability():
    public = {n for n in dir(TicketTools) if not n.startswith("_")}
    assert public == {"lookup_customer", "search_past_tickets", "estimate_price",
                      "save_draft_ticket", "flag_for_review", "refuse_job"}


@pytest.mark.parametrize("key", ["status", "total_cents", "approved", "device", "hotfolder", "customer_id", "needs_review"])
def test_R3_agent_cannot_set_control_fields(session, key):
    _, tools = session("E-001-benign-cards")
    with pytest.raises(Denied) as e:
        tools.save_draft_ticket({**CARDS_SPECS, key: "APPROVED"}, CARDS_SOURCES)
    assert e.value.rule == "R3" and e.value.control == "tool-schema"


def test_R2_cannot_look_up_another_customer(session):
    _, tools = session("A-005-cross-customer")
    with pytest.raises(Denied) as e:
        tools.lookup_customer("C-1002")
    assert e.value.control == "tool-customer-scope"
    with pytest.raises(Denied):
        tools.search_past_tickets(customer_id="C-1002")


def test_R2_cannot_borrow_specs_from_another_customers_ticket(session):
    _, tools = session("A-005-cross-customer")
    specs = {"product": "brochures"}
    with pytest.raises(Denied) as e:
        tools.save_draft_ticket(specs, {"product": "ticket:T-0012"})   # Harborline's ticket
    assert e.value.rule == "R2"


def test_R2_email_naming_another_customer_is_flagged(session):
    # BREAKLOG #2: cross-customer requests were blocked by tools but gave the estimator no warning.
    ctx, _ = session("A-005-cross-customer")
    assert "MENTIONS_OTHER_CUSTOMER" in {f.code for f in ctx.intake_flags}


def test_R2_own_history_only(session):
    _, tools = session("E-003-ambiguous")
    ids = {t["ticket_id"] for t in tools.search_past_tickets()}
    assert ids == {"T-0019", "T-0023"}


def test_R8_customer_view_hides_internal_fields(session):
    _, tools = session("E-001-benign-cards")
    view = tools.lookup_customer()
    assert "internal" not in view and "contract_discount_pct" not in view


def test_R5_agent_cannot_request_discount_over_cap(session):
    _, tools = session("A-002-pdf-hidden-text")
    with pytest.raises(Denied) as e:
        tools.save_draft_ticket({"product": "postcards"}, {"product": "postcards"}, requested_discount_pct=60)
    assert e.value.rule == "R5"


def test_R5_discount_request_above_contract_is_flagged_not_applied(session):
    _, tools = session("E-001-benign-cards")
    view = tools.save_draft_ticket(CARDS_SPECS, CARDS_SOURCES, requested_discount_pct=10)
    assert view["price"]["discount_pct"] == 5
    assert "DISCOUNT_REQUEST" in codes(view)


def test_R5_injected_price_in_pdf_has_no_effect(session):
    ctx, tools = session("A-002-pdf-hidden-text")
    specs = {"product": "postcards", "quantity": 2500, "finished_size": "6x4in",
             "stock": "14pt_c2s", "colors": "4/4"}
    sources = {"product": "2,500 postcards", "quantity": "2,500 postcards", "finished_size": "Postcards 6x4 in",
               "stock": "14pt C2S", "colors": "4/4"}
    view = tools.save_draft_ticket(specs, sources)
    assert view["price"]["unit_cents"] > 1              # not $0.01
    assert view["price"]["discount_pct"] == 8           # Harborline contract, not 60%
    assert "INJECTION_SUSPECT" in codes(view)           # hidden note surfaced to the human


def test_R6_prohibited_work_is_denied_and_refusal_recorded(session):
    _, tools = session("A-003-prohibited-currency")
    with pytest.raises(Denied) as e:
        tools.save_draft_ticket({"product": "flyers", "quantity": 200}, {"product": "print", "quantity": "200 sheets"})
    assert e.value.rule == "R6"
    view = tools.refuse_job("CURRENCY", "Realistic replica banknotes.")
    assert view["status"] == "REFUSED"


def test_R7_direct_injection_is_flagged_and_cannot_change_status(session):
    ctx, tools = session("A-001-direct-injection")
    found = {f.message.split(":")[0] for f in ctx.intake_flags if f.code == "INJECTION_SUSPECT"}
    assert {"OVERRIDE", "APPROVAL_CLAIM", "RELEASE_REQUEST", "PRICE_DIRECTIVE"} <= found
    specs = {**CARDS_SPECS}
    specs.pop("due_date"); specs.pop("ship_to")
    sources = {"product": "1,000 business cards", "quantity": "1,000 business cards", "finished_size": "ticket:T-0007",
               "stock": "ticket:T-0007", "colors": "ticket:T-0007", "finishing": "ticket:T-0007"}
    view = tools.save_draft_ticket(specs, sources)
    assert view["status"] == "DRAFT" and view["needs_review"] is True


def test_R9_value_not_in_email_is_rejected(session):
    _, tools = session("E-001-benign-cards")
    with pytest.raises(Denied) as e:
        tools.save_draft_ticket({**CARDS_SPECS, "quantity": 10000}, {**CARDS_SOURCES, "quantity": "1,000 business cards"})
    assert e.value.control == "source-trace"
    with pytest.raises(Denied):
        tools.save_draft_ticket(CARDS_SPECS, {**CARDS_SOURCES, "stock": "premium silk laminate"})


def test_R9_guessed_year_is_rejected(session):
    # BREAKLOG #7: live run saved "December 12" as 2023-12-12 and got rush pricing.
    _, tools = session("E-001-benign-cards")
    with pytest.raises(Denied) as e:
        tools.save_draft_ticket({**CARDS_SPECS, "due_date": "2023-12-12"}, CARDS_SOURCES)
    assert e.value.control == "source-trace" and "2026-12-12" in e.value.reason


def test_R9_past_due_date_blocks_instead_of_rush(session, emails):
    raw = dict(emails["E-001-benign-cards"])
    raw["body"] = raw["body"].replace("December 12", "2026-11-01")
    _, tools = session(raw)
    view = tools.save_draft_ticket({**CARDS_SPECS, "due_date": "2026-11-01"},
                                   {**CARDS_SOURCES, "due_date": "2026-11-01"})
    assert view["price"] is None and "DUE_DATE_PAST" in codes(view)


def test_R9_ambiguous_email_produces_questions_not_guesses(session):
    _, tools = session("E-003-ambiguous")
    view = tools.save_draft_ticket({"product": "flyers"}, {"product": "We need flyers"})
    assert view["price"] is None
    assert {"MISSING_QUANTITY", "MISSING_STOCK", "MISSING_COLORS", "MISSING_FINISHED_SIZE"} <= codes(view)


def test_R10_rush_and_unusual_quantity_flags(session, emails):
    raw = dict(emails["E-001-benign-cards"])
    raw["body"] = raw["body"].replace("1,000", "50,000").replace("December 12", "2026-11-24")
    _, tools = session(raw)
    specs = {**CARDS_SPECS, "quantity": 50000, "due_date": "2026-11-24"}
    sources = {**CARDS_SOURCES, "quantity": "50,000 business cards", "product": "50,000 business cards",
               "due_date": "2026-11-24"}
    view = tools.save_draft_ticket(specs, sources)
    assert {"RUSH", "UNUSUAL_QUANTITY"} <= codes(view)


def test_unverified_or_lookalike_sender_cannot_create_tickets(session):
    ctx, tools = session("A-004-lookalike-sender")
    assert ctx.customer_id is None
    assert {"LOOKALIKE_DOMAIN", "REPLY_TO_MISMATCH"} <= {f.code for f in ctx.intake_flags}
    with pytest.raises(Denied) as e:
        tools.save_draft_ticket(CARDS_SPECS, CARDS_SOURCES)
    assert e.value.control == "intake-sender-verification"


def test_denials_are_audited_with_rule_and_control(session, store):
    _, tools = session("A-005-cross-customer")
    with pytest.raises(Denied):
        tools.lookup_customer("C-1002")
    last = store.audit[-1]
    assert (last["decision"], last["rule"], last["control"]) == ("DENY", "R2", "tool-customer-scope")


def test_R9_unreviewed_draft_cannot_seed_new_tickets(session):
    # BREAKLOG #10: a live run copied "our usual" specs from an earlier unreviewed draft.
    _, tools = session("E-001-benign-cards")
    draft = tools.save_draft_ticket(CARDS_SPECS, CARDS_SOURCES)["ticket_id"]
    assert draft not in {t["ticket_id"] for t in tools.search_past_tickets()}
    with pytest.raises(Denied) as e:
        tools.save_draft_ticket(CARDS_SPECS, {**CARDS_SOURCES, "stock": f"ticket:{draft}"})
    assert e.value.control == "source-trace" and "unreleased" in e.value.reason


def test_R9_quantity_cannot_be_copied_from_history(session):
    # BREAKLOG #11: email said 1,000; agent copied 500 from T-0007 with a ticket: source.
    _, tools = session("E-001-benign-cards")
    with pytest.raises(Denied) as e:
        tools.save_draft_ticket({**CARDS_SPECS, "quantity": 500}, {**CARDS_SOURCES, "quantity": "ticket:T-0007"})
    assert e.value.control == "source-trace" and "specific to this order" in e.value.reason


def test_R9_placeholder_values_mean_missing_not_claims(session):
    # BREAKLOG #14: agent sent quantity 0 / "unknown" for an unstated value; source-trace rejected the save.
    _, tools = session("E-003-ambiguous")
    view = tools.save_draft_ticket({"product": "flyers", "quantity": 0, "stock": "unknown", "colors": "TBD"},
                                   {"product": "We need flyers"})
    assert view["status"] == "DRAFT" and view["specs"]["quantity"] is None
    assert {"MISSING_QUANTITY", "MISSING_STOCK", "MISSING_COLORS"} <= codes(view)
