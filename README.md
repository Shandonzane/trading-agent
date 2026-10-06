# Trading Agent (paper only)

An AI trading agent that learns strategies from YouTube videos, proves them with a backtest, then paper-trades the ones that pass. **No real money.** There is no live-money code path here at all.

```
YouTube video ──► transcript ──► Claude extracts exact rules (with quotes) ──► strategy JSON
                                                                                   │
                         backtest on 10 years of data, with fees, vs buy & hold ◄──┘
                                                │ PASS only
                                                ▼
                    approved strategy ──► daily agent ──► risk checks ──► Alpaca PAPER order
                                                                              │
                                        Streamlit dashboard ◄── journal (every decision + why)
```

## Setup (on your computer)

```bash
cd trading-agent
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # then paste your Alpaca PAPER keys + Anthropic key
python -m pytest tests        # should say 9 passed
```

## Use it

```bash
python cli.py backtest-all                      # test the 3 example strategies on real data
python cli.py learn "https://youtube.com/watch?v=..." --backtest   # learn from a video
python cli.py approve strategies/rsi2_mean_reversion.json          # only works if it PASSED
python cli.py run-agent                         # run once after the market closes
streamlit run dashboard/app.py                  # open the dashboard
```

## Safety rails

- `TradingClient(..., paper=True)` is hard-coded, and `config.json` refuses any mode but `paper`.
- A strategy can only be approved after a **PASS** on real data: enough out-of-sample trades, Sharpe ≥ 0.5, drawdown ≤ 25%, and better risk-adjusted return than just holding the stock.
- Every order goes through risk checks: symbol allowlist, max 10% per position, max 60% invested, 20 orders/day, and a 3% daily-loss kill switch. Edit these in `config.json`.
- Video-learned strategies list what the video left out and what was assumed, with transcript quotes for each rule. Incomplete strategies can't pass.

## Files

| Path | What |
|---|---|
| `tradebot/backtest.py` | Backtester: next-bar fills, slippage, stops, walk-forward pass/fail |
| `tradebot/video_learner.py` | YouTube transcript → Claude → strategy JSON |
| `tradebot/agent.py` | Daily agent loop + risk manager |
| `tradebot/broker.py` | Alpaca paper broker + offline simulator |
| `tradebot/strategy.py` | The rule language strategies are written in |
| `dashboard/app.py` | Streamlit dashboard |
| `dashboard/crew.py` | Agent crew tab: each strategy as a character whose mood follows its paper P&L |
| `strategies/` | Example, learned and approved strategies |
| `results/` | Backtest results (the `__synthetic` ones are fake-data demos) |
