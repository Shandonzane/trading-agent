"""The trading agent: runs approved strategies once per day and places PAPER orders.

Run after the close (signals use the day's close; Alpaca queues the market order for
the next open), e.g. `python cli.py run-agent` from a daily scheduler.
"""
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from . import journal
from .broker import Account, AlpacaPaperBroker, SimBroker, SizedBroker
from .config import ROOT, have_alpaca_keys, load_config
from .data import get_bars
from .strategy import StrategySpec, evaluate, load_spec

APPROVED = ROOT / "strategies" / "approved"


class RiskBlock(Exception):
    pass


class RiskManager:
    def __init__(self, risk: dict, con):
        self.r = risk
        self.con = con

    def orders_today(self) -> int:
        return self.con.execute("SELECT COUNT(*) FROM orders WHERE ts >= ?", (str(date.today()),)).fetchone()[0]

    def kill_switch(self, acct: Account) -> bool:
        return acct.last_equity > 0 and (acct.equity / acct.last_equity - 1) <= -self.r["daily_loss_kill_switch_pct"]

    def check_buy(self, symbol: str, qty: float, price: float, acct: Account, positions_value: float):
        if symbol not in self.r["allowed_symbols"]:
            raise RiskBlock(f"{symbol} not in allowed_symbols")
        if self.orders_today() >= self.r["max_orders_per_day"]:
            raise RiskBlock("max_orders_per_day reached")
        if self.kill_switch(acct):
            raise RiskBlock("daily loss kill switch is tripped")
        value = qty * price
        if value > self.r["max_position_pct"] * acct.equity * 1.001:
            raise RiskBlock("position larger than max_position_pct")
        if positions_value + value > self.r["max_total_exposure_pct"] * acct.equity:
            raise RiskBlock("would exceed max_total_exposure_pct")
        if value > acct.cash:
            raise RiskBlock("not enough cash")


def approved_specs() -> list[StrategySpec]:
    return [load_spec(p) for p in sorted(APPROVED.glob("*.json"))] if APPROVED.exists() else []


def fresh_bars(symbol, start, end):
    """Always refetch: a cached file can look fresh for days and replay an old close."""
    return get_bars(symbol, start, end, use_cache=False)


def reconcile(con, broker, specs, note):
    """Make journal lots match what the broker actually holds.

    Lots are written when an order is accepted. If an order never filled, or a broker-side
    stop sold the shares, the lot would otherwise outlive the shares and a later sell could
    open a short. Shortfalls come out of stop-carrying strategy lots first, then the rest.
    """
    held = broker.positions()
    session = broker.last_session()
    with_stops = {s.name for s in specs if s.stop_loss_pct}
    rows = con.execute("SELECT strategy, symbol, qty, entry_price, entry_date FROM lots").fetchall()
    by_sym = {}
    for r in rows:
        if session and r[4] and r[4] >= str(session):
            continue  # ordered after the last close: not filled yet, nothing to check
        by_sym.setdefault(r[1], []).append(r)
    for sym, lots in by_sym.items():
        short = sum(r[2] for r in lots) - max(held.get(sym, 0), 0)
        for strat, _, qty, px, d in sorted(lots, key=lambda r: r[0] not in with_stops):
            if short <= 1e-9:
                break
            cut = min(qty, short)
            journal.set_lot(con, strat, sym, qty - cut, px, d)
            short -= cut
            note(strat, sym, "reconcile", "broker holds fewer shares than the journal "
                 "(order never filled, or a broker stop sold them)", removed=cut)


