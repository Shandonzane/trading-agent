"""Poster credibility: grade every tagged call against what the price did next, then test whether
a poster's past grade predicts their future calls. Paper research only; places no orders.

A "call" is a StockTwits message tagged Bullish or Bearish that names 1-2 stocks. It is graded
as if bought (or shorted) at the next market open after the post and held 5 trading days,
minus what SPY did over the same days. A poster's score is the average of their graded calls,
shrunk toward zero so a lucky 3-for-3 doesn't outrank a steady 60-for-100.

  python -m scout.credibility            # build calls table + walk-forward test, write report
"""
import json
from datetime import time as dtime
from pathlib import Path

import numpy as np
import pandas as pd

from . import prices

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "scout"
OUT = ROOT / "results" / "scout"
HOLD = 5          # trading days held
SHRINK = 20       # pseudo-calls at zero edge added to every poster's record
MIN_PRIOR = 10    # graded calls needed before a poster's score counts
SKIP = {"SPY", "QQQ", "IWM", "DIA", "VIX", "UVXY", "SQQQ", "TQQQ", "SPXL", "SOXL", "SOXS", "TSLL", "NVDL"}


def load_messages() -> pd.DataFrame:
    rows = {}
    for line in (DATA / "messages.jsonl").open():
        m = json.loads(line)
        rows[m["id"]] = m
    df = pd.DataFrame(rows.values())
    df["t"] = pd.to_datetime(df["t"], utc=True)
    return df


def build_calls(msgs: pd.DataFrame, min_mentions: int = 3) -> pd.DataFrame:
    c = msgs[msgs["sent"].notna() & (msgs["official"] != True)].copy()
    c["syms"] = c["syms"].apply(lambda s: [x for x in s if "." not in x and x not in SKIP])
    c = c[c["syms"].apply(len).between(1, 2)].explode("syms").rename(columns={"syms": "sym"})
    c["dir"] = np.where(c["sent"] == "Bullish", 1, -1)
    keep = c["sym"].value_counts()
    c = c[c["sym"].isin(keep[keep >= min_mentions].index)]

    spy = prices.daily("SPY")
    days = spy.index
    et = c["t"].dt.tz_convert("America/New_York")
    d = et.dt.tz_localize(None).dt.normalize()
    before_open = et.dt.time < dtime(9, 30)
    # first session whose open is after the post
    pos = np.searchsorted(days.values, d.values, side="left")
    is_day = (pos < len(days)) & (days.values[np.minimum(pos, len(days) - 1)] == d.values)
    entry_pos = np.where(is_day & before_open.values, pos, np.where(is_day, pos + 1, pos))
    c["entry_pos"] = entry_pos
    c = c[c["entry_pos"] + HOLD - 1 < len(days)].copy()
    c["entry_day"] = days[c["entry_pos"]]
    c["exit_day"] = days[c["entry_pos"] + HOLD - 1]

    out = []
    for sym, g in c.groupby("sym"):
        px = prices.daily(sym)
        if px is None or len(px) < 30:
            continue
        px = px.reindex(days).ffill()
        o, cl = px["open"].values, px["close"].values
        e, x = g["entry_pos"].values, g["entry_pos"].values + HOLD - 1
        r = cl[x] / o[e] - 1
        rs = spy["close"].values[x] / spy["open"].values[e] - 1
        pre = o[e] / cl[np.maximum(e - 6, 0)] - 1  # move in the ~5 sessions before entry
        g = g.assign(ret=r, spy=rs, excess=r - rs, pre=pre)
        out.append(g)
    calls = pd.concat(out).dropna(subset=["excess"])
    calls["excess"] = calls["excess"].clip(-0.5, 0.5)
    calls["edge"] = calls["dir"] * calls["excess"]
    # one call per poster per ticker per direction per holding window: spammers don't get extra votes
    calls = calls.sort_values("t")
    calls["bucket"] = calls["entry_pos"] // HOLD
    calls = calls.drop_duplicates(["uid", "sym", "dir", "bucket"])
    return calls[["id", "t", "uid", "user", "followers", "sym", "dir", "entry_day", "exit_day",
                  "ret", "spy", "excess", "edge", "pre", "likes", "body"]].reset_index(drop=True)


def poster_scores(calls: pd.DataFrame, asof: pd.Timestamp | None = None) -> pd.DataFrame:
    """Score posters using only calls already graded (exit before `asof`)."""
    c = calls if asof is None else calls[calls["exit_day"] < asof]
    g = c.groupby("uid")
    s = pd.DataFrame({"user": g["user"].last(), "n": g.size(), "edge_sum": g["edge"].sum(),
                      "hit": g["edge"].apply(lambda e: (e > 0).mean()), "followers": g["followers"].last(),
                      "bull_share": g["dir"].apply(lambda d: (d > 0).mean())})
    s["score"] = s["edge_sum"] / (s["n"] + SHRINK)
    return s.sort_values("score", ascending=False)


