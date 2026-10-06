"""Big-bet sleeve: a small, capped slice of asymmetric bets, bought once and held. Paper only.

Spec (research/day-trade-brackets-and-big-bets.md, Shandon chose "your picks"):
- config.bigbet.weights are fractions of the whole account (BTC/USD 2%, ETH/USD 1%,
  NVDA 1%, AMD 1% = 5%). Each name is bought once, at its target weight, from cash.
- Buy and hold, no stops. No top-ups when a name falls.
- Trim every name back to its target weight on the first run in January, and whenever
  the sleeve grows past config.bigbet.max_pct (10%) of the account.
- Own journal lots under NAME and its own client_order_id tag ("bigbet-..."), so it never
  mixes with the breakout, portfolio or trend slices. Crypto trades 24/7 on Alpaca paper.
"""
import json
from datetime import date, timedelta

from . import journal

NAME = "Big bets"
TAG = "bigbet"


def is_crypto(symbol: str) -> bool:
    return "/" in symbol


def _prices(symbols, today: date, session) -> dict:
    """Latest price per symbol: Alpaca crypto bars for crypto, the daily close for stocks."""
    from .data import get_bars

    out = {}
    crypto = [s for s in symbols if is_crypto(s)]
    if crypto:
        from alpaca.data.historical import CryptoHistoricalDataClient
        from alpaca.data.requests import CryptoLatestBarRequest

        bars = CryptoHistoricalDataClient().get_crypto_latest_bar(CryptoLatestBarRequest(symbol_or_symbols=crypto))
        out.update({s: float(b.close) for s, b in bars.items()})
    for s in symbols:
        if is_crypto(s):
            continue
        df = get_bars(s, today - timedelta(days=10), today + timedelta(days=1), use_cache=False)
        if not df.empty and not (session and df.index[-1].date() < session):
            out[s] = float(df["close"].iloc[-1])
    return out


def _qty(value: float, price: float, symbol: str) -> float:
    return round(value / price, 6 if is_crypto(symbol) else 4)  # fractional shares and coins


def run(broker, con, cfg: dict, risk, note, prices_fn=None, today: date | None = None, state_path=None) -> None:
    bcfg = cfg.get("bigbet", {})
    if not bcfg.get("enabled"):
        return
    from .live import halted_today

    today = today or date.today()
    weights = bcfg["weights"]
    state_path = state_path or journal.DB.parent / "bigbet_state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    prices_fn = prices_fn or (lambda syms: _prices(syms, today, broker.last_session()))
    px = prices_fn(list(weights))
    lots = {s: journal.get_lot(con, NAME, s) for s in weights}
    acct = broker.account()
    value = {s: (l["qty"] * px[s] if l and s in px else 0.0) for s, l in lots.items()}
    sleeve = sum(value.values())

    # trims first: back to target weights, never buying the laggards
    reasons = []
    if any(lots.values()) and today.month == 1 and state.get("trimmed_year") != today.year:
        reasons.append("yearly trim")
    if sleeve > bcfg.get("max_pct", 0.10) * acct.equity:
        reasons.append(f"sleeve is {sleeve / acct.equity:.1%} of the account, over {bcfg.get('max_pct', 0.10):.0%}")
    if reasons:
        for s, l in lots.items():
            excess = value[s] - weights[s] * acct.equity
            qty = min(_qty(excess, px[s], s), l["qty"]) if l and s in px and excess > 0 else 0
            if qty <= 0:
                continue
            oid, status = broker.submit(s, qty, "sell", px[s], tag=TAG)
            journal.order(con, NAME, s, "sell", qty, px[s], broker.name, status, oid)
            journal.set_lot(con, NAME, s, round(l["qty"] - qty, 6), l["entry_price"], l["entry_date"])
            note(NAME, s, "sell", "; ".join(reasons), qty=qty, price=px[s])
        if today.month == 1:
            state["trimmed_year"] = today.year

    # first buys: each name once, at its target weight. A name already bought is never topped up.
    funded = set(state.get("funded", []))
    for s, w in weights.items():
        if s in funded or lots[s]:
            funded.add(s)
            continue
        if s not in px:
            note(NAME, s, "skip", "stale or missing price")
            continue
        acct = broker.account()
        qty = _qty(w * acct.equity, px[s], s)
        committed = sum(q * (p or 0) for q, p in con.execute("SELECT qty, entry_price FROM lots"))
        why = None
        if qty <= 0:
            why = "position size rounds to 0"
        elif halted_today():
            why = "live monitor halted new buys today"
        elif qty * px[s] > acct.cash:
            why = "not enough cash"
        elif committed + qty * px[s] > cfg["risk"]["max_total_exposure_pct"] * acct.equity:
            why = "would exceed max_total_exposure_pct"
        elif risk.orders_today() >= cfg["risk"]["max_orders_per_day"]:
            why = "max_orders_per_day reached"
        elif risk.kill_switch(acct):
            why = "daily loss kill switch is tripped"
        if why:
            note(NAME, s, "blocked", why, qty=qty, price=px[s])
            continue
        oid, status = broker.submit(s, qty, "buy", px[s], tag=TAG)
        journal.order(con, NAME, s, "buy", qty, px[s], broker.name, status, oid)
        journal.set_lot(con, NAME, s, qty, px[s], str(today))
        funded.add(s)
        note(NAME, s, "buy", f"first buy at {w:.0%} of the account; held, no stops", qty=qty, price=px[s])

    for s, l in lots.items():
        if l:
            note(NAME, s, "hold", "buy and hold", qty=l["qty"], price=px.get(s))
    state["funded"] = sorted(funded)
    state_path.write_text(json.dumps(state, indent=2))
