"""Local bars-replay/v1 harness driver.

Faithful re-implementation of the make.fun harness side of
agent-fun/bars-replay/v1, from contracts/bars-protocol.ts (kit harness-src)
and the competition's own config (capital 1,000 USDC, fee 4 bps, slippage
2 bps, min notional $1, max 1 open position, max weight 100%, latency 1 bar).

Two ways to run a bot:
  * in-process: run_inprocess(bot_on_batch, bars) — drives the bot's real
    on_batch through the real vendor shim (BarsState), no subprocess.
  * subprocess: run_subprocess(bot_path, bars) — speaks NDJSON to the bot
    exactly like the server harness does (hello, batch..., done).

Bars are dicts in kit shape: {"t": ms, "o","h","l","c": str ints at
price_scale 5, "o_depth": [str ints], "c_depth": [str ints]}.

Fill math (all integer, truncating division; every value positive):
  buy : spend = notional * 9996 // 10000
        price = open * 10002 * y * (y + spend) // (10000 * D * D)
        qty   = spend * 10**7 // price
  sell: a     = qty * open // 10**7            (value of qty at the open)
        price = max(1, open * 9998 * y * y // (10000 * (D*D + a*y)))
        gross = qty * price // 10**7
        proceeds = gross * 9996 // 10000
  D = 1 / sum(1/Di) over the bar's o_depth hops; y starts at D and walks
  with each fill at that open. Max-slippage is checked against the latest
  close the bot decided on (buy: price*10000 > close*10200 rejects).
  At the end every position is flattened at the last close against that
  close's c_depth, with no slippage cap and no min-notional check.
"""

import gzip
import json
import subprocess
import sys

QUOTE_SCALE = 6
PRICE_SCALE = 5
QTY_SCALE = 8
FEE_BPS = 4
SLIP_BPS = 2
MIN_NOTIONAL = 1_000_000
START_CASH = 1_000_000_000
SYMBOL = "CBBTC"
MINT = "cbbtcf3aa214zXHbiAZQwf4122FBYbraNdFqgw4iMij"
DEV_DEPTH_MEDIAN = 642377006705  # median o_depth of the kit dev bars (quote units)

# Paths for this race (btc-race-24h-20261117-0603). The dev bars come
# straight from the new competition kit; the race window is the local
# sanity copy kept under data/ (dev tool only — never part of the entry).
WORKDIR = "/home/hatch/workspace/makefun/work/btc-race-24h-v2"
NEW_KIT_DEV_BARS = ("/home/hatch/workspace/makefun/work/btc-race-24h-v2-kit/"
                    "btc-race-24h-20261117-0603-kit/dataset/bars/CBBTC.ndjson.gz")
RACE_WINDOW_JSON = WORKDIR + "/data/btc-race-window.json"
RACE_WINDOW_START = 1794753780  # 2026-11-15 08:28 UTC


def value(qty, price):
    return qty * price * 10 ** QUOTE_SCALE // 10 ** (QTY_SCALE + PRICE_SCALE)


def load_dev_bars():
    """The new kit's dev bars: 16 Nov 13:04 -> 17 Nov 13:03 WIB, 1440 bars."""
    bars = []
    with gzip.open(NEW_KIT_DEV_BARS, "rt") as f:
        for line in f:
            b = json.loads(line)
            bars.append({
                "t": int(b["t"]),
                "o": int(b["o"]), "h": int(b["h"]), "l": int(b["l"]), "c": int(b["c"]),
                "o_depth": [int(d) for d in b["o_depth"]],
                "c_depth": [int(d) for d in b["c_depth"]],
            })
    return bars


def load_coinbase(path, start_ts, depth=DEV_DEPTH_MEDIAN):
    """Coinbase BTC-USD 1m candles -> kit-shaped bars, exactly 1440 bars
    from start_ts (seconds). Candle: [time, low, high, open, close, vol]."""
    raw = json.load(open(path))
    seen = {}
    for c in raw:
        seen[int(c[0])] = c  # dedupe chunk-boundary overlaps
    out = []
    for t_open in sorted(seen):
        c = seen[t_open]
        if t_open < start_ts or t_open >= start_ts + 86400:
            continue
        o = int(round(float(c[3]) * 10 ** PRICE_SCALE))
        h = int(round(float(c[2]) * 10 ** PRICE_SCALE))
        l = int(round(float(c[1]) * 10 ** PRICE_SCALE))
        cl = int(round(float(c[4]) * 10 ** PRICE_SCALE))
        out.append({"t": (t_open + 60) * 1000, "o": o, "h": h, "l": l, "c": cl,
                    "o_depth": [depth], "c_depth": [depth]})
    out.sort(key=lambda b: b["t"])
    assert len(out) >= 1400, f"{path}: {len(out)} bars, expected ~1440"
    return out


