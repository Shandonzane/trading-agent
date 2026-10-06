# Earnings reports: speculation and returns

Researched 2026-10-06 for Shandon. Paper research only; nothing here trades.

**Short answer:** the only earnings pattern with a long track record is just *owning a stock into its report*: about +0.4% over SPY per report, on top of the stock's normal return. It held in 20 of 25 years, but it has been negative in 2025 and 2026. Chasing beats after the report (post-earnings drift) doesn't work for these stocks any more. Buying options before earnings loses money once you pay the spread. Selling them roughly breaks even, with nasty tails.

## What was tested

- **10,854 earnings reports, Dec 2001 to Jul 2026**, for 146 stocks: 97 S&P 100 large caps plus 49 speculative or high-chatter names (TSLA, AMD, PLTR, SMCI, COIN, MSTR, HOOD, GME, AMC, RIVN, SNOW, NIO and others). The report dates, before/after-close timing and EPS estimate vs actual come from Yahoo. Prices are Yahoo adjusted daily.
- **1,434 real option straddles around reports from Feb 2024 to Sep 2026**, using Alpaca's real daily option prices. For each report I took the at-the-money call plus put on the first weekly expiry after the report, bought at the close before the report and sold at the close after it.
- Every return is measured against SPY over the same days. "Abnormal" also subtracts each stock's own normal daily edge over SPY. That strips out the survivorship tailwind: I picked today's winners, so NVDA and others look great no matter what.
- The test is split into two eras, 2002-2012 and 2013-2026, plus the last three years on their own.

Code: `trading-agent/results/earnings/` (`fetch.py`, `events.py`, `analyze.py`, `options.py`, `rules.py`). Data: `trading-agent/data/cache/earnings/`.

## 1. How big the moves are

| | Median move on report day | Normal day | Reports moving >5% | >10% |
|---|---|---|---|---|
| Large caps | 2.7% | 0.9% | 25% | 6% |
| Speculative | 7.7% | 1.8% | 66% | 39% |

A report packs roughly 3 to 4 normal days of movement into one day. Four in ten speculative-name reports move the stock more than 10%.

## 2. Beat vs miss (the "speculation" around the number)

- **Companies beat EPS estimates about 80% of the time** (81% since 2013). The estimate is set to be beaten, so a beat is the default, not news.
- Beat: +0.75% vs SPY on the day for large caps, +2.0% for speculative names. Miss: -1.7% and -2.8%.
- **45% of beats still fall vs SPY on the day.** Whether it's a beat tells you little about which way the stock moves. What matters is the beat vs the whisper number and the guidance, which this data doesn't have.
- "Buy the rumor, sell the news" is weak. Stocks that ran up the most in the 20 days before a report did no worse on report day than the ones that fell (+0.43% vs +0.43% abnormal). Among beats, 47% fell after a big run-up vs 43% after a decline, so there's a slight tilt, but it isn't tradeable.

## 3. After the report: does the move keep going? (post-earnings drift)

The textbook anomaly says stocks that beat keep drifting up for weeks. It's dead for these names:

| Next 20 days, abnormal | 2002-12 | 2013-26 |
|---|---|---|
| Biggest beats (top 20% surprise) | -0.30% | -0.22% |
| Biggest up-gap on report day (top 20%) | +0.40% | +0.58% (not significant) |
| Biggest down-gap (bottom 20%) | -0.87% | -1.07% |
| "Beat but fell >3%" dip buy | no gain | no gain |

Big losers drift a little lower, but shorting them added nothing once survivorship is handled honestly. That fits the published work: drift survives mostly in small, illiquid stocks, not in mega caps.

## 4. Owning the stock into the report (the earnings premium)

Stocks tend to earn a bit extra in the days around their own report. This is the "earnings announcement premium" in the academic literature (Frazzini and Lamont, Barber and others), usually explained as paying holders for event risk and for attention-driven buying.

