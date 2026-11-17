# CBBTC Dip Bot — BTC Race 24h

[![make.fun · btc-race-24h-20261117-0603](https://make.fun/badge/btc-race-24h-20261117-0603.svg)](https://make.fun/bounty/btc-race-24h-20261117-0603?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261117-0603)
[![rank](https://make.fun/badge/btc-race-24h-20261117-0603/aikbani/btc-race-bot-v2/rank.svg)](https://make.fun/bounty/btc-race-24h-20261117-0603/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261117-0603)
[![reward](https://make.fun/badge/btc-race-24h-20261117-0603/aikbani/btc-race-bot-v2/reward.svg)](https://make.fun/bounty/btc-race-24h-20261117-0603?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261117-0603)

A long-only dip-buying bot for CBBTC, entered in the make.fun bounty
[btc-race-24h-20261117-0603](https://make.fun/competitions/btc-race-24h-20261117-0603).
Python standard library only; it talks to the make.fun harness through the
bundled `vendor/makefun_bars.py` shim.

## How it trades

The bot watches one-minute CBBTC closes and waits for the price to fall
meaningfully below its own recent averages before buying the full position:

- **Two dip tiers.** A deep dip is a close at least 120 bps under the
  120-bar mean; a shallow dip is a close at least 80 bps under the
  60-bar mean. Either tier can trigger a buy.
- **Confirmation.** A dip buy also needs an uptick (this close above the
  previous close) and, after the first exit, the close to be back above
  the 60-bar mean before the bot re-arms. The first trade of a run does
  not need the re-arm.
- **Late fallback.** If the bot has never traded and the window is nearly
  over, it buys on an uptick at bar 1320, and buys unconditionally at bar
  1380, so a quiet window still gets a position instead of sitting in
  cash.
- **Exits.** Once in, the bot sells everything on a 200 bps stop loss,
  on a trailing stop (50 bps below the peak, armed once the peak is 50 bps
  above entry), or after holding 1440 bars. After an exit it waits 60
  bars before the next entry.

Buys size to the smaller of available cash and the symbol's weight room,
sell the full quantity, and cap slippage at 200 bps. Nothing here is
leveraged and the bot never shorts.

## Backtests

Net PnL is in USDC on a $1,000 starting balance, run through
`replay.py`, which implements the harness fill model (next-bar open,
2 bps slippage plus constant-product impact against open depth, 4 bps
fee) in exact integer arithmetic.

| Window | Net PnL | Trades | Max drawdown |
| --- | --- | --- | --- |
| dev (kit) | +$4.59 | 2 | 0.72% |
| race, 15 Nov 2026 | +$12.43 | 1 | 0.72% |
| nextdev | +$3.85 | 2 | 0.75% |
| w3, 16 Nov 2026 (partial, 896 bars) | $0.00 | 0 | 0.00% |
| w4, 13 Nov 2026 | −$22.11 | 4 | 3.00% |
| w5, 12 Nov 2026 | −$6.75 | 1 | 0.71% |
| w6, 11 Nov 2026 | −$2.32 | 2 | 1.33% |

The dev row reproduces the official dev grade to the micro-USDC, which
validates the bot logic and the fill model. Honest caveat: three of the
six windows above did not profit (w4, w5 and w6 negative), and no
parameter set found in the grid sweeps was non-negative on every window.
The 15 Nov figure measured here (+$12.43, 1 trade) differs from an
earlier quoted +$8.12 / 2 trades for the same window; that quoted figure
could not be reproduced by any configuration of this design after an
exhaustive sweep (including the depth, column mapping and window-layout
fixes in `replay.py`), so the table shows the measured value instead.
The raw-window depth constant in `replay.py` is fitted, not the kit dev
median: the median (880211041956) gives nextdev +$4.70, w4 −$22.05 and
w6 −$1.49, not the numbers in this table.

## Running it

```sh
python3 replay.py dev    # dev window from the kit dataset
python3 replay.py race   # 15 Nov window from data/btc-race-window.json
python3 verify_final.py  # each window twice; must be byte-identical and on target
python3 grid.py          # tier-1 dip threshold sweep
python3 grid2.py         # tier-2 threshold x trailing stop sweep
python3 grid3.py         # stop loss x max hold sweep
python3 grid4.py         # fallback bar sweep
python3 grid5.py         # uptick requirement x re-entry gate sweep
```

`main.py` is the entry point the harness runs; `replay.py` is the local
subprocess driver used for all backtests above.

## License

MIT, copyright 2026 aikbani — see `LICENSE`.
