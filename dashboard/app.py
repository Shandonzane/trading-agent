"""Trading agent dashboard.  Run:  streamlit run dashboard/app.py"""
import json
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tradebot.config import have_alpaca_keys, load_config  # noqa: E402
from tradebot.journal import DB  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from crew import avatar_svg, load_crew  # noqa: E402

STRAT = "#2a78d6"   # strategy line (categorical slot 1)
BENCH = "#8a8984"   # buy-and-hold benchmark, neutral so the strategy reads first

st.set_page_config(page_title="Trading Agent", layout="wide")
cfg = load_config()

st.title("Trading Agent")
mode = "Alpaca paper account" if have_alpaca_keys() else "Offline simulator (no Alpaca keys yet)"
st.caption(f"Mode: **PAPER ONLY** · {mode}")

tab_crew, tab_live, tab_bt, tab_lib, tab_learn = st.tabs(
    ["Agent crew", "Paper account", "Backtests", "Strategy library", "Learn from a video"])


def q(sql):
    if not DB.exists():
        return pd.DataFrame()
    with sqlite3.connect(DB) as con:
        return pd.read_sql_query(sql, con)


def line_chart(series: dict, title: str, pct=False):
    fig = go.Figure()
    for name, (s, color, width) in series.items():
        fig.add_trace(go.Scatter(x=s.index, y=s.values, name=name, mode="lines",
                                 line=dict(color=color, width=width),
                                 hovertemplate="%{x|%b %d %Y}<br>" + name + ": %{y:" + (".1%" if pct else "$,.0f") + "}<extra></extra>"))
    fig.update_layout(title=title, height=380, margin=dict(l=10, r=10, t=40, b=10), hovermode="x unified",
                      legend=dict(orientation="h", y=1.02, x=1, xanchor="right", yanchor="bottom"))
    fig.update_yaxes(tickformat=".0%" if pct else "$,.0f", gridcolor="rgba(128,128,128,0.15)")
    fig.update_xaxes(showgrid=False)
    st.plotly_chart(fig, width="stretch")


CREW_CSS = """
<style>
.agent {border:1px solid rgba(128,128,128,.25); border-radius:16px; padding:14px 16px 10px; margin-bottom:6px;
        border-top:6px solid var(--c);}
.agent-top {display:flex; gap:12px; align-items:center;}
.agent-top h3 {margin:0; padding:0; font-size:1.25rem;}
.agent-top .who {opacity:.65; font-size:.85rem;}
.badge {display:inline-block; border-radius:999px; padding:1px 9px; font-size:.75rem; font-weight:600; margin-top:4px;}
.on {background:rgba(42,157,143,.18); color:#2a9d8f;} .off {background:rgba(128,128,128,.18); opacity:.8;}
.bubble {position:relative; background:rgba(128,128,128,.12); border-radius:12px; padding:8px 12px; margin:10px 0 6px;
         font-style:italic;}
.bubble:before {content:""; position:absolute; top:-8px; left:34px; border:8px solid transparent;
                border-top:0; border-bottom-color:rgba(128,128,128,.12);}
.bio {font-size:.85rem; opacity:.75; margin:4px 0 0;}
</style>
"""


def money(x):
    return f"{'+' if x > 0 else '-' if x < 0 else ''}${abs(x):,.0f}"


