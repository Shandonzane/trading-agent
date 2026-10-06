"""Fetch raw (unadjusted) closes, total-return closes and dividend history from Yahoo for dividend research."""
import sys, time
from pathlib import Path
import pandas as pd
import yfinance as yf

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "data/cache/dividends")
OUT.mkdir(parents=True, exist_ok=True)

ETFS = ["SPY", "SCHD", "VYM", "DVY", "SDY", "NOBL", "DGRO", "HDV", "SPYD", "VIG", "SPHD", "DIA",
        "JEPI", "QYLD", "XYLD", "QQQ", "TLT"]
# Large US dividend payers across sectors (today's names, so survivorship-biased: see write-up)
STOCKS = ["AAPL", "MSFT", "JNJ", "PG", "KO", "PEP", "XOM", "CVX", "JPM", "BAC", "WFC", "C", "GS", "MS",
          "PFE", "MRK", "ABBV", "BMY", "AMGN", "GILD", "T", "VZ", "IBM", "INTC", "CSCO", "TXN", "QCOM",
          "HD", "LOW", "MCD", "WMT", "TGT", "COST", "MMM", "CAT", "DE", "HON", "UPS", "LMT", "RTX", "GD",
          "MO", "PM", "KMB", "CL", "GIS", "K", "HSY", "SO", "DUK", "NEE", "D", "AEP", "O", "SPG", "PLD",
          "AMT", "OKE", "KMI", "WMB", "EPD", "ENB", "BTI", "USB", "PNC", "TFC", "MET", "PRU", "AFL", "ADP",
          "ITW", "EMR", "NUE", "LYB", "DOW", "WBA", "F", "GM", "BBY", "CVS", "UNH", "ABT", "MDT", "SBUX",
          "NKE", "V", "MA", "AVGO", "ORCL", "BLK", "TROW", "BEN", "IVZ", "NEM", "FCX", "VLO", "MPC", "PSX",
          "COP", "EOG", "DVN", "OXY", "CMCSA", "DIS", "PARA", "HPQ", "STX", "WY", "IP", "ED", "XEL", "ES"]

for sym in ETFS + STOCKS:
    p = OUT / f"{sym}.csv"
    if p.exists():
        continue
    for attempt in range(3):
        try:
            h = yf.Ticker(sym).history(period="max", auto_adjust=False, actions=True)
            if h.empty:
                raise RuntimeError("empty")
            h.index = pd.to_datetime(h.index).tz_localize(None).normalize()
            h = h.rename(columns={"Adj Close": "adj", "Close": "close", "Open": "open", "Dividends": "div"})
            h[["open", "close", "adj", "div"]].loc["1995":].to_csv(p)
            print(sym, len(h), flush=True)
            break
        except Exception as e:
            print(sym, "retry", e, flush=True)
            time.sleep(2)
