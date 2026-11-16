#!/usr/bin/env python3
"""Green Face Tracker backtest. Writes research/greenface/results.json (+ charts via charts.py).

Pre-registered (fixed before looking at outcomes):
  primary metric   worst close in the next 14 days (min close[t+1..t+14] / close[t] - 1), by zone, out of sample
  in-sample        composite start .. 2023-12-31 ; out of sample 2024-01-01 .. end
  zone cut-offs    IS quantiles of the composite at the normal-CDF positions of the handoff's suggested
                   z cut-offs (1.5, 0.7, -0.8, -1.3) -> 93.3%, 75.8%, 21.2%, 9.7%. Outcome-free; nothing is fit on returns.
  headline test    OOS mean worst14 in frothy+ (frothy or green face) minus neutral; block bootstrap (30d blocks)
"""
import json, math, os, random, sys, time, datetime as dt
import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import data

D = 86400
OUT = os.path.dirname(os.path.abspath(__file__))
IS_END = int(dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc).timestamp())
Q = [0.9332, 0.7580, 0.2119, 0.0968]  # Phi(1.5), Phi(0.7), Phi(-0.8), Phi(-1.3)
FIXED = [1.5, 0.7, -0.8, -1.3]
ZN = ["green", "crowded", "neutral", "washed", "flushed"]  # "crowded" was called "frothy" before 2026-11-15
COST = 0.001  # 10 bps per side
rng = np.random.default_rng(7)


def day(s): return int(dt.datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp())
def ds(t): return time.strftime("%Y-%m-%d", time.gmtime(t))


def rmean(a, n, minp=None):
    minp = minp or n
    out = np.full(len(a), np.nan)
    for i in range(len(a)):
        w = a[max(0, i - n + 1):i + 1]
        w = w[~np.isnan(w)]
        if len(w) >= minp: out[i] = w.mean()
    return out


def rz(a, win=365, minp=90):
    out = np.full(len(a), np.nan)
    for i in range(len(a)):
        if np.isnan(a[i]): continue
        w = a[max(0, i - win + 1):i + 1]
        w = w[~np.isnan(w)]
        if len(w) < minp: continue
        sd = w.std(ddof=1)
        if sd > 0: out[i] = (a[i] - w.mean()) / sd
    return out


def rpct(a, win=365, minp=90):
    out = np.full(len(a), np.nan)
    for i in range(len(a)):
        if np.isnan(a[i]): continue
        w = a[max(0, i - win + 1):i + 1]
        w = w[~np.isnan(w)]
        if len(w) < minp: continue
        out[i] = (w < a[i]).mean() * 100 + (w == a[i]).mean() * 50
    return out


def ema(a, n):
    out, k, e = np.full(len(a), np.nan), 2 / (n + 1), None
    for i, x in enumerate(a):
        e = x if e is None else e + k * (x - e)
        if i >= n - 1: out[i] = e
    return out


