#!/usr/bin/env python3
"""Add a CZ post to the Green Face Tracker log (data/cz_posts.csv). Standard library only.

    python3 scripts/add_post.py 2026-11-20 "Bitcoin is resilient." hype https://x.com/cz_binance/status/...

Arguments: date (YYYY-MM-DD, UTC), label (the post's words, as shown on the page), kind (hype or dip),
source URL (the post itself, or a news story quoting it). Options: --approx if the date is not exact,
--dry-run to check without writing.

It checks the row, appends it, and prints what the page's hero will show after the next publish.
The page picks it up on the next 6-hourly run, or run the publisher by hand. Commit data/cz_posts.csv.
"""
import argparse, csv, datetime as dt, json, os, sys, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(ROOT, "data", "cz_posts.csv")
PRICES = ["https://that1618guy.github.io/btc-gt-data/gt_regime.json", os.path.join(ROOT, "docs", "gt_regime.json")]
FIELDS = ["date", "label", "kind", "date_conf", "source"]


def closes():
    """Daily BTC closes {YYYY-MM-DD: price} from the live gt_regime.json, else the local copy."""
    for src in PRICES:
        try:
            if src.startswith("http"):
                with urllib.request.urlopen(urllib.request.Request(src, headers={"User-Agent": "greenface-add-post"}), timeout=20) as r:
                    g = json.load(r)
            else:
                g = json.load(open(src))
            return {row["date"]: row["price"] for row in g["rows"] if row.get("price")}, src
        except Exception as e:  # noqa: BLE001 - fall through to the next source, say which failed
            print(f"  (could not read {src}: {e})", file=sys.stderr)
    return {}, None


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("date"); ap.add_argument("label"); ap.add_argument("kind"); ap.add_argument("source")
    ap.add_argument("--approx", action="store_true", help="the date is approximate")
    ap.add_argument("--dry-run", action="store_true", help="check and preview without writing")
    a = ap.parse_args()

    errs = []
    try:
        day = dt.date.fromisoformat(a.date)
        if day > dt.datetime.now(dt.timezone.utc).date():
            errs.append(f"date {a.date} is in the future")
        if day < dt.date(2020, 1, 1):
            errs.append(f"date {a.date} is before the index's data")
    except ValueError:
        errs.append(f"date must be YYYY-MM-DD, got {a.date!r}")
    label = " ".join(a.label.split())
    if not label:
        errs.append("label is empty")
    if len(label) > 140:
        errs.append(f"label is {len(label)} characters; keep it to 140 so it fits on the page")
    kind = a.kind.strip().lower()
    if kind not in ("hype", "dip"):
        errs.append(f"kind must be hype or dip, got {a.kind!r}")
    src = a.source.strip()
    if not src.startswith("https://") or " " in src:
        errs.append(f"source must be one https:// URL, got {a.source!r}")

    rows = list(csv.DictReader(open(LOG, newline="")))
    if rows and list(rows[0].keys()) != FIELDS:
        errs.append(f"{LOG} columns are {list(rows[0].keys())}, expected {FIELDS}")
    for r in rows:
        if r["date"] == a.date and r["kind"] == kind:
            errs.append(f"a {kind} post on {a.date} is already logged: {r['label']}")
        if r["source"] == src:
            errs.append(f"that source is already logged, for {r['date']}: {r['label']}")
    if errs:
        sys.exit("Not added:\n  " + "\n  ".join(errs))

    new = {"date": a.date, "label": label, "kind": kind, "date_conf": "approx" if a.approx else "exact", "source": src}
    if not a.dry_run:
        with open(LOG, "rb+") as f:  # make sure the file ends with a newline before appending
            f.seek(0, 2)
            if f.tell():
                f.seek(-1, 2)
                if f.read(1) != b"\n":
                    f.write(b"\n")
        with open(LOG, "a", newline="") as f:
            csv.DictWriter(f, FIELDS, lineterminator="\n").writerow(new)
    print(("Checked (dry run, nothing written): " if a.dry_run else "Added to data/cz_posts.csv: ") +
          f"{a.date}  {kind}  {label}")

    # What the hero will show: the latest hype post by date, BTC from its close to the latest close.
    allrows = rows + [new]
    hypes = sorted((r for r in allrows if r["kind"] == "hype"), key=lambda r: r["date"])
    if not hypes:
        print("No hype posts in the log, so the hero has nothing to show.")
        return
    px, used = closes()
    if not px:
        print("Could not read BTC closes, so no preview of the hero.")
        return
    last = max(px)
    if a.date not in px:
        print(f"The {a.date} close is not in the price series yet (latest is {last}). The publisher holds the post "
              "off the page until that close lands, then it appears on its own.")
    shown = [r for r in hypes if r["date"] in px]  # the publisher skips posts whose day has no close
    if not shown:
        print("No hype post has a close yet, so the hero has nothing to show.")
        return
    h = shown[-1]
    if kind == "dip":
        print(f"This is a dip post: it goes on the tape and the record. The hero stays on the latest hype post, {h['date']}.")
    q = h["label"] if h["label"].startswith(('"', "\u201c")) else "\u201c" + h["label"] + "\u201d"
    print(f"\nHero after the next publish (BTC closes from {used}):")
    print(f"  LATEST HYPE POST  {h['date']}  \u00b7  {(dt.date.fromisoformat(last) - dt.date.fromisoformat(h['date'])).days} days ago")
    print(f"  {q}")
    print(f"  BTC since  {(px[last] / px[h['date']] - 1) * 100:+.1f}%   (${px[h['date']]:,.0f} on {h['date']} to ${px[last]:,.0f} on {last})")
    print("\nNext: commit data/cz_posts.csv. The page updates on the next 6-hourly run.")


if __name__ == "__main__":
    main()
