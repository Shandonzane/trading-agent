"""Economic and social "flags" vs industry sectors: which conditions historically moved which industries.

A flag is a yes/no condition known before the trading day, e.g. "the yield curve is inverted",
"oil is up 25% in 3 months", "consumer sentiment is below 65", "it's an FOMC decision day",
"holiday shopping season". For every flag (and every pair of flags) and every sector ETF we measure
the sector's return relative to SPY on days the flag was on versus days it was off, in two
separate halves of history.

Honesty rules:
- Macro numbers are only used once they were published: monthly data waits 45 days after the
  month starts (e.g. September CPI counts from Oct 16), weekly jobless claims 6 days, daily market
  series 1 day. FRED serves today's revised numbers, not the first print, so some small look-ahead
  from revisions remains. The Sahm rule series used is FRED's real-time version.
- The flag is read at the prior close and the sector is held close-to-close the next day.
- In-sample 1999-2012, out-of-sample 2013 on. A link "held up" only if it is strong (|t| > 2) in
  both halves with the same sign. The report also shows how many links pure chance would produce.
- Regime flags (e.g. "Fed hiking") can be on for years at a time but happen only a few times, so
  each result shows the number of separate episodes. Few episodes = an anecdote, not a pattern.
"""
import json
import math
import re
import time as _time
from datetime import date, timedelta
from itertools import combinations
from statistics import NormalDist

import numpy as np
import pandas as pd

from .config import ROOT

CACHE = ROOT / "data" / "cache" / "macro"
IS_LAST_YEAR = 2012
YEAR_DAYS = 252

SECTORS = {
    "XLK": "Technology", "XLF": "Financials", "XLE": "Energy", "XLV": "Health care",
    "XLY": "Consumer discretionary", "XLP": "Consumer staples", "XLI": "Industrials",
    "XLU": "Utilities", "XLB": "Materials", "XLRE": "Real estate", "XLC": "Communication",
    "ITB": "Homebuilders", "KRE": "Regional banks", "XRT": "Retail", "SMH": "Semiconductors",
    "GDX": "Gold miners", "XOP": "Oil & gas producers", "IYT": "Transportation", "JETS": "Airlines",
}
BENCH = "SPY"

# FRED series: id -> (days after the observation date before it's public)
FRED = {
    "DGS10": 1, "T10Y2Y": 1, "DFF": 1, "DCOILWTICO": 1, "BAA10Y": 1, "DTWEXBGS": 1,
    "CPIAUCSL": 45, "SAHMREALTIME": 45, "UMCSENT": 45, "HOUST": 50, "ICSA": 6,
}


# ----------------------------------------------------------------------------- data

def _get(url: str) -> bytes:
    import urllib.request

    # federalreserve.gov refuses Python's default user agent; FRED stalls on unfamiliar ones.
    headers = {"User-Agent": "trading-agent-research/1.0"} if "federalreserve.gov" in url else {}
    req = urllib.request.Request(url, headers=headers)
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except Exception:
            if attempt == 4:
                raise
            _time.sleep(2 ** attempt)


def _fresh(path, max_age_days=1):
    return path.exists() and _time.time() - path.stat().st_mtime < max_age_days * 86400


def fred(series_id: str) -> pd.Series:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"fred_{series_id}.csv"
    if not _fresh(path):
        path.write_bytes(_get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"))
    df = pd.read_csv(path, index_col=0, parse_dates=True, na_values=".")
    return df.iloc[:, 0].dropna().astype(float)


def fomc_decision_dates() -> list[date]:
    """Scheduled FOMC decision days (last day of each meeting), 1999 on, from federalreserve.gov."""
    path = CACHE / "fomc_dates.json"
    if _fresh(path, 7):
        return [date.fromisoformat(d) for d in json.loads(path.read_text())]
    def get(url):
        return _get(url).decode("utf-8", "ignore")

    months = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july",
                                          "august", "september", "october", "november", "december"], 1)}

    def last_day(year, month_txt, day_txt):
        mt = month_txt.split("/")[-1].strip().lower()
        m = next(v for k, v in months.items() if k.startswith(mt[:3]))
        d = int(re.findall(r"\d+", day_txt)[-1])
        return date(year, m, d)

    out = set()
    cal = get("https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm")
    for block in re.split(r'<h4><a id="\d+">', cal)[1:]:
        year = int(block[:4])
        mons = re.findall(r'fomc-meeting__month[^>]*><strong>([^<]+)', block)
        days = re.findall(r'fomc-meeting__date[^>]*>([^<]+)', block)
        for mt, dt in zip(mons, days):
            if "notation" in dt.lower() or not re.search(r"\d", dt):
                continue
            out.add(last_day(year, mt, dt))
    for year in range(1999, 2021):
        page = get(f"https://www.federalreserve.gov/monetarypolicy/fomchistorical{year}.htm")
        for h in re.findall(r"<h5[^>]*>([^<]*Meeting[^<]*)</h5>", page):
            if "conference" in h.lower() or "unscheduled" in h.lower():
                continue
            txt = h.split("Meeting")[0].lower()          # e.g. "july 31-august 1" or "march 13"
            mts = [w for w in re.findall(r"[a-z]+", txt) if any(k.startswith(w[:3]) for k in months) and len(w) >= 3]
            if mts and re.search(r"\d", txt):
                out.add(last_day(year, mts[-1], txt))
    dates = sorted(d for d in out if d.year >= 1999)
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([d.isoformat() for d in dates]))
    return dates


