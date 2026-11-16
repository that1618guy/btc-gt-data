#!/usr/bin/env python3
"""Publishes the Green Face Index reading as one JSON file for stateofblocks.com.

How crowded Binance BTC perps are. Four inputs from Binance's public data dumps (data.binance.vision),
each z-scored against its trailing 365 days (minimum 90), composite = their mean (needs 3 of 4):

  funding   7-day average of the day's summed funding
  oi        30-day log change in open interest (BTC terms)
  ls        7-day average of the all-account long/short ratio
  gap       7-day average of log(all-account ratio) - log(top-trader position ratio)

Binance posts funding as monthly files, so days after the last monthly file are rebuilt from the
hourly premium index (avg premium + clamp(0.01% - premium, +/-0.05%), capped at +/-0.3%).

BTC closes come from the `price` field of docs/gt_regime.json and nothing else in that file is read. A single
missing day there is filled with Binance's BTCUSDT perp daily close and listed in "closeFilled".
The post log is data/cz_posts.csv. The research numbers in the method block come from
research/greenface/summary.json, written by the backtest in research/greenface/.

Runs in the btc-gt-data repo through .github/workflows/greenface.yml every 6 hours.

By hand:  python3 scripts/greenface_publish.py docs/greenface.json
Standard library only. Downloads are cached in $GREENFACE_CACHE (default .cache/greenface).
"""
import csv, io, json, math, os, sys, tempfile, time, urllib.error, urllib.request, zipfile
import datetime as dt
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.environ.get("GREENFACE_CACHE", os.path.join(ROOT, ".cache", "greenface"))
VISION = "https://data.binance.vision/data/futures/um/"
SYM = "BTCUSDT"
D = 86400
METRICS_START = dt.date(2020, 9, 1)
FUNDING_START = dt.date(2020, 1, 1)
# Zone cut-offs, set on 2020-2023 data only (research/greenface/README.md): composite quantiles at the
# normal-curve positions of z = 1.5 / 0.7 / -0.8 / -1.3. Not fit to returns.
CUTS = [(1.24, "green", "green face"), (0.40, "crowded", "crowded"), (-0.49, "neutral", "neutral"), (-0.75, "washed", "washed")]
LOW = ("flushed", "flushed")
BOARD_START = "2024-12-01"  # scoreboard baseline: every day from here
# Window lengths and thresholds. Used by the code below and published as-is in "params",
# so the page never types one.
P = {"zWin": 365, "zMin": 90, "avg": 7, "avgMin": 5, "oiDays": 30, "dirDays": 20, "dirStep": 0.4, "peakDays": 30,
     "rangeDays": 90, "emaDays": 21, "need": 3, "localHighWin": 10, "localHighFrom": -1, "localHighTo": 3,
     "fundCap": 0.003, "fundClamp": 0.0005, "fundInterest": 0.0001, "everyHours": 6,
     "staleAfterHours": 18, "staleAsofDays": 3}


class FetchError(Exception):
    pass


def fetch(path, recent):
    """Return the zip's CSV rows, or None if the file is not published yet (404 allowed only when recent)."""
    p = os.path.join(CACHE, path)
    if not os.path.exists(p):
        err = None
        for k in range(4):
            try:
                req = urllib.request.Request(VISION + path, headers={"user-agent": "sob-greenface"})
                with urllib.request.urlopen(req, timeout=60) as r:
                    b = r.read()
                os.makedirs(os.path.dirname(p), exist_ok=True)
                with open(p + ".tmp", "wb") as fh:
                    fh.write(b)
                os.replace(p + ".tmp", p)
                break
            except urllib.error.HTTPError as e:
                if e.code == 404 and recent:
                    return None
                err = e
            except Exception as e:
                err = e
            time.sleep(2 * (k + 1))
        else:
            raise FetchError(f"{path}: {err}")
    with zipfile.ZipFile(p) as z:
        txt = z.read(z.namelist()[0]).decode()
    rows = list(csv.reader(io.StringIO(txt)))
    return [r for r in rows if r and r[0][:1].isdigit()]


def secs(x):
    x = int(float(x))
    return x // 1_000_000 if x > 1e15 else x // 1000 if x > 1e12 else x


