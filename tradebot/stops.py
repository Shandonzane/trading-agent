"""12% trailing stops on every stock holding, and the re-entry rule after a stop fires. Paper only.

Shandon asked for a 12% stop on any stock (2026-10-06). Backtests on SPY, QQQ and EFA
(research/crash-plan.md) showed a stop measured from the entry price almost never fires on a
long hold, so the stop trails the highest close since entry. A stop-out without a rule to
get back in just turns a dip into cash forever, so a stopped-out holding waits until:
  - its close is back above its 200-day average (the crash is over), or
  - it is 30% or more below the peak the stop trailed (buy the crash),
and then the slice that owned it buys it back at its normal size.

Covered: the portfolio, trend and big-bet slices' stock and stock-ETF lots. Not covered:
the breakout slice (its own spec stop, now 12%), crypto, options, and bond/gold/cash ETFs.
State lives beside the journal in stops_state.json.
"""
import json
from datetime import date

from . import journal
from .broker import _slug

COVERED = ("Speculative + trend (paper)", "SPY 200-day trend", "Big bets")


def _path():
    return journal.DB.parent / "stops_state.json"


def load() -> dict:
    p = _path()
    return json.loads(p.read_text()) if p.exists() else {"peaks": {}, "out": {}, "seen": []}


def save(state: dict):
    _path().write_text(json.dumps(state, indent=2))


def _key(strategy, symbol):
    return f"{strategy}|{symbol}"


def covered(cfg: dict, strategy: str, symbol: str) -> bool:
    s = cfg.get("stops", {})
    return (s.get("enabled") and strategy in COVERED and "/" not in symbol and len(symbol) <= 6
            and symbol not in s.get("exclude", []))


def record_stop_outs(broker, cfg: dict, note) -> None:
    """Mark holdings whose broker stop filled since the last run as stopped out."""
    if not cfg.get("stops", {}).get("enabled"):
        return
    st = load()
    prefixes = {f"{_slug(s)}-stop-": s for s in COVERED}
    for oid, coid, sym, when in broker.stop_fills():
        if oid in st["seen"]:
            continue
        strat = next((s for p, s in prefixes.items() if coid.startswith(p)), None)
        if not strat:
            continue
        st["seen"].append(oid)
        k = _key(strat, sym)
        st["out"][k] = {"since": str(when)[:10], "peak": st["peaks"].pop(k, None)}
        note(strat, sym, "stopped_out", "12% trailing stop filled; waiting for the re-entry rule",
             peak=st["out"][k]["peak"])
    st["seen"] = st["seen"][-200:]
    save(st)


def waiting(strategy: str, symbol: str, closes, note=None) -> bool:
    """True while a stopped-out holding should stay in cash. Clears the mark on re-entry."""
    st = load()
    k = _key(strategy, symbol)
    out = st["out"].get(k)
    if not out:
        return False
    closes = closes.dropna()
    close = float(closes.iloc[-1])
    sma = float(closes.rolling(200).mean().iloc[-1]) if len(closes) >= 200 else None
    peak = out.get("peak")
    why = None
    if sma and close > sma:
        why = f"back above its 200-day average ({close:.2f} > {sma:.2f})"
    elif peak and close <= peak * 0.70:
        why = f"30%+ below its {peak:.2f} peak: buying the crash"
    if not why:
        return True
    del st["out"][k]
    save(st)
    if note:
        note(strategy, symbol, "re_entry", why)
    return False


def is_out(strategy: str, symbol: str) -> bool:
    return _key(strategy, symbol) in load()["out"]


def protect(broker, con, cfg: dict, note, bars_fn, session) -> None:
    """Place or raise the trailing stop at the broker for every covered, filled lot."""
    if not cfg.get("stops", {}).get("enabled"):
        return
    pct = cfg["stops"]["trail_pct"]
    st = load()
    live = set()
    for strat, sym, qty, entry_px, entry_date in con.execute(
            "SELECT strategy, symbol, qty, entry_price, entry_date FROM lots").fetchall():
        if not covered(cfg, strat, sym) or qty <= 0:
            continue
        if session and entry_date and entry_date >= str(session):
            continue  # not filled yet; the stop goes in once the shares exist
        k = _key(strat, sym)
        live.add(k)
        df = bars_fn(sym)
        since = df[df.index.date >= date.fromisoformat(entry_date)] if entry_date else df
        peak = max([st["peaks"].get(k) or 0, entry_px or 0] + ([float(since["close"].max())] if len(since) else []))
        st["peaks"][k] = peak
        try:
            status = broker.ensure_stop(sym, qty, peak * (1 - pct), strat)
            note(strat, sym, "stop", f"{pct:.0%} trailing stop {status}", stop=round(peak * (1 - pct), 2), peak=round(peak, 2))
        except Exception as e:
            note(strat, sym, "error", f"stop not placed: {type(e).__name__}: {e}")
    st["peaks"] = {k: v for k, v in st["peaks"].items() if k in live}
    save(st)
