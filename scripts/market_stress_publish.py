#!/usr/bin/env python3
"""Publishes the Market Stress Index (State of Blocks, Macro column) as one JSON file.

The reading, 0-100, a third each:
  fear       VIX                        scored 15 -> 40
  fragility  VVIX                       scored 90 -> 150
  damage     S&P 500 under its 100-day  scored 0% -> 10% under
Seals: S&P under its 100-day; 20-session realized vol over 25% (annualized, log returns).
Washout: both seals broken and the reading at 80+. An episode opens on the first washout and
ends when the reading is back under 40. Clearing: 20+ points under the episode's crest for 3
straight sessions. A fresh washout after clearing starts a new crest.

Funding check, outside the reading: rates stressed when MOVE is in the top 10% of its last 252
sessions; dollar stressed when DXY is up 2%+ over 20 sessions. Either one = funding stressed.

Sources: Cboe's daily history files (VIX, VVIX) and Yahoo Finance daily bars (^GSPC, ^MOVE,
DX-Y.NYB, BTC-USD). The research numbers shown on the page come from
research/market-stress/summary.json, written by the backtest in research/market-stress/.

Runs in the btc-gt-data repo through .github/workflows/market-stress.yml.

By hand:      python3 scripts/market_stress_publish.py docs/market-stress.json
Offline test: python3 scripts/market_stress_publish.py out.json --local <dir with panel.csv move.csv dxy.csv btc.csv>
Standard library only.
"""
import csv, json, math, os, sys, tempfile, time, urllib.request
import datetime as dt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
D = 86400
CBOE = "https://cdn.cboe.com/api/global/us_indices/daily_prices/{}_History.csv"
YAHOO = "https://query{}.finance.yahoo.com/v8/finance/chart/{}?period1={}&period2={}&interval=1d&includePrePost=false"
UA = {"user-agent": "Mozilla/5.0 (sob-washout; +https://stateofblocks.com)"}

# Every threshold lives here and is published as-is in "params", so the page never types one.
P = {"vixLo": 15.0, "vixHi": 40.0, "vvixLo": 90.0, "vvixHi": 150.0, "damageFull": 0.10, "ma": 100,
     "rvDays": 20, "rvLine": 25.0, "washoutMin": 80.0, "clearDrop": 20.0, "clearHold": 3, "episodeEnd": 40.0,
     "zones": [[0, 40, "calm"], [40, 65, "uneasy"], [65, 80, "stressed"], [80, 101, "panic"]],
     "ratesWin": 252, "ratesMin": 201, "ratesLine": 0.90, "dollarDays": 20, "dollarLine": 2.0,
     "fillSessions": 2, "start": "2006-03-06", "sparkSessions": 130, "dailySessions": 260,
     "changeDays": 90, "staleAsofDays": 4, "fundingStaleSessions": 3}
STATE_LABEL = {"calm": "calm", "uneasy": "uneasy", "stressed": "stressed", "panic": "panic",
               "washout": "washout", "crest": "at the crest", "clearing": "clearing"}
STATE_CODE = {"calm": 0, "uneasy": 1, "stressed": 2, "panic": 3, "washout": 4, "crest": 5, "clearing": 6}


# ---------------------------------------------------------------------------- loading
def get(url, tries=4):
    err = None
    for k in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001
            err = e
            time.sleep(3 * (k + 1))
    raise RuntimeError(f"{url}: {err}")


def cboe(name):
    rows = list(csv.reader(get(CBOE.format(name)).decode("utf-8-sig").splitlines()))
    head = [h.strip().upper() for h in rows[0]]
    col = head.index("CLOSE") if "CLOSE" in head else len(head) - 1
    out = {}
    for r in rows[1:]:
        try:
            out[dt.datetime.strptime(r[0].strip(), "%m/%d/%Y").date()] = float(r[col])
        except (ValueError, IndexError):
            continue
    return out


