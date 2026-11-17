"""Grid 4: sweep the late-window fallback bars.

Question: when should the bot stop waiting for a textbook dip and buy anyway
if it has not traded yet (first on an uptick, then regardless)? Each row
replays the 15 Nov window with only LATE_BUY_BAR and FORCE_BUY_BAR changed.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import replay

LATE_VALUES = [1200, 1260, 1320, 1380]
FORCE_VALUES = [1350, 1380, 1410]


def main():
    slots, first = replay.race_slots()
    print("late_bar | force_bar | net_pnl_usd | trades | max_dd_pct")
    print("---------|-----------|-------------|--------|-----------")
    for late in LATE_VALUES:
        for force in FORCE_VALUES:
            res = replay.run_with_overrides(
                slots, first, {"LATE_BUY_BAR": late, "FORCE_BUY_BAR": force}
            )
            print(
                f"{late:>8} | {force:>9} | {res['net_pnl_usd']:>+11.2f} | "
                f"{res['trades']:>6} | {res['max_drawdown'] * 100:>9.2f}"
            )


if __name__ == "__main__":
    main()
