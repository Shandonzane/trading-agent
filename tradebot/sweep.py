"""Fast strategy-variant sweeps on 5-minute bars: times, weekdays, calls vs puts, combined filters.

One variant = WHEN to enter (5-min slot) x HOW LONG to hold x WHICH WAY (long/calls or short/puts,
or a rule that picks the side each day) x WHICH DAYS (weekday and a filter such as "the approved
20-day breakout is in a position" AND "VIX below 15") x WHAT to trade (shares, 0DTE or weekly ATM
options).

Why it's fast: every (entry, hold, direction, instrument) combination becomes one column of daily
returns, every (filter, weekday, year) becomes one 0/1 row mask, and the per-year statistics of
millions of variants fall out of a handful of matrix multiplications (BLAS), not Python loops.

Honesty rules:
- Filters only use information known before the entry: daily indicators as of the PRIOR close,
  today's gap as of 9:30 (entries start at 9:35), the morning move up to the bar before entry.
- Entry fills at the open of the entry bar, exit at the close of the exit bar, slippage both ways.
- Options are MODELED (Black-Scholes, ATM strike, IV = prior-day VIX for SPY / VXN for QQQ, held
  constant through the trade, cost haircut per side). No real option quotes are used. Daily SPY
  0DTE expiries only exist since 2022 (Mon/Wed/Fri before that), so earlier 0DTE results are
  hypothetical.
- Selection uses in-sample years only (2016-2021); out-of-sample (2022 on) is reported, never used
  to pick. A walk-forward re-picks the best variant every year from data before that year.
- Results are compared with buy-and-hold of the same symbol over the same days.
"""
import heapq
import json
import math
import time as _time
from dataclasses import dataclass
from itertools import combinations
from statistics import NormalDist

import numpy as np
import pandas as pd

from .config import ROOT
from .indicators import rsi, sma
from .strategy import evaluate, load_spec

SLOTS = 78                      # 5-minute bars from 9:30 to 15:55
CACHE = ROOT / "data" / "cache"
SWEEP_CACHE = CACHE / "sweep"
VOL_INDEX = {"QQQ": "^VXN", "SPY": "^VIX"}
IS_LAST_YEAR = 2021             # in-sample through 2021, out-of-sample from 2022
YEAR_DAYS = 252
INTRADAY_SHARE = 0.8            # share of a day's variance during market hours (rest is overnight)

WEEKDAYS = ["any", "Mon", "Tue", "Wed", "Thu", "Fri"]
DIRECTIONS = ["long", "short", "follow_morning", "fade_morning", "follow_gap", "fade_gap",
              "follow_prev_day", "trend_200"]
INSTRUMENTS = ["shares", "0dte", "weekly"]
HOLDS = [15, 30, 60, 120, "close"]
STRATEGY_FILES = ["approved/20_day_breakout.json", "rsi2_mean_reversion.json", "sma_trend_50_200.json"]


def slot_time(k: int) -> str:
    m = 570 + 5 * k
    return f"{m // 60:02d}:{m % 60:02d}"


def time_slot(hhmm: str) -> int:
    h, m = map(int, hhmm.split(":"))
    return (h * 60 + m - 570) // 5


# ----------------------------------------------------------------------------- data

