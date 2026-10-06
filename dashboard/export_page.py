"""Write the Agent crew tab as one self-contained HTML page (for viewing away from Streamlit).

    python dashboard/export_page.py out.html
"""
import html
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from crew import avatar_svg, load_crew  # noqa: E402


def money(x):
    return f"{'+' if x > 0 else '−' if x < 0 else ''}${abs(x):,.0f}"


def pct(x, d=1):
    return f"{'+' if x > 0 else '−' if x < 0 else ''}{abs(x) * 100:.{d}f}%"


def weekly(s):
    return s.resample("W").last().dropna()


def line_chart(series, w, h, log=False, labels=None, pad=(8, 8, 22, 40)):
    """series: list of (pd.Series, css color, stroke width, dashed). Returns SVG."""
    top, right, bottom, left = pad
    ys = np.concatenate([s.values for s, *_ in series])
    f = np.log10 if log else (lambda v: v)
    lo, hi = f(ys.min()), f(ys.max())
    hi = hi if hi > lo else lo + 1
    x0 = min(s.index[0] for s, *_ in series)
    x1 = max(s.index[-1] for s, *_ in series)
    span = (x1 - x0).total_seconds() or 1
    X = lambda t: left + (w - left - right) * (t - x0).total_seconds() / span  # noqa: E731
    Y = lambda v: top + (h - top - bottom) * (1 - (f(v) - lo) / (hi - lo))  # noqa: E731
    out = [f'<svg viewBox="0 0 {w} {h}" class="chart" role="img" preserveAspectRatio="none">']
    # grid: a few round multiples
    cands = [0.5, 0.75, 1, 1.5, 2, 3, 4, 5, 6, 8, 10, 15, 20, 30, 50, 100]
    ticks = [c for c in cands if ys.min() * 0.98 <= c <= ys.max() * 1.02]
    if len(ticks) > 5:
        ticks = ticks[:: int(np.ceil(len(ticks) / 5))]
    for t in ticks:
        out.append(f'<line x1="{left}" x2="{w - right}" y1="{Y(t):.1f}" y2="{Y(t):.1f}" class="grid"/>'
                   f'<text x="{left - 6}" y="{Y(t) + 4:.1f}" class="tick" text-anchor="end">{t:g}x</text>')
    for yr in range(x0.year + 1, x1.year + 1, 2 if (x1.year - x0.year) > 6 else 1):
        t = x0.replace(year=yr, month=1, day=1)
        out.append(f'<text x="{X(t):.1f}" y="{h - 4}" class="tick" text-anchor="middle">{yr}</text>')
    for s, color, width, dashed in series:
        pts = " ".join(f"{X(t):.1f},{Y(v):.1f}" for t, v in s.items())
        out.append(f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="{width}" '
                   f'stroke-linejoin="round" vector-effect="non-scaling-stroke"{" stroke-dasharray=\"3 3\"" if dashed else ""}/>')
        out.append(f'<circle cx="{X(s.index[-1]):.1f}" cy="{Y(s.iloc[-1]):.1f}" r="3" fill="{color}"/>')
    for (s, color, *_), lab in zip(series, labels or []):
        if lab:
            out.append(f'<text x="{X(s.index[-1]) + 7:.1f}" y="{Y(s.iloc[-1]) + 4:.1f}" class="lab" fill="{color}">{html.escape(lab)}</text>')
    out.append("</svg>")
    return "".join(out)


