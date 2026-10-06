"""Command line for the trading agent.

  python cli.py backtest strategies/rsi2_mean_reversion.json [--symbol SPY] [--start 2015-01-01] [--synthetic]
  python cli.py backtest-all [--synthetic]
  python cli.py learn "https://www.youtube.com/watch?v=..." [--backtest]
  python cli.py approve strategies/rsi2_mean_reversion.json
  python cli.py run-agent
  streamlit run dashboard/app.py
"""
import argparse
import json
import re
import sys
from pathlib import Path

from tradebot import backtest
from tradebot.config import ROOT, load_config
from tradebot.data import get_bars, synthetic_bars
from tradebot.strategy import load_spec

RESULTS = ROOT / "results"


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def do_backtest(spec_path: Path, symbols=None, start="2015-01-01", synthetic=False) -> list[dict]:
    cfg = load_config()
    spec = load_spec(spec_path)
    out = []
    for sym in symbols or spec.symbols:
        df = synthetic_bars(seed=sum(map(ord, sym))) if synthetic else get_bars(sym, start)
        res = backtest.run(spec, df, symbol=sym + (" (synthetic)" if synthetic else ""),
                           slippage_bps=cfg["costs"]["slippage_bps"],
                           commission_per_share=cfg["costs"]["commission_per_share"],
                           approval=cfg["approval"]).to_dict()
        res["spec_path"] = str(spec_path.relative_to(ROOT)) if spec_path.is_relative_to(ROOT) else str(spec_path)
        RESULTS.mkdir(exist_ok=True)
        (RESULTS / f"{slug(spec.name)}__{slug(sym)}{'__synthetic' if synthetic else ''}.json").write_text(json.dumps(res))
        m, b, om = res["metrics"], res["benchmark"], res["oos_metrics"]
        print(f"{spec.name} on {res['symbol']}: {res['verdict']}\n"
              f"  full period  CAGR {m['cagr']:.1%}  Sharpe {m['sharpe']:.2f}  maxDD {m['max_drawdown']:.0%}  "
              f"trades {m['trades']}  win {m['win_rate']:.0%}\n"
              f"  buy & hold   CAGR {b['cagr']:.1%}  Sharpe {b['sharpe']:.2f}  maxDD {b['max_drawdown']:.0%}\n"
              f"  out-of-sample Sharpe {om.get('sharpe', 0):.2f} vs B&H {res['oos_benchmark'].get('sharpe', 0):.2f}")
        for r in res["verdict_reasons"]:
            print(f"   - {r}")
        out.append(res)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backtest")
    b.add_argument("spec")
    b.add_argument("--symbol", action="append")
    b.add_argument("--start", default="2015-01-01")
    b.add_argument("--synthetic", action="store_true", help="use fake random-walk data (offline demo)")
    ba = sub.add_parser("backtest-all")
    ba.add_argument("--synthetic", action="store_true")
    ln = sub.add_parser("learn")
    ln.add_argument("source", help="YouTube URL/id, or a .txt transcript file")
    ln.add_argument("--backtest", action="store_true")
    apv = sub.add_parser("approve")
    apv.add_argument("spec")
    sub.add_parser("run-agent")
    lv = sub.add_parser("live", help="stream quotes, news and fills; intraday kill switch (paper)")
    lv.add_argument("--forever", action="store_true", help="don't stop at the market close")
    sub.add_parser("serve", help="always-on: live monitor in market hours + feed over HTTP (for a server)")
    bi = sub.add_parser("backtest-intraday", help="opening-range day-trade presets on 5-minute bars")
    bi.add_argument("--preset", action="append")
    bi.add_argument("--symbol", action="append")
    bi.add_argument("--slippage-bps", type=float)
    a = ap.parse_args(argv)

    if a.cmd == "backtest":
        do_backtest(Path(a.spec), a.symbol, a.start, a.synthetic)
    elif a.cmd == "backtest-all":
        for p in sorted((ROOT / "strategies").rglob("*.json")):
            if "approved" in p.parts or p.name.endswith(".extraction.json"):
                continue
            do_backtest(p, synthetic=a.synthetic)
    elif a.cmd == "learn":
        from tradebot.video_learner import learn

        res, paths = learn(a.source)
        print(f"Summary: {res.summary}\nTestable strategy: {res.has_testable_strategy}")
        for w in res.why_not_testable:
            print(f"  - {w}")
        for p in paths:
            s = load_spec(p)
            print(f"Saved {p.relative_to(ROOT)}  complete={s.complete}  missing={s.missing}  assumptions={s.assumptions}")
            if a.backtest:
                do_backtest(p)
    elif a.cmd == "approve":
        spec = load_spec(a.spec)
        hits = [json.loads(f.read_text()) for f in RESULTS.glob(f"{slug(spec.name)}__*.json")
                if not f.name.endswith("__synthetic.json")]
        passed = [h for h in hits if h["verdict"] == "PASS"]
        if not passed:
            sys.exit("Not approved: needs a PASS backtest on real data first (synthetic runs don't count).")
        spec.symbols = [h["symbol"] for h in passed]  # only the symbols it passed on
        dest = ROOT / "strategies" / "approved" / f"{slug(spec.name)}.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(spec.model_dump_json(indent=2, exclude_none=True))
        print(f"Approved for PAPER trading on {spec.symbols}: {dest.relative_to(ROOT)}")
    elif a.cmd == "backtest-intraday":
        from tradebot import intraday

        cfg = load_config()
        slip = cfg["costs"]["slippage_bps"] if a.slippage_bps is None else a.slippage_bps
        for key in a.preset or list(intraday.PRESETS):
            p = intraday.PRESETS[key]
            for sym in a.symbol or ["QQQ", "SPY"]:
                res = intraday.run(p, intraday.get_5min(sym), sym, slip, approval=cfg["approval"]).to_dict()
                RESULTS.mkdir(exist_ok=True)
                (RESULTS / f"{key}__{slug(sym)}__intraday_{slug(str(slip))}bps.json").write_text(json.dumps(res))
                m, b, om = res["metrics"], res["benchmark"], res["oos_metrics"]
                print(f"{p.name} on {sym} ({slip} bps): {res['verdict']}\n"
                      f"  full period  CAGR {m['cagr']:.1%}  Sharpe {m['sharpe']:.2f}  maxDD {m['max_drawdown']:.0%}  "
                      f"trades {m['trades']}  win {m['win_rate']:.0%}  avg {m['avg_trade_pct']:.3f}%\n"
                      f"  buy & hold   CAGR {b['cagr']:.1%}  Sharpe {b['sharpe']:.2f}  maxDD {b['max_drawdown']:.0%}\n"
                      f"  out-of-sample Sharpe {om.get('sharpe', 0):.2f} vs B&H {res['oos_benchmark'].get('sharpe', 0):.2f}")
                for r in res["verdict_reasons"]:
                    print(f"   - {r}")
    elif a.cmd == "live":
        from tradebot.live import main as live_main

        live_main(until_close=not a.forever)
    elif a.cmd == "serve":
        from tradebot.server import main as serve_main

        serve_main()
    elif a.cmd == "run-agent":
        from tradebot.agent import run_once

        for row in run_once():
            print(row)


if __name__ == "__main__":
    main()
