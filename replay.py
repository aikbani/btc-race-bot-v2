"""Local replay driver for the cbBTC race bot.

Runs main.py as a subprocess and feeds it the bars of a window exactly the
way the official harness does: one hello, one batch per minute, orders decided
on a batch filling at the next bar's open. Fills follow the protocol's own
model, in integer arithmetic throughout:

- a buy spends its notional less a 4 bps fee, fills 2 bps above the open, and
  pays constant-product impact against the bar's open depth;
- a sell gets 2 bps under the open (after impact), less a 4 bps fee;
- anything still held at the end is flattened at the last close against that
  close's depth, with the same fee and slippage and no slippage cap.

The driver also enforces the harness's decision checks (cash, weight, minimum
notional, last-batch rejection) in the same order, tracks cash and the one
position, and reports net PnL, trade count and maximum drawdown.

Two entry points for the other scripts in this repo: run_dev() grades the bot
on the frozen dev window from the kit, and run_window() on a raw-numbers
window from the local extras folder (race is the 15 Nov window). A raw file
carries no pool depths, so every one of its bars trades against the same
open and close depth: one constant, RAW_DEPTH below. Its bars are
deduplicated by timestamp and sliced to exactly 24 hours from the window
start, with each bar timestamped at its open time plus 60 seconds, in
milliseconds.

A raw row is [timestamp, LOW, HIGH, OPEN, CLOSE, volume] -- the first price
column is the bar's low (it is the minimum of the four prices in every row
of every file) and the third is the open (it chains to the previous row's
close), not [timestamp, open, high, low, close] as the column order first
suggests. The bot's decisions only read closes, so the old mapping got the
decisions right and only the fill prices wrong.
"""

import gzip
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

WORKDIR = Path(__file__).resolve().parent
KIT = WORKDIR.parent / "btc-race-24h-v2-kit" / "btc-race-24h-20261117-0603-kit"
EXTRAS = WORKDIR.parent / "btc-race-24h-v2-local-extras"

SYMBOL = "CBBTC"
MINT = "cbbtcf3aa214zXHbiAZQwf4122FBYbraNdFqgw4iMij"
PRICE_SCALE = 5
QTY_SCALE = 8
QUOTE_SCALE = 6
START_CASH = 1_000_000_000  # 1,000 USDC, in micro-USDC
FEE_BPS = 4
SLIP_BPS = 2
MIN_NOTIONAL = 1_000_000  # 1 USDC
MAX_ORDERS = 16
MAX_WEIGHT_BPS = 10000
BPS = 10000
STEP_MS = 60000


def _gcd(a, b):
    while b:
        a, b = b, a % b
    return a


def value(qty, price):
    """What qty is worth at price, truncated, in micro-USDC."""
    return qty * price * 10 ** QUOTE_SCALE // 10 ** (QTY_SCALE + PRICE_SCALE)


class _Pool:
    """One bar's constant-product pool, walked by the fills at its open.

    Mirrors the harness's pool: depths are hops in series, the state is the
    quote side y against the invariant x*y = D^2, kept as reduced fractions
    so every fill price comes out exactly.
    """

    __slots__ = ("yn", "yd", "kn", "kd", "unit")

    def __init__(self, yn, yd, kn, kd, unit=10 ** 6):
        g = _gcd(yn, yd)
        self.yn = yn // g
        self.yd = yd // g
        self.kn = kn
        self.kd = kd
        self.unit = unit

    @classmethod
    def fresh(cls, depths):
        d = [x * 10 ** QUOTE_SCALE for x in depths]
        prod = 1
        for x in d:
            prod *= x
        total = 0
        for i in range(len(d)):
            other = 1
            for j, x in enumerate(d):
                if j != i:
                    other *= x
            total += other
        g = _gcd(prod, total)
        yn, yd = prod // g, total // g
        return cls(yn, yd, yn * yn, yd * yd)

    def buy(self, spend):
        yn2 = self.yn + spend * self.unit * self.yd
        num = self.yn * yn2 * self.kd
        den = self.yd * self.yd * self.kn
        return num, den, _Pool(yn2, self.yd, self.kn, self.kd)

    def sell(self, worth):
        t = self.kn * self.yd + worth * self.unit * self.yn * self.kd
        num = t * self.yd
        den = self.kd * self.yn * self.yn
        return num, den, _Pool(self.kn * self.yn, t, self.kn, self.kd)


