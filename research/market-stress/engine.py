"""Market Stress Index engine. State of Blocks, Macro column.

The reading (0-100) asks one question: how close are US equities to a washout, the forced-
selling flush that historically marks the buy? Three inputs, a third of the reading each:

  fear       VIX                        15 -> 40
  fragility  VVIX (the VIX of the VIX)  90 -> 150
  damage     S&P 500 under its 100-day  0% -> 10% under

Two seals: the S&P is under its 100-day, and 20-session realized vol is over 25%.
Washout = both seals broken with the reading at 80 or higher.

The funding check (MOVE + DXY) sits outside the reading. It answers a different question:
what kind of flush is this? Rates vol in the top 10% of its last year, or the dollar up 2%+
in 20 sessions, marks a funding flush. In the 2006-2026 backtest those were the flushes that
were not done yet, and the ones that hurt Bitcoin most.

Lineage: inspired by a public Crash Risk Index (VIX, VVIX, COR1M, momentum vs the 100-day,
crash triggers incl. "COR1M > 60"). method="replica" rebuilds that scale for comparison.
COR1M was tested and dropped: its range drifted (median ~55 in 2009-11, ~10 in 2024-26) and
removing it changed no washout call except a weak May 2022 one.

Pure pandas/numpy. Input: DataFrame indexed by session date with spx, vix, vvix
(+ cor1m for the replica, + move / dxy for the funding check).
"""
from __future__ import annotations
import numpy as np
import pandas as pd

# reading
VIX_LO, VIX_HI = 15.0, 40.0
VVIX_LO, VVIX_HI = 90.0, 150.0
DAMAGE_FULL = 0.10
# seals
RV_LINE = 25.0
# states
WASHOUT_MIN = 80
EBB_DROP, EBB_HOLD = 20, 3        # 20 points under the crest, held 3 sessions = clearing
EPISODE_END = 40
ZONES = [(0, 40, "calm"), (40, 65, "uneasy"), (65, 80, "stressed"), (80, 101, "panic")]
# funding check
RATES_WIN, RATES_LINE = 252, 0.90  # MOVE in the top 10% of its last year
DOLLAR_LINE = 2.0                  # DXY up 2%+ over 20 sessions

PILLARS = ["fear", "fragility", "damage"]


def _c01(x):
    return np.clip(x, 0.0, 1.0)


def rolling_pct(s: pd.Series, win: int, minp: int | None = None) -> pd.Series:
    """Percentile of today's value inside its trailing window (today included), 0..1."""
    return s.rolling(win, min_periods=minp or int(win * 0.8)).rank(pct=True)


def trend(p: pd.DataFrame) -> pd.DataFrame:
    o = pd.DataFrame(index=p.index)
    o["ma100"] = p["spx"].rolling(100, min_periods=100).mean()
    o["dist"] = p["spx"] / o["ma100"] - 1.0
    o["rv20"] = np.log(p["spx"]).diff().rolling(20, min_periods=20).std() * np.sqrt(252) * 100
    return o


def score(p: pd.DataFrame, method: str = "washout") -> pd.DataFrame:
    """method: washout (ours) | washout4 (ours + COR1M, research) | replica (the original's scale)."""
    p = p.sort_index()
    t = trend(p)
    o = pd.DataFrame(index=p.index)
    seals = ["seal_trend", "seal_vol"]
    if method == "replica":
        o["fear"] = 25 * _c01((p["vix"] - 15) / 25)
        o["fragility"] = 25 * _c01((p["vvix"] - 85) / 90)
        o["herding"] = 25 * _c01((p["cor1m"] - 20) / 40)
        o["damage"] = 25 * _c01(-t["dist"] / DAMAGE_FULL)
        o["seal_herd"] = (p["cor1m"] > 60).astype(float).where(p["cor1m"].notna())
        pill = ["fear", "fragility", "herding", "damage"]; seals.append("seal_herd")
    elif method in ("washout", "washout4"):
        w = 100 / 3 if method == "washout" else 25
        o["fear"] = w * _c01((p["vix"] - VIX_LO) / (VIX_HI - VIX_LO))
        o["fragility"] = w * _c01((p["vvix"] - VVIX_LO) / (VVIX_HI - VVIX_LO))
        o["damage"] = w * _c01(-t["dist"] / DAMAGE_FULL)
        pill = list(PILLARS)
        if method == "washout4":
            hp = rolling_pct(p["cor1m"], 252)
            o["herding"] = 25 * _c01((hp - 0.8) / 0.2)
            o["seal_herd"] = (hp >= 0.95).astype(float).where(hp.notna())
            pill.append("herding"); seals.append("seal_herd")
    else:
        raise ValueError(method)
    o["seal_trend"] = (t["dist"] < 0).astype(float).where(t["dist"].notna())
    o["seal_vol"] = (t["rv20"] > RV_LINE).astype(float).where(t["rv20"].notna())
    o["score"] = o[pill].sum(axis=1, min_count=len(pill))
    o["seals"] = o[seals].sum(axis=1, min_count=len(seals))
    o["seals_max"] = len(seals)
    return o.join(t)


