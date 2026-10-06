"""The agent crew: each strategy is a character with a face, a personality and a mood.

Pure data + SVG helpers so they can be tested without Streamlit. Reads the paper
journal, backtest results and price cache; never writes anything.
"""
import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent

# Known strategies get hand-made characters, matched on words in the strategy name.
PERSONAS = [
    {"match": ["20-day breakout"], "name": "Tess", "title": "the Turtle",
     "color": "#2a9d8f", "prop": "shell",
     "bio": "Patient breakout hunter. Buys fresh 20-day highs, bails at the first sign of a 10-day low.",
     "voice": {"buy": "New high! I'm in.", "sell": "Trend's broken. I'm out.", "wait": "Nothing's breaking out. Shell up and wait.",
               "hold": "Riding it. No reason to leave yet.", "blocked": "Risk desk said no. Fair."}},
    {"match": ["rsi(2) pullback in uptrend"], "name": "Bea", "title": "the Bounce",
     "color": "#e76f51", "prop": "ears",
     "bio": "Jumpy dip-buyer. Loves a sharp 2-day selloff in an uptrend and sells the snap-back.",
     "voice": {"buy": "Oversold! Pouncing.", "sell": "Bounced. Taking the money.", "wait": "Nobody's panicking yet. Bored.",
               "hold": "Waiting for the snap-back...", "blocked": "Risk desk benched my trade."}},
    {"match": ["golden cross trend (50/200 sma)"], "name": "Goldie", "title": "the Owl",
     "color": "#d4a017", "prop": "glasses",
     "bio": "Slow, wise trend follower. Only moves when the 50-day crosses the 200-day. Trades rarely.",
     "voice": {"buy": "The cross has come. In we go.", "sell": "The trend has turned. Out.", "wait": "Hoo. Not yet.",
               "hold": "Still trending. Patience.", "blocked": "The risk desk overruled me."}},
]
SPARE = [("Max", "the Rookie", "#6a4c93", "cap"), ("Juno", "the Scout", "#1982c4", "antenna"),
         ("Ollie", "the Tinkerer", "#8ac926", "glasses"), ("Rio", "the Gambler", "#ff595e", "cap"),
         ("Pip", "the Sprinter", "#f15bb5", "ears"), ("Moss", "the Hermit", "#588157", "shell"),
         ("Vex", "the Contrarian", "#9b5de5", "antenna"), ("Kit", "the Day Trader", "#ff924c", "cap")]
DEFAULT_VOICE = {"buy": "Signal fired, buying.", "sell": "Exit rule hit, selling.", "wait": "No signal today.",
                 "hold": "Holding.", "blocked": "Risk check stopped me."}

# (min return, mood, emoji). First row whose threshold the return clears wins.
MOODS = [(0.05, "Ecstatic", "🤩"), (0.005, "Happy", "😄"), (-0.005, "Calm", "😐"),
         (-0.05, "Worried", "😟"), (-9e9, "Rattled", "😱")]


def persona(strategy: str, taken: set | None = None) -> dict:
    s = strategy.lower().strip()
    for p in PERSONAS:
        if s in p["match"]:
            return p
    # Video-learned strategies: stable pick from the spare cast, skipping names already on the crew.
    taken = taken or set()
    start = int(hashlib.md5(s.encode()).hexdigest(), 16) % len(SPARE)
    for i in range(len(SPARE)):
        name, title, color, prop = SPARE[(start + i) % len(SPARE)]
        if name not in taken:
            break
    return {"name": name, "title": title, "color": color, "prop": prop, "voice": DEFAULT_VOICE,
            "bio": "Learned this one from a video. Still finding my feet."}


def shorten(text: str, n: int = 150) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1].rsplit(" ", 1)[0].rstrip(",.;:(") + "…"


def mood(ret: float | None, benched: bool) -> tuple[str, str]:
    if ret is None:
        return ("Benched", "😴") if benched else ("Eager", "🙂")
    for threshold, label, emoji in MOODS:
        if ret >= threshold:
            return label, emoji
    return MOODS[-1][1:]


@dataclass
class Agent:
    strategy: str
    persona: dict
    on_duty: bool                      # approved for paper trading
    symbols: list = field(default_factory=list)
    # Paper trading
    paper_invested: float = 0.0
    paper_realized: float = 0.0
    paper_unrealized: float = 0.0
    positions: list = field(default_factory=list)
    last_words: str = ""
    last_ts: str = ""
    # Backtest (equal-weight across the symbols it was tested on)
    bt_equity: pd.Series | None = None
    bt_bench: pd.Series | None = None
    bt_return: float | None = None
    bt_bench_return: float | None = None
    bt_cagr: float | None = None
    bt_bench_cagr: float | None = None
    bt_win_rate: float | None = None
    bt_trades: int = 0
    bt_verdicts: dict = field(default_factory=dict)   # symbol -> PASS/FAIL
    bt_reason: str = ""
    bt_by_symbol: dict = field(default_factory=dict)   # symbol -> (cagr, buy & hold cagr, verdict)
    synthetic: bool = False

    @property
    def paper_pnl(self) -> float:
        return self.paper_realized + self.paper_unrealized

    @property
    def paper_ret(self) -> float | None:
        return self.paper_pnl / self.paper_invested if self.paper_invested else None

    @property
    def mood(self) -> tuple[str, str]:
        # Only agents trading paper money have feelings about it; the rest are asleep on the bench.
        return mood(self.paper_ret if self.on_duty else None, benched=not self.on_duty)


