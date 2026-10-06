"""Build the Trading Floor page with a snapshot baked in.

The published page reads the project files live when the viewer allows it; the
snapshot is what it shows until then (or for viewers outside the project).

    python dashboard/floor_page.py out.html
"""
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent


def _rules(s: dict) -> dict:
    return {"entry": (s.get("entry") or {}).get("conditions", []), "exit": (s.get("exit") or {}).get("conditions", []),
            "stop_loss_pct": s.get("stop_loss_pct"), "position_size_pct": s.get("position_size_pct"), "max_hold_days": s.get("max_hold_days")}


def snapshot(root: Path = ROOT) -> dict:
    approved, strategies = [], {}
    for f in sorted((root / "strategies").rglob("*.json")):
        if f.name.endswith(".extraction.json"):
            continue
        try:
            s = json.loads(f.read_text())
        except Exception:
            continue
        if not isinstance(s, dict) or not s.get("name"):
            continue
        is_approved = "approved" in f.relative_to(root / "strategies").parts
        if is_approved:
            approved.append({"name": s["name"], "symbols": s.get("symbols", [])})
        if s["name"] not in strategies or is_approved:
            strategies[s["name"]] = {"name": s["name"], "approved": is_approved, "symbols": s.get("symbols", []),
                                     "description": s.get("description", ""), "complete": s.get("complete"),
                                     "missing": s.get("missing", []), "rules": _rules(s)}
    for a in approved:
        strategies[a["name"]]["approved"] = True
    results = []
    for f in sorted((root / "results").glob("*.json")) if (root / "results").exists() else []:
        if "synthetic" in f.name:
            continue
        try:
            r = json.loads(f.read_text())
        except Exception:
            continue
        if not isinstance(r, dict) or "verdict" not in r or "strategy" not in r:
            continue
        results.append({"strategy": r["strategy"], "symbol": r.get("symbol"), "verdict": r["verdict"],
                        "cagr": (r.get("metrics") or {}).get("cagr"), "bench_cagr": (r.get("benchmark") or {}).get("cagr"),
                        "reasons": r.get("verdict_reasons", [])})
    db = root / "data" / "journal.sqlite"
    rows = {"orders": [], "lots": [], "decisions": []}
    if db.exists():
        with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as con:
            con.row_factory = sqlite3.Row
            for t, order in (("orders", " ORDER BY ts"), ("lots", ""), ("decisions", " ORDER BY ts")):
                rows[t] = [dict(r) for r in con.execute(f"SELECT * FROM {t}{order}")]
    closes = {}
    for p in sorted((root / "data" / "cache").glob("*.csv")):
        try:
            closes[p.stem.upper()] = [round(float(x), 4) for x in pd.read_csv(p)["close"].tail(260)]
        except Exception:
            pass
    account = None
    cfg = root / "config.json"
    if cfg.exists():
        try:
            c = json.loads(cfg.read_text())
            account = {"account_size": c.get("account_size"), "starting_cash": c.get("starting_cash")}
        except Exception:
            account = None
    return {"approved": approved, "strategies": list(strategies.values()), "results": results, **rows, "closes": closes,
            "account": account, "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds")}


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "trading_floor.html")
    tpl = (Path(__file__).parent / "floor3d_template.html").read_text()
    data = json.dumps(snapshot()).replace("</", "<\\/")
    out.write_text(tpl.replace("/*SNAPSHOT*/null", data))
    print(out)
