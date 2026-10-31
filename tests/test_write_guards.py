"""Write tools: evidence rule (Retention playbook §1.4), loan approval, waiver caps."""
from __future__ import annotations

import pytest

from app import admin
from mcp_servers import crm, field_ops, offers_messaging, ticketing
from mcp_servers.db import ToolError, query
from tests.conftest import CITE


@pytest.mark.parametrize("bad", [
    {"evidence": [], "playbook_ref": "Retention playbook §3.1"},
    {"evidence": ["  "], "playbook_ref": "Retention playbook §3.1"},
    {"evidence": ["x"], "playbook_ref": ""},
    {"evidence": ["x"], "playbook_ref": "playbook 3.1"},
    {"evidence": ["x"], "playbook_ref": "Retention playbook §12.4"},   # section does not exist
    {"evidence": ["x"], "playbook_ref": "Product catalogue §9"},
])
def test_writes_rejected_without_valid_citation(fresh, bad):
    with pytest.raises(ToolError):
        offers_messaging.send_message("M_DEMO_SMALL", "whatsapp", "hello", **bad)
    with pytest.raises(ToolError):
        field_ops.book_technician("M_DEMO_RAVI", "soundbox", "2026-11-01", **bad)
    assert query("SELECT count(*) AS n FROM actions")[0]["n"] == 0


def test_write_records_evidence_and_section(fresh):
    r = field_ops.book_technician("M_DEMO_RAVI", "soundbox", "2026-11-01", **CITE)
    a = query("SELECT * FROM actions WHERE action_id = %s", (r["booking_id"],))[0]
    assert a["evidence"] == CITE["evidence"] and a["playbook_ref"] == "Retention playbook §3.1"
    assert a["autonomy_tier"] == "agent_alone" and a["ts"].startswith("2026-10-31")


def test_technician_needs_device_and_future_date(fresh):
    with pytest.raises(ToolError):
        field_ops.book_technician("M_DEMO_SMALL", "soundbox", "2026-11-01", **CITE)   # QR only
    with pytest.raises(ToolError):
        field_ops.book_technician("M_DEMO_RAVI", "soundbox", "2026-10-20", **CITE)   # in the past


def test_loan_offer_requires_approved_approval(fresh):
    cite = {"evidence": ["VoiceCore clip1: loan for festive stock"], "playbook_ref": "Product catalogue §4", "line": "grow"}
    with pytest.raises(ToolError, match="needs human approval"):
        offers_messaging.make_offer("M_DEMO_RAVI", "loan", "field", "loan details", **cite)
    req = crm.request_approval("M_DEMO_RAVI", "loan_offer", {"lender": "Partner NBFC", "amount": 200000}, **cite)
    with pytest.raises(ToolError, match="pending"):
        offers_messaging.make_offer("M_DEMO_RAVI", "loan", "field", "loan details", approval_id=req["approval_id"], **cite)
    with pytest.raises(ToolError, match="not for a loan_offer"):
        offers_messaging.make_offer("M_DEMO_MEENA", "loan", "field", "x", approval_id=req["approval_id"], **cite)

    admin.decide_approval(req["approval_id"], "approved", "RM Kavya")
    assert crm.get_approval(req["approval_id"])["decision"] == "approved"
    out = offers_messaging.make_offer("M_DEMO_RAVI", "loan", "field", "loan details", approval_id=req["approval_id"], **cite)
    assert out["response"] == "pending"
    assert offers_messaging.get_offers("M_DEMO_RAVI")[-1]["product"] == "loan"
    with pytest.raises(ToolError, match="already been used"):
        offers_messaging.make_offer("M_DEMO_RAVI", "loan", "field", "again", approval_id=req["approval_id"], **cite)


def test_rejected_approval_blocks(fresh):
    cite = {"evidence": ["x"], "playbook_ref": "Product catalogue §4", "line": "grow"}
    req = crm.request_approval("M_DEMO_MEENA", "loan_offer", {"amount": 100000}, **cite)
    admin.decide_approval(req["approval_id"], "rejected", "RM Kavya")
    with pytest.raises(ToolError, match="rejected"):
        offers_messaging.make_offer("M_DEMO_MEENA", "loan", "field", "x", approval_id=req["approval_id"], **cite)


def test_waiver_caps(fresh):
    cite = {"evidence": ["economics: 400 x 12 x 0.10 = 480 > 149"], "playbook_ref": "Retention playbook §7.1"}
    ok = offers_messaging.grant_waiver("M_DEMO_RAVI", 149, "Soundbox rent", **cite)
    assert ok["tier"] == "agent_alone"
    with pytest.raises(ToolError, match="needs human approval"):
        offers_messaging.grant_waiver("M_DEMO_RAVI", 400, "second month", **cite)   # 149 + 400 > 500 cap
    req = crm.request_approval("M_DEMO_RAVI", "waiver", {"amount": 400}, **cite)
    admin.decide_approval(req["approval_id"], "approved", "RM Kavya")
    over = offers_messaging.grant_waiver("M_DEMO_RAVI", 400, "second month", approval_id=req["approval_id"], **cite)
    assert over["tier"] == "human_approves"
    budget = offers_messaging.get_waiver_budget("Z_A")
    assert budget["spent"] == 549 and budget["remaining"] == budget["budget"] - 549


def test_ticket_lifecycle(fresh):
    t = ticketing.create_ticket("M_DEMO_SMALL", "other", "Asked about fee", "app", **CITE)
    ticketing.chase_ticket(t["ticket_id"], "following up", **CITE)
    ticketing.update_ticket(t["ticket_id"], "resolved", "answered", **CITE)
    assert ticketing.list_tickets("M_DEMO_SMALL", "resolved")[0]["ticket_id"] == t["ticket_id"]
    with pytest.raises(ToolError, match="already resolved"):
        ticketing.chase_ticket(t["ticket_id"], "again", **CITE)


def test_visit_must_match_zone(fresh):
    with pytest.raises(ToolError, match="works in"):
        field_ops.plan_visit("FA_B1", "M_DEMO_RAVI", "2026-11-03", "retain", "x", **CITE)   # Ravi is Z_A
