"""Portfolio backtest of the trend strategy on real OKX candles.

    python -m okxbot.backtest --days 1095 --capital 200

Signals are taken on a candle's close and filled at the NEXT candle's open.
Stops are checked against each candle's high/low. Fees, slippage and funding included.
"""
import argparse
import math
from dataclasses import dataclass, field

import pandas as pd

from .config import SETTINGS, Settings
from .data import TF_MS, fetch_history
from .strategy import entry_signal, prepare, trail_stop


@dataclass
class Pos:
    symbol: str
    side: str
    qty: float
    entry: float
    stop: float
    extreme: float
    opened: pd.Timestamp
    funding: float = 0.0


@dataclass
class Result:
    equity: pd.Series
    trades: list = field(default_factory=list)
    pauses: int = 0
    nav: pd.Series | None = None      # value of 1 unit: performance with deposits removed
    deposited: float = 0.0            # starting capital + all monthly deposits


MAJORS = {"BTC/USDT:USDT", "ETH/USDT:USDT"}


def slip_for(sym: str, s: Settings) -> float:
    # altcoins have thinner order books: assume ~3x the slippage of BTC/ETH
    return s.slippage if sym in MAJORS else s.slippage * 3


def run(frames: dict[str, pd.DataFrame], s: Settings, capital: float,
        deposit: float = 0.0, deposit_days: int = 30) -> Result:
    frames = {k: prepare(v, s) for k, v in frames.items()}
    index = sorted(set().union(*[f.index for f in frames.values()]))
    cash = capital
    positions: dict[str, Pos] = {}
    pending: dict[str, object] = {}
    trades, curve = [], []
    peak = capital
    day, day_start = None, capital
    paused_until = None
    pauses = 0
    bars_per_8h = 8 * 3_600_000 / TF_MS[s.timeframe]
    dir_ = {"long": 1, "short": -1}
    units, navs, deposited = capital, [], capital
    next_deposit = index[0] + pd.Timedelta(days=deposit_days)

    def mark(ts) -> float:
        eq = cash
        for p in positions.values():
            f = frames[p.symbol]
            px = f.at[ts, "close"] if ts in f.index else p.entry
            eq += dir_[p.side] * p.qty * (px - p.entry)
        return eq

    def close(p: Pos, px: float, ts, why: str):
        nonlocal cash
        d = dir_[p.side]
        fill = px * (1 - d * slip_for(p.symbol, s))
        gross = d * p.qty * (fill - p.entry)
        fee = p.qty * fill * s.fee_rate
        cash += gross - fee - p.funding
        trades.append({"symbol": p.symbol, "side": p.side, "opened": p.opened, "closed": ts,
                       "entry": p.entry, "exit": fill, "pnl": gross - fee - p.funding, "why": why})
        del positions[p.symbol]

    for ts in index:
        # ---- 1) fill pending entries at this bar's open
        for sym, sig in list(pending.items()):
            f = frames[sym]
            if ts not in f.index:
                continue
            del pending[sym]
            if sym in positions or len(positions) >= s.max_positions:
                continue
            o = f.at[ts, "open"]
            d = dir_[sig.side]
            fill = o * (1 + d * slip_for(sym, s))
            dist = abs(fill - sig.stop)
            if dist <= 0 or (d == 1 and sig.stop >= fill) or (d == -1 and sig.stop <= fill):
                continue
            eq = mark(ts)
            qty = eq * s.risk_per_trade / dist
            used = sum(p.qty * p.entry for p in positions.values())
            room = max(0.0, s.max_leverage * eq - used)
            qty = min(qty, room / fill)
            if qty * fill < 5:  # OKX minimums / not worth it
                continue
            cash -= qty * fill * s.fee_rate
            positions[sym] = Pos(sym, sig.side, qty, fill, sig.stop, fill, ts)

        # ---- 2) stops, funding, trailing
        for sym, p in list(positions.items()):
            f = frames[sym]
            if ts not in f.index:
                continue
            bar = f.loc[ts]
            p.funding += p.qty * bar["close"] * s.funding_per_8h / bars_per_8h
            if p.side == "long" and bar["low"] <= p.stop:
                close(p, min(p.stop, bar["open"]), ts, "stop")
                continue
            if p.side == "short" and bar["high"] >= p.stop:
                close(p, max(p.stop, bar["open"]), ts, "stop")
                continue
            p.extreme = max(p.extreme, bar["high"]) if p.side == "long" else min(p.extreme, bar["low"])
            p.stop = trail_stop(p.side, p.stop, p.extreme, bar["atr"], s)

        # ---- 3) monthly deposit, equity + kill switches
        if deposit and ts >= next_deposit:
            nav_before = mark(ts) / units
            cash += deposit
            deposited += deposit
            units += deposit / nav_before
            peak += deposit  # a deposit is not a profit
            next_deposit += pd.Timedelta(days=deposit_days)
        eq = mark(ts)
        curve.append((ts, eq))
        navs.append((ts, eq / units))
        peak = max(peak, eq)
        if ts.date() != day:
            day, day_start = ts.date(), eq
        blocked = eq <= day_start * (1 - s.daily_loss_limit)
        if paused_until is None and eq <= peak * (1 - s.max_drawdown):
            paused_until = ts + pd.Timedelta(days=7)
            pauses += 1
            peak = eq  # reset reference after the cool-down
        if paused_until is not None:
            if ts < paused_until:
                blocked = True
            else:
                paused_until = None

        # ---- 4) new signals on this closed bar
        if not blocked:
            for sym, f in frames.items():
                if ts in f.index and sym not in positions:
                    sig = entry_signal(f.loc[ts], s)
                    if sig:
                        pending[sym] = sig

    last = index[-1]
    for p in list(positions.values()):
        close(p, frames[p.symbol]["close"].iloc[-1], last, "end")
    curve[-1] = (last, cash)
    navs[-1] = (last, cash / units)
    return Result(pd.Series(dict(curve)), trades, pauses, pd.Series(dict(navs)), deposited)


