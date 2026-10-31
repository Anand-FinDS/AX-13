"""device_health_daily: one row per device (Soundbox and/or POS) per day, history + future."""
from __future__ import annotations

from datetime import datetime, time, timedelta

import numpy as np
import pandas as pd

from data.generators.context import Calendar, rng_for
from data.generators.merchants import Plan


def generate_devices(plan: Plan, cal: Calendar, cfg: dict, seed: int) -> pd.DataFrame:
    dev = cfg["sim_config"]["device"]
    n = cal.n
    frames = []
    for device_type, has in (("soundbox", plan.has_soundbox), ("pos", plan.has_pos)):
        if not has:
            continue
        r = rng_for(seed, plan.merchant_id, "device", device_type)
        uptime = r.uniform(*dev["healthy_uptime_pct"], n)
        err_peak = r.poisson(dev["network_errors_peak_lambda"], n)
        err_off = r.poisson(dev["network_errors_offpeak_lambda"], n)
        if plan.network_from is not None:
            net = np.arange(n) >= plan.network_from
            uptime = np.where(net, r.uniform(95.0, 99.5, n), uptime)
            err_peak = np.where(net, r.poisson(plan.params["peak_errors_lambda"], n), err_peak)
            err_off = np.where(net, r.poisson(plan.params["offpeak_errors_lambda"], n), err_off)
        hw = plan.hw_fault & (device_type == "soundbox")
        offline = plan.offline if device_type == "soundbox" or plan.closed_from is not None else np.zeros(n, bool)
        uptime = np.where(offline, r.uniform(0.0, 8.0, n), uptime)
        if plan.closed_from is not None:
            uptime[plan.closed_from:] = 0.0
            err_peak[plan.closed_from:] = 0
            err_off[plan.closed_from:] = 0
        uptime = np.round(uptime, 2)

        heartbeats = []
        prev = datetime.combine(cal.date(-1), time(23, 55))
        for i in range(n):
            if uptime[i] >= 50:
                prev = datetime.combine(cal.date(i), time(23, 30)) + timedelta(minutes=int(r.integers(0, 30)))
            elif uptime[i] > 0:
                prev = datetime.combine(cal.date(i), time(0, 0)) + timedelta(minutes=int(uptime[i] / 100 * 1440))
            heartbeats.append(prev)

        frames.append(pd.DataFrame({
            "merchant_id": plan.merchant_id,
            "device_id": f"{'SB' if device_type == 'soundbox' else 'POS'}-{plan.merchant_id}",
            "date": cal.day_list,
            "device_type": device_type,
            "uptime_pct": uptime,
            "offline_minutes": np.rint((100 - uptime) / 100 * 1440).astype(int),
            "network_errors_peak": err_peak.astype(int),
            "network_errors_offpeak": err_off.astype(int),
            "hardware_fault_flag": hw,
            "last_heartbeat": heartbeats,
        }))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
