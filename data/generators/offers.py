"""offers_history: sparse past offers with plausible responses (history window only)."""
from __future__ import annotations

import numpy as np

from data.generators.context import Calendar, rng_for
from data.generators.merchants import Plan


def generate_offers(plan: Plan, cal: Calendar, cfg: dict, seed: int) -> list[dict]:
    of = cfg["sim_config"]["offers"]
    cat = cfg["categories"]["categories"][plan.category]
    r = rng_for(seed, plan.merchant_id, "offers")
    planted = [{"date": cal.date(o["idx"]), "product": o["product"], "channel": o["channel"],
                "response": o["response"]} for o in plan.offers]
    if plan.scripted:
        return [{"merchant_id": plan.merchant_id, **o} for o in planted]

    products = ["insurance", "plan_change", "loan"]
    if not plan.has_soundbox:
        products.append("soundbox")
    if not plan.has_pos and cat["products"]["pos"] > 0:
        products.append("pos")
    channels, ch_p = list(of["channel_mix"]), np.array(list(of["channel_mix"].values()))
    resp, resp_p = list(of["response_mix"]), np.array(list(of["response_mix"].values()))
    last = min(plan.closed_from if plan.closed_from is not None else cal.i_d0, cal.i_d0)
    for _ in range(r.poisson(of["rate_per_90d"] * cal.n_hist / 90)):
        planted.append({"date": cal.date(int(r.integers(0, max(last, 1)))),
                        "product": str(r.choice(products)),
                        "channel": str(r.choice(channels, p=ch_p / ch_p.sum())),
                        "response": str(r.choice(resp, p=resp_p / resp_p.sum()))})
    return [{"merchant_id": plan.merchant_id, **o} for o in planted]
