"""Strategy-variant sweeps (paper research only; nothing here places orders).

  python sweep.py run                       # sweep SPY and QQQ: millions of variants, report + JSON
  python sweep.py run --symbols SPY --no-pairs --entry-step 15     # smaller, faster sweep
  python sweep.py test SPY --entry 10:00 --hold 60 --dir follow_morning --inst 0dte \
      --weekday Mon --filter "above200 & vix<15"                   # one variant, year by year

Directions: long (shares long / calls), short (short / puts), follow_morning, fade_morning,
follow_gap, fade_gap, follow_prev_day, trend_200.  Instruments: shares, 0dte, weekly.
Filters: all, above200, below200, rsi2<10, rsi2>90, vix<15, vix15-25, vix>25, gap_up, gap_down,
prev_up, prev_down, "in:<strategy name>", and any "a & b" of them.
"""
import argparse
import json
import sys
from datetime import datetime

from tradebot.config import ROOT
from tradebot.sweep import Context, SweepConfig, run_sweep, save, test_variant


def pct(x):
    return f"{x * 100:.1f}%"


def report_md(results: list[dict]) -> str:
    L = ["# Strategy sweep results", ""]
    for r in results:
        s, o, b = r["settings"], r["overfitting"], r["benchmark"]
        L += [f"## {r['symbol']}", "",
              f"{r['speed']['variants']:,} variants in {r['speed']['seconds']}s "
              f"({r['speed']['variants_per_second']:,}/s). In-sample {s['in_sample']}, out-of-sample {s['out_of_sample']}. "
              f"Costs: {s['slippage_bps']:.0f} bp slippage per side on shares, {pct(s['option_cost_per_side'])} of premium per side on options "
              f"(modeled prices), option premium {pct(s['option_premium_per_trade'])} of the account per trade.", "",
              f"Buy and hold: in-sample Sharpe {b['in_sample']['sharpe']}, out-of-sample Sharpe {b['out_of_sample']['sharpe']} "
              f"(CAGR {pct(b['out_of_sample']['cagr'])}, worst drop {pct(b['out_of_sample']['max_drawdown'])}).", "",
              "### Luck check", "",
              f"- Variants with enough trades: {o['valid_variants']:,}. Best in-sample Sharpe {o['best_is_sharpe']}; "
              f"pure luck would produce about {o['noise_bar_is_sharpe']} from this many tries.",
              f"- Of {o['positive_is_variants']:,} variants that made money in-sample, {pct(o['positive_is_still_positive_oos'])} still did "
              f"out-of-sample (vs {pct(o['base_rate_positive_oos'])} of all variants). Rank correlation within the top 10%: "
              f"{o['top_decile_is_oos_rank_correlation']}.",
              f"- Top 100 in-sample variants: median out-of-sample Sharpe {o['top100_median_oos_sharpe']}, "
              f"{pct(o['top100_share_beating_buy_hold_oos'])} beat buy-and-hold out-of-sample.", ""]
        wf = r["walk_forward"]
        key = next(k for k in wf if k.startswith("top_"))
        L += ["### Walk-forward (re-pick the best variant each year using only earlier years)", "",
              "| Year | Pick | Pick return | Top-10 blend | Buy & hold |", "|---|---|---|---|---|"]
        for y in wf["years"]:
            L.append(f"| {y['year']} | {y['pick']} | {pct(y['pick_year_return'])} | {pct(y['top_n_year_return'])} | {pct(y['buy_hold_year_return'])} |")
        L += ["", f"Stitched: best pick Sharpe {wf['best_1']['sharpe']} (CAGR {pct(wf['best_1']['cagr'])}), "
              f"top-10 blend Sharpe {wf[key]['sharpe']} (CAGR {pct(wf[key]['cagr'])}), "
              f"buy and hold Sharpe {wf['buy_hold']['sharpe']} (CAGR {pct(wf['buy_hold']['cagr'])}).", "",
              "### Top in-sample variants and how they did afterwards", "",
              "| Variant | IS Sharpe | OOS Sharpe | OOS CAGR | OOS worst drop | OOS trades | OOS win |", "|---|---|---|---|---|---|---|"]
        for t in r["top_in_sample"][:15]:
            L.append(f"| {t['label']} | {t['is_sh']:.2f} | {t['oos_sh']:.2f} | {pct(t['oos_cagr'])} | "
                     f"{pct(t['oos_max_drawdown'])} | {int(t['oos_n'])} | {pct(t['oos_win'])} |")
        L += ["", "### What tends to work on average (median Sharpe of all variants, filter = all)", "",
              "Long = shares long or calls, short = shares short or puts.", ""]
        for inst in ["shares", "0dte", "weekly"]:
            for dim in ["entry", "hold", "direction", "weekday"]:
                rows = r["marginals"][inst][dim]
                L.append(f"**{inst} by {dim}**: " + ", ".join(f"{x[dim]} {x['is_sharpe']:+.2f}/{x['oos_sharpe']:+.2f}" for x in rows))
                L.append("")
        L.append("**filters, shares (best 12 by in-sample median)**: " + ", ".join(
            f"{x['filter']} {x['is_sharpe']:+.2f}/{x['oos_sharpe']:+.2f}" for x in r["marginals"]["filter_shares"][:12]))
        L += ["", "(pairs are in-sample / out-of-sample median Sharpe)", ""]
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--symbols", default="SPY,QQQ")
    r.add_argument("--slippage-bps", type=float, default=1.0)
    r.add_argument("--opt-cost", type=float, default=0.015, help="option cost per side, fraction of premium")
    r.add_argument("--opt-budget", type=float, default=0.02, help="option premium per trade, fraction of account")
    r.add_argument("--entry-step", type=int, default=5, help="minutes between candidate entry times")
    r.add_argument("--no-pairs", action="store_true", help="single filters only (no 'a & b' combinations)")
    r.add_argument("--out", default=None)
    t = sub.add_parser("test")
    t.add_argument("symbol")
    t.add_argument("--entry", default="09:35", help="HH:MM or 'overnight'")
    t.add_argument("--hold", default="close", help="minutes, or 'close'")
    t.add_argument("--dir", default="long")
    t.add_argument("--inst", default="shares")
    t.add_argument("--weekday", default="any")
    t.add_argument("--filter", default="all")
    t.add_argument("--slippage-bps", type=float, default=1.0)
    t.add_argument("--opt-cost", type=float, default=0.015)
    t.add_argument("--opt-budget", type=float, default=0.02)
    a = ap.parse_args(argv)

    if a.cmd == "test":
        ctx = Context(a.symbol, a.slippage_bps, a.opt_cost, a.opt_budget, pairs=False)
        print(json.dumps(test_variant(ctx, a.entry, a.hold, a.dir, a.inst, a.weekday, a.filter), indent=2))
        return 0

    out = ROOT / "results" / "sweeps" / (a.out or datetime.now().strftime("%Y%m%d-%H%M"))
    results = []
    for sym in a.symbols.split(","):
        ctx = Context(sym.strip(), a.slippage_bps, a.opt_cost, a.opt_budget, pairs=not a.no_pairs)
        res = run_sweep(ctx, SweepConfig(entry_step_min=a.entry_step))
        save(res, out / f"{ctx.symbol}.json")
        results.append(res)
    (out / "report.md").write_text(report_md(results))
    print(f"Wrote {out}/report.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
