"""Read-only social sources for the scout. No keys needed, nothing is ever posted.

StockTwits is the backbone: every message names its tickers (cashtags), its author, and often a
self-declared Bullish/Bearish tag, so a poster's calls can be graded against later prices.
ApeWisdom aggregates Reddit ticker mentions (Reddit itself blocks this cloud's IPs).
"""
import json
import time
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (research scout; read-only)"}
ST = "https://api.stocktwits.com/api/2"


def get_json(url: str, tries: int = 4) -> dict:
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
                return json.load(r)
        except Exception as e:
            code = getattr(e, "code", None)
            if code == 404:
                return {}
            if i == tries - 1:
                raise
            time.sleep(5 * (i + 1) * (4 if code == 429 else 1))
    return {}


def symbol_stream(symbol: str, max_id: int | None = None) -> list[dict]:
    return get_json(f"{ST}/streams/symbol/{symbol}.json" + (f"?max={max_id}" if max_id else "")).get("messages", [])


def user_stream(user_id: int, max_id: int | None = None) -> list[dict]:
    return get_json(f"{ST}/streams/user/{user_id}.json" + (f"?max={max_id}" if max_id else "")).get("messages", [])


def trending_stream() -> list[dict]:
    return get_json(f"{ST}/streams/trending.json").get("messages", [])


def trending_symbols() -> list[dict]:
    return get_json(f"{ST}/trending/symbols.json").get("symbols", [])


def reddit_mentions(pages: int = 2) -> list[dict]:
    """Reddit ticker mention counts (now and 24h ago) from ApeWisdom."""
    out = []
    for p in range(1, pages + 1):
        out += get_json(f"https://apewisdom.io/api/v1.0/filter/all-stocks/page/{p}").get("results", [])
    return out


def slim(m: dict) -> dict:
    """Keep what scoring needs: who, when, which tickers, which direction, what they said."""
    sent = ((m.get("entities") or {}).get("sentiment") or {}).get("basic")
    u = m.get("user") or {}
    return {
        "id": m["id"], "t": m["created_at"], "uid": u.get("id"), "user": u.get("username"),
        "followers": u.get("followers"), "ideas": u.get("ideas"), "official": u.get("official"),
        "sent": sent, "syms": [s["symbol"] for s in m.get("symbols") or []],
        "body": (m.get("body") or "")[:600], "likes": (m.get("likes") or {}).get("total", 0),
    }
