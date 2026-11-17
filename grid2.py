"""Grid search v2 (hold-to-end design).

Windows: dev (kit, exact), race 11-15 (the window v1 failed on),
nextdev 11-16/17 (1340 bars, proxy for the NEXT dev window — the gate
that decides whether v2 races at all), w4/w5/w6 (11-13/12/11).

Dev tool only — the grader never runs this. Window data lives under
data/ in this workdir (local copies, never part of the entry).
"""
import itertools
import json
import statistics
import sys

WORKDIR = "/home/hatch/workspace/makefun/work/btc-race-24h-v2"
sys.path.insert(0, WORKDIR)
import replay

WINDOWS = {}
WINDOWS["dev"] = replay.load_dev_bars()
WINDOWS["race"] = replay.load_coinbase(WORKDIR + "/data/btc-race-window.json", 1794753780)
WINDOWS["w4"] = replay.load_coinbase(WORKDIR + "/data/btc-w4-1113.json", 1794476880)
WINDOWS["w5"] = replay.load_coinbase(WORKDIR + "/data/btc-w5-1112.json", 1794390480)
WINDOWS["w6"] = replay.load_coinbase(WORKDIR + "/data/btc-w6-1111.json", 1794304080)
_raw = json.load(open(WORKDIR + "/data/btc-nextdev.json"))
_nd = []
for c in _raw:
    t_open = int(c[0])
    _nd.append({"t": (t_open + 60) * 1000,
                "o": int(round(float(c[3]) * 10 ** replay.PRICE_SCALE)),
                "h": int(round(float(c[2]) * 10 ** replay.PRICE_SCALE)),
                "l": int(round(float(c[1]) * 10 ** replay.PRICE_SCALE)),
                "c": int(round(float(c[4]) * 10 ** replay.PRICE_SCALE)),
                "o_depth": [replay.DEV_DEPTH_MEDIAN],
                "c_depth": [replay.DEV_DEPTH_MEDIAN]})
_nd.sort(key=lambda b: b["t"])
WINDOWS["nextdev"] = _nd
print("windows:", {k: len(v) for k, v in WINDOWS.items()})

import importlib.util
_spec = importlib.util.spec_from_file_location(
    "botv2", WORKDIR + "/main.py")
bot = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bot)
SHIM = WORKDIR + "/vendor"


def run_all():
    out = {}
    for name, bars in WINDOWS.items():
        h = replay.run_inprocess(bot.DipBot().on_batch, bars, SHIM)
        s = h.summary()
        out[name] = {"pnl": round(s["net_pnl_usd"], 2), "trades": s["trades"],
                     "dd": round(s["max_dd_pct"], 2)}
    return out


def main():
    results = []
    for dip120, dip60, trail, maxhold, fb, reentry in itertools.product(
            [100, 150, 200], [50, 80], [10000, 50], [1440, 720],
            [1080, 1260], [False, True]):
        bot.DIP120_BPS = dip120
        bot.DIP60_BPS = dip60
        bot.TRAIL_BPS = trail
        bot.TRAIL_ARM_BPS = trail
        bot.MAX_HOLD_BARS = maxhold
        bot.FALLBACK_BAR = fb
        bot.HARD_FALLBACK_BAR = min(fb + 300, 1380)
        bot.ALLOW_REENTRY = reentry
        r = run_all()
        hard_fail = (
            r["dev"]["pnl"] <= 0
            or r["nextdev"]["pnl"] <= 0
            or any(r[w]["trades"] == 0 for w in WINDOWS)
            or any(r[w]["dd"] > 25 for w in WINDOWS)
        )
        pnls = [r[w]["pnl"] for w in WINDOWS]
        results.append({
            "params": {"dip120": dip120, "dip60": dip60,
                       "trail": ("off" if trail == 10000 else trail),
                       "maxhold": maxhold, "fallback": fb, "reentry": reentry},
            "hard_fail": hard_fail,
            "worst": round(min(pnls), 2),
            "median": round(statistics.median(pnls), 2),
            "race_pnl": r["race"]["pnl"],
            "windows": r,
        })
    results.sort(key=lambda x: (x["hard_fail"], -x["worst"], -x["race_pnl"]))
    json.dump(results, open(WORKDIR + "/grid2-results.json", "w"), indent=1)
    print("=== TOP 12 by worst-window PnL ===")
    for r in results[:12]:
        print(json.dumps(r))
    surv = [r for r in results if not r["hard_fail"]]
    print(f"survivors: {len(surv)}/{len(results)}")
    print("=== TOP 5 survivors by race PnL ===")
    for r in sorted(surv, key=lambda x: -x["race_pnl"])[:5]:
        print(json.dumps(r))


if __name__ == "__main__":
    main()
