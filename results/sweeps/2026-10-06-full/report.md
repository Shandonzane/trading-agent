# Strategy sweep results

## SPY

4,700,160 variants in 9.9s (474,283/s). In-sample 2016-2021, out-of-sample 2022-2026. Costs: 1 bp slippage per side on shares, 1.5% of premium per side on options (modeled prices), option premium 2.0% of the account per trade.

Buy and hold: in-sample Sharpe 0.97, out-of-sample Sharpe 0.77 (CAGR 12.4%, worst drop -24.5%).

### Luck check

- Variants with enough trades: 1,824,156. Best in-sample Sharpe 1.459; pure luck would produce about 2.04 from this many tries.
- Of 110,780 variants that made money in-sample, 22.1% still did out-of-sample (vs 7.0% of all variants). Rank correlation within the top 10%: 0.078.
- Top 100 in-sample variants: median out-of-sample Sharpe -0.204, 0.0% beat buy-and-hold out-of-sample.

### Walk-forward (re-pick the best variant each year using only earlier years)

| Year | Pick | Pick return | Top-10 blend | Buy & hold |
|---|---|---|---|---|
| 2019 | 13:35 30m | follow_morning | shares | any | vix15-25 & prev_up | -1.5% | -0.8% | 31.1% |
| 2020 | 10:20 to close | fade_morning | shares | Thu | vix15-25 | 0.3% | -1.4% | 18.3% |
| 2021 | 13:40 to close | long | shares | Fri | in:20-day breakout & prev_down | -3.3% | -2.8% | 28.7% |
| 2022 | 09:50 to close | follow_morning | shares | Fri | in:20-day breakout & above200 | 0.9% | 0.6% | -18.2% |
| 2023 | 09:50 to close | follow_morning | shares | Fri | in:20-day breakout & above200 | 0.2% | 0.5% | 26.2% |
| 2024 | 13:55 120m | long | shares | Fri | in:20-day breakout & prev_down | -0.1% | 0.1% | 24.9% |
| 2025 | 10:10 60m | follow_morning | shares | Tue | gap_up & prev_up | -0.9% | -1.2% | 17.7% |
| 2026 | 11:00 to close | long | shares | Tue | in:20-day breakout & prev_down | -2.0% | 0.1% | 14.5% |

Stitched: best pick Sharpe -0.55 (CAGR -0.8%), top-10 blend Sharpe -0.456 (CAGR -0.6%), buy and hold Sharpe 0.933 (CAGR 17.5%).

### Top in-sample variants and how they did afterwards

| Variant | IS Sharpe | OOS Sharpe | OOS CAGR | OOS worst drop | OOS trades | OOS win |
|---|---|---|---|---|---|---|
| 09:50 to close | follow_morning | shares | Fri | in:20-day breakout & above200 | 1.46 | -0.42 | -1.2% | -9.9% | 115 | 47.8% |
| 10:10 30m | fade_morning | shares | Tue | gap_down | 1.45 | -0.30 | -0.3% | -2.8% | 69 | 46.4% |
| 11:40 60m | follow_gap | shares | any | below200 & vix>25 | 1.37 | -0.08 | -0.3% | -9.2% | 156 | 50.0% |
| 11:35 60m | follow_gap | shares | any | below200 & vix>25 | 1.36 | -0.41 | -1.2% | -11.1% | 156 | 48.1% |
| 15:35 to close | short | shares | Wed | above200 | 1.36 | 0.18 | 0.2% | -2.1% | 190 | 48.4% |
| 11:00 120m | long | shares | Tue | in:20-day breakout & prev_down | 1.33 | -0.66 | -0.8% | -3.6% | 58 | 43.1% |
| 10:05 30m | long | shares | any | below200 & vix>25 | 1.33 | -0.37 | -0.9% | -7.1% | 156 | 48.7% |
| overnight 16:00->9:30 | long | shares | Mon | prev_down | 1.32 | 0.43 | 1.4% | -3.8% | 98 | 52.0% |
| 15:40 to close | short | shares | Wed | above200 | 1.31 | 0.10 | 0.1% | -2.6% | 190 | 46.3% |
| overnight 16:00->9:30 | long | 0dte | Mon | prev_down | 1.31 | 0.05 | 0.1% | -13.8% | 98 | 41.8% |
| 09:35 to close | long | shares | Fri | rsi2>90 | 1.31 | 0.69 | 1.1% | -2.0% | 43 | 55.8% |
| 11:35 30m | follow_gap | shares | any | below200 & vix>25 | 1.30 | -0.46 | -0.9% | -8.3% | 156 | 46.2% |
| 15:35 to close | short | shares | Wed | in:Golden cross trend (50/200 SMA) & above200 | 1.30 | 0.26 | 0.3% | -2.1% | 176 | 48.3% |
| 09:50 30m | long | shares | any | in:Golden cross trend (50/200 SMA) & vix>25 | 1.30 | 0.10 | 0.2% | -3.3% | 62 | 40.3% |
| 12:20 30m | follow_morning | shares | any | in:RSI(2) pullback in uptrend & rsi2<10 | 1.29 | -0.66 | -0.7% | -3.6% | 87 | 39.1% |

