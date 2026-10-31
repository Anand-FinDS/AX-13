"""merchant_labels: hidden ground truth for evaluation only. Never exposed via MCP or prompts."""
from __future__ import annotations

import pandas as pd

from data.generators.context import Calendar
from data.generators.merchants import Plan


def labels_table(plans: list[Plan], merchants: pd.DataFrame, fee_affected: set[str], cal: Calendar) -> pd.DataFrame:
    tier = merchants.set_index("merchant_id")["merchant_value_tier"]
    rows = []
    for p in plans:
        lab = dict(p.label)
        affected = p.merchant_id in fee_affected
        if p.archetype == "post_fee_dropper" and lab["expected_action"] == "fee_cost_estimate":
            lab["expected_escalation"] = tier[p.merchant_id] == "high"
        rows.append({
            "merchant_id": p.merchant_id,
            "archetype": p.archetype,
            "onset_date": cal.date(p.onset_idx) if p.onset_idx is not None else None,
            "expected_line": lab["expected_line"],
            "expected_action": lab["expected_action"],
            "expected_escalation": bool(lab["expected_escalation"]),
            "is_fee_affected": affected,
            "notes": " ".join(str(lab.get("notes", "")).split()),
        })
    return pd.DataFrame(rows)