with tab_crew:
    crew = load_crew(ROOT)
    st.markdown(CREW_CSS, unsafe_allow_html=True)
    if not crew:
        st.info("No agents yet. Add a strategy or run `python cli.py backtest-all`.")
    else:
        on = [a for a in crew if a.on_duty]
        total = sum(a.paper_pnl for a in on)
        c1, c2, c3 = st.columns(3)
        c1.metric("Agents trading paper money", f"{len(on)} of {len(crew)}")
        c2.metric("Crew paper P&L", money(total))
        c3.metric("Open positions", sum(len(a.positions) for a in crew))

        for row in range(0, len(crew), 3):
            cols = st.columns(3)
            for col, a in zip(cols, crew[row:row + 3]):
                p = a.persona
                mood_label, mood_emoji = a.mood
                with col:
                    status = ('<span class="badge on">On duty · paper trading</span>' if a.on_duty
                              else '<span class="badge off">Benched · not approved to trade</span>')
                    st.markdown(
                        f'''<div class="agent" style="--c:{p["color"]}">
                        <div class="agent-top">{avatar_svg(p, mood_label, 84)}
                          <div><h3>{p["name"]} {p["title"]}</h3>
                          <div class="who">{a.strategy} · {"napping on the bench" if mood_label == "Benched" else "feeling " + mood_label.lower()} {mood_emoji}</div>{status}</div>
                        </div>
                        <div class="bubble">“{a.last_words}”</div>
                        <p class="bio">{p["bio"]}</p></div>''', unsafe_allow_html=True)
                    m1, m2 = st.columns(2)
                    if a.on_duty:
                        m1.metric("Paper P&L", money(a.paper_pnl),
                                  f"{a.paper_ret:+.2%}" if a.paper_ret is not None else None)
                    else:
                        m1.metric("Paper P&L", "—")
                    if a.bt_return is not None:
                        m2.metric("Backtest per year", f"{a.bt_cagr:+.1%}", f"{a.bt_cagr - a.bt_bench_cagr:+.1%} vs buy & hold",
                                  help=f"Equal-weight across the symbols it trades (or was tested on, if benched), with fees and slippage. "
                                       f"Total {a.bt_return:+.0%} vs buy & hold {a.bt_bench_return:+.0%}.")
                        fig = go.Figure()
                        fig.add_trace(go.Scatter(x=a.bt_bench.index, y=a.bt_bench.values, line=dict(color=BENCH, width=1),
                                                 name="Buy & hold", hovertemplate="%{x|%b %Y}<br>Buy & hold: %{y:.2f}x<extra></extra>"))
                        fig.add_trace(go.Scatter(x=a.bt_equity.index, y=a.bt_equity.values, line=dict(color=p["color"], width=2.5),
                                                 name=p["name"], hovertemplate="%{x|%b %Y}<br>" + p["name"] + ": %{y:.2f}x<extra></extra>"))
                        fig.update_layout(height=150, margin=dict(l=0, r=0, t=4, b=0), showlegend=False, hovermode="x unified")
                        fig.update_yaxes(tickformat=".1f", ticksuffix="x", gridcolor="rgba(128,128,128,0.15)", nticks=4)
                        fig.update_xaxes(showgrid=False, nticks=4)
                        st.plotly_chart(fig, width="stretch", config={"displayModeBar": False}, key=f"spark-{a.strategy}")
                        st.caption(f"{a.bt_trades} backtest trades · {a.bt_win_rate:.0%} winners"
                                   + (" · synthetic data" if a.synthetic else "") + "  \n"
                                   + "  \n".join(f"{'✅' if v == 'PASS' else '❌'} {sym}: {c:+.1%}/yr vs buy & hold {b:+.1%}"
                                                  for sym, (c, b, v) in a.bt_by_symbol.items()))
                    if a.positions:
                        st.dataframe(pd.DataFrame(a.positions).rename(columns={"pnl": "P&L"}).style.format(
                            {"entry": "${:,.2f}", "mark": "${:,.2f}", "P&L": money, "qty": "{:,.0f}"}),
                            width="stretch", hide_index=True)

        st.subheader("The race")
        st.caption("Each agent's backtest, as growth of $1 (log scale), against holding the same stocks. "
                   "Paper results replace this once agents have a few weeks of history.")
        fig = go.Figure()
        for a in crew:
            if a.bt_equity is None:
                continue
            s = a.bt_equity
            fig.add_trace(go.Scatter(x=s.index, y=s.values, name=f"{a.persona['name']} ({a.strategy})", mode="lines",
                                     line=dict(color=a.persona["color"], width=2.5 if a.on_duty else 1.5),
                                     opacity=1 if a.on_duty else 0.7,
                                     hovertemplate="%{x|%b %d %Y}<br>" + a.persona["name"] + ": %{y:.2f}x<extra></extra>"))
            fig.add_annotation(x=s.index[-1], y=np.log10(float(s.iloc[-1])), text=f"{a.mood[1]} {a.persona['name']}", showarrow=False,
                               xanchor="left", xshift=6, font=dict(color=a.persona["color"], size=13), yref="y")
        fig.update_layout(height=420, margin=dict(l=10, r=90, t=10, b=10), hovermode="x unified",
                          legend=dict(orientation="h", y=1.02, x=0, yanchor="bottom"))
        fig.update_yaxes(type="log", tickformat=".1f", ticksuffix="x", gridcolor="rgba(128,128,128,0.15)")
        fig.update_xaxes(showgrid=False)
        st.plotly_chart(fig, width="stretch", key="race")


