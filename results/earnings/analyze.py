"""Event-level tables. 'abn_' = excess over SPY minus the stock's own normal daily excess
(its average excess-vs-SPY per day over the whole sample x window length). That strips the
survivorship tailwind out of today's winners, so what's left is the earnings effect itself."""
from pathlib import Path
import numpy as np
import pandas as pd

D = Path(__file__).resolve().parents[2] / "data" / "cache" / "earnings"
ev = pd.read_csv(D / "events.csv", parse_dates=["d0"])
c = pd.read_csv(D / "close.csv", index_col=0, parse_dates=True)
r = c.pct_change(fill_method=None)
xr = r.sub(r["SPY"], axis=0)
base = xr.mean()  # per-day normal excess
L = dict(run21=20, run5=5, react=1, gap=1, intraday0=1, drift5=5, drift20=20, drift60=60)
for k, n in L.items():
    ev["abn_" + k] = ev["x_" + k] - ev.ticker.map(base) * n
ev["era"] = np.where(ev.d0.dt.year <= 2012, "2002-12", "2013-26")
ev["grp"] = np.where(ev.spec, "speculative", "large-cap")
ev["beat"] = np.sign(ev.surprise).map({1: "beat", -1: "miss", 0: "inline"})


def tstat(x):
    x = x.dropna()
    return x.mean() / (x.std() / np.sqrt(len(x))) if len(x) > 2 else np.nan


def tab(df, by, cols):
    g = df.groupby(by)
    out = pd.concat({f"{k}": g["abn_" + k].mean() * 100 for k in cols}, axis=1).round(2)
    out["n"] = g.size()
    out["t_" + cols[0]] = g["abn_" + cols[0]].apply(tstat).round(1)
    return out


pd.set_option("display.width", 200)
W = ["run5", "react", "drift20", "drift60"]
print("== All reports: abnormal % (vs SPY, minus stock's normal) ==")
print(tab(ev.assign(all="all"), "all", W + ["gap", "intraday0", "run21"]))
print(tab(ev, ["grp", "era"], W))
print("\n== Size of the move: median |react| % vs median |normal day| % ==")
ev["absr"] = ev.react.abs()
normal = r.abs().median()
print(ev.groupby("grp").apply(lambda g: pd.Series(dict(med_abs_react=g.absr.median() * 100,
      normal_day=g.ticker.map(normal).median() * 100, pct_over_5=(g.absr > .05).mean() * 100,
      pct_over_10=(g.absr > .10).mean() * 100)), include_groups=False).round(2))
print("\n== Beat vs miss ==")
print(tab(ev, ["grp", "beat"], ["react"] + ["drift20", "drift60"]))
print("beat rate by era:", ev.groupby("era").apply(lambda g: (g.surprise > 0).mean(), include_groups=False).round(3).to_dict())
print("beat but stock fell on react (excess<0):", round(((ev.surprise > 0) & (ev.x_react < 0)).sum() / (ev.surprise > 0).sum(), 3))
print("\n== Surprise quintile -> drift (PEAD) ==")
ev["sq"] = ev.groupby(ev.d0.dt.year)["surprise"].transform(lambda s: pd.qcut(s.rank(method="first"), 5, labels=False) + 1)
print(tab(ev, ["era", "sq"], ["react", "drift5", "drift20", "drift60"]))
print("\n== Reaction-day quintile -> drift (does the gap keep going?) ==")
ev["rq"] = ev.groupby(ev.d0.dt.year)["x_react"].transform(lambda s: pd.qcut(s, 5, labels=False) + 1)
print(tab(ev, ["era", "rq"], ["drift5", "drift20", "drift60", "react"]))
print(tab(ev[ev.era == "2013-26"], ["grp", "rq"], ["drift20", "drift60", "react"]))
print("\n== Speculation: 20-day run-up into report quintile -> reaction ==")
ev["uq"] = ev.groupby(ev.d0.dt.year)["x_run21"].transform(lambda s: pd.qcut(s, 5, labels=False) + 1)
print(tab(ev, ["uq"], ["react", "drift20", "run21"]))
print(tab(ev[ev.era == "2013-26"], ["grp", "uq"], ["react", "drift20"]))
print("beat rate by run-up quintile:", ev.groupby("uq").apply(lambda g: (g.surprise > 0).mean(), include_groups=False).round(3).to_dict())
print("among beats, share falling vs SPY on react, by run-up quintile:",
      ev[ev.surprise > 0].groupby("uq").apply(lambda g: (g.x_react < 0).mean(), include_groups=False).round(3).to_dict())
print("\n== Pre-report run-up (run5) by year, % abnormal ==")
print((ev.groupby(ev.d0.dt.year)[["abn_run5", "abn_react"]].mean() * 100).round(2).T.to_string())
ev.to_csv(D / "events_scored.csv", index=False)