def card(a):
    p = a.persona
    mood_label, mood_emoji = a.mood
    feeling = "napping on the bench" if mood_label == "Benched" else f"feeling {mood_label.lower()}"
    badge = ('<span class="pill on">On duty · paper trading</span>' if a.on_duty
             else '<span class="pill off">Benched · not approved to trade</span>')
    if a.on_duty:
        pnl_cls = "up" if a.paper_pnl > 0 else "down" if a.paper_pnl < 0 else ""
        paper = (f'<div class="num {pnl_cls}">{money(a.paper_pnl)}</div>'
                 f'<div class="sub">{pct(a.paper_ret, 2) if a.paper_ret is not None else "no trades yet"} on ${a.paper_invested:,.0f} invested</div>')
    else:
        paper = '<div class="num muted">—</div><div class="sub">not trading</div>'
    bt = chart = rows = ""
    if a.bt_cagr is not None:
        gap = a.bt_cagr - a.bt_bench_cagr
        bt = (f'<div class="num">{pct(a.bt_cagr)}<small>/yr</small></div>'
              f'<div class="sub {"up" if gap >= 0 else "down"}">{pct(gap)} vs buy &amp; hold</div>')
        chart = line_chart([(weekly(a.bt_bench), "var(--bench)", 1.2, True), (weekly(a.bt_equity), p["color"], 2.2, False)],
                           320, 120, pad=(8, 8, 18, 34))
        rows = "".join(f'<li><span class="{"pass" if v == "PASS" else "fail"}">{"Pass" if v == "PASS" else "Fail"}</span>'
                       f'<b>{html.escape(sym)}</b><span>{pct(c)}/yr</span><span class="muted">hold {pct(b)}</span></li>'
                       for sym, (c, b, v) in a.bt_by_symbol.items())
    pos = ""
    if a.positions:
        pos = ('<table><thead><tr><th>Holding</th><th>Shares</th><th>Bought</th><th>Now</th><th>P&amp;L</th></tr></thead><tbody>'
               + "".join(f'<tr><td>{x["symbol"]}</td><td>{x["qty"]:,.0f}</td><td>${x["entry"]:,.2f}</td>'
                         f'<td>${x["mark"]:,.2f}</td><td>{money(x["pnl"])}</td></tr>' for x in a.positions)
               + "</tbody></table>")
    return f'''
<article class="agent{' benched' if not a.on_duty else ''}" style="--c:{p['color']}">
  <header>
    <div class="face">{avatar_svg(p, mood_label, 88)}</div>
    <div class="id"><h2>{p['name']} <span>{p['title']}</span></h2>
      <div class="meta">{html.escape(a.strategy)} · {feeling} {mood_emoji}</div>{badge}</div>
  </header>
  <blockquote>“{html.escape(a.last_words)}”</blockquote>
  <p class="bio">{html.escape(p['bio'])}</p>
  <div class="stats"><div><div class="k">Paper P&amp;L</div>{paper}</div><div><div class="k">Backtest</div>{bt}</div></div>
  {chart}
  <div class="legend"><i style="background:{p['color']}"></i>{p['name']} <i class="dash"></i>buy &amp; hold same stocks</div>
  <ul class="syms">{rows}</ul>
  <div class="sub">{a.bt_trades} backtest trades{f" · {a.bt_win_rate:.0%} winners" if a.bt_win_rate is not None else ""}</div>
  {pos}
</article>'''


