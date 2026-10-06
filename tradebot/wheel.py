"""Options wheel backtester with modeled option prices.

Real historical option quotes need a paid data source, so premiums are priced with
Black-Scholes using the matching volatility index as implied volatility (VXN for QQQ,
VIX for SPY). That index is a 30-day at-the-money vol, close to the near-the-money
strikes the wheel sells. To stay conservative, each sale gives up a bid/ask haircut.

Rules (from video K6YVPHULzPA):
1. Sell a cash-secured put ~1.5% below the price, expiring the next monthly
   (third-Friday) expiration at least `min_dte` days out.
2. If assigned at expiry, own the shares and sell a covered call `call_otm` above the
   assignment price for the next monthly expiration.
3. If the call expires worthless, sell the same strike again next month.
4. If called away, go back to step 1.
Held to expiration, no rolling or early close (the video gives none). Idle cash earns 0.
"""
from dataclasses import asdict, dataclass, field
from math import erf, exp, log, sqrt

import numpy as np
import pandas as pd

from .backtest import metrics

VOL_INDEX = {"QQQ": "^VXN", "SPY": "^VIX"}


def _ncdf(x):
    return 0.5 * (1 + erf(x / sqrt(2)))


def bs_price(kind: str, s: float, k: float, t: float, vol: float, r: float = 0.0) -> float:
    if t <= 0:
        return max(0.0, (k - s) if kind == "put" else (s - k))
    d1 = (log(s / k) + (r + vol * vol / 2) * t) / (vol * sqrt(t))
    d2 = d1 - vol * sqrt(t)
    if kind == "call":
        return s * _ncdf(d1) - k * exp(-r * t) * _ncdf(d2)
    return k * exp(-r * t) * _ncdf(-d2) - s * _ncdf(-d1)


def monthly_expiries(start, end) -> list[pd.Timestamp]:
    out = []
    for m in pd.date_range(pd.Timestamp(start).replace(day=1), end, freq="MS"):
        fridays = pd.date_range(m, m + pd.offsets.MonthEnd(0), freq="W-FRI")
        out.append(fridays[2])
    return out


