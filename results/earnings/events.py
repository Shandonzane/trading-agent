"""Build one row per earnings report with returns around it, raw and in excess of SPY.

d0 = the first session that can trade on the news: same day for reports before/during the
session (Yahoo time <16:00 ET), next session for after-close reports (>=16:00).
Windows (all close-to-close unless noted, index k = sessions relative to d0):
  run21 / run5 : C[-21]->C[-1], C[-6]->C[-1]   (run-up into the report, no event risk)
  gap          : C[-1]->O[0]                   (overnight jump on the news)
  react        : C[-1]->C[0]                   (holding through the report)
  intraday0    : O[0]->C[0]                    (buy the open after the gap)
  drift5/20/60 : C[0]->C[+5/20/60]             (post-earnings drift, entered at d0 close)
Excess = stock return minus SPY return over the same window.
"""
from pathlib import Path
import numpy as np
import pandas as pd

D = Path(__file__).resolve().parents[2] / "data" / "cache" / "earnings"
SPEC = set("""PLTR SMCI COIN MSTR HOOD SNOW CRWD SHOP ROKU PYPL RIVN LCID SOFI AFRM UPST DKNG RBLX
NET DDOG ZS PANW ARM MRVL TTD ENPH FSLR CHWY GME AMC BYND PTON ZM DOCU ETSY SNAP PINS U
NIO XPEV LI BABA JD PDD SE MELI TSLA AMD""".split())


def load():
    o = pd.read_csv(D / "open.csv", index_col=0, parse_dates=True)
    c = pd.read_csv(D / "close.csv", index_col=0, parse_dates=True)
    d = pd.read_csv(D / "dates.csv")
    d["t"] = pd.to_datetime(d.ts.str[:19])
    d = d[d.eps_act.notna() | d.surprise_pct.notna()].drop_duplicates(["ticker", "t"])
    d["day"] = d.t.dt.date
    d = d.drop_duplicates(["ticker", "day"])
    return o, c, d


def build():
    o, c, d = load()
    days = c.index
    spy = c["SPY"].values
    rows = []
    for r in d.itertuples():
        if r.ticker not in c.columns:
            continue
        day = pd.Timestamp(r.t.date())
        i = days.searchsorted(day)  # first session on/after the report date
        if i >= len(days):
            continue
        if r.t.hour >= 16 and days[i] == day:
            i += 1
        if i < 22 or i + 60 >= len(days):
            continue
        s = c[r.ticker].values
        so = o[r.ticker].values
        if np.isnan(s[i - 21]) or np.isnan(s[i]) or np.isnan(s[i + 60]) or np.isnan(so[i]):
            continue
        f = lambda a, b, x=s: x[b] / x[a] - 1
        row = dict(ticker=r.ticker, d0=days[i], amc=r.t.hour >= 16, surprise=r.surprise_pct,
                   eps_est=r.eps_est, eps_act=r.eps_act, spec=r.ticker in SPEC,
                   run21=f(i - 21, i - 1), run5=f(i - 6, i - 1), gap=so[i] / s[i - 1] - 1,
                   react=f(i - 1, i), intraday0=s[i] / so[i] - 1,
                   drift5=f(i, i + 5), drift20=f(i, i + 20), drift60=f(i, i + 60),
                   vol60=np.nanstd(np.diff(np.log(s[i - 61:i - 1]))) )
        for k in ["run21", "run5", "react", "drift5", "drift20", "drift60"]:
            a, b = dict(run21=(i - 21, i - 1), run5=(i - 6, i - 1), react=(i - 1, i), drift5=(i, i + 5),
                        drift20=(i, i + 20), drift60=(i, i + 60))[k]
            row["x_" + k] = row[k] - (spy[b] / spy[a] - 1)
        row["x_gap"] = row["gap"] - (o["SPY"].values[i] / spy[i - 1] - 1)
        row["x_intraday0"] = row["intraday0"] - (spy[i] / o["SPY"].values[i] - 1)
        rows.append(row)
    ev = pd.DataFrame(rows)
    ev.to_csv(D / "events.csv", index=False)
    return ev


if __name__ == "__main__":
    ev = build()
    print(len(ev), ev.d0.min(), ev.d0.max())
    print(ev.describe().T[["mean", "50%", "std"]].round(4))
