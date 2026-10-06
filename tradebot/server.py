"""Always-on entry point for a small server (Railway, Fly.io, any Docker host).

  python cli.py serve

- During market hours runs the live monitor (tradebot/live.py): quotes, news, fills,
  P&L and the intraday kill switch. Paper only.
- Outside market hours it sleeps until a minute before the next open.
- Serves the live feed over HTTP on $PORT for the dashboard:
    GET /state.json?token=FEED_TOKEN     latest snapshot
    GET /feed?token=FEED_TOKEN&n=200     last n events from today's feed (&since=ISO ts for newer only)
    GET /floor?token=FEED_TOKEN          the 3D trading-floor page (dashboard/floor_server.html)
    GET /health                          "ok" (no token), for the host's health check
  FEED_TOKEN is required: without it the data endpoints return 403.
"""
import asyncio
import json
import os
import threading
import time
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .config import ROOT, env
from .live import LIVE, LiveMonitor


class FeedHandler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, ctype: str = "application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urlparse(self.path)
        q = parse_qs(url.query)
        if url.path == "/health":
            return self._send(200, b"ok", "text/plain")
        token = env("FEED_TOKEN")
        if not token or q.get("token", [""])[0] != token:
            return self._send(403, b'{"error": "bad or missing token"}')
        if url.path == "/state.json":
            f = LIVE / "state.json"
            return self._send(200, f.read_bytes() if f.exists() else b"{}")
        if url.path == "/floor":  # the 3D trading-floor page, served same-origin so it can poll this feed
            f = ROOT / "dashboard" / "floor_server.html"
            return self._send(200, f.read_bytes(), "text/html; charset=utf-8") if f.exists() else self._send(404, b"{}")
        if url.path == "/feed":
            n = min(int(q.get("n", ["200"])[0]), 2000)
            since = q.get("since", [""])[0]  # ISO timestamp: only events after it
            f = LIVE / f"feed-{date.today()}.jsonl"
            lines = f.read_text().splitlines() if f.exists() else []
            if since:
                lines = [l for l in lines if json.loads(l).get("ts", "") > since]
            lines = lines[-n:]
            return self._send(200, ("[" + ",".join(lines) + "]").encode())
        self._send(404, b'{"error": "not found"}')

    def log_message(self, *a):
        pass


def serve_http(port: int):
    LIVE.mkdir(parents=True, exist_ok=True)
    ThreadingHTTPServer(("0.0.0.0", port), FeedHandler).serve_forever()


def main():
    port = int(os.environ.get("PORT", "8080"))
    threading.Thread(target=serve_http, args=(port,), daemon=True).start()
    print(f"feed server on :{port}", flush=True)
    while True:
        try:
            mon = LiveMonitor()
            clock = mon.client.get_clock()
            if clock.is_open:
                print(f"{datetime.now():%F %T} market open, streaming {mon.symbols}", flush=True)
                asyncio.run(mon.run(until_close=True))
                continue
            wait = (clock.next_open - clock.timestamp).total_seconds() - 60
            print(f"{datetime.now():%F %T} market closed, next open {clock.next_open}", flush=True)
            time.sleep(min(max(wait, 30), 3600))  # re-check at least hourly
        except Exception as e:  # network blips: log and retry rather than exit
            print(f"{datetime.now():%F %T} error: {e}; retrying in 60s", flush=True)
            time.sleep(60)
