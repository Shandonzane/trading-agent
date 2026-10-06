"""Dividend-stock research: ETFs vs SPY, yield/grower portfolios, dividend capture, pre-ex run-up,
dividend raise/cut drift, and rate sensitivity. Reads data/cache/dividends/*.csv (fetch.py)."""
import json, sys
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
D = ROOT / "data/cache/dividends"
OUT = Path(__file__).parent
COST = 0.0005  # 5 bps per side

data = {p.stem: pd.read_csv(p, index_col=0, parse_dates=True) for p in sorted(D.glob("*.csv"))}
adj = pd.DataFrame({k: v["adj"] for k, v in data.items()}).sort_index()
close = pd.DataFrame({k: v["close"] for k, v in data.items()}).sort_index()
opn = pd.DataFrame({k: v["open"] for k, v in data.items()}).sort_index()
div = pd.DataFrame({k: v["div"] for k, v in data.items()}).sort_index().fillna(0)
spy = adj["SPY"]
res = {}


def stats(eq):
    eq = eq.dropna()
    r = eq.pct_change().dropna()
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1
    vol = r.std() * np.sqrt(252)
    mdd = (eq / eq.cummax() - 1).min()
    return dict(cagr=round(cagr * 100, 2), vol=round(vol * 100, 1), sharpe=round(cagr / vol, 2),
                maxdd=round(mdd * 100, 1), years=round(yrs, 1))


# ---- A. Dividend ETFs vs SPY on the same window --------------------------------------------
etfs = ["SCHD", "VYM", "DVY", "SDY", "NOBL", "DGRO", "HDV", "SPYD", "VIG", "SPHD", "DIA", "JEPI", "QYLD", "XYLD"]
rows = []
for e in etfs:
    s = adj[e].dropna()
    s = s.loc[max(s.index[0], spy.index[0]):]
    b = spy.loc[s.index[0]:]
    a, bb = stats(s), stats(b)
    r, rb = s.pct_change().dropna(), b.pct_change().reindex(s.index).dropna()
    beta = np.cov(r.loc[rb.index], rb)[0, 1] / rb.var()
    yrs = {}
    for y in ["2008", "2020", "2022", "2023", "2024", "2025"]:
        if s.index[0] <= pd.Timestamp(f"{y}-01-05"):
            ys = s.loc[y]; ysp = spy.loc[y]
            yrs[y] = round((ys.iloc[-1] / ys.iloc[0] - 1 - (ysp.iloc[-1] / ysp.iloc[0] - 1)) * 100, 1)
    last = {}
    for n, lab in [(252, "1y"), (756, "3y"), (1260, "5y")]:
        if len(s) > n:
            last[lab] = round(((s.iloc[-1] / s.iloc[-n - 1]) - (spy.iloc[-1] / spy.iloc[-n - 1])) * 100, 1)
    ttm = div[e].loc[s.index[-1] - pd.Timedelta(days=365):].sum() / close[e].iloc[-1]
    rows.append(dict(etf=e, start=str(s.index[0].date()), **{f"{k}": v for k, v in a.items()},
                     spy_cagr=bb["cagr"], spy_maxdd=bb["maxdd"], excess_cagr=round(a["cagr"] - bb["cagr"], 2),
                     beta=round(beta, 2), yield_ttm=round(ttm * 100, 2), excess_by_year=yrs, excess_trailing=last))
res["etfs"] = rows

# ---- B. Stock portfolios: annual rebalance, equal weight ------------------------------------
stocks = [c for c in adj.columns if c not in etfs + ["SPY", "QQQ", "TLT"]]
start = pd.Timestamp("2001-01-01")
rebal = [d for d in adj.loc[start:].resample("YE").last().index]
rebal_days = [adj.index[adj.index <= d][-1] for d in rebal]


def ttm_yield(sym, d):
    dv = div[sym].loc[d - pd.Timedelta(days=365):d].sum()
    c = close[sym].loc[:d].dropna()
    return dv / c.iloc[-1] if len(c) and c.index[-1] >= d - pd.Timedelta(days=7) else np.nan


def years_raised(sym, d, n):
    """Latest payment vs the payment a year earlier, for each of the last n years: no cuts,
    and at least n-1 strict raises (robust to ex-dates drifting across calendar years)."""
    dv = div[sym].loc[:d]
    dv = dv[dv > 0]
    pays = []
    for k in range(n + 1):
        x = dv.loc[:d - pd.Timedelta(days=365 * k)]
        if not len(x) or x.index[-1] < d - pd.Timedelta(days=365 * k + 200):
            return False
        pays.append(x.iloc[-1])
    ch = np.diff(pays[::-1])
    return bool((ch >= -1e-9).all() and (ch > 1e-9).sum() >= n - 1)


def mom(sym, d):
    s = adj[sym].loc[:d].dropna()
    return s.iloc[-1] / s.iloc[-253] - 1 if len(s) > 253 else np.nan


