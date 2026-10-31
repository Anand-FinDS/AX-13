"""Config loading, simulation calendar and RNG streams shared by the table generators."""
from __future__ import annotations

import zlib
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from common.settings import get_settings

CONFIG_FILES = ("sim_config", "categories", "archetypes", "demo_merchants")


def load_configs(config_dir: Path | None = None) -> dict:
    d = config_dir or get_settings().data_config_dir
    return {name: yaml.safe_load((d / f"{name}.yaml").read_text(encoding="utf-8")) for name in CONFIG_FILES}


def resolve_seed(cfg: dict) -> int:
    override = get_settings().ax13_seed
    return int(override) if override is not None else int(cfg["sim_config"]["seed"])


def rng_for(seed: int, *keys: str) -> np.random.Generator:
    """Independent, stable stream per (merchant, table): adding draws in one table never shifts another."""
    return np.random.default_rng([seed, *(zlib.crc32(k.encode()) for k in keys)])


class Calendar:
    """Day index 0 = first history day (D-119); i_d0 = D0; last index = D+30."""

    def __init__(self, sim: dict):
        dt, sc = sim["dates"], sim["scale"]
        self.d0: date = dt["sim_start_date"]
        self.g0: date = dt["fee_go_live_date"]
        self.n_hist: int = sc["history_days"]
        self.n_future: int = sc["future_days"]
        self.n = self.n_hist + self.n_future
        self.start = self.d0 - timedelta(days=self.n_hist - 1)
        self.end = self.start + timedelta(days=self.n - 1)
        self.dates = pd.date_range(self.start, periods=self.n, freq="D")
        self.day_list = [d.date() for d in self.dates]
        self.i_d0 = self.n_hist - 1
        self.i_g0 = self.idx(self.g0)
        self.dow = self.dates.dayofweek.to_numpy()
        self.festive = self._triangle(dt["festive_start"], dt["festive_peak"], dt["festive_end"])
        self.festive_dates = (dt["festive_start"], dt["festive_peak"], dt["festive_end"])

    def idx(self, d: date) -> int:
        return (d - self.start).days

    def at(self, anchor: str, offset: int) -> int:
        return (self.i_d0 if anchor == "D" else self.i_g0) + int(offset)

    def date(self, i: int) -> date:
        return self.start + timedelta(days=int(i))

    def _triangle(self, start: date, peak: date, end: date) -> np.ndarray:
        """0 before start, rises to 1 at peak, back to 0 at end."""
        out = np.zeros(self.n)
        for i, d in enumerate(self.day_list):
            if start <= d <= peak:
                out[i] = (d - start).days / max((peak - start).days, 1)
            elif peak < d <= end:
                out[i] = (end - d).days / max((end - peak).days, 1)
        return out

    def month_segments(self) -> list[tuple[str, np.ndarray, bool]]:
        """(YYYY-MM, day indices in window, whole calendar month inside the window)."""
        keys = self.dates.strftime("%Y-%m").to_numpy()
        segs = []
        for key in dict.fromkeys(keys):
            idx = np.flatnonzero(keys == key)
            first, last = self.dates[idx[0]], self.dates[idx[-1]]
            complete = first.day == 1 and last.day == last.days_in_month
            segs.append((key, idx, complete))
        return segs

    def completed_history_months(self) -> list[tuple[str, np.ndarray]]:
        """Months fully inside history and finished on or before D0."""
        return [(k, idx) for k, idx, complete in self.month_segments() if complete and idx[-1] <= self.i_d0]


def lognormal_from_range(r: np.random.Generator, lo: float, hi: float, z: float = 1.645) -> float:
    """Draw with [lo, hi] read as the 5th-95th percentile of a lognormal."""
    return float(r.lognormal(np.log(np.sqrt(lo * hi)), np.log(hi / lo) / (2 * z)))


def ramp(mult: np.ndarray, start: int, end: int, to: float) -> None:
    """Linear ramp from the value just before `start` to `to` across [start, end]; holds `to` after."""
    n = len(mult)
    if start >= n or end < start:
        return
    frm = mult[start - 1] if start > 0 else 1.0
    length = end - start + 1
    vals = frm + (to - frm) * np.arange(1, length + 1) / length
    lo = max(start, 0)
    seg = vals[lo - start: n - start]
    mult[lo: lo + len(seg)] = seg
    if end + 1 < n:
        mult[max(end + 1, 0):] = to
