"""Robustness for 'dividend growers, top-N by yield': vary N, raise-streak length and rebalance month."""
import sys, json
import numpy as np, pandas as pd
sys.argv = [sys.argv[0], sys.argv[1]]
src = open(__file__.replace("robust.py", "analyze.py")).read()
exec(src[: src.index("rules = {")])  # reuse loaders/helpers only
out = {}
def run(n_top, streak, month):
    days = [adj.index[adj.index <= d][-1] for d in adj.loc["2001":].resample("ME").last().index if d.month == month]
    eq = [1.0]; idx = [days[0]]
    for d0, d1 in zip(days, days[1:] + [adj.index[-1]]):
        el = [s for s in stocks if pd.notna(adj[s].get(d0)) and pd.notna(ttm_yield(s, d0)) and years_raised(s, d0, streak)]
        pick = sorted(el, key=lambda s: -ttm_yield(s, d0))[:n_top] or ["SPY"]  # no qualifiers yet -> hold SPY
        seg = adj.loc[d0:d1, pick].ffill()
        pr = (seg / seg.iloc[0]).mean(axis=1) * eq[-1] * (1 - 2 * COST)
        eq += list(pr.iloc[1:]); idx += list(pr.index[1:])
    e = pd.Series(eq, index=idx)
    ew = None
    return e
sp = spy
for n_top in (5, 10, 20):
    for streak in (3, 5, 10):
        for month in (12, 6):
            e = run(n_top, streak, month)
            s15 = e.loc["2015":]; b15 = sp.loc[s15.index[0]:]
            out[f"top{n_top}_streak{streak}_m{month}"] = dict(full=stats(e)["cagr"], spy_full=stats(sp.loc[e.index[0]:])["cagr"],
                since2015=stats(s15)["cagr"], spy2015=stats(b15)["cagr"], maxdd=stats(e)["maxdd"])
            print(f"top{n_top}_streak{streak}_m{month}", out[f"top{n_top}_streak{streak}_m{month}"], flush=True)
json.dump(out, open(OUT / "robust.json", "w"), indent=1)