with tab_live:
    eq = q("SELECT ts, equity, cash FROM equity ORDER BY ts")
    lots = q("SELECT * FROM lots")
    dec = q("SELECT * FROM decisions ORDER BY ts DESC LIMIT 200")
    orders = q("SELECT * FROM orders ORDER BY ts DESC LIMIT 200")
    c1, c2, c3 = st.columns(3)
    c1.metric("Equity", f"${eq['equity'].iloc[-1]:,.0f}" if len(eq) else "—")
    c2.metric("Cash", f"${eq['cash'].iloc[-1]:,.0f}" if len(eq) else "—")
    c3.metric("Open positions", len(lots))
    if len(eq) > 1:
        line_chart({"Equity": (eq.set_index(pd.to_datetime(eq["ts"]))["equity"], STRAT, 2)}, "Paper account equity")
    st.subheader("Open positions")
    st.dataframe(lots, width="stretch", hide_index=True) if len(lots) else st.info("No open positions.")
    st.subheader("Agent decisions (why it did what it did)")
    st.dataframe(dec, width="stretch", hide_index=True) if len(dec) else st.info("The agent hasn't run yet. Run `python cli.py run-agent`.")
    st.subheader("Orders")
    st.dataframe(orders, width="stretch", hide_index=True) if len(orders) else st.info("No orders yet.")
    with st.expander("Risk limits in force"):
        st.json(cfg["risk"])

