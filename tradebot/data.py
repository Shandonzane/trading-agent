"""Daily price data. Tries Alpaca (if keys), then Yahoo Finance, and caches to data/cache/*.csv."""
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from .config import ROOT, env, have_alpaca_keys

CACHE = ROOT / "data" / "cache"
COLS = ["open", "high", "low", "close", "volume"]


def _from_alpaca(symbol: str, start: date, end: date) -> pd.DataFrame:
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    from alpaca.data.enums import Adjustment

    client = StockHistoricalDataClient(env("ALPACA_API_KEY"), env("ALPACA_SECRET_KEY"))
    req = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame.Day,
        start=datetime.combine(start, datetime.min.time()),
        end=datetime.combine(end, datetime.min.time()),
        adjustment=Adjustment.ALL,
    )
    df = client.get_stock_bars(req).df
    df = df.xs(symbol, level="symbol") if isinstance(df.index, pd.MultiIndex) else df
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    return df[COLS]


def _from_yahoo(symbol: str, start: date, end: date) -> pd.DataFrame:
    import yfinance as yf

    df = yf.download(symbol, start=start, end=end, progress=False, auto_adjust=True)
    if df.empty:
        raise RuntimeError(f"Yahoo returned no data for {symbol}")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.rename(columns=str.lower)
    df.index = pd.to_datetime(df.index).tz_localize(None)
    return df[COLS]


def get_bars(symbol: str, start: date | str, end: date | str | None = None, use_cache: bool = True) -> pd.DataFrame:
    start = pd.Timestamp(start).date()
    end = pd.Timestamp(end).date() if end else date.today() + timedelta(days=1)
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{symbol.upper()}.csv"
    if use_cache and path.exists():
        cached = pd.read_csv(path, index_col=0, parse_dates=True)
        if len(cached) and cached.index[0].date() <= start and cached.index[-1].date() >= end - timedelta(days=4):
            return cached.loc[str(start):str(end)]
    errors = []
    sources = ([_from_alpaca] if have_alpaca_keys() else []) + [_from_yahoo]
    for fn in sources:
        try:
            df = fn(symbol, start, end).dropna()
            if use_cache:  # a fresh short fetch must not replace the long backtest cache
                df.to_csv(path)
            return df
        except Exception as e:  # try the next source
            errors.append(f"{fn.__name__}: {e}")
    if use_cache and path.exists():
        return pd.read_csv(path, index_col=0, parse_dates=True).loc[str(start):str(end)]
    raise RuntimeError(f"No data for {symbol}. " + " | ".join(errors))


def synthetic_bars(n: int = 1500, seed: int = 7, drift: float = 0.0003, vol: float = 0.012,
                   start: str = "2019-01-02") -> pd.DataFrame:
    """Random-walk OHLCV for tests and offline demos. Not real market data."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(start, periods=n)
    # regime-switching drift so trend and mean-reversion rules both get exercised
    regime = np.repeat(rng.choice([-1, 1], size=n // 120 + 1), 120)[:n]
    rets = drift + regime * 0.0006 + rng.normal(0, vol, n)
    close = 100 * np.exp(np.cumsum(rets))
    open_ = close * np.exp(rng.normal(0, vol / 4, n))
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, vol / 2, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, vol / 2, n)))
    volume = rng.integers(1_000_000, 5_000_000, n)
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume}, index=idx)