| Per report, abnormal vs SPY | Mean | t-stat (by date) | Years positive |
|---|---|---|---|
| 5 days before the report (out before it) | +0.12% | 3.9 | 15 of 25 |
| Report day only (close before to close after) | +0.35% | 3.4 | 21 of 25 |
| **Both: 5 days before through report day** | **+0.47%** | **5.1** | **20 of 25** |

The 6-day version, run as a sleeve on an SPY account (each live position swaps 5% of the account out of SPY, costs of 5bp per side for large caps and 15bp for speculative names, survivorship removed):

| Era | SPY alone | SPY + earnings sleeve | Sharpe SPY / sleeve |
|---|---|---|---|
| 2002-12 | 4.0%/yr | 12.5%/yr | 0.29 / 0.63 |
| 2013-26 | 15.1%/yr | 21.8%/yr | 0.92 / 1.01 |
| **2024-26** | **21.2%/yr** | **18.9%/yr** | **1.31 / 0.98** |

The caveats matter here:
- **It has turned negative recently.** By year, the 6-day abnormal return was +0.78% in 2024, -0.48% in 2025 and -0.72% in 2026 so far. That could be noise or the edge getting crowded. I can't tell yet.
- It's a many-small-bets edge. Any single report is a coin flip (50.8% beat SPY). It only shows up across hundreds of reports a year.
- The sleeve averages about 45% of the account in single stocks, so drawdowns are deeper than SPY's in the full sample.
- Speculative names show a bigger premium (+0.88% per report) but it's noisier (t 2.7), and the report-day part has been negative three years running (2024-26).

## 5. Options around earnings (2024-2026, real prices)

| | Large caps | Speculative |
|---|---|---|
| Implied move priced in (median straddle cost) | 4.9% | 11.1% |
| Actual move (median) | 4.0% | 8.2% |
| Reports where the actual move beat the implied move | 42% | 37% |
| Buy straddle, before costs (mean / median) | +0.9% / -12% | -3.7% / -21% |
| **Buy straddle, after a 3% spread each way** | **-5.0%** | **-9.3%** |
| Buy ATM call only, before costs (mean / win rate) | +4.5% / 37% | -11% / 30% |

- **Buying earnings straddles loses**: the market prices the move about right for large caps and overprices it for speculative names, and the spread finishes it off. On a typical trade you lose 12-21% of the premium.
- **Selling straddles** makes about 0% of the stock price on large caps and +0.5% on speculative names, but with a standard deviation of 3.5% and 6.8% of the stock price, and single losses up to 2.7x the premium collected. That's not enough edge for the tail risk. It agrees with the edges review: selling options pays over long periods, but not specifically around earnings.
- Large-cap calls before earnings averaged +4.5% because 2024-26 was a strong up market. They lost 63% of the time and turn negative after spreads. Speculative-name calls lost 11% on average.

## What I'd propose for the paper agent

Only one candidate, flagged as possibly fading. This is for the Paper trading thread to decide; I haven't touched the agent.

**"Earnings run-up sleeve" (paper only):** buy large-cap S&P 100 stocks 5 sessions before a scheduled report and sell at the close of the first session after it. Each position is 5% of the account, funded from SPY, with at most 8 at once. Pause the sleeve if its trailing 12-month return vs SPY goes negative, which is meant to catch a fade like 2025's (I haven't backtested the pause rule itself). Report dates are published weeks ahead (Yahoo calendar), so this is tradeable live without look-ahead.

What not to do: chase beats after the report, buy calls or straddles into earnings, or sell naked straddles on speculative names.

## Limits

- The universe is today's names (survivorship). The "abnormal" numbers correct for each stock's average edge, but not perfectly.
- Yahoo's report times are occasionally wrong (before vs after the close). The 6-day window covers both cases, but the report-day-only numbers carry some timing noise.
- Option prices are daily last trades, not bid/ask. The 3% spread haircut is a guess; speculative names often cost more.
- There are no whisper numbers, guidance or revenue surprises, which drive most of the actual reaction.
- How social-media sentiment ties into reports (70% call chatter before earnings and so on) belongs to the social scout thread. The event table here (`data/cache/earnings/events_scored.csv`) is ready to join with it.
