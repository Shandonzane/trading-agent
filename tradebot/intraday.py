"""Intraday opening-range breakout + retest backtester (5-minute bars).

Built for day-trading videos that a daily-bar backtest can't represent:
mark the opening range, wait for a 5-minute close through one side, wait for a
retest (a candle trades back into the level but closes outside), then enter.

Honesty rules, same spirit as backtest.py:
- Entry fills at the OPEN of the bar after the retest candle closes (no look-ahead).
- If a bar touches both stop and target, the stop is assumed to hit first.
- Stops fill at the stop price, or at the bar open if it gapped through.
- Every fill pays slippage. Flat by the close every day (no overnight risk).
- Daily P&L is computed at 1x notional (whole account in the trade, no leverage) so
  Sharpe is comparable with buy-and-hold of the same symbol.
"""
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta

import numpy as np
import pandas as pd

from .backtest import metrics
from .config import ROOT, env

CACHE = ROOT / "data" / "cache" / "intraday"
OPEN, CLOSE = time(9, 30), time(16, 0)


def get_5min(symbol: str, start: str = "2016-01-01", end: str | None = None) -> pd.DataFrame:
    """5-minute bars incl. pre-market, in US/Eastern, cached per symbol."""
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
    from alpaca.data.enums import Adjustment

    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{symbol.upper()}_5min.csv"
    end_d = pd.Timestamp(end).date() if end else date.today()
    if path.exists():
        df = pd.read_csv(path, index_col=0, parse_dates=True)
        if df.index[0].date() <= pd.Timestamp(start).date() + timedelta(days=7) and \
                df.index[-1].date() >= end_d - timedelta(days=4):
            return df.loc[start:]
    client = StockHistoricalDataClient(env("ALPACA_API_KEY"), env("ALPACA_SECRET_KEY"))
    parts = []
    for y in range(pd.Timestamp(start).year, end_d.year + 1):
        s = max(datetime(y, 1, 1), pd.Timestamp(start).to_pydatetime())
        e = min(datetime(y + 1, 1, 1), datetime.combine(end_d, time()))
        req = StockBarsRequest(symbol_or_symbols=symbol, timeframe=TimeFrame(5, TimeFrameUnit.Minute),
                               start=s, end=e, adjustment=Adjustment.ALL)
        d = client.get_stock_bars(req).df
        if len(d):
            parts.append(d.xs(symbol, level="symbol") if isinstance(d.index, pd.MultiIndex) else d)
    df = pd.concat(parts)
    df.index = pd.to_datetime(df.index).tz_convert("America/New_York").tz_localize(None)
    df = df[["open", "high", "low", "close", "volume"]].sort_index()
    df = df[~df.index.duplicated()]
    df.to_csv(path)
    return df


@dataclass
class ORBParams:
    name: str
    or_minutes: int = 5               # opening range length after 9:30
    stop: str = "midpoint"            # "midpoint" of the range, or "opposite" side of the range
    target_r: float = 2.0             # take profit at N x risk
    last_entry: time = time(15, 0)    # no new entries after this
    exit_time: time = time(15, 55)    # flat by this bar's close
    one_trade_per_day: bool = True
    allow_short: bool = True
    ntz_filter: bool = False          # only trade outside pre-market + yesterday high/low box
    sma_filter: int = 0               # >0: long only above SMA(n) of 5-min closes, short only below


@dataclass
class IntradayTrade:
    day: str
    side: str
    entry_time: str
    entry: float
    stop: float
    target: float
    exit_time: str
    exit: float
    reason: str
    ret_pct: float
    days: int = 0


@dataclass
class IntradayResult:
    strategy: str
    symbol: str
    start: str
    end: str
    params: dict
    slippage_bps: float
    metrics: dict
    benchmark: dict
    oos_metrics: dict
    oos_benchmark: dict
    verdict: str
    verdict_reasons: list
    trades: list = field(default_factory=list)
    equity: dict = field(default_factory=dict)
    bh_equity: dict = field(default_factory=dict)

    def to_dict(self):
        return asdict(self)