def run_once(broker=None, specs=None, bars_fn=fresh_bars, today: date | None = None) -> list[dict]:
    cfg = load_config()
    con = journal.connect()
    if broker is None:
        broker = AlpacaPaperBroker() if have_alpaca_keys() else SimBroker(cfg["starting_cash"])
        if cfg.get("account_size") and isinstance(broker, AlpacaPaperBroker):
            broker = SizedBroker(broker, cfg["starting_cash"] - cfg["account_size"])
    risk = RiskManager(cfg["risk"], con)
    specs = approved_specs() if specs is None else specs
    today = today or date.today()
    log = []

    def note(strategy, symbol, action, reason, **detail):
        journal.decision(con, strategy, symbol, action, reason, detail)
        log.append({"strategy": strategy, "symbol": symbol, "action": action, "reason": reason, **detail})

    reconcile(con, broker, specs, note)

    if cfg.get("portfolio", {}).get("enabled"):
        from . import portfolio_agent

        try:
            portfolio_agent.run(broker, con, cfg, risk, note)
        except Exception as e:  # never let the portfolio stop the strategies from running
            note("portfolio", "-", "error", f"{type(e).__name__}: {e}")

    if cfg.get("trend", {}).get("enabled"):
        from . import trend_agent

        try:
            trend_agent.run(broker, con, cfg, risk, note)
        except Exception as e:
            note(trend_agent.NAME, "-", "error", f"{type(e).__name__}: {e}")

    if not specs:
        note("-", "-", "idle", "No approved strategies yet. Backtest and approve one first.")
        return log

    bars = {}
    for spec in specs:
        for sym in spec.symbols:
            if sym not in bars:
                bars[sym] = bars_fn(sym, today - timedelta(days=500), today + timedelta(days=1))
    session = broker.last_session()
    for sym in list(bars):
        if session and (bars[sym].empty or bars[sym].index[-1].date() < session):
            got = bars[sym].index[-1].date() if len(bars[sym]) else "nothing"
            note("-", sym, "skip", f"stale data: last bar {got}, expected {session}")
            del bars[sym]
    if isinstance(broker, SimBroker):
        for sym, df in bars.items():
            broker.mark(sym, float(df["close"].iloc[-1]))

    acct = broker.account()
    if risk.kill_switch(acct):
        note("-", "-", "halt", "Daily loss kill switch tripped; no new orders today", equity=acct.equity)

    for spec in specs:
        for sym in spec.symbols:
            if sym not in bars:
                continue
            df = bars[sym]
            if len(df) < 2:
                continue
            last = df.index[-1]
            price = float(df["close"].iloc[-1])
            entry = bool(evaluate(df, spec.entry).iloc[-1])
            exit_ = bool(evaluate(df, spec.exit).iloc[-1])
            lot = journal.get_lot(con, spec.name, sym)

            if lot:
                why = None
                if exit_:
                    why = "exit rule fired"
                elif spec.stop_loss_pct and price <= lot["entry_price"] * (1 - spec.stop_loss_pct):
                    why = "stop loss"
                elif spec.take_profit_pct and price >= lot["entry_price"] * (1 + spec.take_profit_pct):
                    why = "take profit"
                elif spec.max_hold_days and (pd.Timestamp(last) - pd.Timestamp(lot["entry_date"])).days >= spec.max_hold_days * 7 / 5:
                    why = "max hold reached"
                if why:
                    broker.cancel_stops(sym, spec.name)
                    qty = min(lot["qty"], max(broker.positions().get(sym, 0), 0))
                    if qty > 0:
                        oid, status = broker.submit(sym, qty, "sell", price, tag=spec.name)
                        journal.order(con, spec.name, sym, "sell", qty, price, broker.name, status, oid)
                    journal.set_lot(con, spec.name, sym, 0)
                    note(spec.name, sym, "sell", why, qty=qty, price=price)
                else:
                    detail = {}
                    if spec.stop_loss_pct:  # stop lives at the broker so it fires intraday, as in the backtest
                        detail["stop"] = broker.ensure_stop(sym, lot["qty"], lot["entry_price"] * (1 - spec.stop_loss_pct), spec.name)
                    note(spec.name, sym, "hold", "in position, no exit", price=price, **detail)
            elif entry:
                acct = broker.account()
                pos_val = sum(abs(q) * float(bars[s]["close"].iloc[-1]) if s in bars else 0
                              for s, q in broker.positions().items())
                qty = int(min(spec.position_size_pct, cfg["risk"]["max_position_pct"]) * acct.equity // price)
                try:
                    if qty < 1:
                        raise RiskBlock("position size rounds to 0 shares")
                    risk.check_buy(sym, qty, price, acct, pos_val)
                except RiskBlock as e:
                    note(spec.name, sym, "blocked", str(e), qty=qty, price=price)
                    continue
                oid, status = broker.submit(sym, qty, "buy", price, tag=spec.name)
                journal.order(con, spec.name, sym, "buy", qty, price, broker.name, status, oid)
                journal.set_lot(con, spec.name, sym, qty, price, str(last.date()))
                note(spec.name, sym, "buy", "entry rule fired", qty=qty, price=price)
            else:
                note(spec.name, sym, "wait", "no entry signal", price=price)

    acct = broker.account()
    journal.snapshot(con, broker.name, acct.equity, acct.cash)
    return log
