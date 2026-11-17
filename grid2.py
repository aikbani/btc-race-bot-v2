"""Grid 2: sweep the tier-2 dip threshold against the trailing-stop width.

Question: does a shallower dip (distance under the 60-bar mean) paired with a
wider or narrower trail buy better? Each row replays the 15 Nov window with
only SHALLOW_DIP_BPS and TRAIL_BPS changed.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import replay

TIER2_VALUES = [60, 80, 100, 120]
TRAIL_VALUES = [30, 50, 80, 120]


def main():
    slots, first = replay.race_slots()
    print("tier2_bps | trail_bps | net_pnl_usd | trades | max_dd_pct")
    print("----------|-----------|-------------|--------|-----------")
    for tier2 in TIER2_VALUES:
        for trail in TRAIL_VALUES:
            res = replay.run_with_overrides(
                slots, first, {"SHALLOW_DIP_BPS": tier2, "TRAIL_BPS": trail}
            )
            print(
                f"{tier2:>9} | {trail:>9} | {res['net_pnl_usd']:>+11.2f} | "
                f"{res['trades']:>6} | {res['max_drawdown'] * 100:>9.2f}"
            )


if __name__ == "__main__":
    main()