def build(sym):
    fr = data.daily_frame(sym)
    # fill month-to-date funding (monthly dumps lag) from the premium-index reconstruction
    f8 = data.funding(sym)
    pi = data.premium_1h(sym)
    last = max(f8)
    t = last + 8 * 3600
    rec = {}
    while t in pi or t - 3600 in pi:
        hs = [pi.get(t - 3600 * k) for k in range(1, 9)]
        if None in hs: break
        P = sum(h[1] for h in hs) / 8
        rec.setdefault(t // D * D, []).append(min(0.003, max(-0.003, P + max(-0.0005, min(0.0005, 0.0001 - P)))))
        t += 8 * 3600
    for r in fr:
        if r["fund"] is None and len(rec.get(r["t"], [])) == 3:
            r["fund"] = sum(rec[r["t"]]); r["fund_rec"] = True
    t = np.array([r["t"] for r in fr])
    c = np.array([r["close"] for r in fr], float)
    g = lambda k: np.array([np.nan if r[k] is None else r[k] for r in fr], float)
    fund, oi, acct, top = g("fund"), g("oi"), g("acct"), g("top")
    inp = {
        "funding": rmean(fund, 7, 5),
        "oi": np.log(oi / np.roll(oi, 30)),
        "ls": rmean(acct, 7, 5),
        "gap": rmean(np.log(acct) - np.log(top), 7, 5),
    }
    inp["oi"][:30] = np.nan
    z = {k: rz(v) for k, v in inp.items()}
    stretch = (rpct(c / ema(c, 21) - 1) + rpct(c / np.roll(c, 7) - 1) +
               rpct(np.array([(c[i] - c[max(0, i - 29):i + 1].min()) / max(1e-9, np.ptp(c[max(0, i - 29):i + 1])) for i in range(len(c))]))) / 3
    stretch[:7] = np.nan
    n = len(c)
    fwd = {h: np.array([c[i + h] / c[i] - 1 if i + h < n else np.nan for i in range(n)]) for h in (1, 3, 7, 14, 30)}
    w14 = np.array([c[i + 1:i + 15].min() / c[i] - 1 if i + 14 < n else np.nan for i in range(n)])
    return {"t": t, "c": c, "z": z, "stretch": stretch, "fwd": fwd, "w14": w14, "inp": inp,
            "fund_rec_days": sum(1 for r in fr if r.get("fund_rec"))}


def composite(z, keys, need=None):
    m = np.vstack([z[k] for k in keys])
    cnt = (~np.isnan(m)).sum(0)
    need = need or max(1, len(keys) - 1) if len(keys) > 1 else 1
    with np.errstate(invalid="ignore"):
        out = np.nanmean(np.where(np.isnan(m), np.nan, m), 0)
    out[cnt < need] = np.nan
    return out


def cuts_from(sig, mask):
    s = sig[mask & ~np.isnan(sig)]
    return [float(np.quantile(s, q)) for q in Q]


def label(sig, cuts):
    lab = np.full(len(sig), -1)
    for i, x in enumerate(sig):
        if np.isnan(x): continue
        lab[i] = next((k for k, cut in enumerate(cuts) if x >= cut), 4)
    return lab


def zstats(B, lab, mask):
    out = {}
    for k in range(5):
        m = mask & (lab == k) & ~np.isnan(B["w14"])
        if m.sum() == 0:
            out[ZN[k]] = {"n": 0}; continue
        out[ZN[k]] = {"n": int(m.sum()),
                      **{f"r{h}": float(np.nanmean(B["fwd"][h][m])) for h in (1, 3, 7, 14, 30)},
                      "w14": float(B["w14"][m].mean()),
                      "p10": float((B["w14"][m] <= -0.10).mean())}
    m = mask & (lab >= 0) & ~np.isnan(B["w14"])
    out["all"] = {"n": int(m.sum()), **{f"r{h}": float(np.nanmean(B["fwd"][h][m])) for h in (1, 3, 7, 14, 30)},
                  "w14": float(B["w14"][m].mean()), "p10": float((B["w14"][m] <= -0.10).mean())}
    return out


def diff_test(B, lab, mask, hot=(0, 1), cold=(2,), block=30, nb=4000, better=False):
    """mean w14 in hot zones minus cold zones; circular block bootstrap CI and one-sided p (H1: hot worse)."""
    idx = np.where(mask & (lab >= 0) & ~np.isnan(B["w14"]))[0]
    y, l = B["w14"][idx], lab[idx]
    H, C = np.isin(l, hot), np.isin(l, cold)
    if H.sum() < 5 or C.sum() < 5: return None
    obs = y[H].mean() - y[C].mean()
    n = len(idx); nblk = math.ceil(n / block); bs = []
    for _ in range(nb):
        st = rng.integers(0, n, nblk)
        ii = ((st[:, None] + np.arange(block)[None, :]) % n).ravel()[:n]
        yy, ll = y[ii], l[ii]
        h, cc = np.isin(ll, hot), np.isin(ll, cold)
        if h.sum() and cc.sum(): bs.append(yy[h].mean() - yy[cc].mean())
    bs = np.array(bs)
    # p: centre the bootstrap distribution at 0 (null of no difference)
    p = float(((bs - bs.mean()) >= obs).mean()) if better else float(((bs - bs.mean()) <= obs).mean())
    # non-overlapping: every 14th day, each offset; Welch t
    ps = []
    for off in range(14):
        sub = np.arange(off, n, 14)
        a, b = y[sub][H[sub]], y[sub][C[sub]]
        if len(a) >= 3 and len(b) >= 3:
            tstat = (a.mean() - b.mean()) / math.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b))
            ps.append(0.5 * math.erfc((tstat if better else -tstat) / math.sqrt(2)))  # one-sided, normal approx
    return {"diff": float(obs), "ci": [float(np.quantile(bs, .05)), float(np.quantile(bs, .95))], "p_block": p,
            "p_nonoverlap_median": float(np.median(ps)) if ps else None, "nonoverlap_n_hot": int(H[::14].sum()),
            "n_hot": int(H.sum()), "n_cold": int(C.sum())}


