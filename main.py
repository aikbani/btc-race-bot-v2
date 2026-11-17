"""A dip-buying bot for the cbBTC 24-hour race (make.fun, agent-fun/bars-replay/v1).

The idea is deliberately boring: sit in cash, wait for Bitcoin to sag below its
own recent average, buy the dip in full size, and get out on a stop, a trailing
stop once the trade has run in our favour, or old age. One position at a time,
long only, no leverage, no guessing at anything the protocol does not hand us.

Everything is integer math on the prices the harness sends (CBBTC closes are
integers at price_scale 5, money is integer micro-USDC). Nothing here reads the
clock, the network or the filesystem, so two runs on the same bars answer the
same way, byte for byte.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))

import makefun_bars

SYMBOL = "CBBTC"

# --- What counts as a dip, and how we leave ---------------------------------
# How many closes go into each of the two averages we compare the price with.
SLOW_MEAN_BARS = 120
FAST_MEAN_BARS = 60
# A buy needs the close this far under an average (basis points).
DEEP_DIP_BPS = 120
SHALLOW_DIP_BPS = 80
# Only buy on a bar that closed above the one before it.
REQUIRE_UPTICK = True
# Once the trade is up this far, a trailing stop starts watching from the peak.
TRAIL_ARM_BPS = 50
# ... and the trail itself hangs this far below the best close since entry.
TRAIL_BPS = 50
# The hard stop: this far under the entry price, we are out, no discussion.
STOP_BPS = 200
# Longest we will babysit one position, in bars (a full window, effectively).
MAX_HOLD_BARS = 1440
# Bars to sit out after an exit before we may buy again.
COOLDOWN_BARS = 60
# Re-entering after an exit is allowed at all.
ALLOW_REENTRY = True
# Re-entries only count once the close has climbed back over the fast average.
# (Any other value here switches that gate off.)
GATE_MODE = "above_fast_mean"
# Late in the window, if we still have not traded at all, buy the first uptick
# instead of waiting for a textbook dip ...
LATE_BUY_BAR = 1320
# ... and a little later, buy regardless, so the window is never a blank.
FORCE_BUY_BAR = 1380
# The most the fill may move against the close we decided on.
MAX_SLIPPAGE_BPS = 200

BPS = 10000


class DipBot:
    """Keeps the closes we have seen and the state of one trade at a time."""

    def __init__(self):
        # Closes of CBBTC, oldest first, in the harness's integer price units.
        self.closes = []
        # Where the open position came in, and the best close since then.
        self.entry_price = None
        self.best_close = None
        # Batch numbers (seq) of the fill that opened us and of our last exit.
        self.entry_seq = None
        self.exit_seq = None
        # Whether any buy / sell of ours has ever filled.
        self.bought_once = False
        self.sold_once = False
        # Re-entry is armed from the start; an exit disarms it until the gate
        # (close back over the fast average) re-arms it.
        self.reentry_armed = True

    def _remember_fills(self, batch):
        for fill in batch.get("fills", []):
            if fill.get("status") != "filled":
                continue
            if fill.get("side") == "buy":
                self.bought_once = True
            elif fill.get("side") == "sell":
                self.sold_once = True

    def _recover_entry(self, state, batch, holding):
        """Work out the entry price of a position we hold but cannot price.

        First choice is the buy fill that arrived with this batch; failing
        that, back it out of the account's cost basis for the position.
        """
        fill_price = None
        for fill in batch.get("fills", []):
            if (
                fill.get("status") == "filled"
                and fill.get("side") == "buy"
                and fill.get("symbol") == SYMBOL
            ):
                fill_price = int(fill["price"])
                break
        if fill_price is not None:
            self.entry_price = fill_price
            self.entry_seq = state.seq
            latest = state.close(SYMBOL)
            self.best_close = latest if latest is not None else fill_price
            return
        spec = state.symbols[SYMBOL]
        quote_scale = state.hello["capital"]["quote_scale"]
        self.entry_price = (
            holding.cost
            * 10 ** (spec["qty_scale"] + spec["price_scale"])
            // (holding.qty * 10 ** quote_scale)
        )
        self.entry_seq = state.seq
        self.best_close = state.close(SYMBOL)

    def on_batch(self, state, batch):
        self._remember_fills(batch)
        if state.warming_up:
            return []
        # Only the bar this batch actually carries counts; on a gap minute the
        # symbol is absent and there is nothing to decide on.
        ours = None
        for bar in batch.get("bars", []):
            if bar.get("symbol") == SYMBOL:
                ours = bar
                break
        if ours is None:
            return []
        self.closes.append(int(ours["c"]))

        holding = state.positions.get(SYMBOL)
        if holding is not None and self.entry_price is None:
            self._recover_entry(state, batch, holding)

        # An average needs its full lookback, plus one bar to see an uptick.
        if len(self.closes) < SLOW_MEAN_BARS + 1:
            return []

        now = state.close(SYMBOL)

        if holding is None:
            # Flat: nothing to protect, so forget the last trade's prices.
            self.entry_price = None
            self.best_close = None
            self.entry_seq = None
            if self.exit_seq is not None and not ALLOW_REENTRY:
                return []
            if (
                self.exit_seq is not None
                and state.seq - self.exit_seq < COOLDOWN_BARS
            ):
                return []
            uptick = now > self.closes[-2]
            slow_mean = sum(self.closes[-SLOW_MEAN_BARS:]) // SLOW_MEAN_BARS
            fast_mean = sum(self.closes[-FAST_MEAN_BARS:]) // FAST_MEAN_BARS
            under_slow = (now - slow_mean) * BPS // slow_mean
            under_fast = (now - fast_mean) * BPS // fast_mean
            turning = uptick or not REQUIRE_UPTICK
            if GATE_MODE == "above_fast_mean" and now > fast_mean:
                self.reentry_armed = True
            dip_ok = turning and (
                self.reentry_armed
                or GATE_MODE != "above_fast_mean"
                or self.exit_seq is None
            )
            traded_before = self.bought_once or self.sold_once
            want_buy = False
            if under_slow <= -DEEP_DIP_BPS and dip_ok:
                want_buy = True
            elif under_fast <= -SHALLOW_DIP_BPS and dip_ok:
                want_buy = True
            elif not traded_before and state.seq >= LATE_BUY_BAR and uptick:
                want_buy = True
            elif not traded_before and state.seq >= FORCE_BUY_BAR:
                want_buy = True
            if not want_buy:
                return []
            size = min(state.cash, state.weight_room(SYMBOL))
            smallest = int(state.hello["fees"]["min_notional_quote_units"])
            if size < smallest:
                return []
            return [
                {
                    "side": "buy",
                    "symbol": SYMBOL,
                    "notional_quote_units": size,
                    "max_slippage_bps": MAX_SLIPPAGE_BPS,
                }
            ]

        # Holding: defend the position.
        if self.entry_price is None:
            return []
        if now > self.best_close:
            self.best_close = now
        stop_price = self.entry_price * (BPS - STOP_BPS) // BPS
        arm_price = self.entry_price * (BPS + TRAIL_ARM_BPS) // BPS
        trail_price = self.best_close * (BPS - TRAIL_BPS) // BPS
        too_old = (
            self.entry_seq is not None
            and state.seq - self.entry_seq >= MAX_HOLD_BARS
        )
        if (
            now <= stop_price
            or (self.best_close >= arm_price and now <= trail_price)
            or too_old
        ):
            self.exit_seq = state.seq
            self.reentry_armed = False
            return [
                {
                    "side": "sell",
                    "symbol": SYMBOL,
                    "qty": holding.qty,
                    "max_slippage_bps": MAX_SLIPPAGE_BPS,
                }
            ]
        return []


def main():
    bot = DipBot()
    makefun_bars.run(bot.on_batch)


if __name__ == "__main__":
    main()