def _journal(db: Path, sql: str) -> pd.DataFrame:
    if not db.exists():
        return pd.DataFrame()
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as con:
        try:
            return pd.read_sql_query(sql, con)
        except Exception:
            return pd.DataFrame()


def last_close(symbol: str, root: Path = ROOT) -> float | None:
    p = root / "data" / "cache" / f"{symbol.upper()}.csv"
    if not p.exists():
        return None
    try:
        return float(pd.read_csv(p).iloc[-1]["close"])
    except Exception:
        return None


def _say(p: dict, action: str, reason: str) -> str:
    a = (action or "").lower()
    key = next((k for k in ("buy", "sell", "hold", "blocked", "wait") if k in a), None)
    if key is None and ("risk" in (reason or "").lower() or "block" in (reason or "").lower()):
        key = "blocked"
    return p["voice"].get(key or "wait", DEFAULT_VOICE["wait"])


def load_crew(root: Path = ROOT) -> list[Agent]:
    db = root / "data" / "journal.sqlite"
    approved = {}
    for f in (root / "strategies" / "approved").glob("*.json"):
        s = json.loads(f.read_text())
        approved[s["name"]] = s
    names = dict.fromkeys(approved)
    for f in (root / "strategies").rglob("*.json"):
        if not f.name.endswith(".extraction.json"):
            names.setdefault(json.loads(f.read_text()).get("name"), None)

    # Backtests: prefer real data; fall back to synthetic demos when an agent has none.
    results: dict[str, list] = {}
    for f in sorted((root / "results").glob("*.json")):
        r = json.loads(f.read_text())
        results.setdefault(r["strategy"], []).append(r)
        names.setdefault(r["strategy"], None)

    orders = _journal(db, "SELECT * FROM orders ORDER BY ts")
    lots = _journal(db, "SELECT * FROM lots")
    dec = _journal(db, "SELECT * FROM decisions ORDER BY ts")

    crew = []
    known = {m for p in PERSONAS for m in p["match"]}
    # Hand-made characters first, so the spare cast never borrows their names.
    order = sorted((n for n in names if n), key=lambda n: n.lower().strip() not in known)
    taken = {p["name"] for p in PERSONAS}
    for name in order:
        a = Agent(strategy=name, persona=persona(name, taken), on_duty=name in approved)
        taken.add(a.persona["name"])
        a.symbols = approved.get(name, {}).get("symbols", [])

        rs = results.get(name, [])
        real = [r for r in rs if "synthetic" not in r["symbol"].lower()]
        a.synthetic = bool(rs) and not real
        rs = real or rs
        # The agent's own record is the symbols it actually trades; benched agents use everything tested.
        traded = {x.upper() for x in a.symbols}
        if traded and any(r["symbol"].upper() in traded for r in rs):
            rs = [r for r in rs if r["symbol"].upper() in traded]
        if rs:
            eq = pd.concat([pd.Series(r["equity"]) for r in rs], axis=1).sort_index().ffill().dropna()
            bh = pd.concat([pd.Series(r["bh_equity"]) for r in rs], axis=1).sort_index().ffill().dropna()
            a.bt_equity = eq.div(eq.iloc[0]).mean(axis=1)
            a.bt_bench = bh.div(bh.iloc[0]).mean(axis=1)
            for s in (a.bt_equity, a.bt_bench):
                s.index = pd.to_datetime(s.index)
            a.bt_return = float(a.bt_equity.iloc[-1] - 1)
            a.bt_bench_return = float(a.bt_bench.iloc[-1] - 1)
            years = max((a.bt_equity.index[-1] - a.bt_equity.index[0]).days / 365.25, 1 / 365)
            a.bt_cagr = float(a.bt_equity.iloc[-1] ** (1 / years) - 1)
            a.bt_bench_cagr = float(a.bt_bench.iloc[-1] ** (1 / years) - 1)
            trades = [t for r in rs for t in r["trades"]]
            a.bt_trades = len(trades)
            a.bt_win_rate = sum(t["ret_pct"] > 0 for t in trades) / len(trades) if trades else None
            a.bt_verdicts = {r["symbol"]: r["verdict"] for r in rs}
            a.bt_by_symbol = {r["symbol"]: (r["metrics"]["cagr"], r["benchmark"]["cagr"], r["verdict"]) for r in rs}
            fails = [r for r in rs if r["verdict"] != "PASS"]
            if fails:
                a.bt_reason = fails[0]["verdict_reasons"][0] if fails[0]["verdict_reasons"] else ""

        # Paper P&L: replay this agent's orders per symbol, then mark open lots at the last close.
        if len(orders):
            for sym, g in orders[orders["strategy"] == name].groupby("symbol"):
                qty = cost = 0.0
                for o in g.itertuples():
                    if o.side == "buy":
                        qty += o.qty
                        cost += o.qty * o.price
                        a.paper_invested += o.qty * o.price
                    elif o.side == "sell" and qty:
                        avg = cost / qty
                        sold = min(o.qty, qty)
                        a.paper_realized += sold * (o.price - avg)
                        qty -= sold
                        cost -= sold * avg
        if len(lots):
            for lot in lots[lots["strategy"] == name].itertuples():
                mark = last_close(lot.symbol, root) or lot.entry_price
                pnl = lot.qty * (mark - lot.entry_price)
                a.paper_unrealized += pnl
                a.positions.append({"symbol": lot.symbol, "qty": lot.qty, "entry": lot.entry_price,
                                    "mark": mark, "pnl": pnl, "since": lot.entry_date})
        if len(dec):
            mine = dec[dec["strategy"] == name]
            if len(mine):
                last_ts = mine["ts"].iloc[-1]
                today = mine[mine["ts"] == last_ts]
                acted = today[~today["action"].str.lower().isin(["wait", "hold"])]
                row = (acted if len(acted) else today).iloc[-1]
                syms = ", ".join(sorted((acted if len(acted) else today)["symbol"].unique()))
                a.last_words = f"{_say(a.persona, row['action'], row['reason'])} ({syms})"
                a.last_ts = last_ts
        if not a.last_words:
            if a.on_duty:
                a.last_words = "Approved and ready. Waiting for my first market close."
            elif a.bt_return is not None:
                passed = [sym for sym, v in a.bt_verdicts.items() if v == "PASS"]
                if passed:
                    a.last_words = (f"Passed on {', '.join(passed)}, but not approved to trade yet. "
                                    f"Elsewhere: {a.bt_reason.rstrip('.')}.")
                    a.last_words = shorten(a.last_words, 170)
                else:
                    a.last_words = shorten(f"Benched. {a.bt_reason.rstrip('.')}.") if a.bt_reason else "Benched for now."
            else:
                a.last_words = "No backtest yet. Put me through my paces."
        crew.append(a)
    crew.sort(key=lambda x: (not x.on_duty, -x.paper_pnl, -(x.bt_return or -9)))
    return crew