def make_hello(first_close_ms):
    return {
        "type": "hello",
        "protocol": "agent-fun/bars-replay/v1",
        "seed": "1",
        "symbols": [{"symbol": SYMBOL, "mint": MINT,
                     "price_scale": PRICE_SCALE, "qty_scale": QTY_SCALE}],
        "capital": {"quote": "USDC", "quote_scale": QUOTE_SCALE,
                    "quote_units": str(START_CASH)},
        "bar_interval": "1m",
        "first_close_ms": first_close_ms,
        "caps": {"max_open_positions": 1, "max_orders_per_bar": 16,
                 "max_weight_bps": 10000, "shorts": False, "leverage": False},
        "fees": {"fee_bps": FEE_BPS, "slippage_bps": SLIP_BPS,
                 "min_notional_quote_units": str(MIN_NOTIONAL)},
        "latency_bars": 1,
    }


class Harness:
    """The harness side: holds the account, resolves orders at each bar's
    open, tracks equity / drawdown / trades / fees."""

    def __init__(self, bars):
        self.bars = bars
        self.cash = START_CASH
        self.qty = 0
        self.cost = 0
        self.trades = 0          # bot's own filled orders
        self.fees = 0
        self.peak_equity = START_CASH
        self.max_dd = 0.0
        self.equity_curve = []
        self.fills_log = []      # (seq, side, price, qty, notional, status)
        self._pending = []       # (orders, decision_close)
        self._flattened = False

    def account(self, equity):
        pos = []
        if self.qty > 0:
            pos.append({"symbol": SYMBOL, "qty": str(self.qty),
                        "cost_quote_units": str(self.cost)})
        return {"cash_quote_units": str(self.cash), "positions": pos,
                "equity_quote_units": str(equity)}

    def equity_at(self, close):
        return self.cash + value(self.qty, close)

    def _note_equity(self, close):
        eq = self.equity_at(close)
        if eq > self.peak_equity:
            self.peak_equity = eq
        dd = (self.peak_equity - eq) / self.peak_equity if self.peak_equity else 0.0
        if dd > self.max_dd:
            self.max_dd = dd
        self.equity_curve.append(eq)
        return eq

    @staticmethod
    def _pool_D(depths):
        # D = 1 / sum(1/Di), exact fraction as (num, den): D = prod / sum(prod/Di)
        prod = 1
        for d in depths:
            prod *= d
        total = 0
        for i, d in enumerate(depths):
            other = 1
            for j, dd in enumerate(depths):
                if j != i:
                    other *= dd
            total += other
        return prod, total  # D = prod / total

    def resolve_orders(self, orders, decision_close, bar):
        """Resolve one reply's orders at bar's open. Returns fill dicts."""
        fills = []
        D_num, D_den = self._pool_D(bar["o_depth"])
        # y starts at D: keep y as fraction y_num / y_den with y_den = D_den
        # (y = D * k where k tracks walks). Simpler: track y as a Fraction
        # via numerator over D_den * D_num powers — but with <=16 orders and
        # one hop in practice, use exact rational with Python ints.
        from fractions import Fraction
        D = Fraction(D_num, D_den)
        y = D
        op = int(bar["o"])
        for o in orders:
            side = o["side"]
            cid = o["client_id"]
            bps = o["max_slippage_bps"]
            base = {"client_id": cid, "symbol": SYMBOL, "side": side}
            if side == "buy":
                notional = int(o["notional_quote_units"])
                if notional < MIN_NOTIONAL:
                    fills.append({**base, "status": "rejected",
                                  "reason": "below_min_notional"})
                    continue
                if notional > self.cash:
                    fills.append({**base, "status": "rejected",
                                  "reason": "insufficient_cash"})
                    continue
                if self.qty > 0:
                    fills.append({**base, "status": "rejected",
                                  "reason": "max_open_positions"})
                    continue
                eq_dec = self.cash + value(self.qty, decision_close)
                if value(self.qty, decision_close) + notional > eq_dec:
                    fills.append({**base, "status": "rejected",
                                  "reason": "max_weight"})
                    continue
                spend = notional * (10000 - FEE_BPS) // 10000
                # price = open * (10000+slip) * M / 10000, M = y*(y+a)/D^2
                a = spend  # quote units == USDC raw units (quote_scale 6)
                M = y * (y + a) / (D * D)
                price = int(op * (10000 + SLIP_BPS) * M // 10000)
                if price * 10000 > decision_close * (10000 + bps):
                    fills.append({**base, "status": "rejected",
                                  "reason": "max_slippage"})
                    continue
                q = spend * 10 ** (QTY_SCALE + PRICE_SCALE) // (price * 10 ** QUOTE_SCALE)
                if q <= 0:
                    fills.append({**base, "status": "rejected",
                                  "reason": "zero_amount"})
                    continue
                y = y + a
                self.cash -= notional
                self.qty += q
                self.cost += notional
                self.fees += notional - spend
                self.trades += 1
                fills.append({**base, "status": "filled", "price": str(price),
                              "qty": str(q), "notional_quote_units": str(notional),
                              "fee_quote_units": str(notional - spend)})
            else:  # sell
                q = int(o["qty"])
                if q <= 0 or q > self.qty:
                    fills.append({**base, "status": "rejected",
                                  "reason": "insufficient_position"})
                    continue
                val_close = value(q, decision_close)
                empties = (q == self.qty)
                if val_close < MIN_NOTIONAL and not empties:
                    fills.append({**base, "status": "rejected",
                                  "reason": "below_min_notional"})
                    continue
                a = value(q, op)
                M = (D * D + a * y) / (y * y)
                price = int(op * (10000 - SLIP_BPS) // (10000 * M))
                if price < 1:
                    price = 1
                if price * 10000 < decision_close * (10000 - bps):
                    fills.append({**base, "status": "rejected",
                                  "reason": "max_slippage"})
                    continue
                gross = value(q, price)
                proceeds = gross * (10000 - FEE_BPS) // 10000
                y = D * D * y / (D * D + a * y)
                self.cash += proceeds
                self.cost = self.cost * (self.qty - q) // self.qty if self.qty else 0
                self.qty -= q
                self.fees += gross - proceeds
                self.trades += 1
                fills.append({**base, "status": "filled", "price": str(price),
                              "qty": str(q), "notional_quote_units": str(proceeds),
                              "fee_quote_units": str(gross - proceeds)})
        return fills

    def flatten(self):
        """Sell everything at the last close against its c_depth."""
        if self.qty <= 0:
            return None
        bar = self.bars[-1]
        # Reuse resolve_orders with a synthetic sell at the last bar's close
        # depth: temporarily swap o_depth for c_depth.
        fake = dict(bar)
        fake["o_depth"] = bar["c_depth"]
        fills = self.resolve_orders(
            [{"side": "sell", "client_id": "_flatten_0", "symbol": SYMBOL,
              "qty": str(self.qty), "max_slippage_bps": 10000}],
            int(bar["c"]), fake)
        self._flattened = True
        return fills[0] if fills else None

    def summary(self):
        pnl = self.cash - START_CASH
        return {
            "net_pnl_usd": pnl / 1e6,
            "net_pnl_micro": pnl,
            "trades": self.trades,
            "max_dd_pct": self.max_dd * 100,
            "fees_usd": self.fees / 1e6,
            "final_cash_usd": self.cash / 1e6,
            "guardrail_trades_ok": self.trades >= 1,
            "guardrail_dd_ok": self.max_dd <= 0.25,
        }


def run_inprocess(bot_on_batch, bars, shim_dir):
    """Drive the bot's real on_batch through the real vendor shim."""
    sys.path.insert(0, shim_dir)
    import makefun_bars
    hello = make_hello(bars[0]["t"])
    h = Harness(bars)
    state = makefun_bars.BarsState(hello)
    for seq, bar in enumerate(bars):
        fills = []
        if h._pending:
            orders, decision_close = h._pending
            fills = h.resolve_orders(orders, decision_close, bar)
            h._pending = []
        eq = h.equity_at(int(bar["c"]))
        if eq > h.peak_equity:
            h.peak_equity = eq
        dd = (h.peak_equity - eq) / h.peak_equity
        h.max_dd = max(h.max_dd, dd)
        h.equity_curve.append(eq)
        batch = {"type": "batch", "seq": seq, "t": int(bar["t"]),
                 "bars": [{"symbol": SYMBOL, "t": int(bar["t"]),
                           "o": str(bar["o"]), "h": str(bar["h"]),
                           "l": str(bar["l"]), "c": str(bar["c"]),
                           "v": None,
                           "o_depth": [str(d) for d in bar["o_depth"]],
                           "c_depth": [str(d) for d in bar["c_depth"]]}],
                 "fills": fills,
                 "account": h.account(eq)}
        state.apply_batch(batch)
        orders = state.to_orders(bot_on_batch(state, batch))
        state.record_orders(orders)
        if orders:
            h._pending = (orders, int(bar["c"]))
        for f in fills:
            h.fills_log.append((seq, f))
    # Orders decided on the last batch are rejected (after_end): drop them.
    h._pending = []
    flat = h.flatten()
    final_eq = h.cash
    h._note_equity(int(bars[-1]["c"]))
    return h


def run_subprocess(bot_path, bars):
    """Speak the protocol to the bot over pipes, exactly like the server."""
    hello = make_hello(bars[0]["t"])
    h = Harness(bars)
    proc = subprocess.Popen(
        [sys.executable, "-P", "-s", bot_path],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        text=True, bufsize=1, cwd="/tmp")
    def send(obj):
        proc.stdin.write(json.dumps(obj, separators=(",", ":")) + "\n")
        proc.stdin.flush()
    def recv():
        line = proc.stdout.readline()
        if not line:
            raise RuntimeError("bot closed stdout")
        return json.loads(line)
    send(hello)
    for seq, bar in enumerate(bars):
        fills = []
        if h._pending:
            orders, decision_close = h._pending
            fills = h.resolve_orders(orders, decision_close, bar)
            h._pending = []
        eq = h.equity_at(int(bar["c"]))
        if eq > h.peak_equity:
            h.peak_equity = eq
        dd = (h.peak_equity - eq) / h.peak_equity
        h.max_dd = max(h.max_dd, dd)
        h.equity_curve.append(eq)
        batch = {"type": "batch", "seq": seq, "t": int(bar["t"]),
                 "bars": [{"symbol": SYMBOL, "t": int(bar["t"]),
                           "o": str(bar["o"]), "h": str(bar["h"]),
                           "l": str(bar["l"]), "c": str(bar["c"]),
                           "v": None,
                           "o_depth": [str(d) for d in bar["o_depth"]],
                           "c_depth": [str(d) for d in bar["c_depth"]]}],
                 "fills": fills,
                 "account": h.account(eq)}
        send(batch)
        reply = recv()
        assert reply.get("type") == "reply" and reply.get("seq") == seq, reply
        orders = reply.get("orders", [])
        if orders:
            h._pending = (orders, int(bar["c"]))
        for f in fills:
            h.fills_log.append((seq, f))
    h._pending = []
    h.flatten()
    h._note_equity(int(bars[-1]["c"]))
    done = {"type": "done", "seq": len(bars), "reason": "end_of_data",
            "account": h.account(h.cash)}
    send(done)
    proc.stdin.close()
    proc.wait(timeout=30)
    if proc.returncode != 0:
        raise RuntimeError(f"bot exited {proc.returncode}")
    return h


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--bot", required=True)
    ap.add_argument("--bars", required=True, choices=["dev", "race"])
    ap.add_argument("--mode", default="subprocess", choices=["subprocess", "inprocess"])
    args = ap.parse_args()
    if args.bars == "dev":
        bars = load_dev_bars()
    else:
        bars = load_coinbase(RACE_WINDOW_JSON, RACE_WINDOW_START)
    if args.mode == "subprocess":
        h = run_subprocess(args.bot, bars)
    else:
        import importlib.util
        specmod = importlib.util.spec_from_file_location("botmod", args.bot)
        mod = importlib.util.module_from_spec(specmod)
        specmod.loader.exec_module(mod)
        shim = "/".join(args.bot.split("/")[:-1]) + "/vendor"
        h = run_inprocess(mod.DipBot().on_batch, bars, shim)
    print(json.dumps({**h.summary(), "bars": args.bars, "mode": args.mode}, indent=1))