def prices() -> pd.DataFrame:
    path = CACHE / "sector_prices.csv"
    if not _fresh(path):
        import yfinance as yf

        tickers = [BENCH, "^VIX", *SECTORS]
        df = yf.download(tickers, start="1998-12-01", progress=False, auto_adjust=True)["Close"]
        df.index = pd.to_datetime(df.index).tz_localize(None)
        CACHE.mkdir(parents=True, exist_ok=True)
        df.to_csv(path)
    return pd.read_csv(path, index_col=0, parse_dates=True)


# ----------------------------------------------------------------------------- flags

def _known(series_id: str, days: pd.DatetimeIndex) -> pd.Series:
    """Latest value that was public by each trading day."""
    s = fred(series_id)
    s.index = s.index + pd.Timedelta(days=FRED[series_id])
    s = s[~s.index.duplicated(keep="last")]
    return s.reindex(s.index.union(days)).ffill().reindex(days)


def _monthly_change(series_id: str, days, periods: int, pct: bool) -> pd.Series:
    s = fred(series_id)
    ch = (s.pct_change(periods) * 100) if pct else s.diff(periods)
    ch.index = ch.index + pd.Timedelta(days=FRED[series_id])
    return ch.reindex(ch.index.union(days)).ffill().reindex(days)


def build_flags(days: pd.DatetimeIndex, vix: pd.Series) -> tuple[dict, dict]:
    """Return ({group: {flag: bool array known at the prior close}}, descriptions)."""
    g, desc = {}, {}

    def add(group, name, on, text):
        on = pd.Series(on, index=days).fillna(False).astype(bool)
        g.setdefault(group, {})[name] = on.shift(1, fill_value=False).to_numpy()
        desc[name] = text

    dgs10 = _known("DGS10", days)
    add("rates", "rates_up_6m", dgs10.diff(126) > 0.75, "10-yr Treasury yield up more than 0.75 pt over 6 months")
    add("rates", "rates_down_6m", dgs10.diff(126) < -0.75, "10-yr Treasury yield down more than 0.75 pt over 6 months")
    curve = _known("T10Y2Y", days)
    add("curve", "curve_inverted", curve < 0, "Yield curve inverted (10-yr below 2-yr)")
    add("curve", "curve_steepening", curve.diff(126) > 0.5, "Yield curve steepened 0.5 pt+ over 6 months")
    dff = _known("DFF", days)
    add("fed", "fed_hiking", dff.diff(126) > 0.5, "Fed funds rate up 0.5 pt+ over 6 months")
    add("fed", "fed_cutting", dff.diff(126) < -0.5, "Fed funds rate down 0.5 pt+ over 6 months")
    cpi = _monthly_change("CPIAUCSL", days, 12, True)
    add("inflation", "inflation_high", cpi > 4, "CPI inflation above 4% a year")
    add("inflation", "inflation_low", cpi < 1.5, "CPI inflation below 1.5% a year")
    sahm = _known("SAHMREALTIME", days)
    add("labor", "jobs_weakening", sahm >= 0.3, "Unemployment rising (real-time Sahm indicator at 0.3+)")
    claims = _known("ICSA", days).rolling(20).mean()
    add("labor", "claims_rising", claims / claims.shift(126) - 1 > 0.15, "Weekly jobless claims up 15%+ vs 6 months ago")
    oil = _known("DCOILWTICO", days)
    add("oil", "oil_spike", oil / oil.shift(63) - 1 > 0.25, "Oil up 25%+ in 3 months")
    add("oil", "oil_crash", oil / oil.shift(63) - 1 < -0.25, "Oil down 25%+ in 3 months")
    add("credit", "credit_stress", _known("BAA10Y", days) > 3.0, "Corporate credit spread (Baa minus 10-yr) above 3 pts")
    usd = _known("DTWEXBGS", days)
    add("dollar", "dollar_surge", usd / usd.shift(126) - 1 > 0.05, "US dollar up 5%+ over 6 months")
    add("housing", "housing_slump", _monthly_change("HOUST", days, 12, True) < -15, "Housing starts down 15%+ from a year ago")
    sent = _known("UMCSENT", days)
    add("sentiment", "sentiment_low", sent < 65, "Consumer sentiment (U. Michigan) below 65")
    add("sentiment", "sentiment_falling", _monthly_change("UMCSENT", days, 12, False) < -10, "Consumer sentiment down 10+ pts in a year")
    v = vix.shift(0).reindex(days).ffill()
    add("fear", "fear_vix30", v > 30, "Fear: VIX above 30")
    add("fear", "calm_vix15", v < 15, "Calm: VIX below 15")

    md = days.month * 100 + days.day
    add("season", "holiday_season", md >= 1120, "Holiday shopping season (Nov 20 to Dec 31)")
    add("season", "sell_in_may", (days.month >= 5) & (days.month <= 10), "May to October")
    add("season", "january", days.month == 1, "January")
    pres = days.year % 4 == 0
    add("politics", "election_run_up", pres & (md >= 901) & (md <= 1110), "Presidential election run-up (Sep 1 to Nov 10)")
    add("politics", "midterm_year", days.year % 4 == 2, "Midterm election year")

    # Event days. These are scheduled, so they're known ahead: no prior-close shift needed,
    # we pre-shift them forward one day so add()'s shift lands them on the event day itself.
    fomc = pd.DatetimeIndex(pd.to_datetime(fomc_decision_dates()))
    is_fomc = days.isin(fomc)
    pos = np.flatnonzero(is_fomc)
    after = np.zeros(len(days), bool)
    for p in pos:
        after[p + 1:p + 6] = True
    nxt = lambda a: np.concatenate([a[1:], [False]])  # noqa: E731
    add("events", "fomc_day", nxt(is_fomc), "FOMC decision day")
    add("events", "fomc_week_after", nxt(after), "The 5 trading days after an FOMC decision")
    first_fri = (days.weekday == 4) & (days.day <= 7)
    add("events", "jobs_report_day", nxt(np.asarray(first_fri)), "Jobs report day (first Friday, approximate)")
    return g, desc


