# Claude Trade Brain

**A rules-based crypto trend bot with an LLM risk officer that can only say "less" or "no".**

> [!WARNING]
> Educational project. This is not financial advice. Run it on OKX **Demo Trading** first, and only trade money you can afford to lose.

## Why this design
Most "AI trading bots" let the model decide when to buy. This one doesn't. A plain, backtested strategy decides *when* to trade. Claude (with web search) acts as a **risk officer**: it reads fresh news and upcoming events, compares the setup with 263 historical cases, and can **approve, shrink or veto** a trade. Hard limits are enforced in code, so the model can never raise risk, even if it hallucinates.

| Layer | Who decides | Can it increase risk? |
|---|---|---|
| Entry and exit signals | Deterministic strategy (`okxbot/strategy.py`) | n/a |
| Trade review and daily risk scan | Claude + web search (`okxbot/brain.py`) | **No**: only approve, shrink or veto |
| Position size, leverage, daily loss, drawdown | Hard limits in code (`okxbot/config.py`) | **No** |

---

## OKX trend bot + AI risk brain

Trades BTC, ETH and SOL perpetual futures on OKX, 24/7.

- **Rules decide when to trade.** A trend-following strategy (40-bar breakout, EMA50/200 trend filter, ADX) that was backtested on 3 years of real OKX prices.
- **The AI brain (Claude) reviews every trade.** It searches the web for fresh news and upcoming events (FOMC, CPI, hacks, regulation), compares today's setup with 263 real historical cases, then approves, shrinks or vetoes the trade. It also runs a daily risk scan.
- **The brain can only reduce risk, never increase it.** The hard limits are enforced in code.

## Risk limits ("Balanced")
| Limit | Value |
|---|---|
| Risk per trade (loss if the stop is hit) | 2% of equity |
| Max leverage (total notional / equity) | 3x, cross margin |
| Max open positions | 3 |
| Daily loss limit | −6%, then no new trades that day |
| Drawdown pause | −20% from peak, then no new trades for 7 days |
| Stop-loss | Always placed **on the exchange**, so it works even if the bot is offline |

## Honest expectations (backtest on real OKX prices, fees included)
- Exactly the last 3 years: **1.49x (about +14%/yr)**. Worst drawdown −30%. Only 44% of 30-day periods were positive, and the worst month was −12%.
- By year: +24%, then −1%, then 0%. Nearly all the profit came in one trending year, which is normal for trend-following.
- **Monthly deposits** ($200 + $100 every 30 days, about 38 months): $4,000 in became **$5,008**. Buying BTC monthly with no bot became $4,760, ETH $3,610, and SOL $4,123.
- **More coins made it worse.** Trading 25 coins returned 1.04–1.32x, with drawdowns of −41% to −56%. Altcoins move together with BTC, so more positions just means one bigger bet with more fake breakouts and higher costs.
- **0 months reached 6x.** Nobody can predict that, and the bot doesn't try.
- The brain **cannot be backtested** because there is no clean archive of historical news. Its value only shows up in forward testing, which `report.py` measures.

## Setup
1. On OKX, switch to **Demo Trading**, go to Profile → API, and create a key with **Read + Trade** permissions only. **Never enable Withdraw.** Restrict it to your server's IP.
2. Get an Anthropic API key at console.anthropic.com.
3. `cp .env.example .env` and fill it in.
4. Test:
   ```bash
   .venv/bin/python -m okxbot.brain --scan            # AI news scan (costs a few cents)
   .venv/bin/python -m okxbot.brain --review BTC long # AI review of a hypothetical trade
   .venv/bin/python -m okxbot.bot --once              # one full cycle on demo
   ```
5. Run 24/7: see the top of `okx-bot.service`.

## Commands
| Command | What it does |
|---|---|
| `python -m okxbot.backtest --days 1095` | Re-run the backtest |
| `python -m okxbot.casebook` | Rebuild the historical cases the brain uses (do this monthly) |
| `python -m okxbot.report` | Forward-test report: P&L, brain vetoes, brain cost, brain vs rules-only |

## Going live
Demo first. Go live only when all of these are true:
- No unexplained errors.
- Every position always had a stop.
- Trades match what the rules say.
- You understand every brain veto.

Then set `OKX_DEMO=false`, create a **live** API key with the same restrictions, and start with money you can afford to lose. A strong first week **does not** prove the strategy works: most of its profit comes from a few big trends a month or more apart. Add money based on months of results, not one good week.

## AI cost
Each brain call (web search plus decision) costs roughly $0.10–0.40 on Claude Opus 5.5. Expect about 1 scan per day plus about 2 trade reviews per week, so **roughly $5–15/month**. On $200 that's a large share of the expected profit. Options: switch `BRAIN_MODEL` to `claude-sonnet-5-5` (about half the cost) or `claude-haiku-5-5` (much cheaper), raise `NEWS_SCAN_HOURS`, or set `BRAIN_ENABLED=false` to run rules only.

---

# Monthly stock picker (`picker/`)

Every 30 days the picker ranks the 20 largest US companies by 3- and 6-month momentum. It keeps only stocks above their 200-day average and picks the strongest 3. Then the AI checks the top 6 against the last 30 days of company news. It can **drop** a pick for a concrete reason (fraud, a lawsuit, a guidance collapse), and the next-ranked stock takes its place. It can't add stocks of its own. You get the picks by Telegram and place them by hand in any broker that has real shares.

## Why real shares, not OKX
OKX stocks are perpetual futures. Holding the popular stocks costs long holders about **10–14%/yr in funding** (checked Nov 2026: MU 11%, INTC 14%, QQQ 10%), and that wipes out the picker's edge.

## Backtest ($200 + $100 every 30 days, fair list = top-20 of Jan 2021)
| | 3 years ($3,800 paid) | 5 years ($6,200 paid) |
|---|---|---|
| Bank 5% | +$304 | +$848 |
| Buy QQQ monthly | +$991 | +$3,451 |
| Picker (top 3) | +$1,650 (worst drop −32%) | +$4,641 (worst drop −42%) |

**Warning:** with today's list, the 3-year result was only +$333, worse than buying QQQ. The edge is small and inconsistent. That's why the paper portfolio tracks it live against QQQ and the bank.

## Commands
| Command | What it does |
|---|---|
| `python -m picker.run --value 1500` | Run the picker. Every 30 days it picks, reviews and sends a buy list sized to your account value |
| `python -m picker.run --status` | Paper portfolio vs buying QQQ vs bank |
| `python -m picker.backtest` | Fair backtest (`--current` uses today's list, which has hindsight bias) |

Edit `picker/config.py` every January so the list is the 20 largest companies, chosen by size and never by performance. The AI review costs about $0.30/month.
