import pandas as pd

from tradebot.intraday import ORBParams, _day_trades


def _day(rows):
    idx = pd.date_range("2024-01-02 09:30", periods=len(rows), freq="5min")
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)


def test_long_breakout_retest_hits_target_and_enters_next_open():
    rows = [(100, 101, 99, 100.5),        # opening range 99-101, midpoint 100
            (100.5, 101.6, 100.4, 101.5),  # closes above 101: breakout
            (101.5, 101.8, 100.9, 101.4),  # trades back to 101, closes outside: retest
            (101.6, 101.9, 101.3, 101.8),  # entry at this open: 101.6, risk 1.6, target 104.8
            (101.8, 105.0, 101.7, 104.9)]  # target hit
    rows += [(104.9, 105, 104.8, 104.9)] * 3
    tr = _day_trades(_day(rows), ORBParams("t"), 0.0, None, None)
    assert len(tr) == 1 and tr[0].side == "long" and tr[0].entry == 101.6
    assert tr[0].reason == "target" and abs(tr[0].exit - 104.8) < 1e-9


def test_no_trade_without_retest_and_close_inside_resets():
    rows = [(100, 101, 99, 100.5),
            (100.5, 101.6, 100.4, 101.5),  # breakout
            (101.5, 101.6, 100.2, 100.5),  # closes back inside: failed breakout, not a retest
            (100.5, 100.8, 100.2, 100.6)] + [(100.6, 100.7, 100.5, 100.6)] * 4
    assert _day_trades(_day(rows), ORBParams("t"), 0.0, None, None) == []


def test_stop_assumed_first_when_both_touched():
    rows = [(100, 101, 99, 100.5),
            (100.5, 101.6, 100.4, 101.5),
            (101.5, 101.8, 100.9, 101.4),
            (101.6, 110, 95, 101.0)] + [(101, 101, 101, 101)] * 3  # one bar spans stop and target
    tr = _day_trades(_day(rows), ORBParams("t"), 0.0, None, None)
    assert tr[0].reason == "stop" and tr[0].exit == 100.0
