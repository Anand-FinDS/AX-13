"""zones and field_agents tables."""
from __future__ import annotations

import pandas as pd
from faker import Faker

from data.generators.context import rng_for


def zones_table(sim: dict) -> pd.DataFrame:
    return pd.DataFrame([{
        "zone_id": z["zone_id"],
        "name": z["name"],
        "languages": list(z["languages"]),
        "waiver_budget_month": float(z["waiver_budget_month"]),
        "waiver_cap_per_merchant": float(z["waiver_cap_per_merchant"]),
    } for z in sim["zones"]])


def field_agents_table(sim: dict, seed: int, fake: Faker) -> pd.DataFrame:
    fa = sim["field_agents"]
    rows = []
    for z in sim["zones"]:
        r = rng_for(seed, z["zone_id"], "agents")
        others = [lang for lang in z["languages"] if lang != "kn"]
        for k in range(fa["per_zone"]):
            n_lang = int(r.integers(fa["languages_per_agent"][0], fa["languages_per_agent"][1] + 1))
            langs = ["kn"] + list(r.choice(others, size=min(n_lang - 1, len(others)), replace=False))
            rows.append({
                "agent_id": f"FA_{z['zone_id'][-1]}{k + 1}",
                "name": f"{fake.first_name()} {fake.last_name()}",
                "zone_id": z["zone_id"],
                "languages": langs,
                "visits_per_day": int(r.integers(fa["visits_per_day"][0], fa["visits_per_day"][1] + 1)),
                "home_lat": round(float(r.uniform(*z["bbox"]["lat"])), 6),
                "home_lng": round(float(r.uniform(*z["bbox"]["lng"])), 6),
            })
    return pd.DataFrame(rows)
