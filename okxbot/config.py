"""All tunable settings in one place. Secrets come from .env, never from code."""
import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    # --- Exchange ---
    api_key: str = os.getenv("OKX_API_KEY", "")
    api_secret: str = os.getenv("OKX_API_SECRET", "")
    api_passphrase: str = os.getenv("OKX_API_PASSPHRASE", "")
    demo: bool = _bool("OKX_DEMO", True)  # True = OKX demo trading (fake money)

    # --- Markets ---
    symbols: tuple = ("BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT")
    timeframe: str = "4h"

    # --- Strategy (trend following) ---
    donchian_len: int = 40      # breakout of the 40-bar high/low (robust in parameter sweep)
    ema_fast: int = 50
    ema_slow: int = 200
    adx_len: int = 14
    adx_min: float = 15.0       # only trade when some trend exists
    atr_len: int = 14
    stop_atr: float = 2.5       # initial stop distance in ATRs
    trail_atr: float = 3.0      # chandelier trailing stop in ATRs

    # --- Risk ("Balanced") - HARD limits, the AI brain can never raise these ---
    risk_per_trade: float = 0.02     # 2% of equity lost if the stop is hit
    max_leverage: float = 3.0        # total notional / equity
    max_positions: int = 3
    daily_loss_limit: float = 0.06   # stop trading for the day at -6%
    max_drawdown: float = 0.20       # pause everything at -20% from peak
    # Trade as if the account held only this much (+ the bot's own P&L). 0 = use the real balance.
    # Useful on demo, which starts with ~$90k of fake money.
    trading_capital: float = float(os.getenv("TRADING_CAPITAL", "0"))

    # --- Costs used by the backtest ---
    fee_rate: float = 0.0005         # OKX taker fee (entry tier)
    slippage: float = 0.0003
    funding_per_8h: float = 0.0001   # conservative: always charged

    # --- AI brain ---
    brain_enabled: bool = _bool("BRAIN_ENABLED", True)
    brain_model: str = os.getenv("BRAIN_MODEL", "claude-opus-5-5")
    brain_effort: str = os.getenv("BRAIN_EFFORT", "high")
    news_scan_hours: int = int(os.getenv("NEWS_SCAN_HOURS", "24"))

    # --- Alerts ---
    telegram_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "")

    data_dir: str = os.getenv("DATA_DIR", os.path.join(os.path.dirname(__file__), "..", "data"))


SETTINGS = Settings()
