"""Crash hedge: small SPYM put options on a 5% slice. Paper only.

Spec: research/worst-case-hedge.md section 6 (Shandon picked puts, then "assign another 5% to
hedge a crash", 2026-10-06).
- Every ~6 months buy SPYM puts about 6 months out, strike about 20% below SPYM. One contract
  per ~$9,000 of stock (stock_share of the account), so 2 on a $20k account.
- Limit orders, never market orders: these spreads are wide. A buy starts at the bid/ask midpoint
  and, each day it is still unfilled, moves halfway closer to the ask (the ask by day 3).
- Hold to expiry. A put that's in the money within `sell_before_days` of expiry is sold, so it is
  never exercised (exercise would open a short). Out-of-the-money puts just expire.
- The rest of the slice's budget sits in BIL (T-bills). The budget starts at capital_pct of the
  account and is not topped up; premiums come out of it and put sales go back in. If a crash
  payoff lifts it past 1.5x its starting size, the excess is released to account cash, where the
  stock slices reinvest it at their next rebalance (research/crash-plan.md).
"""
import json
from datetime import date, timedelta

from . import journal

NAME = "Crash hedge (puts)"
TAG = "hedge"


def parse_occ(sym: str) -> tuple[date, float]:
    """SPYM270319P00075000 -> (2027-03-19, 75.0)."""
    tail = sym[-15:]
    return date(2000 + int(tail[:2]), int(tail[2:4]), int(tail[4:6])), int(tail[7:]) / 1000


def _alpaca_chain(underlying, lo, hi):
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import GetOptionContractsRequest

    from .config import env

    key, sec = env("ALPACA_API_KEY"), env("ALPACA_SECRET_KEY")
    cs = TradingClient(key, sec, paper=True).get_option_contracts(GetOptionContractsRequest(
        underlying_symbols=[underlying], type="put", expiration_date_gte=lo, expiration_date_lte=hi, limit=1000)).option_contracts
    return [c.symbol for c in cs]


def _alpaca_quotes(symbols):
    from alpaca.data.historical.option import OptionHistoricalDataClient
    from alpaca.data.requests import OptionLatestQuoteRequest

    from .config import env

    q = OptionHistoricalDataClient(env("ALPACA_API_KEY"), env("ALPACA_SECRET_KEY")).get_option_latest_quote(
        OptionLatestQuoteRequest(symbol_or_symbols=list(symbols)))
    return {s: (float(v.bid_price or 0), float(v.ask_price or 0)) for s, v in q.items()}


def _last_close(sym, today):
    from .data import get_bars

    df = get_bars(sym, today - timedelta(days=10), today + timedelta(days=1), use_cache=False)
    return float(df["close"].iloc[-1])