# ---------------------------------------------------------------- avatar SVG

def _mouth(m: str) -> str:
    return {
        "Ecstatic": '<path d="M34 64 Q50 84 66 64 Z" fill="#3b1f1f"/><path d="M40 70 Q50 76 60 70" fill="#ff8fa3"/>',
        "Happy": '<path d="M36 64 Q50 76 64 64" stroke="#3b1f1f" stroke-width="4" fill="none" stroke-linecap="round"/>',
        "Calm": '<path d="M38 68 L62 68" stroke="#3b1f1f" stroke-width="4" stroke-linecap="round"/>',
        "Eager": '<path d="M38 66 Q50 74 62 66" stroke="#3b1f1f" stroke-width="4" fill="none" stroke-linecap="round"/>',
        "Benched": '<path d="M40 70 Q50 66 60 70" stroke="#3b1f1f" stroke-width="4" fill="none" stroke-linecap="round"/>',
        "Worried": '<path d="M36 72 Q50 62 64 72" stroke="#3b1f1f" stroke-width="4" fill="none" stroke-linecap="round"/>',
        "Rattled": '<ellipse cx="50" cy="70" rx="9" ry="11" fill="#3b1f1f"/>',
    }[m]


def _eyes(m: str) -> str:
    if m == "Benched":  # asleep
        return ('<path d="M30 46 Q36 51 42 46" stroke="#3b1f1f" stroke-width="3.5" fill="none" stroke-linecap="round"/>'
                '<path d="M58 46 Q64 51 70 46" stroke="#3b1f1f" stroke-width="3.5" fill="none" stroke-linecap="round"/>'
                '<text x="74" y="28" font-size="14" font-family="sans-serif" fill="#3b1f1f">z</text>'
                '<text x="83" y="18" font-size="10" font-family="sans-serif" fill="#3b1f1f">z</text>')
    if m == "Ecstatic":  # star eyes
        pts = [(0, -9), (2.5, -3), (8, -3), (4, 1), (5.5, 8), (0, 4), (-5.5, 8), (-4, 1), (-8, -3), (-2.5, -3)]
        out = ""
        for cx in (36, 64):
            d = "M" + " L".join(f"{cx + dx} {47 + dy}" for dx, dy in pts) + " Z"
            out += f'<path d="{d}" fill="#ffd166" stroke="#3b1f1f" stroke-width="1.5"/>'
        return out
    r = 7 if m in ("Worried", "Rattled") else 6
    eyes = (f'<circle cx="36" cy="47" r="{r}" fill="#fff"/><circle cx="64" cy="47" r="{r}" fill="#fff"/>'
            f'<circle cx="37" cy="48" r="3.4" fill="#3b1f1f"/><circle cx="65" cy="48" r="3.4" fill="#3b1f1f"/>')
    brows = {
        "Happy": ('<path d="M28 36 Q36 31 44 36" stroke="#3b1f1f" stroke-width="3" fill="none" stroke-linecap="round"/>'
                  '<path d="M56 36 Q64 31 72 36" stroke="#3b1f1f" stroke-width="3" fill="none" stroke-linecap="round"/>'),
        "Worried": ('<path d="M28 38 L44 33" stroke="#3b1f1f" stroke-width="3" stroke-linecap="round"/>'
                    '<path d="M72 38 L56 33" stroke="#3b1f1f" stroke-width="3" stroke-linecap="round"/>'),
        "Rattled": ('<path d="M28 36 L44 31" stroke="#3b1f1f" stroke-width="3" stroke-linecap="round"/>'
                    '<path d="M72 36 L56 31" stroke="#3b1f1f" stroke-width="3" stroke-linecap="round"/>'
                    '<path d="M80 40 Q86 50 80 54 Q74 50 80 40Z" fill="#7cc6fe"/>'),  # sweat drop
    }.get(m, "")
    return eyes + brows


