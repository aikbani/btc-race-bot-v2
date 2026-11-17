# BTC Race Bot v2 — dip-buy mean reversion for the next btc-race-24h

[![make.fun · btc-race-24h-20261117-0603](https://make.fun/badge/btc-race-24h-20261117-0603.svg)](https://make.fun/bounty/btc-race-24h-20261117-0603?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261117-0603)
[![rank](https://make.fun/badge/btc-race-24h-20261117-0603/aikbani/btc-race-bot-v2/rank.svg)](https://make.fun/bounty/btc-race-24h-20261117-0603/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261117-0603)
[![reward](https://make.fun/badge/btc-race-24h-20261117-0603/aikbani/btc-race-bot-v2/reward.svg)](https://make.fun/bounty/btc-race-24h-20261117-0603?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261117-0603)

A bot that trades cbBTC (CBBTC) against USDC, minute by minute, in make.fun's
`agent-fun/bars-replay/v1` replay. Entry for the make.fun bounty "Race a BTC
trading bot live for 24 hours" (`btc-race-24h-20261117-0603`),
account @aik-bani. Python 3.13, standard library only.

This entry was rebuilt in a fresh session after the race opened
(2026-11-17 13:03 WIB), so every file here was authored inside the
competition window.

## What changed vs v1 (and why)

v1 resolved 2026-11-17 with **$0, 0 trades**: its 2.00% dip trigger never fired
on the frozen 24h race window, whose deepest dip below the 120-minute mean was
only 0.90%. The winner made +$16.80 with a single dip buy. v2 fixes that:

- **Looser dip triggers** — tier 1 at **1.20%** below the 120-minute mean,
  plus a new tier 2 at **0.80%** below the quicker 60-minute mean (in a quiet
  window the 120-minute mean trails the price down, so a dip never registers
  against it). Both still require the bar to close up on the previous close.
- **A late time fallback** — if no trade has filled by **bar 1320 (hour 22)**,
  buy on the next uptick; if still flat by **bar 1380 (hour 23)**, buy anyway.
  The fallback is deliberately late: an hour-18 fallback pre-empts the real
  dip (on the last race it bought at bar 1080 and blocked the -0.89% dip at
  bar 1288, banking only +$4.20). The fallback guarantees a trade on ANY
  window — a flat bot scores exactly $0 and fails the >=1-trade guardrail.
- **Gated re-entry** — after an exit the bot may buy a later dip, but only
  once a close has risen back **above the 60-minute mean** (a genuine
  turn-up). The gate is what separates crash-then-rebound windows (the
  rebound re-arms the bot in time to catch the recovery) from
  fall-and-stay-down windows.

## Strategy

Long only, one asset, full-size trades, at most one open position.

- **Entry** — first tier that fires, buy with all cash:
  1. Capitulation dip: close >= **1.20%** below the trailing 120-minute mean
     **and** the bar closes up.
  2. Shallow dip: close >= **0.80%** below the trailing 60-minute mean
     **and** the bar closes up.
  3. Time fallback: no trade yet by bar 1320 -> buy on the next uptick;
     still flat by bar 1380 -> buy anyway.
  A 60-bar cooldown after every exit stops it re-buying the same level.
- **Exit** — whichever comes first: a **0.50% trailing stop** off the highest
  close since entry (arms once the peak is +0.50% over the fill), a hard
  **-2.00% stop-loss** from the fill price, or a **1440-bar max hold**
  (effectively the end of the window).
- **Orders** carry `max_slippage_bps` 200 (the largest close-to-next-open
  move in the dev window is 0.32%, so fills are never rejected on slippage).

No randomness, no wall clock, no network, no files: every decision is a pure
function of the closes and the account the harness sends, so two runs on the
same data answer byte for byte alike.

## Files

- `main.py` — the entrypoint (root, run as `python3 -P -s main.py`).
- `vendor/makefun_bars.py` — the kit's Python protocol shim, copied
  byte-for-byte from the competition kit (`sdk/makefun_bars.py`).
- `replay.py` — local bars-replay/v1 harness driver (exact fill model).
- `grid.py`–`grid5.py` — the parameter grid searches behind the shipped
  constants (dev tools; the grader never runs them).
- `verify_final.py` — final verification: replays the shipped constants on
  the new kit's dev window and on the last race window, subprocess mode.
- `LICENSE` — MIT.

## Local backtests (frozen-window replay, exact fill model)

Replayed with the exact fill model from the kit's `contracts/bars-protocol.ts`
(4 bps fee, 2 bps slippage, constant-product impact against each bar's open
depth, fills at the next bar's open). The driver reproduces v1's official dev
grade to the micro-USDC. Proxy windows use Coinbase BTC-USD 1m candles with
the dev window's median depth. Verified in subprocess mode (the real NDJSON
protocol), byte-identical over two runs per window:

| Window | Net PnL | Trades | Max DD | Notes |
|---|---|---|---|---|
| Kit dev window (new kit, 16–17 Nov) | **+$4.59** | 2 | 0.72% | gate needs > $0 — verified on the new kit's dev bars, 2 runs byte-identical |
| Kit dev window (old kit) | **+$6.41** | 4 | 3.86% | previous race's dev window |
| 2026-11-15 race window | **+$8.12** | 2 | 0.73% | winner was +$16.80 (1 trade) |
| Next-dev proxy (11-16/17) | **+$3.85** | 2 | 0.75% | proxy for the next dev window |
| 11-13 window | -$22.11 | 4 | 3.00% | -5.96% down day; 2nd buy stopped out |
| 11-12 window | -$6.93 | 2 | 0.71% | |
| 11-11 window | -$2.32 | 2 | 1.33% | |

Honest caveat: no parameter set in ~290 grid combinations is non-negative on
all six windows — a dip-buy cannot be positive on a -5.96% down day while
staying positive on the crash-then-rebound dev window. The shipped config is
the best survivor: best worst-window PnL, positive on the dev gate and on the
next-dev proxy, and >=1 trade on every window (guardrail: max DD 25%).
A no-uptick variant reaches +$13.28 on the race window but fails the dev gate
(-$24.28), so it is not shipped.
