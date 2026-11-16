#!/usr/bin/env python3
"""Pull Binance USD-M perp history from data.binance.vision into ~/greenface/raw.

metrics (5-min OI + long/short), daily files; fundingRate monthly; klines 1h + 1d monthly
(and daily for the month in progress); premiumIndexKlines 1h (monthly + daily) for the
funding reconstruction check. Cached: a file already on disk is not fetched again.
"""
import os, re, sys, urllib.request, datetime as dt
from concurrent.futures import ThreadPoolExecutor

RAW = os.environ.get("GREENFACE_RAW", os.path.expanduser("~/greenface/raw"))
B = "https://data.binance.vision/"
S3 = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision?delimiter=/&prefix="


def listing(prefix):
    keys, marker = [], ""
    while True:
        url = S3 + prefix + (f"&marker={marker}" if marker else "")
        x = urllib.request.urlopen(url, timeout=60).read().decode()
        k = re.findall(r"<Key>([^<]*\.zip)</Key>", x)
        keys += k
        if "<IsTruncated>true</IsTruncated>" not in x or not k:
            return keys
        marker = k[-1]


def fetch(key):
    p = os.path.join(RAW, key.replace("data/futures/um/", ""))
    if os.path.exists(p) and os.path.getsize(p) > 0:
        return 0
    os.makedirs(os.path.dirname(p), exist_ok=True)
    for _ in range(4):
        try:
            b = urllib.request.urlopen(B + key, timeout=60).read()
            open(p + ".tmp", "wb").write(b)
            os.replace(p + ".tmp", p)
            return 1
        except Exception as e:
            err = e
    print("FAIL", key, err)
    return 0


def main():
    prefixes = []
    for s in ("BTCUSDT", "ETHUSDT"):
        prefixes += [f"data/futures/um/daily/metrics/{s}/",
                     f"data/futures/um/monthly/fundingRate/{s}/",
                     f"data/futures/um/monthly/klines/{s}/1h/",
                     f"data/futures/um/monthly/klines/{s}/1d/",
                     f"data/futures/um/monthly/premiumIndexKlines/{s}/1h/"]
    keys = []
    for p in prefixes:
        k = listing(p)
        print(p, len(k), k[0].split("/")[-1] if k else "", k[-1].split("/")[-1] if k else "")
        keys += k
    # daily files for the month in progress (monthly dumps lag a month)
    today = dt.date.today()
    first = today.replace(day=1)
    for s in ("BTCUSDT", "ETHUSDT"):
        for kind in ("klines/%s/1h" % s, "klines/%s/1d" % s, "premiumIndexKlines/%s/1h" % s):
            pre = f"data/futures/um/daily/{kind}/"
            ks = [k for k in listing(pre + f"{s}-{kind.split('/')[-1]}-{first:%Y-%m}")]
            keys += ks
    with ThreadPoolExecutor(32) as ex:
        n = sum(ex.map(fetch, keys))
    print("fetched", n, "of", len(keys))


if __name__ == "__main__":
    main()
