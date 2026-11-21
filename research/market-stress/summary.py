"""Compact research numbers the page shows. Reads research/out/results.json, writes research/market-stress/summary.json."""
import json, sys
import pandas as pd
R = json.load(open(sys.argv[1]))
P = pd.read_csv(sys.argv[2], parse_dates=["date"], index_col="date")
def pick(s, keys=("spx_3m", "spx_12m", "spx_dd6m", "btc_3m", "btc_dd3m")):
    o = {"n": s["n"]}
    for k in keys:
        v = s.get(k)
        o[k] = None if not v else {"median": round(v["median"], 6), "hit": round(v["hit"], 4), "n": v["n"], "worst": round(v["worst"], 6)}
    return o
fs = R["funding_split"]
c1 = P.cor1m.dropna()
out = {
  "sample": {"from": R["sample"][0], "to": R["sample"][1], "stressEvents": fs["either"]["stressed"]["n"] + fs["either"]["calm"]["n"],
             "washouts": len([e for e in R["washout"]["episodes"] if e["first_washout"] < R["live"]])},
  "split": {"calm": pick(fs["either"]["calm"]), "stressed": pick(fs["either"]["stressed"]),
            "dollarStressed": pick(fs["dollar_only_rule"]["stressed"]), "dollarCalm": pick(fs["dollar_only_rule"]["calm"])},
  "entries": {"firstWashout": pick(R["entry_rules"]["buy_first_washout"]), "fundingCalm": pick(R["entry_rules"]["buy_when_funding_calm"]),
              "clearing": pick(R["entry_rules"]["buy_clearing"])},
  "cor1m": {"lastAbove60": c1[c1 > 60].index[-1].strftime("%Y-%m-%d"), "max2025": round(float(c1.loc["2025"].max()), 2),
            "median2010": round(float(c1.loc["2010"].median()), 1), "median2026": round(float(c1.loc["2026"].median()), 1)},
  "note": "Stress events = every first cross of 65 after a reset under 40, 2006-03-06 to 2026-11-19. Funding stressed = MOVE in the top 10% of its last year or DXY up 2%+ in 20 sessions. Medians of forward returns; BTC from 2014-09."
}
out["events"] = [{"date": e["date"], "score": e["score"], "funding": "stressed" if e["funding_stress"] == 1 else "calm", "rates": e["rates_stress"] == 1, "dollar": e["dollar_stress"] == 1, "spx_3m": None if e["spx_3m"] is None else round(e["spx_3m"], 4), "btc_3m": None if e["btc_3m"] is None else round(e["btc_3m"], 4)} for e in R["stress_events"]]
# zones: every session since 2006 by state, S&P forward returns (3M = 63 sessions, 12M = 252), BTC 91 days
r6 = lambda x: None if x is None else round(x, 6)
r4 = lambda x: None if x is None else round(x, 4)
zones = {}
for k, v in R["daily_states"].items():
    z = {"sessions": v["days"]}
    if "share" in v: z["share"] = r4(v["share"])
    z.update({"spx_3m": r6(v["spx_3m_median"]), "spx_3m_up": r4(v["spx_3m_hit"]), "spx_12m": r6(v["spx_12m_median"]), "spx_12m_up": r4(v["spx_12m_hit"])})
    if k != "all": z.update({"btc_3m": r6(v["btc_3m_median"]), "btc_n": v["btc_days"]})
    zones[k] = z
out["zones"] = zones
# matched: the washouts where bonds and the dollar calmed, both entries on the same episodes
hist = [e for e in R["washout"]["episodes"] if e["first_washout"] < R["live"]]
both = [e for e in hist if e["funding_clear"]]
med = lambda xs: None if not xs else float(pd.Series(xs).median())
def leg(rows):
    o = {k: r6(med([x[k] for x in rows if x.get(k) is not None])) for k in ("spx_3m", "spx_12m", "spx_dd6m", "btc_3m", "btc_dd3m")}
    return o
out["matched"] = {"n": len(both), "episodes": [e["first_washout"] for e in both], "washout": leg([e["washout"] for e in both]),
                  "calm": leg([e["funding_clear"] for e in both]), "btcN": len([e for e in both if e["washout"].get("btc_3m") is not None])}
# lower close still to come: a close under the first washout close within 126 sessions
dd = [e["washout"]["spx_dd6m"] for e in hist if e["washout"].get("spx_dd6m") is not None]
out["lowerCloseAfter"] = {"n": sum(1 for x in dd if x < 0), "of": len(dd)}
json.dump(out, open(sys.argv[3], "w"), indent=1)
print(json.dumps(out, indent=1)[:1800])
