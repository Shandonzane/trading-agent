"""Speculative + trend paper portfolio: compute today's targets, check the data flags, and show the
orders a rebalance would send. DRY RUN ONLY: this never places orders. The paper-trading thread's
agent is the only thing that trades; hand it the printed orders or the spec file.

  python portfolio.py                 # targets, flags, and the orders vs current paper positions
  python portfolio.py --no-broker     # same without reading the Alpaca paper account
"""
import argparse
import json
import sys
from datetime import date

import pandas as pd

from tradebot.config import ROOT, env, have_alpaca_keys

SPEC = ROOT / "strategies" / "portfolio" / "speculative_trend.json"
TICKERS = ["SPY", "SHY", "RSP", "EFA", "EEM", "EWJ", "XYLD", "GLD", "XLU", "XLE", "ITB", "TLT", "IWM", "^VIX"]


def prices() -> pd.DataFrame:
    import yfinance as yf

    out = {}
    for t in TICKERS:
        d = yf.download(t, start="2024-01-01", progress=False, auto_adjust=True)["Close"]
        d = d.iloc[:, 0] if isinstance(d, pd.DataFrame) else d
        out[t] = d
    df = pd.DataFrame(out)
    df.index = pd.to_datetime(df.index).tz_localize(None)
    return df.ffill()


def fred_last(series_id: str, n: int = 90) -> pd.Series:
    from tradebot.macro import fred

    return fred(series_id).tail(n)


def targets(spec: dict, px: pd.DataFrame) -> tuple[dict, dict]:
    """Return ({ticker: weight}, {sleeve: what it holds and why})."""
    w, why = {}, {}
    spy, sma200 = px["SPY"].iloc[-1], px["SPY"].rolling(200).mean().iloc[-1]
    for name, s in spec["sleeves"].items():
        if name == "core_spy_trend":
            hold = "SPY" if spy > sma200 else "SHY"
            why[name] = f"SPY {spy:.2f} {'above' if hold == 'SPY' else 'below'} 200-day {sma200:.2f} -> {hold}"
        elif name == "ex_us_momentum":
            m = px[["EFA", "EEM", "EWJ"]].resample("ME").last()
            mom = (m.iloc[-1] / m.iloc[-13] - 1) if len(m) > 13 else (m.iloc[-1] / m.iloc[0] - 1)
            best = mom.idxmax()
            hold = best if mom[best] > 0 else "SHY"
            why[name] = "12-mo: " + ", ".join(f"{k} {v * 100:+.1f}%" for k, v in mom.items()) + f" -> {hold}"
        else:
            hold = s["hold"]
            why[name] = f"-> {hold}"
        w[hold] = w.get(hold, 0) + s["weight"]
    return w, why


def check_flags(spec: dict, px: pd.DataFrame) -> list[dict]:
    out = []
    vix = px["^VIX"].iloc[-1]
    q = px[["IWM", "SPY"]].resample("QE").last().pct_change().dropna().tail(2)
    iwm_leads = len(q) == 2 and bool((q["IWM"] > q["SPY"]).all())
    try:
        dgs10 = fred_last("DGS10")
        dff = fred_last("DFF", 60)  # DFF is daily incl. weekends, so 60 rows = the spec's 60 days
        y10, hike = float(dgs10.iloc[-1]), float(dff.iloc[-1] - dff.iloc[0])
    except Exception as e:  # FRED down: report, don't guess
        y10, hike = None, None
        out.append({"id": "fred", "status": f"could not read FRED ({e})"})
    for f in spec["watch_flags"]:
        if f["type"] == "news":
            out.append({"id": f["id"], "status": "needs a headline check (Claude veto step)", "on": None})
            continue
        on = {"yield_spike": y10 is not None and y10 > 5.5,
              "fed_hike": hike is not None and hike >= 0.25,
              "small_cap_leadership": iwm_leads,
              "vix_panic": vix > 35}[f["id"]]
        detail = {"yield_spike": f"10-yr {y10}", "fed_hike": f"fed funds change over 60 days {hike}",
                  "small_cap_leadership": f"IWM beat SPY last 2 quarters: {iwm_leads}", "vix_panic": f"VIX {vix:.1f}"}[f["id"]]
        out.append({"id": f["id"], "on": bool(on), "detail": detail, "action_if_on": f["action"]})
    return out


def _news_flags() -> dict:
    """Latest newsflags.py result, keyed by flag id. Empty if never run."""
    try:
        from newsflags import latest
        data = latest()
    except Exception:
        data = None
    if not data:
        return {}
    age = (date.today() - date.fromisoformat(data["date"])).days
    return {f["id"]: {"on": f["on"], "yellow": bool(f.get("yellow")), "note": f.get("note", ""), "date": data["date"],
                      "stale": age > 3} for f in data["flags"]}


def paper_positions() -> tuple[float, dict]:
    from alpaca.trading.client import TradingClient

    c = TradingClient(env("ALPACA_API_KEY"), env("ALPACA_SECRET_KEY"), paper=True)
    acct = c.get_account()
    pos = {p.symbol: float(p.market_value) for p in c.get_all_positions()}
    return float(acct.equity), pos


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-broker", action="store_true")
    a = ap.parse_args(argv)
    spec = json.loads(SPEC.read_text())
    assert spec["mode"] == "paper"
    px = prices()
    w, why = targets(spec, px)
    print(f"# {spec['name']}  ({date.today()})\n")
    print("## Sleeves")
    for k, v in why.items():
        print(f"- {k} ({spec['sleeves'][k]['weight'] * 100:.0f}%): {v}")
    print("\n## Target weights")
    for k, v in sorted(w.items(), key=lambda kv: -kv[1]):
        print(f"- {k}: {v * 100:.0f}%")
    print("\n## Flags")
    news = _news_flags()
    for f in check_flags(spec, px):
        if f["id"] in news:
            n = news[f["id"]]
            print(f"- {f['id']}: " + ("ON  " if n["on"] else ("YELLOW " if n["yellow"] else "off ")) + f"(headline check {n['date']}{' STALE' if n['stale'] else ''}) {n['note']}")
            continue
        print(f"- {f['id']}: " + (f"ON  {f['detail']}  -> {f['action_if_on']}" if f.get("on") else
                                  (f"off ({f['detail']})" if f.get("on") is False else f["status"])))
    if a.no_broker or not have_alpaca_keys():
        print("\n(no broker read; add --no-broker to silence this)" if not a.no_broker else "")
        return 0
    equity, pos = paper_positions()
    print(f"\n## Orders a rebalance would send (DRY RUN, paper equity ${equity:,.0f}, band {spec['drift_band'] * 100:.0f}%)")
    band = spec["drift_band"] * equity
    for t in sorted(set(w) | set(pos)):
        diff = w.get(t, 0) * equity - pos.get(t, 0)
        if abs(diff) > band:
            print(f"- {'BUY' if diff > 0 else 'SELL'} {t} ${abs(diff):,.0f}")
    others = [t for t in pos if t not in w]
    if others:
        print(f"- (positions outside this portfolio, left alone: {', '.join(others)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
