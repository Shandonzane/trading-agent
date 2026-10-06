import numpy as np
import pytest

from tradebot.sweep import SLOTS, Context, SweepConfig, Window, bs_unit, run_sweep
from tradebot.sweep import test_variant as one_variant


def test_bs_put_call_parity_and_expiry():
    s = np.array([0.95, 1.0, 1.05])
    c, p = bs_unit(s, 0.01, np.full(3, 0.2))
    assert np.allclose(c - p, s - 1, atol=1e-6)
    c0, p0 = bs_unit(s, 0.0, np.full(3, 0.2))
    assert np.allclose(c0, [0, 0, 0.05]) and np.allclose(p0, [0.05, 0, 0])


@pytest.fixture(scope="module")
def ctx():
    try:
        return Context("SPY", pairs=False)
    except FileNotFoundError:
        pytest.skip("SPY 5-min cache not present")


def test_morning_direction_uses_only_bars_before_entry(ctx):
    w = Window("t", 6, 17)
    d = ctx.direction("follow_morning", w)
    assert np.array_equal(d, np.sign(ctx.C[:, 5] - ctx.O[:, 0]).astype(np.int8))


def test_matrix_stats_match_direct_computation(ctx):
    res = run_sweep(ctx, SweepConfig(entry_step_min=60, top=3), log=lambda *_: None)
    t = res["top_in_sample"][0]
    direct = one_variant(ctx, "overnight" if t["entry_slot"] >= SLOTS else
                          f"{(570 + 5 * t['entry_slot']) // 60:02d}:{(570 + 5 * t['entry_slot']) % 60:02d}",
                          "close" if t["exit_slot"] == SLOTS - 1 else (t["exit_slot"] - t["entry_slot"] + 1) * 5,
                          t["direction"], t["instrument"], t["weekday"], t["filter"])
    assert abs(direct["in_sample"]["sharpe"] - t["is_sh"]) < 0.01
    assert direct["out_of_sample"]["trades"] == int(t["oos_n"])


def test_occ_symbol_format():
    import pandas as pd

    from tradebot.real_options import occ

    assert occ("SPY", pd.Timestamp("2024-02-16"), "P", 480) == "SPY240216P00480000"
    assert occ("QQQ", pd.Timestamp("2026-11-20"), "C", 761.5) == "QQQ261120C00761500"