def funding(p: pd.DataFrame) -> pd.DataFrame:
    """The funding check. Needs move and/or dxy."""
    o = pd.DataFrame(index=p.index)
    if "move" in p:
        mp = rolling_pct(p["move"], RATES_WIN)
        o["move_pct"] = mp
        o["rates_stress"] = (mp >= RATES_LINE).astype(float).where(mp.notna())
    if "dxy" in p:
        d20 = (p["dxy"] / p["dxy"].shift(20) - 1) * 100
        o["dxy20"] = d20
        o["dollar_stress"] = (d20 >= DOLLAR_LINE).astype(float).where(d20.notna())
    if {"rates_stress", "dollar_stress"} <= set(o):
        o["funding_stress"] = ((o["rates_stress"] == 1) | (o["dollar_stress"] == 1)).astype(float)
        o.loc[o["rates_stress"].isna() & o["dollar_stress"].isna(), "funding_stress"] = np.nan
    return o


def zone(x: float) -> str:
    for lo, hi, name in ZONES:
        if lo <= x < hi:
            return name
    return "calm"


def states(s: pd.DataFrame, washout_min: float = WASHOUT_MIN, ebb_drop: float = EBB_DROP,
           ebb_hold: int = EBB_HOLD, end_below: float = EPISODE_END) -> pd.DataFrame:
    """Label each session using only data up to that session.

    Outside an episode: calm / uneasy / stressed / panic by reading.
    An episode opens on the first washout (all seals broken, reading >= washout_min).
    Inside it: washout on washout days; crest while the reading holds near its peak;
    clearing once it has sat ebb_drop points under the crest for ebb_hold sessions.
    A fresh washout after clearing starts a new crest. The episode ends under end_below.
    """
    sc = s["score"].to_numpy(dtype=float)
    se = s["seals"].to_numpy(dtype=float)
    sm = s["seals_max"].to_numpy(dtype=float)
    n = len(sc)
    lab = [None] * n
    crest = np.full(n, np.nan)
    ep = np.zeros(n, dtype=int)
    ebb_sig = np.zeros(n, dtype=int)
    first = np.zeros(n, dtype=int)
    in_ep = False; peak = np.nan; ebbed = False; below = 0; ep_id = 0
    for i in range(n):
        x = sc[i]
        if np.isnan(x):
            continue
        is_wash = (se[i] == sm[i]) and (x >= washout_min)
        if is_wash and not in_ep:
            in_ep = True; ep_id += 1; peak = x; ebbed = False; below = 0; first[i] = 1
        if not in_ep:
            lab[i] = zone(x)
            continue
        if is_wash:
            if ebbed:
                ebbed = False; peak = x
            peak = max(peak, x); below = 0; l = "washout"
        else:
            peak = max(peak, x)
            if not ebbed:
                below = below + 1 if (peak - x) >= ebb_drop else 0
                if below >= ebb_hold:
                    ebbed = True; ebb_sig[i] = 1
            l = "clearing" if ebbed else "crest"
        ep[i] = ep_id; crest[i] = peak
        if x < end_below:
            lab[i] = zone(x); in_ep = False; ebbed = False; below = 0
            continue
        lab[i] = l
    o = s.copy()
    o["state"] = lab
    o["crest"] = crest
    o["episode"] = ep
    o["clear_signal"] = ebb_sig
    o["washout_first"] = first
    return o