def _prop(prop: str, color: str) -> str:
    return {
        "shell": f'<path d="M14 34 Q50 -6 86 34 Q50 22 14 34Z" fill="#264653"/>'
                 f'<path d="M34 18 L50 26 L66 18 M50 26 L50 10" stroke="{color}" stroke-width="2.5" fill="none"/>',
        "ears": f'<ellipse cx="32" cy="10" rx="8" ry="20" fill="{color}" stroke="#3b1f1f" stroke-width="2"/>'
                f'<ellipse cx="68" cy="10" rx="8" ry="20" fill="{color}" stroke="#3b1f1f" stroke-width="2"/>'
                '<ellipse cx="32" cy="12" rx="3.5" ry="13" fill="#ffc8b4"/><ellipse cx="68" cy="12" rx="3.5" ry="13" fill="#ffc8b4"/>',
        "glasses": '<circle cx="36" cy="47" r="11" fill="none" stroke="#3b1f1f" stroke-width="2.5"/>'
                   '<circle cx="64" cy="47" r="11" fill="none" stroke="#3b1f1f" stroke-width="2.5"/>'
                   '<path d="M47 47 L53 47" stroke="#3b1f1f" stroke-width="2.5"/>'
                   '<path d="M26 22 L34 8 L40 22 M60 22 L66 8 L74 22" fill="#b8860b"/>',
        "cap": '<path d="M18 30 Q50 0 82 30 Z" fill="#3b1f1f"/><path d="M50 26 L92 30 L82 34 Z" fill="#3b1f1f"/>',
        "antenna": f'<path d="M50 14 L50 0" stroke="#3b1f1f" stroke-width="3"/><circle cx="50" cy="0" r="5" fill="{color}"/>',
    }.get(prop, "")


def avatar_svg(p: dict, mood_label: str, size: int = 96) -> str:
    """A round character face whose expression follows the mood."""
    behind = _prop(p["prop"], p["color"]) if p["prop"] in ("ears", "antenna") else ""
    front = _prop(p["prop"], p["color"]) if p["prop"] not in ("ears", "antenna") else ""
    # Glasses sit over the eyes; other props sit on the head.
    blush = ('<circle cx="24" cy="60" r="6" fill="#ff8fa3" opacity=".55"/><circle cx="76" cy="60" r="6" fill="#ff8fa3" opacity=".55"/>'
             if mood_label in ("Happy", "Ecstatic", "Eager") else "")
    face = (f'<circle cx="50" cy="54" r="38" fill="{p["color"]}" stroke="#3b1f1f" stroke-width="2.5"/>'
            f'{blush}{_eyes(mood_label)}{_mouth(mood_label)}')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="-6 -22 112 120" width="{size}" height="{size}" '
            f'role="img" aria-label="{p["name"]} looks {mood_label.lower()}">{behind}{face}{front}</svg>')
