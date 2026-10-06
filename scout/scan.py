"""Live scout run: find what social media is moving on, weigh it by who is saying it, check the
claims, and write candidate signals to results/scout/signals/<date>.json. Never places orders.

No keyword search. Tickers come from three places:
  1. what is trending on StockTwits right now,
  2. Reddit tickers whose mention count jumped vs 24h ago (ApeWisdom),
  3. whatever the posters with the best graded track record are posting about (their own feeds).

  python -m scout.scan                 # full run with Claude claim check
  python -m scout.scan --no-claude     # skip the claim check
"""
import argparse
import json
import re
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from . import prices, sources
from .credibility import DATA, MIN_PRIOR, OUT, SKIP

SIGNALS = OUT / "signals"
MODEL = "claude-opus-5-5"


def load_scores() -> pd.DataFrame:
    p = DATA / "poster_scores.csv"
    return pd.read_csv(p, index_col=0) if p.exists() else pd.DataFrame(columns=["n", "score", "hit", "user"])


def discover(scores: pd.DataFrame, follow: int = 25) -> tuple[dict[str, set], list[dict]]:
    """Return {ticker: reasons} and every fresh message seen while discovering."""
    why: dict[str, set] = {}
    seen: list[dict] = []
    for s in sources.trending_symbols()[:20]:
        why.setdefault(s["symbol"], set()).add("stocktwits trending")
    for m in sources.trending_stream():
        seen.append(sources.slim(m))
    try:
        for r in sources.reddit_mentions(2):
            now, before = int(r.get("mentions") or 0), int(r.get("mentions_24h_ago") or 0)
            if now >= 20 and now >= 2.5 * max(before, 1):
                why.setdefault(r["ticker"], set()).add(f"reddit mentions {before}->{now} in 24h")
    except Exception as e:
        print("reddit mentions unavailable:", e)
    rated = scores[(scores["n"] >= MIN_PRIOR) & (scores["score"] > 0)].head(follow)
    for uid, row in rated.iterrows():
        for m in sources.user_stream(int(uid)):
            sm = sources.slim(m)
            seen.append(sm)
            if sm["sent"]:
                for s in sm["syms"]:
                    why.setdefault(s, set()).add(f"credible poster @{row['user']}")
    why = {k: v for k, v in why.items() if "." not in k and k not in SKIP}
    return why, seen


def weigh(ticker: str, msgs: list[dict], scores: pd.DataFrame, since: datetime) -> dict:
    fresh = [m for m in msgs if ticker in m["syms"] and m["sent"] and pd.Timestamp(m["t"]) >= since]
    fresh = list({(m["uid"], m["sent"]): m for m in fresh}.values())  # one vote per poster per side
    cred_votes, crowd = [], []
    for m in fresh:
        d = 1 if m["sent"] == "Bullish" else -1
        crowd.append(d)
        if m["uid"] in scores.index:
            r = scores.loc[m["uid"]]
            if r["n"] >= MIN_PRIOR and r["score"] > 0:
                cred_votes.append({"user": m["user"], "dir": d, "score": round(float(r["score"]), 4),
                                   "graded_calls": int(r["n"]), "hit_rate": round(float(r["hit"]), 2),
                                   "said": m["body"][:280], "at": m["t"]})
    px = prices.daily(ticker, refresh=True)
    pre5 = float(px["close"].iloc[-1] / px["close"].iloc[-6] - 1) if px is not None and len(px) > 6 else None
    net_cred = sum(v["dir"] * v["score"] for v in cred_votes)
    sample = [m["body"][:280] for m in sorted(fresh, key=lambda m: -(m["likes"] or 0))[:12]]
    return {"ticker": ticker, "crowd_calls": len(crowd), "sample_posts": sample, "crowd_bullish_share": round(sum(d > 0 for d in crowd) / len(crowd), 2) if crowd else None,
            "credible_calls": cred_votes, "credible_net": round(net_cred, 4),
            "direction": "bullish" if net_cred > 0 else "bearish" if net_cred < 0 else "none",
            "move_last_5d": round(pre5, 4) if pre5 is not None else None,
            "chasing": pre5 is not None and net_cred != 0 and (1 if net_cred > 0 else -1) * pre5 >= 0.05}


def news(ticker: str, days: int = 3) -> list[str]:
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": f"{ticker} stock when:{days}d", "hl": "en-US", "gl": "US", "ceid": "US:en"})
    try:
        from xml.etree import ElementTree
        raw = sources.urllib.request.urlopen(sources.urllib.request.Request(url, headers=sources.UA), timeout=30).read()
        return [re.sub(r"\s+", " ", i.findtext("title") or "") for i in ElementTree.fromstring(raw).iter("item")][:25]
    except Exception:
        return []


