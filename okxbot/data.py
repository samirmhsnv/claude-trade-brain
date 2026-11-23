"""Historical candles from OKX public API (no API key needed), cached to CSV."""
import os
import time

import ccxt
import pandas as pd

TF_MS = {"1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}


def public_exchange() -> ccxt.okx:
    return ccxt.okx({"enableRateLimit": True})


def fetch_history(symbol: str, timeframe: str, days: int, cache_dir: str) -> pd.DataFrame:
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, f"{symbol.replace('/', '_').replace(':', '_')}_{timeframe}.csv")
    ex = public_exchange()
    since = ex.milliseconds() - days * 86_400_000

    cached = None
    if os.path.exists(path):
        cached = pd.read_csv(path)
        if len(cached) and cached["ts"].iloc[0] <= since:
            since = int(cached["ts"].iloc[-1]) + 1
    rows = []
    step = TF_MS[timeframe]
    while since < ex.milliseconds() - step:
        for attempt in range(6):
            try:
                batch = ex.fetch_ohlcv(symbol, timeframe, since=since, limit=100,
                                       params={"method": "publicGetMarketHistoryCandles"})
                break
            except (ccxt.RateLimitExceeded, ccxt.NetworkError):
                time.sleep(2 ** attempt)
        else:
            raise RuntimeError(f"OKX kept rate-limiting {symbol}")
        if not batch:
            break
        rows += batch
        since = batch[-1][0] + 1
        time.sleep(0.12)
    new = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    df = pd.concat([cached, new]) if cached is not None and len(cached) else new
    df = df.drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
    df.to_csv(path, index=False)
    df = df[df["ts"] >= ex.milliseconds() - days * 86_400_000]
    df.index = pd.to_datetime(df["ts"], unit="ms", utc=True)
    return df
