"""Technical indicators on a daily OHLCV DataFrame (columns: open, high, low, close, volume)."""
import numpy as np
import pandas as pd


def sma(s: pd.Series, period: int) -> pd.Series:
    return s.rolling(period).mean()


def ema(s: pd.Series, period: int) -> pd.Series:
    return s.ewm(span=period, adjust=False).mean()


def rsi(s: pd.Series, period: int = 14) -> pd.Series:
    delta = s.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(100)


def bollinger(s: pd.Series, period: int = 20, std: float = 2.0):
    mid = sma(s, period)
    dev = s.rolling(period).std()
    return mid + std * dev, mid, mid - std * dev


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    prev = df["close"].shift()
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev).abs(), (df["low"] - prev).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def macd(s: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    line = ema(s, fast) - ema(s, slow)
    return line, ema(line, signal)


def compute(df: pd.DataFrame, ind: str, period: int | None = None, std: float | None = None) -> pd.Series:
    """Resolve one indicator name to a Series. Names are a fixed whitelist (no eval)."""
    c = df["close"]
    p = period
    if ind in ("open", "high", "low", "close", "volume"):
        return df[ind]
    if ind == "sma":
        return sma(c, p or 20)
    if ind == "ema":
        return ema(c, p or 20)
    if ind == "rsi":
        return rsi(c, p or 14)
    if ind in ("bb_upper", "bb_mid", "bb_lower"):
        up, mid, lo = bollinger(c, p or 20, std or 2.0)
        return {"bb_upper": up, "bb_mid": mid, "bb_lower": lo}[ind]
    if ind == "atr":
        return atr(df, p or 14)
    if ind == "macd":
        return macd(c)[0]
    if ind == "macd_signal":
        return macd(c)[1]
    if ind == "highest":  # highest high of the previous N bars (excludes today)
        return df["high"].rolling(p or 20).max().shift()
    if ind == "lowest":
        return df["low"].rolling(p or 20).min().shift()
    if ind == "volume_sma":
        return sma(df["volume"], p or 20)
    if ind == "pct_change":
        return c.pct_change(p or 1) * 100
    raise ValueError(f"Unknown indicator: {ind}")


INDICATORS = [
    "open", "high", "low", "close", "volume", "sma", "ema", "rsi", "bb_upper", "bb_mid",
    "bb_lower", "atr", "macd", "macd_signal", "highest", "lowest", "volume_sma", "pct_change",
]