def claim_check(c: dict) -> dict:
    """Claude pulls the checkable claims out of the posts and checks each against real headlines."""
    import anthropic
    from tradebot.config import env

    heads = news(c["ticker"])
    posts = [v["said"] for v in c["credible_calls"]] + [p for p in c["sample_posts"] if p not in {v["said"] for v in c["credible_calls"]}]
    posts = posts[:12]
    prompt = (
        f"You check social-media stock claims for a paper-trading research project. Ticker: {c['ticker']}.\n\n"
        "Recent posts:\n" + "\n".join(f"- {p}" for p in posts) +
        "\n\nHeadlines from the last 3 days:\n" + "\n".join(f"- {h}" for h in heads) +
        "\n\nList each FACTUAL claim in the posts that could be checked (an event, a number, a filing, a deal, a rating). "
        "Ignore opinions, price targets and hype. For each claim say 'confirmed' only if a headline above states it, "
        "'contradicted' if a headline says otherwise, else 'unverified'. Quote the headline verbatim as evidence.\n"
        'Answer JSON only: {"claims": [{"claim": "...", "status": "confirmed|contradicted|unverified", "evidence": "verbatim headline or empty"}], '
        '"catalyst": "the real news driving this ticker per the headlines, one line, or none", '
        '"verdict": "supported|hype|contradicted|no_claims", "note": "one sentence"}'
    )
    msg = anthropic.Anthropic(api_key=env("ANTHROPIC_API_KEY")).messages.create(
        model=MODEL, max_tokens=1500, messages=[{"role": "user", "content": prompt}])
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    data = json.loads(re.search(r"\{.*\}", text, re.S).group(0))
    for cl in data.get("claims", []):  # no verbatim headline, no confirmation
        if cl.get("status") in ("confirmed", "contradicted") and cl.get("evidence") not in heads:
            cl["status"], cl["evidence"] = "unverified", ""
    data["headlines_read"] = len(heads)
    return data


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-claude", action="store_true")
    ap.add_argument("--hours", type=int, default=24)
    ap.add_argument("--check", type=int, default=8, help="how many top candidates get a claim check")
    a = ap.parse_args(argv)
    scores = load_scores()
    since = datetime.now(timezone.utc) - timedelta(hours=a.hours)
    why, seen = discover(scores)
    cands = []
    for t, reasons in why.items():
        msgs = seen + [sources.slim(m) for m in sources.symbol_stream(t)]
        c = weigh(t, msgs, scores, since)
        c["found_by"] = sorted(reasons)
        cands.append(c)
    cands.sort(key=lambda c: (c["crowd_calls"], len(c["credible_calls"])), reverse=True)
    if not a.no_claude:
        for c in [c for c in cands if c["crowd_calls"] >= 3][:a.check]:
            try:
                c["claim_check"] = claim_check(c)
            except Exception as e:
                c["claim_check"] = {"error": str(e)}
    for c in cands:
        cc = c.get("claim_check", {})
        bull, mv = c["crowd_bullish_share"] or 0, c["move_last_5d"] or 0
        # The only rule the backtest backed: a bullish crowd piling into a stock already up 10%+ in
        # 5 days lagged SPY by ~1.6% over the next week (t -2.9). Poster scores did not persist.
        c["status"] = ("avoid: crowd chasing a run-up" if c["crowd_calls"] >= 3 and bull >= 0.6 and mv >= 0.10
                       else "avoid: claims don't check out" if cc.get("verdict") in ("hype", "contradicted")
                       else "watch (unproven)" if cc.get("verdict") == "supported" and not c["chasing"]
                       else "noise")
    bt = OUT / "credibility_backtest.json"
    out = {"date": date.today().isoformat(), "run_at": datetime.now(timezone.utc).isoformat(),
           "note": "Research candidates only. Nothing here is an order. See research/social-scout.md for how much (or little) the backtest supports these.",
           "backtest": json.loads(bt.read_text()) if bt.exists() else None, "candidates": cands}
    SIGNALS.mkdir(parents=True, exist_ok=True)
    (SIGNALS / f"{out['date']}.json").write_text(json.dumps(out, indent=1, default=str))
    print(f"# Social scout {out['date']}: {len(cands)} tickers, {sum(c['status'].startswith('avoid') for c in cands)} flagged avoid")
    for c in cands[:20]:
        cc = c.get("claim_check", {})
        print(f"- {c['ticker']:6} {c['status']:28} {c['direction']:8} credible={len(c['credible_calls'])} crowd={c['crowd_calls']} "
              f"5d={c['move_last_5d']} {cc.get('verdict', '')} {cc.get('catalyst', '')}")
    return out


if __name__ == "__main__":
    main()
