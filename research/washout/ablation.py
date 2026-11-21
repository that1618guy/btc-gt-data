"""Which inputs earn their place?  python3 -I research/ablation.py <data_dir> <out_dir>
1. Start from the four-input version (VIX, VVIX, COR1M scored against its own last year, damage) and drop one at a time.
2. Put MOVE and DXY inside the reading (five inputs) instead of using them as a separate check."""
import sys, json
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parent))
import engine as we
DATA, OUT = Path(sys.argv[1]), Path(sys.argv[2])
P = pd.read_csv(DATA / "panel.csv", parse_dates=["date"], index_col="date")
P["move"] = pd.read_csv(DATA / "move.csv", parse_dates=["date"], index_col="date")["move"].reindex(P.index).ffill(limit=2)
P["dxy"] = pd.read_csv(DATA / "dxy.csv", parse_dates=["date"], index_col="date")["dxy"].reindex(P.index).ffill(limit=3)
P = P[P.index >= "2003-01-01"]; spx = P.spx; LIVE = pd.Timestamp("2026-11-20"); START = pd.Timestamp("2006-03-06")
btc = pd.read_csv(DATA / "btc.csv", parse_dates=["date"], index_col="date")["btc"]

def f(d, n):
    i = spx.index.get_loc(d); j = i + n
    return spx.iloc[j] / spx.iloc[i] - 1 if j < len(spx) and spx.index[j] < LIVE else np.nan
def dd(d, n=126):
    i = spx.index.get_loc(d); w = spx.iloc[i + 1:i + n + 1]; w = w[w.index < LIVE]
    return w.min() / spx.iloc[i] - 1 if len(w) >= 21 else np.nan
def fb(d, k):
    a = btc.asof(d); t = d + pd.Timedelta(days=k)
    return np.nan if (pd.isna(a) or d < btc.index[0] or t >= LIVE) else btc.asof(t) / a - 1

def summarize(S, label):
    firsts = [d for d in S.index[S.washout_first == 1] if d >= START]
    h = [d for d in firsts if d < LIVE]
    r = {"variant": label, "washouts": [d.strftime("%Y-%m-%d") for d in firsts],
         "spx_3m": float(np.nanmedian([f(d, 63) for d in h])), "spx_12m": float(np.nanmedian([f(d, 252) for d in h])),
         "spx_12m_up": int(sum(1 for d in h if f(d, 252) > 0)), "n": len(h),
         "dd_median": float(np.nanmedian([dd(d) for d in h])), "dd_worst": float(np.nanmin([dd(d) for d in h])),
         "btc_3m": float(np.nanmedian([fb(d, 91) for d in h])) if any(np.isfinite(fb(d, 91)) for d in h) else None,
         "today": round(float(S.score.iloc[-1]), 1), "today_state": S.state.iloc[-1]}
    return r

rows = []
four = we.score(P, "washout4")
pill4 = ["fear", "fragility", "herding", "damage"]
for drop in [None] + pill4:
    s = four.copy()
    keep = [p for p in pill4 if p != drop]
    s["score"] = s[keep].sum(axis=1, min_count=len(keep)) * 100 / (25 * len(keep))
    seals = [c for c in ["seal_trend", "seal_vol", "seal_herd"] if not (drop == "herding" and c == "seal_herd")]
    s["seals"] = s[seals].sum(axis=1, min_count=len(seals)); s["seals_max"] = len(seals)
    rows.append(summarize(we.states(s), "four inputs" if drop is None else f"drop {drop}"))
rows.append(summarize(we.states(we.score(P, "washout")), "final: VIX, VVIX, damage"))
base = we.score(P, "washout"); F = we.funding(P)
five = base.copy()
five["score"] = base[we.PILLARS].sum(axis=1) * 0.6 + (20 * np.clip((F.move_pct - 0.8) / 0.2, 0, 1)).fillna(0) + (20 * np.clip(F.dxy20 / 4.0, 0, 1)).fillna(0)
rows.append(summarize(we.states(five), "five inputs: + MOVE, DXY"))
(OUT / "ablation.json").write_text(json.dumps(rows, indent=1))
for r in rows:
    print(f"{r['variant']:26s} n={r['n']} 3m {r['spx_3m']*100:+5.1f}% 12m {r['spx_12m']*100:+5.1f}% ({r['spx_12m_up']}/{r['n']} up) dd {r['dd_median']*100:+5.1f}% worst {r['dd_worst']*100:+5.1f}% | today {r['today']} {r['today_state']} | {' '.join(w[:7] for w in r['washouts'])}")
