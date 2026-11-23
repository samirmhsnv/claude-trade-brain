"""Ranking rule shared by the backtest and the live monthly run."""
import pandas as pd

from .config import LOOKBACKS, TREND_SMA


def ranking(px: pd.DataFrame, day: pd.Timestamp) -> pd.DataFrame:
    """All stocks on `day`, strongest first, with the reason each one passes or fails."""
    hist = px[px.index <= day]
    last = hist.iloc[-1]
    sma = hist.iloc[-TREND_SMA:].mean() if len(hist) >= TREND_SMA else pd.Series(float("nan"), index=px.columns)
    moms = {f"ret_{lb}d": last / hist.iloc[-lb - 1] - 1 for lb in LOOKBACKS if len(hist) > lb}
    df = pd.DataFrame(moms)
    df["momentum"] = df.mean(axis=1)
    df["price"] = last
    df["above_200d"] = last > sma
    df["eligible"] = df["above_200d"] & (df["momentum"] > 0)
    return df.sort_values(["eligible", "momentum"], ascending=[False, False])
