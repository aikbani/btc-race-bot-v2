"""Final verification of the shipped v2 constants via the SUBPROCESS
mode of replay.py (the real NDJSON protocol, exactly as the server runs
it). Two runs per window; results must be byte-identical.
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
WINDOWS["w4"] = replay.load_coinbase(
    "/home/hatch/workspace/makefun/work/btc-race-24h-v2/data/btc-w4-1113.json",
    1794476880)
WINDOWS["w5"] = replay.load_coinbase(
    "/home/hatch/workspace/makefun/work/btc-race-24h-v2/data/btc-w5-1112.json",
    1794390480)
WINDOWS["w6"] = replay.load_coinbase(
    "/home/hatch/workspace/makefun/work/btc-race-24h-v2/data/btc-w6-1111.json",
    1794304080)
_raw = json.load(open("/home/hatch/workspace/makefun/work/btc-race-24h-v2/data/btc-nextdev.json"))
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

BOT = "/home/hatch/workspace/makefun/work/btc-race-24h-v2/main.py"

out = {}
for name, bars in WINDOWS.items():
    h1 = replay.run_subprocess(BOT, bars)
    h2 = replay.run_subprocess(BOT, bars)
    s1, s2 = h1.summary(), h2.summary()
    identical = (s1 == s2)
    out[name] = {"run1": s1, "run2": s2, "identical": identical}
    print(name, json.dumps(s1), "identical:", identical)
json.dump(out, open("/home/hatch/workspace/makefun/work/btc-race-24h-v2/verify-final.json", "w"), indent=1)
print("ALL IDENTICAL:", all(v["identical"] for v in out.values()))
