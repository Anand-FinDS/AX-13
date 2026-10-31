"""Compliance gate rules (no LLM): the Meena fixture is blocked on the right sections; compliant drafts pass."""
import json

from agent.nodes.gates import check_rules
from agent.nodes.plan import fee_templates, fill
from common.settings import REPO_ROOT

LOAN_FACTS = {"lender": "Kaveri Finance Ltd", "amount": "₹95,000", "tenure": "12 months", "apr": "24% APR",
              "kfs_link": "https://kfs.example.com/Q-1"}


def test_meena_fixture_blocked_on_expected_rules():
    fx = json.loads((REPO_ROOT / "eval" / "fixtures" / "meena_loan_overpromise.json").read_text(encoding="utf-8"))
    hits = check_rules(fx["draft_kind"], fx, LOAN_FACTS)
    assert {h["rule"] for h in hits} >= set(fx["expected"]["rules"])


def test_compliant_loan_draft_passes_rules():
    en = ("Kaveri Finance Ltd has made an in-principle offer of ₹95,000 for 12 months at 24% APR. Processing fee 2% "
          "plus GST; no prepayment charge after 6 EMIs; late payment 2% a month. Key Fact Statement: "
          "https://kfs.example.com/Q-1. You can apply; the lender will assess your eligibility and decides.")
    assert check_rules("loan_offer", {"english": en, "text": en}, LOAN_FACTS) == []


def test_approval_wording_and_threshold_link_blocked():
    en = ("Kaveri Finance Ltd: in-principle approval for ₹95,000, 12 months, 24% APR, processing 2%, prepay free after "
          "6 EMIs, late 2%. https://kfs.example.com/Q-1. Keep your UPI above ₹1 lakh.")
    rules = {h["rule"] for h in check_rules("loan_offer", {"english": en, "text": en}, LOAN_FACTS)}
    assert {"Compliance rules §2", "Compliance rules §3.2"} <= rules


def test_fee_templates_pass_rules():
    est = fill(fee_templates()["estimate"], value_above_2000="1,34,303", est_monthly_fee="537")
    facts = {"value_above_2000": "₹1,34,303", "est_monthly_fee": "₹537"}
    assert check_rules("fee_message", {"english": est, "text": est}, facts) == []
    a = fill(fee_templates()["A_exempt"], monthly_upi_value="38,074")
    assert check_rules("fee_message", {"english": a, "text": a}, {"variant": "A_exempt", "monthly_upi_value": "₹38,074"}) == []
    b = fee_templates()["B_no_payments_above_2000"]
    assert check_rules("fee_message", {"english": b, "text": b}, {"variant": "B_no_payments_above_2000"}) == []


def test_wrong_fee_figure_blocked():
    est = fill(fee_templates()["estimate"], value_above_2000="1,34,303", est_monthly_fee="900")
    facts = {"value_above_2000": "₹1,34,303", "est_monthly_fee": "₹537"}
    assert {h["rule"] for h in check_rules("fee_message", {"english": est, "text": est}, facts)} == {"Compliance rules §6"}
