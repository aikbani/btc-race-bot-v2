"""Grid 5: sweep the uptick requirement against the re-entry gate.

Question: must a dip buy land on a bar that closed above the previous one,
and must a re-entry wait for the close to climb back over the 60-bar mean?
Each row replays the 15 Nov window with only REQUIRE_UPTICK and GATE_MODE
changed ("off" switches the gate off entirely).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import replay

UPTICK_VALUES = [True, False]
GATE_VALUES = ["above_fast_mean", "off"]


def main():
    slots, first = replay.race_slots()
    print("require_uptick | gate_mode       | net_pnl_usd | trades | max_dd_pct")
    print("---------------|-----------------|-------------|--------|-----------")
    for uptick in UPTICK_VALUES:
        for gate in GATE_VALUES:
            res = replay.run_with_overrides(
                slots, first, {"REQUIRE_UPTICK": uptick, "GATE_MODE": gate}
            )
            print(
                f"{str(uptick):>14} | {gate:<15} | {res['net_pnl_usd']:>+11.2f} | "
                f"{res['trades']:>6} | {res['max_drawdown'] * 100:>9.2f}"
            )


if __name__ == "__main__":
    main()