def _pool_for(depths):
    return None if depths is None else _Pool.fresh(depths)


def _buy_fill(notional, open_price, pool):
    spend = notional * (BPS - FEE_BPS) // BPS
    fee = notional - spend
    if pool is None:
        price = open_price * (BPS + SLIP_BPS) // BPS
        after = None
    else:
        num, den, after = pool.buy(spend)
        price = open_price * (BPS + SLIP_BPS) * num // (BPS * den)
    qty = spend * 10 ** (QTY_SCALE + PRICE_SCALE) // (price * 10 ** QUOTE_SCALE)
    return price, spend, fee, qty, after


def _sell_fill(qty, open_price, pool):
    worth = value(qty, open_price)
    if pool is None:
        slipped = open_price * (BPS - SLIP_BPS) // BPS
        after = None
    else:
        num, den, after = pool.sell(worth)
        slipped = open_price * (BPS - SLIP_BPS) * den // (BPS * num)
    price = slipped if slipped > 1 else 1
    gross = value(qty, price)
    proceeds = gross * (BPS - FEE_BPS) // BPS
    return price, gross, gross - proceeds, proceeds, after


def dev_bars():
    """The frozen dev window's bars, from the kit dataset."""
    path = KIT / "dataset" / "bars" / "CBBTC.ndjson.gz"
    bars = []
    with gzip.open(path, "rt") as fh:
        for line in fh:
            raw = json.loads(line)
            bars.append(
                {
                    "t": int(raw["t"]),
                    "o": int(raw["o"]),
                    "h": int(raw["h"]),
                    "l": int(raw["l"]),
                    "c": int(raw["c"]),
                    "o_depth": [int(x) for x in raw["o_depth"]],
                    "c_depth": [int(x) for x in raw["c_depth"]],
                }
            )
    return bars


# The one constant open/close depth every raw-window bar trades against.
#
# The median open depth of the kit dev bars was measured first
# (880211041956, the mean of the two middle values 879955678897 and
# 880466405015) and does NOT reproduce the verified numbers: at that depth
# nextdev nets +$4.70 (not +$3.85), w4 -$22.05 (not -$22.11) and w6 -$1.49
# (not -$2.32). No statistic of any depth series in the kit can be the
# fitting depth either -- the smallest depth value anywhere in the kit
# (dev o_depth, samples liquidity and vaults[1]) is 708848708439, above
# the fitting range. So the constant is fitted, not derived: 642500000000
# is the midpoint of 642.2e9-642.7e9, the only depth interval on which
# nextdev, w4 and w6 all match their verified numbers exactly (net to the
# cent, trade count, drawdown to 0.01pp). At this depth dev is also exact.
# Race and w5 still do not match their quoted targets (see verify_final.py):
# in every 1440-bar construction the bot's exit never fires in-window, so
# both windows end on the single late buy with the flatten -- a structural
# property of the bot and the bars, not of the depth.
RAW_DEPTH = 642_500_000_000


def _row_bar(row, depth):
    to_int = lambda x: int((Decimal(str(x)) * 100000).to_integral_value())
    return {
        # row[0] is the bar's open time in seconds; the harness timestamps
        # a bar at its close, i.e. open + 60 seconds, in milliseconds.
        # Columns are [ts, low, high, open, close, volume] (see the module
        # docstring): row[1] is the low, row[2] the high, row[3] the open.
        "t": int(row[0]) * 1000 + STEP_MS,
        "o": to_int(row[3]),
        "h": to_int(row[2]),
        "l": to_int(row[1]),
        "c": to_int(row[4]),
        "o_depth": [depth],
        "c_depth": [depth],
    }