### What tends to work on average (median Sharpe of all variants, filter = all)

Long = shares long or calls, short = shares short or puts.

**shares by entry**: 09:30 -0.46/-0.50, 10:00 -0.53/-0.52, 10:30 -0.57/-0.60, 11:00 -0.62/-0.58, 11:30 -0.63/-0.61, 12:00 -0.65/-0.67, 12:30 -0.67/-0.65, 13:00 -0.67/-0.67, 13:30 -0.66/-0.74, 14:00 -0.75/-0.82, 14:30 -0.72/-0.79, 15:00 -0.85/-0.84, 15:30 -0.78/-0.89, overnight -0.05/-0.31

**shares by hold**: 120m -0.43/-0.40, 15m -1.08/-1.08, 30m -0.79/-0.78, 60m -0.58/-0.56, overnight -0.05/-0.31, to close -0.39/-0.38

**shares by direction**: fade_gap -0.64/-0.56, fade_morning -0.67/-0.80, follow_gap -0.56/-0.69, follow_morning -0.58/-0.43, follow_prev_day -0.78/-0.73, long -0.54/-0.51, short -0.70/-0.71, trend_200 -0.68/-0.73

**shares by weekday**: Fri -0.58/-0.58, Mon -0.53/-0.62, Thu -0.55/-0.58, Tue -0.64/-0.55, Wed -0.53/-0.55, any -1.22/-1.20

**0dte by entry**: 09:30 -1.61/-1.34, 10:00 -1.90/-1.60, 10:30 -2.13/-1.94, 11:00 -2.42/-2.25, 11:30 -2.68/-2.45, 12:00 -2.93/-2.57, 12:30 -3.08/-2.47, 13:00 -3.06/-2.39, 13:30 -2.98/-2.82, 14:00 -2.82/-2.73, 14:30 -2.77/-2.87, 15:00 -2.56/-2.71, 15:30 -1.87/-1.83, overnight -0.19/-0.22

**0dte by hold**: 120m -3.07/-2.64, 15m -2.12/-2.00, 30m -2.29/-2.07, 60m -2.65/-2.27, overnight -0.19/-0.22, to close -3.26/-3.08

**0dte by direction**: fade_gap -2.55/-2.20, fade_morning -2.68/-2.40, follow_gap -2.73/-2.47, follow_morning -2.58/-2.19, follow_prev_day -2.61/-2.28, long -2.72/-2.37, short -2.60/-2.26, trend_200 -2.73/-2.46

**0dte by weekday**: Fri -2.55/-2.20, Mon -2.54/-2.54, Thu -2.21/-1.72, Tue -2.61/-2.44, Wed -2.26/-1.82, any -5.56/-4.89

