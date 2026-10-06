"""Live paper-trading monitor: streams Alpaca quotes, news and fills during market hours.

  python cli.py live                # run until the close (Ctrl+C to stop)

What it does now (observe + protect; it never opens new positions):
- Streams 1-minute bars for every symbol the agent trades or holds (IEX real-time feed).
- Streams news for those symbols.
- Streams our own order fills and cancels.
- Every `snapshot_secs` it reads the paper account and checks the intraday kill switch:
  if the (sized) account is down `daily_loss_kill_switch_pct` on the day, it cancels every
  open BUY order and writes data/live/HALT, which the daily agent treats as "no new buys today".
- Writes a live feed for the dashboard:
    data/live/state.json   latest snapshot (account, P&L, positions with live prices, recent news/fills)
    data/live/feed-YYYY-MM-DD.jsonl   one JSON event per line (bar, news, fill, snapshot, halt)

New intraday entries stay off until a strategy passes an intraday paper trial.
"""
import asyncio
import json
import signal
from collections import deque
from datetime import date, datetime, timezone
from pathlib import Path

from .config import ROOT, env, load_config

LIVE = ROOT / "data" / "live"
HALT = LIVE / "HALT"


def halted_today() -> bool:
    return HALT.exists() and HALT.read_text().strip() == str(date.today())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class LiveMonitor:
    def __init__(self, cfg: dict | None = None, out_dir: Path = LIVE, snapshot_secs: int = 30):
        from alpaca.trading.client import TradingClient

        self.cfg = cfg or load_config()
        self.key, self.secret = env("ALPACA_API_KEY"), env("ALPACA_SECRET_KEY")
        self.client = TradingClient(self.key, self.secret, paper=True)  # paper only, hard-coded
        self.offset = (self.cfg["starting_cash"] - self.cfg["account_size"]) if self.cfg.get("account_size") else 0.0
        self.out = out_dir
        self.out.mkdir(parents=True, exist_ok=True)
        self.snapshot_secs = snapshot_secs
        self.prices: dict[str, float] = {}
        self.news = deque(maxlen=30)
        self.fills = deque(maxlen=30)
        self.state: dict = {}
        self.symbols = self._watchlist()

    # ---------- setup ----------
    def _watchlist(self) -> list[str]:
        # stock stream only: crypto positions (BTCUSD) are priced by the 30s snapshot instead
        syms = {p.symbol for p in self.client.get_all_positions() if "crypto" not in str(p.asset_class).lower()}
        approved = ROOT / "strategies" / "approved"
        for f in approved.glob("*.json") if approved.exists() else []:
            syms |= set(json.loads(f.read_text()).get("symbols", []))
        spec = ROOT / "strategies" / "portfolio" / "speculative_trend.json"
        if spec.exists() and self.cfg.get("portfolio", {}).get("enabled"):
            s = json.loads(spec.read_text())
            syms |= {v["hold"] for v in s["sleeves"].values() if v.get("hold")} | {"SPY", "SHY", "EFA", "EEM", "EWJ"}
        if self.cfg.get("trend", {}).get("enabled"):
            syms |= {"SPY", "SHY"}
        if self.cfg.get("bigbet", {}).get("enabled"):
            syms |= {s for s in self.cfg["bigbet"]["weights"] if "/" not in s}
        return sorted(syms)

    # ---------- output ----------
    def emit(self, kind: str, **data):
        rec = {"ts": _now(), "kind": kind, **data}
        with open(self.out / f"feed-{date.today()}.jsonl", "a") as f:
            f.write(json.dumps(rec, default=str) + "\n")
        return rec

    def write_state(self):
        tmp = self.out / "state.json.tmp"
        tmp.write_text(json.dumps(self.state, default=str, indent=1))
        tmp.replace(self.out / "state.json")

    # ---------- handlers ----------
    async def on_bar(self, bar):
        self.prices[bar.symbol] = float(bar.close)
        self.emit("bar", symbol=bar.symbol, t=bar.timestamp, o=bar.open, h=bar.high, l=bar.low, c=bar.close, v=bar.volume)

    async def on_news(self, n):
        item = {"t": n.created_at, "headline": n.headline, "symbols": n.symbols, "source": n.source, "url": n.url}
        self.news.appendleft(item)
        self.emit("news", **item)

    async def on_trade_update(self, u):
        o = u.order
        item = {"t": u.timestamp, "event": str(u.event), "symbol": o.symbol, "side": str(o.side), "qty": o.qty,
                "filled_qty": o.filled_qty, "price": u.price, "tag": o.client_order_id}
        self.fills.appendleft(item)
        self.emit("fill", **item)

    # ---------- periodic ----------
    def snapshot(self) -> dict:
        a = self.client.get_account()
        equity, last = float(a.equity) - self.offset, float(a.last_equity) - self.offset
        pos = []
        for p in self.client.get_all_positions():
            px = self.prices.get(p.symbol, float(p.current_price))
            qty = float(p.qty)
            pos.append({"symbol": p.symbol, "qty": qty, "avg": float(p.avg_entry_price), "price": px,
                        "value": qty * px, "unrealized": qty * (px - float(p.avg_entry_price))})
        day_pl = equity - last
        self.state = {"ts": _now(), "account_size": self.cfg.get("account_size"), "equity": equity,
                      "last_equity": last, "day_pl": day_pl, "day_pl_pct": day_pl / last if last else 0,
                      "cash": float(a.cash) - self.offset, "positions": pos, "prices": self.prices,
                      "news": list(self.news), "fills": list(self.fills), "halted": halted_today(),
                      "watchlist": self.symbols}
        return self.state

    def kill_switch(self):
        s = self.state
        limit = self.cfg["risk"]["daily_loss_kill_switch_pct"]
        if s.get("last_equity") and s["day_pl_pct"] <= -limit and not halted_today():
            from alpaca.trading.enums import OrderSide, QueryOrderStatus
            from alpaca.trading.requests import GetOrdersRequest

            canceled = []
            for o in self.client.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN)):
                if o.side == OrderSide.BUY:
                    self.client.cancel_order_by_id(o.id)
                    canceled.append(o.symbol)
            HALT.write_text(str(date.today()))
            self.emit("halt", reason=f"down {s['day_pl_pct']:.1%} today", canceled_buys=canceled)
            s["halted"] = True

    async def periodic(self, stop: asyncio.Event):
        while not stop.is_set():
            try:
                self.snapshot()
                self.kill_switch()
                self.write_state()
                self.emit("snapshot", equity=self.state["equity"], day_pl=self.state["day_pl"])
            except Exception as e:  # keep streaming even if one REST call fails
                self.emit("error", where="snapshot", error=str(e))
            try:
                await asyncio.wait_for(stop.wait(), self.snapshot_secs)
            except asyncio.TimeoutError:
                pass

    # ---------- run ----------
    async def run(self, until_close: bool = True):
        from alpaca.data.live import NewsDataStream, StockDataStream
        from alpaca.trading.stream import TradingStream

        stocks = StockDataStream(self.key, self.secret)
        news = NewsDataStream(self.key, self.secret)
        trades = TradingStream(self.key, self.secret, paper=True)
        stocks.subscribe_bars(self.on_bar, *self.symbols)
        news.subscribe_news(self.on_news, *self.symbols)
        trades.subscribe_trade_updates(self.on_trade_update)
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop.set)
            except NotImplementedError:
                pass
        if until_close:
            close = self.client.get_clock().next_close
            loop.call_later(max((close - datetime.now(close.tzinfo)).total_seconds() + 120, 60), stop.set)
        self.emit("start", symbols=self.symbols)
        tasks = [asyncio.create_task(c) for c in (stocks._run_forever(), news._run_forever(),
                                                   trades._run_forever(), self.periodic(stop))]
        await stop.wait()
        for s in (stocks, news, trades):
            try:
                await s.stop_ws()
            except Exception:
                pass
        for t in tasks:
            t.cancel()
        self.snapshot()
        self.write_state()
        self.emit("stop")


def main(until_close: bool = True):
    asyncio.run(LiveMonitor().run(until_close))