with tab_bt:
    files = sorted((ROOT / "results").glob("*.json"))
    if not files:
        st.info("No backtests yet. Run `python cli.py backtest-all`.")
    else:
        rows = []
        for f in files:
            r = json.loads(f.read_text())
            rows.append({"file": f.name, "strategy": r["strategy"], "symbol": r["symbol"], "verdict": r["verdict"],
                         "CAGR": r["metrics"]["cagr"], "B&H CAGR": r["benchmark"]["cagr"],
                         "Sharpe": r["metrics"]["sharpe"], "B&H Sharpe": r["benchmark"]["sharpe"],
                         "OOS Sharpe": r["oos_metrics"].get("sharpe"), "Max DD": r["metrics"]["max_drawdown"],
                         "Trades": r["metrics"]["trades"]})
        table = pd.DataFrame(rows)
        st.dataframe(table.drop(columns="file").style.format(
            {"CAGR": "{:.1%}", "B&H CAGR": "{:.1%}", "Max DD": "{:.0%}", "Sharpe": "{:.2f}",
             "B&H Sharpe": "{:.2f}", "OOS Sharpe": "{:.2f}"}), width="stretch", hide_index=True)
        pick = st.selectbox("Inspect", table["file"], format_func=lambda f: f"{table.set_index('file').loc[f, 'strategy']} · {table.set_index('file').loc[f, 'symbol']}")
        r = json.loads((ROOT / "results" / pick).read_text())
        if r["verdict"] == "PASS":
            st.success("PASS: allowed into paper trading (`python cli.py approve <spec>`).")
        else:
            st.error("FAIL: " + " · ".join(r["verdict_reasons"]))
        m, b = r["metrics"], r["benchmark"]
        cols = st.columns(5)
        cols[0].metric("CAGR", f"{m['cagr']:.1%}", f"{m['cagr'] - b['cagr']:+.1%} vs B&H")
        cols[1].metric("Sharpe", f"{m['sharpe']:.2f}", f"{m['sharpe'] - b['sharpe']:+.2f} vs B&H")
        cols[2].metric("Max drawdown", f"{m['max_drawdown']:.0%}", f"B&H {b['max_drawdown']:.0%}", delta_color="off")
        cols[3].metric("Win rate", f"{m['win_rate']:.0%}")
        cols[4].metric("Time in market", f"{m['exposure']:.0%}")
        se = pd.Series(r["equity"]); se.index = pd.to_datetime(se.index)
        sb = pd.Series(r["bh_equity"]); sb.index = pd.to_datetime(sb.index)
        line_chart({"Strategy": (se, STRAT, 2), "Buy & hold": (sb, BENCH, 1.5)}, "Equity: strategy vs buy & hold")
        line_chart({"Strategy": (se / se.cummax() - 1, STRAT, 2), "Buy & hold": (sb / sb.cummax() - 1, BENCH, 1.5)},
                   "Drawdown", pct=True)
        st.subheader("Trades")
        st.dataframe(pd.DataFrame(r["trades"]), width="stretch", hide_index=True)

with tab_lib:
    for p in sorted((ROOT / "strategies").rglob("*.json")):
        if p.name.endswith(".extraction.json"):
            continue
        s = json.loads(p.read_text())
        tag = "APPROVED" if "approved" in p.parts else ("LEARNED" if "learned" in p.parts else "EXAMPLE")
        with st.expander(f"[{tag}] {s['name']}"):
            st.write(s.get("description", ""))
            if s.get("source_url"):
                st.write(f"Source: {s['source_url']}")
            if s.get("complete") is False:
                st.warning("Incomplete in the source. Missing: " + ", ".join(s.get("missing", [])))
            if s.get("assumptions"):
                st.info("Assumed (not said in the source): " + "; ".join(s["assumptions"]))
            for e in s.get("evidence", []):
                st.markdown(f"> **{e['rule']}**: “{e['quote']}”")
            st.json({k: v for k, v in s.items() if k not in ("evidence", "description")}, expanded=False)
            st.caption(str(p.relative_to(ROOT)))

with tab_learn:
    st.write("Paste a YouTube trading video. Claude reads the transcript, writes down the exact rules, "
             "flags anything the video left out, and backtests the result. Nothing trades until it passes.")
    url = st.text_input("YouTube URL")
    if st.button("Learn and backtest", disabled=not url):
        from cli import do_backtest
        from tradebot.video_learner import learn

        with st.spinner("Reading transcript and extracting rules..."):
            try:
                res, paths = learn(url)
            except Exception as e:
                st.error(f"Couldn't learn from that video: {e}")
                st.stop()
        st.write(res.summary)
        if not res.has_testable_strategy:
            st.warning("No testable strategy: " + "; ".join(res.why_not_testable))
        for idea in res.non_mechanical_ideas:
            st.caption(f"Non-mechanical idea: {idea}")
        if res.options_notes:
            st.info(f"Options structure: {res.options_notes}")
        for p in paths:
            with st.spinner(f"Backtesting {p.name}..."):
                for r in do_backtest(p):
                    st.write(f"**{r['strategy']} on {r['symbol']}: {r['verdict']}**")
                    for why in r["verdict_reasons"]:
                        st.write(f"- {why}")
        st.success("Saved. See the Backtests and Strategy library tabs.")
