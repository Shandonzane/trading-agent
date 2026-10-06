"""SPY 200-day trend yardstick: hold SPY while SPY closed above its 200-day SMA, else SHY.

Runs on its own slice of the account (config.trend.capital_pct) with its own journal lots
and client_order_id tag, so its SPY never mixes with the breakout's or the portfolio's.
Trades only on the first run and when the rule flips.
"""
from datetime import date, timedelta

from . import journal
from .data import get_bars

NAME = "SPY 200-day trend"


def run(broker, con, cfg: dict, risk, note, bars_fn=None, today: date | None = None) -> None:
    tcfg = cfg.get("trend", {})
    if not tcfg.get("enabled"):
        return
    today = today or date.today()
    bars_fn = bars_fn or (lambda s: get_bars(s, today - timedelta(days=420), today + timedelta(days=1), use_cache=False))
    spy, shy = bars_fn("SPY"), bars_fn("SHY")
    session = broker.last_session()
    for sym, df in (("SPY", spy), ("SHY", shy)):
        if df.empty or (session and df.index[-1].date() < session):
            note(NAME, sym, "skip", "stale or missing data")
            return
    close, sma = float(spy["close"].iloc[-1]), float(spy["close"].rolling(200).mean().iloc[-1])
    want = "SPY" if close > sma else "SHY"
    price = {"SPY": close, "SHY": float(shy["close"].iloc[-1])}
    lots = {s: journal.get_lot(con, NAME, s) for s in ("SPY", "SHY")}
    have = next((s for s, l in lots.items() if l), None)
    if have == want:
        note(NAME, want, "hold", f"SPY {close:.2f} vs 200-day {sma:.2f}: stay in {want}")
        return

    if have:  # the rule flipped: exit the old side first
        qty = min(lots[have]["qty"], max(broker.positions().get(have, 0), 0))
        if qty > 0:
            oid, status = broker.submit(have, qty, "sell", price[have], tag=NAME)
            journal.order(con, NAME, have, "sell", qty, price[have], broker.name, status, oid)
        journal.set_lot(con, NAME, have, 0)
        note(NAME, have, "sell", f"trend flipped to {want}", qty=qty, price=price[have])

    acct = broker.account()
    qty = int(tcfg["capital_pct"] * acct.equity // price[want])
    why = None  # total exposure is bounded by the fixed slices (breakout + portfolio + trend)
    if qty < 1:
        why = "position size rounds to 0 shares"
    elif qty * price[want] > acct.cash:
        why = "not enough cash"
    elif risk.orders_today() >= cfg["risk"]["max_orders_per_day"]:
        why = "max_orders_per_day reached"
    elif risk.kill_switch(acct):
        why = "daily loss kill switch is tripped"
    if why:
        note(NAME, want, "blocked", why, qty=qty, price=price[want])
        return
    oid, status = broker.submit(want, qty, "buy", price[want], tag=NAME)
    journal.order(con, NAME, want, "buy", qty, price[want], broker.name, status, oid)
    journal.set_lot(con, NAME, want, qty, price[want], str(spy.index[-1].date()))
    note(NAME, want, "buy", f"SPY {close:.2f} {'above' if want == 'SPY' else 'below'} 200-day {sma:.2f}",
         qty=qty, price=price[want])
