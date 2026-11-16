"""Load the pulled Binance files into daily (UTC) and hourly series. Shared by the backtest."""
import csv, io, os, glob, json, math, zipfile, datetime as dt
from collections import defaultdict

RAW = os.environ.get("GREENFACE_RAW", os.path.expanduser("~/greenface/raw"))
GT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "docs", "gt_regime.json")  # price field only
DAY = 86400


def rows(path):
    with zipfile.ZipFile(path) as z:
        txt = z.read(z.namelist()[0]).decode()
    r = list(csv.reader(io.StringIO(txt)))
    if r and r[0] and not r[0][0].replace(".", "").isdigit() and not r[0][0][:4].isdigit():
        r = r[1:]  # header
    return r


def ms(x):
    x = int(float(x))
    return x // 1000 if x > 1e14 else x  # some newer files are in microseconds? normalise


def to_s(x):
    x = int(float(x))
    if x > 1e15: return x // 1_000_000
    if x > 1e12: return x // 1000
    return x


def klines(sym, iv):
    out = {}
    for p in sorted(glob.glob(f"{RAW}/monthly/klines/{sym}/{iv}/*.zip")) + sorted(glob.glob(f"{RAW}/daily/klines/{sym}/{iv}/*.zip")):
        for r in rows(p):
            t = to_s(r[0])
            out[t] = (float(r[1]), float(r[2]), float(r[3]), float(r[4]))
    return out


def premium_1h(sym):
    out = {}
    for p in sorted(glob.glob(f"{RAW}/monthly/premiumIndexKlines/{sym}/1h/*.zip")) + sorted(glob.glob(f"{RAW}/daily/premiumIndexKlines/{sym}/1h/*.zip")):
        for r in rows(p):
            out[to_s(r[0])] = (float(r[1]), float(r[4]))  # open, close
    return out


def funding(sym):
    out = {}
    for p in sorted(glob.glob(f"{RAW}/monthly/fundingRate/{sym}/*.zip")):
        for r in rows(p):
            out[to_s(r[0])] = float(r[2])
    return out


def metrics_daily(sym):
    """Per UTC day: last OI (base units), mean all-account L/S, mean top-trader position L/S."""
    out = {}
    for p in sorted(glob.glob(f"{RAW}/daily/metrics/{sym}/*.zip")):
        oi, acct, top = [], [], []
        day = None
        for r in rows(p):
            try:
                t = dt.datetime.strptime(r[0], "%Y-%m-%d %H:%M:%S").replace(tzinfo=dt.timezone.utc)
            except ValueError:
                continue
            day = int(t.timestamp()) // DAY * DAY
            if r[2] and float(r[2]) > 0: oi.append(float(r[2]))
            if r[6] and float(r[6]) > 0: acct.append(float(r[6]))
            if r[5] and float(r[5]) > 0: top.append(float(r[5]))
        if day is None: continue
        out[day] = (oi[-1] if oi else None,
                    sum(acct) / len(acct) if acct else None,
                    sum(top) / len(top) if top else None)
    return out


def daily_frame(sym):
    """Dict day -> fields. close from gt_regime for BTC (the page's price), perp 1d close for ETH."""
    k = klines(sym, "1d")
    f = funding(sym)
    m = metrics_daily(sym)
    fd = defaultdict(list)
    for t, v in f.items():
        fd[t // DAY * DAY].append(v)
    if sym == "BTCUSDT":
        g = json.load(open(GT))["rows"]
        close = {int(dt.datetime.strptime(r["date"], "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp()): r["price"] for r in g}
    else:
        close = {t: v[3] for t, v in k.items()}
    days = list(range(min(close), max(close) + DAY, DAY))  # calendar days; a missing close is filled from the perp
    filled = [d for d in days if d not in close]
    for d in filled:
        close[d] = k[d][3]
    out = []
    for d in days:
        mm = m.get(d, (None, None, None))
        out.append({"t": d, "close": close[d], "perp": k.get(d, (None,) * 4)[3],
                    "fund": sum(fd[d]) if fd.get(d) else None,  # sum of the day's settlements = per-day carry
                    "oi": mm[0], "acct": mm[1], "top": mm[2]})
    return out
