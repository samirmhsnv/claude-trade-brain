"""Trend-following rules. Pure functions, shared by the backtest and the live bot,
so what you backtest is exactly what trades."""
from dataclasses import dataclass

import pandas as pd

from . import indicators as ind
from .config import Settings


def prepare(df: pd.DataFrame, s: Settings) -> pd.DataFrame:
    df = df.copy()
    df["ema_fast"] = ind.ema(df["close"], s.ema_fast)
    df["ema_slow"] = ind.ema(df["close"], s.ema_slow)
    df["atr"] = ind.atr(df, s.atr_len)
    df["adx"] = ind.adx(df, s.adx_len)
    # Channel of the PREVIOUS n bars (no look-ahead)
    df["dc_high"] = df["high"].shift(1).rolling(s.donchian_len).max()
    df["dc_low"] = df["low"].shift(1).rolling(s.donchian_len).min()
    return df


@dataclass
class Signal:
    side: str          # "long" | "short"
    stop: float        # initial stop price
    atr: float
    reason: str


def entry_signal(row: pd.Series, s: Settings) -> Signal | None:
    """Evaluated on a CLOSED candle. Returns a signal or None."""
    if pd.isna(row["ema_slow"]) or pd.isna(row["dc_high"]) or pd.isna(row["adx"]):
        return None
    if row["adx"] < s.adx_min:
        return None
    c, a = row["close"], row["atr"]
    if c > row["dc_high"] and c > row["ema_slow"] and row["ema_fast"] > row["ema_slow"]:
        return Signal("long", c - s.stop_atr * a, a,
                      f"{s.donchian_len}-bar breakout up, uptrend (EMA{s.ema_fast}>EMA{s.ema_slow}), ADX {row['adx']:.0f}")
    if c < row["dc_low"] and c < row["ema_slow"] and row["ema_fast"] < row["ema_slow"]:
        return Signal("short", c + s.stop_atr * a, a,
                      f"{s.donchian_len}-bar breakdown, downtrend (EMA{s.ema_fast}<EMA{s.ema_slow}), ADX {row['adx']:.0f}")
    return None


def trail_stop(side: str, current_stop: float, extreme: float, atr: float, s: Settings) -> float:
    """Chandelier stop: only ever moves in the trade's favour.
    `extreme` = highest high since entry (long) / lowest low since entry (short)."""
    if side == "long":
        return max(current_stop, extreme - s.trail_atr * atr)
    return min(current_stop, extreme + s.trail_atr * atr)
