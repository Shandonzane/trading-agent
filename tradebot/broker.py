"""Brokers. Both are fake money: Alpaca PAPER, or a local simulator for offline runs.

There is intentionally no live-money broker here. Adding one is a separate, reviewed change.
"""
import json
import re
import uuid
from dataclasses import dataclass
from datetime import date

from .config import ROOT, env


def _slug(tag: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", tag.lower()).strip("-")[:40] or "agent"


def _coid(tag: str) -> str:
    """client_order_id tagged with the strategy, so orders can be traced per strategy."""
    return f"{_slug(tag)}-{uuid.uuid4().hex[:12]}"


def _order_symbol(p) -> str:
    """Alpaca lists crypto positions as BTCUSD but trades them as BTC/USD; journal lots use BTC/USD."""
    if "crypto" in str(p.asset_class).lower() and "/" not in p.symbol:
        return f"{p.symbol[:-3]}/{p.symbol[-3:]}"
    return p.symbol


@dataclass
class Account:
    equity: float
    cash: float
    last_equity: float  # equity at previous close, for the daily-loss kill switch


class AlpacaPaperBroker:
    name = "alpaca-paper"

    def __init__(self):
        from alpaca.trading.client import TradingClient

        # paper=True is hard-coded. Do not parameterize.
        self.client = TradingClient(env("ALPACA_API_KEY"), env("ALPACA_SECRET_KEY"), paper=True)

    def account(self) -> Account:
        a = self.client.get_account()
        return Account(float(a.equity), float(a.cash), float(a.last_equity))

    def positions(self) -> dict[str, float]:
        return {_order_symbol(p): float(p.qty) for p in self.client.get_all_positions()}

    def market_open(self) -> bool:
        return bool(self.client.get_clock().is_open)

    def submit(self, symbol: str, qty: float, side: str, ref_price: float, tag: str = "") -> tuple[str, str]:
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest

        o = self.client.submit_order(MarketOrderRequest(
            symbol=symbol, qty=qty, side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
            time_in_force=TimeInForce.GTC if "/" in symbol else TimeInForce.DAY,  # crypto needs GTC
            client_order_id=_coid(tag),
        ))
        return str(o.id), str(o.status)

    def avg_price(self, symbol: str) -> float | None:
        try:
            return float(self.client.get_open_position(symbol).avg_entry_price)
        except Exception:
            return None

    def _open_stops(self, symbol: str, tag: str):
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        orders = self.client.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[symbol]))
        return [o for o in orders if str(o.client_order_id).startswith(f"{_slug(tag)}-stop-")]

    def ensure_stop(self, symbol: str, qty: float, stop_price: float, tag: str) -> str:
        """Keep one GTC stop-loss order at the broker for this strategy's shares."""
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import StopOrderRequest

        stop_price = round(stop_price, 2)
        for o in self._open_stops(symbol, tag):
            if float(o.qty) == qty and abs(float(o.stop_price) - stop_price) < 0.01:
                return "kept"
            self.client.cancel_order_by_id(o.id)
        whole = float(qty).is_integer()  # Alpaca takes fractional stops as DAY orders only: re-placed each run
        self.client.submit_order(StopOrderRequest(
            symbol=symbol, qty=qty, side=OrderSide.SELL, time_in_force=TimeInForce.GTC if whole else TimeInForce.DAY,
            stop_price=stop_price, client_order_id=_coid(f"{tag}-stop")))
        return "placed"

    def stop_fills(self, days: int = 10) -> list[tuple]:
        """(order id, client_order_id, symbol, filled_at) for stop orders filled in the last few days."""
        from datetime import datetime, timedelta, timezone
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        orders = self.client.get_orders(GetOrdersRequest(
            status=QueryOrderStatus.CLOSED, after=datetime.now(timezone.utc) - timedelta(days=days), limit=500))
        return [(str(o.id), str(o.client_order_id), o.symbol, o.filled_at) for o in orders
                if "-stop-" in str(o.client_order_id) and o.filled_at and float(o.filled_qty or 0) > 0]

    def submit_limit(self, symbol: str, qty: float, side: str, limit: float, tag: str = "") -> tuple[str, str]:
        """Limit DAY order, used for option contracts (never market orders on wide spreads)."""
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import LimitOrderRequest

        o = self.client.submit_order(LimitOrderRequest(
            symbol=symbol, qty=qty, side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
            time_in_force=TimeInForce.DAY, limit_price=round(limit, 2), client_order_id=_coid(tag)))
        return str(o.id), str(o.status)

    def cancel_stops(self, symbol: str, tag: str):
        for o in self._open_stops(symbol, tag):
            self.client.cancel_order_by_id(o.id)

    def last_session(self) -> date:
        """Date of the most recent completed regular session (calendar times are US/Eastern)."""
        from datetime import timedelta
        from alpaca.trading.requests import GetCalendarRequest

        now = self.client.get_clock().timestamp  # tz-aware, US/Eastern
        local = now.replace(tzinfo=None)
        days = self.client.get_calendar(GetCalendarRequest(start=local.date() - timedelta(days=10), end=local.date()))
        return [d for d in days if d.close <= local][-1].date


