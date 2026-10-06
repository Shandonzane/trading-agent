from types import SimpleNamespace

from tradebot import live


def test_kill_switch_cancels_buys_and_halts(tmp_path, monkeypatch):
    monkeypatch.setattr(live, "HALT", tmp_path / "HALT")
    from alpaca.trading.enums import OrderSide

    canceled = []
    m = live.LiveMonitor.__new__(live.LiveMonitor)
    m.cfg = {"risk": {"daily_loss_kill_switch_pct": 0.03}}
    m.out = tmp_path
    m.client = SimpleNamespace(
        get_orders=lambda req: [SimpleNamespace(id=1, side=OrderSide.BUY, symbol="SPY"),
                                SimpleNamespace(id=2, side=OrderSide.SELL, symbol="QQQ")],
        cancel_order_by_id=canceled.append)
    m.state = {"last_equity": 20_000, "day_pl_pct": -0.031}
    m.kill_switch()
    assert canceled == [1] and live.halted_today() and m.state["halted"]
    m.kill_switch()  # already halted today: nothing more to cancel
    assert canceled == [1]


def test_no_halt_on_small_loss(tmp_path, monkeypatch):
    monkeypatch.setattr(live, "HALT", tmp_path / "HALT")
    m = live.LiveMonitor.__new__(live.LiveMonitor)
    m.cfg = {"risk": {"daily_loss_kill_switch_pct": 0.03}}
    m.state = {"last_equity": 20_000, "day_pl_pct": -0.01}
    m.kill_switch()
    assert not live.halted_today()


def test_feed_server_requires_token(tmp_path, monkeypatch):
    import json
    import threading
    import urllib.error
    import urllib.request
    from http.server import ThreadingHTTPServer

    from tradebot import server

    monkeypatch.setattr(server, "LIVE", tmp_path)
    monkeypatch.setenv("FEED_TOKEN", "secret")
    (tmp_path / "state.json").write_text(json.dumps({"equity": 20000}))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.FeedHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_port}"
    assert urllib.request.urlopen(base + "/health").read() == b"ok"
    assert json.loads(urllib.request.urlopen(base + "/state.json?token=secret").read())["equity"] == 20000
    with open(tmp_path / f"feed-{__import__('datetime').date.today()}.jsonl", "w") as f:
        f.write(json.dumps({"ts": "2026-01-01T10:00:00+00:00", "kind": "a"}) + "\n")
        f.write(json.dumps({"ts": "2026-01-01T10:01:00+00:00", "kind": "b"}) + "\n")
    newer = json.loads(urllib.request.urlopen(base + "/feed?token=secret&since=2026-01-01T10:00:00%2B00:00").read())
    assert [e["kind"] for e in newer] == ["b"]
    try:
        urllib.request.urlopen(base + "/state.json?token=wrong")
        assert False, "should be forbidden"
    except urllib.error.HTTPError as e:
        assert e.code == 403
    httpd.shutdown()
