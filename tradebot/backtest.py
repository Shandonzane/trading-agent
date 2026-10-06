"""Daily-bar backtester.

Honesty rules baked in:
- Signals use the bar's close; orders fill at the NEXT bar's open (no look-ahead).
- Every fill pays slippage (bps) and commission.
- Stops/targets fill at the stop price, or at the open if the price gapped through it.
- Results are always reported next to buy-and-hold of the same symbol.
- Approval uses the out-of-sample (later) part of the data only.
"""
from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

from .strategy import StrategySpec, evaluate

TRADING_DAYS = 252


@dataclass
class Trade:
    entry_date: str
    entry_price: float
    exit_date: str
    exit_price: float
    reason: str
    ret_pct: float
    days: int


@dataclass
class Result:
    strategy: str
    symbol: str
    start: str
    end: str
    metrics: dict
    benchmark: dict
    oos_metrics: dict
    oos_benchmark: dict
    verdict: str
    verdict_reasons: list
    trades: list = field(default_factory=list)
    equity: dict = field(default_factory=dict)
    bh_equity: dict = field(default_factory=dict)
    spec: dict = field(default_factory=dict)

    def to_dict(self):
        return asdict(self)


def metrics(equity: pd.Series, trades: list[Trade] | None = None) -> dict:
    equity = equity.dropna()
    if len(equity) < 2:
        return {}
    rets = equity.pct_change().dropna()
    years = len(equity) / TRADING_DAYS
    total = equity.iloc[-1] / equity.iloc[0] - 1
    cagr = (1 + total) ** (1 / years) - 1 if years > 0 and total > -1 else -1.0
    sharpe = float(rets.mean() / rets.std() * np.sqrt(TRADING_DAYS)) if rets.std() > 0 else 0.0
    dd = float((equity / equity.cummax() - 1).min())
    m = {"total_return": float(total), "cagr": float(cagr), "sharpe": round(sharpe, 3), "max_drawdown": dd}
    if trades is not None:
        wins = [t for t in trades if t.ret_pct > 0]
        m.update({
            "trades": len(trades),
            "win_rate": len(wins) / len(trades) if trades else 0.0,
            "avg_trade_pct": float(np.mean([t.ret_pct for t in trades])) if trades else 0.0,
            "exposure": float(sum(t.days for t in trades) / max(len(equity), 1)),
        })
    return m


def run(spec: StrategySpec, df: pd.DataFrame, symbol: str = "", slippage_bps: float = 5,
        commission_per_share: float = 0.0, cash: float = 100_000.0, oos_frac: float = 0.4,
        approval: dict | None = None) -> Result:
    df = df.sort_index()
    entry_sig = evaluate(df, spec.entry)
    exit_sig = evaluate(df, spec.exit)
    slip = slippage_bps / 10_000

    o, h, l, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    dates = df.index
    shares = 0.0
    entry_px = entry_i = None
    pending = None  # "buy" or "sell", filled at next open
    trades: list[Trade] = []
    eq = np.empty(len(df))

    def close_pos(i, px, reason):
        nonlocal shares, cash, entry_px, entry_i
        fill = px * (1 - slip)
        cash += shares * fill - commission_per_share * shares
        trades.append(Trade(str(dates[entry_i].date()), round(entry_px, 4), str(dates[i].date()),
                            round(fill, 4), reason, round((fill / entry_px - 1) * 100, 3), i - entry_i))
        shares, entry_px, entry_i = 0.0, None, None

    for i in range(len(df)):
        # 1) fill yesterday's decision at today's open
        if pending == "buy" and shares == 0:
            fill = o[i] * (1 + slip)
            shares = np.floor(cash / (fill + commission_per_share))
            if shares > 0:
                cash -= shares * fill + commission_per_share * shares
                entry_px, entry_i = fill, i
        elif pending == "sell" and shares > 0:
            close_pos(i, o[i], "exit_signal")
        pending = None

        # 2) intrabar stop / target / time exit
        if shares > 0:
            if spec.stop_loss_pct:
                stop = entry_px * (1 - spec.stop_loss_pct)
                if l[i] <= stop:
                    close_pos(i, min(o[i], stop) if i > entry_i else stop, "stop_loss")
            if shares > 0 and spec.take_profit_pct:
                tgt = entry_px * (1 + spec.take_profit_pct)
                if h[i] >= tgt:
                    close_pos(i, max(o[i], tgt) if i > entry_i else tgt, "take_profit")
            if shares > 0 and spec.max_hold_days and i - entry_i >= spec.max_hold_days:
                pending = "sell"

        # 3) decide at close for tomorrow's open
        if shares > 0 and exit_sig.iloc[i]:
            pending = "sell"
        elif shares == 0 and entry_sig.iloc[i] and i < len(df) - 1:
            pending = "buy"

        eq[i] = cash + shares * c[i]

    if shares > 0:  # mark open position closed at the last close for stats
        close_pos(len(df) - 1, c[-1], "end_of_data")
        eq[-1] = cash

    equity = pd.Series(eq, index=dates)
    bh = c / c[0] * eq[0]
    bh_equity = pd.Series(bh, index=dates)

    split = int(len(df) * (1 - oos_frac))
    split_date = dates[split]
    oos_trades = [t for t in trades if pd.Timestamp(t.entry_date) >= split_date]
    oos_m = metrics(equity.iloc[split:], oos_trades)
    oos_b = metrics(bh_equity.iloc[split:])
    verdict, reasons = judge(spec, oos_m, oos_b, approval or {})

    return Result(
        strategy=spec.name, symbol=symbol, start=str(dates[0].date()), end=str(dates[-1].date()),
        metrics=metrics(equity, trades), benchmark=metrics(bh_equity),
        oos_metrics=oos_m, oos_benchmark=oos_b, verdict=verdict, verdict_reasons=reasons,
        trades=[asdict(t) for t in trades],
        equity={str(k.date()): round(v, 2) for k, v in equity.items()},
        bh_equity={str(k.date()): round(v, 2) for k, v in bh_equity.items()},
        spec=spec.model_dump(exclude_none=True),
    )


def judge(spec: StrategySpec, m: dict, b: dict, a: dict) -> tuple[str, list[str]]:
    """PASS means 'allowed into paper trading', nothing more."""
    reasons = []
    if not spec.complete:
        reasons.append(f"Source strategy is incomplete (missing: {', '.join(spec.missing) or 'unspecified'})")
    if m.get("trades", 0) < a.get("min_oos_trades", 8):
        reasons.append(f"Too few out-of-sample trades ({m.get('trades', 0)}) to trust the result")
    if m.get("sharpe", 0) < a.get("min_oos_sharpe", 0.5):
        reasons.append(f"Out-of-sample Sharpe {m.get('sharpe', 0):.2f} below {a.get('min_oos_sharpe', 0.5)}")
    if m.get("max_drawdown", -1) < -a.get("max_oos_drawdown", 0.25):
        reasons.append(f"Out-of-sample drawdown {m['max_drawdown']:.0%} too deep")
    if a.get("must_beat_buy_hold_sharpe", True) and m.get("sharpe", 0) <= b.get("sharpe", 0):
        reasons.append(f"Doesn't beat buy-and-hold on risk-adjusted return (Sharpe {m.get('sharpe', 0):.2f} vs {b.get('sharpe', 0):.2f})")
    return ("PASS" if not reasons else "FAIL"), reasons
