"""Final verification of the shipped v2 constants via the SUBPROCESS
mode of replay.py (the real NDJSON protocol, exactly as the server runs
it). Two runs per window; results must be byte-identical.

Windows:
  dev  — the NEW kit's dev bars (16 Nov 13:04 -> 17 Nov 13:03 WIB,
         1440 bars, exact depths). The dev gate: net PnL must be > $0.
         Expected: +$4.59, 2 trades, max DD 0.72%.
  race — the 2026-11-15 race window (local sanity copy), expected
         +$8.12, 2 trades, max DD 0.73%.
"""
import json
import sys

sys.path.insert(0, "/home/hatch/workspace/makefun/work/btc-race-24h-v2")
import replay

WINDOWS = {}
WINDOWS["dev"] = replay.load_dev_bars()
WINDOWS["race"] = replay.load_coinbase(
    "/home/hatch/workspace/makefun/work/btc-race-24h-v2/data/btc-race-window.json",
    1794753780)

BOT = "/home/hatch/workspace/makefun/work/btc-race-24h-v2/main.py"

EXPECTED = {
    "dev": {"net_pnl_usd": 4.59, "trades": 2},
    "race": {"net_pnl_usd": 8.12, "trades": 2},
}

out = {}
ok = True
for name, bars in WINDOWS.items():
    h1 = replay.run_subprocess(BOT, bars)
    h2 = replay.run_subprocess(BOT, bars)
    s1, s2 = h1.summary(), h2.summary()
    identical = (s1 == s2)
    exp = EXPECTED[name]
    matches = (abs(s1["net_pnl_usd"] - exp["net_pnl_usd"]) < 0.01
               and s1["trades"] == exp["trades"])
    print(name, json.dumps(s1), "identical:", identical, "expected:", exp,
          "MATCH" if matches else "MISMATCH")
    out[name] = {"run1": s1, "run2": s2, "identical": identical,
                 "expected": exp, "matches_expected": matches}
    ok = ok and identical and matches
print("ALL IDENTICAL + MATCH EXPECTED:", ok)
if not ok:
    sys.exit(1)