rules = {
    "top10_yield": lambda d, el: sorted(el, key=lambda s: -ttm_yield(s, d))[:10],
    "top10_yield_ex_top3": lambda d, el: sorted(el, key=lambda s: -ttm_yield(s, d))[3:13],
    "growers_5y": lambda d, el: [s for s in el if years_raised(s, d, 5)],
    "growers_5y_top10_yield": lambda d, el: sorted([s for s in el if years_raised(s, d, 5)], key=lambda s: -ttm_yield(s, d))[:10],
    "yield_top20_then_mom_top10": lambda d, el: sorted(sorted(el, key=lambda s: -ttm_yield(s, d))[:20], key=lambda s: -mom(s, d))[:10],
    "no_dividend_or_low_yield": lambda d, el: sorted(el, key=lambda s: ttm_yield(s, d))[:10],
    "equal_weight_universe": lambda d, el: el,
}
port = {}
holdings_last = {}
for name, rule in rules.items():
    eq = [1.0]; idx = [rebal_days[0]]
    for d0, d1 in zip(rebal_days[:-1], rebal_days[1:] + [adj.index[-1]] if False else rebal_days[1:]):
        el = [s for s in stocks if pd.notna(adj[s].get(d0)) and adj[s].loc[:d0].count() > 300 and pd.notna(ttm_yield(s, d0))]
        pick = rule(d0, el)
        seg = adj.loc[d0:d1, pick].ffill()
        pr = (seg / seg.iloc[0]).mean(axis=1) * eq[-1] * (1 - 2 * COST)
        eq += list(pr.iloc[1:]); idx += list(pr.index[1:])
        holdings_last[name] = pick
    # last partial year
    d0 = rebal_days[-1]
    el = [s for s in stocks if pd.notna(adj[s].get(d0)) and pd.notna(ttm_yield(s, d0))]
    pick = rules[name](d0, el); holdings_last[name] = pick
    seg = adj.loc[d0:, pick].ffill()
    pr = (seg / seg.iloc[0]).mean(axis=1) * eq[-1] * (1 - COST)
    eq += list(pr.iloc[1:]); idx += list(pr.index[1:])
    port[name] = pd.Series(eq, index=idx)
port["SPY"] = spy.loc[rebal_days[0]:] / spy.loc[rebal_days[0]]
pdf = pd.DataFrame(port)
ann = pdf.resample("YE").last().pct_change().dropna() * 100
res["portfolios"] = {k: {**stats(pdf[k]),
                         "beat_spy_years": int((ann[k] > ann["SPY"]).sum()), "n_years": len(ann),
                         "since_2015": stats(pdf[k].loc["2015":]),
                         "since_2020": stats(pdf[k].loc["2020":])} for k in pdf}
res["portfolio_holdings_2026"] = {k: holdings_last[k] for k in rules}
res["portfolio_annual"] = ann.round(1).to_dict(orient="index")
res["portfolio_annual"] = {str(k.year): v for k, v in res["portfolio_annual"].items()}

# ---- C/D. Ex-date events: capture, pre-ex run-up, raise/cut drift ----------------------------
spy_c = adj["SPY"]
cap, runup, raise_ev, cut_ev = [], [], [], []
for s in stocks:
    dv = div[s][div[s] > 0]
    c = close[s]; o = opn[s]; a = adj[s]
    idx = c.dropna().index
    prev_amt = None; amts = []
    for d, amt in dv.items():
        if d not in idx: continue
        i = idx.get_loc(d)
        if i < 12 or i + 61 >= len(idx): amts.append(amt); continue
        dm1 = idx[i - 1]
        yld = amt / c[dm1]
        # capture: buy close day before ex, sell at ex-day open / close; collect dividend
        cap.append(dict(sym=s, date=d, yld=yld,
                        open_ret=(o[d] + amt) / c[dm1] - 1 - 2 * COST,
                        close_ret=(c[d] + amt) / c[dm1] - 1 - 2 * COST,
                        spy=spy_c.get(d, np.nan) / spy_c.get(dm1, np.nan) - 1))
        # pre-ex run-up: buy close t-10, sell close t-1 (no dividend received)
        d10 = idx[i - 10]
        runup.append(dict(sym=s, date=d, yld=yld, ret=c[dm1] / c[d10] - 1 - 2 * COST,
                          spy=spy_c.get(dm1) / spy_c.get(d10) - 1))
        # raise/cut vs median of last 3 payments (filters specials)
        if len(amts) >= 3:
            ref = np.median(amts[-3:])
            ch = amt / ref - 1
            if ref > 0 and (0.03 < ch < 0.5 or ch < -0.2):
                ev = dict(sym=s, date=d, change=ch)
                for h in (5, 20, 60):
                    ev[f"x{h}"] = (a[idx[i + h]] / a[d] - 1) - (spy_c.get(idx[i + h], np.nan) / spy_c.get(d, np.nan) - 1)
                (raise_ev if ch > 0 else cut_ev).append(ev)
        amts.append(amt)