# ----------------------------------------------------------------------------- tests

def _episodes(on: np.ndarray) -> int:
    return int(((on[1:]) & (~on[:-1])).sum() + on[0])


def run(pairs: bool = True, min_days: int = 40) -> dict:
    t0 = _time.time()
    px = prices()
    days = px.index[px.index >= "1999-01-01"]
    px = px.loc[days]
    groups, desc = build_flags(days, px["^VIX"])
    flags = {"all": np.ones(len(days), bool)}
    for gr in groups.values():
        flags.update(gr)
    if pairs:
        for ga, gb in combinations(groups, 2):
            for na, a in groups[ga].items():
                for nb, b in groups[gb].items():
                    flags[f"{na} & {nb}"] = a & b
    names = list(flags)

    secs = [s for s in SECTORS if s in px]
    rets = px[[BENCH, *secs]].pct_change()
    avail = rets[secs].notna().to_numpy() & rets[BENCH].notna().to_numpy()[:, None]
    ex = np.where(avail, (rets[secs].to_numpy() - rets[[BENCH]].to_numpy()), 0.0)   # sector minus SPY
    raw = np.where(avail, rets[secs].to_numpy(), 0.0)

    years = np.array(days.year)
    is_ = years <= IS_LAST_YEAR
    F = np.array([flags[n] for n in names], np.float32)                 # flags x days
    # The effect of a flag = the sector's excess return on flag days MINUS on all other days, so a
    # sector that simply beat SPY for the whole period (tech) doesn't look like a flag effect.
    out_rows = []
    av, ex32, ex2, raw32 = avail.astype(np.float32), ex.astype(np.float32), (ex * ex).astype(np.float32), raw.astype(np.float32)
    for half, sel in (("is", is_), ("oos", ~is_)):
        tot = [sel.astype(np.float32) @ m for m in (av, ex32, ex2)]
        Fh = F * sel[None, :]
        n, s1, s2 = (Fh @ m for m in (av, ex32, ex2))
        r1 = Fh @ raw32
        n0, s10, s20 = tot[0] - n, tot[1] - s1, tot[2] - s2
        mu, mu0 = s1 / np.maximum(n, 1), s10 / np.maximum(n0, 1)
        var = np.maximum(s2 / np.maximum(n, 1) - mu ** 2, 1e-12)
        var0 = np.maximum(s20 / np.maximum(n0, 1) - mu0 ** 2, 1e-12)
        se = np.sqrt(var / np.maximum(n, 1) + var0 / np.maximum(n0, 1))
        t = np.where(n0 > 0, (mu - mu0) / se, mu / np.sqrt(var / np.maximum(n, 1)))
        out_rows.append({"n": n, "excess": mu * YEAR_DAYS, "effect": np.where(n0 > 0, mu - mu0, mu) * YEAR_DAYS,
                         "t": t, "raw": r1 / np.maximum(n, 1) * YEAR_DAYS})
    IS, OOS = out_rows
    n_tests = int(((IS["n"] >= min_days) & (OOS["n"] >= min_days)).sum())

    rows = []
    for i, name in enumerate(names):
        on = flags[name]
        for j, s in enumerate(secs):
            if IS["n"][i, j] < min_days or OOS["n"][i, j] < min_days:
                continue
            rows.append({"flag": name, "sector": s, "industry": SECTORS[s],
                         "is_days": int(IS["n"][i, j]), "oos_days": int(OOS["n"][i, j]),
                         "episodes_is": _episodes(on & is_), "episodes_oos": _episodes(on & ~is_),
                         "is_excess": float(IS["excess"][i, j]), "is_effect": float(IS["effect"][i, j]),
                         "is_t": float(IS["t"][i, j]),
                         "oos_excess": float(OOS["excess"][i, j]), "oos_effect": float(OOS["effect"][i, j]),
                         "oos_t": float(OOS["t"][i, j]),
                         "is_raw": float(IS["raw"][i, j]), "oos_raw": float(OOS["raw"][i, j])})
    df = pd.DataFrame(rows)
    df["held_up"] = (df.is_t.abs() > 2) & (df.oos_t.abs() > 2) & (np.sign(df.is_t) == np.sign(df.oos_t))
    df["flipped"] = (df.is_t.abs() > 2) & (df.oos_t.abs() > 2) & (np.sign(df.is_t) != np.sign(df.oos_t))

    # Chance check: if nothing were real, |t|>2 in both halves with the same sign happens with
    # probability ~ 0.0455^2 / 2 per test (more with correlated tests, so this is a floor).
    p_both = (2 * (1 - NormalDist().cdf(2))) ** 2 / 2
    strong_is = df[df.is_t.abs() > 2]
    single = df[~df.flag.str.contains("&")]
    return {
        "speed": {"flags": len(names), "sectors": len(secs), "tests": n_tests, "seconds": round(_time.time() - t0, 1)},
        "periods": {"in_sample": f"{days[0].date()} to {IS_LAST_YEAR}", "out_of_sample": f"{IS_LAST_YEAR + 1} to {days[-1].date()}"},
        "chance": {"expected_held_up_by_luck": round(n_tests * p_both, 1), "held_up": int(df.held_up.sum()),
                   "flipped": int(df.flipped.sum()),
                   "strong_in_sample": int(len(strong_is)),
                   "strong_in_sample_same_sign_oos": round(float((np.sign(strong_is.is_t) == np.sign(strong_is.oos_t)).mean()), 3) if len(strong_is) else None},
        "descriptions": desc,
        "results": df,
        "single": single,
    }