def page(crew):
    on = [a for a in crew if a.on_duty]
    total = sum(a.paper_pnl for a in on)
    race_series = [(weekly(a.bt_equity), a.persona["color"], 2.4 if a.on_duty else 1.4, False) for a in crew if a.bt_equity is not None]
    race_labels = [f"{a.mood[1]} {a.persona['name']}" for a in crew if a.bt_equity is not None]
    race = line_chart(race_series, 900, 340, log=True, labels=race_labels, pad=(12, 90, 24, 40)) if race_series else ""
    stamp = datetime.now(timezone.utc).strftime("%b %d, %Y %H:%M UTC")
    return f'''<title>Agent Crew</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Fredoka:wght@500;600&family=Nunito+Sans:opsz,wght@6..12,400;6..12,600;6..12,700&family=JetBrains+Mono:wght@500&display=swap" rel="stylesheet">
<style>
/* Layout: a trading-floor roll call. Summary strip, one card per agent, then the race chart. */
:root {{
  --bg:#f6f7f4; --surface:#ffffff; --fg:#1f2a2b; --muted:#66706f; --line:#dfe3df; --bench:#9aa19e;
  --up:#1f8a5b; --down:#c2410c; --onbg:#d9f2e9; --offbg:#eceeec; --quote:#f0f2ee;
  --display:"Fredoka", "Nunito Sans", system-ui, sans-serif; --body:"Nunito Sans", system-ui, sans-serif;
  --mono:"JetBrains Mono", ui-monospace, Menlo, monospace;
}}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{
  --bg:#121817; --surface:#1a2221; --fg:#e7ecea; --muted:#9aa5a2; --line:#2b3634; --bench:#7c8784;
  --up:#4cc38a; --down:#fb8b5b; --onbg:#173a2f; --offbg:#252e2d; --quote:#222c2b; color-scheme:dark }} }}
:root[data-theme="dark"] {{
  --bg:#121817; --surface:#1a2221; --fg:#e7ecea; --muted:#9aa5a2; --line:#2b3634; --bench:#7c8784;
  --up:#4cc38a; --down:#fb8b5b; --onbg:#173a2f; --offbg:#252e2d; --quote:#222c2b; color-scheme:dark }}
body {{ background:var(--bg); color:var(--fg); font-family:var(--body); font-size:15px; line-height:1.45; }}
main {{ max-width:1180px; margin:0 auto; padding-inline:16px; padding-block:28px 48px; display:grid; gap:28px; }}
h1 {{ font-family:var(--display); font-weight:600; font-size:clamp(28px,5vw,40px); margin:0; text-wrap:balance; }}
.top p {{ margin:4px 0 0; color:var(--muted); }}
.paper {{ display:inline-block; font-family:var(--mono); font-size:12px; letter-spacing:.06em; border:1.5px solid var(--fg); border-radius:4px; padding:1px 6px; margin-right:6px; }}
.summary {{ display:flex; flex-wrap:wrap; gap:12px 40px; }}
.summary .k, .k {{ font-size:12px; text-transform:uppercase; letter-spacing:.07em; color:var(--muted); }}
.summary .v {{ font-family:var(--display); font-size:30px; font-weight:600; font-variant-numeric:tabular-nums; }}
.crew {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(300px,1fr)); gap:18px; align-items:start; }}
.agent {{ background:var(--surface); border:1px solid var(--line); border-radius:18px; padding:16px; display:grid; gap:10px; min-width:0;
          box-shadow:inset 0 5px 0 var(--c); }}
.agent header {{ display:flex; gap:12px; align-items:center; }}
.face svg {{ display:block; }}
.benched .face {{ filter:saturate(.7); }}
.id {{ min-width:0; }}
h2 {{ font-family:var(--display); font-weight:600; font-size:22px; margin:0; line-height:1.15; }}
h2 span {{ font-weight:500; color:var(--muted); }}
.meta {{ font-size:13px; color:var(--muted); }}
.pill {{ display:inline-block; margin-top:5px; font-size:12px; font-weight:700; border-radius:999px; padding:2px 10px; }}
.pill.on {{ background:var(--onbg); color:var(--up); }} .pill.off {{ background:var(--offbg); color:var(--muted); }}
blockquote {{ margin:0; background:var(--quote); border-radius:14px; padding:9px 13px; font-style:italic; position:relative; }}
blockquote:before {{ content:""; position:absolute; top:-7px; left:36px; border:7px solid transparent; border-top:0; border-bottom-color:var(--quote); }}
.bio {{ margin:0; color:var(--muted); font-size:14px; }}
.stats {{ display:grid; grid-template-columns:1fr 1fr; gap:8px; border-top:1px solid var(--line); padding-top:10px; }}
.num {{ font-family:var(--display); font-weight:600; font-size:26px; font-variant-numeric:tabular-nums; }}
.num small {{ font-size:14px; color:var(--muted); font-weight:500; }}
.sub {{ font-size:13px; color:var(--muted); }}
.up {{ color:var(--up) !important; }} .down {{ color:var(--down) !important; }} .muted {{ color:var(--muted); }}
.chart {{ width:100%; height:auto; display:block; overflow:visible; }}
.grid {{ stroke:var(--line); stroke-width:1; }}
.tick {{ fill:var(--muted); font:11px var(--mono); }}
.lab {{ font:600 13px var(--body); }}
.legend {{ font-size:12px; color:var(--muted); display:flex; align-items:center; gap:6px; flex-wrap:wrap; }}
.legend i {{ display:inline-block; width:14px; height:3px; border-radius:2px; }}
.legend i.dash {{ background:repeating-linear-gradient(90deg,var(--bench) 0 3px,transparent 3px 6px); margin-left:8px; }}
.syms {{ list-style:none; margin:0; padding:0; display:grid; gap:3px; font-size:13px; font-variant-numeric:tabular-nums; }}
.syms li {{ display:grid; grid-template-columns:44px 52px 1fr auto; gap:6px; align-items:center; }}
.syms .pass, .syms .fail {{ font-size:11px; font-weight:700; text-align:center; border-radius:4px; padding:1px 0; }}
.pass {{ background:var(--onbg); color:var(--up); }} .fail {{ background:var(--offbg); color:var(--down); }}
table {{ width:100%; border-collapse:collapse; font-size:13px; font-variant-numeric:tabular-nums; }}
th {{ text-align:left; font-weight:600; color:var(--muted); font-size:12px; }}
th, td {{ padding:5px 4px; border-bottom:1px solid var(--line); }}
section.race {{ background:var(--surface); border:1px solid var(--line); border-radius:18px; padding:16px; min-width:0; }}
section.race h3 {{ font-family:var(--display); font-weight:600; font-size:22px; margin:0; }}
section.race p {{ margin:4px 0 12px; color:var(--muted); font-size:14px; }}
.racewrap {{ overflow-x:auto; }} .racewrap svg {{ min-width:560px; }}
footer {{ font-size:12px; color:var(--muted); }}
</style>
<main>
  <div class="top"><h1>Agent Crew</h1>
    <p><span class="paper">PAPER ONLY</span>Each strategy is a character. Its face follows its paper profit and loss.</p></div>
  <div class="summary">
    <div><div class="k">Trading paper money</div><div class="v">{len(on)} of {len(crew)}</div></div>
    <div><div class="k">Crew paper P&amp;L</div><div class="v">{money(total)}</div></div>
    <div><div class="k">Open positions</div><div class="v">{sum(len(a.positions) for a in crew)}</div></div>
  </div>
  <div class="crew">{"".join(card(a) for a in crew)}</div>
  <section class="race"><h3>The race</h3>
    <p>Each agent's backtest as growth of $1 (log scale). Paper results take over once the agents have a few weeks of history.</p>
    <div class="racewrap">{race}</div></section>
  <footer>Snapshot taken {stamp}. Paper P&amp;L is marked at the last daily close. Backtests include fees and slippage.</footer>
</main>'''


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "agent_crew.html")
    out.write_text(page(load_crew()))
    print(out)