def summ(df, col, sub=None):
    x = (df[col] - (df[sub] if sub else 0)).dropna()
    return dict(n=len(x), mean_bps=round(x.mean() * 1e4, 1), median_bps=round(x.median() * 1e4, 1),
                win=round((x > 0).mean() * 100, 1), t=round(x.mean() / x.std() * np.sqrt(len(x)), 2))

cap = pd.DataFrame(cap); runup = pd.DataFrame(runup)
re_, cu = pd.DataFrame(raise_ev), pd.DataFrame(cut_ev)
res["capture"] = {"all_open": summ(cap, "open_ret"), "all_close": summ(cap, "close_ret"),
                  "all_close_minus_spy": summ(cap, "close_ret", "spy"),
                  "since_2015_close_minus_spy": summ(cap[cap.date >= "2015"], "close_ret", "spy"),
                  "high_yield_q_close_minus_spy": summ(cap[cap.yld > cap.yld.quantile(0.8)], "close_ret", "spy")}
res["pre_ex_runup"] = {"all_minus_spy": summ(runup, "ret", "spy"),
                       "since_2015_minus_spy": summ(runup[runup.date >= "2015"], "ret", "spy"),
                       "top_yield_quintile_minus_spy": summ(runup[runup.yld > runup.yld.quantile(0.8)], "ret", "spy"),
                       "raw": summ(runup, "ret")}
res["raise_drift"] = {f"x{h}": summ(re_, f"x{h}") for h in (5, 20, 60)}
res["raise_drift_since_2015"] = {f"x{h}": summ(re_[re_.date >= "2015"], f"x{h}") for h in (5, 20, 60)}
res["big_raise_10pct"] = {f"x{h}": summ(re_[re_.change > 0.10], f"x{h}") for h in (5, 20, 60)}
res["big_raise_since_2015"] = {f"x{h}": summ(re_[(re_.change > 0.10) & (re_.date >= "2015")], f"x{h}") for h in (5, 20, 60)}
res["cut_drift"] = {f"x{h}": summ(cu, f"x{h}") for h in (5, 20, 60)}

# ---- E. Rate sensitivity: monthly excess vs SPY vs change in 10y yield -----------------------
try:
    t10 = pd.read_csv(ROOT / "data/cache/macro/fred_DGS10.csv", index_col=0, parse_dates=True).iloc[:, 0]
    t10 = pd.to_numeric(t10, errors="coerce").resample("ME").last().diff()
    m = adj.resample("ME").last().pct_change()
    out = {}
    for e in ["SCHD", "VYM", "HDV", "SPYD", "NOBL", "DVY", "VIG", "DGRO"]:
        x = (m[e] - m["SPY"]).dropna()
        j = pd.concat([x, t10], axis=1).dropna()
        b = np.polyfit(j.iloc[:, 1], j.iloc[:, 0], 1)[0]
        out[e] = dict(excess_pct_per_100bp_rise=round(b * 100, 2), corr=round(j.corr().iloc[0, 1], 2), months=len(j))
    res["rate_sensitivity"] = out
except Exception as ex:
    res["rate_sensitivity"] = str(ex)

# ---- F. Switch: hold the dividend ETF only while it is out-running SPY (monthly, lagged) -----
sw = {}
m_adj = adj.resample("ME").last()
for e in ["DVY", "VYM", "SDY", "VIG", "SCHD", "HDV"]:
    for look in (3, 6, 12):
        mm = m_adj[[e, "SPY"]].dropna()
        rel = (mm[e].pct_change(look) - mm["SPY"].pct_change(look))
        r = mm.pct_change()
        pick_e = (rel > 0).shift(1)  # decided at prior month end
        ret = np.where(pick_e.fillna(False), r[e], r["SPY"])
        switches = pick_e.astype(float).diff().abs().fillna(0)
        ret = pd.Series(ret, index=mm.index) - switches * 2 * COST
        ret = ret.iloc[look + 1:]
        eq = (1 + ret).cumprod(); sp = (1 + r["SPY"].iloc[look + 1:]).cumprod()
        yrs = len(ret) / 12
        mdd = lambda q: round(((q / q.cummax()) - 1).min() * 100, 1)
        sw[f"{e}_{look}m"] = dict(start=str(ret.index[0].date()), cagr=round((eq.iloc[-1] ** (1 / yrs) - 1) * 100, 2),
                                  spy_cagr=round((sp.iloc[-1] ** (1 / yrs) - 1) * 100, 2), maxdd=mdd(eq), spy_maxdd=mdd(sp),
                                  pct_time_in_div=round(pick_e.iloc[look + 1:].mean() * 100, 0),
                                  in_div_now=bool(rel.iloc[-1] > 0))
res["switch"] = sw

(OUT / "results.json").write_text(json.dumps(res, indent=1, default=str))
pdf.to_csv(OUT / "portfolio_equity.csv")
print(json.dumps(res, indent=1, default=str))
