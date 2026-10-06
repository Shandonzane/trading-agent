"""Fetch earnings dates (Yahoo, with EPS estimate/actual/surprise) and adjusted daily prices.

Universe: S&P 100 large caps + speculative/high-chatter names Shandon trades. Survivorship
bias: these are TODAY's names, so long-run numbers flatter "buy the stock" a bit. The
earnings-window tests compare each stock to itself / SPY on the same days, which limits that.
"""
import sys, time
from pathlib import Path
import pandas as pd
import yfinance as yf

OUT = Path(__file__).resolve().parents[2] / "data" / "cache" / "earnings"
LARGE = """AAPL MSFT NVDA AMZN GOOGL META AVGO TSLA BRK-B JPM LLY V UNH XOM MA JNJ PG HD COST ABBV
MRK WMT NFLX BAC CRM CVX KO ORCL AMD PEP TMO ADBE LIN MCD ACN CSCO ABT WFC DHR QCOM TXN DIS INTU
AMGN PM IBM CAT GE VZ NOW ISRG AMAT UNP CMCSA PFE NEE SPGI GS RTX T LOW HON AXP BKNG UBER BLK MS
SYK ELV PLD SCHW C VRTX LMT MU BMY ADP DE TJX MDT CB MMC GILD ADI LRCX SBUX MO SO BA UPS NKE INTC
CVS MDLZ DUK CL KLAC TGT F GM""".split()
SPEC = """PLTR SMCI COIN MSTR HOOD SNOW CRWD SHOP ROKU SQ PYPL RIVN LCID SOFI AFRM UPST DKNG RBLX
NET DDOG ZS PANW ARM MRVL TTD ENPH FSLR CHWY GME AMC BYND PTON ZM DOCU ETSY SNAP PINS U
NIO XPEV LI BABA JD PDD SE MELI""".split()
UNIVERSE = LARGE + SPEC


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for i, t in enumerate(UNIVERSE):
        p = OUT / "dates.csv"
        try:
            d = yf.Ticker(t).get_earnings_dates(limit=100)
        except Exception as e:
            print(t, "dates fail", e, file=sys.stderr); continue
        if d is None or not len(d):
            print(t, "no dates", file=sys.stderr); continue
        d = d.reset_index()
        d.columns = ["ts", "eps_est", "eps_act", "surprise_pct"]
        d["ticker"] = t
        rows.append(d)
        time.sleep(0.3)
        if i % 20 == 0:
            print(i, t, len(d), flush=True)
    dates = pd.concat(rows)
    dates["ts"] = dates["ts"].astype(str)
    dates.to_csv(OUT / "dates.csv", index=False)
    px = yf.download(UNIVERSE + ["SPY", "QQQ"], start="2000-01-01", auto_adjust=True,
                     progress=False, group_by="column", threads=True)
    px["Open"].to_csv(OUT / "open.csv"); px["Close"].to_csv(OUT / "close.csv")
    print("done", len(dates), px["Close"].shape)


if __name__ == "__main__":
    main()
