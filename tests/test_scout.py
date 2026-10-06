import pandas as pd

from scout import credibility as c


def _calls():
    d = pd.bdate_range("2026-01-05", periods=60)
    rows = []
    for i in range(0, 50, 5):
        rows.append({"uid": 1, "user": "a", "followers": 1, "sym": "X", "dir": 1, "entry_day": d[i],
                     "exit_day": d[i + 4], "edge": 0.02, "excess": 0.02, "pre": 0.0})
    return pd.DataFrame(rows)


def test_scores_only_use_calls_already_graded():
    calls = _calls()
    asof = calls["entry_day"].iloc[3]
    s = c.poster_scores(calls, asof=asof)
    assert s.loc[1, "n"] == (calls["exit_day"] < asof).sum()


def test_walk_forward_has_no_lookahead():
    wf = c.walk_forward(_calls(), "2026-01-05")
    for _, r in wf.iterrows():
        known = (_calls()["exit_day"] < r["week"]).sum()
        assert (r["prior_n"] if pd.notna(r["prior_n"]) else 0) == known


def test_shrinkage_pulls_small_records_toward_zero():
    calls = _calls().head(3)
    s = c.poster_scores(calls)
    assert 0 < s.loc[1, "score"] < 0.02 * 3 / 3
