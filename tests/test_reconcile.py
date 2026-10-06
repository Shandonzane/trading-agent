from datetime import date
from types import SimpleNamespace

from tradebot import journal
from tradebot.agent import reconcile


class FakeBroker:
    def __init__(self, held, session=date(2026, 10, 7)):
        self.held, self.session = held, session

    def positions(self):
        return dict(self.held)

    def last_session(self):
        return self.session


def test_shortfall_comes_from_stop_strategy_first_and_skips_unfilled(tmp_path):
    con = journal.connect(tmp_path / "j.sqlite")
    journal.set_lot(con, "breakout", "SPY", 10, 500, "2026-10-05")
    journal.set_lot(con, "portfolio", "SPY", 20, 500, "2026-10-05")
    journal.set_lot(con, "breakout", "QQQ", 5, 700, "2026-10-07")  # ordered after the last close
    notes = []
    reconcile(con, FakeBroker({"SPY": 20}), [SimpleNamespace(name="breakout", stop_loss_pct=0.07)],
              lambda *a, **k: notes.append(a))
    assert journal.get_lot(con, "breakout", "SPY") is None       # stopped out at the broker
    assert journal.get_lot(con, "portfolio", "SPY")["qty"] == 20  # untouched
    assert journal.get_lot(con, "breakout", "QQQ")["qty"] == 5    # not filled yet, left alone
    assert len(notes) == 1


def test_trend_buys_on_first_run_then_flips(tmp_path):
    import pandas as pd
    from tradebot import trend_agent
    from tradebot.broker import Account

    class B(FakeBroker):
        name = "fake"

        def __init__(self):
            super().__init__({}, None)
            self.orders = []

        def account(self):
            return Account(20_000, 20_000, 20_000)

        def submit(self, sym, qty, side, price, tag=""):
            self.orders.append((sym, side, qty, tag))
            self.held[sym] = self.held.get(sym, 0) + (qty if side == "buy" else -qty)
            return "id", "accepted"

    con = journal.connect(tmp_path / "j.sqlite")
    risk = SimpleNamespace(orders_today=lambda: 0, kill_switch=lambda a: False)
    cfg = {"trend": {"enabled": True, "capital_pct": 0.2}, "risk": {"max_orders_per_day": 20}}
    idx = pd.bdate_range("2025-01-01", periods=250)
    up = pd.DataFrame({"close": [100 + i for i in range(250)]}, index=idx)
    shy = pd.DataFrame({"close": [80.0] * 250}, index=idx)
    b = B()
    trend_agent.run(b, con, cfg, risk, lambda *a, **k: None, bars_fn=lambda s: up if s == "SPY" else shy)
    assert b.orders == [("SPY", "buy", int(4000 // 349), trend_agent.NAME)]
    down = up.copy()
    down.iloc[-1, 0] = 50  # last close below the 200-day average
    trend_agent.run(b, con, cfg, risk, lambda *a, **k: None, bars_fn=lambda s: down if s == "SPY" else shy)
    assert [o[:2] for o in b.orders[1:]] == [("SPY", "sell"), ("SHY", "buy")]
    assert journal.get_lot(con, trend_agent.NAME, "SPY") is None
