"""Big asymmetric bets as a capped sleeve next to SPY. Research only, never trades.

Three baskets, each equal-weight, bought on the start date:
  hindsight_2016  BTC, AMD, NVDA from 2016 (the winners we already know about)
  hype_2016       popular speculative names of early 2016 that Yahoo still lists
  hype_2021       popular speculative names of 2021 (crypto, meme, IPO boom)
Yahoo only has names that still trade, so the hype baskets are themselves flattered (no zeros).
"""
import json
from pathlib import Path
import numpy as np, pandas as pd, yfinance as yf

OUT = Path(__file__).resolve().parent
BASKETS = {
    "hindsight_2016": ("2016-01-04", ["BTC-USD", "AMD", "NVDA"]),
    "hype_2016": ("2016-01-04", ["BTC-USD", "TSLA", "NFLX", "GPRO", "BABA", "BB", "SHOP", "PLUG", "LTC-USD", "XRP-USD"]),
    "hype_2021": ("2021-02-01", ["ETH-USD", "DOGE-USD", "COIN", "PLTR", "HOOD", "ARKK", "SOFI", "RIVN", "LCID", "AMC", "GME", "SPCE"]),
}
SLEEVES = [0.0, 0.05, 0.10, 0.20, 1.0]


def prices(tickers, start):
    df = yf.download(tickers + ["SPY"], start="2015-06-01", auto_adjust=True, progress=False, threads=False)["Close"]
    df = df[df["SPY"].notna()].ffill()          # trading days of SPY; crypto sampled on those days
    return df[df.index >= start]


def run():
    out = {}
    for name, (start, tick) in BASKETS.items():
        p = prices(tick, start)
        p = p.dropna(axis=1, how="all")
        tick = [t for t in tick if t in p.columns]
        # names that list later (IPO) enter on their first day with their 1/n share held in cash until then
        rel = p[tick].div(p[tick].bfill().iloc[0])
        basket = rel.mean(axis=1)                  # buy and let ride, no rebalance
        spy = p["SPY"] / p["SPY"].iloc[0]
        yrs = (p.index[-1] - p.index[0]).days / 365.25
        res = {"start": start, "end": str(p.index[-1].date()), "years": round(yrs, 1), "per_name_multiple": {},
               "per_name_worst_drop": {}, "sleeves": {}}
        for t in tick:
            s = p[t].dropna()
            res["per_name_multiple"][t] = round(float(s.iloc[-1] / s.iloc[0]), 2)
            res["per_name_worst_drop"][t] = round(float((s / s.cummax() - 1).min()), 2)
        for w in SLEEVES:
            eq = (1 - w) * spy + w * basket          # set once, never rebalanced
            r = eq.pct_change().dropna()
            res["sleeves"][f"{int(w*100)}%"] = {
                "cagr": round(float(eq.iloc[-1] ** (1 / yrs) - 1), 4),
                "multiple": round(float(eq.iloc[-1]), 2),
                "sharpe": round(float(r.mean() / r.std() * np.sqrt(252)), 2),
                "max_dd": round(float((eq / eq.cummax() - 1).min()), 3),
            }
        out[name] = res
        print(name, json.dumps(res["sleeves"]), json.dumps(res["per_name_multiple"]))
    (OUT / "bigbets.json").write_text(json.dumps(out, indent=2))


if __name__ == "__main__":
    run()