def yahoo(sym, start=dt.date(1990, 1, 1)):
    p1 = int(dt.datetime(start.year, start.month, start.day, tzinfo=dt.timezone.utc).timestamp())
    p2 = int(time.time()) + D
    last = None
    for host in (1, 2):
        try:
            res = json.loads(get(YAHOO.format(host, urllib.request.quote(sym), p1, p2)))["chart"]["result"][0]
            off = res["meta"].get("gmtoffset", 0)
            closes = res["indicators"]["quote"][0]["close"]
            out = {}
            for t, c in zip(res["timestamp"], closes):
                if c is not None:
                    out[dt.datetime.fromtimestamp(t + off, dt.timezone.utc).date()] = float(c)
            return out
        except Exception as e:  # noqa: BLE001
            last = e
    raise RuntimeError(f"yahoo {sym}: {last}")


def load_remote():
    return {"vix": cboe("VIX"), "vvix": cboe("VVIX"), "spx": yahoo("^GSPC", dt.date(2003, 1, 1)),
            "move": yahoo("^MOVE", dt.date(2002, 1, 1)), "dxy": yahoo("DX-Y.NYB", dt.date(2002, 1, 1)),
            "btc": yahoo("BTC-USD", dt.date(2014, 1, 1))}


def load_local(folder):
    def col(fn, name, skip_prov=False):
        out = {}
        with open(os.path.join(folder, fn)) as fh:
            for r in csv.DictReader(fh):
                v = r.get(name)
                if v not in (None, ""):
                    out[dt.date.fromisoformat(r["date"][:10])] = float(v)
        return out
    pan = {k: col("panel.csv", k) for k in ("spx", "vix", "vvix")}
    pan["move"] = col("move.csv", "move"); pan["dxy"] = col("dxy.csv", "dxy"); pan["btc"] = col("btc.csv", "btc")
    pan["spx"] = {d: v for d, v in pan["spx"].items() if d >= dt.date(2003, 1, 1)}
    return pan


# ---------------------------------------------------------------------------- maths (mirrors research engine)
def aligned(series, cal, fill):
    out, last, gap = [], None, 0
    started = False
    for d in cal:
        v = series.get(d)
        if v is not None:
            out.append(v); last = v; gap = 0; started = True
        elif started and last is not None and gap < fill:
            out.append(last); gap += 1
        else:
            out.append(None)
            if started:
                gap += 1
    return out


def rolling_mean(a, n):
    out, s, q = [], 0.0, []
    for x in a:
        q.append(x)
        s += x
        if len(q) > n:
            s -= q.pop(0)
        out.append(s / n if len(q) == n else None)
    return out


def rv(a, n):
    lr = [None] + [math.log(a[i] / a[i - 1]) for i in range(1, len(a))]
    out = []
    for i in range(len(a)):
        w = lr[max(1, i - n + 1):i + 1]
        if i < n or len(w) < n:
            out.append(None); continue
        m = sum(w) / n
        out.append(math.sqrt(sum((x - m) ** 2 for x in w) / (n - 1)) * math.sqrt(252) * 100)
    return out


def rolling_pct(a, win, minp):
    out = []
    for i in range(len(a)):
        v = a[i]
        w = [x for x in a[max(0, i - win + 1):i + 1] if x is not None]
        if v is None or len(w) < minp:
            out.append(None); continue
        less = sum(1 for x in w if x < v); eq = sum(1 for x in w if x == v)
        out.append((less + (eq + 1) / 2) / len(w))
    return out


def clip01(x):
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def zone(x):
    for lo, hi, name in P["zones"]:
        if lo <= x < hi:
            return name
    return "calm"


def run_states(score, seals_ok):
    n = len(score)
    lab = [None] * n; crest = [None] * n; ep = [0] * n; clear = [0] * n; first = [0] * n
    in_ep = False; peak = None; ebbed = False; below = 0; eid = 0
    for i in range(n):
        x = score[i]
        if x is None:
            continue
        is_wash = seals_ok[i] and x >= P["washoutMin"]
        if is_wash and not in_ep:
            in_ep = True; eid += 1; peak = x; ebbed = False; below = 0; first[i] = 1
        if not in_ep:
            lab[i] = zone(x); continue
        if is_wash:
            if ebbed:
                ebbed = False; peak = x
            peak = max(peak, x); below = 0; l = "washout"
        else:
            peak = max(peak, x)
            if not ebbed:
                below = below + 1 if peak - x >= P["clearDrop"] else 0
                if below >= P["clearHold"]:
                    ebbed = True; clear[i] = 1
            l = "clearing" if ebbed else "crest"
        ep[i] = eid; crest[i] = peak
        if x < P["episodeEnd"]:
            lab[i] = zone(x); in_ep = False; ebbed = False; below = 0; continue
        lab[i] = l
    return lab, crest, ep, clear, first


