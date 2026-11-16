import json, os, sys
R = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "results.json")))
ZN = ["green", "frothy", "neutral", "washed", "flushed", "all"]
pc = lambda x: "—" if x is None else f"{x*100:+.1f}%"


def ztable(z):
    s = "| zone | days | 1d | 3d | 7d | 14d | 30d | 14d worst close | odds of -10% in 14d |\n|---|---|---|---|---|---|---|---|---|\n"
    for k in ZN:
        v = z.get(k, {})
        if not v.get("n"): s += f"| {k} | 0 | | | | | | | |\n"; continue
        s += f"| {k} | {v['n']} | {pc(v['r1'])} | {pc(v['r3'])} | {pc(v['r7'])} | {pc(v['r14'])} | {pc(v['r30'])} | {pc(v['w14'])} | {v['p10']*100:.0f}% |\n"
    return s


def test(t):
    if not t: return "n/a"
    return (f"frothy+ minus neutral, 14d worst close: {pc(t['diff'])} (90% CI {pc(t['ci'][0])} to {pc(t['ci'][1])}), "
            f"block-bootstrap p={t['p_block']:.3f}, non-overlapping p (median of 14 offsets)={'n/a' if t['p_nonoverlap_median'] is None else format(t['p_nonoverlap_median'],'.3f')}; "
            f"{t['n_hot']} frothy+ days vs {t['n_cold']} neutral")


if __name__ == "__main__":
    for sym in ("BTCUSDT", "ETHUSDT"):
        S = R[sym]
        print("#", sym, "start", S["composite"]["start"], "cuts", [round(c, 3) for c in S["composite"]["cuts"]], "fund rec days", S["fund_rec_days"])
        print("IS\n" + ztable(S["composite"]["IS"]))
        print("OOS\n" + ztable(S["composite"]["OOS"]))
        print("IS test:", test(S["composite"]["test_IS"]))
        print("OOS test:", test(S["composite"]["test_OOS"]))
        print("OOS green-only test:", test(S["composite"]["test_OOS_green"]))
        print("fixed cuts OOS:\n" + ztable(S["fixed_cuts"]["OOS"]))
        for k in S:
            if k.startswith(("alone", "drop", "shift")):
                print(k, "OOS:", test(S[k]["test_OOS"]), "| IS:", test(S[k]["test_IS"]))
        if "periods" in S:
            for k, v in S["periods"].items():
                print("period", k, test(v["test"]))
                print(ztable(v["zones"]))
