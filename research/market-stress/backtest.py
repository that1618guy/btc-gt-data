"""Market Stress Index backtest.  python3 -I research/backtest.py <data_dir> <out_dir>

Trailing data only. Entries at the session close (SPX) or the BTC daily close of the same date
(00:00 UTC, three hours after the US close). Sample: 2006-03-06 (first VVIX print) to 2026-11-19;
2026-11-20 is the live, provisional session and is never used as an outcome.
"""
import sys, json
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import engine as we  # noqa: E402

DATA, OUT = Path(sys.argv[1]), Path(sys.argv[2])
OUT.mkdir(parents=True, exist_ok=True)
START, LIVE = pd.Timestamp("2006-03-06"), pd.Timestamp("2026-11-20")

P = pd.read_csv(DATA / "panel.csv", parse_dates=["date"], index_col="date")
P["move"] = pd.read_csv(DATA / "move.csv", parse_dates=["date"], index_col="date")["move"].reindex(P.index).ffill(limit=2)
P["dxy"] = pd.read_csv(DATA / "dxy.csv", parse_dates=["date"], index_col="date")["dxy"].reindex(P.index).ffill(limit=3)
P = P[P.index >= "2003-01-01"]
btc = pd.read_csv(DATA / "btc.csv", parse_dates=["date"], index_col="date")["btc"]
spx = P["spx"]
FUND = we.funding(P)


# ------------------------------------------------------------------ outcome helpers
def f_spx(d, n):
    i = spx.index.get_loc(d); j = i + n
    return float(spx.iloc[j] / spx.iloc[i] - 1) if j < len(spx) and spx.index[j] < LIVE else None


def dd_spx(d, n=126):
    i = spx.index.get_loc(d); w = spx.iloc[i + 1:i + n + 1]; w = w[w.index < LIVE]
    return float(w.min() / spx.iloc[i] - 1) if len(w) >= 21 else None


def btc_px(d):
    return None if d < btc.index[0] else float(btc.asof(d))


def f_btc(d, k):
    a = btc_px(d); t = d + pd.Timedelta(days=k)
    return None if a is None or t >= LIVE else float(btc.asof(t) / a - 1)


def dd_btc(d, k=91):
    a = btc_px(d)
    if a is None:
        return None
    w = btc[(btc.index > d) & (btc.index <= d + pd.Timedelta(days=k)) & (btc.index < LIVE)]
    return float(w.min() / a - 1) if len(w) >= 20 else None


def outcome(d):
    return {"date": d.strftime("%Y-%m-%d"), "spx": round(float(spx.loc[d]), 2),
            "spx_1m": f_spx(d, 21), "spx_3m": f_spx(d, 63), "spx_6m": f_spx(d, 126), "spx_12m": f_spx(d, 252),
            "spx_dd6m": dd_spx(d), "btc": btc_px(d), "btc_1m": f_btc(d, 30), "btc_3m": f_btc(d, 91),
            "btc_6m": f_btc(d, 182), "btc_12m": f_btc(d, 365), "btc_dd3m": dd_btc(d)}


def stats(rows, keys=("spx_1m", "spx_3m", "spx_6m", "spx_12m", "spx_dd6m", "btc_1m", "btc_3m", "btc_6m", "btc_12m", "btc_dd3m")):
    df = pd.DataFrame(rows)
    o = {"n": int(len(df))}
    for k in keys:
        v = pd.to_numeric(df.get(k), errors="coerce").dropna() if k in df else pd.Series(dtype=float)
        o[k] = None if v.empty else {"n": int(len(v)), "median": float(v.median()), "hit": float((v > 0).mean()),
                                     "worst": float(v.min()), "best": float(v.max())}
    return o


def fund_at(d):
    r = FUND.loc[d]
    g = lambda c: None if c not in r or pd.isna(r[c]) else float(r[c])
    return {"move": None if pd.isna(P.loc[d, "move"]) else float(P.loc[d, "move"]), "move_pct": g("move_pct"),
            "dxy20": g("dxy20"), "rates_stress": g("rates_stress"), "dollar_stress": g("dollar_stress"),
            "funding_stress": g("funding_stress")}


# ------------------------------------------------------------------ episode table
def episodes(S):
    S = S[S.index >= START]
    out = []
    for e, g in S[S.episode > 0].groupby("episode"):
        fw = g.index[g.washout_first == 1]
        if not len(fw):
            continue
        dw = fw[0]
        clears = g.index[g.clear_signal == 1]
        ep = {"first_washout": dw.strftime("%Y-%m-%d"), "crest": round(float(g.score.max()), 1),
              "crest_date": g.score.idxmax().strftime("%Y-%m-%d"), "washout_days": int((g.state == "washout").sum()),
              "reflushes": int(max(0, len(clears) - 1)), "end": g.index[-1].strftime("%Y-%m-%d"),
              "funding_at_washout": fund_at(dw), "washout": outcome(dw),
              "clearing": outcome(clears[0]) if len(clears) else None}
        # wait-for-funding entry: first session from the washout on where funding is calm
        fc = FUND.loc[dw:, "funding_stress"]
        fc = fc[(fc.index < LIVE) & (fc.index <= g.index[-1])]
        calm = fc[fc == 0]
        ep["funding_clear"] = outcome(calm.index[0]) if len(calm) else None
        ep["funding_clear_wait_sessions"] = int(spx.index.get_loc(calm.index[0]) - spx.index.get_loc(dw)) if len(calm) else None
        out.append(ep)
    return out


