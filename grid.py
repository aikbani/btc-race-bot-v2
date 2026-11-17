"""Grid 1: sweep the tier-1 dip threshold (distance under the 120-bar mean).

Question: how far under its slow average must the close be before we buy?
Each row replays the 15 Nov window with only DEEP_DIP_BPS changed.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import replay

VALUES = [80, 100, 120, 140, 160, 180, 200]


def main():
    slots, first = replay.race_slots()
    print("tier1_bps | net_pnl_usd | trades | max_dd_pct")
    print("----------|-------------|--------|-----------")
    for tier1 in VALUES:
        res = replay.run_with_overrides(slots, first, {"DEEP_DIP_BPS": tier1})
        print(
            f"{tier1:>9} | {res['net_pnl_usd']:>+11.2f} | {res['trades']:>6} | "
            f"{res['max_drawdown'] * 100:>9.2f}"
        )


if __name__ == "__main__":
    main()