def load_sessions(symbol: str) -> dict:
    """Regular-hours 5-min bars as (days x 78) arrays. Half days are dropped, gaps forward-filled."""
    symbol = symbol.upper()
    src = CACHE / "intraday" / f"{symbol}_5min.csv"
    npz = SWEEP_CACHE / f"{symbol}_sessions.npz"
    if npz.exists() and npz.stat().st_mtime >= src.stat().st_mtime:
        z = np.load(npz)
        return {"days": pd.DatetimeIndex(z["days"]), **{k: z[k] for k in "OHLC"}}
    df = pd.read_csv(src, index_col=0, parse_dates=True)
    mins = df.index.hour * 60 + df.index.minute
    df = df[(mins >= 570) & (mins < 960)]
    day = df.index.normalize()
    counts = pd.Series(1, index=day).groupby(level=0).size()
    days = counts.index[counts >= 72]
    df = df[day.isin(days)]
    di = pd.Index(days).get_indexer(df.index.normalize())
    si = ((df.index.hour * 60 + df.index.minute - 570) // 5).to_numpy()
    out = {"days": pd.DatetimeIndex(days)}
    for k, col in zip("OHLC", ["open", "high", "low", "close"]):
        a = np.full((len(days), SLOTS), np.nan)
        a[di, si] = df[col].to_numpy(float)
        out[k] = a
    # Missing bars: flat bar at the previous close.
    c = pd.DataFrame(out["C"]).ffill(axis=1).bfill(axis=1).to_numpy()
    prev_c = np.concatenate([c[:, :1], c[:, :-1]], axis=1)
    for k in "OHL":
        out[k] = np.where(np.isnan(out[k]), prev_c, out[k])
    out["C"] = c
    SWEEP_CACHE.mkdir(parents=True, exist_ok=True)
    np.savez(npz, days=out["days"].to_numpy(), **{k: out[k] for k in "OHLC"})
    return out


def get_vol_index(symbol: str) -> pd.Series:
    path = SWEEP_CACHE / f"{VOL_INDEX[symbol].strip('^')}.csv"
    if path.exists() and _time.time() - path.stat().st_mtime < 86400:
        return pd.read_csv(path, index_col=0, parse_dates=True).iloc[:, 0]
    import yfinance as yf

    df = yf.download(VOL_INDEX[symbol], start="2014-01-01", progress=False, auto_adjust=False)
    s = df["Close"]
    s = s.iloc[:, 0] if isinstance(s, pd.DataFrame) else s
    s.index = pd.to_datetime(s.index).tz_localize(None)
    SWEEP_CACHE.mkdir(parents=True, exist_ok=True)
    s.rename("close").to_csv(path)
    return s


def position_state(daily: pd.DataFrame, spec) -> pd.Series:
    """True on days the strategy is holding at the close (entry/exit/max-hold rules; stops ignored)."""
    ent = evaluate(daily, spec.entry).to_numpy()
    ex = evaluate(daily, spec.exit).to_numpy()
    st = np.zeros(len(daily), bool)
    inpos, held = False, 0
    for i in range(len(daily)):
        if inpos:
            held += 1
            if ex[i] or (spec.max_hold_days and held >= spec.max_hold_days):
                inpos = False
        elif ent[i]:
            inpos, held = True, 0
        st[i] = inpos
    return pd.Series(st, index=daily.index)


# ----------------------------------------------------------------------------- context

@dataclass
class Window:
    label: str
    e: int          # entry slot (fill at its open); 78 = overnight (enter at the 16:00 close)
    x: int          # exit slot (fill at its close); -1 = next day's 9:30 open


def build_windows(entry_step_min: int = 5) -> list[Window]:
    seen, out = set(), []
    for e in range(1, SLOTS - 1, max(1, entry_step_min // 5)):
        for h in HOLDS:
            x = SLOTS - 1 if h == "close" else min(SLOTS - 1, e + h // 5 - 1)
            if (e, x) in seen:
                continue
            seen.add((e, x))
            hl = "to close" if x == SLOTS - 1 else f"{(x - e + 1) * 5}m"
            out.append(Window(f"{slot_time(e)} {hl}", e, x))
    out.append(Window("overnight 16:00->9:30", SLOTS, -1))
    return out


class Context:
    """Everything one symbol's sweep needs: price matrices, filters, directions, cost settings."""

    def __init__(self, symbol: str, slippage_bps: float = 1.0, opt_cost: float = 0.015,
                 opt_budget: float = 0.02, pairs: bool = True):
        self.symbol = symbol.upper()
        s = load_sessions(self.symbol)
        self.days, self.O, self.H, self.L, self.C = s["days"], s["O"], s["H"], s["L"], s["C"]
        self.D = len(self.days)
        self.slip = slippage_bps / 1e4
        self.opt_cost = opt_cost
        self.opt_budget = opt_budget
        self.years = np.array(self.days.year)
        self.year_list = sorted(set(self.years))
        self.close = self.C[:, -1]
        self.prev_close = np.concatenate([[np.nan], self.close[:-1]])
        self.next_open = np.concatenate([self.O[1:, 0], [np.nan]])
        vol = get_vol_index(self.symbol).shift(1)  # prior close
        self.iv = (vol.reindex(self.days).ffill().bfill().to_numpy() / 100.0)
        self._filters(pairs)

    # -- filters: name -> bool[D], grouped so pairs only combine different groups
    def _filters(self, pairs: bool):
        daily = pd.read_csv(CACHE / f"{self.symbol}.csv", index_col=0, parse_dates=True).sort_index()
        prior = lambda s: s.shift(1).reindex(self.days).fillna(False).to_numpy(bool)  # noqa: E731
        vix = self.iv * 100
        gap = self.O[:, 0] / self.prev_close - 1
        prev_ret = np.concatenate([[np.nan], (self.close / self.O[:, 0] - 1)[:-1]])
        r2 = rsi(daily["close"], 2)
        groups = {
            "strategy": {},
            "trend": {"above200": prior(daily["close"] > sma(daily["close"], 200)),
                      "below200": prior(daily["close"] <= sma(daily["close"], 200))},
            "rsi": {"rsi2<10": prior(r2 < 10), "rsi2>90": prior(r2 > 90)},
            "vix": {"vix<15": vix < 15, "vix15-25": (vix >= 15) & (vix < 25), "vix>25": vix >= 25},
            "gap": {"gap_up": np.nan_to_num(gap) > 0.0025, "gap_down": np.nan_to_num(gap) < -0.0025},
            "prev": {"prev_up": np.nan_to_num(prev_ret) > 0, "prev_down": np.nan_to_num(prev_ret) < 0},
        }
        for f in STRATEGY_FILES:
            spec = load_spec(ROOT / "strategies" / f)
            groups["strategy"]["in:" + spec.name] = prior(position_state(daily, spec))
        self.filters = {"all": np.ones(self.D, bool)}
        for g in groups.values():
            self.filters.update(g)
        if pairs:
            names = list(groups)
            for ga, gb in combinations(names, 2):
                for (na, a), (nb, b) in ((p, q) for p in groups[ga].items() for q in groups[gb].items()):
                    self.filters[f"{na} & {nb}"] = a & b
        self.filter_names = list(self.filters)
        self.wd = np.array(self.days.weekday)

    def filter_mask(self, name: str) -> np.ndarray:
        if name in self.filters:
            return self.filters[name]
        parts = [p.strip() for p in name.split("&")]
        m = np.ones(self.D, bool)
        for p in parts:
            m &= self.filters[p]
        return m

    # -- per-day side for a direction rule at a window: +1 long/call, -1 short/put, 0 skip
    def direction(self, rule: str, w: Window) -> np.ndarray:
        if rule == "long":
            return np.ones(self.D, np.int8)
        if rule == "short":
            return -np.ones(self.D, np.int8)
        if rule in ("follow_morning", "fade_morning"):
            ref = self.close if w.e >= SLOTS else self.C[:, w.e - 1]
            d = np.sign(ref - self.O[:, 0])
        elif rule in ("follow_gap", "fade_gap"):
            d = np.sign(np.nan_to_num(self.O[:, 0] / self.prev_close - 1))
        elif rule == "follow_prev_day":
            d = np.sign(np.nan_to_num(np.concatenate([[0], (self.close - self.O[:, 0])[:-1]])))
        elif rule == "trend_200":
            d = np.where(self.filters["above200"], 1, -1)
        else:
            raise ValueError(rule)
        if rule.startswith("fade"):
            d = -d
        return d.astype(np.int8)

    # -- raw returns of one window for long and short, per instrument
    def window_returns(self, w: Window) -> dict:
        if w.e >= SLOTS:
            pe, px = self.close, self.next_open
        else:
            pe, px = self.O[:, w.e], self.C[:, w.x]
        s = self.slip
        out = {"shares": (px * (1 - s) / (pe * (1 + s)) - 1, (pe * (1 - s) - px * (1 + s)) / (pe * (1 - s)))}
        day = 1.0 / YEAR_DAYS
        if w.e >= SLOTS:   # overnight: "0dte" means the option expiring next day's close
            t0 = {"0dte": day, "weekly": 5 * day}
            t1 = {k: v - (1 - INTRADAY_SHARE) * day for k, v in t0.items()}
        else:
            left_e = (390 - 5 * w.e) / 390 * INTRADAY_SHARE * day
            left_x = (390 - 5 * (w.x + 1)) / 390 * INTRADAY_SHARE * day
            t0 = {"0dte": left_e, "weekly": left_e + 4 * day}
            t1 = {"0dte": left_x, "weekly": left_x + 4 * day}
        m = px / pe   # moneyness at exit, strike = entry price
        c = self.opt_cost
        for inst in ("0dte", "weekly"):
            ce, pe_ = bs_unit(1.0, t0[inst], self.iv)
            cx, px_ = bs_unit(m, t1[inst], self.iv)
            out[inst] = (cx * (1 - c) / (ce * (1 + c)) - 1, px_ * (1 - c) / (pe_ * (1 + c)) - 1)
        return out

    def column(self, wr: dict, d: np.ndarray, inst: str) -> np.ndarray:
        lo, sh = wr[inst]
        r = np.where(d > 0, lo, np.where(d < 0, sh, 0.0))
        r = np.nan_to_num(r, nan=0.0, posinf=0.0, neginf=0.0)
        return r * (1.0 if inst == "shares" else self.opt_budget)

    def benchmark(self) -> np.ndarray:
        return np.nan_to_num(self.close / self.prev_close - 1)


def _ncdf(x):
    # Abramowitz-Stegun 7.1.26 erf approximation (|error| < 1.5e-7), vectorized.
    z = np.abs(x) / math.sqrt(2)
    t = 1 / (1 + 0.3275911 * z)
    y = 1 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * np.exp(-z * z)
    return 0.5 * (1 + np.sign(x) * y)


def bs_unit(s, t, vol):
    """Call and put value with strike 1, zero rates. s = spot/strike, t in years."""
    s = np.asarray(s, float)
    t = np.broadcast_to(np.asarray(t, float), s.shape)
    sd = vol * np.sqrt(np.maximum(t, 0))
    live = sd > 1e-9
    sd_ = np.where(live, sd, 1.0)
    d1 = (np.log(np.maximum(s, 1e-12)) + 0.5 * sd_ ** 2) / sd_
    call = np.where(live, s * _ncdf(d1) - _ncdf(d1 - sd_), np.maximum(s - 1, 0))
    put = call - s + 1
    return call, np.maximum(put, 0)


# ----------------------------------------------------------------------------- stats

def sharpe(s1, s2, n):
    n = np.maximum(n, 1)
    mu = s1 / n
    var = np.maximum(s2 / n - mu ** 2, 1e-18)
    return mu / np.sqrt(var) * math.sqrt(YEAR_DAYS)


def series_metrics(r: np.ndarray) -> dict:
    eq = np.cumprod(1 + r)
    yrs = len(r) / YEAR_DAYS
    sd = r.std()
    return {"sharpe": round(float(r.mean() / sd * math.sqrt(YEAR_DAYS)) if sd > 0 else 0.0, 3),
            "cagr": round(float(eq[-1] ** (1 / yrs) - 1), 4) if yrs > 0 and eq[-1] > 0 else -1.0,
            "max_drawdown": round(float((eq / np.maximum.accumulate(eq) - 1).min()), 4)}


def noise_bar(n_trials: int, years: float) -> float:
    """Expected best annualized Sharpe among n_trials strategies with NO edge (independent trials).
    Variants here are correlated, so the real bar is lower, but it shows the scale of luck."""
    if n_trials < 2:
        return 0.0
    g = 0.5772156649
    nd = NormalDist()
    z = (1 - g) * nd.inv_cdf(1 - 1 / n_trials) + g * nd.inv_cdf(1 - 1 / (n_trials * math.e))
    return z / math.sqrt(years)


# ----------------------------------------------------------------------------- sweep

@dataclass
class SweepConfig:
    entry_step_min: int = 5
    min_is_trades: int = 60
    min_oos_trades: int = 20
    chunk: int = 1536
    top: int = 25
    wf_first_test_year: int = 2019
    wf_pool: int = 10


def run_sweep(ctx: Context, cfg: SweepConfig = SweepConfig(), log=print) -> dict:
    t_start = _time.time()
    windows = build_windows(cfg.entry_step_min)
    cols = [(wi, dr, inst) for wi in range(len(windows)) for dr in DIRECTIONS for inst in INSTRUMENTS]
    masks = [(f, wd) for f in ctx.filter_names for wd in range(len(WEEKDAYS))]
    Y = ctx.year_list
    ny = len(Y)
    days_per_year = np.array([(ctx.years == y).sum() for y in Y], float)
    is_y = np.array([y <= IS_LAST_YEAR for y in Y])
    n_var = len(masks) * len(cols)
    log(f"{ctx.symbol}: {len(windows)} time windows x {len(DIRECTIONS)} directions x {len(INSTRUMENTS)} "
        f"instruments x {len(ctx.filter_names)} filters x {len(WEEKDAYS)} weekday sets = {n_var:,} variants")

    # Row masks: (mask, year) x day, 0/1 float32.
    A = np.zeros((len(masks), ny, ctx.D), np.float32)
    for mi, (f, wd) in enumerate(masks):
        m = ctx.filters[f] & (True if wd == 0 else ctx.wd == wd - 1)
        for yi, y in enumerate(Y):
            A[mi, yi] = m & (ctx.years == y)
    A = A.reshape(len(masks) * ny, ctx.D)

    # Benchmark per year.
    bh = ctx.benchmark()
    yrs_is, yrs_oos = days_per_year[is_y].sum() / YEAR_DAYS, days_per_year[~is_y].sum() / YEAR_DAYS
    bench = {"in_sample": series_metrics(bh[np.isin(ctx.years, np.array(Y)[is_y])]),
             "out_of_sample": series_metrics(bh[np.isin(ctx.years, np.array(Y)[~is_y])])}

    # Per-variant summaries (flattened mask-major: idx = mask * ncols + col).
    keys = ["is_sh", "oos_sh", "is_cagr", "oos_cagr", "is_n", "oos_n", "is_win", "oos_win", "is_pos_years"]
    summ = {k: np.zeros(n_var, np.float32) for k in keys}
    wf_test_years = [y for y in Y if y >= cfg.wf_first_test_year]
    wf_heaps = {y: [] for y in wf_test_years}

    wr_cache: dict[int, dict] = {}
    dir_cache: dict[tuple, np.ndarray] = {}
    for c0 in range(0, len(cols), cfg.chunk):
        cc = cols[c0:c0 + cfg.chunk]
        R = np.empty((ctx.D, len(cc)), np.float32)
        for j, (wi, dr, inst) in enumerate(cc):
            if wi not in wr_cache:
                wr_cache.clear()
                wr_cache[wi] = ctx.window_returns(windows[wi])
            key = (wi, dr)
            if key not in dir_cache:
                if len(dir_cache) > 64:
                    dir_cache.clear()
                dir_cache[key] = ctx.direction(dr, windows[wi])
            R[:, j] = ctx.column(wr_cache[wi], dir_cache[key], inst)
        T = (R != 0).astype(np.float32)
        shp = (len(masks), ny, len(cc))
        N = (A @ T).reshape(shp)
        S1 = (A @ R).reshape(shp)
        S2 = (A @ (R * R)).reshape(shp)
        Wn = (A @ (R > 0).astype(np.float32)).reshape(shp)
        Lg = (A @ np.log1p(np.maximum(R, -0.999))).reshape(shp)
        del R, T

        def agg(sel):
            n, s1, s2, w, lg = (x[:, sel].sum(1) for x in (N, S1, S2, Wn, Lg))
            nd = days_per_year[sel].sum()
            return n, sharpe(s1, s2, nd), np.expm1(lg / (nd / YEAR_DAYS)), w / np.maximum(n, 1)

        idx = (np.arange(len(masks))[:, None] * len(cols) + (c0 + np.arange(len(cc)))[None, :]).ravel()
        n_i, sh_i, cg_i, w_i = agg(is_y)
        n_o, sh_o, cg_o, w_o = agg(~is_y)
        pos_years = (S1[:, is_y] > 0).sum(1) / is_y.sum()
        for k, v in zip(keys, (sh_i, sh_o, cg_i, cg_o, n_i, n_o, w_i, w_o, pos_years)):
            summ[k][idx] = v.ravel()

        # Walk-forward: for each test year, best variants on data strictly before it (expanding).
        cN, cS1, cS2 = (np.cumsum(x, axis=1) for x in (N, S1, S2))
        cdays = np.cumsum(days_per_year)
        for y in wf_test_years:
            yi = Y.index(y)
            score = sharpe(cS1[:, yi - 1], cS2[:, yi - 1], cdays[yi - 1])
            score = np.where(cN[:, yi - 1] >= cfg.min_is_trades, score, -np.inf).ravel()
            best = np.argpartition(-score, cfg.wf_pool)[:cfg.wf_pool]
            for b in best:
                if np.isfinite(score[b]):
                    gi = int((b // len(cc)) * len(cols) + c0 + b % len(cc))
                    item = (float(score[b]), gi)
                    h = wf_heaps[y]
                    (heapq.heappush if len(h) < cfg.wf_pool else heapq.heappushpop)(h, item)
        log(f"  {min(c0 + cfg.chunk, len(cols))}/{len(cols)} columns  ({_time.time() - t_start:.1f}s)")

    elapsed = _time.time() - t_start
    decode = lambda gi: describe(windows, cols, masks, gi)  # noqa: E731

    valid = (summ["is_n"] >= cfg.min_is_trades) & (summ["oos_n"] >= cfg.min_oos_trades)
    vidx = np.flatnonzero(valid)
    order = vidx[np.argsort(-summ["is_sh"][vidx])]
    top = []
    for gi in order[:cfg.top]:
        d = decode(gi)
        d.update({k: round(float(summ[k][gi]), 4) for k in keys})
        r = variant_returns(ctx, windows, d)
        d["oos_max_drawdown"] = series_metrics(r[ctx.years > IS_LAST_YEAR])["max_drawdown"]
        top.append(d)

    # How well does in-sample rank predict out-of-sample? (all valid variants)
    # Among the top 10% in-sample, does a better in-sample rank still mean a better out-of-sample one?
    dec = order[:max(3, len(order) // 10)]
    rk_is, rk_oos = pd.Series(summ["is_sh"][dec]).rank(), pd.Series(summ["oos_sh"][dec]).rank()
    spearman = float(np.corrcoef(rk_is, rk_oos)[0, 1]) if len(dec) > 2 else 0.0
    pos_is = vidx[summ["is_sh"][vidx] > 0]
    b_oos = bench["out_of_sample"]["sharpe"]
    top100 = order[:100]

    # Walk-forward stitched results: best-1 and equal-weight best-N, re-picked every year.
    wf_rows, wf1, wfn = [], [], []
    for y in wf_test_years:
        picks = sorted(wf_heaps[y], reverse=True)
        ym = ctx.years == y
        series = [variant_returns(ctx, windows, decode(gi))[ym] for _, gi in picks]
        wf1.append(series[0])
        wfn.append(np.mean(series, axis=0))
        d0 = decode(picks[0][1])
        wf_rows.append({"year": y, "pick": d0["label"], "pick_train_sharpe": round(picks[0][0], 3),
                        "pick_year_sharpe": series_metrics(series[0])["sharpe"],
                        "pick_year_return": round(float(np.prod(1 + series[0]) - 1), 4),
                        "top_n_year_return": round(float(np.prod(1 + wfn[-1]) - 1), 4),
                        "buy_hold_year_return": round(float(np.prod(1 + bh[ym]) - 1), 4)})
    wf_mask = ctx.years >= cfg.wf_first_test_year

    marg = marginals(summ, windows, cols, masks, len(cols), b_oos)

    return {
        "symbol": ctx.symbol,
        "settings": {"slippage_bps": ctx.slip * 1e4, "option_cost_per_side": ctx.opt_cost,
                     "option_premium_per_trade": ctx.opt_budget, "in_sample": f"{Y[0]}-{IS_LAST_YEAR}",
                     "out_of_sample": f"{IS_LAST_YEAR + 1}-{Y[-1]}", "min_is_trades": cfg.min_is_trades,
                     "min_oos_trades": cfg.min_oos_trades, "days": int(ctx.D)},
        "speed": {"variants": int(n_var), "seconds": round(elapsed, 1),
                  "variants_per_second": int(n_var / max(elapsed, 1e-9))},
        "benchmark": bench,
        "overfitting": {
            "valid_variants": int(len(vidx)),
            "noise_bar_is_sharpe": round(noise_bar(len(vidx), yrs_is), 2),
            "best_is_sharpe": round(float(summ["is_sh"][order[0]]), 3) if len(order) else None,
            "top_decile_is_oos_rank_correlation": round(spearman, 3),
            "positive_is_variants": int(len(pos_is)),
            "positive_is_still_positive_oos": round(float((summ["oos_sh"][pos_is] > 0).mean()), 3) if len(pos_is) else None,
            "base_rate_positive_oos": round(float((summ["oos_sh"][vidx] > 0).mean()), 3),
            "top100_median_oos_sharpe": round(float(np.median(summ["oos_sh"][top100])), 3) if len(top100) else None,
            "top100_share_beating_buy_hold_oos": round(float((summ["oos_sh"][top100] > b_oos).mean()), 3) if len(top100) else None,
            "all_share_beating_buy_hold_oos": round(float((summ["oos_sh"][vidx] > b_oos).mean()), 4),
        },
        "top_in_sample": top,
        "walk_forward": {"years": wf_rows,
                         "best_1": series_metrics(np.concatenate(wf1)),
                         f"top_{cfg.wf_pool}_equal_weight": series_metrics(np.concatenate(wfn)),
                         "buy_hold": series_metrics(bh[wf_mask])},
        "marginals": marg,
    }


def describe(windows, cols, masks, gi: int) -> dict:
    mi, ci = divmod(int(gi), len(cols))
    wi, dr, inst = cols[ci]
    f, wd = masks[mi]
    w = windows[wi]
    label = f"{w.label} | {dr} | {inst} | {WEEKDAYS[wd]} | {f}"
    return {"label": label, "window": w.label, "entry_slot": w.e, "exit_slot": w.x, "direction": dr,
            "instrument": inst, "weekday": WEEKDAYS[wd], "filter": f}


def variant_returns(ctx: Context, windows, d: dict) -> np.ndarray:
    w = next(x for x in windows if x.e == d["entry_slot"] and x.x == d["exit_slot"])
    m = ctx.filter_mask(d["filter"])
    if d["weekday"] != "any":
        m = m & (ctx.wd == WEEKDAYS.index(d["weekday"]) - 1)
    return np.where(m, ctx.column(ctx.window_returns(w), ctx.direction(d["direction"], w), d["instrument"]), 0.0)


def marginals(summ, windows, cols, masks, ncols, b_oos) -> dict:
    """Median in- and out-of-sample Sharpe by each dimension on its own, per instrument, filter = all.
    Averages over many variants are far less prone to luck than the single best one."""
    base = [mi for mi, (f, _) in enumerate(masks) if f == "all"]
    rows = []
    out = {}
    for mi in base:
        for ci, (wi, dr, inst) in enumerate(cols):
            gi = mi * ncols + ci
            if summ["is_n"][gi] < 30:
                continue
            w = windows[wi]
            m = 570 + 5 * w.e
            rows.append({"entry": "overnight" if w.e >= SLOTS else f"{m // 60:02d}:{m % 60 // 30 * 30:02d}",
                         "hold": "overnight" if w.e >= SLOTS else w.label.split(" ", 1)[1],
                         "direction": dr, "instrument": inst, "weekday": WEEKDAYS[masks[mi][1]],
                         "is_sh": float(summ["is_sh"][gi]), "oos_sh": float(summ["oos_sh"][gi])})
    df = pd.DataFrame(rows)
    for inst, part in df.groupby("instrument"):
        out[inst] = {}
        for dim in ["entry", "hold", "direction", "weekday"]:
            g = part.groupby(dim).agg(is_sharpe=("is_sh", "median"), oos_sharpe=("oos_sh", "median"),
                                      variants=("oos_sh", "size"))
            out[inst][dim] = g.round(3).reset_index().to_dict("records")
    # Filters: median over everything, any weekday.
    rows = []
    fidx = {}
    for mi, (f, wd) in enumerate(masks):
        if wd == 0:
            fidx[f] = mi
    shares_cols = np.array([inst == "shares" for _, _, inst in cols])
    for f, mi in fidx.items():
        sl = slice(mi * ncols, (mi + 1) * ncols)
        ok = (summ["is_n"][sl] >= 30) & shares_cols
        if ok.sum() == 0:
            continue
        rows.append({"filter": f, "is_sharpe": round(float(np.median(summ["is_sh"][sl][ok])), 3),
                     "oos_sharpe": round(float(np.median(summ["oos_sh"][sl][ok])), 3), "variants": int(ok.sum())})
    out["filter_shares"] = sorted(rows, key=lambda r: -r["is_sharpe"])
    return out


# ----------------------------------------------------------------------------- single test

def test_variant(ctx: Context, entry: str, hold, direction: str, instrument: str = "shares",
                 weekday: str = "any", filt: str = "all") -> dict:
    """Detailed result for one hand-written variant (the 'tweak' loop)."""
    if entry == "overnight":
        w = Window("overnight 16:00->9:30", SLOTS, -1)
    else:
        e = time_slot(entry)
        x = SLOTS - 1 if str(hold) == "close" else min(SLOTS - 1, e + int(hold) // 5 - 1)
        w = Window(f"{entry} {hold}", e, x)
    d = {"entry_slot": w.e, "exit_slot": w.x, "direction": direction, "instrument": instrument,
         "weekday": weekday, "filter": filt}
    r = variant_returns(ctx, [w], d)
    bh = ctx.benchmark()
    out = {"variant": f"{w.label} | {direction} | {instrument} | {weekday} | {filt}", "years": []}
    for y in ctx.year_list:
        ym = ctx.years == y
        out["years"].append({"year": int(y), "trades": int((r[ym] != 0).sum()),
                             "return": round(float(np.prod(1 + r[ym]) - 1), 4),
                             "buy_hold": round(float(np.prod(1 + bh[ym]) - 1), 4)})
    for name, m in (("in_sample", ctx.years <= IS_LAST_YEAR), ("out_of_sample", ctx.years > IS_LAST_YEAR)):
        rr = r[m]
        t = rr[rr != 0]
        out[name] = {**series_metrics(rr), "trades": int(len(t)),
                     "win_rate": round(float((t > 0).mean()), 3) if len(t) else 0.0,
                     "avg_trade": round(float(t.mean()), 5) if len(t) else 0.0,
                     "buy_hold": series_metrics(bh[m])}
    return out


def save(result: dict, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2, default=float))
