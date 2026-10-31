# Compliance Rules

> Illustrative policy document for the AX-13 prototype. Not Paytm policy and not legal advice. Written in our own words, with reference to the RBI (Digital Lending) Directions, 2025. Fee figures follow the proposed framework as reported and may change on final notification.

These rules apply to every message AX-13 drafts and every action it takes. The compliance gate (§7) enforces them before anything reaches a merchant. A blocked draft cites the rule it broke (for example "Compliance rules §2").

---

## 1. Loan offers: required disclosures

Any message that offers or describes a loan must include all of these:

1. **Lender name**: the regulated entity that lends, not just "our partner".
2. **Amount** offered by the lender.
3. **Tenure.**
4. **APR**: the all-in annual percentage rate, not just the interest rate.
5. **All charges**: processing fee, prepayment terms, late-payment charges.
6. **Key Fact Statement (KFS) link**, given before the merchant commits.

If any item is missing, the draft does not go out.

## 2. No eligibility promises

The lender decides eligibility. AX-13, field agents and RMs do not.

- Not allowed: "You are eligible", "pre-approved", "guaranteed", "you will get ₹X", unless the lender has issued that decision in writing.
- Allowed: "You can apply; the lender will assess your eligibility."

## 3. No misleading or pressure claims

**3.1 No pressure.** No false urgency ("offer ends today", "last chance") unless it is true and the end date is stated.

**3.2 Never link credit to fee avoidance or the ₹1 lakh threshold.** This covers merchants holding back UPI near the exemption limit.
- Not allowed: "Cross ₹1 lakh and you'll be eligible for a ₹2 lakh loan."
- Not allowed: "Take more UPI so you qualify for a loan."
- Allowed, and true: "Payments you take digitally are recorded. Recorded sales are the history lenders look at when they assess an application." No promise of an outcome.

**3.3 No comparisons without data.** Claims such as "cheaper than X" or "you'll earn more" need the merchant's own numbers behind them, shown in the message.

## 4. Consent

- **Data use:** use a merchant's data only for servicing their account and the purposes they agreed to.
- **Recording:** ask for consent at the start of every recorded visit or call ("This visit is being recorded for service quality, is that okay?"). Store the answer with the recording. No consent, no recording.
- **Privacy:** mask personal data (names, phone numbers, account numbers) in transcripts before storage or analysis.

## 5. Human approval for credit

Every credit offer needs RM approval before it reaches the merchant. The graph pauses, the RM sees the draft with its evidence and gate result, and approves or rejects. AX-13 cannot skip this step for any merchant or amount.

## 6. Fee communication

- **Accurate:** rate 0.4% on UPI payments above ₹2,000, cap ₹300 per payment, exemption under ₹1 lakh monthly UPI.
- **Framed correctly:** say "under the proposed framework" until the final notification.
- **Personal:** use the merchant's own numbers from their last full month (Retention playbook §6.2).
- **No fear:** do not overstate the fee to push a product. Do not understate it to stop a complaint.
- **Exempt merchants:** they get the not-affected message once, and nothing else.

## 7. Gate procedure

**7.1 Rules first.** Fast checks in code, run on every draft:
- Loan drafts: all six items in §1 present.
- Banned patterns: eligibility claims (§2), pressure phrases (§3.1), credit linked to the threshold or fee (§3.2).
- Fee figures match §6 and the merchant's actual numbers.
- Recording actions have a consent flag (§4).

**7.2 LLM judge second.** A judge model reads drafts that pass the rules and flags softer problems: implied promises, misleading tone, claims the data does not support. It returns pass or block, with the rule and a one-line reason.

**7.3 Outcomes.**

| Result | What happens |
|---|---|
| Pass | Draft continues to the autonomy check (Autonomy policy §1) |
| Block | Draft stopped; the agent may redraft once, citing the reason |
| Block twice, or judge unsure | Sent to a human with both drafts and the reasons |

**7.4 Logging.** Every gate decision is logged with the draft, the result, the rule cited and the judge's reason (Autonomy policy §5).

**7.5 Testing.** The gate is tested against fixed bad drafts in `eval/fixtures/`, including the over-promising loan draft to Meena. Gate catch rate is one of the reported eval scores.
