"""Option strategies on REAL Alpaca option prices (daily bars, history from 2024-01-18).

Two tests, both paper research:
1. The wheel on SPY/QQQ with real monthly put and call prices, next to the modeled version.
2. The "overnight call" lead from the sweep: buy the next-day-expiring ATM call at the close,
   sell it at the next open, with real prices.

Honesty rules:
- Prices come from Alpaca's daily option bars (last trade of the day). Bars are trade prices,
  roughly mid-market, so every fill gives up `haircut` of the premium for the spread plus a fee.
- Strikes are chosen from the underlying's UNADJUSTED close (what was actually quoted).
- Assignment and exercise settle at the underlying's close on expiry day.
- Equity is marked every day with the option's real close, not a model.
"""
import math
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from .config import ROOT, env

CACHE = ROOT / "data" / "cache" / "options"
START = "2024-01-18"


def _client():
    from alpaca.data.historical.option import OptionHistoricalDataClient

    return OptionHistoricalDataClient(env("ALPACA_API_KEY"), env("ALPACA_SECRET_KEY"))


def occ(symbol: str, expiry: pd.Timestamp, kind: str, strike: float) -> str:
    return f"{symbol}{expiry:%y%m%d}{kind}{int(round(strike * 1000)):08d}"


def underlying(symbol: str) -> pd.DataFrame:
    """Unadjusted daily OHLC from Alpaca (strikes must match real quotes, not dividend-adjusted)."""
    from alpaca.data.enums import Adjustment
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame

    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{symbol}_raw.csv"
    if path.exists() and datetime.now().timestamp() - path.stat().st_mtime < 86400:
        return pd.read_csv(path, index_col=0, parse_dates=True)
    c = StockHistoricalDataClient(env("ALPACA_API_KEY"), env("ALPACA_SECRET_KEY"))
    df = c.get_stock_bars(StockBarsRequest(symbol_or_symbols=symbol, timeframe=TimeFrame.Day,
                                           start=datetime(2023, 12, 1), adjustment=Adjustment.RAW)).df
    df = df.xs(symbol, level="symbol") if isinstance(df.index, pd.MultiIndex) else df
    df.index = pd.to_datetime(df.index).tz_convert("America/New_York").tz_localize(None).normalize()
    df = df[["open", "high", "low", "close"]]
    df.to_csv(path)
    return df


def option_bars(symbols: list[str]) -> pd.DataFrame:
    """Daily bars for many contracts, cached. Returns columns: symbol, date, open, close."""
    from alpaca.data.requests import OptionBarsRequest
    from alpaca.data.timeframe import TimeFrame

    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / "bars.csv"
    have = pd.read_csv(path, parse_dates=["date"]) if path.exists() else pd.DataFrame(columns=["symbol", "date", "open", "close"])
    known = set(have.symbol) | (set(pd.read_csv(CACHE / "empty.txt", header=None)[0]) if (CACHE / "empty.txt").exists() else set())
    todo = sorted(set(symbols) - known)
    if todo:
        c = _client()
        new, empty = [], []
        for i in range(0, len(todo), 100):
            batch = todo[i:i + 100]
            df = c.get_option_bars(OptionBarsRequest(symbol_or_symbols=batch, timeframe=TimeFrame.Day,
                                                     start=datetime(2024, 1, 1))).df
            got = set()
            if len(df):
                df = df.reset_index()
                df["date"] = pd.to_datetime(df["timestamp"]).dt.tz_convert("America/New_York").dt.tz_localize(None).dt.normalize()
                new.append(df[["symbol", "date", "open", "close"]])
                got = set(df.symbol)
            empty += [s for s in batch if s not in got]
        if new:
            have = pd.concat([have, *new], ignore_index=True)
            have.to_csv(path, index=False)
        if empty:
            with open(CACHE / "empty.txt", "a") as f:
                f.write("".join(s + "\n" for s in empty))
    return have[have.symbol.isin(symbols)]


def third_fridays(start, end) -> list[pd.Timestamp]:
    out, d = [], pd.Timestamp(start).replace(day=1)
    while d <= pd.Timestamp(end):
        f = d + pd.Timedelta(days=(4 - d.weekday()) % 7 + 14)
        if f >= pd.Timestamp(start):
            out.append(f)
        d = (d + pd.Timedelta(days=32)).replace(day=1)
    return out


def _settle_day(expiry: pd.Timestamp, days: pd.DatetimeIndex) -> pd.Timestamp:
    """Expiry, or the last trading day before it (Good Friday etc.)."""
    return days[days <= expiry][-1]


# ----------------------------------------------------------------------------- wheel

@dataclass
class WheelRealParams:
    put_otm: float = 0.015
    call_otm: float = 0.042
    haircut: float = 0.05
    fee: float = 0.65


