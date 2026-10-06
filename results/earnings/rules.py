"""Tradeable earnings rules as an SPY account with an earnings sleeve, compared with SPY.

Each position in its window swaps 5% of the account out of SPY into the stock. 'sleeve_adds'
= what the sleeve adds per year; 'fair' also removes each stock's normal excess return,
i.e. the survivorship tailwind of picking today's winners. Costs per side: 5 bp large caps,
15 bp speculative names, charged on entry and exit.
"""
from pathlib import Path
import numpy as np
import pandas as pd

D = Path(__file__).resolve().parents[2] / "data" / "cache" / "earnings"
ev = pd.read_csv(D / "events_scored.csv", parse_dates=["d0"])
c = pd.read_csv(D / "close.csv", index_col=0, parse_dates=True)
r = c.pct_change(fill_method=None)
xr = r.sub(r["SPY"], axis=0)
base = xr.mean()
days = c.index
pos = {d: i for i, d in enumerate(days)}

RULES = {
    "Pre-report run-up (5 days before, out before report)": (lambda e: e, -5, -1),
    "Hold through the report (close before -> close after)": (lambda e: e, 0, 0),
    "Run-up + through report (6 days)": (lambda e: e, -5, 0),
    "Drift after biggest up-reaction (top 20%, 20 days)": (lambda e: e[e.rq == 5], 1, 20),
    "Drift after biggest beat (top 20% surprise, 20 days)": (lambda e: e[e.sq == 5], 1, 20),
    "Short after biggest down-reaction (bottom 20%, 20 days)": (lambda e: e[e.rq == 1], 1, 20),
    "Buy the dip after a beat that fell (beat, reaction<-3%, 20 days)": (lambda e: e[(e.surprise > 0) & (e.x_react < -0.03)], 1, 20),
}


POS = 0.05  # each position is 5% of the account; the rest sits in SPY


def run(sel, a, b, short=False):
    """SPY account plus an earnings sleeve: each live position swaps 5% of SPY for the stock
    (k relative to d0; k=0 is the d0 session). Sleeve capped at 100%. Returns daily returns:
    'acct' = SPY + sleeve, 'sleeve' = sleeve contribution alone (stock minus SPY, after costs),
    'fair' = sleeve with each stock's normal excess return (survivorship tailwind) removed."""
    T = len(days)
    w = np.zeros((T, c.shape[1]))
    cost = np.zeros(T)
    cols = {t: j for j, t in enumerate(c.columns)}
    for e in sel.itertuples():
        i = pos.get(e.d0)
        if i is None or i + b >= T:
            continue
        w[i + a:i + b + 1, cols[e.ticker]] += POS
        cpb = (0.0015 if e.spec else 0.0005) * POS
        cost[i + a] += cpb; cost[i + b] += cpb
    tot = w.sum(1)
    scale = np.where(tot > 1, 1 / np.maximum(tot, 1e-9), 1.0)
    w *= scale[:, None]; cost *= scale
    s = -1 if short else 1
    X = np.nan_to_num(xr.values)
    sleeve = s * (w * X).sum(1) - cost
    fair = s * (w * (X - np.nan_to_num(base.values))).sum(1) - cost
    spy = np.nan_to_num(r["SPY"].values)
    return pd.DataFrame(dict(acct=spy + sleeve, sleeve=sleeve, fair=spy + fair, inmkt=tot > 0,
                             avg_w=np.minimum(tot, 1)), index=days)


def stats(x, inm=None):
    x = x[x.index >= "2002-01-01"]
    yrs = len(x) / 252
    cagr = (1 + x).prod() ** (1 / yrs) - 1
    sh = x.mean() / x.std() * np.sqrt(252) if x.std() > 0 else np.nan
    eq = (1 + x).cumprod(); mdd = (eq / eq.cummax() - 1).min()
    return cagr * 100, sh, mdd * 100


rows = []
spy = r["SPY"].fillna(0)
for era, lo, hi in [("2002-12", "2002", "2012-12-31"), ("2013-26", "2013", "2026-12-31"), ("2024-26", "2024", "2026-12-31")]:
    m = (days >= lo) & (days <= hi)
    rows.append(dict(rule="SPY buy & hold", era=era, cagr=stats(spy[m])[0], sharpe=stats(spy[m])[1], mdd=stats(spy[m])[2]))
for name, (f, a, b) in RULES.items():
    p = run(f(ev), a, b, short=name.startswith("Short"))
    for era, lo, hi in [("2002-12", "2002", "2012-12-31"), ("2013-26", "2013", "2026-12-31"), ("2024-26", "2024", "2026-12-31")]:
        m = (days >= lo) & (days <= hi)
        q = p[m]
        ca, sa, ma = stats(q.acct); cf, sf, mf = stats(q.fair); cs = stats(q.sleeve)[0]
        rows.append(dict(rule=name, era=era, avg_sleeve_pct=q.avg_w.mean() * 100, cagr=ca, sharpe=sa, mdd=ma,
                         sleeve_adds=cs, fair_cagr=cf, fair_sharpe=sf))
out = pd.DataFrame(rows).round(2)
pd.set_option("display.width", 250); pd.set_option("display.max_colwidth", 60)
print(out.to_string(index=False))
out.to_csv(Path(__file__).parent / "rules.csv", index=False)
