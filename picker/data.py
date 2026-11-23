"""Daily prices from Yahoo Finance's public chart endpoint, cached for 12 hours."""
import os
import time

import pandas as pd
import requests

from .config import YAHOO_DIR


def daily(symbol: str, years: int = 8, max_age_h: float = 12) -> pd.Series:
    os.makedirs(YAHOO_DIR, exist_ok=True)
    path = os.path.join(YAHOO_DIR, f"{symbol}.csv")
    fresh = os.path.exists(path) and time.time() - os.path.getmtime(path) < max_age_h * 3600
    if not fresh:
        r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
                         params={"range": f"{years}y", "interval": "1d"},
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
        r.raise_for_status()
        d = r.json()["chart"]["result"][0]
        q = d["indicators"]["quote"][0]
        pd.DataFrame({"ts": d["timestamp"], "close": q["close"]}).dropna().to_csv(path, index=False)
        time.sleep(0.4)
    df = pd.read_csv(path)
    s = pd.Series(df["close"].values,
                  index=pd.DatetimeIndex(pd.to_datetime(df["ts"], unit="s", utc=True)).normalize())
    return s[~s.index.duplicated(keep="last")]


def price_table(symbols: list[str], years: int = 8) -> pd.DataFrame:
    """Calendar-day table; weekends/holidays carry the last close."""
    cols = {s: daily(s, years) for s in symbols}
    start = min(c.index[0] for c in cols.values())
    end = max(c.index[-1] for c in cols.values())
    cal = pd.date_range(start, end, freq="D", tz="UTC")
    return pd.DataFrame(cols).reindex(cal).ffill()
