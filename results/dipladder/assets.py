"""Lump-sum ladder on other assets, 5-year rolling starts, two fixed settings (no re-optimizing)."""
from pathlib import Path
import pandas as pd
from ladder import lump_sum
HERE = Path(__file__).resolve().parent
PICKS = {"classic martingale (10% steps, x2, 4 tiers, 25% up front, 24-month patience)": (0.10, 2.0, 4, 0.25, 24),
         "best on SPY 1993-2009 (15% steps, x3, 3 tiers, 0% up front, wait forever)": (0.15, 3.0, 3, 0.0, 999)}
rows = []
for sym in ["SPY", "QQQ", "^GSPC", "EEM", "EWJ", "MSFT", "AAPL", "INTC", "CSCO", "GE", "C", "BAC", "AMD", "NVDA", "BTC-USD"]:
    df, n = lump_sum(sym, 5)
    for name, (d, m, L, b, pat) in PICKS.items():
        r = df[(df.d == d) & (df.m == m) & (df.L == L) & (df.b == b) & (df.patience_m == pat)].iloc[0]
        rows.append({"asset": sym, "setting": name.split(" (")[0], "starts": n, **r.drop(["d", "m", "L", "b", "patience_m"]).to_dict()})
out = pd.DataFrame(rows)
out.to_csv(HERE / "assets_5y.csv", index=False)
pd.set_option("display.width", 250)
print(out.round(3).to_string())