def months(a, b):
    y, m = a.year, a.month
    while (y, m) <= (b.year, b.month):
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def load(today):
    """Pull every file the index needs. Returns per-day dicts for metrics and funding."""
    yday = today - dt.timedelta(days=1)
    jobs = []
    d = METRICS_START
    while d <= yday:
        jobs.append(("m", d, f"daily/metrics/{SYM}/{SYM}-metrics-{d:%Y-%m-%d}.zip", (yday - d).days <= 3))
        d += dt.timedelta(days=1)
    for y, m in months(FUNDING_START, yday):
        recent = (y, m) >= ((yday.replace(day=1) - dt.timedelta(days=1)).year, (yday.replace(day=1) - dt.timedelta(days=1)).month)
        jobs.append(("f", (y, m), f"monthly/fundingRate/{SYM}/{SYM}-fundingRate-{y}-{m:02d}.zip", recent))
    # premium index, hourly, for the last ~70 days: covers any month the funding dumps have not reached
    d = yday - dt.timedelta(days=70)
    while d <= yday:
        jobs.append(("p", d, f"daily/premiumIndexKlines/{SYM}/1h/{SYM}-1h-{d:%Y-%m-%d}.zip", (yday - d).days <= 3))
        d += dt.timedelta(days=1)
    with ThreadPoolExecutor(24) as ex:
        res = list(ex.map(lambda j: (j[0], j[1], fetch(j[2], j[3])), jobs))
    metrics, funding, prem = {}, {}, {}
    for kind, key, rows in res:
        if rows is None:
            continue
        if kind == "m":
            oi, acct, top = [], [], []
            for r in rows:
                if r[2] and float(r[2]) > 0: oi.append(float(r[2]))
                if r[6] and float(r[6]) > 0: acct.append(float(r[6]))
                if r[5] and float(r[5]) > 0: top.append(float(r[5]))
            t = int(dt.datetime(key.year, key.month, key.day, tzinfo=dt.timezone.utc).timestamp())
            metrics[t] = (oi[-1] if oi else None, sum(acct) / len(acct) if acct else None, sum(top) / len(top) if top else None)
        elif kind == "f":
            for r in rows:
                funding[secs(r[0])] = float(r[2])
        else:
            for r in rows:
                prem[secs(r[0])] = float(r[4])
    return metrics, funding, prem


