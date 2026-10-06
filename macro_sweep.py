"""Economic and social flags vs industry sectors (research only; nothing here places orders).

  python macro_sweep.py              # every flag and flag pair vs every sector ETF, report + CSV
  python macro_sweep.py --no-pairs   # single flags only
"""
import argparse
import sys

from tradebot.config import ROOT
from tradebot.macro import SECTORS, rotation, run


def pct(x):
    return f"{x * 100:+.1f}%"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-pairs", action="store_true")
    ap.add_argument("--out", default="2026-10-06")
    a = ap.parse_args(argv)
    res = run(pairs=not a.no_pairs)
    out = ROOT / "results" / "macro" / a.out
    out.mkdir(parents=True, exist_ok=True)
    df = res["results"]
    df.round(4).to_csv(out / "all_links.csv", index=False)
    c, desc = res["chance"], res["descriptions"]

    held = df[df.held_up].copy()
    held["score"] = held[["is_t", "oos_t"]].abs().min(axis=1)
    held = held.sort_values("score", ascending=False)
    L = ["# Economic and social flags vs industries", "",
         f"In-sample {res['periods']['in_sample']}, out-of-sample {res['periods']['out_of_sample']}. "
         f"{res['speed']['flags']} flags and flag pairs x {res['speed']['sectors']} sectors = {res['speed']['tests']:,} tests "
         f"in {res['speed']['seconds']}s. 'Effect' = the sector's return minus SPY on flag days, minus the same on all other days, annualized.", "",
         "## Luck check", "",
         f"- Links strong in both halves with the same sign: **{c['held_up']}**. Pure chance would give about "
         f"{c['expected_held_up_by_luck']} (more, since tests overlap).",
         f"- Links strong in both halves but with the sign flipped: {c['flipped']}.",
         f"- Of {c['strong_in_sample']} links strong in-sample, {c['strong_in_sample_same_sign_oos'] * 100:.0f}% kept the same direction later (50% = coin flip).", "",
         "## Single flags that held up in both halves", "",
         "| Flag | Industry | Effect vs SPY (early / later) | t (early / later) | Episodes (early / later) |", "|---|---|---|---|---|"]
    for _, r in held[~held.flag.str.contains("&")].iterrows():
        L.append(f"| {desc.get(r.flag, r.flag)} | {r.industry} ({r.sector}) | {pct(r.is_effect)} / {pct(r.oos_effect)} | "
                 f"{r.is_t:.1f} / {r.oos_t:.1f} | {r.episodes_is} / {r.episodes_oos} |")
    L += ["", "## Strongest flag pairs that held up (top 25)", "",
          "| Flags | Industry | Effect (early / later) | t (early / later) | Episodes |", "|---|---|---|---|---|"]
    for _, r in held[held.flag.str.contains("&")].head(25).iterrows():
        L.append(f"| {r.flag} | {r.industry} | {pct(r.is_effect)} / {pct(r.oos_effect)} | {r.is_t:.1f} / {r.oos_t:.1f} | "
                 f"{r.episodes_is} / {r.episodes_oos} |")

    L += ["", "## As a trade: hold SPY, switch into the sector (or SPY minus the sector) while the flag is on", "",
          "5 bp per switch. Only links with 3+ episodes in each half.", "",
          "| Link | Sharpe early (SPY) | Sharpe later (SPY) | CAGR later (SPY) | Worst drop later (SPY) |", "|---|---|---|---|---|"]
    picks = held[(held.episodes_is >= 3) & (held.episodes_oos >= 3)].head(12)
    for _, r in picks.iterrows():
        sign = 1 if r.oos_t > 0 else -1
        rt = rotation(r.flag, r.sector, sign)
        a, b = rt["in_sample"], rt["out_of_sample"]
        side = "into" if sign > 0 else "SPY minus"
        L.append(f"| {r.flag} -> {side} {r.sector} | {a['strategy']['sharpe']} ({a['spy']['sharpe']}) | "
                 f"{b['strategy']['sharpe']} ({b['spy']['sharpe']}) | {pct(b['strategy']['cagr'])} ({pct(b['spy']['cagr'])}) | "
                 f"{pct(b['strategy']['max_drawdown'])} ({pct(b['spy']['max_drawdown'])}) |")

    L += ["", "## Flags used", ""] + [f"- `{k}`: {v}" for k, v in desc.items()]
    L += ["", "Sectors: " + ", ".join(f"{k} {v}" for k, v in SECTORS.items()) +
          ". XLRE, XLC and JETS start after 2012, so they have no early half and are left out."]
    (out / "report.md").write_text("\n".join(L))
    print(f"Wrote {out}/report.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