def evaluate(B, sig, name, shift=0.0):
    valid = ~np.isnan(sig)
    ism = valid & (B["t"] < IS_END)
    oos = valid & (B["t"] >= IS_END)
    cuts = [x + shift for x in cuts_from(sig, ism)]
    lab = label(sig, cuts)
    return {"name": name, "cuts": cuts, "lab": lab,
            "IS": zstats(B, lab, ism), "OOS": zstats(B, lab, oos),
            "test_IS": diff_test(B, lab, ism), "test_OOS": diff_test(B, lab, oos),
            "test_OOS_green": diff_test(B, lab, oos, hot=(0,)),
            "test_OOS_low": diff_test(B, lab, oos, hot=(3, 4), better=True),
            "test_IS_low": diff_test(B, lab, ism, hot=(3, 4), better=True),
            "start": ds(B["t"][valid][0])}


def periods(B, lab):
    P = {"2020-21": ("2020-01-01", "2022-01-01"), "2022": ("2022-01-01", "2023-01-01"),
         "2023-24": ("2023-01-01", "2025-01-01"), "2025-26": ("2025-01-01", "2027-01-01")}
    out = {}
    for k, (a, b) in P.items():
        m = (B["t"] >= day(a)) & (B["t"] < day(b))
        out[k] = {"zones": zstats(B, lab, m), "test": diff_test(B, lab, m, nb=2000)}
    return out


# ---------------------------------------------------------------- CZ
def snowflake(i): return (int(i) >> 22) + 1288834974657


def load_posts():
    import csv
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "cz_posts.csv")
    rows = list(csv.DictReader(open(p)))
    for r in rows:
        r["t"] = day(r["date"])
        m = __import__("re").search(r"x\.com/[^/]+/status/(\d+)", r["source"])
        r["ts"] = snowflake(m.group(1)) // 1000 if m else None
    return rows


def decluster(posts, gap=21):
    out, last = [], None
    for p in sorted(posts, key=lambda r: r["t"]):
        if last is None or p["t"] - last >= gap * D:
            out.append(p); last = p["t"]
    return out


def cz_tests(B, lab, posts):
    ti = {t: i for i, t in enumerate(B["t"])}
    lo, hi = day("2024-12-01"), B["t"][-1]
    post_days = {p["t"] for p in posts}
    near = {p["t"] + k * D for p in posts for k in range(-3, 4)}
    win = np.array([(lo <= t <= hi) for t in B["t"]])
    res = {}

    def zone_at(p):  # reading visible when the post went out: prior day's close
        i = ti[p["t"]] - 1
        return int(lab[i])

    def outc(i):
        return {"r1": B["fwd"][1][i], "r7": B["fwd"][7][i], "r30": B["fwd"][30][i], "w14": B["w14"][i]}

    for kind in ("hype", "dip", "all"):
        for dec in (False, True):
            ps = [p for p in posts if kind == "all" or p["kind"] == kind]
            if dec: ps = decluster(ps)
            key = f"{kind}{'_1per21' if dec else ''}"
            hot = [p for p in ps if zone_at(p) in (0, 1)]
            # B1: frothy+ posts vs frothy+ no-post days (same-size bootstrap)
            pool = [i for i, t in enumerate(B["t"]) if win[i] and lab[i - 1] in (0, 1) and t not in near and not np.isnan(B["w14"][i])]
            hi_ = [ti[p["t"]] for p in hot if not np.isnan(B["w14"][ti[p["t"]]])]
            b1 = None
            if len(hi_) >= 2 and len(pool) >= 10:
                obs = float(np.mean(B["w14"][hi_]))
                draws = np.array([B["w14"][rng.choice(pool, len(hi_), replace=False)].mean() for _ in range(10000)])
                obs7 = float(np.mean(B["fwd"][7][hi_]))
                d7 = np.array([np.nanmean(B["fwd"][7][rng.choice(pool, len(hi_), replace=False)]) for _ in range(10000)])
                b1 = {"n_posts_hot": len(hi_), "n_pool": len(pool), "w14_posts": obs, "w14_pool": float(B["w14"][pool].mean()),
                      "p_w14_worse": float((draws <= obs).mean()), "r7_posts": obs7, "r7_pool": float(np.nanmean(B["fwd"][7][pool])),
                      "p_r7_lower": float((d7 <= obs7).mean())}
            # B4: placebo, every post's date shuffled within its own zone (coverage window), 10,000x
            byz = {}
            for i, t in enumerate(B["t"]):
                if win[i] and i > 0 and lab[i - 1] >= 0 and not np.isnan(B["w14"][i]) and t not in post_days:
                    byz.setdefault(int(lab[i - 1]), []).append(i)
            ii = [ti[p["t"]] for p in ps if not np.isnan(B["w14"][ti[p["t"]]]) and zone_at(p) in byz]
            zz = [zone_at(B_p) for B_p in ps if not np.isnan(B["w14"][ti[B_p["t"]]]) and zone_at(B_p) in byz]
            obs_w, obs_7 = float(np.mean(B["w14"][ii])), float(np.mean(B["fwd"][7][ii]))
            sim_w, sim_7 = np.empty(10000), np.empty(10000)
            for s in range(10000):
                pick = [byz[z][rng.integers(len(byz[z]))] for z in zz]
                sim_w[s] = B["w14"][pick].mean(); sim_7[s] = B["fwd"][7][pick].mean()
            res[key] = {"n": len(ps), "n_scored": len(ii), "n_hot": len(hot), "B1": b1,
                        "placebo": {"w14_obs": obs_w, "w14_null": float(sim_w.mean()), "p_w14_worse": float((sim_w <= obs_w).mean()),
                                    "r7_obs": obs_7, "r7_null": float(sim_7.mean()), "p_r7_lower": float((sim_7 <= obs_7).mean())},
                        "posts": [{"date": p["date"], "label": p["label"], "zone": ZN[zone_at(p)] if zone_at(p) >= 0 else None,
                                   **{k: (None if np.isnan(v) else float(v)) for k, v in outc(ti[p["t"]]).items()}} for p in ps]}
    return res


