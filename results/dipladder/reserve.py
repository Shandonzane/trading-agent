"""Dip reserve: account stays 75% invested; the other 25% sits in T-bills and is spent on a
martingale ladder when the price falls; when the price makes a new all-time high, trim back to 75/25
so the reserve refills. Compared with a plain 75/25 mix rebalanced monthly and with 100% held.
Also: the classic lump-sum ladder applied to single stocks, crypto and other markets."""
import itertools, sys
from pathlib import Path
import numpy as np, pandas as pd
from ladder import series, tier_weights, lump_sum, DS, MS, LS

HERE = Path(__file__).resolve().parent
BASE = 0.75


def stats(eq):
    r = np.diff(np.log(eq))
    yrs = len(r) / 252
    return {"cagr": float(np.expm1(r.sum() / yrs)), "sharpe": float(r.mean() / r.std() * np.sqrt(252)),
            "max_dd": float((eq / np.maximum.accumulate(eq) - 1).min())}


def reserve(sym, start=None, end=None):
    p, g, idx = series(sym, start, end)
    grid = list(itertools.product(DS, MS, LS)); V = len(grid)
    d = np.array([x[0] for x in grid]); L = np.array([x[2] for x in grid])
    W = np.zeros((V, 5))
    for i, (_, m, LL) in enumerate(grid):
        w = tier_weights(m, LL); W[i, :LL] = w / np.cumsum(w[::-1])[::-1]
    units = np.full(V, BASE / p[0]); cash = np.full(V, 1 - BASE); done = np.zeros(V, int)
    eq = np.empty((len(p), V)); ath = p[0]
    s_units, s_cash = BASE / p[0], 1 - BASE; s_eq = np.empty(len(p))
    newmonth = np.r_[True, idx.month[1:] != idx.month[:-1]]
    for t in range(len(p)):
        if t:
            cash *= g[t] / g[t - 1]; s_cash *= g[t] / g[t - 1]
        if newmonth[t]:
            tot = s_units * p[t] + s_cash; s_units, s_cash = BASE * tot / p[t], (1 - BASE) * tot
        if p[t] >= ath:
            ath = p[t]; done[:] = 0
            tot = units * p[t] + cash
            over = units * p[t] > BASE * tot
            units = np.where(over, BASE * tot / p[t], units); cash = np.where(over, (1 - BASE) * tot, cash)
        dd = 1 - p[t] / ath
        tgt = np.minimum(L, np.floor(dd / d + 1e-9).astype(int))
        for k in range(1, 6):
            go = (tgt >= k) & (done < k)
            spend = cash * W[:, k - 1] * go
            units += spend / p[t]; cash -= spend
        done = np.maximum(done, tgt)
        eq[t] = units * p[t] + cash; s_eq[t] = s_units * p[t] + s_cash
    rows = [{"d": a, "m": b_, "L": c, **stats(eq[:, i])} for i, (a, b_, c) in enumerate(grid)]
    df = pd.DataFrame(rows)
    return df, stats(s_eq), stats(p)


if __name__ == "__main__":
    res = {}
    for nm, s, e in [("is", None, "2009-12-31"), ("oos", "2010-01-01", None)]:
        df, st, hold = reserve("SPY", s, e)
        df.to_csv(HERE / f"reserve_spy_{nm}.csv", index=False)
        print(nm, "static 75/25", {k: round(v, 3) for k, v in st.items()}, "hold", {k: round(v, 3) for k, v in hold.items()})
        print(df.sort_values("sharpe", ascending=False).head(3).round(3).to_string())
        print("share beating static on cagr", round((df.cagr > st["cagr"]).mean(), 2), "on sharpe", round((df.sharpe > st["sharpe"]).mean(), 2))
    # long history stress test on the S&P 500 index (approx dividends) 1950-1992
    df, st, hold = reserve("^GSPC", "1950-01-01", "1992-12-31")
    df.to_csv(HERE / "reserve_gspc_1950_1992.csv", index=False)
    print("gspc static", {k: round(v, 3) for k, v in st.items()}, "hold", {k: round(v, 3) for k, v in hold.items()})
    print("share beating static on cagr", round((df.cagr > st["cagr"]).mean(), 2), "on sharpe", round((df.sharpe > st["sharpe"]).mean(), 2))
    print(df[(df.d == 0.10) & (df.m == 2) & (df.L == 3)].round(3).to_string())