def _day_trades(day: pd.DataFrame, p: ORBParams, slip: float, ntz: tuple | None, sma: pd.Series | None):
    rth = day[(day.index.time >= OPEN) & (day.index.time < CLOSE)]
    n_or = p.or_minutes // 5
    if len(rth) < n_or + 3:
        return []
    rng = rth.iloc[:n_or]
    hi, lo = float(rng["high"].max()), float(rng["low"].min())
    mid = (hi + lo) / 2
    bars = rth.iloc[n_or:]
    o, h, l, c = (bars[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    t = bars.index
    trades = []
    broke = None  # "long" / "short" once a 5-min candle has closed through a level
    i = 0
    while i < len(bars):
        if t[i].time() > p.last_entry:
            break
        # 1) breakout: candle closes through a level (the latest break wins)
        if c[i] > hi:
            if broke != "long":
                broke = "long"
                i += 1
                continue
        elif c[i] < lo:
            if broke != "short":
                broke = "short"
                i += 1
                continue
        elif broke and lo <= c[i] <= hi:
            broke = None  # closed back inside the range: breakout failed, wait for a new one
            i += 1
            continue
        # 2) retest: trades back into the broken level but closes outside the range
        retest = (broke == "long" and l[i] <= hi < c[i]) or (broke == "short" and h[i] >= lo > c[i])
        if retest and i + 1 < len(bars):
            side = broke
            ok = side == "long" or p.allow_short
            if ok and ntz:
                ok = c[i] > ntz[1] if side == "long" else c[i] < ntz[0]
            if ok and sma is not None:
                s = sma.get(t[i], np.nan)
                ok = (c[i] > s) if side == "long" else (c[i] < s)
            if ok:
                j = i + 1  # enter at next bar's open
                sign = 1 if side == "long" else -1
                entry = o[j] * (1 + sign * slip)
                stop = mid if p.stop == "midpoint" else (lo if side == "long" else hi)
                risk = (entry - stop) * sign
                if risk > 0:
                    target = entry + sign * p.target_r * risk
                    k, ex, why = j, None, None
                    while k < len(bars):
                        hit_stop = (l[k] <= stop) if sign > 0 else (h[k] >= stop)
                        hit_tgt = (h[k] >= target) if sign > 0 else (l[k] <= target)
                        if hit_stop:  # conservative: stop first if both touched
                            ex = min(o[k], stop) if sign > 0 else max(o[k], stop)
                            ex = ex if k > j else stop
                            why = "stop"
                        elif hit_tgt:
                            ex = (max(o[k], target) if sign > 0 else min(o[k], target)) if k > j else target
                            why = "target"
                        elif t[k].time() >= p.exit_time or k == len(bars) - 1:
                            ex, why = c[k], "eod"
                        if why:
                            break
                        k += 1
                    fill = ex * (1 - sign * slip)
                    ret = sign * (fill / entry - 1)
                    trades.append(IntradayTrade(str(t[i].date()), side, str(t[j].time()), round(entry, 4),
                                                round(stop, 4), round(target, 4), str(t[k].time()),
                                                round(fill, 4), why, round(ret * 100, 4)))
                    if p.one_trade_per_day:
                        break
                    i, broke = k + 1, None
                    continue
            broke = None if retest else broke  # a failed retest needs a fresh break
        i += 1
    return trades


def run(p: ORBParams, df5: pd.DataFrame, symbol: str, slippage_bps: float = 5,
        oos_frac: float = 0.4, approval: dict | None = None) -> IntradayResult:
    slip = slippage_bps / 10_000
    sma = df5["close"].rolling(p.sma_filter).mean() if p.sma_filter else None
    days = df5.groupby(df5.index.date)
    prev_rth = None
    rows, trades = [], []
    for d, day in days:
        rth = day[(day.index.time >= OPEN) & (day.index.time < CLOSE)]
        if rth.empty:
            continue
        ntz = None
        if p.ntz_filter and prev_rth is not None:
            pre = day[(day.index.time >= time(4, 0)) & (day.index.time < OPEN)]
            lows = [prev_rth["low"].min()] + ([pre["low"].min()] if len(pre) else [])
            highs = [prev_rth["high"].max()] + ([pre["high"].max()] if len(pre) else [])
            ntz = (float(min(lows)), float(max(highs)))
        tr = _day_trades(day, p, slip, ntz, sma) if (not p.ntz_filter or ntz) else []
        trades += tr
        day_ret = float(np.prod([1 + x.ret_pct / 100 for x in tr]) - 1) if tr else 0.0
        rows.append((pd.Timestamp(d), day_ret, float(rth["close"].iloc[-1])))
        prev_rth = rth
    idx = pd.DatetimeIndex([r[0] for r in rows])
    equity = pd.Series(100_000 * np.cumprod([1 + r[1] for r in rows]), index=idx)
    closes = np.array([r[2] for r in rows])
    bh = pd.Series(100_000 * closes / closes[0], index=idx)

    class _T:  # adapter so backtest.metrics can count trades
        def __init__(self, t):
            self.ret_pct, self.days = t.ret_pct, 1

    split = int(len(idx) * (1 - oos_frac))
    oos_tr = [t for t in trades if pd.Timestamp(t.day) >= idx[split]]
    oos_m = metrics(equity.iloc[split:], [_T(t) for t in oos_tr])
    oos_b = metrics(bh.iloc[split:])
    a = approval or {}
    reasons = []
    if oos_m.get("trades", 0) < a.get("min_oos_trades", 8):
        reasons.append(f"Too few out-of-sample trades ({oos_m.get('trades', 0)})")
    if oos_m.get("sharpe", 0) < a.get("min_oos_sharpe", 0.5):
        reasons.append(f"Out-of-sample Sharpe {oos_m.get('sharpe', 0):.2f} below {a.get('min_oos_sharpe', 0.5)}")
    if oos_m.get("max_drawdown", -1) < -a.get("max_oos_drawdown", 0.25):
        reasons.append(f"Out-of-sample drawdown {oos_m['max_drawdown']:.0%} too deep")
    if oos_m.get("sharpe", 0) <= oos_b.get("sharpe", 0):
        reasons.append(f"Doesn't beat buy-and-hold on risk-adjusted return "
                       f"(Sharpe {oos_m.get('sharpe', 0):.2f} vs {oos_b.get('sharpe', 0):.2f})")
    pd_ = asdict(p)
    pd_["last_entry"], pd_["exit_time"] = str(p.last_entry), str(p.exit_time)
    return IntradayResult(
        strategy=p.name, symbol=symbol, start=str(idx[0].date()), end=str(idx[-1].date()), params=pd_,
        slippage_bps=slippage_bps, metrics=metrics(equity, [_T(t) for t in trades]), benchmark=metrics(bh),
        oos_metrics=oos_m, oos_benchmark=oos_b, verdict="PASS" if not reasons else "FAIL",
        verdict_reasons=reasons, trades=[asdict(t) for t in trades],
        equity={str(k.date()): round(v, 2) for k, v in equity.items()},
        bh_equity={str(k.date()): round(v, 2) for k, v in bh.items()},
    )


# Presets for the videos learned so far. Gaps the videos left open are filled with the
# assumption noted beside each one; they are reported with the results.
PRESETS = {
    # q0oAwBKDuFg: first 5-min candle range, close through, retest, stop at midpoint, fixed 2:1.
    # Assumed: one trade a day, no entries after 15:00, flat at 15:55 (video gives no time rules).
    "first_5min_candle_retest": ORBParams("First 5-min candle breakout + retest (video q0oAwBKDuFg)",
                                          or_minutes=5, stop="midpoint", target_r=2.0),
    # tGPN_JMQhAg: 15-min opening range, retest, trade only outside the no-trading zone
    # (pre-market + yesterday high/low), 200 SMA direction filter, first 90 minutes only.
    # Assumed: 200 SMA on 5-min bars, stop at the opposite side of the range, 2R target in place
    # of his hand-drawn hourly levels, entries until 11:00, flat at 15:55.
    "orb15_ntz_sma200": ORBParams("15-min ORB + retest, NTZ + 200 SMA filters (video tGPN_JMQhAg)",
                                  or_minutes=15, stop="opposite", target_r=2.0, last_entry=time(11, 0),
                                  ntz_filter=True, sma_filter=200),
}
