# Economic and social flags vs industries

In-sample 1999-01-04 to 2012, out-of-sample 2013 to 2026-10-05. 364 flags and flag pairs x 19 sectors = 2,635 tests in 0.5s. 'Effect' = the sector's return minus SPY on flag days, minus the same on all other days, annualized.

## Luck check

- Links strong in both halves with the same sign: **6**. Pure chance would give about 2.7 (more, since tests overlap).
- Links strong in both halves but with the sign flipped: 2.
- Of 42 links strong in-sample, 74% kept the same direction later (50% = coin flip).

## Single flags that held up in both halves

| Flag | Industry | Effect vs SPY (early / later) | t (early / later) | Episodes (early / later) |
|---|---|---|---|---|
| FOMC decision day | Consumer staples (XLP) | -74.4% / -54.9% | -3.3 / -2.7 | 112 / 110 |

## Strongest flag pairs that held up (top 25)

| Flags | Industry | Effect (early / later) | t (early / later) | Episodes |
|---|---|---|---|---|
| rates_down_6m & inflation_low | Retail | +62.5% / +78.9% | 2.6 / 2.8 | 14 / 3 |
| sell_in_may & fomc_day | Consumer staples | -62.8% / -83.4% | -2.2 / -2.9 | 57 / 54 |
| inflation_low & sentiment_falling | Retail | +73.9% / +80.7% | 2.2 / 2.2 | 1 / 1 |
| fed_cutting & inflation_low | Retail | +108.4% / +61.5% | 3.1 / 2.1 | 5 / 1 |
| inflation_low & oil_crash | Retail | +103.5% / +39.8% | 2.3 / 2.1 | 2 / 17 |

## As a trade: hold SPY, switch into the sector (or SPY minus the sector) while the flag is on

5 bp per switch. Only links with 3+ episodes in each half.

| Link | Sharpe early (SPY) | Sharpe later (SPY) | CAGR later (SPY) | Worst drop later (SPY) |
|---|---|---|---|---|
| fomc_day -> SPY minus XLP | 0.3 (0.24) | 0.96 (0.92) | +16.2% (+15.1%) | -35.5% (-33.7%) |
| rates_down_6m & inflation_low -> into XRT | 0.51 (0.29) | 1.02 (0.92) | +17.5% (+15.1%) | -33.7% (-33.7%) |
| sell_in_may & fomc_day -> SPY minus XLP | 0.26 (0.24) | 0.97 (0.92) | +16.2% (+15.1%) | -33.7% (-33.7%) |

## Flags used

- `rates_up_6m`: 10-yr Treasury yield up more than 0.75 pt over 6 months
- `rates_down_6m`: 10-yr Treasury yield down more than 0.75 pt over 6 months
- `curve_inverted`: Yield curve inverted (10-yr below 2-yr)
- `curve_steepening`: Yield curve steepened 0.5 pt+ over 6 months
- `fed_hiking`: Fed funds rate up 0.5 pt+ over 6 months
- `fed_cutting`: Fed funds rate down 0.5 pt+ over 6 months
- `inflation_high`: CPI inflation above 4% a year
- `inflation_low`: CPI inflation below 1.5% a year
- `jobs_weakening`: Unemployment rising (real-time Sahm indicator at 0.3+)
- `claims_rising`: Weekly jobless claims up 15%+ vs 6 months ago
- `oil_spike`: Oil up 25%+ in 3 months
- `oil_crash`: Oil down 25%+ in 3 months
- `credit_stress`: Corporate credit spread (Baa minus 10-yr) above 3 pts
- `dollar_surge`: US dollar up 5%+ over 6 months
- `housing_slump`: Housing starts down 15%+ from a year ago
- `sentiment_low`: Consumer sentiment (U. Michigan) below 65
- `sentiment_falling`: Consumer sentiment down 10+ pts in a year
- `fear_vix30`: Fear: VIX above 30
- `calm_vix15`: Calm: VIX below 15
- `holiday_season`: Holiday shopping season (Nov 20 to Dec 31)
- `sell_in_may`: May to October
- `january`: January
- `election_run_up`: Presidential election run-up (Sep 1 to Nov 10)
- `midterm_year`: Midterm election year
- `fomc_day`: FOMC decision day
- `fomc_week_after`: The 5 trading days after an FOMC decision
- `jobs_report_day`: Jobs report day (first Friday, approximate)

Sectors: XLK Technology, XLF Financials, XLE Energy, XLV Health care, XLY Consumer discretionary, XLP Consumer staples, XLI Industrials, XLU Utilities, XLB Materials, XLRE Real estate, XLC Communication, ITB Homebuilders, KRE Regional banks, XRT Retail, SMH Semiconductors, GDX Gold miners, XOP Oil & gas producers, IYT Transportation, JETS Airlines. XLRE, XLC and JETS start after 2012, so they have no early half and are left out.