def compute(src):
    cal = sorted(d for d in src["spx"] if d >= dt.date(2003, 1, 1))
    spx = [src["spx"][d] for d in cal]
    vix = aligned(src["vix"], cal, P["fillSessions"])
    vvix = aligned(src["vvix"], cal, P["fillSessions"])
    move = aligned(src["move"], cal, P["fillSessions"])
    dxy = aligned(src["dxy"], cal, P["fillSessions"] + 1)
    ma = rolling_mean(spx, P["ma"])
    dist = [None if m is None else s / m - 1 for s, m in zip(spx, ma)]
    rv20 = rv(spx, P["rvDays"])
    third = 100 / 3
    fear = [None if v is None else third * clip01((v - P["vixLo"]) / (P["vixHi"] - P["vixLo"])) for v in vix]
    frag = [None if v is None else third * clip01((v - P["vvixLo"]) / (P["vvixHi"] - P["vvixLo"])) for v in vvix]
    dmg = [None if x is None else third * clip01(-x / P["damageFull"]) for x in dist]
    score = [None if None in (a, b, c) else a + b + c for a, b, c in zip(fear, frag, dmg)]
    s_trend = [None if x is None else x < 0 for x in dist]
    s_vol = [None if x is None else x > P["rvLine"] for x in rv20]
    seals_ok = [bool(a) and bool(b) for a, b in zip(s_trend, s_vol)]
    lab, crest, ep, clear, first = run_states(score, seals_ok)
    mpct = rolling_pct(move, P["ratesWin"], P["ratesMin"])
    d20 = [None if i < P["dollarDays"] or dxy[i] is None or dxy[i - P["dollarDays"]] is None
           else (dxy[i] / dxy[i - P["dollarDays"]] - 1) * 100 for i in range(len(cal))]
    rates = [None if x is None else x >= P["ratesLine"] for x in mpct]
    dollar = [None if x is None else x >= P["dollarLine"] for x in d20]
    fund = [None if (a is None and b is None) else bool(a) or bool(b) for a, b in zip(rates, dollar)]
    return dict(cal=cal, spx=spx, vix=vix, vvix=vvix, move=move, dxy=dxy, ma=ma, dist=dist, rv20=rv20,
                fear=fear, frag=frag, dmg=dmg, score=score, s_trend=s_trend, s_vol=s_vol, state=lab, crest=crest,
                ep=ep, clear=clear, first=first, mpct=mpct, d20=d20, rates=rates, dollar=dollar, fund=fund)


# ---------------------------------------------------------------------------- output helpers
def ts(d):
    return int(dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc).timestamp())


def r(x, k=2):
    return None if x is None else round(x, k)


