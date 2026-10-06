from datetime import date
from types import SimpleNamespace

from tradebot import bigbet_agent, journal
from tradebot.broker import Account

CFG = {"bigbet": {"enabled": True, "max_pct": 0.10,
                  "weights": {"BTC/USD": 0.02, "ETH/USD": 0.01, "NVDA": 0.01, "AMD": 0.01}},
       "risk": {"max_total_exposure_pct": 0.75, "max_orders_per_day": 20}}
RISK = SimpleNamespace(orders_today=lambda: 0, kill_switch=lambda a: False)


class B:
    name = "fake"

    def __init__(self):
        self.orders, self.equity = [], 20_000

    def account(self):
        return Account(self.equity, 15_000, self.equity)

    def last_session(self):
        return None

    def submit(self, sym, qty, side, price, tag=""):
        self.orders.append((sym, side, qty, tag))
        return "id", "accepted"


def run(b, con, px, tmp_path, today):
    bigbet_agent.run(b, con, CFG, RISK, lambda *a, **k: None, prices_fn=lambda s: px,
                     today=today, state_path=tmp_path / "bb.json")


def test_buys_once_then_holds_without_top_ups(tmp_path, monkeypatch):
    monkeypatch.setattr("tradebot.live.halted_today", lambda: False)
    con = journal.connect(tmp_path / "j.sqlite")
    b = B()
    px = {"BTC/USD": 100_000.0, "ETH/USD": 4_000.0, "NVDA": 200.0, "AMD": 160.0}
    run(b, con, px, tmp_path, date(2026, 10, 6))
    assert {(s, side) for s, side, _, _ in b.orders} == {(s, "buy") for s in px}
    assert all(tag == "bigbet" for *_, tag in b.orders)
    assert journal.get_lot(con, "Big bets", "BTC/USD")["qty"] == 0.004  # 2% of $20k
    assert journal.get_lot(con, "Big bets", "NVDA")["qty"] == 1.0
    b.orders.clear()
    px["NVDA"] = 100.0  # halved: no top-up
    run(b, con, px, tmp_path, date(2026, 10, 7))
    assert b.orders == []


def test_trims_when_sleeve_passes_cap_and_in_january(tmp_path, monkeypatch):
    monkeypatch.setattr("tradebot.live.halted_today", lambda: False)
    con = journal.connect(tmp_path / "j.sqlite")
    b = B()
    px = {"BTC/USD": 100_000.0, "ETH/USD": 4_000.0, "NVDA": 200.0, "AMD": 160.0}
    run(b, con, px, tmp_path, date(2026, 10, 6))
    b.orders.clear()
    px["BTC/USD"] = 500_000.0  # BTC now $2,000 = 10% alone; sleeve ~13% > 10%
    run(b, con, px, tmp_path, date(2026, 11, 2))
    assert b.orders == [("BTC/USD", "sell", 0.0032, "bigbet")]  # back to $400 = 2%
    b.orders.clear()
    px["NVDA"] = 300.0  # +50%, sleeve well under 10%: nothing until January
    run(b, con, px, tmp_path, date(2026, 12, 1))
    assert b.orders == []
    run(b, con, px, tmp_path, date(2027, 1, 4))
    assert b.orders == [("NVDA", "sell", 0.3333, "bigbet")]
    b.orders.clear()
    run(b, con, px, tmp_path, date(2027, 1, 5))  # once per January
    assert b.orders == []


def test_crypto_position_symbols_match_journal():
    from tradebot.broker import _order_symbol

    assert _order_symbol(SimpleNamespace(symbol="BTCUSD", asset_class="AssetClass.CRYPTO")) == "BTC/USD"
    assert _order_symbol(SimpleNamespace(symbol="NVDA", asset_class="us_equity")) == "NVDA"
