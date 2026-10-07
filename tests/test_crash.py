from datetime import date
from types import SimpleNamespace

import pandas as pd

from tradebot import hedge_agent, journal, stops
from tradebot.broker import Account

CFG = {"stops": {"enabled": True, "trail_pct": 0.12, "exclude": ["SHY", "BIL"]},
       "hedge": {"enabled": True, "capital_pct": 0.05, "underlying": "SPYM", "otm": 0.20, "days_out": 182,
                 "stock_share": 0.70, "sell_before_days": 4, "cash_etf": "BIL"},
       "risk": {"max_orders_per_day": 20}}
RISK = SimpleNamespace(orders_today=lambda: 0, kill_switch=lambda a: False)


class B:
    name = "fake"

    def __init__(self, fills=()):
        self.orders, self.stops, self.fills = [], {}, list(fills)

    def account(self):
        return Account(20_000, 15_000, 20_000)

    def ensure_stop(self, sym, qty, price, tag):
        self.stops[(tag, sym)] = (qty, round(price, 2))
        return "placed"

    def stop_fills(self):
        return self.fills

    def cancel_stops(self, *a):
        pass

    def submit(self, sym, qty, side, price, tag=""):
        self.orders.append((sym, side, qty))
        return "id", "accepted"

    def submit_limit(self, sym, qty, side, limit, tag=""):
        self.orders.append((sym, side, qty, limit))
        return "id", "accepted"


def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(journal, "DB", tmp_path / "j.sqlite")
    monkeypatch.setattr("tradebot.live.halted_today", lambda: False)
    return journal.connect(tmp_path / "j.sqlite")


def test_trailing_stop_follows_peak_and_skips_bonds_crypto_unfilled(tmp_path, monkeypatch):
    con = setup(tmp_path, monkeypatch)
    journal.set_lot(con, "SPY 200-day trend", "SPY", 5, 700, "2026-09-01")
    journal.set_lot(con, "SPY 200-day trend", "SHY", 5, 80, "2026-09-01")      # bond ETF: no stop
    journal.set_lot(con, "Big bets", "BTC/USD", 0.004, 85000, "2026-09-01")     # crypto: no stop
    journal.set_lot(con, "Big bets", "NVDA", 0.8, 240, "2026-10-06")            # not filled yet
    journal.set_lot(con, "20-day breakout", "QQQ", 2, 756, "2026-09-01")        # has its own stop
    idx = pd.bdate_range("2026-08-01", "2026-10-05")
    df = pd.DataFrame({"close": [700 + i for i in range(len(idx))]}, index=idx)
    b = B()
    stops.protect(b, con, CFG, lambda *a, **k: None, lambda s: df, date(2026, 10, 6))
    peak = float(df.loc["2026-09-01":, "close"].max())
    assert b.stops == {("SPY 200-day trend", "SPY"): (5, round(peak * 0.88, 2))}


def test_stop_out_waits_for_200_day_or_crash_then_reenters(tmp_path, monkeypatch):
    con = setup(tmp_path, monkeypatch)
    journal.set_lot(con, "SPY 200-day trend", "SPY", 5, 700, "2026-01-02")
    idx = pd.bdate_range("2025-06-01", periods=300)
    df = pd.DataFrame({"close": [500 + i for i in range(300)]}, index=idx)
    stops.protect(B(), con, CFG, lambda *a, **k: None, lambda s: df, None)
    notes = []
    stops.record_stop_outs(B([("o1", "spy-200-day-trend-stop-abc", "SPY", "2026-10-06")]), CFG,
                           lambda *a, **k: notes.append(a[2]))
    assert notes == ["stopped_out"] and stops.is_out("SPY 200-day trend", "SPY")
    falling = pd.Series([800 - i / 2 for i in range(300)], index=idx)             # below its 200-day, only -19%
    assert stops.waiting("SPY 200-day trend", "SPY", falling)
    crash = pd.Series([800 - i for i in range(299)] + [500], index=idx)          # 30%+ below the 799 peak
    assert not stops.waiting("SPY 200-day trend", "SPY", crash)
    assert not stops.is_out("SPY 200-day trend", "SPY")


def test_hedge_buys_puts_and_parks_rest_in_bills(tmp_path, monkeypatch):
    con = setup(tmp_path, monkeypatch)
    b = B()
    chain = ["SPYM270319P00075000", "SPYM270319P00070000", "SPYM270618P00075000"]
    hedge_agent.run(b, con, CFG, RISK, lambda *a, **k: None, today=date(2026, 10, 6),
                    chain_fn=lambda u, lo, hi: chain, quotes_fn=lambda s: {x: (0.43, 0.78) for x in s},
                    price_fn=lambda s: {"SPYM": 92.0, "BIL": 91.5}[s], state_path=tmp_path / "h.json")
    assert b.orders[0] == ("SPYM270319P00075000", "buy", 2, 0.6)   # 2 x ~$9.2k covers $14k of stock
    assert b.orders[1] == ("BIL", "buy", 9)                         # ($1,000 - $121) in T-bills
    b.orders.clear()
    # near expiry and in the money after a crash: sold, never exercised; then rolls into a new put
    hedge_agent.run(b, con, CFG, RISK, lambda *a, **k: None, today=date(2027, 3, 16),
                    chain_fn=lambda u, lo, hi: ["SPYM270917P00050000"], quotes_fn=lambda s: {x: (12.0, 12.4) for x in s},
                    price_fn=lambda s: {"SPYM": 63.0, "BIL": 91.5}[s], state_path=tmp_path / "h.json")
    assert b.orders[0] == ("SPYM270319P00075000", "sell", 2, 12.2)
    assert b.orders[1][:3] == ("SPYM270917P00050000", "buy", 2)


def test_parse_occ():
    assert hedge_agent.parse_occ("SPYM270319P00075000") == (date(2027, 3, 19), 75.0)
