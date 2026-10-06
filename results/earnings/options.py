"""What the options market charges for an earnings report vs what the stock actually does.

Real Alpaca option daily bars (history from 2024-01). For each report since Feb 2024:
- ATM straddle (call + put, same strike nearest the raw close the day before d0), expiry =
  the first weekly expiry on/after d0. Bought at the close before the report, sold at d0 close.
- implied move = straddle / stock price at entry; realized = |stock move| entry close -> d0 close.
Bars are last trades (roughly mid). Costs: `HAIR` of premium per side for the spread.
"""
import os, time
from pathlib import Path
import numpy as np
import pandas as pd
import requests

D = Path(__file__).resolve().parents[2] / "data" / "cache" / "earnings"
H = {"APCA-API-KEY-ID": os.environ["ALPACA_API_KEY"], "APCA-API-SECRET-KEY": os.environ["ALPACA_SECRET_KEY"]}
URL = "https://data.alpaca.markets"
INC = [0.5, 1, 2.5, 5, 10]


def get_bars(path, symbols, start, end, **kw):
    out = {}
    for i in range(0, len(symbols), 100):
        p = dict(symbols=",".join(symbols[i:i + 100]), timeframe="1Day", start=start, end=end, limit=10000, **kw)
        while True:
            for k in range(5):
                r = requests.get(URL + path, headers=H, params=p, timeout=60)
                if r.status_code == 429:
                    time.sleep(2 ** k); continue
                break
            r.raise_for_status()
            j = r.json()
            for s, bars in (j.get("bars") or {}).items():
                out.setdefault(s, []).extend(bars)
            if not j.get("next_page_token"):
                break
            p["page_token"] = j["next_page_token"]
    return {s: pd.Series({pd.Timestamp(b["t"][:10]): b["c"] for b in v}) for s, v in out.items()}


def occ(sym, exp, kind, k):
    return f"{sym.replace('-', '')}{exp:%y%m%d}{kind}{int(round(k * 1000)):08d}"


def main(HAIR=0.03):
    d = pd.read_csv(D / "dates.csv")
    d["t"] = pd.to_datetime(d.ts.str[:19])
    d = d[(d.eps_act.notna() | d.surprise_pct.notna()) & (d.t >= "2024-02-01")].drop_duplicates(["ticker", "t"])
    tick = sorted(set(d.ticker) - {"BRK-B"})
    rawp = D / "raw_close.csv"
    if rawp.exists():
        raw = pd.read_csv(rawp, index_col=0, parse_dates=True)
    else:
        raw = pd.DataFrame(get_bars("/v2/stocks/bars", tick, "2024-01-02", "2026-10-05", adjustment="raw", feed="sip"))
        raw.to_csv(rawp)
    days = raw.index
    ev = []
    for r in d.itertuples():
        if r.ticker not in raw.columns:
            continue
        day = pd.Timestamp(r.t.date())
        i = days.searchsorted(day)
        if i >= len(days) or i < 1:
            continue
        if r.t.hour >= 16 and days[i] == day:
            i += 1
        if i >= len(days):
            continue
        d0, pm1 = days[i], days[i - 1]
        S0, S1 = raw[r.ticker].iloc[i - 1], raw[r.ticker].iloc[i]
        if np.isnan(S0) or np.isnan(S1):
            continue
        exp = d0 + pd.Timedelta(days=(4 - d0.weekday()) % 7)
        ks = sorted({round(round(S0 / inc + o) * inc, 2) for inc in INC for o in (-1, 0, 1)})
        ks = [k for k in ks if abs(k / S0 - 1) < 0.06]
        ev.append(dict(ticker=r.ticker, d0=d0, pm1=pm1, exp=exp, S0=S0, S1=S1, surprise=r.surprise_pct, ks=ks))
    print("events", len(ev))
    cache = D / "opt_bars.pkl"
    bars = pd.read_pickle(cache) if cache.exists() else {}
    by_exp = {}
    for e in ev:
        by_exp.setdefault(e["exp"], []).append(e)
    for n, (exp, es) in enumerate(sorted(by_exp.items())):
        syms = sorted({occ(e["ticker"], exp, kd, k) for e in es for k in e["ks"] for kd in "CP"} - set(bars))
        if not syms:
            continue
        got = get_bars("/v1beta1/options/bars", syms, min(e["pm1"] for e in es).strftime("%Y-%m-%d"),
                       (exp + pd.Timedelta(days=1)).strftime("%Y-%m-%d"))
        for s in syms:
            bars[s] = got.get(s)
        if n % 10 == 0:
            print(n, exp.date(), len(syms), sum(v is not None for v in got.values()), flush=True)
            pd.to_pickle(bars, cache)
    pd.to_pickle(bars, cache)
    rows = []
    for e in ev:
        best = None
        for k in sorted(e["ks"], key=lambda k: abs(k - e["S0"])):
            c, p = bars.get(occ(e["ticker"], e["exp"], "C", k)), bars.get(occ(e["ticker"], e["exp"], "P", k))
            if c is None or p is None:
                continue
            if all(x in s.index for s in (c, p) for x in (e["pm1"], e["d0"])):
                best = (k, c, p); break
        if best is None:
            continue
        k, c, p = best
        st0 = c[e["pm1"]] + p[e["pm1"]]
        st1 = c[e["d0"]] + p[e["d0"]]
        rows.append(dict(ticker=e["ticker"], d0=e["d0"], exp=e["exp"], K=k, S0=e["S0"], S1=e["S1"],
                         surprise=e["surprise"], implied=st0 / e["S0"], realized=abs(e["S1"] / e["S0"] - 1),
                         move=e["S1"] / e["S0"] - 1, straddle_ret=st1 / st0 - 1,
                         straddle_ret_net=(st1 * (1 - HAIR)) / (st0 * (1 + HAIR)) - 1,
                         call_ret=c[e["d0"]] / c[e["pm1"]] - 1, put_ret=p[e["d0"]] / p[e["pm1"]] - 1,
                         dte=(e["exp"] - e["d0"]).days))
    out = pd.DataFrame(rows)
    out.to_csv(D / "straddles.csv", index=False)
    print("priced", len(out))


if __name__ == "__main__":
    main()