def race_slots(filename="btc-race-window.json"):
    """A raw JSON window as minute slots: dedup, exactly 24h from the start.

    The raw file is [timestamp, low, high, open, close, volume] rows with
    timestamps in seconds. The same minute can appear twice where chunks
    overlap, so later duplicates are dropped; the window is then the
    first 1440 minute slots from its start (exactly 24 hours), with gaps
    kept as None. Every bar gets the same open and close depth: RAW_DEPTH
    (see its comment above -- the kit dev median does not reproduce the
    verified numbers, so the constant is the fitted midpoint).
    """
    rows = json.load(open(EXTRAS / "data" / filename))
    depth = RAW_DEPTH
    by_ts = {}
    for row in rows:
        by_ts.setdefault(int(row[0]), row)
    stamps = sorted(by_ts)
    first = stamps[0] * 1000 + STEP_MS
    slots = [None] * ((stamps[-1] - stamps[0]) // 60 + 1)
    for stamp in stamps:
        slots[(stamp - stamps[0]) // 60] = _row_bar(by_ts[stamp], depth)
    return slots[:1440], first


def _wire_bar(bar):
    # The bot never reads depths (it sizes from cash and the weight room),
    # so a bar without depths still wires a harmless placeholder there.
    return {
        "symbol": SYMBOL,
        "t": bar["t"],
        "o": str(bar["o"]),
        "h": str(bar["h"]),
        "l": str(bar["l"]),
        "c": str(bar["c"]),
        "v": None,
        "o_depth": [str(x) for x in bar["o_depth"]] if bar["o_depth"] else ["1"],
        "c_depth": [str(x) for x in bar["c_depth"]] if bar["c_depth"] else ["1"],
    }


def _decide(orders, seq, is_last, cash, qty, close, equity, used_ids):
    """The harness's decision checks, in its order, for one reply."""
    pending = []
    buys = 0
    bought = 0
    sold = 0
    open_syms = {SYMBOL} if qty > 0 else set()
    for index, order in enumerate(orders):
        side = order["side"]
        written = (
            int(order["notional_quote_units"])
            if side == "buy"
            else int(order["qty"])
        )
        reject = None
        amount = written
        if index >= MAX_ORDERS:
            reject = "max_orders_per_bar"
        elif order["client_id"] in used_ids:
            reject = "duplicate_client_id"
        elif order["symbol"] != SYMBOL:
            reject = "not_in_universe"
        elif written == 0:
            reject = "zero_amount"
        elif is_last:
            reject = "after_end"
        elif close is None:
            reject = "no_bar"
        elif side == "buy":
            cash_left = cash - buys
            held_value = value(qty, close)
            if amount == 0:
                reject = "insufficient_cash" if cash_left <= 0 else "max_weight"
            elif amount < MIN_NOTIONAL:
                reject = "below_min_notional"
            elif amount > cash_left:
                reject = "insufficient_cash"
            elif SYMBOL not in open_syms and len(open_syms) >= 1:
                reject = "max_open_positions"
            elif (held_value + bought + amount) * BPS > MAX_WEIGHT_BPS * equity:
                reject = "max_weight"
            if reject is None:
                buys += amount
                bought += amount
                open_syms.add(SYMBOL)
        else:
            available = qty - sold
            if amount == 0 or amount > available:
                reject = "insufficient_position"
            elif amount != available and value(amount, close) < MIN_NOTIONAL:
                reject = "below_min_notional"
            if reject is None:
                sold += amount
        used_ids.add(order["client_id"])
        pending.append(
            {
                "order": order,
                "seq": seq,
                "index": index,
                "reject": reject,
                "amount": amount,
                "close": close,
            }
        )
    return pending


def run_slots(slots, first_close_ms, bot_path=None):
    """Replay slots through the bot subprocess; return metrics and replies."""
    bot = str(bot_path or WORKDIR / "main.py")
    env = dict(os.environ)
    env["PYTHONHASHSEED"] = "0"
    proc = subprocess.Popen(
        [sys.executable, "-P", "-s", bot],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        env=env,
    )

    def send(obj):
        proc.stdin.write(json.dumps(obj, separators=(",", ":")) + "\n")
        proc.stdin.flush()

    def recv():
        line = proc.stdout.readline()
        if not line:
            err = proc.stderr.read()
            raise RuntimeError(f"bot exited early: {err[:2000]}")
        return line.rstrip("\n")

    hello = {
        "type": "hello",
        "protocol": "agent-fun/bars-replay/v1",
        "seed": "20261117",
        "symbols": [
            {
                "symbol": SYMBOL,
                "mint": MINT,
                "price_scale": PRICE_SCALE,
                "qty_scale": QTY_SCALE,
            }
        ],
        "capital": {
            "quote": "USDC",
            "quote_scale": QUOTE_SCALE,
            "quote_units": str(START_CASH),
        },
        "bar_interval": "1m",
        "first_close_ms": first_close_ms,
        "caps": {
            "max_open_positions": 1,
            "max_orders_per_bar": MAX_ORDERS,
            "max_weight_bps": MAX_WEIGHT_BPS,
            "shorts": False,
            "leverage": False,
        },
        "fees": {
            "fee_bps": FEE_BPS,
            "slippage_bps": SLIP_BPS,
            "min_notional_quote_units": str(MIN_NOTIONAL),
        },
        "latency_bars": 1,
    }
    send(hello)

    cash = START_CASH
    qty = 0
    cost = 0
    last_close = None
    last_close_depth = None
    trades = 0
    replies = []
    used_ids = set()
    equity_points = [START_CASH]
    pending = []

    def resolve_one(p, bar, pools):
        nonlocal cash, qty, cost, trades
        base = {
            "client_id": p["order"]["client_id"],
            "symbol": SYMBOL,
            "side": p["order"]["side"],
            "status": "rejected",
            "reason": None,
            "decided_bar": first_close_ms + p["seq"] * STEP_MS,
            "fill_bar": first_close_ms + (p["seq"] + 1) * STEP_MS,
            "price": "0",
            "qty": "0",
            "notional_quote_units": "0",
            "fee_quote_units": "0",
        }
        if p["reject"] is not None:
            base["reason"] = p["reject"]
            return base
        if bar is None:
            base["reason"] = "no_bar"
            return base
        pool = pools.get(SYMBOL)
        if pool is None:
            pool = _pool_for(bar["o_depth"])
            pools[SYMBOL] = pool
        close = p["close"]
        max_slip = int(p["order"]["max_slippage_bps"])
        if p["order"]["side"] == "buy":
            price, spend, fee, got, after = _buy_fill(
                p["amount"], bar["o"], pool
            )
            if price * BPS > close * (BPS + max_slip):
                base["reason"] = "max_slippage"
                return base
            if got == 0:
                base["reason"] = "zero_amount"
                return base
            pools[SYMBOL] = after
            cash -= p["amount"]
            qty += got
            cost += p["amount"]
            trades += 1
            base.update(
                {
                    "status": "filled",
                    "price": str(price),
                    "qty": str(got),
                    "notional_quote_units": str(p["amount"]),
                    "fee_quote_units": str(fee),
                }
            )
            return base
        price, gross, fee, proceeds, after = _sell_fill(
            p["amount"], bar["o"], pool
        )
        if price * BPS < close * (BPS - max_slip):
            base["reason"] = "max_slippage"
            return base
        if p["amount"] > qty:
            raise RuntimeError("sell exceeds the position held")
        pools[SYMBOL] = after
        cash += proceeds
        cost = cost * (qty - p["amount"]) // qty if qty else 0
        qty -= p["amount"]
        trades += 1
        base.update(
            {
                "status": "filled",
                "price": str(price),
                "qty": str(p["amount"]),
                "notional_quote_units": str(proceeds),
                "fee_quote_units": str(fee),
            }
        )
        return base

    try:
        for k, bar in enumerate(slots):
            t = first_close_ms + k * STEP_MS
            fills = []
            if pending:
                pools = {}
                for p in pending:
                    fills.append(resolve_one(p, bar, pools))
                pending = []
            if bar is not None:
                last_close = bar["c"]
                last_close_depth = bar["c_depth"]
            equity = cash
            if qty > 0 and last_close is not None:
                equity += value(qty, last_close)
            account = {
                "cash_quote_units": str(cash),
                "positions": (
                    [
                        {
                            "symbol": SYMBOL,
                            "qty": str(qty),
                            "cost_quote_units": str(cost),
                        }
                    ]
                    if qty > 0
                    else []
                ),
                "equity_quote_units": str(equity),
            }
            batch = {
                "type": "batch",
                "seq": k,
                "t": t,
                "bars": [_wire_bar(bar)] if bar is not None else [],
                "fills": fills,
                "account": account,
            }
            send(batch)
            line = recv()
            replies.append(line)
            reply = json.loads(line)
            if reply.get("type") != "reply" or reply.get("seq") != k:
                raise RuntimeError(f"bad reply at batch {k}: {line[:200]}")
            pending = _decide(
                reply["orders"],
                k,
                k == len(slots) - 1,
                cash,
                qty,
                last_close,
                equity,
                used_ids,
            )
            equity_points.append(equity)
        for p in pending:
            if p["reject"] is None:
                raise RuntimeError("last batch order passed its checks")
        if qty > 0:
            _, _, _, proceeds, _ = _sell_fill(
                qty, last_close, _pool_for(last_close_depth)
            )
            cash += proceeds
            qty = 0
            cost = 0
        equity_points.append(cash)
    finally:
        try:
            proc.stdin.close()
        except BrokenPipeError:
            pass
        proc.wait(timeout=60)

    peak = equity_points[0]
    worst = 0.0
    for eq in equity_points:
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak if peak else 0.0
        if dd > worst:
            worst = dd
    net = cash - START_CASH
    return {
        "net_pnl_units": net,
        "net_pnl_usd": net / 1_000_000,
        "trades": trades,
        "max_drawdown": worst,
        "final_cash_units": cash,
        "batches": len(slots),
        "replies": replies,
    }


def run_dev(bot_path=None):
    bars = dev_bars()
    return run_slots(bars, bars[0]["t"], bot_path)


WINDOW_FILES = {
    "race": "btc-race-window.json",
    "nextdev": "btc-nextdev.json",
    "w3": "btc-w3-1116-partial.json",
    "w4": "btc-w4-1113.json",
    "w5": "btc-w5-1112.json",
    "w6": "btc-w6-1111.json",
}


def run_window(name, bot_path=None):
    slots, first = race_slots(WINDOW_FILES[name])
    return run_slots(slots, first, bot_path)


def run_race(bot_path=None):
    return run_window("race", bot_path)


def run_with_overrides(slots, first_close_ms, overrides):
    """Replay with a throwaway copy of the bot whose constants are patched.

    Used by the grid sweeps: the entry's own main.py is never touched, the
    patched copy lives in a temp dir and is deleted afterwards.
    """
    tmp = Path(tempfile.mkdtemp(prefix="btc-grid-"))
    try:
        shutil.copytree(WORKDIR / "vendor", tmp / "vendor")
        text = (WORKDIR / "main.py").read_text()
        for name, val in overrides.items():
            text, n = re.subn(
                rf"^{name} = .*$", f"{name} = {val!r}", text, count=1, flags=re.M
            )
            if n != 1:
                raise ValueError(f"constant {name} not found in main.py")
        (tmp / "main.py").write_text(text)
        return run_slots(slots, first_close_ms, tmp / "main.py")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def describe(name, res):
    return (
        f"{name}: net ${res['net_pnl_usd']:+.2f} | "
        f"trades {res['trades']} | max DD {res['max_drawdown'] * 100:.2f}%"
    )


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "dev"
    if which == "dev":
        print(describe("dev", run_dev()))
    elif which in WINDOW_FILES:
        print(describe(which, run_window(which)))
    else:
        raise SystemExit(
            f"unknown window {which!r} (dev | race | nextdev | w3 | w4 | w5 | w6)"
        )