def walk_forward(calls: pd.DataFrame, start: str) -> pd.DataFrame:
    """For each week from `start`, attach each poster's score as known before that week."""
    rows = []
    weeks = pd.date_range(start, calls["entry_day"].max(), freq="W-MON")
    for w0, w1 in zip(weeks[:-1], weeks[1:]):
        wk = calls[(calls["entry_day"] >= w0) & (calls["entry_day"] < w1)]
        if wk.empty:
            continue
        sc = poster_scores(calls, asof=w0)
        wk = wk.join(sc[["n", "score", "hit"]].rename(columns={"n": "prior_n", "score": "prior_score",
                                                                 "hit": "prior_hit"}), on="uid")
        rows.append(wk.assign(week=w0))
    return pd.concat(rows)


def _weekly_t(df: pd.DataFrame) -> tuple[float, float, int]:
    """Mean edge per call, and a t-stat on weekly averages (calls in one week aren't independent)."""
    if df.empty:
        return float("nan"), float("nan"), 0
    wk = df.groupby("week")["edge"].mean()
    t = wk.mean() / (wk.std(ddof=1) / np.sqrt(len(wk))) if len(wk) > 2 else float("nan")
    return df["edge"].mean(), t, len(df)


def evaluate(calls: pd.DataFrame, start: str) -> dict:
    wf = walk_forward(calls, start)
    rated = wf[wf["prior_n"].fillna(0) >= MIN_PRIOR].copy()
    res = {"calls_total": len(calls), "posters_total": int(calls["uid"].nunique()),
           "test_start": start, "test_calls": len(wf), "test_calls_rated": len(rated)}
    m, t, n = _weekly_t(wf)
    res["all_calls"] = {"mean_edge": m, "t": t, "n": n}
    for d, name in [(1, "bullish"), (-1, "bearish")]:
        m, t, n = _weekly_t(wf[wf["dir"] == d])
        res[f"all_{name}"] = {"mean_edge": m, "t": t, "n": n}
    if len(rated) > 50:
        rated["q"] = pd.qcut(rated["prior_score"].rank(method="first"), 5, labels=[1, 2, 3, 4, 5])
        res["by_prior_quintile"] = {}
        for q, g in rated.groupby("q", observed=True):
            m, t, n = _weekly_t(g)
            res["by_prior_quintile"][int(q)] = {"mean_edge": m, "t": t, "n": n,
                                                "posters": int(g["uid"].nunique())}
        top = rated[rated["q"] == 5]
        for label, sub in [("top_not_chasing", top[top["dir"] * top["pre"] < 0.05]),
                           ("top_chasing", top[top["dir"] * top["pre"] >= 0.05]),
                           ("top_bullish", top[top["dir"] == 1]), ("top_bearish", top[top["dir"] == -1])]:
            m, t, n = _weekly_t(sub)
            res[label] = {"mean_edge": m, "t": t, "n": n}
        # is skill persistent at all? rank correlation of prior score vs the call's outcome
        res["score_outcome_spearman"] = float(rated["prior_score"].rank().corr(rated["edge"].rank()))
    # consensus: weekly crowd direction per ticker vs that ticker's next week
    for label, sub in [("crowd_attention", wf)]:
        cons = sub.groupby(["week", "sym"]).agg(net=("dir", "mean"), n=("dir", "size"), ex=("excess", "mean"))
        cons = cons[cons["n"] >= 5]
        res["crowd_net_vs_excess_corr"] = float(cons["net"].corr(cons["ex"])) if len(cons) > 20 else None
    return res, wf


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    msgs = load_messages()
    calls = build_calls(msgs)
    calls.to_parquet(DATA / "calls.parquet") if _has_parquet() else calls.to_csv(DATA / "calls.csv", index=False)
    start = str((calls["entry_day"].min() + (calls["entry_day"].max() - calls["entry_day"].min()) * 0.4).date())
    res, wf = evaluate(calls, start)
    print(json.dumps(res, indent=1, default=float))
    (OUT / "credibility_backtest.json").write_text(json.dumps(res, indent=1, default=float))
    scores = poster_scores(calls)
    scores.to_csv(DATA / "poster_scores.csv")
    scores[scores["n"] >= MIN_PRIOR].head(100).drop(columns=["edge_sum"]).to_csv(OUT / "poster_leaderboard.csv")
    return res


def _has_parquet() -> bool:
    try:
        import pyarrow  # noqa: F401
        return True
    except ImportError:
        return False


if __name__ == "__main__":
    main()