class SizedBroker:
    """Makes a bigger paper account behave like a smaller one.

    Alpaca only lets you pick a paper balance when you reset the account on its website.
    With config "account_size" set, the agent sees equity, cash and last_equity reduced by
    (starting_cash - account_size), so every size, cap and kill switch works off the smaller
    amount while P&L still flows through one-for-one.
    """

    def __init__(self, broker, offset: float):
        self._b, self.offset = broker, offset

    def __getattr__(self, name):
        return getattr(self._b, name)

    def account(self) -> Account:
        a = self._b.account()
        return Account(a.equity - self.offset, max(a.cash - self.offset, 0.0), a.last_equity - self.offset)


class SimBroker:
    """Offline paper broker: fills instantly at the reference price, state in data/sim_account.json."""

    name = "sim"

    def __init__(self, cash: float = 100_000.0, path=ROOT / "data" / "sim_account.json"):
        self.path = path
        if path.exists():
            self.state = json.loads(path.read_text())
        else:
            self.state = {"cash": cash, "positions": {}, "prices": {}, "last_equity": cash, "day": str(date.today())}

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.state, indent=2))

    def mark(self, symbol: str, price: float):
        self.state["prices"][symbol] = price

    def account(self) -> Account:
        eq = self.state["cash"] + sum(q * self.state["prices"].get(s, 0) for s, q in self.state["positions"].items())
        if self.state["day"] != str(date.today()):
            self.state["last_equity"], self.state["day"] = eq, str(date.today())
            self._save()
        return Account(eq, self.state["cash"], self.state["last_equity"])

    def positions(self) -> dict[str, float]:
        return dict(self.state["positions"])

    def market_open(self) -> bool:
        return True

    def avg_price(self, symbol: str) -> float | None:
        return None

    def ensure_stop(self, *a, **k) -> str:
        return "sim: stops checked at the daily close"

    def cancel_stops(self, *a, **k):
        pass

    def stop_fills(self, *a, **k):
        return []

    def last_session(self):
        return None

    def submit_limit(self, symbol, qty, side, limit, tag=""):
        return self.submit(symbol, qty, side, limit, tag)

    def submit(self, symbol: str, qty: float, side: str, ref_price: float, tag: str = "") -> tuple[str, str]:
        sign = 1 if side == "buy" else -1
        self.state["cash"] -= sign * qty * ref_price
        newq = self.state["positions"].get(symbol, 0) + sign * qty
        if newq:
            self.state["positions"][symbol] = newq
        else:
            self.state["positions"].pop(symbol, None)
        self.mark(symbol, ref_price)
        self._save()
        return f"sim-{symbol}-{side}", "filled"
