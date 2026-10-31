# Retention Playbook

> Illustrative policy document for the AX-13 prototype. Not Paytm policy. Fee figures follow the proposed framework as reported and may change on final notification. Numbers marked (illus.) are prototype settings, listed for tuning in Autonomy policy §6.

This playbook is for the Save line: merchants who are at risk because something is broken, unclear or getting worse. AX-13 cites the section it acted on (for example "Retention playbook §3.1"). Field agents use the same sections.

---

## 1. Principles

**1.1 Save before Grow.** A merchant with an open Save case gets no Grow offer. Once the case is resolved, the merchant is eligible for Grow again.

**1.2 Fix the cause.** A volume drop is a symptom. Find out why before acting. A discount does not fix a dead Soundbox, and a technician does not fix a network problem.

**1.3 Explain before offering.** If a charge or fee is behind the problem, explain it first, with the merchant's own numbers. Offer a waiver or refund only after that, and only under §6.3 and §7.

**1.4 Every recommendation cites its evidence.** State the signals (for example "UPI down 40% in 3 weeks, Soundbox offline 6 days, open complaint") and the playbook section. No evidence, no action.

**1.5 Remote first.** Resolve remotely whatever can be resolved remotely. Book a technician for device-only fixes. Send a field agent only when a conversation is needed. One visit can retain and sell.

---

## 2. Diagnosis guide

**2.1 Signal table.** Match the pattern before choosing an action.

| Cause | What the data shows | Go to |
|---|---|---|
| Device | Sudden drop on one day; Soundbox offline or hardware fault flagged; complaint about announcements often follows in 1–3 days | §3 |
| Network | Failures and network errors concentrated in peak hours (11–14h, 18–21h); off-peak normal; device hardware OK; dip only at peaks | §4 |
| Settlement / failed-but-debited | Delayed or held settlements; customers debited but payment failed; tickets about missing money | §5 |
| Charge | A deduction the merchant was not told about; complaint within a few days; mild dip after | §6 |
| Fee | Fee-affected merchant (monthly UPI ≥ ₹1 lakh with payments above ₹2,000) drops after fee go-live | §6.2, §7 |
| Competitor | Gradual decline over weeks; device and network healthy; no fault tickets; sometimes a mention of a rival offer | §8 |
| Closed | Zero transactions for 14 days or more; device offline the same period; no tickets | §9 |

**2.2 Conflicting signals.** Act on the most specific cause first. A hardware fault explains a drop better than a vague decline does. If two causes are both clearly present (for example a dead Soundbox and an unexplained charge), open both and fix the device first, because it is quicker to fix and fixing it stops further losses.

**2.3 Seasonality check.** Festivals move every merchant's volume. Before treating a drop as a Save signal, compare it with the same-category merchants in the same zone over the same days.

- A drop counts as seasonal only if it is in line with those peers.
- A drop clearly larger than peers is a real signal. "Clearly larger" means below the bottom quarter of peer changes over the same days.
- From fee go-live onward, compare a fee-affected merchant only with peers who are **not** fee-affected. Fee go-live falls five days after Diwali, so post-festive decline and fee reaction overlap. If fee-affected merchants were compared with each other, a fee-driven drop would look normal.
- Never use seasonality to close a case that also has a device, charge or complaint signal.

Example (prototype data): in the week after fee go-live, Ravi's daily UPI count falls 28%. Over the same days the non-affected kiranas in his zone change by −1% (median), and even the bottom quarter only by −9% or less. His drop is a real signal.

---

## 3. Device issues

**3.1 Soundbox swap via technician.** Book a technician when the Soundbox shows a hardware fault, or has been offline for more than a day while the network is healthy and a remote reset (§3.2) has failed.
- Book the visit and send the merchant a WhatsApp update with the date and time window. The agent does this alone.
- Keep the ticket open until uptime has held for 2 days after the swap, then close it and tell the merchant.
- Do not pause Grow eligibility longer than needed. Once the device is fixed and the case closed, §1.1 no longer applies.

**3.2 Remote reset steps.** Try these first, by message or call, in the merchant's language:
1. Hold the power button for 10 seconds and restart.
2. Check the SIM or Wi-Fi indicator and move the device away from metal shutters or cold-storage fridges.
3. Charge it for 30 minutes on the original adapter.
4. Make a ₹1 test payment and confirm the announcement.

If the device still fails after step 4, book a technician (§3.1).

**3.3 POS faults.** A card machine fault blocks card sales but not UPI. Tell the merchant to keep taking UPI by QR meanwhile, and book a POS technician. Card faults on high-value merchants go to the human-approves tier under Autonomy policy §4.

---

## 4. Network issues

**4.1 Signs.** Errors and failed payments cluster in peak hours. Off-peak is normal and the device reports no hardware fault. A technician visit will find nothing.

**4.2 Peak-hour guidance message.** Send a short message in the merchant's language:
- Where the device sits matters: near the shop front, away from metal shutters.
- If the device has a SIM, ask whether a Wi-Fi option is available in the shop.
- Keep the printed QR visible as a backup during rush hours.
- Ask customers to wait for the announcement before leaving.

**4.3 No technician.** Do not book a technician for a network problem. It wastes a visit and does not fix the cause.

**4.4 Persistent issues.** If peak-hour errors continue for 7 days (illus.) after the guidance message, escalate to the network operations team with the error pattern attached.