def wheel_real(symbol: str, p: WheelRealParams = WheelRealParams(), cash: float = 100_000.0) -> dict:
    u = underlying(symbol)
    u = u[u.index >= START]
    days = u.index
    S = u["close"]
    exps = [e for e in third_fridays(days[0], days[-1] + pd.Timedelta(days=45))]
    # Trade on each settle day (close): settle the old contract, open the next one.
    trade_days = [_settle_day(e, days) for e in exps if _settle_day(e, days) >= days[0]]
    # Pre-build every contract we might need so one fetch covers them.
    wanted = []
    for i, e in enumerate(exps[:-1]):
        td = _settle_day(e, days)
        if td < days[0] or td > days[-1]:
            continue
        s = S[td]
        for k in range(int(s * 0.9), int(s * 1.1) + 1):
            wanted += [occ(symbol, exps[i + 1], "P", k), occ(symbol, exps[i + 1], "C", k)]
    bars = option_bars(wanted)
    px = {(r.symbol, r.date): r.close for r in bars.itertuples()}

    shares, cost, opt = 0, 0.0, None
    log, eq = [], pd.Series(index=days, dtype=float)
    for d in days:
        s = S[d]
        if opt and d == opt["settle"]:
            k, n = opt["strike"], opt["n"]
            if opt["kind"] == "P" and s < k:
                shares, cost = n * 100, k
                cash -= n * 100 * k
                log.append(f"{d.date()} assigned {n*100} {symbol} at {k}")
            elif opt["kind"] == "C" and s > k:
                cash += shares * k
                log.append(f"{d.date()} called away at {k} (cost {cost})")
                shares, cost = 0, 0.0
            opt = None
        if d in trade_days and opt is None:
            e_i = [i for i, x in enumerate(exps) if _settle_day(x, days) == d]
            if not e_i or e_i[0] + 1 >= len(exps):
                continue
            nxt = exps[e_i[0] + 1]
            if _settle_day(nxt, days) > days[-1]:
                continue
            if shares == 0:
                k = round(s * (1 - p.put_otm))
                n = int(cash // (k * 100))
                kind = "P"
            else:
                k = max(round(cost * (1 + p.call_otm)), round(s))
                n = shares // 100
                kind = "C"
            # Nearest strike to the target that actually traded today (within 1%).
            cands = sorted((abs(kk - k), kk) for kk in range(int(k * 0.99), int(k * 1.01) + 2)
                           if (occ(symbol, nxt, kind, kk), d) in px)
            if cands:
                k = cands[0][1]
                n = int(cash // (k * 100)) if kind == "P" else n
            sym = occ(symbol, nxt, kind, k)
            prem = px.get((sym, d))
            if prem is None or n == 0:
                log.append(f"{d.date()} no price for {sym}, skipped")
                continue
            cash += n * 100 * prem * (1 - p.haircut) - n * p.fee
            opt = {"kind": kind, "strike": k, "n": n, "sym": sym, "settle": _settle_day(nxt, days), "sold": prem}
            log.append(f"{d.date()} sold {n} {sym} at {prem:.2f}")
        mark = 0.0
        if opt:
            m = px.get((opt["sym"], d))
            if m is None:   # no trade today: intrinsic value
                m = max(opt["strike"] - s, 0) if opt["kind"] == "P" else max(s - opt["strike"], 0)
            mark = opt["n"] * 100 * m
        eq[d] = cash + shares * s - mark
    r = eq.pct_change().dropna()
    bh = S.pct_change().dropna()
    return {"symbol": symbol, "start": str(days[0].date()), "end": str(days[-1].date()),
            "wheel": _m(r), "buy_hold_unadjusted": _m(bh), "cycles": len([x for x in log if "sold" in x]),
            "assignments": len([x for x in log if "assigned" in x]), "log": log}


# ----------------------------------------------------------------------------- overnight calls

def overnight_calls(symbol: str, budget: float = 0.02, haircut: float = 0.05, fee: float = 0.65,
                    only_when: pd.Series | None = None) -> dict:
    """Each day: buy the ATM call expiring NEXT trading day at today's close, sell at tomorrow's open."""
    u = underlying(symbol)
    u = u[u.index >= START]
    days = u.index
    wanted = [occ(symbol, days[i + 1], "C", round(u["close"].iloc[i])) for i in range(len(days) - 1)]
    bars = option_bars(wanted)
    close = {(r.symbol, r.date): r.close for r in bars.itertuples()}
    open_ = {(r.symbol, r.date): r.open for r in bars.itertuples()}
    rets, n_missing = pd.Series(0.0, index=days), 0
    for i in range(len(days) - 1):
        d, nd = days[i], days[i + 1]
        if only_when is not None and not bool(only_when.get(d, False)):
            continue
        sym = wanted[i]
        buy, sell = close.get((sym, d)), open_.get((sym, nd))
        if buy is None or sell is None or buy <= 0:
            n_missing += 1
            continue
        buy_px, sell_px = buy * (1 + haircut), sell * (1 - haircut)
        n = max(int(budget * 100_000 // (buy_px * 100)), 1)
        rets[nd] = (n * 100 * (sell_px - buy_px) - 2 * n * fee) / 100_000
    bh = u["close"].pct_change().dropna()
    t = rets[rets != 0]
    return {"symbol": symbol, "days_traded": int(len(t)), "days_missing_price": n_missing,
            "strategy": _m(rets.iloc[1:]), "buy_hold": _m(bh),
            "win_rate": round(float((t > 0).mean()), 3) if len(t) else None,
            "avg_trade_pct_of_account": round(float(t.mean() * 100), 3) if len(t) else None}


def _m(r: pd.Series) -> dict:
    r = r.dropna()
    eq = (1 + r).cumprod()
    yrs = len(r) / 252
    return {"sharpe": round(float(r.mean() / r.std() * math.sqrt(252)), 2) if r.std() > 0 else 0.0,
            "cagr": round(float(eq.iloc[-1] ** (1 / yrs) - 1), 4) if yrs > 0 else 0.0,
            "total_return": round(float(eq.iloc[-1] - 1), 4),
            "max_drawdown": round(float((eq / eq.cummax() - 1).min()), 4), "days": int(len(r))}
