"""Monthly stock picker (momentum rotation) settings."""
import os

# The 20 largest US companies. Review this list every January and replace companies
# that dropped out of the top 20. Use a RULE (largest by market value), never
# "stocks that went up" - that is how hindsight bias sneaks in.
UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "AVGO", "TSLA", "BRK-B", "JPM",
    "LLY", "V", "WMT", "ORCL", "MA", "XOM", "NFLX", "COST", "JNJ", "HD",
]
# Fixed list used for the fair backtest: the 20 largest US companies as of January 2021
UNIVERSE_2021 = [
    "AAPL", "MSFT", "AMZN", "GOOGL", "META", "TSLA", "BRK-B", "V", "JNJ", "WMT",
    "JPM", "MA", "PG", "UNH", "NVDA", "DIS", "HD", "PYPL", "BAC", "INTC",
]
BENCHMARK = "QQQ"

TOP_N = 3                  # hold the 3 strongest
LOOKBACKS = (90, 180)      # momentum = average of 3- and 6-month return
TREND_SMA = 200            # only buy stocks above their 200-day average
REBALANCE_DAYS = 30
REVIEW_CANDIDATES = 6      # the AI reviews the top 6, so it can swap out a bad pick

FEE = 0.001                # broker fee + slippage per side, conservative
BANK_RATE = 0.05           # what the money would earn in the bank instead

DATA_DIR = os.getenv("DATA_DIR", os.path.join(os.path.dirname(__file__), "..", "data"))
YAHOO_DIR = os.path.join(DATA_DIR, "yahoo")
STATE_PATH = os.path.join(DATA_DIR, "picker_state.json")