---

## 5. Settlement and failed-but-debited payments

**5.1 Status check.** Look up the settlement or transaction before replying. Tell the merchant exactly where it stands: settled, delayed (with expected time) or held (with reason).

**5.2 Reversal timeline message.** Use the turnaround times in RBI circular DPSS.CO.PD No.629/02.01.014/2019-20 (20 Sep 2019) on failed transactions.
- **UPI payment to a merchant** (customer debited, no confirmation at the merchant): auto-reversal within T+5 days. If it takes longer, the customer is due ₹100 per day of delay beyond that.
- **UPI person-to-person transfers** have a T+1 reversal. Mention this only to explain why a merchant payment can take longer than a transfer between friends.

Tell the merchant what their customer should expect, so the customer is not sent back and forth.

**5.3 Follow-up to closure.** Keep the ticket open until the reversal or settlement is confirmed. Then tell the merchant. Do not close a ticket because time has passed.

---

## 6. Charges and fees

**6.1 Explaining deductions.** Name the deduction, the amount, the date and what it was for.

| Type | What to say |
|---|---|
| Soundbox rent | Monthly rental, taken from the first settlement of the month |
| Maintenance charge | Name the service it covers. If the merchant was not told in advance, say so plainly and go to §6.3 |
| UPI fee (from go-live) | Explain using §6.2 with the merchant's own numbers |
| Other | Name it exactly; never say "system charge" |

**6.2 Fee estimate message.** Use the merchant's own numbers from the last full month. Say "proposed framework" until the final notification.

> Under the proposed framework, a 0.4% fee applies to UPI payments above ₹2,000, capped at ₹300 per payment. Merchants with monthly UPI under ₹1 lakh are exempt. Last month you received ₹{value_above_2000} in UPI payments above ₹2,000. Your estimated fee is about ₹{est_monthly_fee} a month. Payments of ₹2,000 or less are not charged.

**Not-affected variants.** Pick the variant from the merchant's data for the last full month.

*A. Exempt (monthly UPI under ₹1 lakh):*

> Under the proposed framework, the new UPI fee does not apply to you. Your monthly UPI is ₹{monthly_upi_value}, under the ₹1 lakh exemption. Nothing changes for you.

*B. Above ₹1 lakh, but no payments above ₹2,000:*

> Under the proposed framework, the fee applies only to UPI payments above ₹2,000. Your UPI payments are all ₹2,000 or less, so the fee does not apply to you.

Send the not-affected message once. Do not follow it with offers.

**6.3 Refund and waiver rules.**
- **Unnotified charge:** recommend a refund by default. The merchant was not told, so the charge should not stand while it is reviewed.
- **Correct, notified charge:** explain it (§6.1). A waiver is possible only under §7.
- **Angry or disputed:** hand to a human (Autonomy policy §4). The agent prepares the case: amount, date, notification status, ticket history.

---

## 7. Waivers

**7.1 Economics check.** Grant a waiver only if the expected retained revenue is greater than the waiver cost.

> expected retained revenue = monthly revenue from the merchant × months the merchant is expected to stay × increase in the chance of staying because of the waiver

Use the merchant's own revenue. Until LearnLoop has enough outcomes to estimate it, use a starting estimate of +10 percentage points (illus.) for the increase in the chance of staying. Once LearnLoop has outcomes for similar merchants, its estimate replaces the starting value.

Example: a merchant earning us ₹400 a month, expected to stay 12 months, with the starting +10 points: 400 × 12 × 0.10 = ₹480. A ₹149 rental waiver passes; a ₹500 waiver does not.

**7.2 Caps.** Per merchant: ₹500 a month (illus.), which the agent may grant alone. Per zone: ₹50,000 a month (illus.) in total. Both are set per zone in configuration.

**7.3 Approval tiers.**

| Waiver | Who decides |
|---|---|
| Within the per-merchant cap and the zone budget has room | Agent alone |
| Above the per-merchant cap, or the zone budget is used up | RM approves |
| Disputed charge, angry merchant | Human only |

---

## 8. Competitor risk

**8.1 Signs.** Gradual decline over 3–6 weeks with a healthy device and network. Sometimes the merchant mentions a rival offer: a free device, lower charges or cashback.

**8.2 Field conversation guide.**
1. Ask what changed and listen. Do not defend first.
2. Ask what the rival offered, in their words.
3. Compare honestly: their actual charges with us, from their own data, against the rival's offer as the merchant described it.
4. Fix anything real that comes up (device, settlement, charge) before talking about offers.
5. If the merchant is still leaving, record why. That is useful too.

**8.3 What not to say.** Do not run down the rival. Do not claim the rival's offer is fake or temporary unless you can show it. Do not promise anything outside the catalogue or the waiver rules.

**8.4 Log it.** Write the rival's name (if given), their offer and the merchant's reaction to the CRM. LearnLoop uses this to rank retention actions.

---

## 9. Closed or inactive shops

**9.1 Remote check before any visit.** Zero transactions for 14 days or more may mean closed, renovating, travelling or switched. Before sending anyone, check the device heartbeat and settlements, then call or message the merchant.

**9.2 Mark inactive.** If the shop is confirmed closed, mark it inactive and remove it from visit plans. No offers.

**9.3 Reactivation.** If the merchant is away and plans to reopen, set a follow-up for the date they give. On reopening, check the device works (§3.2) before anything else.
