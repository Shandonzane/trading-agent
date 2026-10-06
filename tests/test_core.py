import json
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tradebot import backtest, journal  # noqa: E402
from tradebot.broker import SimBroker  # noqa: E402
from tradebot.data import synthetic_bars  # noqa: E402
from tradebot.strategy import Condition, Operand, RuleSet, StrategySpec, evaluate, load_spec  # noqa: E402
from tradebot.video_learner import VideoExtraction, video_id  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def always(op=">"):
    return RuleSet(conditions=[Condition(left=Operand(ind="close"), op=op, right=Operand(value=0))])


def test_examples_load_and_run():
    df = synthetic_bars(800)
    for p in (ROOT / "strategies").glob("*.json"):
        r = backtest.run(load_spec(p), df)
        assert r.verdict in ("PASS", "FAIL")
        assert len(r.equity) == len(df)


def test_fills_next_open_no_lookahead():
    df = synthetic_bars(50)
    spec = StrategySpec(name="t", entry=always(), exit=None)
    r = backtest.run(spec, df, slippage_bps=0)
    t = r.trades[0]
    # signal on day 0 close -> fill at day 1 open
    assert t["entry_date"] == str(df.index[1].date())
    assert t["entry_price"] == pytest.approx(df["open"].iloc[1], rel=1e-6)


def test_slippage_costs_money():
    df = synthetic_bars(1200)
    spec = load_spec(ROOT / "strategies" / "rsi2_mean_reversion.json")
    free = backtest.run(spec, df, slippage_bps=0).metrics["total_return"]
    costly = backtest.run(spec, df, slippage_bps=50).metrics["total_return"]
    assert costly < free


def test_stop_loss_gap_fills_at_open():
    idx = pd.bdate_range("2024-01-01", periods=5)
    df = pd.DataFrame({"open": [100, 100, 100, 80, 80], "high": [101] * 3 + [81, 81],
                       "low": [99, 99, 99, 79, 79], "close": [100, 100, 100, 80, 80],
                       "volume": [1] * 5}, index=idx)
    spec = StrategySpec(name="t", entry=always(), stop_loss_pct=0.05)
    r = backtest.run(spec, df, slippage_bps=0)
    t = r.trades[0]
    assert t["reason"] == "stop_loss"
    assert t["exit_price"] == pytest.approx(80)  # gapped below the 95 stop, so filled at the open


def test_crosses_above():
    idx = pd.bdate_range("2024-01-01", periods=4)
    df = pd.DataFrame({"open": 1, "high": 1, "low": 1, "close": [1, 3, 4, 1], "volume": 1}, index=idx)
    rs = RuleSet(conditions=[Condition(left=Operand(ind="close"), op="crosses_above", right=Operand(value=2))])
    assert evaluate(df, rs).tolist() == [False, True, False, False]


def test_incomplete_spec_never_passes():
    df = synthetic_bars(800)
    spec = StrategySpec(name="t", entry=always(), complete=False, missing=["exit"])
    r = backtest.run(spec, df)
    assert r.verdict == "FAIL"


def test_video_id():
    assert video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=10") == "dQw4w9WgXcQ"
    assert video_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert video_id("https://www.youtube.com/shorts/dQw4w9WgXcQ") == "dQw4w9WgXcQ"


def test_extraction_schema_builds():
    assert "strategies" in json.dumps(VideoExtraction.model_json_schema())


def test_agent_sim_run(tmp_path, monkeypatch):
    from tradebot import agent

    monkeypatch.setattr(journal, "DB", tmp_path / "j.sqlite")
    from tradebot import portfolio_agent

    monkeypatch.setattr(portfolio_agent, "SPEC", tmp_path / "none.json")  # strategies only here
    from tradebot import trend_agent

    monkeypatch.setattr(trend_agent, "run", lambda *a, **k: None)
    from tradebot import bigbet_agent

    monkeypatch.setattr(bigbet_agent, "run", lambda *a, **k: None)
    df = synthetic_bars(400)
    spec = StrategySpec(name="always", symbols=["SPY"], entry=always(), position_size_pct=0.5)
    broker = SimBroker(100_000, path=tmp_path / "sim.json")
    log = agent.run_once(broker=broker, specs=[spec], bars_fn=lambda *a, **k: df)
    buys = [r for r in log if r["action"] == "buy"]
    assert len(buys) == 1
    # position size capped at max_position_pct (10%) even though spec asked for 50%
    assert buys[0]["qty"] * buys[0]["price"] <= 0.10 * 100_000 * 1.001
    # disallowed symbol is blocked
    spec2 = StrategySpec(name="bad", symbols=["GME"], entry=always())
    log2 = agent.run_once(broker=broker, specs=[spec2], bars_fn=lambda *a, **k: df)
    assert log2[0]["action"] == "blocked"