def run(broker, con, cfg: dict, risk, note, today: date | None = None, chain_fn=None, quotes_fn=None,
        price_fn=None, state_path=None) -> None:
    h = cfg.get("hedge", {})
    if not h.get("enabled"):
        return
    from .live import halted_today

    today = today or date.today()
    chain_fn, quotes_fn = chain_fn or _alpaca_chain, quotes_fn or _alpaca_quotes
    price_fn = price_fn or (lambda s: _last_close(s, today))
    state_path = state_path or journal.DB.parent / "hedge_state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    acct = broker.account()
    start = h["capital_pct"] * acct.equity
    budget = state.get("budget", start)
    und, cash_etf = h.get("underlying", "SPYM"), h.get("cash_etf", "BIL")
    spot = price_fn(und)

    puts = [(s, l) for s, l in ((r[0], journal.get_lot(con, NAME, r[0])) for r in
                                con.execute("SELECT symbol FROM lots WHERE strategy=?", (NAME,)))
            if s != cash_etf and l]
    live = []
    for sym, lot in puts:
        exp, strike = parse_occ(sym)
        if exp < today:
            journal.set_lot(con, NAME, sym, 0)
            note(NAME, sym, "expired", f"expired out of the money (strike {strike:g}, SPYM {spot:.2f})")
            continue
        if (exp - today).days <= h.get("sell_before_days", 4):
            if strike > spot:  # in the money: sell so it's never exercised
                bid, ask = quotes_fn([sym]).get(sym, (0, 0))
                px = round((bid + ask) / 2, 2) if bid else 0.01
                oid, status = broker.submit_limit(sym, lot["qty"], "sell", px, tag=TAG)
                journal.order(con, NAME, sym, "sell", lot["qty"], px, broker.name, status, oid)
                journal.set_lot(con, NAME, sym, 0)
                budget += lot["qty"] * 100 * px
                note(NAME, sym, "sell", "in the money near expiry: sold so it is never exercised", qty=lot["qty"], price=px)
            continue  # out of the money near expiry: let it expire, roll into a new one now
        live.append(sym)

    if not live:
        n = max(1, round(h.get("stock_share", 0.70) * acct.equity / (100 * spot)))
        target_exp = today + timedelta(days=h.get("days_out", 182))
        syms = chain_fn(und, today + timedelta(days=120), today + timedelta(days=240))
        why, pick = None, None
        if not syms:
            why = f"no {und} puts 4-8 months out"
        else:
            exp = min({parse_occ(s)[0] for s in syms}, key=lambda d: abs((d - target_exp).days))
            same = [s for s in syms if parse_occ(s)[0] == exp]
            pick = min(same, key=lambda s: abs(parse_occ(s)[1] - spot * (1 - h.get("otm", 0.20))))
            bid, ask = quotes_fn([pick]).get(pick, (0, 0))
            # a midpoint limit often expires unfilled: each day the buy is still missing, pay
            # half of the way from the midpoint to the ask more, capped at the ask
            tries = state.get("tries", 0) + 1 if state.get("last_try", str(today)) < str(today) else 0
            mid = round(min(ask, (bid + ask) / 2 + min(tries, 2) * (ask - bid) / 4), 2)
            if tries:  # the unfilled order never spent its premium
                budget += state.pop("last_cost", 0)
            cost = n * 100 * mid
            if not bid or not ask:
                why = "no two-sided quote"
            elif cost > budget:
                why = f"premium ${cost:.0f} is more than the slice's ${budget:.0f} budget"
            elif halted_today():
                why = "live monitor halted new buys today"
            elif risk.orders_today() >= cfg["risk"]["max_orders_per_day"]:
                why = "max_orders_per_day reached"
        if why:
            note(NAME, pick or und, "blocked", why)
        else:
            oid, status = broker.submit_limit(pick, n, "buy", mid, tag=TAG)
            journal.order(con, NAME, pick, "buy", n, mid, broker.name, status, oid)
            journal.set_lot(con, NAME, pick, n, mid, str(today))
            budget -= cost
            state.update(tries=tries, last_try=str(today), last_cost=round(cost, 2))
            note(NAME, pick, "buy", f"{n} puts, strike {parse_occ(pick)[1]:g} vs SPYM {spot:.2f}, expiring {exp}",
                 qty=n, price=mid, cost=round(cost, 2))
    else:
        for k in ("tries", "last_try", "last_cost"):
            state.pop(k, None)
        note(NAME, ", ".join(live), "hold", "holding puts to expiry")

    if budget > 1.5 * start:  # a crash paid off: release the excess to the stock slices
        note(NAME, "-", "release", f"hedge budget ${budget:.0f} > 1.5x ${start:.0f}; ${budget - start:.0f} back to account cash")
        budget = start

    # park the rest of the budget in T-bills
    bpx = price_fn(cash_etf)
    lot = journal.get_lot(con, NAME, cash_etf)
    have = lot["qty"] if lot else 0
    want = int(max(budget, 0) // bpx)  # the budget is the slice's non-option money: BIL plus loose cash
    diff = want - have
    if abs(diff) >= 2:
        why = ("max_orders_per_day reached" if risk.orders_today() >= cfg["risk"]["max_orders_per_day"] else
               "not enough cash" if diff > 0 and diff * bpx > acct.cash else
               "live monitor halted new buys today" if diff > 0 and halted_today() else None)
        if why:
            note(NAME, cash_etf, "blocked", why, qty=diff)
        else:
            side = "buy" if diff > 0 else "sell"
            oid, status = broker.submit(cash_etf, abs(diff), side, bpx, tag=TAG)
            journal.order(con, NAME, cash_etf, side, abs(diff), bpx, broker.name, status, oid)
            journal.set_lot(con, NAME, cash_etf, want, bpx, (lot or {}).get("entry_date") or str(today))
            note(NAME, cash_etf, side, "slice cash in T-bills", qty=abs(diff), price=bpx)
    state.update(budget=round(budget, 2), start=round(start, 2))
    state_path.write_text(json.dumps(state, indent=2))