**weekly by entry**: 09:30 -2.01/-1.76, 10:00 -2.20/-1.97, 10:30 -2.30/-2.31, 11:00 -2.53/-2.51, 11:30 -2.68/-2.59, 12:00 -2.89/-2.64, 12:30 -2.90/-2.55, 13:00 -2.90/-2.52, 13:30 -2.71/-2.60, 14:00 -2.78/-2.73, 14:30 -2.66/-2.64, 15:00 -2.77/-2.76, 15:30 -2.67/-2.59, overnight -0.69/-0.73

**weekly by hold**: 120m -2.12/-1.93, 15m -3.53/-3.42, 30m -2.85/-2.71, 60m -2.38/-2.20, overnight -0.69/-0.73, to close -1.90/-1.88

**weekly by direction**: fade_gap -2.58/-2.38, fade_morning -2.67/-2.55, follow_gap -2.60/-2.48, follow_morning -2.48/-2.34, follow_prev_day -2.67/-2.52, long -2.55/-2.29, short -2.66/-2.61, trend_200 -2.59/-2.43

**weekly by weekday**: Fri -2.45/-2.19, Mon -2.37/-2.46, Thu -2.21/-2.01, Tue -2.58/-2.44, Wed -2.25/-2.05, any -5.34/-4.78

**filters, shares (best 12 by in-sample median)**: in:RSI(2) pullback in uptrend & gap_down -0.10/-0.16, vix>25 & gap_down -0.12/-0.25, below200 & rsi2<10 -0.12/-0.25, rsi2<10 & vix>25 -0.12/-0.22, below200 & gap_down -0.12/-0.29, in:RSI(2) pullback in uptrend & vix>25 -0.13/-0.10, in:Golden cross trend (50/200 SMA) & below200 -0.15/-0.24, below200 & prev_up -0.16/-0.29, above200 & rsi2<10 -0.19/-0.30, below200 & vix>25 -0.20/-0.34, in:RSI(2) pullback in uptrend & rsi2<10 -0.21/-0.28, in:RSI(2) pullback in uptrend & prev_up -0.21/-0.21

(pairs are in-sample / out-of-sample median Sharpe)

## QQQ

4,700,160 variants in 9.1s (516,915/s). In-sample 2016-2021, out-of-sample 2022-2026. Costs: 1 bp slippage per side on shares, 1.5% of premium per side on options (modeled prices), option premium 2.0% of the account per trade.

Buy and hold: in-sample Sharpe 1.149, out-of-sample Sharpe 0.73 (CAGR 15.3%, worst drop -34.7%).

### Luck check

- Variants with enough trades: 1,850,478. Best in-sample Sharpe 1.741; pure luck would produce about 2.04 from this many tries.
- Of 142,832 variants that made money in-sample, 28.8% still did out-of-sample (vs 10.1% of all variants). Rank correlation within the top 10%: 0.074.
- Top 100 in-sample variants: median out-of-sample Sharpe -0.014, 9.0% beat buy-and-hold out-of-sample.

### Walk-forward (re-pick the best variant each year using only earlier years)

| Year | Pick | Pick return | Top-10 blend | Buy & hold |
|---|---|---|---|---|
| 2019 | 09:55 120m | follow_morning | shares | Wed | in:Golden cross trend (50/200 SMA) & prev_up | -0.3% | -0.7% | 38.9% |
| 2020 | 15:10 30m | fade_gap | shares | any | gap_down & prev_down | 1.9% | 1.0% | 48.6% |
| 2021 | overnight 16:00->9:30 | long | shares | Tue | in:20-day breakout | 1.1% | 0.2% | 27.4% |
| 2022 | overnight 16:00->9:30 | long | shares | Tue | in:20-day breakout | -0.5% | -0.8% | -32.4% |
| 2023 | overnight 16:00->9:30 | long | shares | Tue | in:20-day breakout | 1.0% | -0.1% | 54.8% |
| 2024 | overnight 16:00->9:30 | trend_200 | shares | Tue | in:20-day breakout & above200 | -0.4% | -0.6% | 25.6% |
| 2025 | overnight 16:00->9:30 | long | 0dte | any | in:Golden cross trend (50/200 SMA) | 33.5% | 4.1% | 20.8% |
| 2026 | overnight 16:00->9:30 | trend_200 | 0dte | any | in:Golden cross trend (50/200 SMA) | 18.6% | 10.8% | 23.5% |

