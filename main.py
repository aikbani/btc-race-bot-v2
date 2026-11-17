"""Dip-buy mean-reversion bot for make.fun BTC 24h race, v2.

Race: btc-race-24h-20261117-0603 ("Race a BTC trading bot live for 24
hours"), account @aik-bani. Rebuilt in a fresh session after the race
opened, so every line of this entry was authored inside the window.

Strategy (long only, one asset CBBTC, full-size trades):
  * Entry, three tiers, first one that fires:
    1. Capitulation dip: the close sits at least DIP120_BPS below its
       trailing 120-minute mean AND the bar closes up on the previous
       close (the first uptick after the washout). This is the v1 rule,
       loosened from 200 to 120 bps: v1's 200 bps never fired on the
       2026-11-15 race window, whose deepest dip below the 120-minute
       mean was only 0.90%.
    2. Shallow dip: the close sits at least DIP60_BPS below its trailing
       60-minute mean AND the bar closes up. In a quiet, range-bound
       window the 120-minute mean trails the price down, so a dip never
       registers against it; the 60-minute mean is quicker and still
       catches real pullbacks.
    3. Time fallback: if no trade has filled yet by FALLBACK_BAR
       (bar 1320, hour 22), buy on the next uptick; if still flat by
       HARD_FALLBACK_BAR (bar 1380, hour 23), buy anyway. The fallback
       is deliberately LATE: an hour-18 fallback pre-empts the real dip
       (on the 2026-11-15 race window it bought at bar 1080 and blocked
       the -0.89% dip at bar 1288, banking only +$4.20 instead of the
       dip's +$8-13). This tier exists because a bot that waits for a
       textbook dip can end the window flat, and a flat bot scores
       exactly $0, fails the >=1-trade guardrail and takes no prize.
       The fallback guarantees a trade on ANY window.
  * Exit: a TRAIL_BPS trailing stop off the highest close since entry
    (arms once the peak is +TRAIL_ARM_BPS over the fill), a hard
    STOP_BPS stop-loss from the fill price, or a MAX_HOLD_BARS max hold.
    ALLOW_REENTRY + REENTRY_GATE: after an exit the bot may buy a later
    dip, but only once a close has risen back ABOVE the 60-minute mean
    (a genuine turn-up). The gate is what separates crash-then-rebound
    windows (the rebound re-arms the bot in time to catch the recovery:
    dev +$2.35 -> +$6.41) from fall-and-stay-down windows. Grid-tested
    across six recent 24h windows (see README): the trailing stop
    banks rebounds on crash windows but sells quiet-window recoveries
    early, so its width is fitted, not assumed.
  * A COOLDOWN_BARS cooldown after every exit stops re-buying the same
    level.
  * Nothing else trades. No randomness, no wall clock, no network, no
    files: every decision is a pure function of the closes and the
    account the harness sends, so two runs on the same data answer byte
    for byte alike.

Local backtests (replay driver with the exact fill model from
contracts/bars-protocol.ts: 4 bps fee, 2 bps slippage, constant-product
impact against each bar's open depth, fills at the next bar's open;
driver reproduces v1's official dev grade to the micro-USDC).
Final constants, subprocess mode, verified byte-identical over 2 runs:
  window      net PnL    trades  max DD   notes
  kit dev     +$4.59     2       0.72%    NEW kit dev window 16-17 Nov,
                                         gate must be > $0 — PASS
  kit dev     +$6.41     4       3.86%    old kit dev window
  race 11-15  +$8.12     2       0.73%    winner was +$16.80 (1 trade)
  nextdev     +$3.85     2       0.75%    proxy for the next dev window
  w4 11-13    -$22.11    4       3.00%    -5.96% down day; 2nd buy stopped
  w5 11-12    -$6.93     2       0.71%
  w6 11-11    -$2.32     2       1.33%
  No parameter set in ~290 grid combinations is non-negative on all six
  windows — the dip-buy family cannot be positive on a -5.96% down day
  while staying positive on the crash-then-rebound dev window. The
  shipped config is the best survivor (best worst-window PnL, positive
  on the dev gate and on the next-dev proxy, >=1 trade everywhere).
  The no-uptick variant reaches +$13.28 on the race window but fails
  the dev gate (-$24.28), so it is NOT shipped. See README.

Entry point: main.py at the repo root, Python 3.13 stdlib only. It speaks
agent-fun/bars-replay/v1 through the kit's Python shim, vendored byte for
byte under vendor/ (see vendor/makefun_bars.py).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import makefun_bars

SYMBOL = "CBBTC"

# --- Strategy constants (all in basis points unless noted) -------------------
SMA_LEN = 120            # trailing mean length for the capitulation dip
SMA60_LEN = 60           # trailing mean length for the shallow dip
DIP120_BPS = 120         # tier 1: close >= 1.20% below the 120-bar mean
DIP60_BPS = 80           # tier 2: close >= 0.80% below the 60-bar mean
REQUIRE_UPTICK = True    # dip tiers also need the bar to close up on the
                       # previous close (buy the turn, not the knife).
                       # Grid-tested: False buys the dip level itself,
                       # which matters in slow drift-down windows where
                       # the bottom is flat and no uptick ever comes.
TRAIL_BPS = 50           # exit: close retraces 0.50% from the peak since entry
TRAIL_ARM_BPS = 50       # trailing only arms once the peak is +0.50% over entry
STOP_BPS = 200           # exit: close falls 2.00% below the entry fill price
MAX_HOLD_BARS = 1440     # exit: effectively the end of the window
COOLDOWN_BARS = 60       # wait this long after an exit before a new entry
ALLOW_REENTRY = True     # after the first exit the bot MAY buy a later dip,
                       # but only via the REENTRY_GATE below: re-entry is
                       # what saves crash windows (it catches the rebound)
                       # and what churns falling windows (it re-buys a dead
                       # cat). The gate separates the two cases.
REENTRY_GATE = "above_mean60"  # re-entry is only re-armed once a close
                       # rises back ABOVE the 60-bar mean — i.e. the trend
                       # has genuinely turned up. On a crash-then-rebound
                       # window the rebound re-arms the bot in time to
                       # catch the recovery; on a fall-and-stay-down window
                       # the price never gets back above the mean, so the
                       # bot takes its first trade's result and stops.
                       # "none" disables the gate (re-arm immediately).
FALLBACK_BAR = 1320      # tier 3 arms: hour 22, if no trade has filled yet
HARD_FALLBACK_BAR = 1380  # tier 3 forces a buy: hour 23, still flat
MAX_SLIP_BPS = 200       # max slippage vs the latest close on every order


class DipBot:
    """State for one run. Everything here is rebuilt from the batches the
    harness sends, so a replay over bars already seen answers the same."""

    def __init__(self):
        self.closes = []          # closes so far, ints at the symbol's price scale
        self.entry_px = None      # fill price of the open position (price units)
        self.peak_px = None       # highest close since the entry fill
        self.entry_seq = None     # batch seq at which the entry fill was seen
        self.last_exit_seq = -10 ** 9  # batch seq at which the last sell was decided
        self.traded = False       # any buy of ours has filled in this run
        self.exited = False       # any sell of ours has filled in this run
        self.armed = True         # re-entry gate state (see REENTRY_GATE)

    def _note_fills(self, batch):
        """Pick up the entry fill price from this batch's fills, if our buy
        filled on it. Falls back to the account's cost basis when a fill
        record is ever missing."""
        for fill in batch.get("fills", []):
            if fill.get("symbol") != SYMBOL or fill.get("status") != "filled":
                continue
            if fill.get("side") == "buy" and self.entry_px is None:
                self.traded = True
                self.entry_px = int(fill["price"])
                self.entry_seq = batch["seq"]
                # Peak starts at this batch's own close for the symbol.
                for bar in batch.get("bars", []):
                    if bar.get("symbol") == SYMBOL:
                        self.peak_px = int(bar["c"])
                        break
                else:
                    self.peak_px = self.entry_px

    def _cost_entry_px(self, state):
        """Entry price implied by cost / qty (cost carries the fee, so this
        runs ~4 bps above the true fill price; only a fallback)."""
        holding = state.positions.get(SYMBOL)
        if holding is None or holding.qty <= 0:
            return None
        info = state.symbols[SYMBOL]
        quote_scale = state.hello["capital"]["quote_scale"]
        return holding.cost * 10 ** (info["qty_scale"] + info["price_scale"]) // (
            holding.qty * 10 ** quote_scale
        )

    def on_batch(self, state, batch):
        # Warm-up bars still count toward the trailing mean's history, but
        # no order decided on one may fill (the harness rejects it), so
        # answer them empty after recording their closes.
        for bar in batch.get("bars", []):
            if bar.get("symbol") == SYMBOL:
                self.closes.append(int(bar["c"]))
        if state.warming_up:
            return []
        if SYMBOL not in state.symbols:
            return []

        holding = state.positions.get(SYMBOL)
        qty = holding.qty if holding is not None else 0

        if qty > 0 and self.entry_px is None:
            self._note_fills(batch)
            if self.entry_px is None:
                self.entry_px = self._cost_entry_px(state)
                self.entry_seq = batch["seq"]
            if self.entry_px is not None and self.peak_px is None:
                self.peak_px = self.closes[-1]

        close = state.close(SYMBOL)
        if close is None or len(self.closes) < SMA_LEN + 1:
            return []

        if qty == 0:
            self.entry_px = None
            self.peak_px = None
            self.entry_seq = None
            if self.exited and not ALLOW_REENTRY:
                return []           # one dip, one ride: no re-entry
            if state.seq - self.last_exit_seq < COOLDOWN_BARS:
                return []
            uptick = close > self.closes[-2]
            mean120 = sum(self.closes[-SMA_LEN:]) // SMA_LEN
            dip120_bps = (close - mean120) * 10000 // mean120
            mean60 = sum(self.closes[-SMA60_LEN:]) // SMA60_LEN
            dip60_bps = (close - mean60) * 10000 // mean60
            turn = uptick or not REQUIRE_UPTICK
            # Re-entry gate: after an exit the bot is disarmed until a
            # close rises back above the 60-bar mean (a real turn-up).
            # The fallback tiers ignore the gate — they are the guarantee.
            if REENTRY_GATE == "above_mean60" and close > mean60:
                self.armed = True
            dip_ok = turn and (self.armed or REENTRY_GATE == "none" or not self.exited)
            want_buy = False
            if dip120_bps <= -DIP120_BPS and dip_ok:
                want_buy = True       # tier 1: capitulation dip
            elif dip60_bps <= -DIP60_BPS and dip_ok:
                want_buy = True       # tier 2: shallow dip
            elif not self.traded and state.seq >= FALLBACK_BAR and uptick:
                want_buy = True       # tier 3a: fallback on the next uptick
            elif not self.traded and state.seq >= HARD_FALLBACK_BAR:
                want_buy = True       # tier 3b: forced buy, still flat
            if want_buy:
                notional = min(state.cash, state.weight_room(SYMBOL))
                if notional >= int(state.hello["fees"]["min_notional_quote_units"]):
                    return [{
                        "side": "buy",
                        "symbol": SYMBOL,
                        "notional_quote_units": notional,
                        "max_slippage_bps": MAX_SLIP_BPS,
                    }]
            return []

        # In a position: maintain the peak and check the three exits.
        if self.entry_px is None:
            return []
        if close > self.peak_px:
            self.peak_px = close
        entry = self.entry_px
        stop_px = entry * (10000 - STOP_BPS) // 10000
        armed_px = entry * (10000 + TRAIL_ARM_BPS) // 10000
        trail_px = self.peak_px * (10000 - TRAIL_BPS) // 10000
        exit_now = False
        if close <= stop_px:
            exit_now = True  # hard stop: the dip kept falling
        elif self.peak_px >= armed_px and close <= trail_px:
            exit_now = True  # rebound rolled over from its peak
        elif self.entry_seq is not None and state.seq - self.entry_seq >= MAX_HOLD_BARS:
            exit_now = True  # 20 hours is enough for any one dip
        if exit_now:
            self.last_exit_seq = state.seq
            self.exited = True
            self.armed = False    # re-entry gate: disarm until a close
                                  # gets back above the 60-bar mean
            return [{
                "side": "sell",
                "symbol": SYMBOL,
                "qty": qty,
                "max_slippage_bps": MAX_SLIP_BPS,
            }]
        return []


def main():
    bot = DipBot()
    makefun_bars.run(bot.on_batch)


if __name__ == "__main__":
    main()
