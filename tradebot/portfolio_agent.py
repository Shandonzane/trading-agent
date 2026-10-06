"""Runs the "Speculative + trend" ETF portfolio inside the daily paper agent.

Targets and flags come from portfolio.py (the dry-run tool); this module turns them into
PAPER orders through the same journal, broker and risk limits as the strategy agent.

- The portfolio runs on its own slice of the account: config.portfolio.capital_pct.
- Its holdings are tracked as journal lots under the portfolio's name, so a symbol it shares
  with a strategy (SPY) is never double-counted or sold out from under the other.
- Rebalances on the first run, the first trading day of each quarter, or when the SPY
  trend pick, the ex-US momentum pick or a data flag changes. On a rebalance only positions
  more than the drift band off target are traded. Sells go first, then buys.
- Per-ETF cap is the spec's max_single_etf of the portfolio slice, in place of the
  strategy cap (max_position_pct), which is meant for single-strategy bets.
- News flags come from newsflags.latest() (if it ran in the last 4 days); a "yellow" private-credit
  reading moves 5 pts from SPY to SHY, per the spec.
"""
import json
import sys
from datetime import date

from . import journal
from .config import ROOT

SPEC = ROOT / "strategies" / "portfolio" / "speculative_trend.json"


def _apply_flags(w: dict, on: set) -> dict:
    """Turn flag actions into weight changes. Sizes are ours where the spec gives none."""
    w = dict(w)

    def move(src, dst, amt=None):
        amt = w.get(src, 0) if amt is None else min(amt, w.get(src, 0))
        w[src] = w.get(src, 0) - amt
        w[dst] = w.get(dst, 0) + amt

    if "small_cap_leadership" in on and "SPY" in w:
        move("SPY", "RSP", 0.10)
    if "fed_hike" in on:  # stagflation tilt: homebuilders out, split into energy and gold
        cut = w.get("ITB", 0)
        w["ITB"] = 0
        w["XLE"] = w.get("XLE", 0) + cut / 2
        w["GLD"] = w.get("GLD", 0) + cut / 2
    if "yield_spike" in on:
        move("TLT", "SHY")
    if "vix_panic" in on:
        move("SHY", "SPY", w.get("SHY", 0))
    if "hyperscaler_capex_cut" in on:
        move("SPY", "SHY", w.get("SPY", 0) / 2)
        move("RSP", "SHY", w.get("RSP", 0) / 2)
    if on & {"private_credit_gate", "taiwan_rare_earths"}:  # panic playbook: core to cash
        move("SPY", "SHY")
        move("RSP", "SHY")
    if "private_credit_yellow" in on:  # spec "yellow": raise cash by 5 pts, nothing else
        move("SPY", "SHY", 0.05)
    return {k: round(v, 4) for k, v in w.items() if v > 1e-9}


def plan(spec: dict, px) -> tuple[dict, dict, set, list]:
    sys.path.insert(0, str(ROOT))
    import portfolio  # the dry-run tool owns target and flag logic

    w, why = portfolio.targets(spec, px)
    flags = portfolio.check_flags(spec, px)
    on = {f["id"] for f in flags if f.get("on")}
    try:  # headline check from newsflags.py, used only if it ran in the last few days
        from newsflags import latest

        news = latest() or {}
        if news.get("date") and (date.today() - date.fromisoformat(news["date"])).days <= 4:
            flags = [f for f in flags if f.get("on") is not None]
            for f in news.get("flags", []):
                flags.append({"id": f["id"], "on": bool(f.get("on")), "detail": f.get("note", "")[:200]})
                on |= {f["id"]} if f.get("on") else set()
                if f.get("yellow"):
                    on.add(f["id"].split("_gate")[0] + "_yellow")
    except Exception as e:
        flags.append({"id": "news", "on": None, "status": f"headline check unavailable ({e})"})
    picks = {"core_spy_trend": "SPY" if "SPY" in why["core_spy_trend"].split("->")[-1] else "SHY",
             "ex_us_momentum": why["ex_us_momentum"].split("->")[-1].strip()}
    return _apply_flags(w, on), picks, on, flags


