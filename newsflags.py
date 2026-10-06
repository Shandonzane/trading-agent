"""Headline check for the portfolio's news flags (paper research; places no orders).

Pulls the last two days of headlines from Google News RSS searches and the WSJ markets feed,
asks Claude whether any of the three NEWS flags in strategies/portfolio/speculative_trend.json
has actually fired, and writes results/flags/<date>.json. portfolio.py reads the latest file.

Rules Claude is held to:
- A flag is ON only for a confirmed event (announced guidance cut, an actual gate, an actual
  blockade or export ban), never for speculation, analyst notes, or "could".
- Every ON must cite the headline, verbatim. No headline, no flag.
- It can only raise a flag or say "nothing today". It never recommends a trade.

  python newsflags.py            # run, print, and save
  python newsflags.py --dry      # fetch and print headlines only (no Claude call)
"""
import argparse
import json
import re
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

from tradebot.config import ROOT, env

SPEC = ROOT / "strategies" / "portfolio" / "speculative_trend.json"
OUT = ROOT / "results" / "flags"
MODEL = "claude-opus-5-5"
QUERIES = {
    "hyperscaler_capex_cut": ["hyperscaler capex", "AI capex guidance", "Microsoft capital expenditures", "Amazon capital expenditures",
                              "Alphabet capital expenditures", "Meta capital expenditures", "data center spending cut"],
    "private_credit_gate": ["private credit fund redemptions", "private credit fund gates withdrawals", "BDC redemption limit"],
    "taiwan_rare_earths": ["Taiwan blockade", "Taiwan Strait military", "China rare earth export ban United States", "TSMC disruption"],
}
FEEDS = ["https://feeds.a.dj.com/rss/RSSMarketsMain.xml",
         "https://news.google.com/rss/search?q=stock+market+today&hl=en-US&gl=US&ceid=US:en",
         "https://news.google.com/rss/search?q=Federal+Reserve+OR+Treasury+yields&hl=en-US&gl=US&ceid=US:en"]


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def _parse(xml: bytes, source: str, since: datetime) -> list[dict]:
    out = []
    for item in ElementTree.fromstring(xml).iter("item"):
        title = (item.findtext("title") or "").strip()
        pub = item.findtext("pubDate")
        try:
            when = parsedate_to_datetime(pub) if pub else None
        except Exception:
            when = None
        if when and when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        if title and (when is None or when >= since):
            out.append({"title": re.sub(r"\s+", " ", title), "when": when.isoformat() if when else None,
                        "source": source, "link": item.findtext("link")})
    return out


def headlines(days: int = 4) -> dict[str, list[dict]]:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    out = {}
    for flag, qs in QUERIES.items():
        items, seen = [], set()
        for q in qs:
            url = "https://news.google.com/rss/search?" + urllib.parse.urlencode({"q": q, "hl": "en-US", "gl": "US", "ceid": "US:en"})
            try:
                for it in _parse(_get(url), f"google news: {q}", since):
                    if it["title"] not in seen:
                        seen.add(it["title"])
                        items.append(it)
            except Exception as e:
                items.append({"title": f"(feed error: {e})", "when": None, "source": url, "link": None})
        out[flag] = items[:40]
    general = []
    for f in FEEDS:
        try:
            general += _parse(_get(f), f, since)
        except Exception as e:
            general.append({"title": f"(feed error: {e})", "when": None, "source": f, "link": None})
    out["general"] = general[:40]
    return out


def judge(spec: dict, hl: dict) -> dict:
    import anthropic

    flags = [f for f in spec["watch_flags"] if f["type"] == "news"]
    prompt = (
        "You are a strict news checker for a paper-trading research project. For each flag below, decide from the "
        "headlines ONLY whether the trigger event has actually happened (confirmed, announced, in effect), not whether "
        "it might, could, or is being discussed. Analyst warnings, forecasts, opinion pieces and 'fears' are OFF.\n\n"
        "Flags:\n" + "\n".join(f"- {f['id']}: {f['trigger']}" + (f" (yellow, report but do not set on: {f['yellow']})" if f.get("yellow") else "") for f in flags) +
        "\n\nHeadlines (grouped by the search that found them, then a general markets feed):\n" +
        json.dumps({k: [h["title"] for h in v] for k, v in hl.items()}, indent=1) +
        "\n\nAnswer with JSON only, exactly this shape:\n"
        '{"flags": [{"id": "...", "on": true/false, "yellow": true/false, "confidence": "high|medium|low", '
        '"evidence": ["verbatim headline", ...], "note": "one sentence"}], "other_notable": ["anything else market-moving in these headlines, one line each, max 5"]}'
        "\n\n'yellow' is true only for a flag that defines a yellow condition and whose headlines meet it; it is false when the flag is on."
    )
    client = anthropic.Anthropic(api_key=env("ANTHROPIC_API_KEY"))
    msg = client.messages.create(model=MODEL, max_tokens=2000, messages=[{"role": "user", "content": prompt}])
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    m = re.search(r"\{.*\}", text, re.S)
    data = json.loads(m.group(0))
    titles = {h["title"] for v in hl.values() for h in v}
    for f in data["flags"]:   # a flag with no quoted headline that we actually fetched is not ON (or yellow)
        f["evidence"] = [e for e in f.get("evidence", []) if e in titles]
        f["yellow"] = bool(f.get("yellow")) and bool(f["evidence"]) and not f.get("on")
        if f.get("on") and not f["evidence"]:
            f["on"], f["note"] = False, "turned off: no verbatim headline backed it. " + f.get("note", "")
    return data


def latest() -> dict | None:
    files = sorted(OUT.glob("*.json")) if OUT.exists() else []
    return json.loads(files[-1].read_text()) if files else None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args(argv)
    spec = json.loads(SPEC.read_text())
    hl = headlines()
    n = sum(len(v) for v in hl.values())
    if a.dry:
        for k, v in hl.items():
            print(f"\n## {k} ({len(v)})")
            for h in v[:12]:
                print("-", h["title"])
        return 0
    if not env("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY missing; cannot judge headlines.")
        return 1
    data = judge(spec, hl)
    data.update({"date": date.today().isoformat(), "checked_at": datetime.now(timezone.utc).isoformat(),
                 "headlines_read": n, "model": MODEL})
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{data['date']}.json").write_text(json.dumps(data, indent=1))
    print(f"# News flags {data['date']} ({n} headlines)")
    for f in data["flags"]:
        print(f"- {f['id']}: {'ON' if f['on'] else ('YELLOW' if f.get('yellow') else 'off')} ({f.get('confidence')}) {f.get('note', '')}")
        for e in f.get("evidence", []):
            print(f"    \"{e}\"")
    for o in data.get("other_notable", []):
        print(f"- note: {o}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
