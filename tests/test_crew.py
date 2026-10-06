import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "dashboard"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import crew  # noqa: E402
from tradebot import journal  # noqa: E402


def test_mood_follows_pnl():
    assert crew.mood(0.08, False)[0] == "Ecstatic"
    assert crew.mood(0.01, False)[0] == "Happy"
    assert crew.mood(0.0, False)[0] == "Calm"
    assert crew.mood(-0.02, False)[0] == "Worried"
    assert crew.mood(-0.2, False)[0] == "Rattled"
    assert crew.mood(None, True)[0] == "Benched"


def test_persona_is_stable_for_unknown_strategies():
    assert crew.persona("20-day breakout")["name"] == "Tess"
    assert crew.persona("Some video strategy") == crew.persona("Some video strategy")
    for m in ["Ecstatic", "Happy", "Calm", "Eager", "Benched", "Worried", "Rattled"]:
        assert crew.avatar_svg(crew.persona("x"), m).startswith("<svg")


def test_paper_pnl_from_journal(tmp_path):
    (tmp_path / "strategies" / "approved").mkdir(parents=True)
    (tmp_path / "results").mkdir()
    (tmp_path / "data" / "cache").mkdir(parents=True)
    (tmp_path / "strategies" / "approved" / "b.json").write_text(json.dumps({"name": "20-day breakout", "symbols": ["SPY"]}))
    (tmp_path / "data" / "cache" / "QQQ.csv").write_text("Date,close\n2026-01-02,110\n")
    con = journal.connect(tmp_path / "data" / "journal.sqlite")
    journal.order(con, "20-day breakout", "SPY", "buy", 10, 100, "sim", "filled")
    journal.order(con, "20-day breakout", "SPY", "sell", 10, 105, "sim", "filled")   # +50 realized
    journal.order(con, "20-day breakout", "QQQ", "buy", 5, 100, "sim", "filled")
    journal.set_lot(con, "20-day breakout", "QQQ", 5, 100, "2026-01-01")             # +50 open at 110
    journal.decision(con, "20-day breakout", "QQQ", "buy", "entry rule fired")
    con.close()
    [a] = crew.load_crew(tmp_path)
    assert a.on_duty and a.paper_realized == 50 and a.paper_unrealized == 50
    assert a.mood[0] == "Ecstatic"           # +100 on 1,500 invested = +6.7%
    assert "QQQ" in a.last_words
