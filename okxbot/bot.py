"""The 24/7 bot.

    python -m okxbot.bot           # run forever (checks every 15 min)
    python -m okxbot.bot --once    # one cycle, then exit (for testing)

Each cycle is idempotent, so restarts are safe: state is rebuilt from the exchange.
Stop-losses live ON THE EXCHANGE, so positions stay protected even if this process dies.
"""
import argparse
import time
import traceback
from datetime import datetime, timedelta, timezone

import ccxt

from . import casebook, journal, notify
from .brain import Brain, BrainError
from .config import SETTINGS as S
from .exchange import OKX, market_snapshot_public
from .strategy import entry_signal, prepare, trail_stop

CHECK_EVERY_S = 15 * 60


def px(x: float) -> str:
    return f"{x:,.2f}" if x >= 10 else f"{x:.4g}"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse(ts: str | None) -> datetime | None:
    return datetime.fromisoformat(ts) if ts else None


class Bot:
    def __init__(self):
        self.okx = OKX()
        self.okx.setup()
        self.brain = Brain() if S.brain_enabled else None
        self.st = journal.load_state()
        self.st.setdefault("last_signal_bar", {})
        mode = "DEMO (fake money)" if S.demo else "LIVE (real money)"
        notify.send(f"🤖 Bot started - {mode}. Equity ${self.okx.equity():.2f}. "
                    f"Brain: {S.brain_model if self.brain else 'off'}")

    # ------------------------------------------------------------------ cycle
    def cycle(self):
        st, now = self.st, utcnow()
        eq = self.okx.equity()
        journal.log_equity(eq)
        st["start_equity"] = st.get("start_equity") or eq
        st["peak_equity"] = max(st.get("peak_equity") or eq, eq)
        if st.get("day") != now.date().isoformat():
            st["day"], st["day_start_equity"] = now.date().isoformat(), eq

        paused = self._kill_switches(eq, now)
        positions = self.okx.positions()
        self._reconcile(positions)
        for sym, p in positions.items():
            self._manage(sym, p)

        if self.brain and self._scan_due(now):
            self._news_scan(list(positions))
            positions = self.okx.positions()

        daily_block = eq <= st["day_start_equity"] * (1 - S.daily_loss_limit)
        news_block = (parse(st.get("block_entries_until")) or now) > now
        if paused or daily_block or news_block:
            why = "drawdown pause" if paused else "daily loss limit" if daily_block else "news block"
            print(f"[{now:%H:%M}] entries blocked: {why}")
        else:
            for sym in S.symbols:
                if sym not in positions and len(positions) < S.max_positions:
                    if self._try_entry(sym, eq, positions):
                        positions = self.okx.positions()

        if self.brain:
            st["brain_cost_usd"] = round(st.get("brain_cost_usd", 0) + self.brain.cost_usd, 4)
            self.brain.cost_usd = 0.0
        journal.save_state(st)
        print(f"[{now:%Y-%m-%d %H:%M}] equity ${eq:.2f} | positions {list(positions) or '-'}")

    # ------------------------------------------------------------ safety
    def _kill_switches(self, eq: float, now: datetime) -> bool:
        st = self.st
        until = parse(st.get("paused_until"))
        if until and now >= until:
            st["paused_until"] = None
            st["peak_equity"] = eq
            notify.send(f"▶️ Drawdown pause over, trading resumes. Equity ${eq:.2f}")
            until = None
        if until is None and eq <= st["peak_equity"] * (1 - S.max_drawdown):
            st["paused_until"] = (now + timedelta(days=7)).isoformat()
            journal.log("pause", equity=eq, peak=st["peak_equity"])
            notify.send(f"⛔ Down {S.max_drawdown:.0%} from peak (${st['peak_equity']:.2f} → ${eq:.2f}). "
                        "No new trades for 7 days. Existing stops stay active.")
            return True
        return until is not None

    def _reconcile(self, positions: dict):
        """Positions that vanished were closed by their exchange stop (or by hand)."""
        for sym in list(self.st["positions"]):
            if sym not in positions:
                meta = self.st["positions"].pop(sym)
                for o in self.okx.open_stops(sym):
                    self.okx.cancel_stop(sym, o["id"])
                journal.log("close", sym, reason="stop hit / closed outside bot", **meta)
                notify.send(f"✅ {sym.split('/')[0]} {meta['side']} closed (stop). Entry {px(meta['entry'])}, "
                            f"last stop {px(meta['stop'])}")

    def _manage(self, sym: str, p: dict):
        df = prepare(self.okx.closed_candles(sym), S)
        last = df.iloc[-1]
        side, contracts = p["side"], float(p["contracts"])
        meta = self.st["positions"].get(sym)
        if meta is None:  # opened by hand or state lost: adopt it with a fresh stop
            entry = float(p["entryPrice"])
            stop = entry - S.stop_atr * last["atr"] if side == "long" else entry + S.stop_atr * last["atr"]
            meta = {"side": side, "entry": entry, "stop": stop, "extreme": entry,
                    "opened": df.index[-1].isoformat(), "contracts": contracts}
            notify.send(f"⚠️ Adopted untracked {sym} {side} position, stop {px(stop)}")
        since = df[df.index >= parse(meta["opened"])]
        if len(since):
            ext = since["high"].max() if side == "long" else since["low"].min()
            meta["extreme"] = max(meta["extreme"], ext) if side == "long" else min(meta["extreme"], ext)
        new_stop = trail_stop(side, meta["stop"], meta["extreme"], last["atr"], S)
        price = float(self.okx.ex.fetch_ticker(sym)["last"])
        stops = self.okx.open_stops(sym)

        if (side == "long" and new_stop >= price) or (side == "short" and new_stop <= price):
            self._close(sym, side, contracts, "price already through trailing stop", stops)
            return
        moved = abs(new_stop - meta["stop"]) / price > 0.001
        if not stops or moved or contracts != meta.get("contracts"):
            meta["algo_id"] = self.okx.replace_stop(sym, side, contracts, new_stop, [o["id"] for o in stops])
            if moved:
                journal.log("trail", sym, old=meta["stop"], new=new_stop)
        meta["stop"], meta["contracts"] = new_stop, contracts
        self.st["positions"][sym] = meta

    def _close(self, sym: str, side: str, contracts: float, why: str, stops=None):
        self.okx.market_order(sym, "sell" if side == "long" else "buy", contracts, reduce_only=True)
        for o in stops if stops is not None else self.okx.open_stops(sym):
            self.okx.cancel_stop(sym, o["id"])
        meta = self.st["positions"].pop(sym, {})
        journal.log("close", sym, reason=why, **meta)
        notify.send(f"🔻 Closed {sym.split('/')[0]} {side}: {why}")

    # ------------------------------------------------------------- brain
    def _scan_due(self, now: datetime) -> bool:
        last = parse(self.st.get("last_scan"))
        return last is None or now - last >= timedelta(hours=S.news_scan_hours)

    def _news_scan(self, open_syms: list[str]):
        self.st["last_scan"] = utcnow().isoformat()
        try:
            d = self.brain.news_scan(market_snapshot_public(), [s.split("/")[0] for s in open_syms])
        except BrainError as e:
            notify.send(f"🧠 News scan failed: {e}")
            return
        journal.log("scan", **d)
        if d["block_new_entries_hours"]:
            self.st["block_entries_until"] = (utcnow() + timedelta(hours=d["block_new_entries_hours"])).isoformat()
        notify.send(f"🧠 Risk {d['market_risk'].upper()} | bias {d['bias']} | "
                    f"block {d['block_new_entries_hours']}h\n{d['summary']}")
        positions = self.okx.positions()
        for coin in d["close_positions"]:
            sym = f"{coin}/USDT:USDT"
            if sym in positions:
                p = positions[sym]
                self._close(sym, p["side"], float(p["contracts"]), "brain: extreme risk")

    # ------------------------------------------------------------- entry
    def _try_entry(self, sym: str, eq: float, positions: dict) -> bool:
        df = prepare(self.okx.closed_candles(sym), S)
        bar = df.index[-1].isoformat()
        if self.st["last_signal_bar"].get(sym) == bar:
            return False  # this candle was already evaluated
        self.st["last_signal_bar"][sym] = bar
        sig = entry_signal(df.iloc[-1], S)
        if not sig:
            return False

        price = float(self.okx.ex.fetch_ticker(sym)["last"])
        if (sig.side == "long" and sig.stop >= price) or (sig.side == "short" and sig.stop <= price):
            return False
        coins = eq * S.risk_per_trade / abs(price - sig.stop)
        used = sum(float(p["contracts"]) * self.okx.contract_size(s) * float(p.get("markPrice") or p["entryPrice"])
                   for s, p in positions.items())
        coins = min(coins, max(0.0, S.max_leverage * eq - used) / price)

        mult, review = 1.0, None
        if self.brain:
            try:
                fng, _ = casebook.fear_greed_now()
                review = self.brain.review_trade(sym, sig.side, sig.reason, market_snapshot_public(),
                                                 casebook.similar_cases(sym, sig.side, fng))
                mult = review["size_multiplier"]
            except (BrainError, Exception) as e:  # noqa: BLE001 - never let the brain crash the bot
                mult = 0.5
                notify.send(f"🧠 Review failed for {sym} ({e}); trading at half size")
        journal.log("signal", sym, side=sig.side, reason=sig.reason, price=price, stop=sig.stop,
                    multiplier=mult, review={k: v for k, v in (review or {}).items() if k != "briefing"})
        if mult <= 0:
            notify.send(f"🧠 VETO {sym.split('/')[0]} {sig.side}: {review['reasoning'][:600]}")
            return False

        contracts = self.okx.coins_to_contracts(sym, coins * mult)
        if contracts <= 0:
            journal.log("skip", sym, reason="below exchange minimum size")
            return False
        self.okx.market_order(sym, "buy" if sig.side == "long" else "sell", contracts)
        time.sleep(1.5)
        pos = self.okx.positions().get(sym)
        if not pos:
            notify.send(f"⚠️ {sym} order sent but no position found - check OKX")
            return False
        filled = float(pos["contracts"])
        entry = float(pos["entryPrice"])
        try:
            algo_id = self.okx.place_stop(sym, sig.side, filled, sig.stop)
        except ccxt.BaseError as e:
            self._close(sym, sig.side, filled, f"could not place stop-loss ({e})", stops=[])
            return False
        self.st["positions"][sym] = {"side": sig.side, "entry": entry, "stop": sig.stop, "extreme": entry,
                                     "opened": bar, "contracts": filled, "algo_id": algo_id}
        journal.log("open", sym, side=sig.side, entry=entry, stop=sig.stop, contracts=filled, multiplier=mult)
        risk_usd = abs(entry - sig.stop) * filled * self.okx.contract_size(sym)
        brain_note = f"\n🧠 {review['action']} x{mult:.2f}: {review['reasoning'][:400]}" if review else ""
        notify.send(f"🚀 {sig.side.upper()} {sym.split('/')[0]} @ {px(entry)}, stop {px(sig.stop)} "
                    f"(risk ${risk_usd:.2f})\n{sig.reason}{brain_note}")
        return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args()
    bot = Bot()
    errors = 0
    while True:
        try:
            bot.cycle()
            errors = 0
        except Exception as e:  # noqa: BLE001 - keep running; stops are on the exchange
            errors += 1
            traceback.print_exc()
            journal.log("error", error=repr(e))
            if errors in (1, 5, 20):
                notify.send(f"⚠️ Bot error #{errors}: {e!r}"[:500])
        if a.once:
            break
        time.sleep(min(CHECK_EVERY_S * (1 + errors), 3600))


if __name__ == "__main__":
    main()
