"""Grid search v5: the re-entry gate.

Adds REENTRY_GATE: after an exit the bot is disarmed until a close rises
back above the 60-bar mean. Hypothesis: crash-then-rebound windows
re-arm in time to catch the recovery; fall-and-stay-down windows never
re-arm, so the loss is capped at the first trade.
Dims: UPTICK x DIP60 x DIP120 x TRAIL x REENTRY x GATE.
Fixed: STOP 200, MAX_HOLD 1440, COOLDOWN 60, FALLBACK 1320, HARD 1380.

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
    for uptick, dip60, dip120, trail, reentry, gate in itertools.product(
            [True, False], [60, 80], [120, 200], [50, 100], [True, False],
            ["none", "above_mean60"]):
        bot.REQUIRE_UPTICK = uptick
        bot.DIP60_BPS = dip60
        bot.DIP120_BPS = dip120
        bot.TRAIL_BPS = trail
        bot.TRAIL_ARM_BPS = trail
        bot.ALLOW_REENTRY = reentry
        bot.REENTRY_GATE = gate
        bot.MAX_HOLD_BARS = 1440
        bot.FALLBACK_BAR = 1320
        bot.HARD_FALLBACK_BAR = 1380
        r = run_all()
        hard_fail = (
            r["dev"]["pnl"] <= 0
            or r["nextdev"]["pnl"] <= 0
            or any(r[w]["trades"] == 0 for w in WINDOWS)
            or any(r[w]["dd"] > 25 for w in WINDOWS)
        )
        pnls = [r[w]["pnl"] for w in WINDOWS]
        results.append({
            "params": {"uptick": uptick, "dip60": dip60, "dip120": dip120,
                       "trail": trail, "reentry": reentry, "gate": gate},
            "hard_fail": hard_fail,
            "worst": round(min(pnls), 2),
            "median": round(statistics.median(pnls), 2),
            "race_pnl": r["race"]["pnl"],
            "windows": r,
        })
    results.sort(key=lambda x: (x["hard_fail"], -x["worst"], -x["race_pnl"]))
    json.dump(results, open(WORKDIR + "/grid5-results.json", "w"), indent=1)
    print("=== TOP 12 by worst-window PnL ===")
    for r in results[:12]:
        print(json.dumps(r))
    surv = [r for r in results if not r["hard_fail"]]
    print(f"survivors: {len(surv)}/{len(results)}")
    print("=== TOP 8 survivors by race PnL ===")
    for r in sorted(surv, key=lambda x: -x["race_pnl"])[:8]:
        print(json.dumps(r))
    print("=== BEST race PnL overall ===")
    for r in sorted(results, key=lambda x: -x["race_pnl"])[:5]:
        print(json.dumps(r))


if __name__ == "__main__":
    main()