def cross_events(S, hi=65, reset=40):
    ev, armed = [], True
    for d, x in S.loc[START:, "score"].dropna().items():
        if armed and x >= hi:
            ev.append(d); armed = False
        elif x < reset:
            armed = True
    return ev


R = {"sample": [str(START.date()), "2026-11-19"], "live": str(LIVE.date())}

# ------------------------------------------------------------------ 1. three scorers
for m in ("replica", "washout4", "washout"):
    S = we.states(we.score(P, m))
    eps = episodes(S)
    hist = [e for e in eps if pd.Timestamp(e["first_washout"]) < LIVE]
    t = S.loc[LIVE]
    R[m] = {"today": {"score": round(float(t.score), 1), "seals": int(t.seals), "seals_max": int(t.seals_max), "state": t.state},
            "episodes": eps,
            "entry_washout": stats([e["washout"] for e in hist]),
            "entry_clearing": stats([e["clearing"] for e in hist if e["clearing"]]),
            "days_ge80": int((S.loc[START:LIVE].score >= 80).sum())}
    if m == "washout":
        S_final = S

# ------------------------------------------------------------------ 2. funding split, every stress event (first cross of 65)
S = S_final.join(FUND)
ev = [d for d in cross_events(S) if d < LIVE]
rows = []
for d in ev:
    r = outcome(d); r.update(fund_at(d)); r["score"] = round(float(S.loc[d, "score"]), 1); rows.append(r)
T = pd.DataFrame(rows)
R["stress_events"] = rows


def split(mask_name, mask):
    a, b = T[mask], T[~mask]
    return {mask_name: stats(a.to_dict("records")), "calm": stats(b.to_dict("records"))}


R["funding_split"] = {
    "either": split("stressed", T.funding_stress == 1),
    "rates_only_rule": split("stressed", T.rates_stress == 1),
    "dollar_only_rule": split("stressed", T.dollar_stress == 1),
    "both": split("stressed", (T.rates_stress == 1) & (T.dollar_stress == 1)),
}
rob = []
for dl in (1.0, 1.5, 2.0, 2.5, 3.0):
    for rl in (0.80, 0.85, 0.90, 0.95):
        m = (T.dxy20 >= dl) | (T.move_pct >= rl)
        a, b = T[m], T[~m]
        rob.append({"dollar_line": dl, "rates_line": rl, "n_stressed": int(m.sum()), "n_calm": int((~m).sum()),
                    "spx3m_stressed": float(a.spx_3m.median()), "spx3m_calm": float(b.spx_3m.median()),
                    "spx3m_hit_calm": float((b.spx_3m > 0).mean()), "spx3m_hit_stressed": float((a.spx_3m > 0).mean()),
                    "btc3m_stressed": float(a.btc_3m.dropna().median()) if a.btc_3m.notna().any() else None,
                    "btc3m_calm": float(b.btc_3m.dropna().median()) if b.btc_3m.notna().any() else None})
R["funding_robustness"] = rob

# ------------------------------------------------------------------ 3. entry rules on the washout episodes
hist = [e for e in R["washout"]["episodes"] if pd.Timestamp(e["first_washout"]) < LIVE]
R["entry_rules"] = {
    "buy_first_washout": stats([e["washout"] for e in hist]),
    "buy_clearing": stats([e["clearing"] for e in hist if e["clearing"]]),
    "buy_when_funding_calm": stats([e["funding_clear"] for e in hist if e["funding_clear"]]),
    "funding_clear_waits": {e["first_washout"]: e["funding_clear_wait_sessions"] for e in hist},
}

# ------------------------------------------------------------------ 4. daily states
D = S_final[(S_final.index >= START) & (S_final.index < LIVE)].copy()
D["f63"] = [f_spx(d, 63) for d in D.index]
D["f252"] = [f_spx(d, 252) for d in D.index]
D["b91"] = [f_btc(d, 91) for d in D.index]
st = {}
for name in ["calm", "uneasy", "stressed", "panic", "washout", "crest", "clearing"]:
    g = D[D.state == name]
    f = g.f63.dropna(); y = g.f252.dropna(); b = g.b91.dropna()
    st[name] = {"days": int(len(g)), "share": float(len(g) / len(D)),
                "spx_3m_median": float(f.median()) if len(f) else None, "spx_3m_hit": float((f > 0).mean()) if len(f) else None,
                "spx_12m_median": float(y.median()) if len(y) else None, "spx_12m_hit": float((y > 0).mean()) if len(y) else None,
                "btc_3m_median": float(b.median()) if len(b) else None, "btc_3m_hit": float((b > 0).mean()) if len(b) else None,
                "btc_days": int(len(b))}
st["all"] = {"days": int(len(D)), "spx_3m_median": float(D.f63.median()), "spx_3m_hit": float((D.f63.dropna() > 0).mean()),
             "spx_12m_median": float(D.f252.median()), "spx_12m_hit": float((D.f252.dropna() > 0).mean()),
             "btc_3m_median": float(D.b91.dropna().median()), "btc_3m_hit": float((D.b91.dropna() > 0).mean())}
R["daily_states"] = st
R["today_percentile"] = float((D.score < S_final.loc[LIVE, "score"]).mean())

S_final.join(FUND).to_csv(OUT / "washout_daily.csv", float_format="%.4f")
(OUT / "results.json").write_text(json.dumps(R, indent=1))
print("wrote", OUT / "results.json")
