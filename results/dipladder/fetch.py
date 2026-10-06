"""Daily adjusted closes for the buy-the-dip ladder study (Yahoo)."""
from pathlib import Path
import pandas as pd, yfinance as yf
OUT = Path(__file__).resolve().parent / "prices.csv"
T = ["SPY", "QQQ", "^GSPC", "^IRX", "INTC", "CSCO", "GE", "C", "BAC", "AMD", "NVDA", "MSFT", "AAPL", "BTC-USD", "EEM", "EWJ"]
df = yf.download(T, start="1927-01-01", auto_adjust=True, progress=False, threads=False)["Close"]
df.to_csv(OUT)
print(df.apply(lambda s: s.first_valid_index()).to_string(), df.index[-1])