def main(out_path, local=None):
    src = load_local(local) if local else load_remote()
    X = compute(src)
    cal = X["cal"]; n = len(cal)
    i = max(k for k in range(n) if X["score"][k] is not None and src["vix"].get(cal[k]) is not None
            and src["vvix"].get(cal[k]) is not None)
    asof = cal[i]
    if (dt.datetime.now(dt.timezone.utc).date() - asof).days > P["staleAsofDays"] and not local:
        sys.exit(f"latest complete session {asof} is too old; refusing to publish")
    k0 = next(k for k in range(n) if cal[k] >= dt.date.fromisoformat(P["start"]))
    x = X["score"][i]

    # funding inputs: latest real print on or before asof
    def last_real(name):
        for k in range(i, max(-1, i - 10), -1):
            if src[name].get(cal[k]) is not None:
                return k
        return None
    km, kd = last_real("move"), last_real("dxy")
    win = [v for v in X["move"][max(0, i - P["ratesWin"] + 1):i + 1] if v is not None]
    move_line = None
    if win:
        sw = sorted(win); q = P["ratesLine"] * (len(sw) - 1)
        lo = int(math.floor(q)); hi = min(lo + 1, len(sw) - 1)
        move_line = sw[lo] + (sw[hi] - sw[lo]) * (q - lo)

    # episode now
    eid = X["ep"][i]
    episode = None
    if eid and X["state"][i] in ("washout", "crest", "clearing"):
        ks = [k for k in range(n) if X["ep"][k] == eid]
        k_start = ks[0]
        crest_k = max(ks[:ks.index(i) + 1], key=lambda k: X["score"][k])
        calm_k = next((k for k in range(k_start, i + 1) if X["fund"][k] is False), None)
        episode = {"start": cal[k_start].isoformat(), "sessions": i - k_start + 1, "crest": r(X["crest"][i], 1),
                   "crestDate": cal[crest_k].isoformat(), "clearBelow": r(X["crest"][i] - P["clearDrop"], 1),
                   "fundingCalmSince": None if calm_k is None else cal[calm_k].isoformat()}

    # history + episodes record
    btc = src.get("btc", {})
    bdays = sorted(btc)

    def btc_at(d):
        lo, hi = 0, len(bdays) - 1
        if not bdays or d < bdays[0]:
            return None
        while lo < hi:
            m = (lo + hi + 1) // 2
            if bdays[m] <= d:
                lo = m
            else:
                hi = m - 1
        return btc[bdays[lo]]

    def outcome(k):
        if k is None:
            return None
        o = {"date": cal[k].isoformat()}
        for lab, h in (("m1", 21), ("m3", 63), ("m12", 252)):
            o["spx_" + lab] = r(X["spx"][k + h] / X["spx"][k] - 1, 4) if k + h <= i - 1 else None
        w = X["spx"][k + 1:min(k + 127, i)]
        o["spx_dd6m"] = r(min(w) / X["spx"][k] - 1, 4) if len(w) >= 21 else None
        b0 = btc_at(cal[k])
        for lab, h in (("m1", 30), ("m3", 91), ("m12", 365)):
            t = cal[k] + dt.timedelta(days=h)
            o["btc_" + lab] = r(btc_at(t) / b0 - 1, 4) if b0 and t < asof else None
        return o

    eps, eps_k = [], []
    for e in sorted(set(X["ep"][k0:i + 1]) - {0}):
        ks = [k for k in range(k0, i + 1) if X["ep"][k] == e]
        fw = next(k for k in ks if X["first"][k])
        crest_k = max(ks, key=lambda k: X["score"][k])
        calm_k = next((k for k in ks if X["fund"][k] is False), None)
        clear_k = next((k for k in ks if X["clear"][k]), None)
        eps_k.append({"fw": fw, "calm": calm_k, "clear": clear_k})
        eps.append({"start": cal[fw].isoformat(), "end": cal[ks[-1]].isoformat(), "open": ks[-1] == i and X["state"][i] in ("washout", "crest", "clearing"),
                    "crest": r(X["score"][crest_k], 1), "crestDate": cal[crest_k].isoformat(),
                    "washoutSessions": sum(1 for k in ks if X["state"][k] == "washout"),
                    "fundingAtStart": {"rates": X["rates"][fw], "dollar": X["dollar"][fw], "movePct": r(X["mpct"][fw], 3), "dxy20": r(X["d20"][fw], 2)},
                    "waitSessions": None if calm_k is None else calm_k - fw,
                    "fromWashout": outcome(fw), "fromFundingCalm": outcome(calm_k), "fromClearing": outcome(clear_k)})

    # changes (state transitions), last 90 days
    changes = []
    for k in range(k0 + 1, i + 1):
        a, b = X["state"][k - 1], X["state"][k]
        if a != b and (asof - cal[k]).days <= P["changeDays"]:
            changes.append({"date": cal[k].isoformat(), "from": a, "to": b, "value": r(X["score"][k], 1)})
    changes.reverse()

    w90 = [(X["score"][k], k) for k in range(max(k0, i - 62), i + 1)]  # ~90 calendar days = 63 sessions
    hi90 = max(w90); lo90 = min(w90)
    valid = [v for v in X["score"][k0:i + 1] if v is not None]
    pct = sum(1 for v in valid if v < x) / len(valid) * 100

    def series(k_from, step=1, keep=None):
        return [[ts(cal[k]), r(X["score"][k], 1)] + ([STATE_CODE.get(X["state"][k], 0)] if keep else [])
                for k in range(k_from, i + 1, step) if X["score"][k] is not None]
    weekly = [[ts(cal[k]), r(X["score"][k], 1)] for k in range(k0, i + 1) if cal[k].weekday() == 4 or k == i]
    btc_daily = [[ts(d), r(btc[d], 0)] for d in bdays if d > asof - dt.timedelta(days=380) and d <= asof]
    btc_weekly = [[ts(d), r(btc[d], 0)] for d in bdays if d.weekday() == 4 and d <= asof]

    # MOVE level at the 90th percentile of its trailing year, per session (the line the funding check uses)
    def q90(k):
        w = sorted(v for v in X["move"][max(0, k - P["ratesWin"] + 1):k + 1] if v is not None)
        if len(w) < P["ratesMin"]:
            return None
        q = P["ratesLine"] * (len(w) - 1); lo = int(math.floor(q)); hi = min(lo + 1, len(w) - 1)
        return w[lo] + (w[hi] - w[lo]) * (q - lo)
    k6 = max(k0, i - P["sparkSessions"] + 1)
    inputs_hist = [[ts(cal[k]), r(X["vix"][k]), r(X["vvix"][k]), r(X["dist"][k] * 100 if X["dist"][k] is not None else None),
                    r(X["rv20"][k]), r(X["move"][k]), r(q90(k), 1), r(X["d20"][k])] for k in range(k6, i + 1)]
    spx_daily = [[ts(cal[k]), r(X["spx"][k]), r(X["ma"][k])] for k in range(max(k0, i - P["dailySessions"] + 1), i + 1)]
    spx_weekly = [[ts(cal[k]), r(X["spx"][k])] for k in range(k0, i + 1) if cal[k].weekday() == 4 or k == i]

    def pct_since(series, k, flip=False):
        v = series[k]
        if v is None:
            return None
        vals = [x for x in series[k0:k + 1] if x is not None]
        if flip:
            return r(sum(1 for x in vals if x > v) / len(vals) * 100, 1)
        return r(sum(1 for x in vals if x < v) / len(vals) * 100, 1)
    pct_all = {"vix": pct_since(X["vix"], i), "vvix": pct_since(X["vvix"], i), "dist": pct_since(X["dist"], i, flip=True),
               "rv20": pct_since(X["rv20"], i)}

    # event study: S&P (to +252 sessions) and Bitcoin (to +365 days) around each washout's first close, indexed to 100
    paths = []
    for e in eps:
        kf = cal.index(dt.date.fromisoformat(e["start"]))
        sp = [[o, r(X["spx"][kf + o] / X["spx"][kf] * 100, 2)] for o in range(-20, 253) if 0 <= kf + o <= i]
        b0 = btc_at(cal[kf]); bp = []
        if b0:
            for o in range(-30, 366):
                dd_ = cal[kf] + dt.timedelta(days=o)
                if dd_ > asof or not bdays or dd_ < bdays[0]:
                    continue
                bp.append([o, r(btc_at(dd_) / b0 * 100, 2)])
        calm = None if e["waitSessions"] is None else e["waitSessions"]
        calm_days = None if calm is None else (cal[kf + calm] - cal[kf]).days
        paths.append({"start": e["start"], "open": e["open"], "calmOffset": calm, "calmDays": calm_days, "spx": sp, "btc": bp})

    # research numbers, recomputed every run from the same series (the backtest scripts produce the same figures)
    def med(v):
        v = sorted(x for x in v if x is not None); m_ = len(v)
        return None if not m_ else (v[m_ // 2] if m_ % 2 else (v[m_ // 2 - 1] + v[m_ // 2]) / 2)

    def stat(vals):
        v = [x for x in vals if x is not None]
        return None if not v else {"median": r(med(v), 6), "hit": r(sum(1 for x in v if x > 0) / len(v), 4), "n": len(v), "worst": r(min(v), 6)}

    def fwd(k):
        o = {"spx_3m": X["spx"][k + 63] / X["spx"][k] - 1 if k + 63 <= i - 1 else None,
             "spx_12m": X["spx"][k + 252] / X["spx"][k] - 1 if k + 252 <= i - 1 else None}
        w = X["spx"][k + 1:min(k + 127, i)]
        o["spx_dd6m"] = min(w) / X["spx"][k] - 1 if len(w) >= 21 else None
        b0 = btc_at(cal[k]); t = cal[k] + dt.timedelta(days=91)
        o["btc_3m"] = btc_at(t) / b0 - 1 if b0 and t < asof else None
        return o

    def group(rows):
        return {"n": len(rows), **{key: stat([o[key] for o in rows]) for key in ("spx_3m", "spx_12m", "spx_dd6m", "btc_3m")}}

    past = [k for k in range(k0, i) if X["score"][k] is not None]  # every session before the live one
    F = {k: fwd(k) for k in past}
    zones = {}
    for name in ("calm", "uneasy", "stressed", "panic", "washout", "crest", "clearing"):
        g = [F[k] for k in past if X["state"][k] == name]
        z = {"sessions": len(g), "share": r(len(g) / len(past), 4)}
        for key, lab in (("spx_3m", "spx_3m"), ("spx_12m", "spx_12m")):
            v = [o[key] for o in g if o[key] is not None]
            z[lab] = r(med(v), 6); z[lab + "_up"] = r(sum(1 for x in v if x > 0) / len(v), 4) if v else None
        b = [o["btc_3m"] for o in g if o["btc_3m"] is not None]
        z["btc_3m"] = r(med(b), 6); z["btc_n"] = len(b)
        zones[name] = z
    allv = list(F.values())
    zones["all"] = {"sessions": len(past), **{lab: r(med([o[lab] for o in allv]), 6) for lab in ("spx_3m", "spx_12m")},
                    **{lab + "_up": r(sum(1 for o in allv if o[lab] is not None and o[lab] > 0) / max(1, sum(1 for o in allv if o[lab] is not None)), 4) for lab in ("spx_3m", "spx_12m")}}
    # selloffs: every first close at 65+ after a reset under 40, split by the bonds and dollar check that day
    sell, armed = [], True
    for k in past:
        if armed and X["score"][k] >= 65:
            sell.append(k); armed = False
        elif X["score"][k] < 40:
            armed = True
    split = {"calm": group([F[k] for k in sell if not X["fund"][k]]), "stressed": group([F[k] for k in sell if X["fund"][k]]),
             "dollarStressed": group([F[k] for k in sell if X["dollar"][k]]), "dollarCalm": group([F[k] for k in sell if not X["dollar"][k]])}
    hist = [e for e in eps_k if e["fw"] < i]
    entries = {"firstWashout": group([F[e["fw"]] for e in hist]),
               "fundingCalm": group([F[e["calm"]] for e in hist if e["calm"] is not None and e["calm"] < i]),
               "clearing": group([F[e["clear"]] for e in hist if e["clear"] is not None and e["clear"] < i])}
    both = [e for e in hist if e["calm"] is not None and e["calm"] < i]
    leg = lambda ks: {key: r(med([F[k][key] for k in ks]), 6) for key in ("spx_3m", "spx_12m", "spx_dd6m", "btc_3m")}
    matched = {"n": len(both), "episodes": [cal[e["fw"]].isoformat() for e in both], "washout": leg([e["fw"] for e in both]),
               "calm": leg([e["calm"] for e in both]), "btcN": sum(1 for e in both if F[e["fw"]]["btc_3m"] is not None)}
    dd6 = [F[e["fw"]]["spx_dd6m"] for e in hist if F[e["fw"]]["spx_dd6m"] is not None]
    research = {"computed": "every run", "sample": {"from": cal[k0].isoformat(), "to": cal[i - 1].isoformat(), "stressEvents": len(sell), "washouts": len(hist)},
                "zones": zones, "split": split, "entries": entries, "matched": matched,
                "lowerCloseAfter": {"n": sum(1 for x in dd6 if x < 0), "of": len(dd6)}}
    summary_path = os.path.join(ROOT, "research", "market-stress", "summary.json")
    if os.path.exists(summary_path):  # notes on the tested-and-dropped input stay with the research
        research["cor1m"] = json.load(open(summary_path)).get("cor1m")

    fstate = None if X["fund"][i] is None else ("stressed" if X["fund"][i] else "calm")
    provisional = any(src[nm].get(asof) is None for nm in ("move", "dxy"))
    out = {"asof": ts(asof), "asofDate": asof.isoformat(), "value": r(x, 1), "state": X["state"][i],
           "stateLabel": STATE_LABEL[X["state"][i]], "zone": zone(x), "scale": min(4, int(x // 20)), "pct": r(pct, 2),
           "d5": r(x - X["score"][i - 5], 1), "d20": r(x - X["score"][i - 20], 1),
           "hi90": r(hi90[0], 1), "hi90Date": cal[hi90[1]].isoformat(), "lo90": r(lo90[0], 1), "lo90Date": cal[lo90[1]].isoformat(),
           "pillars": {"fear": r(X["fear"][i], 1), "fragility": r(X["frag"][i], 1), "damage": r(X["dmg"][i], 1)},
           "seals": {"trend": bool(X["s_trend"][i]), "vol": bool(X["s_vol"][i])},
           "inputs": {"spx": r(X["spx"][i]), "ma100": r(X["ma"][i]), "dist": r(X["dist"][i] * 100), "rv20": r(X["rv20"][i]),
                      "vix": r(X["vix"][i]), "vvix": r(X["vvix"][i]),
                      "move": r(X["move"][km]) if km is not None else None, "moveDate": cal[km].isoformat() if km is not None else None,
                      "movePct": r(X["mpct"][i] * 100 if X["mpct"][i] is not None else None, 1), "moveLine": r(move_line, 1),
                      "dxy": r(X["dxy"][kd]) if kd is not None else None, "dxyDate": cal[kd].isoformat() if kd is not None else None,
                      "dxy20": r(X["d20"][i]), "btc": r(btc_at(asof), 0),
                      "vix1w": r(X["vix"][i] - X["vix"][i - 5]) if X["vix"][i - 5] is not None else None,
                      "move1w": r(X["move"][km] - X["move"][km - 5]) if km is not None and km >= 5 and X["move"][km - 5] is not None else None},
           "funding": {"state": fstate, "rates": X["rates"][i], "dollar": X["dollar"][i]},
           "episode": episode, "episodes": eps, "changes": changes,
           "spark": series(max(k0, i - P["sparkSessions"] + 1)),
           "history": {"daily": series(max(k0, i - P["dailySessions"] + 1), keep=True), "weekly": weekly},
           "btcHistory": {"daily": btc_daily, "weekly": btc_weekly},
           "spxHistory": {"daily": spx_daily, "weekly": spx_weekly},
           "inputsHistory": {"cols": ["t", "vix", "vvix", "dist", "rv20", "move", "moveLine", "dxy20"], "rows": inputs_hist},
           "pctAll": pct_all, "paths": paths,
           "research": research, "provisional": provisional, "published": int(time.time()), "params": P}
    # skip the write when nothing but the publish stamp would change (keeps the repo history quiet)
    if os.path.exists(out_path):
        try:
            prev = json.load(open(out_path)); prev.pop("published", None)
            cur = dict(out); cur.pop("published", None)
            if json.dumps(prev, sort_keys=True) == json.dumps(json.loads(json.dumps(cur)), sort_keys=True):
                print(f"{asof}  unchanged, nothing written"); return
        except (ValueError, OSError):
            pass
    tmpdir = os.path.dirname(os.path.abspath(out_path))
    fd, tmp = tempfile.mkstemp(dir=tmpdir)
    with os.fdopen(fd, "w") as fh:
        json.dump(out, fh, separators=(",", ":"))
    os.replace(tmp, out_path)
    fs = fstate or "unknown"
    print(f"{asof}  {x:.1f}  {X['state'][i]}  seals trend={out['seals']['trend']} vol={out['seals']['vol']}  funding {fs}"
          f"  (MOVE pct {out['inputs']['movePct']}, DXY 20d {out['inputs']['dxy20']}%)")


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        sys.exit(__doc__)
    loc = None
    if "--local" in args:
        j = args.index("--local"); loc = args[j + 1]; del args[j:j + 2]
    try:
        main(args[0], loc)
    except RuntimeError as e:
        sys.exit(f"download failed, nothing published: {e}")
