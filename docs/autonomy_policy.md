# Autonomy Policy

> Illustrative policy document for the AX-13 prototype. Not Paytm policy. Numbers marked (illus.) are prototype settings, collected in §6 for tuning.

This policy sets what AX-13 may do alone, what it prepares for a human, and what it leaves to humans. Autonomy and compliance rules are set centrally; zone agents cannot change them.

---

## 1. Three tiers

| Agent acts alone | Agent prepares, human approves | Human only |
|---|---|---|
| Ticket chase | Loan offers (RM) | Angry or disputed cases |
| Settlement status check | Waivers above the zone cap (RM) | Low-confidence decisions (§2) |
| Technician booking | Actions on high-value merchants | |
| Standard offers from the catalogue | | |
| CRM updates | | |
| Waivers within the zone cap | | |

**1.1 Router rule: Save before Grow.** A merchant with an open Save case gets no Grow offer. Resolved Save merchants become eligible for Grow.

**1.2 Order of checks.** Every action passes the compliance gate (Compliance rules §7) first, then this tier check. A draft that fails the gate never reaches a human queue as if it were ready.

**1.3 The higher tier wins.** If an action fits two tiers, use the higher one. For example, a standard offer to a high-value merchant needs approval.

## 2. Confidence thresholds

**2.1 Decision confidence.** Each Save or Grow decision carries a confidence from the scoring step. Below 0.6 (illus.), the case goes to a human with the evidence attached. AX-13 does not act.

**2.2 ASR confidence.** Transcript segments below 0.7 (illus.) confidence go back to the field agent to confirm before they are written to the CRM or turned into signals.

**2.3 Extraction.** If VoiceCore extracts a commitment or complaint from a low-confidence segment, the extraction is held until the segment is confirmed.

## 3. Waiver caps

- **Per merchant:** ₹500 a month (illus.). Within this, the agent may grant a waiver alone, if the economics check passes (Retention playbook §7.1).
- **Per zone:** ₹50,000 a month (illus.) across all merchants in the zone.
- **Zone budget used up:** every further waiver needs RM approval, even small ones.
- Caps are set per zone in configuration (`sim_config.yaml › zones`).

## 4. Escalation triggers

Hand a case to a human when any of these is true:

| Trigger | Tier |
|---|---|
| Angry sentiment in a ticket, call or visit | Human only |
| Merchant disputes a charge or an action | Human only |
| Decision confidence below threshold (§2.1) | Human only |
| Legal or regulatory mention (complaint to a regulator, lawyer, police) | Human only |
| Loan offer of any size | Human approves (RM) |
| High-value merchant (top value tier) | Human approves |
| Third repeat issue within 30 days (illus.) | Human approves |
| Waiver above cap or beyond zone budget (§3) | Human approves (RM) |

## 5. Audit logging

Every action is logged, whoever took it.

- **Actions:** merchant, line (Save/Grow), action type, payload, autonomy tier, evidence cited (signals and playbook section), gate result, approver if any, status.
- **Approvals:** request time, decision, decided by, decision time, and the draft as the human saw it.
- **Traces:** each loop run (score, route, map, act) is traced end to end, so any action can be followed back to its inputs.
- **Privacy:** logs hold masked transcripts only (Compliance rules §4).
- **Retention:** keep everything for the prototype. A production retention period would be set with the compliance team.

## 6. Illustrative thresholds (tuning table)

Prototype settings, not validated policy. Tune them here. Other documents refer to this table.

| # | Setting | Value (illus.) | Used in |
|---|---|---|---|
| 1 | Decision confidence below which a human decides | 0.6 | §2.1 |
| 2 | ASR segment confidence below which the field agent confirms | 0.7 | §2.2 |
| 3 | Soundbox readiness: UPI payments a day, sustained | 10 a day for 4 weeks | Product catalogue §1.2 |
| 4 | Loan path readiness: steady volume history | 90 days | Product catalogue §1.2 |
| 5 | Peak-hour network errors persisting after guidance, before escalation to network ops | 7 days | Retention playbook §4.4 |
| 6 | Repeat issues before escalation | 3rd issue within 30 days | §4 |
| 7 | Starting estimate of the increase in the chance of staying because of a waiver, used until LearnLoop has outcomes | +10 percentage points | Retention playbook §7.1 |

Waiver caps (§3) and Soundbox rental (Product catalogue §2) are also illustrative but live in configuration.
