"""Daily adjusted prices for any ticker, from Yahoo's chart API, cached in data/scout/prices/.

Kept separate from tradebot/data.py so the scout never touches the live agent's cache.
"""
import time
import urllib.request
import json
from pathlib import Path

import pandas as pd

CACHE = Path(__file__).resolve().parent.parent / "data" / "scout" / "prices"


def daily(symbol: str, start: str = "2025-10-01", refresh: bool = False) -> pd.DataFrame | None:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{symbol}.csv"
    if path.exists() and not refresh and time.time() - path.stat().st_mtime < 12 * 3600:
        df = pd.read_csv(path, index_col=0, parse_dates=True)
        return df if len(df) else None
    p1 = int(pd.Timestamp(start).timestamp())
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?period1={p1}&period2={int(time.time())}"
           "&interval=1d&events=splits")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        r = json.load(urllib.request.urlopen(req, timeout=30))["chart"]["result"][0]
        q = r["indicators"]["quote"][0]
        adj = r["indicators"].get("adjclose", [{}])[0].get("adjclose") or q["close"]
        df = pd.DataFrame({"open": q["open"], "close": q["close"], "adjclose": adj},
                          index=pd.to_datetime(r["timestamp"], unit="s").normalize())
        # adjust the open the same way as the close so splits don't look like 50% moves
        df["open"] = df["open"] * df["adjclose"] / df["close"]
        df = df[["open", "adjclose"]].rename(columns={"adjclose": "close"}).dropna()
        df = df[~df.index.duplicated(keep="last")]
    except Exception:
        df = pd.DataFrame(columns=["open", "close"])
    df.to_csv(path)
    return df if len(df) else None
