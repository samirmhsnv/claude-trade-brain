"""Monthly stock picker + paper portfolio.

    python -m picker.run                    # daily: update paper portfolio; every 30 days picks + AI review
    python -m picker.run --value 1500       # also show what to buy if your real account is worth $1500
    python -m picker.run --force            # pick now, even if 30 days have not passed
    python -m picker.run --status           # paper portfolio vs buy-QQQ vs bank

The paper portfolio starts with $200 and adds $100 every 30 days, exactly like your plan,
and is compared live against buying QQQ monthly and a 5% bank deposit.
"""
import argparse
import json
import os
from datetime import datetime, timezone

import pandas as pd

from okxbot import notify
from okxbot.brain import Brain, BrainError
from okxbot.config import SETTINGS

from . import config as C
from .brain import review_picks
from .data import price_table
from .strategy import ranking


def load() -> dict:
    if os.path.exists(C.STATE_PATH):
        with open(C.STATE_PATH) as f:
            return json.load(f)
    return {"cash": 200.0, "units": {}, "paid": 200.0, "qqq_units": 0.0, "bank": 200.0,
            "last_rebalance": None, "last_update": None, "history": [], "picks_log": []}


def save(st: dict) -> None:
    os.makedirs(os.path.dirname(C.STATE_PATH), exist_ok=True)
    tmp = C.STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f, indent=2, default=str)
    os.replace(tmp, C.STATE_PATH)


def value(st: dict, prices: pd.Series) -> float:
    return st["cash"] + sum(u * prices[t] for t, u in st["units"].items())


def choose(px: pd.DataFrame, today, use_brain: bool) -> tuple[list[str], list[dict], dict | None]:
    r = ranking(px[C.UNIVERSE], today)
    elig = r[r.eligible].head(C.REVIEW_CANDIDATES)
    candidates = [{"ticker": t, "price": round(row.price, 2), "momentum_pct": round(row.momentum * 100, 1),
                   **{k: round(v * 100, 1) for k, v in row.items() if k.startswith("ret_")}}
                  for t, row in elig.iterrows()]
    review = None
    if use_brain and candidates:
        try:
            review = review_picks(Brain(), candidates)
        except (BrainError, Exception) as e:  # noqa: BLE001 - rules still work without the AI
            notify.send(f"🧠 Stock review failed ({e}); using the rule picks unchanged")
    dropped = {x["ticker"] for x in (review or {}).get("reviews", []) if x["decision"] == "drop"}
    picks = [c["ticker"] for c in candidates if c["ticker"] not in dropped][: C.TOP_N]
    return picks, candidates, review


def rebalance(st: dict, prices: pd.Series, picks: list[str]) -> None:
    val = value(st, prices)
    target = {t: val / C.TOP_N for t in picks}  # empty slots stay in cash
    turnover = sum(abs(target.get(t, 0) - st["units"].get(t, 0) * prices[t]) for t in set(target) | set(st["units"]))
    st["cash"] = val - sum(target.values()) - turnover * C.FEE
    st["units"] = {t: v / prices[t] for t, v in target.items()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--value", type=float, help="your real account value in $ (for the buy list)")
    ap.add_argument("--deposit", type=float, default=100.0)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()

    st = load()
    px = price_table(C.UNIVERSE + [C.BENCHMARK], years=2)
    today = px.index[-1]
    prices = px.loc[today]
    now = datetime.now(timezone.utc)

    # daily bank interest for the comparison
    if st["last_update"]:
        days = (now - datetime.fromisoformat(st["last_update"])).total_seconds() / 86400
        st["bank"] *= (1 + C.BANK_RATE) ** (days / 365)
    st["last_update"] = now.isoformat()

    due = st["last_rebalance"] is None or (now - datetime.fromisoformat(st["last_rebalance"])).days >= C.REBALANCE_DAYS
    if (due or a.force) and not a.status:
        first = st["last_rebalance"] is None
        if not first:  # monthly deposit into all three paper accounts
            st["cash"] += a.deposit
            st["paid"] += a.deposit
            st["bank"] += a.deposit
        st["qqq_units"] += (200.0 if first else a.deposit) / prices[C.BENCHMARK]
        picks, candidates, review = choose(px, today, SETTINGS.brain_enabled)
        rebalance(st, prices, picks)
        st["last_rebalance"] = now.isoformat()
        st["picks_log"].append({"date": str(today.date()), "picks": picks,
                                "review": [r for r in (review or {}).get("reviews", [])]})

        lines = [f"📈 Monthly picks {today:%Y-%m-%d}: {', '.join(picks) or 'ALL CASH (nothing in an uptrend)'}"]
        for c in candidates:
            rv = next((r for r in (review or {}).get("reviews", []) if r["ticker"] == c["ticker"]), None)
            tag = "✅" if c["ticker"] in picks else ("❌ AI drop" if rv and rv["decision"] == "drop" else "· spare")
            lines.append(f"  {tag} {c['ticker']:6s} momentum {c['momentum_pct']:+.1f}%"
                         + (f" - {rv['reason']}" if rv else ""))
        if a.value:
            lines.append(f"\nWith your ${a.value:,.0f}: hold about ${a.value / C.TOP_N:,.0f} in each pick:")
            for t in picks:
                lines.append(f"  {t}: ~{a.value / C.TOP_N / prices[t]:.3f} shares at ${prices[t]:,.2f}")
            lines.append("  Sell anything you hold that is not on this list.")
        if review:
            lines.append(f"\n🧠 {review.get('market_summary', '')}")
        notify.send("\n".join(lines))

    v, q = value(st, prices), st["qqq_units"] * prices[C.BENCHMARK]
    st["history"].append({"date": str(today.date()), "picker": round(v, 2), "qqq": round(q, 2),
                          "bank": round(st["bank"], 2), "paid": st["paid"]})
    save(st)
    print(f"Paper results since {st['picks_log'][0]['date'] if st['picks_log'] else 'today'} "
          f"(paid in ${st['paid']:,.0f}):")
    print(f"  Picker      ${v:,.2f}  ({v - st['paid']:+,.2f})   holding: {', '.join(st['units']) or 'cash'}")
    print(f"  Buy {C.BENCHMARK}     ${q:,.2f}  ({q - st['paid']:+,.2f})")
    print(f"  Bank {C.BANK_RATE:.0%}     ${st['bank']:,.2f}  ({st['bank'] - st['paid']:+,.2f})")


if __name__ == "__main__":
    main()
