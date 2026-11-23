"""Thin, defensive wrapper around ccxt for OKX USDT perpetuals (one-way mode, cross margin)."""
import json
import os
import socket
import time

import ccxt
import pandas as pd
import urllib3.util.connection

from .casebook import fear_greed_now
from .config import SETTINGS
from .data import TF_MS

# Optional: the OKX API key is IP-whitelisted. FORCE_IPV4=true makes requests use the server's
# IPv4 address. Off by default: from this server, OKX is only reachable over IPv6.
if os.getenv("FORCE_IPV4", "false").lower() in ("1", "true", "yes"):
    urllib3.util.connection.allowed_gai_family = lambda: socket.AF_INET


class OKX:
    def __init__(self, s=SETTINGS):
        if not (s.api_key and s.api_secret and s.api_passphrase):
            raise SystemExit("Missing OKX_API_KEY / OKX_API_SECRET / OKX_API_PASSPHRASE in .env")
        self.s = s
        self.ex = ccxt.okx({
            "apiKey": s.api_key, "secret": s.api_secret, "password": s.api_passphrase,
            "enableRateLimit": True, "options": {"defaultType": "swap"},
        })
        if s.demo:
            self.ex.set_sandbox_mode(True)  # adds x-simulated-trading: 1
        self.ex.load_markets()

    # ---------- setup ----------
    def setup(self):
        try:
            self.ex.set_position_mode(False)  # one-way ("net") mode
        except ccxt.BaseError as e:
            # fails harmlessly if already set or positions are open
            print(f"[setup] position mode: {e}")
        for sym in self.s.symbols:
            self.ex.set_leverage(int(self.s.max_leverage), sym, {"mgnMode": "cross"})

    # ---------- reads ----------
    def equity(self) -> float:
        bal = self.ex.fetch_balance()
        real = float(bal["info"]["data"][0]["totalEq"])
        cap = self.s.trading_capital
        if cap <= 0:
            return real
        # Virtual account: TRADING_CAPITAL + (real balance change since the first run).
        path = os.path.join(self.s.data_dir, "capital_baseline.json")
        try:
            with open(path) as f:
                base = json.load(f)
        except FileNotFoundError:
            base = None
        if not base or base.get("capital") != cap:
            base = {"capital": cap, "real_start": real}
            os.makedirs(self.s.data_dir, exist_ok=True)
            with open(path, "w") as f:
                json.dump(base, f)
        return cap + (real - base["real_start"])

    def closed_candles(self, sym: str, n: int = 1000) -> pd.DataFrame:
        """~1000 bars so EMA200/ADX match the backtest (which uses full history)."""
        step = TF_MS[self.s.timeframe]
        now = self.ex.milliseconds()
        since, rows = now - n * step, []
        while since < now - step:
            batch = self.ex.fetch_ohlcv(sym, self.s.timeframe, since=since, limit=100,
                                        params={"method": "publicGetMarketHistoryCandles"})
            if not batch:
                break
            rows += batch
            since = batch[-1][0] + 1
        rows += self.ex.fetch_ohlcv(sym, self.s.timeframe, limit=5)  # newest bars
        rows = list({r[0]: r for r in rows}.values())
        rows.sort(key=lambda r: r[0])
        rows = [r for r in rows if r[0] + TF_MS[self.s.timeframe] <= now]  # drop the forming candle
        df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
        df.index = pd.to_datetime(df["ts"], unit="ms", utc=True)
        return df

    def positions(self) -> dict:
        out = {}
        for p in self.ex.fetch_positions(list(self.s.symbols)):
            if p.get("contracts") and float(p["contracts"]) > 0:
                out[p["symbol"]] = p
        return out

    def open_stops(self, sym: str) -> list:
        return self.ex.fetch_open_orders(sym, params={"ordType": "conditional"})

    # ---------- sizing ----------
    def coins_to_contracts(self, sym: str, coins: float) -> float:
        m = self.ex.market(sym)
        contracts = float(self.ex.amount_to_precision(sym, coins / float(m["contractSize"])))
        min_amt = (m.get("limits", {}).get("amount", {}) or {}).get("min") or 0
        return contracts if contracts >= min_amt else 0.0

    def contract_size(self, sym: str) -> float:
        return float(self.ex.market(sym)["contractSize"])

    # ---------- writes ----------
    def market_order(self, sym: str, side: str, contracts: float, reduce_only: bool = False) -> dict:
        return self.ex.create_order(sym, "market", side, contracts, None,
                                    {"tdMode": "cross", "reduceOnly": reduce_only})

    def place_stop(self, sym: str, pos_side: str, contracts: float, stop: float) -> str:
        """Exchange-side stop-loss: it protects the position even if this bot is offline."""
        side = "sell" if pos_side == "long" else "buy"
        o = self.ex.create_order(sym, "market", side, contracts, None,
                                 {"stopLossPrice": stop, "reduceOnly": True, "tdMode": "cross"})
        return o["id"]

    def cancel_stop(self, sym: str, algo_id: str):
        self.ex.cancel_order(algo_id, sym, {"trigger": True})

    def replace_stop(self, sym: str, pos_side: str, contracts: float, new_stop: float, old_ids: list[str]) -> str:
        """Place the new stop FIRST, then cancel the old ones - never leave a position naked."""
        new_id = self.place_stop(sym, pos_side, contracts, new_stop)
        for oid in old_ids:
            try:
                self.cancel_stop(sym, oid)
            except ccxt.BaseError as e:
                print(f"[stop] cancel {oid} failed: {e}")
        return new_id


def market_snapshot_public() -> dict:
    """Public data only (no key): price, changes, funding, mood."""
    ex = ccxt.okx({"enableRateLimit": True, "options": {"defaultType": "swap"}})
    snap = {}
    for sym in SETTINGS.symbols:
        coin = sym.split("/")[0]
        try:
            t = ex.fetch_ticker(sym)
            d = ex.fetch_ohlcv(sym, "1d", limit=8)
            fr = ex.fetch_funding_rate(sym).get("fundingRate")
            snap[coin] = {
                "price": t["last"],
                "change_24h_pct": round(t.get("percentage") or 0, 2),
                "change_7d_pct": round((d[-1][4] / d[0][4] - 1) * 100, 2) if len(d) >= 8 else None,
                "funding_rate_8h_pct": round(float(fr) * 100, 4) if fr is not None else None,
            }
        except ccxt.BaseError as e:
            snap[coin] = {"error": str(e)}
        time.sleep(0.2)
    try:
        v, label = fear_greed_now()
        snap["fear_greed"] = f"{v} ({label})"
    except Exception as e:  # noqa: BLE001 - mood index is optional
        snap["fear_greed"] = f"unavailable: {e}"
    return snap
