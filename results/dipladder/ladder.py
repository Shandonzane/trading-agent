"""Buy-the-dip martingale ladder. Research only, never trades.

Ladder: put fraction `b` in right away; keep the rest as cash (earning T-bills) split into L tiers.
Tier k buys when the price is k*d below its peak. Tier sizes grow by `m` each step
(m=1 equal steps, m=2 classic martingale doubling). Never sells.

Two settings:
 A. Lump sum ($20k today): rolling monthly start dates, 5- and 10-year horizons, vs putting it all in on day 1.
    `patience` = months after which unspent cash goes in anyway.
 B. Monthly savings: each month 1 unit is added; b goes in, the rest tops up the reserve.
    Tiers re-arm when the price makes a new all-time high. vs investing every deposit at once.
"""
import itertools, json, sys
from pathlib import Path
import numpy as np, pandas as pd

HERE = Path(__file__).resolve().parent
PX = pd.read_csv(HERE / "prices.csv", index_col=0, parse_dates=True)

DS = [0.03, 0.05, 0.075, 0.10, 0.15, 0.20, 0.25]
MS = [1.0, 1.5, 2.0, 3.0]
LS = [2, 3, 4, 5]
BS = [0.0, 0.25, 0.5, 0.75]
PATIENCE = [12, 24, 999]


def series(sym, start=None, end=None):
    p = PX[sym].dropna()
    irx = PX["^IRX"].ffill().reindex(p.index).ffill().fillna(3.0) / 100
    if sym == "^GSPC":   # price index: add an approximate 3.5%/yr dividend so holding isn't understated
        r = p.pct_change().fillna(0) + 0.035 / 252
        p = (1 + r).cumprod()
    if start: p, irx = p[p.index >= start], irx[irx.index >= start]
    if end: p, irx = p[p.index <= end], irx[irx.index <= end]
    cash = (1 + irx / 252).cumprod()
    return p.to_numpy(float), cash.to_numpy(float), p.index


def tier_weights(m, L):
    w = m ** np.arange(L)
    return w / w.sum()


# ------------------------------------------------------------------ A: lump sum, rolling starts
def lump_sum(sym, horizon_years, start=None, end=None):
    p, g, idx = series(sym, start, end)
    H = int(round(horizon_years * 252))
    months = pd.Series(np.arange(len(idx)), index=idx).groupby(idx.to_period("M")).first().to_numpy()
    starts = [s for s in months if s + H < len(p)]
    levels = sorted({round(k * d, 4) for d in DS for k in range(1, 6)})
    levels = [x for x in levels if x < 1]
    lv_i = {x: i for i, x in enumerate(levels)}
    # first day (offset) each drawdown-from-peak level is hit, per start
    hit = np.full((len(starts), len(levels)), H, dtype=int)
    end_ratio = np.empty(len(starts))
    for si, s in enumerate(starts):
        w = p[s:s + H + 1]
        dd = 1 - w / np.maximum.accumulate(w)
        mx = np.maximum.accumulate(dd)
        hit[si] = np.minimum(np.searchsorted(mx, levels, side="left"), H)
        end_ratio[si] = w[-1] / w[0]
    rows = []
    S = np.array(starts)
    for d, m, L, b, pat in itertools.product(DS, MS, LS, BS, PATIENCE):
        wts = (1 - b) * tier_weights(m, L)
        pat_i = min(pat * 21, H)
        val = b * end_ratio.copy()
        for k in range(1, L + 1):
            lvl = round(k * d, 4)
            t = hit[:, lv_i[lvl]] if lvl in lv_i else np.full(len(S), H)
            t = np.minimum(t, pat_i)              # patience: buy anyway
            bought = t < H
            spend = wts[k - 1] * g[S + t] / g[S]   # cash grew until spent
            units = spend / p[S + t] * p[S]
            val += np.where(bought, units * end_ratio, wts[k - 1] * g[S + H] / g[S])
        rows.append({"d": d, "m": m, "L": L, "b": b, "patience_m": pat,
                     "beat_lump_pct": float((val > end_ratio).mean()),
                     "median_vs_lump": float(np.median(val / end_ratio) - 1),
                     "mean_vs_lump": float(np.mean(val / end_ratio) - 1),
                     "worst_vs_lump": float(np.min(val / end_ratio) - 1),
                     "best_vs_lump": float(np.max(val / end_ratio) - 1),
                     "ladder_lost_pct": float((val < 1).mean()), "lump_lost_pct": float((end_ratio < 1).mean()),
                     "ladder_worst": float(val.min() - 1), "lump_worst": float(end_ratio.min() - 1)})
    return pd.DataFrame(rows), len(starts)


# ------------------------------------------------------------------ B: monthly savings
def savings(sym, start=None, end=None):
    p, g, idx = series(sym, start, end)
    grid = list(itertools.product(DS, MS, LS, BS))
    V = len(grid)
    d = np.array([x[0] for x in grid]); L = np.array([x[2] for x in grid]); b = np.array([x[3] for x in grid])
    W = np.zeros((V, 5))
    for i, (dd_, m, LL, _) in enumerate(grid):
        w = tier_weights(m, LL)
        rem = np.cumsum(w[::-1])[::-1]         # fraction of reserve left before tier k
        W[i, :LL] = w / rem                    # tier k spends this share of the current reserve
    units = np.zeros(V); cash = np.zeros(V); done = np.zeros(V, dtype=int)
    dca_units = 0.0; deposits = 0
    cash_days = np.zeros(V)
    ath = p[0]
    newmonth = np.r_[True, idx.month[1:] != idx.month[:-1]]
    for t in range(len(p)):
        if t: cash *= g[t] / g[t - 1]
        if newmonth[t]:
            deposits += 1
            dca_units += 1 / p[t]
            units += b / p[t]; cash += 1 - b
        if p[t] >= ath:
            ath = p[t]; done[:] = 0
        dd = 1 - p[t] / ath
        tgt = np.minimum(L, np.floor(dd / d + 1e-9).astype(int))
        for k in range(1, 6):
            go = (tgt >= k) & (done < k)
            if go.any():
                spend = cash * W[:, k - 1] * go
                units += spend / p[t]; cash -= spend
        done = np.maximum(done, tgt)
        cash_days += cash / (cash + units * p[t])
    final = units * p[-1] + cash
    dca = dca_units * p[-1]
    df = pd.DataFrame(grid, columns=["d", "m", "L", "b"])
    df["vs_dca"] = final / dca - 1
    df["avg_cash_share"] = cash_days / len(p)
    df["years"] = round(len(p) / 252, 1)
    return df


if __name__ == "__main__":
    out = {}
    A_is, n1 = lump_sum("SPY", 5, end="2014-12-31")      # starts 1993-2009
    A_oos, n2 = lump_sum("SPY", 5, start="2010-01-01")   # starts 2010-2021
    A_is.to_csv(HERE / "A_spy_5y_insample.csv", index=False); A_oos.to_csv(HERE / "A_spy_5y_oos.csv", index=False)
    print("A starts", n1, n2, file=sys.stderr)
    B_is = savings("SPY", end="2009-12-31"); B_oos = savings("SPY", start="2010-01-01")
    B_is.to_csv(HERE / "B_spy_insample.csv", index=False); B_oos.to_csv(HERE / "B_spy_oos.csv", index=False)
    print("B done", file=sys.stderr)