Stitched: best pick Sharpe 0.685 (CAGR 6.5%), top-10 blend Sharpe 0.47 (CAGR 1.8%), buy and hold Sharpe 1.006 (CAGR 23.6%).

### Top in-sample variants and how they did afterwards

| Variant | IS Sharpe | OOS Sharpe | OOS CAGR | OOS worst drop | OOS trades | OOS win |
|---|---|---|---|---|---|---|
| overnight 16:00->9:30 | long | shares | Tue | in:20-day breakout | 1.74 | 0.28 | 0.8% | -5.1% | 116 | 54.3% |
| overnight 16:00->9:30 | long | shares | Tue | in:20-day breakout & above200 | 1.62 | 0.34 | 0.8% | -3.9% | 102 | 55.9% |
| overnight 16:00->9:30 | trend_200 | shares | Tue | in:20-day breakout & above200 | 1.62 | 0.34 | 0.8% | -3.9% | 102 | 55.9% |
| overnight 16:00->9:30 | long | shares | Tue | in:20-day breakout & vix15-25 | 1.46 | 0.16 | 0.4% | -4.1% | 98 | 55.1% |
| overnight 16:00->9:30 | follow_prev_day | shares | Tue | in:20-day breakout & prev_up | 1.45 | -0.35 | -0.8% | -6.1% | 72 | 55.6% |
| overnight 16:00->9:30 | long | shares | Tue | in:20-day breakout & prev_up | 1.45 | -0.35 | -0.8% | -6.1% | 72 | 55.6% |
| overnight 16:00->9:30 | trend_200 | shares | Tue | in:20-day breakout | 1.40 | 0.25 | 0.7% | -6.0% | 116 | 55.2% |
| 10:05 120m | follow_morning | shares | any | vix15-25 & gap_down | 1.39 | 0.33 | 1.0% | -4.1% | 193 | 53.4% |
| 12:30 60m | trend_200 | shares | Tue | in:Golden cross trend (50/200 SMA) & vix>25 | 1.38 | 0.75 | 1.1% | -1.1% | 34 | 64.7% |
| overnight 16:00->9:30 | fade_gap | 0dte | any | vix>25 & gap_down | 1.35 | 0.49 | 4.4% | -10.3% | 178 | 42.7% |
| overnight 16:00->9:30 | long | 0dte | any | vix>25 & gap_down | 1.35 | 0.49 | 4.4% | -10.3% | 178 | 42.7% |
| 12:30 60m | trend_200 | shares | Tue | vix>25 | 1.35 | 1.00 | 2.1% | -2.6% | 88 | 56.8% |
| 15:40 to close | short | shares | Wed | above200 & gap_up | 1.34 | -0.38 | -0.2% | -2.6% | 65 | 41.5% |
| 15:40 to close | fade_gap | shares | Wed | above200 & gap_up | 1.34 | -0.38 | -0.2% | -2.6% | 65 | 41.5% |
| overnight 16:00->9:30 | trend_200 | shares | Mon | above200 & prev_down | 1.33 | -0.72 | -1.6% | -7.8% | 70 | 47.1% |

### What tends to work on average (median Sharpe of all variants, filter = all)

Long = shares long or calls, short = shares short or puts.

**shares by entry**: 09:30 -0.38/-0.34, 10:00 -0.45/-0.37, 10:30 -0.49/-0.45, 11:00 -0.52/-0.46, 11:30 -0.59/-0.47, 12:00 -0.59/-0.54, 12:30 -0.57/-0.54, 13:00 -0.59/-0.55, 13:30 -0.58/-0.67, 14:00 -0.64/-0.68, 14:30 -0.61/-0.63, 15:00 -0.73/-0.65, 15:30 -0.75/-0.71, overnight -0.07/-0.19

**shares by hold**: 120m -0.39/-0.32, 15m -0.91/-0.84, 30m -0.66/-0.62, 60m -0.49/-0.45, overnight -0.07/-0.19, to close -0.35/-0.30