def daily_funding(funding, prem):
    """Per-day summed funding. Days after the last monthly file are rebuilt from the premium index."""
    out = {}
    for t, v in funding.items():
        out.setdefault(t // D * D, []).append(v)
    out = {k: sum(v) for k, v in out.items()}
    rebuilt = {}
    last = max(funding)
    t = last + 8 * 3600
    while True:
        hs = [prem.get(t - 3600 * k) for k in range(1, 9)]
        if None in hs:
            break
        prem_avg = sum(hs) / 8
        rebuilt.setdefault(t // D * D, []).append(min(P["fundCap"], max(-P["fundCap"], prem_avg + max(-P["fundClamp"], min(P["fundClamp"], P["fundInterest"] - prem_avg)))))
        t += 8 * 3600
    rdays = []
    for k, v in rebuilt.items():
        if k not in out and len(v) == 3:
            out[k] = sum(v)
            rdays.append(k)
    return out, sorted(rdays)


def rmean(a, n, minp):
    out = [None] * len(a)
    for i in range(len(a)):
        w = [x for x in a[max(0, i - n + 1):i + 1] if x is not None]
        if len(w) >= minp: out[i] = sum(w) / len(w)
    return out


def rz(a, win=P["zWin"], minp=P["zMin"]):
    out = [None] * len(a)
    for i in range(len(a)):
        if a[i] is None: continue
        xs = [x for x in a[max(0, i - win + 1):i + 1] if x is not None]
        n = len(xs)
        if n < minp: continue
        m = sum(xs) / n
        var = sum((x - m) ** 2 for x in xs) / (n - 1)
        if var > 0: out[i] = (a[i] - m) / math.sqrt(var)
    return out


def zone(x):
    for lim, key, label in CUTS:
        if x >= lim: return key, label
    return LOW


def direction(d):
    if d is None: return None, None
    if d >= P["dirStep"]: return "up", "heating"
    if d <= -P["dirStep"]: return "down", "cooling"
    return "flat", "holding"


def r(x, k=4):
    return None if x is None else round(x, k)


def main(out_path):
    today = dt.datetime.now(dt.timezone.utc).date()
    metrics, funding, prem = load(today)
    fund, rdays = daily_funding(funding, prem)

    g = json.load(open(os.path.join(ROOT, "docs", "gt_regime.json")))["rows"]
    close = {int(dt.datetime.strptime(x["date"], "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp()): float(x["price"])
             for x in g if x.get("price") is not None}
    days = list(range(min(close), max(close) + D, D))  # calendar days
    # A single day missing from gt_regime (both neighbours present) is filled with Binance's BTCUSDT perp
    # daily close, as the research does. Longer gaps stay empty and the returns that need them are skipped.
    filled = []
    for t in days:
        if t not in close and (t - D) in close and (t + D) in close:
            dd = dt.datetime.fromtimestamp(t, dt.timezone.utc).date()
            rows = fetch(f"daily/klines/{SYM}/1d/{SYM}-1d-{dd:%Y-%m-%d}.zip", recent=False)
            close[t] = float(rows[0][4])
            filled.append(f"{dd:%Y-%m-%d}")
    c = [close.get(t) for t in days]
    gaps = sum(1 for v in c[-400:] if v is None)
    if gaps > 5:
        sys.exit(f"gt_regime.json is missing {gaps} closes in the last 400 days; refusing to publish")
    mm = [metrics.get(t, (None, None, None)) for t in days]
    oi = [x[0] for x in mm]; acct = [x[1] for x in mm]; top = [x[2] for x in mm]
    f = [fund.get(t) for t in days]

    raw = {
        "funding": rmean(f, P["avg"], P["avgMin"]),
        "oi": [math.log(oi[i] / oi[i - P["oiDays"]]) if i >= P["oiDays"] and oi[i] and oi[i - P["oiDays"]] else None for i in range(len(days))],
        "ls": rmean(acct, P["avg"], P["avgMin"]),
        "gap": rmean([math.log(a) - math.log(b) if a and b else None for a, b in zip(acct, top)], P["avg"], P["avgMin"]),
    }
    z = {k: rz(v) for k, v in raw.items()}
    comp = []
    for i in range(len(days)):
        xs = [z[k][i] for k in z if z[k][i] is not None]
        comp.append(sum(xs) / len(xs) if len(xs) >= P["need"] else None)

    i = max(k for k, x in enumerate(comp) if x is not None)
    x = comp[i]
    if (days[-1] - days[i]) // D > P["staleAsofDays"]:
        sys.exit(f"latest reading {time.strftime('%Y-%m-%d', time.gmtime(days[i]))} is more than 3 days behind the closes; refusing to publish")
    dd = P["dirDays"]
    d20 = x - comp[i - dd] if i >= dd and comp[i - dd] is not None else None
    valid = [v for v in comp if v is not None]
    pk, pki = max((comp[k], k) for k in range(max(0, i - P["peakDays"]), i + 1) if comp[k] is not None)
    w90 = [(comp[k], k) for k in range(max(0, i - P["rangeDays"] + 1), i + 1) if comp[k] is not None]
    hi90, lo90 = max(w90), min(w90)
    zk, zl = zone(x)
    dk, dl = direction(d20)

    # EMA21 of the close (same seeding as the backtest), for the "what kind of crowding" label
    e, ema = None, []
    for v in c:
        if v is not None:
            e = v if e is None else e + (2 / (P["emaDays"] + 1)) * (v - e)
        ema.append(e)
    state, stateLabel = None, None
    if zk in ("green", "crowded") and c[i] is not None:
        state, stateLabel = ("highs", "crowded at highs") if c[i] > ema[i] else ("selloff", "crowded longs into a selloff")
    zi = {k: z[k][i] for k in z}
    have = {k: v for k, v in zi.items() if v is not None}
    driver = (max if x >= 0 else min)(have, key=lambda k: have[k])

    # ---------------------------------------------------------------- post log + scoreboard
    ti = {t: k for k, t in enumerate(days)}
    labs = [None if v is None else zone(v)[0] for v in comp]
    n = len(days)
    fwd = lambda k, h: c[k + h] / c[k] - 1 if k + h < n and c[k] and c[k + h] else None

    def local_high(k):  # peak of the +/-10 day window falls on day -1 to +3
        L = P["localHighWin"]
        if k - L < 0 or k + L >= n: return None
        w = c[k - L:k + L + 1]
        if None in w: return None
        j = w.index(max(w)) - L
        return P["localHighFrom"] <= j <= P["localHighTo"]

    posts = []
    for row in csv.DictReader(open(os.path.join(ROOT, "data", "cz_posts.csv"))):
        t = int(dt.datetime.strptime(row["date"], "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp())
        k = ti.get(t)
        if k is None: continue
        zprev = labs[k - 1] if k >= 1 else None  # the reading visible when the post went out
        posts.append({"date": row["date"], "t": t, "label": row["label"], "kind": row["kind"], "dateConf": row["date_conf"],
                      "source": row["source"], "zone": zprev, "value": r(comp[k - 1], 3) if k >= 1 else None,
                      "close": c[k] and round(c[k], 2), "r1": r(fwd(k, 1), 5), "r7": r(fwd(k, 7), 5), "r30": r(fwd(k, 30), 5), "localHigh": local_high(k)})
    posts.sort(key=lambda p: p["t"])

    def agg(ps):
        o = {"n": len(ps)}
        for h in (1, 7, 30):
            v = [p[f"r{h}"] for p in ps if p[f"r{h}"] is not None]
            o[f"n{h}"] = len(v)
            o[f"r{h}"] = r(sum(v) / len(v), 5) if v else None
            o[f"up{h}"] = sum(1 for q in v if q > 0)
        lh = [p["localHigh"] for p in ps if p["localHigh"] is not None]
        o["localHigh"], o["localHighN"] = sum(lh), len(lh)
        return o

    b0 = ti[int(dt.datetime.strptime(BOARD_START, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp())]
    base = {"from": BOARD_START, "n": n - b0}
    for h in (1, 7, 30):
        v = [fwd(k, h) for k in range(b0, n) if fwd(k, h) is not None]
        base[f"n{h}"], base[f"r{h}"], base[f"up{h}"] = len(v), r(sum(v) / len(v), 5), sum(1 for q in v if q > 0)
    lh = [local_high(k) for k in range(b0, n) if local_high(k) is not None]
    base["localHigh"], base["localHighN"] = sum(lh), len(lh)
    board = {"kinds": {k: agg([p for p in posts if k == "all" or p["kind"] == k]) for k in ("hype", "dip", "all")},
             "zones": {zk_: agg([p for p in posts if p["zone"] == zk_]) for zk_ in [c_[1] for c_ in CUTS] + [LOW[0]]},
             "baseline": base}
    latest = posts[-1] if posts else None

    summ = json.load(open(os.path.join(ROOT, "research", "greenface", "summary.json")))
    out = {"asof": days[i], "value": round(x, 6), "zone": zk, "zoneLabel": zl, "dir": dk, "dirLabel": dl,
           "d20": r(d20, 6), "pct": round(sum(1 for v in valid if v < x) / len(valid) * 100, 2),
           "peak": round(pk, 6), "peakDate": days[pki], "missing": 4 - len(have),
           "spark": [[days[k], round(comp[k], 3)] for k in range(max(0, i - 179), i + 1) if comp[k] is not None],
           "published": int(time.time()),
           "hi90": round(hi90[0], 6), "hi90Date": days[hi90[1]], "lo90": round(lo90[0], 6), "lo90Date": days[lo90[1]],
           "cuts": [[lim, key, label] for lim, key, label in CUTS] + [[None, LOW[0], LOW[1]]],
           "z": {k: r(v, 6) for k, v in zi.items()},
           "inputs": {"funding7": r(raw["funding"][i], 7), "oi30": r(raw["oi"][i]), "ls7": r(raw["ls"][i]), "gap7": r(raw["gap"][i]),
                      "oi": r(oi[i], 1)},
           "driver": driver, "state": state, "stateLabel": stateLabel,
           "close": c[i] and round(c[i], 2), "ema21": round(ema[i], 2),
           "closeFilled": filled,
           "fundRebuilt": {"days": len(rdays), "from": rdays[0] if rdays else None, "to": rdays[-1] if rdays else None},
           "cz": latest and {k: latest[k] for k in ("date", "label", "kind", "dateConf", "source", "zone", "r1", "r7", "r30")},
           "posts": [{k: v for k, v in p.items() if k != "t"} for p in posts],
           "board": board,
           "chart": [[days[k], c[k] and round(c[k], 2), None if comp[k] is None else round(comp[k], 3)] for k in range(b0, n)],
           "method": summ,
           "histStart": days[next(k for k, v in enumerate(comp) if v is not None)],
           "params": P}

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(out_path)))
    with os.fdopen(fd, "w") as fh:
        json.dump(out, fh, separators=(",", ":"))
    os.replace(tmp, out_path)
    print(f"{time.strftime('%Y-%m-%d', time.gmtime(out['asof']))}  {x:+.2f}  {zl}, {dl}  driver {driver}  "
          f"({out['missing']} inputs missing, {len(rdays)} funding days rebuilt)")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    try:
        main(sys.argv[1])
    except FetchError as e:
        sys.exit(f"download failed, nothing published: {e}")
