"""Collect ~10 months of StockTwits history for the credibility backtest. Resumable.

Phase A samples symbol streams at weekly points in the past (message ids are sequential, so a
`max` id lands at a point in time). That finds posters as they were then, not only the ones still
active today. Phase B pulls the full recent history of the posters who tag calls most often.

  python -m scout.harvest symbols        # phase A
  python -m scout.harvest users --top 250 --pages 15   # phase B
"""
import argparse
import json
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import sources

DATA = Path(__file__).resolve().parent.parent / "data" / "scout"
MSGS = DATA / "messages.jsonl"
DONE = DATA / "harvest_done.txt"
SYMBOLS = ("AAPL MSFT NVDA TSLA AMZN META GOOGL AMD PLTR SMCI MU INTC AVGO NFLX COIN MSTR HOOD SOFI RIVN "
           "LCID GME AMC NIO BABA DIS BA PFE MRNA UBER SNAP RBLX SHOP ARM IONQ RGTI QBTS SOUN RKLB ASTS "
           "OKLO SMR HIMS CVNA UPST AFRM MARA RIOT CLSK BBAI TLRY ACHR JOBY LUNR NBIS CRWV TEM APP").split()
# id 636M ~ late Nov 2025, 665.8M ~ 2026-10-06 (about 2.7M ids a month)
POINTS = list(range(636_000_000, 665_000_001, 650_000))
_lock = threading.Lock()


def _done() -> set[str]:
    return set(DONE.read_text().split()) if DONE.exists() else set()


def _save(key: str, msgs: list[dict]) -> None:
    with _lock:
        with MSGS.open("a") as f:
            for m in msgs:
                f.write(json.dumps(sources.slim(m)) + "\n")
        with DONE.open("a") as f:
            f.write(key + "\n")


def _run(jobs: list[tuple[str, callable]], workers: int) -> None:
    done = _done()
    jobs = [j for j in jobs if j[0] not in done]
    print(f"{len(jobs)} requests to make", flush=True)

    def one(job):
        key, fn = job
        try:
            _save(key, fn())
        except Exception as e:
            print("fail", key, e, flush=True)

    with ThreadPoolExecutor(workers) as ex:
        for i, _ in enumerate(ex.map(one, jobs)):
            if i % 100 == 0:
                print(i, flush=True)


def symbols(workers: int) -> None:
    jobs = [(f"s:{s}:{p}", lambda s=s, p=p: sources.symbol_stream(s, p)) for p in POINTS for s in SYMBOLS]
    _run(jobs, workers)


def users(top: int, pages: int, workers: int) -> None:
    tagged = Counter()
    for line in MSGS.open():
        m = json.loads(line)
        if m["sent"] and not m["official"]:
            tagged[m["uid"]] += 1
    uids = [u for u, n in tagged.most_common() if n >= 3][:top]
    print(f"{len(uids)} posters", flush=True)

    def history(uid):
        out, max_id = [], None
        for _ in range(pages):
            page = sources.user_stream(uid, max_id)
            if not page:
                break
            out += page
            max_id = page[-1]["id"] - 1
        return out

    _run([(f"u:{u}", lambda u=u: history(u)) for u in uids], workers)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("phase", choices=["symbols", "users"])
    ap.add_argument("--top", type=int, default=250)
    ap.add_argument("--pages", type=int, default=15)
    ap.add_argument("--workers", type=int, default=3)
    a = ap.parse_args()
    DATA.mkdir(parents=True, exist_ok=True)
    symbols(a.workers) if a.phase == "symbols" else users(a.top, a.pages, a.workers)