def run(broker, con, cfg: dict, risk, note, prices_fn=None, today: date | None = None) -> None:
    if not SPEC.exists() or not cfg.get("portfolio", {}).get("enabled"):
        return
    spec = json.loads(SPEC.read_text())
    if spec.get("mode") != "paper":
        raise RuntimeError("portfolio spec must be paper mode")
    name = spec["name"]
    today = today or date.today()
    if prices_fn is None:
        sys.path.insert(0, str(ROOT))
        import portfolio

        prices_fn = portfolio.prices
    px = prices_fn()
    weights, picks, on, flags = plan(spec, px)
    last_px = {t: float(px[t].iloc[-1]) for t in px.columns}

    state_path = journal.DB.parent / "portfolio_state.json"  # lives beside the journal
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    held = {t: journal.get_lot(con, name, t) for t in set(weights) | set(state.get("weights", {}))}
    held = {t: l for t, l in held.items() if l}
    quarter = f"{today.year}Q{(today.month - 1) // 3 + 1}"
    reasons = []
    if not held:
        reasons.append("first allocation")
    if state.get("quarter") and state["quarter"] != quarter:
        reasons.append(f"new quarter {quarter}")
    if state.get("picks") and state["picks"] != picks:
        reasons.append(f"rule flip {state['picks']} -> {picks}")
    if "flags_on" in state and set(state["flags_on"]) != on:
        reasons.append(f"flags changed {sorted(state['flags_on'])} -> {sorted(on)}")
    for f in flags:
        if f.get("on") is None and "status" in f:
            note(name, "-", "flag_unchecked", f"{f['id']}: {f['status']}")
    if not reasons:
        note(name, "-", "hold", "no rebalance trigger", picks=picks, flags_on=sorted(on))
        return

    acct = broker.account()
    capital = cfg["portfolio"]["capital_pct"] * acct.equity
    band = spec["drift_band"] * capital
    cap = spec["hard_limits"]["max_single_etf"]
    note(name, "-", "rebalance", "; ".join(reasons), capital=round(capital, 2),
         weights=weights, flags_on=sorted(on))

    sells, buys = [], []
    for t in sorted(set(weights) | set(held)):
        price = last_px.get(t)
        if not price:
            note(name, t, "blocked", "no price")
            continue
        have = held.get(t, {}).get("qty", 0)
        diff = weights.get(t, 0) * capital - have * price
        if t not in weights and have:
            sells.append((t, have, price))
        elif abs(diff) > band or (not have and weights.get(t, 0) > 0):
            qty = int(abs(diff) // price)
            if qty:
                (buys if diff > 0 else sells).append((t, min(qty, have) if diff < 0 else qty, price))

    for t, qty, price in sells:
        oid, status = broker.submit(t, qty, "sell", price, tag=name)
        journal.order(con, name, t, "sell", qty, price, broker.name, status, oid)
        lot = held[t]
        journal.set_lot(con, name, t, lot["qty"] - qty, lot["entry_price"], lot["entry_date"])
        note(name, t, "sell", "rebalance", qty=qty, price=price)

    allowed = {t for s in spec["sleeves"].values() for t in [s.get("hold")] if t} | {"SPY", "SHY", "EFA", "EEM", "EWJ"}
    for t, qty, price in buys:
        acct = broker.account()
        committed = sum(q * last_px.get(s, 0) for s, q in con.execute("SELECT symbol, qty FROM lots"))
        have = (held.get(t) or {}).get("qty", 0)
        why = None
        if t not in allowed:
            why = f"{t} not in the portfolio's ETF list"
        elif (have + qty) * price > cap * capital * 1.001:
            why = f"over max_single_etf {cap:.0%} of the portfolio"
        elif committed + qty * price > cfg["risk"]["max_total_exposure_pct"] * acct.equity:
            why = "would exceed max_total_exposure_pct"
        elif risk.orders_today() >= cfg["risk"]["max_orders_per_day"]:
            why = "max_orders_per_day reached"
        elif risk.kill_switch(acct):
            why = "daily loss kill switch is tripped"
        if why:
            note(name, t, "blocked", why, qty=qty, price=price)
            continue
        oid, status = broker.submit(t, qty, "buy", price, tag=name)
        journal.order(con, name, t, "buy", qty, price, broker.name, status, oid)
        lot = held.get(t)
        avg = ((lot["qty"] * lot["entry_price"] + qty * price) / (lot["qty"] + qty)) if lot else price
        journal.set_lot(con, name, t, have + qty, avg, (lot or {}).get("entry_date") or str(today))
        note(name, t, "buy", "rebalance", qty=qty, price=price)

    state_path.write_text(json.dumps({"quarter": quarter, "picks": picks, "flags_on": sorted(on),
                                 "weights": weights, "last_rebalance": str(today)}, indent=2))
