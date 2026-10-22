#!/usr/bin/env python3
"""Publishes the Grift Index reading as one small JSON file for stateofblocks.com.

Same inputs and math as /dashboards/grift-index/: pump.fun bonding-curve volume plus
17 launchpads' fees from DefiLlama, 7-day averages, log scale, z-score against the
trailing 365 days, composite = average of the two.

Runs in the btc-gt-data repo through .github/workflows/grift.yml, which writes docs/grift.json
once a day next to gt_regime.json. The site reads it from there.

By hand:  python3 scripts/grift_publish.py docs/grift.json
Standard library only.
"""
import json, math, os, sys, tempfile, time, urllib.request
from concurrent.futures import ThreadPoolExecutor

VENUES = {"solana": ["pump.fun", "bonk.fun-launchpad", "launchlab", "meteora-dynamic-bonding-curve", "bags", "launch-coin-on-believe"],
          "rh": ["pons-v2", "pons-v1", "noxa-fun", "stonkbrokers"],
          "bnb": ["four.meme", "flap-sh", "binance-alpha"],
          "base": ["clanker", "zora-coins", "flaunch", "o1-launchpad"]}
CHAIN_NAMES = {"Solana": "solana", "Robinhood Chain": "rh", "BSC": "bnb", "Base": "base"}


def get(url):
    req = urllib.request.Request(url, headers={"accept": "application/json", "user-agent": "sob-grift"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def fees(job):
    ch, slug = job
    try:
        d = get(f"https://api.llama.fi/summary/fees/{slug}?dataType=dailyFees")
        out = [[ts, sum(v or 0 for v in sub.values())] for ts, brk in d.get("totalDataChartBreakdown") or []
               for cn, sub in (brk or {}).items() if CHAIN_NAMES.get(cn) == ch]
        return ch, out if len(out) >= 30 else None
    except Exception:
        return ch, None


def rmean(a, n):
    out, s = [None] * len(a), 0.0
    for i, x in enumerate(a):
        s += x
        if i >= n: s -= a[i - n]
        if i >= n - 1: out[i] = s / n
    return out


def rz(a, win, minp):
    out = [None] * len(a)
    for i in range(len(a)):
        if a[i] is None: continue
        xs = [x for x in a[max(0, i - win + 1):i + 1] if x is not None]
        n = len(xs)
        if n < minp: continue
        m = sum(xs) / n
        var = (sum(x * x for x in xs) - n * m * m) / (n - 1)
        if var > 0: out[i] = (a[i] - m) / math.sqrt(var)
    return out


def zone(x):
    for lim, key, label in [(1.5, "hot", "extraction season"), (0.7, "froth", "frothy"), (-0.8, "luke", "lukewarm"), (-1.3, "quiet", "quiet")]:
        if x >= lim: return key, label
    return "grim", "grim"


def direction(d):
    if d is None: return None, None
    if d >= 0.4: return "up", "filling"
    if d <= -0.4: return "down", "draining"
    return "flat", "holding"


def main(out_path):
    vol = get("https://api.llama.fi/summary/dexs/pump.fun?dataType=dailyVolume")["totalDataChart"]
    with ThreadPoolExecutor(8) as ex:
        res = list(ex.map(fees, [(ch, s) for ch, ss in VENUES.items() for s in ss]))
    missing = sum(1 for _, pts in res if pts is None)
    by_chain = {}
    for ch, pts in res:
        if pts: by_chain.setdefault(ch, []).extend(pts)
    today = int(time.time() // 86400) * 86400
    vol = [p for p in vol if p[0] < today]
    dates = [int(p[0] // 86400) * 86400 for p in vol]
    v = [p[1] for p in vol]
    fmap = {}
    for ch, pts in by_chain.items():
        for ts, val in pts:
            d = int(ts // 86400) * 86400
            fmap[d] = fmap.get(d, 0) + val
    f = [fmap.get(d, 0) for d in dates]
    v7, f7 = rmean(v, 7), rmean(f, 7)
    zV = rz([math.log(x) if x and x > 0 else None for x in v7], 365, 90)
    zF = rz([math.log(x) if x and x > 0 else None for x in f7], 365, 90)
    gb = [(a + b) / 2 if a is not None and b is not None else None for a, b in zip(zV, zF)]
    i = max(k for k, x in enumerate(gb) if x is not None)
    x = gb[i]
    d20 = x - gb[i - 20] if i >= 20 and gb[i - 20] is not None else None
    valid = [g for g in gb if g is not None]
    pk, pki = max((gb[k], k) for k in range(max(0, i - 30), i + 1) if gb[k] is not None)
    zk, zl = zone(x)
    dk, dl = direction(d20)
    out = {"asof": dates[i], "value": round(x, 4), "zone": zk, "zoneLabel": zl, "dir": dk, "dirLabel": dl,
           "d20": None if d20 is None else round(d20, 4), "pct": round(sum(1 for g in valid if g < x) / len(valid) * 100, 2),
           "zV": round(zV[i], 4), "zF": round(zF[i], 4), "v7": round(v7[i]), "f7": round(f7[i]),
           "peak": round(pk, 4), "peakDate": dates[pki], "missing": missing,
           "spark": [[dates[k], round(gb[k], 3)] for k in range(max(0, i - 179), i + 1) if gb[k] is not None],
           "published": int(time.time())}
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(out_path)))
    with os.fdopen(fd, "w") as fh:
        json.dump(out, fh, separators=(",", ":"))
    os.replace(tmp, out_path)
    print(f"{time.strftime('%Y-%m-%d', time.gmtime(out['asof']))}  {x:+.2f}  {zl}, {dl}  ({missing} venues missing)")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