def get_vol_index(symbol: str, start: str = "2015-01-01") -> pd.Series:
    import yfinance as yf

    df = yf.download(VOL_INDEX[symbol], start=start, progress=False, auto_adjust=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    s = df["Close"].dropna() / 100
    s.index = pd.to_datetime(s.index).tz_localize(None)
    return s


@dataclass
class WheelParams:
    name: str = "Options wheel (video K6YVPHULzPA), modeled premiums"
    put_otm: float = 0.015
    call_otm: float = 0.042
    min_dte: int = 25
    haircut: float = 0.10      # give up 10% of each premium to the bid/ask spread
    fee_per_contract: float = 0.65


@dataclass
class WheelResult:
    strategy: str
    symbol: str
    start: str
    end: str
    params: dict
    metrics: dict
    benchmark: dict
    oos_metrics: dict
    oos_benchmark: dict
    verdict: str
    verdict_reasons: list
    cycles: list = field(default_factory=list)
    equity: dict = field(default_factory=dict)
    bh_equity: dict = field(default_factory=dict)

    def to_dict(self):
        return asdict(self)


def run(p: WheelParams, px: pd.Series, vol: pd.Series, symbol: str, cash: float = 100_000.0,
        oos_frac: float = 0.4, approval: dict | None = None) -> WheelResult:
    vol = vol.reindex(px.index).ffill()
    px = px[vol.notna()]
    vol = vol[vol.notna()]
    dates = px.index
    exps = [e for e in monthly_expiries(dates[0], dates[-1] + pd.Timedelta(days=60))]
    shares = 0
    opt = None  # dict(kind, strike, expiry, n)
    cycles = []
    eq = np.empty(len(dates))

    def next_expiry(d):
        for e in exps:
            if (e - d).days >= p.min_dte:
                return e

    for i, d in enumerate(dates):
        s, v = float(px.iloc[i]), float(vol.iloc[i])
        # settle an option on (or after) its expiry date at today's close
        if opt and d >= opt["expiry"]:
            itm = s < opt["strike"] if opt["kind"] == "put" else s > opt["strike"]
            if itm and opt["kind"] == "put":
                shares += 100 * opt["n"]
                cash -= 100 * opt["n"] * opt["strike"]
                cost_basis = opt["strike"]
                opt_res = "assigned"
            elif itm:
                cash += 100 * opt["n"] * opt["strike"]
                shares -= 100 * opt["n"]
                opt_res = "called_away"
            else:
                opt_res = "expired"
            cycles.append({**{k: (str(x.date()) if isinstance(x, pd.Timestamp) else x) for k, x in opt.items()},
                           "settle_price": round(s, 2), "result": opt_res})
            opt = None
        # open the next option at today's close
        if opt is None:
            e = next_expiry(d)
            t = (e - d).days / 365
            if shares == 0:
                k = round(s * (1 - p.put_otm))
                n = int(cash // (100 * k))
                if n > 0:
                    prem = bs_price("put", s, k, t, v) * (1 - p.haircut)
                    cash += 100 * n * prem - n * p.fee_per_contract
                    opt = {"kind": "put", "strike": k, "expiry": e, "n": n, "opened": d, "premium": round(prem, 3)}
            else:
                last = cycles[-1] if cycles else {}
                if last.get("kind") == "call" and last.get("result") == "expired":
                    k = last["strike"]  # re-sell the same strike
                else:
                    k = round(cost_basis * (1 + p.call_otm))
                n = shares // 100
                prem = bs_price("call", s, k, t, v) * (1 - p.haircut)
                cash += 100 * n * prem - n * p.fee_per_contract
                opt = {"kind": "call", "strike": k, "expiry": e, "n": n, "opened": d, "premium": round(prem, 3)}
        liability = 0.0
        if opt:
            liability = 100 * opt["n"] * bs_price(opt["kind"], s, opt["strike"], max((opt["expiry"] - d).days, 0) / 365, v)
        eq[i] = cash + shares * s - liability

    equity = pd.Series(eq, index=dates)
    bh = pd.Series(eq[0] * px.to_numpy() / px.iloc[0], index=dates)
    split = int(len(dates) * (1 - oos_frac))
    oos_m, oos_b = metrics(equity.iloc[split:]), metrics(bh.iloc[split:])
    oos_m["trades"] = sum(1 for c in cycles if pd.Timestamp(c["opened"]) >= dates[split])
    a = approval or {}
    reasons = []
    if oos_m.get("sharpe", 0) < a.get("min_oos_sharpe", 0.5):
        reasons.append(f"Out-of-sample Sharpe {oos_m.get('sharpe', 0):.2f} below {a.get('min_oos_sharpe', 0.5)}")
    if oos_m.get("max_drawdown", -1) < -a.get("max_oos_drawdown", 0.25):
        reasons.append(f"Out-of-sample drawdown {oos_m['max_drawdown']:.0%} too deep")
    if oos_m.get("sharpe", 0) <= oos_b.get("sharpe", 0):
        reasons.append(f"Doesn't beat buy-and-hold on risk-adjusted return "
                       f"(Sharpe {oos_m.get('sharpe', 0):.2f} vs {oos_b.get('sharpe', 0):.2f})")
    reasons.append("Premiums are modeled, not real quotes: needs a paper-trading track record before approval")
    full = metrics(equity)
    full["cycles"] = len(cycles)
    full["assigned"] = sum(c["result"] == "assigned" for c in cycles)
    full["called_away"] = sum(c["result"] == "called_away" for c in cycles)
    return WheelResult(p.name, symbol, str(dates[0].date()), str(dates[-1].date()), asdict(p), full, metrics(bh),
                       oos_m, oos_b, "FAIL" if len(reasons) > 1 else "PROMISING", reasons, cycles,
                       {str(k.date()): round(x, 2) for k, x in equity.items()},
                       {str(k.date()): round(x, 2) for k, x in bh.items()})