def report(r: Result, capital: float, days: int, frames) -> None:
    eq = r.equity
    tr = pd.DataFrame(r.trades)
    final = eq.iloc[-1]
    dd = (eq / eq.cummax() - 1).min()
    years = days / 365
    cagr = (final / capital) ** (1 / years) - 1 if final > 0 else -1
    monthly = eq.resample("30D").last().pct_change().dropna()
    print(f"\n=== Backtest: {days} days, start ${capital:,.0f} ===")
    print(f"Final equity      : ${final:,.2f}  ({final / capital - 1:+.1%})")
    print(f"Yearly (CAGR)     : {cagr:+.1%}")
    print(f"Worst drawdown    : {dd:.1%}")
    print(f"Drawdown pauses   : {r.pauses}")
    if len(tr):
        wins = tr[tr.pnl > 0]
        losses = tr[tr.pnl <= 0]
        pf = wins.pnl.sum() / -losses.pnl.sum() if len(losses) and losses.pnl.sum() else math.inf
        print(f"Trades            : {len(tr)}  (win rate {len(wins) / len(tr):.0%}, profit factor {pf:.2f})")
        print(f"Avg win / avg loss: ${wins.pnl.mean():.2f} / ${losses.pnl.mean():.2f}")
        print(f"Biggest win       : ${tr.pnl.max():.2f}   biggest loss: ${tr.pnl.min():.2f}")
    print(f"30-day periods    : {len(monthly)}  positive {(monthly > 0).mean():.0%}")
    print(f"  best / median / worst 30d: {monthly.max():+.1%} / {monthly.median():+.1%} / {monthly.min():+.1%}")
    print(f"  30d periods >= +500% (the 6x goal): {(monthly >= 5).sum()}")
    for sym, f in frames.items():
        print(f"Buy & hold {sym.split('/')[0]:4s}: {f['close'].iloc[-1] / f['close'].iloc[0] - 1:+.1%}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=1095)
    ap.add_argument("--capital", type=float, default=200.0)
    a = ap.parse_args()
    frames = {sym: fetch_history(sym, SETTINGS.timeframe, a.days + 60, SETTINGS.data_dir)
              for sym in SETTINGS.symbols}
    r = run(frames, SETTINGS, a.capital)
    report(r, a.capital, a.days, frames)
    if r.trades:
        pd.DataFrame(r.trades).to_csv(f"{SETTINGS.data_dir}/backtest_trades.csv", index=False)


if __name__ == "__main__":
    main()
