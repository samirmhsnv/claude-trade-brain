"""Forward-test report: how is the bot doing, and is the brain earning its cost?

    python -m okxbot.report
"""
from collections import Counter
from datetime import datetime, timezone

import pandas as pd

from . import backtest as bt
from . import journal
from .config import SETTINGS as S
from .data import fetch_history


def main():
    st = journal.load_state()
    curve = journal.equity_curve()
    if not curve:
        print("No data yet - run the bot first.")
        return
    start_ts, start_eq = curve[0]
    _, eq = curve[-1]
    started = datetime.fromisoformat(start_ts)
    days = max((datetime.now(timezone.utc) - started).total_seconds() / 86400, 0.01)
    eqs = pd.Series([e for _, e in curve])
    dd = (eqs / eqs.cummax() - 1).min()

    ev = journal.events()
    kinds = Counter(e["kind"] for e in ev)
    signals = [e for e in ev if e["kind"] == "signal"]
    vetoes = [e for e in signals if e["multiplier"] <= 0]
    reduced = [e for e in signals if 0 < e["multiplier"] < 1]
    cost = st.get("brain_cost_usd", 0.0)

    print(f"=== Forward test: {days:.1f} days ({'DEMO' if S.demo else 'LIVE'}) ===")
    print(f"Equity           : ${start_eq:.2f} -> ${eq:.2f}  ({eq / start_eq - 1:+.2%})")
    print(f"Worst drawdown   : {dd:.2%}")
    print(f"Trades opened    : {kinds['open']}, closed {kinds['close']}, stop moves {kinds['trail']}")
    print(f"Signals          : {len(signals)}  (brain vetoed {len(vetoes)}, reduced {len(reduced)})")
    print(f"News scans       : {kinds['scan']}   errors: {kinds['error']}")
    print(f"Brain API cost   : ${cost:.2f}  (~${cost / days * 30:.2f}/month at this pace)")

    # Rule-only shadow: what the plain strategy did over the same window
    lookback = int(days) + 60
    frames = {s: fetch_history(s, S.timeframe, lookback, S.data_dir) for s in S.symbols}
    frames = {s: f for s, f in frames.items()}
    r = bt.run(frames, S, start_eq)
    window = r.equity[r.equity.index >= pd.Timestamp(started)]
    if len(window) > 1:
        shadow = window.iloc[-1] / window.iloc[0] - 1
        print(f"Rules-only (same window, backtest): {shadow:+.2%}")
        print(f"Brain value-add  : {(eq / start_eq - 1) - shadow:+.2%} before API cost, "
              f"{(eq - cost) / start_eq - 1 - shadow:+.2%} after")

    print("\nRecent brain decisions:")
    for e in signals[-5:]:
        rv = e.get("review") or {}
        print(f"  {e['ts'][:16]} {e['symbol']} {e['side']} x{e['multiplier']:.2f} - {rv.get('reasoning', '')[:160]}")
    print("\nReminder: 1-4 weeks is far too short to judge a strategy that makes most of its money "
          "from a few big trends. Judge mainly on: did it follow the rules, were stops always in "
          "place, and were there zero unexplained errors?")


if __name__ == "__main__":
    main()
