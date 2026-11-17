"""Grid 3: sweep the hard stop against the maximum hold.

Question: how much room should a trade get (stop distance under the entry)
and how long may it sit (bars before we give up)? Each row replays the
15 Nov window with only STOP_BPS and MAX_HOLD_BARS changed.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import replay

STOP_VALUES = [100, 150, 200, 300]
HOLD_VALUES = [360, 720, 1440]


def main():
    slots, first = replay.race_slots()
    print("stop_bps | max_hold_bars | net_pnl_usd | trades | max_dd_pct")
    print("---------|---------------|-------------|--------|-----------")
    for stop in STOP_VALUES:
        for hold in HOLD_VALUES:
            res = replay.run_with_overrides(
                slots, first, {"STOP_BPS": stop, "MAX_HOLD_BARS": hold}
            )
            print(
                f"{stop:>8} | {hold:>13} | {res['net_pnl_usd']:>+11.2f} | "
                f"{res['trades']:>6} | {res['max_drawdown'] * 100:>9.2f}"
            )


if __name__ == "__main__":
    main()
