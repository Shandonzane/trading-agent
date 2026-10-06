"""Bracket day-trade test: enter at a 5-min bar open, exit at take-profit, stop, or the close,
then re-enter at the next bar (as many trades as possible). Research only, never trades.

Intrabar order is unknown on 5-min bars, so each variant is run twice:
  'stop_first'  (if a bar touches both levels, assume the stop hit first)  -> realistic/pessimistic
  'target_first'                                                            -> optimistic bound
"""
import json, sys, itertools
from pathlib import Path
import numpy as np, pandas as pd

ROOT = Path(__file__).resolve().parents[2]
SWEEP = ROOT / "data" / "cache" / "sweep"
OUT = Path(__file__).resolve().parent

TPS = [0.0005, 0.001, 0.002, 0.003, 0.005, 0.01]
SLS = [0.00025, 0.0005, 0.001, 0.002, 0.003, 0.005]
RULES = ["long", "short", "follow_last_bar", "fade_last_bar"]
COSTS = [0.5e-4, 1e-4]   # per side (SPY/QQQ spread is ~0.2-0.5 bp; 1 bp also covers slippage)
IS_END = pd.Timestamp("2021-12-31")


def load(sym):
    z = np.load(SWEEP / f"{sym}_sessions.npz")
    return pd.DatetimeIndex(z["days"]), z["O"], z["H"], z["L"], z["C"]


def simulate(O, H, L, C, tp, sl, rule, order):
    n, S = O.shape
    pos = np.zeros(n)            # +1 long, -1 short, 0 flat
    entry = np.zeros(n)
    pnl = np.zeros(n)            # sum of gross log returns per day
    trades = np.zeros(n)
    wins = np.zeros(n)
    for k in range(S):
        o, h, l, c = O[:, k], H[:, k], L[:, k], C[:, k]
        flat = pos == 0
        if k < S - 1:
            if rule == "long":
                d = np.ones(n)
            elif rule == "short":
                d = -np.ones(n)
            else:
                prev = np.sign(C[:, k - 1] - O[:, k - 1]) if k > 0 else np.zeros(n)
                d = prev if rule == "follow_last_bar" else -prev
            go = flat & (d != 0)
            pos = np.where(go, d, pos)
            entry = np.where(go, o, entry)
            trades += go
        # check exits on this bar
        lp = pos == 1
        sp = pos == -1
        tp_px = np.where(lp, entry * (1 + tp), entry * (1 - tp))
        sl_px = np.where(lp, entry * (1 - sl), entry * (1 + sl))
        hit_t = (lp & (h >= tp_px)) | (sp & (l <= tp_px))
        hit_s = (lp & (l <= sl_px)) | (sp & (h >= sl_px))
        # gap through a level at this bar's open: fill at the open
        gap_s = (lp & (o <= sl_px)) | (sp & (o >= sl_px))
        gap_t = (lp & (o >= tp_px)) | (sp & (o <= tp_px))
        if order == "stop_first":
            ex_s = hit_s
            ex_t = hit_t & ~hit_s
        else:
            ex_t = hit_t
            ex_s = hit_s & ~hit_t
        px = np.where(ex_s, np.where(gap_s, o, sl_px), np.where(ex_t, np.where(gap_t, o, tp_px), np.nan))
        if k == S - 1:
            last = (pos != 0) & ~(ex_s | ex_t)
            px = np.where(last, c, px)
        ex = ~np.isnan(px)
        r = np.where(ex, pos * np.log(np.where(ex, px, 1) / np.where(ex, entry, 1)), 0.0)
        pnl += r
        wins += ex & (r > 0)
        pos = np.where(ex, 0, pos)
    return pnl, trades, wins


def stats(daily):
    if len(daily) == 0:
        return {}
    ann = daily.mean() * 252
    vol = daily.std() * np.sqrt(252)
    eq = np.cumsum(daily)
    dd = (eq - np.maximum.accumulate(eq)).min()
    return {"ann_ret": round(float(np.expm1(ann)), 4), "sharpe": round(float(ann / vol), 2) if vol > 0 else 0,
            "max_dd": round(float(np.expm1(dd)), 4)}


def main():
    rows = []
    for sym in ["SPY", "QQQ"]:
        days, O, H, L, C = load(sym)
        is_ = days <= IS_END
        for tp, sl, rule, order in itertools.product(TPS, SLS, RULES, ["stop_first", "target_first"]):
            pnl, tr, wn = simulate(O, H, L, C, tp, sl, rule, order)
            for cost in COSTS:
                net = pnl - 2 * cost * tr
                for per, m in [("in_sample", is_), ("out_of_sample", ~is_)]:
                    d = net[m]
                    rows.append({"symbol": sym, "tp": tp, "sl": sl, "rule": rule, "order": order,
                                 "cost_bp_side": cost * 1e4, "period": per,
                                 "trades_per_day": round(float(tr[m].mean()), 1),
                                 "win_rate": round(float(wn[m].sum() / max(tr[m].sum(), 1)), 3),
                                 "gross_per_trade_bp": round(float(pnl[m].sum() / max(tr[m].sum(), 1) * 1e4), 3),
                                 **stats(d)})
        print(sym, "done", file=sys.stderr)
    df = pd.DataFrame(rows)
    df.to_csv(OUT / "brackets.csv", index=False)
    # buy & hold intraday (open->close) and close->close benchmark
    print(len(df))


if __name__ == "__main__":
    main()
