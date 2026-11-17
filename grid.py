"""Grid search for v2 parameters.

Runs the REAL v2 main.py (via the in-process replay driver, which
reproduces v1's official dev grade to the micro-USDC) over 6 windows:
  dev   - kit dev bars, exact depths (the dev gate: PnL must be > $0)
  race  - 2026-11-15 race window, Coinbase BTC-USD proxy (the window v1
          failed on: 0 trades, $0)
  w3    - 2026-11-16 14:43Z -> now, PARTIAL 896 bars (proxy for the NEXT
          dev window, which ends at the next race's opening)
  w4/w5/w6 - full 24h windows 11-13 / 11-12 / 11-11 Nov (Coinbase proxy)

Selection: hard fail if dev PnL <= 0, any full window has 0 trades, or
any max DD > 25%. Among survivors rank by worst PnL across the six
windows (maximin), then by median PnL. The point is robustness on an
UNSEEN window, not the best number on any single one.
"""
import itertools
import json
import statistics
import sys

sys.path.insert(0, "/home/hatch/workspace/makefun/work/btc-race-24h-v2")
import replay

# --- load windows once ------------------------------------------------------
WINDOWS = {}
WINDOWS["dev"] = replay.load_dev_bars()
WINDOWS["race"] = replay.load_coinbase("/tmp/btc-race-window.json", 1794753780)
WINDOWS["w4"] = replay.load_coinbase("/tmp/btc-w4-1113.json", 1794476880)
WINDOWS["w5"] = replay.load_coinbase("/tmp/btc-w5-1112.json", 1794390480)
WINDOWS["w6"] = replay.load_coinbase("/tmp/btc-w6-1111.json", 1794304080)
# w3 partial: 896 bars
_raw = json.load(open("/tmp/btc-w3-1116-partial.json"))
_w3 = []
for c in _raw:
    t_open = int(c[0])
    _w3.append({"t": (t_open + 60) * 1000,
                "o": int(round(float(c[3]) * 10 ** replay.PRICE_SCALE)),
                "h": int(round(float(c[2]) * 10 ** replay.PRICE_SCALE)),
                "l": int(round(float(c[1]) * 10 ** replay.PRICE_SCALE)),
                "c": int(round(float(c[4]) * 10 ** replay.PRICE_SCALE)),
                "o_depth": [replay.DEV_DEPTH_MEDIAN],
                "c_depth": [replay.DEV_DEPTH_MEDIAN]})
_w3.sort(key=lambda b: b["t"])
WINDOWS["w3"] = _w3

# --- import the bot module (constants patched per combo) --------------------
import importlib.util
_spec = importlib.util.spec_from_file_location(
    "botv2", "/home/hatch/workspace/makefun/work/btc-race-24h-v2/main.py")
bot = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bot)
SHIM = "/home/hatch/workspace/makefun/work/btc-race-24h-v2/vendor"


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
    combos = list(itertools.product(
        [100, 150, 200],      # DIP120
        [40, 60, 80],         # DIP60
        [50, 75],             # TRAIL (arm = trail)
        [240, 720, 1200],     # MAX_HOLD
        [720, 1080, 1260],    # FALLBACK_BAR
    ))
    for dip120, dip60, trail, maxhold, fb in combos:
        bot.DIP120_BPS = dip120
        bot.DIP60_BPS = dip60
        bot.TRAIL_BPS = trail
        bot.TRAIL_ARM_BPS = trail
        bot.MAX_HOLD_BARS = maxhold
        bot.FALLBACK_BAR = fb
        bot.HARD_FALLBACK_BAR = min(fb + 300, 1380)
        r = run_all()
        full = ["dev", "race", "w4", "w5", "w6"]  # w3 is partial, not a hard gate
        hard_fail = (
            r["dev"]["pnl"] <= 0
            or any(r[w]["trades"] == 0 for w in full)
            or any(r[w]["dd"] > 25 for w in WINDOWS)
        )
        pnls = [r[w]["pnl"] for w in WINDOWS]
        results.append({
            "params": {"dip120": dip120, "dip60": dip60, "trail": trail,
                       "maxhold": maxhold, "fallback": fb},
            "hard_fail": hard_fail,
            "worst": round(min(pnls), 2),
            "median": round(statistics.median(pnls), 2),
            "windows": r,
        })
    results.sort(key=lambda x: (x["hard_fail"], -x["worst"], -x["median"]))
    json.dump(results, open("/home/hatch/workspace/makefun/work/btc-race-24h-v2/grid-results.json", "w"), indent=1)
    print("=== TOP 12 (survivors first, by worst-window PnL) ===")
    for r in results[:12]:
        print(json.dumps(r))
    print("=== DEFAULT PARAMS (120/60/50/1200/1080) ===")
    for r in results:
        p = r["params"]
        if p == {"dip120": 120, "dip60": 60, "trail": 50, "maxhold": 1200, "fallback": 1080}:
            print(json.dumps(r))
            break


if __name__ == "__main__":
    main()
