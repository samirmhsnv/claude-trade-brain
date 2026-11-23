"""'Previous cases': every historical trade the strategy would have taken, annotated with
the market mood (Fear & Greed index) at the time. The brain compares today's setup
against these REAL outcomes instead of guessing.

    python -m okxbot.casebook          # (re)build data/casebook.csv
"""
import os

import pandas as pd
import requests

from . import backtest as bt
from .config import SETTINGS
from .data import fetch_history

CASEBOOK = os.path.join(SETTINGS.data_dir, "casebook.csv")


def fear_greed_history() -> pd.Series:
    r = requests.get("https://api.alternative.me/fng/?limit=0", timeout=20)
    r.raise_for_status()
    d = pd.DataFrame(r.json()["data"])
    s = pd.Series(d["value"].astype(int).values,
                  index=pd.DatetimeIndex(pd.to_datetime(d["timestamp"].astype(int), unit="s", utc=True)).normalize())
    return s.sort_index()


def fear_greed_now() -> tuple[int, str]:
    r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=20)
    r.raise_for_status()
    d = r.json()["data"][0]
    return int(d["value"]), d["value_classification"]


def build(days: int = 1095) -> pd.DataFrame:
    frames = {s: fetch_history(s, SETTINGS.timeframe, days + 60, SETTINGS.data_dir) for s in SETTINGS.symbols}
    r = bt.run(frames, SETTINGS, 10_000)
    tr = pd.DataFrame(r.trades)
    fng = fear_greed_history()
    tr["opened"] = pd.to_datetime(tr["opened"], utc=True)
    tr["fng"] = [int(fng.asof(t.normalize())) if len(fng) else None for t in tr["opened"]]
    tr["ret_pct"] = (tr["exit"] / tr["entry"] - 1) * 100 * tr["side"].map({"long": 1, "short": -1})
    tr["days_held"] = (pd.to_datetime(tr["closed"], utc=True) - tr["opened"]).dt.total_seconds() / 86400
    tr["win"] = tr["pnl"] > 0
    tr.to_csv(CASEBOOK, index=False)
    return tr


def load() -> pd.DataFrame:
    if not os.path.exists(CASEBOOK):
        return build()
    return pd.read_csv(CASEBOOK, parse_dates=["opened", "closed"])


def similar_cases(symbol: str, side: str, fng: int) -> str:
    """A plain-text summary of comparable past trades for the brain."""
    cb = load()

    def stats(df: pd.DataFrame, label: str) -> str:
        if df.empty:
            return f"- {label}: no cases"
        return (f"- {label}: {len(df)} cases, win rate {df.win.mean():.0%}, "
                f"avg move {df.ret_pct.mean():+.1f}%, best {df.ret_pct.max():+.1f}%, "
                f"worst {df.ret_pct.min():+.1f}%, avg hold {df.days_held.mean():.1f}d")

    same = cb[(cb.symbol == symbol) & (cb.side == side)]
    mood = cb[(cb.side == side) & ((cb.fng - fng).abs() <= 15)]
    both = same[(same.fng - fng).abs() <= 15]
    recent = same.sort_values("opened").tail(5)
    lines = [
        f"Historical {side} signals from this exact strategy (3y backtest, real OKX prices, fees included):",
        stats(same, f"{symbol} {side}, any mood"),
        stats(mood, f"all coins {side}, Fear&Greed within 15 of {fng}"),
        stats(both, f"{symbol} {side}, Fear&Greed within 15 of {fng}"),
        "Last 5 cases for this coin/side:",
    ]
    for _, t in recent.iterrows():
        lines.append(f"  {t.opened:%Y-%m-%d} F&G={t.fng} -> {t.ret_pct:+.1f}% in {t.days_held:.1f}d")
    return "\n".join(lines)


if __name__ == "__main__":
    tb = build()
    print(f"casebook: {len(tb)} trades -> {CASEBOOK}")
    print(similar_cases("BTC/USDT:USDT", "long", 25))