def hourly_study(sym, posts, B=None, lab=None):
    k = data.klines(sym, "1h")
    ts = np.array(sorted(k)); hi = np.array([k[t][1] for t in ts]); cl = np.array([k[t][3] for t in ts])
    pos = {t: i for i, t in enumerate(ts)}
    rows = []
    for p in posts:
        exact = p["ts"] is not None
        anchors = {"exact": p["ts"] // 3600 * 3600} if exact else {"midday": p["t"] + 12 * 3600, "dayend": p["t"] + D}
        for an, a in anchors.items():
            i = pos.get(a)
            if i is None or i + 72 >= len(ts) or i < 72: continue
            base = cl[i - 1]  # close of the hour before the anchor
            w = hi[i - 72:i + 72]
            rows.append({"date": p["date"], "kind": p["kind"], "anchor": an,
                         "r4": cl[i + 3] / base - 1, "r24": cl[i + 23] / base - 1, "r72": cl[i + 71] / base - 1,
                         "high_after": bool(int(np.argmax(w)) >= 72)})
    # baseline: every hour, how often the +/-72h high is in the second half, and avg r4/r24/r72
    i = np.arange(72, len(ts) - 72, 6)
    base = {"high_after": float(np.mean([np.argmax(hi[j - 72:j + 72]) >= 72 for j in i])),
            "r4": float(np.mean(cl[i + 3] / cl[i - 1] - 1)), "r24": float(np.mean(cl[i + 23] / cl[i - 1] - 1)),
            "r72": float(np.mean(cl[i + 71] / cl[i - 1] - 1))}
    summ = {}
    for kind in ("hype", "dip"):
        for an in ("exact", "midday", "dayend"):
            r = [x for x in rows if x["kind"] == kind and x["anchor"] in (an, "exact")] if an != "exact" else [x for x in rows if x["kind"] == kind and x["anchor"] == "exact"]
            if not r: continue
            summ[f"{kind}_{an}"] = {"n": len(r), "r4": float(np.mean([x["r4"] for x in r])), "r24": float(np.mean([x["r24"] for x in r])),
                                    "r72": float(np.mean([x["r72"] for x in r])), "high_after": float(np.mean([x["high_after"] for x in r])),
                                    "n_high_after": int(sum(x["high_after"] for x in r))}
    # placebo: hype posts' noon anchors replaced by random noon anchors on days with the same prior-day zone
    # and the same sign of the prior-72h return, 10,000x. Tests whether "the 72h high came after" is just trend.
    plc = {}
    if B is not None:
        ti = {t: i for i, t in enumerate(B["t"])}
        lo = day("2024-12-01")
        def feat(dday, off):
            a = pos.get(dday + off * 3600)
            if a is None or a < 72 or a + 72 >= len(ts): return None
            j = ti.get(dday)
            if j is None or j < 1 or lab[j - 1] < 0: return None
            return (int(lab[j - 1]), cl[a - 1] > cl[a - 73]), bool(int(np.argmax(hi[a - 72:a + 72])) >= 72)
        for off in (12, 24):
            pools = {}
            for t in B["t"]:
                if t < lo: continue
                f = feat(int(t), off)
                if f: pools.setdefault(f[0], []).append(f[1])
            for kind in ("hype", "dip"):
                obs, keys = 0, []
                for p in posts:
                    if p["kind"] != kind: continue
                    f = feat(p["t"], off)
                    if f and f[0] in pools: obs += f[1]; keys.append(f[0])
                sims = np.array([sum(pools[k][rng.integers(len(pools[k]))] for k in keys) for _ in range(10000)])
                plc[f"{kind}_{'noon' if off == 12 else 'dayend'}"] = {"n": len(keys), "obs_high_after": int(obs), "null_mean": float(sims.mean()),
                             "p_ge": float((sims >= obs).mean()), "p_le": float((sims <= obs).mean()),
                             "prior72_up": int(sum(k[1] for k in keys))}
    return {"rows": rows, "summary": summ, "baseline": base, "placebo": plc}


def rule(B, lab, posts, N, start, armed_mode="cz"):
    """Step aside N days from the close of the first armed day. Returns equity stats vs holding."""
    ti = {t: i for i, t in enumerate(B["t"])}
    i0 = ti[day(start)]
    armed = np.zeros(len(B["t"]), bool)
    if armed_mode == "cz":
        for p in posts:
            if p["kind"] != "hype" or p["t"] not in ti: continue
            j = ti[p["t"]]
            for k in range(3):  # cz.on lasts 72h: post day + 2
                if j + k < len(armed) and lab[j + k] in (0, 1): armed[j + k] = True
    else:
        armed = lab == 0
    inm, out_until, eq, bh, switches = [], -1, [1.0], [1.0], 0
    pos = 1
    for i in range(i0, len(B["t"]) - 1):
        r = B["c"][i + 1] / B["c"][i] - 1
        if pos == 1 and armed[i]:
            pos, out_until = 0, i + N; switches += 1; eq[-1] *= 1 - COST
        elif pos == 0 and i >= out_until and not armed[i]:
            pos = 1; switches += 1; eq[-1] *= 1 - COST
        elif pos == 0 and armed[i]:
            out_until = max(out_until, i + N)
        eq.append(eq[-1] * (1 + r * pos)); bh.append(bh[-1] * (1 + r)); inm.append(pos)
    def mdd(e):
        e = np.array(e); return float((e / np.maximum.accumulate(e) - 1).min())
    return {"N": N, "mode": armed_mode, "start": start, "ret": eq[-1] - 1, "ret_hold": bh[-1] - 1, "mdd": mdd(eq), "mdd_hold": mdd(bh),
            "time_in": float(np.mean(inm)), "switches": switches, "eq": eq, "bh": bh,
            "t": [int(x) for x in B["t"][i0:]]}


def fund_check(sym="BTCUSDT"):
    """Correlation of the premium-index funding rebuild with actual 8h rates, 2021 on."""
    f, pi = data.funding(sym), data.premium_1h(sym)
    a, b = [], []
    for t, v in f.items():
        if t < day("2021-01-01"): continue
        hs = [pi.get(t - 3600 * k) for k in range(1, 9)]
        if None in hs: continue
        P = sum(h[1] for h in hs) / 8
        a.append(v); b.append(min(0.003, max(-0.003, P + max(-0.0005, min(0.0005, 0.0001 - P)))))
    return {"n": len(a), "corr": float(np.corrcoef(a, b)[0, 1]), "mae": float(np.mean(np.abs(np.array(a) - b)))}


def write_summary(R):
    """The frozen research numbers the publisher copies into greenface.json's method block."""
    rnd = lambda x, k=4: None if x is None else round(float(x), k)
    def zt(z): return {k: {"n": v["n"], "w14": rnd(v.get("w14")), "p10": rnd(v.get("p10")), "r7": rnd(v.get("r7")), "r30": rnd(v.get("r30"))}
                       for k, v in z.items()}
    def tt(t): return None if not t else {"diff": rnd(t["diff"]), "ci": [rnd(t["ci"][0]), rnd(t["ci"][1])], "p": rnd(t["p_block"], 3),
                                          "pNonOverlap": rnd(t["p_nonoverlap_median"], 3), "nHot": t["n_hot"], "nCold": t["n_cold"]}
    b, e = R["BTCUSDT"]["composite"], R["ETHUSDT"]["composite"]
    S = {"generated": R["generated"], "isStart": b["start"], "isEnd": "2023-12-31", "oosStart": "2024-01-01",
         "oosEnd": ds(R["BTCUSDT"]["series"]["t"][-1]), "cuts": [round(c, 2) for c in b["cuts"]],
         "zonesOOS": zt(b["OOS"]), "zonesIS": zt(b["IS"]), "testOOS": tt(b["test_OOS"]), "testIS": tt(b["test_IS"]),
         "lowOOS": tt(b["test_OOS_low"]), "lowIS": tt(b["test_IS_low"]),
         "eth": {"start": e["start"], "testOOS": tt(e["test_OOS"]), "testIS": tt(e["test_IS"]), "zonesOOS": zt(e["OOS"])},
         "alone": {k.split(":")[1]: tt(R["BTCUSDT"][k]["test_OOS"]) for k in R["BTCUSDT"] if k.startswith("alone:")},
         "cz": {"n": R["BTCUSDT"]["cz"]["all"]["n"], "hypeInCrowded": R["BTCUSDT"]["cz"]["hype"]["B1"]["n_posts_hot"],
                "hypeW14": rnd(R["BTCUSDT"]["cz"]["hype"]["B1"]["w14_posts"]), "poolW14": rnd(R["BTCUSDT"]["cz"]["hype"]["B1"]["w14_pool"]),
                "p": rnd(R["BTCUSDT"]["cz"]["hype"]["B1"]["p_w14_worse"], 3), "placeboP": rnd(R["BTCUSDT"]["cz"]["hype"]["placebo"]["p_w14_worse"], 3)},
         "fundRebuild": R["fundCheck"]}
    json.dump(S, open(os.path.join(OUT, "summary.json"), "w"), indent=1)


def main():
    os.makedirs(OUT, exist_ok=True)
    R = {"generated": int(time.time()), "fundCheck": fund_check()}
    for sym in ("BTCUSDT", "ETHUSDT"):
        B = build(sym)
        z = B["z"]
        keys = ["funding", "oi", "ls", "gap"]
        comp = composite(z, keys, need=3)
        S = {"composite": evaluate(B, comp, "composite")}
        S["shift+0.25"] = evaluate(B, comp, "composite +0.25", 0.25)
        S["shift-0.25"] = evaluate(B, comp, "composite -0.25", -0.25)
        # fixed handoff cut-offs, for reference
        lab_f = label(comp, FIXED)
        valid = ~np.isnan(comp)
        S["fixed_cuts"] = {"OOS": zstats(B, lab_f, valid & (B["t"] >= IS_END)), "IS": zstats(B, lab_f, valid & (B["t"] < IS_END)),
                           "test_OOS": diff_test(B, lab_f, valid & (B["t"] >= IS_END))}
        if sym == "BTCUSDT":
            for k in keys:
                S[f"alone:{k}"] = evaluate(B, z[k], f"{k} alone")
                S[f"drop:{k}"] = evaluate(B, composite(z, [x for x in keys if x != k], need=2), f"drop {k}")
            S["alone:stretch"] = evaluate(B, B["stretch"], "price stretch alone")
            S["periods"] = periods(B, S["composite"]["lab"])
            posts = load_posts()
            S["cz"] = cz_tests(B, S["composite"]["lab"], posts)
            S["hourly"] = hourly_study(sym, posts, B, S["composite"]["lab"])
            S["rules"] = [rule(B, S["composite"]["lab"], posts, N, "2024-12-01") for N in (7, 14, 30)]
            S["rules"] += [rule(B, S["composite"]["lab"], posts, N, "2024-01-01", "green") for N in (7, 14, 30)]
            # series for charts / publisher check
            S["series"] = {"t": [int(x) for x in B["t"]], "c": [float(x) for x in B["c"]],
                           "comp": [None if np.isnan(x) else float(x) for x in comp],
                           "z": {k: [None if np.isnan(x) else float(x) for x in z[k]] for k in keys}}
        S["fund_rec_days"] = B["fund_rec_days"]
        for k, v in S.items():
            if isinstance(v, dict) and "lab" in v: v["lab"] = None
        R[sym] = S
    write_summary(R)
    json.dump(R, open(os.path.join(OUT, "results.json"), "w"), default=lambda o: float(o) if isinstance(o, np.floating) else int(o) if isinstance(o, np.integer) else str(o))
    print("ok")


if __name__ == "__main__":
    main()
