"""Backtest the monthly picker with $200 start + $100 every 30 days.

    python -m picker.backtest              # fair test on the Jan-2021 top-20 list
    python -m picker.backtest --current    # today's list (has hindsight bias!)
"""
import argparse

import pandas as pd

from . import config as C
from .data import price_table
from .strategy import ranking


def simulate(px: pd.DataFrame, start, end, top_n=C.TOP_N, start_cash=200.0, deposit=100.0, holding_cost=0.0):
    days = px.index[(px.index >= start) & (px.index <= end)]
    cash, units = start_cash, pd.Series(0.0, index=px.columns)
    paid, nav_units = start_cash, start_cash
    values, navs, log = [], [], []
    for i, d in enumerate(days):
        prices = px.loc[d]
        val = cash + (units * prices).sum()
        if i and i % C.REBALANCE_DAYS == 0:
            nav_units += deposit / (val / nav_units)
            cash += deposit
            paid += deposit
            val += deposit
        if i % C.REBALANCE_DAYS == 0:
            r = ranking(px, d)
            picks = list(r[r.eligible].index[:top_n])
            target = pd.Series(0.0, index=px.columns)
            target[picks] = val / top_n
            turnover = (target - units * prices).abs().sum()
            cash = val - target.sum() - turnover * C.FEE
            units = (target / prices).fillna(0)
            log.append((d.date(), picks))
        if holding_cost:
            units *= 1 - holding_cost / 365
        val = cash + (units * prices).sum()
        values.append((d, val))
        navs.append((d, val / nav_units))
    return pd.Series(dict(values)), pd.Series(dict(navs)), paid, log


def bank(days: int, start_cash=200.0, deposit=100.0):
    bal = paid = start_cash
    for i in range(days):
        if i and i % C.REBALANCE_DAYS == 0:
            bal += deposit
            paid += deposit
        bal *= (1 + C.BANK_RATE) ** (1 / 365)
    return bal, paid


def buy_monthly(series: pd.Series, start_cash=200.0, deposit=100.0):
    units, paid = start_cash / series.iloc[0], start_cash
    for i in range(C.REBALANCE_DAYS, len(series), C.REBALANCE_DAYS):
        units += deposit / series.iloc[i]
        paid += deposit
    return units * series.iloc[-1], paid


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--current", action="store_true", help="use today's list (biased)")
    a = ap.parse_args()
    universe = C.UNIVERSE if a.current else C.UNIVERSE_2021
    px = price_table(universe + [C.BENCHMARK])
    stocks, bench = px[universe], px[C.BENCHMARK]
    end = px.index[-1]
    print(f"Universe: {'TODAY (hindsight-biased)' if a.current else 'top-20 of Jan 2021 (fair)'}")
    for years in (3, 5):
        start = end - pd.Timedelta(days=365 * years)
        print(f"\n=== last {years} years, $200 + $100 every 30 days ===")
        b, paid = bank(365 * years)
        print(f"  {'Bank deposit ' + format(C.BANK_RATE, '.0%'):30s} paid ${paid:,.0f} -> ${b:,.0f}  profit ${b - paid:+,.0f}")
        q, paid = buy_monthly(bench[bench.index >= start])
        print(f"  {'Buy ' + C.BENCHMARK + ' every month':30s} paid ${paid:,.0f} -> ${q:,.0f}  profit ${q - paid:+,.0f}")
        v, nav, paid, log = simulate(stocks, start, end)
        dd = (nav / nav.cummax() - 1).min()
        print(f"  {'Picker (top ' + str(C.TOP_N) + ')':30s} paid ${paid:,.0f} -> ${v.iloc[-1]:,.0f}  "
              f"profit ${v.iloc[-1] - paid:+,.0f}  worst drop {dd:.0%}")
    print("\nLast 3 monthly picks:")
    for d, p in log[-3:]:
        print(f"  {d}: {', '.join(p) or 'all cash'}")


if __name__ == "__main__":
    main()