def rotation(flag: str, sector: str, sign: int) -> dict:
    """Simple trade from one link: hold SPY, and while the flag is on hold the sector instead
    (sign=+1), or hold SPY and buy that sector's puts... approximated here as SPY minus the sector
    (sign=-1, a pair trade). Close-to-close, 5 bp per switch."""
    px = prices()
    days = px.index[px.index >= "1999-01-01"]
    px = px.loc[days]
    groups, _ = build_flags(days, px["^VIX"])
    flags = {k: v for g in groups.values() for k, v in g.items()}
    on = np.ones(len(days), bool)
    for p in flag.split("&"):
        on &= flags[p.strip()]
    r = px[[BENCH, sector]].pct_change().fillna(0).to_numpy()
    strat = np.where(on, r[:, 1] if sign > 0 else 2 * r[:, 0] - r[:, 1], r[:, 0])
    switches = np.abs(np.diff(on.astype(int), prepend=0))
    strat = strat - switches * 0.0005
    have = px[sector].notna().to_numpy()
    years = np.array(days.year)
    out = {}
    for name, sel in (("in_sample", (years <= IS_LAST_YEAR) & have), ("out_of_sample", (years > IS_LAST_YEAR) & have)):
        out[name] = {"strategy": _m(strat[sel]), "spy": _m(r[sel, 0])}
    return out


def _m(r):
    eq = np.cumprod(1 + r)
    yrs = len(r) / YEAR_DAYS
    return {"sharpe": round(float(r.mean() / r.std() * math.sqrt(YEAR_DAYS)), 2) if r.std() > 0 else 0.0,
            "cagr": round(float(eq[-1] ** (1 / yrs) - 1), 4) if yrs > 0 else 0.0,
            "max_drawdown": round(float((eq / np.maximum.accumulate(eq) - 1).min()), 3)}
