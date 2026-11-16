import json, os, csv, datetime as dt
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

H = os.path.dirname(os.path.abspath(__file__))
R = json.load(open(os.path.join(H, "results.json")))
OUT = H
INK, INK2, GRID, SURF = "#0b0b0b", "#52514e", "#e6e5e1", "#fcfcfb"
S1, S2, S3 = "#2a78d6", "#eb6834", "#1baf7a"
plt.rcParams.update({"font.size": 10, "axes.edgecolor": GRID, "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2,
                     "axes.spines.top": False, "axes.spines.right": False, "figure.facecolor": SURF, "axes.facecolor": SURF,
                     "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6, "axes.axisbelow": True})
ZN = ["green", "crowded", "neutral", "washed", "flushed"]
NAME = {"green": "green face", "crowded": "crowded", "neutral": "neutral", "washed": "washed", "flushed": "flushed"}
D = lambda t: dt.datetime.utcfromtimestamp(t)


def zones():
    fig, axs = plt.subplots(1, 2, figsize=(10, 3.8), sharey=True)
    for ax, sym in zip(axs, ("BTCUSDT", "ETHUSDT")):
        z = R[sym]["composite"]["OOS"]
        vals = [z[k]["w14"] * 100 for k in ZN]
        ax.bar(range(5), vals, color=S1, width=0.6)
        for i, v in enumerate(vals):
            ax.text(i, v - 0.3, f"{v:.1f}%\nn={z[ZN[i]]['n']}", ha="center", va="top", fontsize=8.5, color=INK)
        ax.axhline(z["all"]["w14"] * 100, color=INK2, lw=1, ls="--")
        ax.text(4.4, z["all"]["w14"] * 100 + 0.2, "all days", fontsize=8, color=INK2, ha="right")
        ax.set_xticks(range(5), [NAME[k] for k in ZN]); ax.set_title(f"{sym[:3]}, 2024 to 2026 (out of sample)", color=INK, fontsize=10, loc="left")
        ax.grid(axis="x", visible=False)
    axs[0].set_ylabel("avg worst close, next 14 days (%)")
    axs[0].set_ylim(min(-12, axs[0].get_ylim()[0]), 0.5)
    fig.suptitle("Worst close in the next 14 days, by froth zone", x=0.01, ha="left", color=INK, fontsize=11)
    fig.tight_layout(); fig.savefig(os.path.join(OUT, "zones_oos.png"), dpi=150); plt.close(fig)


def timeline():
    s = R["BTCUSDT"]["series"]
    t = [D(x) for x in s["t"]]
    posts = list(csv.DictReader(open(os.path.join(os.path.dirname(os.path.dirname(H)), "data", "cz_posts.csv"))))
    fig, (a, b) = plt.subplots(2, 1, figsize=(10, 6), sharex=True, gridspec_kw={"height_ratios": [3, 2]})
    a.plot(t, s["c"], color=INK, lw=1.2); a.set_yscale("log"); a.set_ylabel("BTC close (log)")
    ti = {x: i for i, x in enumerate(s["t"])}
    for p in posts:
        d = int(dt.datetime.strptime(p["date"], "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp())
        if d in ti:
            a.plot(D(d), s["c"][ti[d]], "o" if p["kind"] == "hype" else "s", ms=6, mfc=S2 if p["kind"] == "hype" else S1, mec=SURF, mew=1.5)
    a.plot([], [], "o", color=S2, label="hype post"); a.plot([], [], "s", color=S1, label="dip post"); a.legend(frameon=False, loc="upper left")
    c = [float("nan") if x is None else x for x in s["comp"]]
    b.plot(t, c, color=S1, lw=1)
    cuts = R["BTCUSDT"]["composite"]["cuts"]
    for cut, nm in zip(cuts, ["green face", "crowded", "neutral", "washed"]):
        b.axhline(cut, color=INK2, lw=0.7, ls=":"); b.text(t[0], cut + 0.03, nm, fontsize=7.5, color=INK2)
    b.axvline(D(1704067200), color=INK2, lw=0.8); b.text(D(1704067200 + 86400 * 10), b.get_ylim()[1] * 0.85, "out of sample ->", fontsize=8, color=INK2)
    b.set_ylabel("froth composite (z)")
    b.xaxis.set_major_locator(mdates.YearLocator()); b.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    fig.suptitle("BTC and the froth composite, with the 24 seed posts", x=0.01, ha="left", color=INK, fontsize=11)
    fig.tight_layout(); fig.savefig(os.path.join(OUT, "timeline.png"), dpi=150); plt.close(fig)


def rules():
    fig, ax = plt.subplots(figsize=(10, 3.6))
    r = next(x for x in R["BTCUSDT"]["rules"] if x["mode"] == "cz" and x["N"] == 14)
    t = [D(x) for x in r["t"]][:len(r["eq"])]
    ax.plot(t, r["bh"][:len(t)], color=INK2, lw=1.2, label="hold BTC")
    ax.plot(t, r["eq"][:len(t)], color=S1, lw=2, label="step aside 14 days when armed")
    ax.legend(frameon=False, loc="lower left"); ax.set_ylabel("growth of 1")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
    fig.suptitle("Sanity check: the armed rule vs holding (10 bp per side)", x=0.01, ha="left", color=INK, fontsize=11)
    fig.tight_layout(); fig.savefig(os.path.join(OUT, "rule_cz14.png"), dpi=150); plt.close(fig)


def posts():
    ps = R["BTCUSDT"]["cz"]["all"]["posts"]
    fig, ax = plt.subplots(figsize=(10, 4))
    for i, p in enumerate(ps):
        v = p["w14"]
        if v is None: continue
        hype = p["date"] in {x["date"] for x in R["BTCUSDT"]["cz"]["hype"]["posts"]}
        ax.bar(i, v * 100, color=S2 if hype else S1, width=0.7)
    pool = R["BTCUSDT"]["cz"]["hype"]["B1"]["w14_pool"] * 100
    ax.axhline(pool, color=INK2, ls="--", lw=1); ax.text(len(ps) - 0.5, pool - 0.4, "avg, crowded+ days with no post (Dec 2024 on)", ha="right", va="top", fontsize=8, color=INK2)
    ax.set_xticks(range(len(ps)), [p["date"][2:] for p in ps], rotation=60, fontsize=7.5)
    from matplotlib.patches import Patch
    ax.legend(handles=[Patch(color=S2, label="hype"), Patch(color=S1, label="dip")], frameon=False, loc="lower left")
    ax.set_ylabel("worst close, next 14 days (%)")
    fig.suptitle("Each post: worst BTC close in the 14 days after", x=0.01, ha="left", color=INK, fontsize=11)
    fig.tight_layout(); fig.savefig(os.path.join(OUT, "posts_w14.png"), dpi=150); plt.close(fig)


if __name__ == "__main__":
    zones(); timeline(); rules(); posts(); print("charts ok")