**shares by direction**: fade_gap -0.62/-0.49, fade_morning -0.58/-0.73, follow_gap -0.37/-0.48, follow_morning -0.45/-0.22, follow_prev_day -0.80/-0.55, long -0.41/-0.36, short -0.59/-0.62, trend_200 -0.59/-0.60

**shares by weekday**: Fri -0.48/-0.47, Mon -0.42/-0.45, Thu -0.49/-0.47, Tue -0.56/-0.44, Wed -0.50/-0.44, any -1.06/-0.96

**0dte by entry**: 09:30 -1.11/-0.82, 10:00 -1.52/-1.23, 10:30 -1.78/-1.60, 11:00 -2.13/-1.97, 11:30 -2.43/-2.15, 12:00 -2.67/-2.35, 12:30 -2.82/-2.31, 13:00 -2.83/-2.30, 13:30 -2.87/-2.76, 14:00 -2.78/-2.75, 14:30 -2.69/-2.82, 15:00 -2.67/-2.70, 15:30 -2.19/-2.06, overnight -0.09/+0.02

**0dte by hold**: 120m -2.81/-2.30, 15m -1.94/-1.82, 30m -2.06/-1.84, 60m -2.37/-2.02, overnight -0.09/+0.02, to close -3.09/-3.01

**0dte by direction**: fade_gap -2.38/-1.97, fade_morning -2.42/-2.25, follow_gap -2.37/-2.26, follow_morning -2.35/-1.83, follow_prev_day -2.65/-2.06, long -2.45/-2.11, short -2.37/-2.05, trend_200 -2.48/-2.24

**0dte by weekday**: Fri -2.29/-2.14, Mon -2.27/-2.27, Thu -2.10/-1.59, Tue -2.44/-2.22, Wed -2.05/-1.56, any -5.14/-4.44

**weekly by entry**: 09:30 -1.62/-1.41, 10:00 -1.91/-1.68, 10:30 -2.08/-2.01, 11:00 -2.30/-2.26, 11:30 -2.50/-2.36, 12:00 -2.68/-2.47, 12:30 -2.69/-2.45, 13:00 -2.69/-2.44, 13:30 -2.57/-2.62, 14:00 -2.71/-2.72, 14:30 -2.56/-2.63, 15:00 -2.79/-2.73, 15:30 -2.74/-2.58, overnight -0.64/-0.62

**weekly by hold**: 120m -1.98/-1.80, 15m -3.34/-3.26, 30m -2.67/-2.56, 60m -2.23/-2.07, overnight -0.64/-0.62, to close -1.80/-1.79

**weekly by direction**: fade_gap -2.40/-2.22, fade_morning -2.47/-2.47, follow_gap -2.36/-2.27, follow_morning -2.32/-2.05, follow_prev_day -2.55/-2.36, long -2.35/-2.14, short -2.45/-2.49, trend_200 -2.48/-2.34

**weekly by weekday**: Fri -2.22/-2.11, Mon -2.12/-2.22, Thu -2.12/-1.95, Tue -2.44/-2.30, Wed -2.14/-1.80, any -4.91/-4.49

**filters, shares (best 12 by in-sample median)**: rsi2<10 & gap_down -0.06/-0.18, rsi2<10 & vix>25 -0.08/-0.24, in:RSI(2) pullback in uptrend & vix>25 -0.12/-0.14, below200 & gap_down -0.12/-0.21, below200 & prev_up -0.15/-0.24, in:RSI(2) pullback in uptrend & gap_down -0.16/-0.14, rsi2<10 & gap_up -0.16/-0.17, vix>25 & gap_down -0.18/-0.27, in:Golden cross trend (50/200 SMA) & below200 -0.19/-0.22, above200 & rsi2<10 -0.19/-0.25, below200 & vix>25 -0.20/-0.39, in:Golden cross trend (50/200 SMA) & rsi2<10 -0.20/-0.27

(pairs are in-sample / out-of-sample median Sharpe)
