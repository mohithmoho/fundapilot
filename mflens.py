"""MF Lens — mutual fund analyser module for FundaPilot.

Real data: AMFI NAVs via api.mfapi.in (search + full NAV history). All metrics are computed
from the NAV series at request time; beta/alpha/Treynor use a Nifty 50 index fund's NAV from
the same API as the market proxy, so there is no Yahoo dependency. Nothing is invented: any
fact we can't compute or verify is returned as null with a "verify" note.

Mounted by fundapilot.py via:  mount_mflens(app, jresp=..., ai_available=..., ai_rate_ok=...,
ai_chat=..., ai_system=...).  Self-check: `python mflens.py selftest` (offline math asserts).
"""
import json
import os
import re
import time
from datetime import date

import numpy as np
import pandas as pd
import requests
from flask import request, Response

BASE = os.path.dirname(os.path.abspath(__file__))
UNIVERSE_PATH = os.path.join(BASE, "mf_universe.json")
MFAPI = "https://api.mfapi.in/mf"
TTL_SECONDS = 6 * 3600  # NAVs update once a day
HORIZON_YEARS = {"lt1": 0.5, "1to3": 2.0, "3to5": 4.0, "gt5": 7.0}
HORIZON_LABEL = {"lt1": "under 1 year", "1to3": "1-3 years", "3to5": "3-5 years", "gt5": "5+ years"}
MATRIX = {
    ("conservative", "lt1"): ["debt-liquid"],
    ("conservative", "1to3"): ["debt-short"],
    ("conservative", "3to5"): ["hybrid-conservative"],
    ("conservative", "gt5"): ["hybrid-conservative", "largecap-index"],
    ("moderate", "lt1"): ["debt-liquid", "debt-short"],
    ("moderate", "1to3"): ["hybrid-conservative", "hybrid-aggressive"],
    ("moderate", "3to5"): ["hybrid-aggressive", "largecap-index"],
    ("moderate", "gt5"): ["largecap-index", "flexicap", "elss"],
    ("aggressive", "lt1"): ["debt-short"],
    ("aggressive", "1to3"): ["hybrid-aggressive"],
    ("aggressive", "3to5"): ["flexicap", "midcap", "elss"],
    ("aggressive", "gt5"): ["midcap", "smallcap", "flexicap"],
}

# Category browser — analyst's shortcuts over the curated universe. `buckets: None` = every fund.
CATEGORIES = {
    "top-returns": {"label": "Highest past returns", "buckets": None,
                    "note": "Ranked by 3-year CAGR across the whole universe. Chart-toppers are usually the riskiest categories - read the Max DD column before falling in love, and remember past returns do not repeat on schedule."},
    "largecap": {"label": "Large cap", "buckets": ["largecap-index"],
                 "note": "Core equity: India's biggest companies. Low-cost index funds here beat most active managers after fees."},
    "midcap": {"label": "Mid cap", "buckets": ["midcap"],
               "note": "More growth than large caps, deeper corrections. Treat as 5+ year money."},
    "smallcap": {"label": "Small cap", "buckets": ["smallcap"],
                 "note": "Highest long-run potential and the deepest drawdowns (-40% happens). 7+ year money; prefer SIPs over lumpsums."},
    "flexicap": {"label": "Flexi cap", "buckets": ["flexicap"],
                 "note": "The manager roams across market caps - a sensible single-fund equity core."},
    "hybrid": {"label": "Hybrid", "buckets": ["hybrid-conservative", "hybrid-aggressive"],
               "note": "Equity + debt in one wrapper: a smoother ride than pure equity, usually ahead of FDs over 3+ years."},
    "debt": {"label": "Debt funds", "buckets": ["debt-liquid", "debt-short"],
             "note": "Parking and capital preservation. Judge on safety and duration, not CAGR - FD-like returns are the point."},
    "elss": {"label": "ELSS (tax saver)", "buckets": ["elss"],
             "note": "Section 80C deduction with a 3-year lock-in. The lock-in is secretly a feature: it forces good behaviour."},
}

# SEBI category-mandate asset allocation (Oct-2017 categorization circular) — midpoints of the
# mandated bands, clearly labeled. This is the regulatory envelope, NOT the live portfolio.
SEBI_ALLOCATION = {
    "debt-liquid": {"label": "Liquid — SEBI mandate: debt & money-market papers maturing within 91 days", "slices": [["Debt & money market", 100]]},
    "debt-short": {"label": "Short/Ultra-short duration — SEBI duration-band mandate (all debt)", "slices": [["Debt & money market", 100]]},
    "hybrid-conservative": {"label": "Conservative Hybrid — SEBI band 10-25% equity (midpoint shown)", "slices": [["Debt", 82.5], ["Equity", 17.5]]},
    "hybrid-aggressive": {"label": "Aggressive Hybrid / BAF — SEBI band 65-80% equity (midpoint; BAF flexes 0-100%)", "slices": [["Equity", 72.5], ["Debt & arbitrage", 27.5]]},
    "largecap-index": {"label": "Large Cap — SEBI: ≥80% large-cap equity (index funds track ≥95%)", "slices": [["Large-cap equity", 90], ["Cash & other", 10]]},
    "flexicap": {"label": "Flexi Cap — SEBI: ≥65% equity, any market cap", "slices": [["Equity (any cap)", 80], ["Debt/cash/other", 20]]},
    "midcap": {"label": "Mid Cap — SEBI: ≥65% mid-cap equity", "slices": [["Mid-cap equity", 70], ["Other equity/cash", 30]]},
    "smallcap": {"label": "Small Cap — SEBI: ≥65% small-cap equity", "slices": [["Small-cap equity", 70], ["Other equity/cash", 30]]},
    "elss": {"label": "ELSS — SEBI: ≥80% equity, 3-year lock-in", "slices": [["Equity", 90], ["Cash & other", 10]]},
}

_MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}


def _holdings_freshness(as_of, today=None):
    """Age of a holdings snapshot, computed at request time so the card can never silently go stale.
    SEBI requires AMCs to disclose portfolios monthly, so 0-1 months old is current; 2+ is behind."""
    if not as_of:
        return None
    match = re.search(r"([A-Za-z]{3})[a-z]*\s+(\d{4})", as_of)
    if not match or match.group(1).lower() not in _MONTHS:
        return None
    month, year = _MONTHS[match.group(1).lower()], int(match.group(2))
    now = today or date.today()
    months_old = (now.year - year) * 12 + (now.month - month)
    if months_old < 0:
        months_old = 0
    label = "current month" if months_old == 0 else ("1 month old" if months_old == 1 else f"{months_old} months old")
    stale = months_old >= 2
    return {"asOf": as_of, "monthsOld": months_old, "stale": stale, "label": label,
            "note": ("Portfolios are disclosed monthly - this snapshot is behind; open the source to see the current one."
                     if stale else "Within the monthly disclosure cycle.")}


_cache = {}


def _universe():
    try:
        with open(UNIVERSE_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        # Fallback keeps the module alive without the curated file; UTI Nifty 50 code verified live 16 Jul 2026.
        return {"asOf": None, "benchmark": {"schemeCode": 120716, "note": "UTI Nifty 50 Index Direct (fallback)"},
                "riskFreeRatePct": 6.5, "funds": []}


def _fetch_scheme(code):
    """Returns (meta, nav Series ascending by date). Cached; raises on network failure."""
    code = int(code)
    hit = _cache.get(code)
    if hit and time.time() - hit[0] < TTL_SECONDS:
        return hit[1], hit[2]
    raw = requests.get(f"{MFAPI}/{code}", timeout=12).json()
    rows = raw.get("data") or []
    if not rows:
        raise ValueError(f"No NAV history for scheme {code}")
    frame = pd.DataFrame(rows)
    frame["date"] = pd.to_datetime(frame["date"], format="%d-%m-%Y")
    frame["nav"] = pd.to_numeric(frame["nav"], errors="coerce")
    frame = frame.dropna().sort_values("date")
    series = pd.Series(frame["nav"].values, index=frame["date"].values)
    meta = raw.get("meta") or {}
    _cache[code] = (time.time(), meta, series)
    return meta, series


def _round(value, digits=2):
    return None if value is None or not np.isfinite(value) else round(float(value), digits)


def _cagr(series, years):
    if len(series) < 30:
        return None
    end_date, end_nav = series.index[-1], series.iloc[-1]
    anchor = end_date - pd.DateOffset(years=years)
    if series.index[0] > anchor + pd.Timedelta(days=10):
        return None  # not enough history — never extrapolate
    start_nav = series.asof(anchor)
    if not np.isfinite(start_nav) or start_nav <= 0:
        return None
    return (end_nav / start_nav) ** (1.0 / years) - 1.0


def compute_metrics(series, bench_series=None, rf_pct=6.5):
    """All metrics from a NAV series (and optional benchmark series). Values in percent where labeled."""
    rf = rf_pct / 100.0
    out = {}
    last3 = series[series.index >= series.index[-1] - pd.DateOffset(years=3)]
    daily = last3.pct_change().dropna()

    cagr1, cagr3, cagr5 = _cagr(series, 1), _cagr(series, 3), _cagr(series, 5)
    six_months = series.asof(series.index[-1] - pd.DateOffset(months=6))
    out["latestNav"] = _round(series.iloc[-1], 4)
    out["navDate"] = str(pd.Timestamp(series.index[-1]).date())
    out["return6mPct"] = _round((series.iloc[-1] / six_months - 1) * 100) if np.isfinite(six_months) else None
    out["cagr1yPct"], out["cagr3yPct"], out["cagr5yPct"] = _round(cagr1 and cagr1 * 100), _round(cagr3 and cagr3 * 100), _round(cagr5 and cagr5 * 100)

    vol = daily.std() * np.sqrt(252) if len(daily) > 60 else None
    out["annVolPct"] = _round(vol and vol * 100)
    out["sharpe"] = _round((cagr3 - rf) / vol, 2) if cagr3 is not None and vol else None
    downside = daily[daily < 0]
    ddev = np.sqrt((downside ** 2).mean()) * np.sqrt(252) if len(downside) > 10 else None
    out["sortino"] = _round((cagr3 - rf) / ddev, 2) if cagr3 is not None and ddev else None

    running_max = series.cummax()
    drawdown = series / running_max - 1.0
    out["maxDrawdownPct"] = _round(drawdown.min() * 100)
    out["maxDrawdownDate"] = str(pd.Timestamp(drawdown.idxmin()).date()) if len(drawdown) else None

    out["beta"] = out["alphaPct"] = out["treynor"] = None
    if bench_series is not None:
        bench3 = bench_series[bench_series.index >= bench_series.index[-1] - pd.DateOffset(years=3)]
        joined = pd.concat([last3, bench3], axis=1, join="inner").pct_change().dropna()
        if len(joined) > 120:
            fund_r, bench_r = joined.iloc[:, 0], joined.iloc[:, 1]
            var = bench_r.var()
            if var > 0:
                beta = fund_r.cov(bench_r) / var
                out["beta"] = _round(beta, 2)
                bench_cagr3 = _cagr(bench_series, 3)
                if cagr3 is not None and bench_cagr3 is not None:
                    out["alphaPct"] = _round((cagr3 - (rf + beta * (bench_cagr3 - rf))) * 100)  # CAPM alpha
                if cagr3 is not None and abs(beta) >= 0.05:
                    out["treynor"] = _round((cagr3 - rf) / beta, 3)

    monthly = series.resample("ME").last().pct_change().dropna()
    out["positiveMonthsPct"] = _round((monthly > 0).mean() * 100, 1) if len(monthly) >= 12 else None
    rolling1y = (series / series.shift(252) - 1).dropna()
    out["best1yPct"] = _round(rolling1y.max() * 100)
    out["worst1yPct"] = _round(rolling1y.min() * 100)
    out["riskFreeAssumptionPct"] = rf_pct
    out["explains"] = {
        "cagr3yPct": "Compound annual growth over the last 3 years - the smoothed yearly return.",
        "annVolPct": "How much daily returns swing, annualised. Higher = bumpier ride.",
        "sharpe": f"Extra return per unit of total risk, vs a {rf_pct}% risk-free rate. Above 1 is good.",
        "sortino": "Like Sharpe but only penalises downside swings - kinder to funds that only bounce up.",
        "beta": "Sensitivity to the Nifty 50: beta 1.2 means it tends to move 20% more than the market.",
        "alphaPct": "Return above what its beta alone would predict (CAPM) - the manager's value-add.",
        "treynor": "Extra return per unit of market risk (beta). Compare within the same category.",
        "maxDrawdownPct": "Worst peak-to-bottom fall in the fund's history - the pain test.",
        "positiveMonthsPct": "Share of calendar months with a positive return.",
    }
    return out


def _xirr(cashflows):
    """Annualised internal rate for dated cashflows [(Timestamp, amount)]; invested < 0. Bisection."""
    if len(cashflows) < 2:
        return None
    t0 = cashflows[0][0]

    def npv(rate):
        return sum(cf / (1.0 + rate) ** ((d - t0).days / 365.25) for d, cf in cashflows)

    lo, hi = -0.95, 8.0
    f_lo = npv(lo)
    if f_lo * npv(hi) > 0:
        return None  # no sign change — rate outside sane bounds
    for _ in range(100):
        mid = (lo + hi) / 2.0
        f_mid = npv(mid)
        if abs(f_mid) < 1e-7:
            return mid
        if f_lo * f_mid < 0:
            hi = mid
        else:
            lo, f_lo = mid, f_mid
    return (lo + hi) / 2.0


def _backtest(series, mode, amount, years):
    """What amount invested this way over the past `years` actually became, on real NAVs."""
    if amount <= 0 or years <= 0:
        return {"error": "Amount and period must be positive."}
    end = series.index[-1]
    window = series[series.index >= end - pd.DateOffset(years=years)]
    if len(window) < 40:
        return {"error": "Not enough NAV history for that period."}
    actual_years = (pd.Timestamp(window.index[-1]) - pd.Timestamp(window.index[0])).days / 365.25
    last_nav = float(window.iloc[-1])
    base = {"mode": mode, "periodYears": _round(actual_years, 1),
            "from": str(pd.Timestamp(window.index[0]).date()), "to": str(pd.Timestamp(window.index[-1]).date()),
            "note": "Computed on actual historical NAVs. Past returns do not guarantee future returns."}
    if mode == "lumpsum":
        units = amount / float(window.iloc[0])
        value = units * last_nav
        cagr = (value / amount) ** (1.0 / max(actual_years, 0.1)) - 1.0
        return {**base, "invested": _round(amount, 0), "finalValue": _round(value, 0),
                "absoluteGainPct": _round((value / amount - 1) * 100), "cagrPct": _round(cagr * 100), "xirrPct": None}
    # SIP: buy at the first available NAV on/after each month start
    months = pd.date_range(pd.Timestamp(window.index[0]).normalize(), pd.Timestamp(window.index[-1]), freq="MS")
    flows, units = [], 0.0
    for month_start in months:
        pos = window.index.searchsorted(month_start)
        if pos >= len(window):
            break
        units += amount / float(window.iloc[pos])
        flows.append((pd.Timestamp(window.index[pos]), -float(amount)))
    if len(flows) < 3:
        return {"error": "Period too short for a SIP backtest."}
    invested = amount * len(flows)
    value = units * last_nav
    rate = _xirr(flows + [(pd.Timestamp(window.index[-1]), value)])
    return {**base, "invested": _round(invested, 0), "finalValue": _round(value, 0),
            "absoluteGainPct": _round((value / invested - 1) * 100),
            "xirrPct": _round(rate * 100) if rate is not None else None, "cagrPct": None,
            "installments": len(flows)}


def _fit_score(entry, metrics, risk, horizon):
    """0-100. Documented: 50 base, +20 risk match, +15/-25 horizon fit, Sharpe up to +/-15, deep-drawdown penalty on short horizons."""
    score = 50.0
    if risk in (entry.get("riskLevels") or []):
        score += 20
    years = HORIZON_YEARS.get(horizon, 0)
    if years >= (entry.get("minHorizonYears") or 0):
        score += 15
    else:
        score -= 25
    sharpe = metrics.get("sharpe")
    if sharpe is not None:
        score += max(-15, min(15, sharpe * 10))
    max_dd = metrics.get("maxDrawdownPct")
    if max_dd is not None and max_dd < -35 and years < 5:
        score -= 10
    return int(max(0, min(100, round(score))))


def _verdict(entry, metrics, risk, horizon, fit):
    name = entry.get("category") or "this fund"
    years_label = HORIZON_LABEL.get(horizon, "your")
    min_years = entry.get("minHorizonYears") or 0
    if HORIZON_YEARS.get(horizon, 0) < min_years:
        text = (f"If I were you, I would skip this for a {years_label} goal - {name} really needs "
                f"{min_years}+ years to ride out its swings (worst fall so far: {metrics.get('maxDrawdownPct')}%).")
    elif fit >= 70:
        text = (f"If I were you, I would consider a SIP here: {name} fits a {risk} profile over {years_label}, "
                f"with a Sharpe of {metrics.get('sharpe')} on the last 3 years.")
    elif fit >= 50:
        text = (f"If I were you, I would shortlist it but compare the Sharpe ({metrics.get('sharpe')}) and the "
                f"worst drawdown ({metrics.get('maxDrawdownPct')}%) against category peers before committing.")
    else:
        text = (f"If I were you, I would look elsewhere - the risk-return profile does not fit a {risk} investor "
                f"with a {years_label} horizon.")
    return {"ifIWereYou": text, "fit": fit,
            "fitFormula": "50 base +20 risk-bucket match, +15 horizon ok / -25 too short, Sharpe x10 capped +/-15, -10 if drawdown < -35% on sub-5y horizons",
            "disclaimer": "Educational only, not investment advice."}


def _analyze(code, risk=None, horizon=None):
    uni = _universe()
    meta, series = _fetch_scheme(code)
    bench = None
    bench_code = (uni.get("benchmark") or {}).get("schemeCode")
    if bench_code and int(bench_code) != int(code):
        try:
            _, bench = _fetch_scheme(bench_code)
        except Exception:
            bench = None  # benchmark down -> beta/alpha/treynor stay null
    metrics = compute_metrics(series, bench, uni.get("riskFreeRatePct", 6.5))
    entry = next((f for f in uni.get("funds", []) if int(f.get("schemeCode", -1)) == int(code)), {})
    fit = _fit_score(entry, metrics, risk or "moderate", horizon or "gt5") if entry else None
    payload = {
        "schemeCode": int(code), "name": meta.get("scheme_name"), "fundHouse": meta.get("fund_house"),
        "category": entry.get("category") or meta.get("scheme_category"),
        "curated": bool(entry),
        "manager": entry.get("manager"), "managerSince": entry.get("managerSince"),
        "expenseRatioPct": entry.get("expenseRatioPct"), "aumCr": entry.get("aumCr"),
        "detailsAsOf": uni.get("asOf"), "detailsSource": entry.get("source"),
        "detailsNote": None if entry.get("manager") else "Manager/expense/AUM not verified here - check the AMC page.",
        "holdings": entry.get("holdings"), "holdingsAsOf": entry.get("holdingsAsOf"), "holdingsSource": entry.get("holdingsSource"),
        "holdingsFreshness": _holdings_freshness(entry.get("holdingsAsOf")),
        "allocation": SEBI_ALLOCATION.get(entry.get("bucket")),
        "metrics": metrics,
        "verdict": _verdict(entry or {"category": meta.get("scheme_category"), "minHorizonYears": 0, "riskLevels": []},
                            metrics, risk or "moderate", horizon or "gt5",
                            fit if fit is not None else _fit_score({"riskLevels": [], "minHorizonYears": 0}, metrics, risk or "moderate", horizon or "gt5")),
        "dataSource": "AMFI NAV history via api.mfapi.in",
    }
    return payload


def mount_mflens(app, *, jresp, ai_available, ai_rate_ok, ai_chat, ai_system):
    mf_ai_system = ai_system + (" You are analysing an INDIAN MUTUAL FUND from NAV-history metrics (stated risk-free assumption). "
                                "Expected returns are tendencies, not promises; past returns do not guarantee future ones. "
                                "Every answer is educational research, not investment advice.")

    @app.route("/mf/")
    def mf_page():
        return Response(PAGE, mimetype="text/html")

    @app.route("/mf/api/recommend")
    def mf_recommend():
        risk = request.args.get("risk", "moderate")
        horizon = request.args.get("horizon", "gt5")
        buckets = MATRIX.get((risk, horizon))
        if not buckets:
            return jresp({"error": "risk must be conservative|moderate|aggressive; horizon lt1|1to3|3to5|gt5"}, 400)
        uni = _universe()
        results, warnings = [], []
        invest_mode = request.args.get("mode") if request.args.get("mode") in ("sip", "lumpsum") else None
        try:
            amount = float(request.args.get("amount", 0))
        except ValueError:
            amount = 0
        back_years = {"lt1": 1, "1to3": 3, "3to5": 5, "gt5": 5}[horizon]
        if risk == "aggressive" and horizon == "lt1":
            warnings.append("Equity funds are unsuitable under 1 year even for aggressive investors - showing short-term debt instead.")
        for fund in uni.get("funds", []):
            if fund.get("bucket") not in buckets:
                continue
            try:
                _, series = _fetch_scheme(fund["schemeCode"])
                metrics = compute_metrics(series, None, uni.get("riskFreeRatePct", 6.5))
                fit = _fit_score(fund, metrics, risk, horizon)
                past = _backtest(series, invest_mode, amount, back_years) if invest_mode and amount > 0 else None
                results.append({"schemeCode": fund["schemeCode"], "name": fund["name"], "category": fund["category"],
                                "fundHouse": fund.get("fundHouse"), "cagr3yPct": metrics.get("cagr3yPct"),
                                "sharpe": metrics.get("sharpe"), "maxDrawdownPct": metrics.get("maxDrawdownPct"),
                                "past": None if not past or past.get("error") else past,
                                "fit": fit, "verdictLine": _verdict(fund, metrics, risk, horizon, fit)["ifIWereYou"]})
            except Exception as exc:
                warnings.append(f"{fund.get('name', fund.get('schemeCode'))}: data unavailable ({str(exc)[:80]})")
        results.sort(key=lambda r: -(r["fit"] or 0))
        return jresp({"risk": risk, "horizon": horizon, "buckets": buckets, "funds": results,
                      "warnings": warnings, "asOf": uni.get("asOf"),
                      "disclaimer": "Educational only, not investment advice."})

    @app.route("/mf/api/analyze")
    def mf_analyze():
        code = request.args.get("code", "")
        if not code.isdigit():
            return jresp({"error": "Provide a numeric mfapi scheme code."}, 400)
        try:
            return jresp(_analyze(int(code), request.args.get("risk"), request.args.get("horizon")))
        except Exception as exc:
            return jresp({"error": f"Could not analyse scheme {code}: {str(exc)[:160]}"}, 200)

    @app.route("/mf/api/category")
    def mf_category():
        name = request.args.get("name", "")
        cat = CATEGORIES.get(name)
        if not cat:
            return jresp({"error": "Unknown category. One of: " + ", ".join(CATEGORIES)}, 400)
        uni = _universe()
        results, warnings = [], []
        for fund in uni.get("funds", []):
            if cat["buckets"] is not None and fund.get("bucket") not in cat["buckets"]:
                continue
            try:
                _, series = _fetch_scheme(fund["schemeCode"])
                metrics = compute_metrics(series, None, uni.get("riskFreeRatePct", 6.5))
                results.append({"schemeCode": fund["schemeCode"], "name": fund["name"], "category": fund["category"],
                                "fundHouse": fund.get("fundHouse"), "cagr1yPct": metrics.get("cagr1yPct"),
                                "cagr3yPct": metrics.get("cagr3yPct"), "cagr5yPct": metrics.get("cagr5yPct"),
                                "sharpe": metrics.get("sharpe"), "maxDrawdownPct": metrics.get("maxDrawdownPct")})
            except Exception as exc:
                warnings.append(f"{fund.get('name', fund.get('schemeCode'))}: data unavailable ({str(exc)[:80]})")
        results.sort(key=lambda r: (r["cagr3yPct"] is None, -(r["cagr3yPct"] or 0)))
        return jresp({"category": name, "label": cat["label"], "note": cat["note"], "funds": results,
                      "warnings": warnings, "asOf": uni.get("asOf"),
                      "disclaimer": "Educational only, not investment advice."})

    @app.route("/mf/api/backtest")
    def mf_backtest():
        code = request.args.get("code", "")
        mode = request.args.get("mode", "sip")
        if not code.isdigit() or mode not in ("sip", "lumpsum"):
            return jresp({"error": "Need a numeric scheme code and mode=sip|lumpsum."}, 400)
        try:
            amount = float(request.args.get("amount", 10000))
            years = min(30.0, max(0.5, float(request.args.get("years", 5))))
        except ValueError:
            return jresp({"error": "amount and years must be numbers."}, 400)
        try:
            _, series = _fetch_scheme(int(code))
            return jresp(_backtest(series, mode, amount, years))
        except Exception as exc:
            return jresp({"error": f"Backtest failed: {str(exc)[:160]}"}, 200)

    @app.route("/mf/api/search")
    def mf_search():
        q = (request.args.get("q") or "").strip()
        if len(q) < 3:
            return jresp([])
        try:
            hits = requests.get(f"{MFAPI}/search", params={"q": q}, timeout=10).json()
            return jresp(hits[:15])
        except Exception as exc:
            return jresp({"error": f"Search unavailable: {str(exc)[:120]}"}, 200)

    @app.route("/mf/api/ai", methods=["GET", "POST"])
    def mf_ai():
        off = "AI is off on this deployment. Set ANTHROPIC_API_KEY, or AI_BASE_URL + AI_API_KEY - see README."
        if request.method == "GET":
            return jresp({"enabled": ai_available(), "note": None if ai_available() else off})
        if not ai_available():
            return jresp({"enabled": False, "note": off})
        ip = (request.headers.get("X-Forwarded-For", request.remote_addr or "?")).split(",")[0].strip()
        ok, why = ai_rate_ok(ip)
        if not ok:
            return jresp({"enabled": True, "note": why}, 429)
        body = request.get_json(force=True) or {}
        ctx = json.dumps(body.get("context") or {})[:6500]
        nl = chr(10)
        if body.get("mode") == "bear":
            user = ("Argue ONLY the bear case against this mutual fund for this investor." + nl + nl + "DATA:" + nl + ctx + nl + nl
                    + "Respond (plain text): BEAR THESIS; KEY RISKS (3-4 bullets from the metrics - drawdown, vol, beta, expense); "
                    + "WHAT THE PAST RETURNS HIDE; STRONGEST REASON TO SKIP (one line). Use only the data given.")
        else:
            user = ("Act as my adviser and make a call on this mutual fund for this investor profile." + nl + nl + "DATA:" + nl + ctx + nl + nl
                    + "Respond (plain text): DECISION: SIP / Lumpsum-on-dips / Watchlist / Avoid; THESIS: 2-3 sentences tied to the "
                    + "metrics and the investor's risk+horizon; KEY RISKS: 3 bullets; WHAT WOULD CHANGE MY MIND: 1-2 bullets; "
                    + "CONFIDENCE: low/medium/high + one line. Use only the data given; never invent numbers.")
        try:
            return jresp({"enabled": True, "text": ai_chat(mf_ai_system, user)})
        except Exception as exc:
            return jresp({"enabled": False, "note": "AI request failed - " + str(exc)[:300]}, 502)


GLOSSARY = {
    "NAV": "Net Asset Value - the per-unit price of a fund, published daily.",
    "CAGR": "Compound Annual Growth Rate - the smoothed yearly return between two dates.",
    "Sharpe ratio": "Extra return per unit of total risk (volatility), above the risk-free rate. Above 1 is good, above 2 excellent.",
    "Sortino ratio": "Like Sharpe, but only counts downside volatility - it doesn't punish upside swings.",
    "Treynor ratio": "Extra return per unit of market risk (beta). Useful for comparing funds in the same category.",
    "Beta": "How strongly the fund moves with the market. Beta 1.2 = tends to rise/fall 20% more than Nifty 50.",
    "Alpha": "Return beyond what beta alone would predict - the manager's value-add (CAPM).",
    "Volatility / Std deviation": "How much returns swing around their average, annualised. Higher = bumpier.",
    "Max drawdown": "The worst peak-to-bottom fall in the fund's history. Ask yourself if you could sit through it.",
    "Expense ratio": "The yearly fee the fund deducts, as % of your money. Direct plans charge less than Regular.",
    "AUM": "Assets Under Management - total money in the fund, in Rupees crore.",
    "SIP": "Systematic Investment Plan - investing a fixed amount monthly, which averages your buy price.",
    "ELSS": "Equity Linked Savings Scheme - a tax-saving equity fund (80C) with a 3-year lock-in.",
    "Exit load": "A fee (often 1%) if you redeem before a minimum period - check the scheme document.",
    "Direct vs Regular plan": "Same fund, two prices: Direct skips distributor commission, so returns compound higher.",
    "Growth vs IDCW": "Growth reinvests profits into NAV; IDCW pays them out (taxed) - Growth usually compounds better.",
    "XIRR": "Annualised return that accounts for WHEN each rupee went in - the right measure for SIPs, where every instalment is invested for a different length of time.",
    "Lumpsum": "Investing the full amount at once. Higher timing risk than a SIP, but more time in the market if invested early.",
    "Risk appetite": "How much loss you can absorb without panic-selling - be honest, not brave.",
    "Time horizon": "When you need the money back. Equity needs 5+ years; under 1 year belongs in liquid funds.",
    "GMP (IPO)": "Grey Market Premium - unofficial pre-listing price signal for IPOs. Informational only.",
    "Price band (IPO)": "The min-max range in which you bid for IPO shares.",
    "Oversubscription (IPO)": "How many times demand exceeded the shares on offer (e.g. 11.5x).",
    "P/E ratio": "Price divided by yearly earnings per share - how many years of profit you pay upfront.",
}

PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>MF Lens - mutual fund analyser</title><style>
:root{--ink:#182c3d;--soft:#627486;--line:rgba(36,73,85,.12);--teal:#2b7180;--good:#49b980;--warn:#e7a33d;--bad:#ec6d74}
*{box-sizing:border-box}body{margin:0;font:15px/1.55 Inter,ui-sans-serif,system-ui,"Segoe UI",sans-serif;color:var(--ink);
background:radial-gradient(circle at 8% 5%,#dff5ec 0,transparent 27rem),radial-gradient(circle at 90% 18%,#e5dffc 0,transparent 25rem),linear-gradient(145deg,#f8fbfa,#eaf3f1 62%,#eaf0fb);min-height:100vh}
.wrap{width:min(1100px,calc(100% - 36px));margin:0 auto;padding:26px 0 50px}
h1{margin:0;font-size:clamp(28px,4vw,40px);letter-spacing:-.05em}h1 em{font-style:normal;color:#4fae9e}
.sub{color:var(--soft);font-size:13px;margin:6px 0 0}
.card{border:1px solid #fff;border-radius:18px;padding:18px 20px;background:linear-gradient(125deg,#ffffffb5,#ffffff5e);
box-shadow:-7px -7px 16px #ffffffd9,12px 16px 30px #39605c1a,inset 1px 1px #ffffffb5;backdrop-filter:blur(10px);margin-top:16px}
.lbl{font-size:10px;font-weight:750;letter-spacing:.12em;text-transform:uppercase;color:#668092;margin:0 0 8px}
.chips{display:flex;gap:8px;flex-wrap:wrap}
.chip{padding:9px 15px;border:1px solid var(--line);border-radius:100px;background:#ffffffad;font-size:13px;font-weight:650;cursor:pointer;
box-shadow:-3px -3px 8px #ffffffcc,4px 5px 11px #39605c14;transition:transform .12s,box-shadow .12s}
.chip:active{transform:translateY(1px) scale(.98)}
.chip.on{background:linear-gradient(145deg,#254e64,#2b7880);color:#f7ffff;border-color:transparent;box-shadow:inset 2px 2px 6px #1d485c}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:13px;margin-top:14px}
.fund{border:1px solid var(--line);border-radius:14px;padding:13px 15px;background:#ffffffad;cursor:pointer;
box-shadow:-4px -4px 10px #ffffffcc,5px 7px 14px #39605c14;transition:transform .12s,border-color .15s}
.fund:hover{border-color:#4fae9e;transform:translateY(-2px)}.fund:active{transform:scale(.985)}
.fund b{font-size:13px;display:block;letter-spacing:-.02em}.fund .cat{font-size:11px;color:var(--soft)}
.fund .row{display:flex;gap:12px;margin-top:8px;font-size:12px;color:#3d5866}
.fit{float:right;font-size:11px;font-weight:750;padding:2px 9px;border-radius:100px}
.fit.hi{background:#d8f3e4;color:#1f7a4d}.fit.mid{background:#fdf0d3;color:#8a6412}.fit.lo{background:#fbe0dc;color:#a33d31}
.metric-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px;margin-top:12px}
.metric{border:1px solid var(--line);border-radius:12px;padding:10px 12px;background:#ffffffad;cursor:help}
.metric b{display:block;font-size:17px;letter-spacing:-.03em}.metric span{font-size:10px;color:var(--soft);text-transform:uppercase;letter-spacing:.06em;font-weight:700}
.metric .exp{display:none;font-size:11px;color:#537084;margin-top:6px;line-height:1.45}.metric:hover .exp,.metric:focus .exp{display:block}
.verdict{margin-top:14px;padding:14px 16px;border-radius:13px;background:#e2f2ee;color:#1e4d44;font-size:14px}
.verdict small{display:block;margin-top:6px;color:#5b7f77;font-size:11px}
.search{display:flex;gap:8px;margin-top:10px}
.search input{flex:1;padding:10px 14px;border:1px solid var(--line);border-radius:12px;background:#ffffffad;font:inherit;font-size:13px;outline:0}
.search input:focus{border-color:#4fae9e;box-shadow:0 0 0 3px #6fcdbc33}
.hits{margin-top:8px}.hit{padding:8px 12px;border:1px solid var(--line);border-radius:10px;background:#ffffffad;font-size:12px;cursor:pointer;margin-bottom:6px}
.hit:hover{border-color:#4fae9e}
.mgr{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:10px;margin-top:12px}
.mgr div{border:1px solid var(--line);border-radius:12px;padding:10px 12px;background:#ffffffad}
.mgr span{font-size:10px;color:var(--soft);text-transform:uppercase;letter-spacing:.06em;font-weight:700;display:block}.mgr b{font-size:13px}
select{padding:10px 12px;border:1px solid var(--line);border-radius:12px;background:#ffffffad;font:inherit;font-size:13px;max-width:100%}
.gloss{margin-top:10px;padding:12px 14px;border-radius:12px;background:#eef6ff;color:#31506b;font-size:13px;display:none}
.btn{padding:11px 17px;border:0;border-radius:12px;font:inherit;font-size:13px;font-weight:650;cursor:pointer;
box-shadow:-3px -3px 8px #ffffffcc,4px 5px 11px #39605c1f;transition:transform .12s,box-shadow .12s}
.btn:active{transform:translateY(1px) scale(.985);box-shadow:inset 3px 3px 8px #b9cfc9}
.btn.pri{color:#f7ffff;background:linear-gradient(145deg,#254e64,#2b7880)}.btn.pri:active{box-shadow:inset 4px 4px 10px #1d485c}
.btn.ghost{background:#ffffffad;border:1px solid var(--line);color:var(--ink)}
.ai-out{margin-top:12px;padding:13px 15px;border:1px solid var(--line);border-radius:12px;background:#ffffffa8;font-size:13px;line-height:1.6;white-space:pre-wrap;color:#3d5866}
.warn{margin-top:10px;padding:10px 13px;border-radius:11px;background:#fdf0d3;color:#8a6412;font-size:12px}
.muted{color:var(--soft);font-size:12px}
footer{margin-top:34px;text-align:center;color:#8b9aa5;font-size:11px}
@media(max-width:560px){.metric-grid{grid-template-columns:1fr 1fr}}
</style></head><body><div class="wrap">
<h1>MF <em>Lens</em></h1><p class="sub">Pick your risk and horizon - real AMFI NAV data via mfapi.in, metrics computed live.</p>

<div class="card"><p class="lbl">1 - Your risk appetite</p>
<div class="chips" id="risk"><span class="chip" data-v="conservative">Conservative</span><span class="chip" data-v="moderate">Moderate</span><span class="chip" data-v="aggressive">Aggressive</span></div>
<p class="lbl" style="margin-top:14px">2 - Your time horizon</p>
<div class="chips" id="horizon"><span class="chip" data-v="lt1">Under 1 year</span><span class="chip" data-v="1to3">1-3 years</span><span class="chip" data-v="3to5">3-5 years</span><span class="chip" data-v="gt5">5+ years</span></div>
<p class="lbl" style="margin-top:14px">3 - Investment type &amp; amount</p>
<div style="display:flex;gap:10px;flex-wrap:wrap;align-items:center">
<div class="chips" id="imode"><span class="chip on" data-v="sip">SIP (monthly)</span><span class="chip" data-v="lumpsum">Lumpsum (one-time)</span></div>
<input id="amt" type="number" min="500" step="500" value="10000" style="width:150px;padding:9px 12px;border:1px solid var(--line);border-radius:12px;background:#ffffffad;font:inherit;font-size:13px" title="Amount in rupees">
<span class="muted">Rs - used for the past-returns line on every fund</span></div>
<div style="margin-top:15px;display:flex;gap:11px;align-items:center;flex-wrap:wrap"><button class="btn pri" id="analyseBtn" disabled>&#128269; Analyse my matches</button><span class="muted" id="analyseHint">Pick risk appetite + horizon first</span></div>
<div id="recWrap"><div id="recWarn"></div><div class="grid" id="recs"></div></div></div>

<div class="card"><p class="lbl">Or browse by category - analyst's shortcuts</p>
<div class="chips" id="cats">
<span class="chip" data-v="top-returns">&#127942; Highest past returns</span>
<span class="chip" data-v="largecap">Large cap</span>
<span class="chip" data-v="midcap">Mid cap</span>
<span class="chip" data-v="smallcap">Small cap</span>
<span class="chip" data-v="flexicap">Flexi cap</span>
<span class="chip" data-v="hybrid">Hybrid</span>
<span class="chip" data-v="debt">Debt funds</span>
<span class="chip" data-v="elss">ELSS (tax saver)</span>
</div><p class="muted" style="margin-top:8px">One click lists the category with live 1y/3y/5y CAGR, Sharpe and worst drawdown - results appear above.</p></div>

<div class="card"><p class="lbl">Or analyse any fund</p>
<div class="search"><input id="q" placeholder="Type 3+ letters, e.g. 'Quant Small Cap direct'..." autocomplete="off"></div>
<div class="hits" id="hits"></div></div>

<div class="card" id="detail" style="display:none">
<p class="lbl" id="dCat">-</p><h2 id="dName" style="margin:2px 0 0;font-size:20px;letter-spacing:-.04em">-</h2>
<p class="muted" id="dHouse"></p>
<div class="metric-grid" id="dMetrics"></div>
<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(270px,1fr));gap:16px;margin-top:18px">
<div><p class="lbl">Top holdings (donut)</p><div id="dHold"></div><p class="muted" id="dHoldNote"></p></div>
<div><p class="lbl">Instrument allocation (pie)</p><div id="dAlloc"></div><p class="muted" id="dAllocNote"></p></div>
</div>
<p class="lbl" style="margin-top:18px">Past returns calculator</p>
<div style="display:flex;gap:10px;flex-wrap:wrap;align-items:center">
<div class="chips" id="cmode"><span class="chip on" data-v="sip">SIP</span><span class="chip" data-v="lumpsum">Lumpsum</span></div>
<input id="camt" type="number" min="500" step="500" value="10000" style="width:130px;padding:9px 12px;border:1px solid var(--line);border-radius:12px;background:#ffffffad;font:inherit;font-size:13px">
<select id="cyears"><option value="1">Past 1 year</option><option value="3">Past 3 years</option><option value="5" selected>Past 5 years</option><option value="10">Past 10 years</option></select>
<button class="btn pri" id="calcBtn">Calculate</button></div>
<div id="calcOut" style="margin-top:10px"></div>
<p class="lbl" style="margin-top:16px">Fund house &amp; management</p>
<div class="mgr" id="dMgr"></div><p class="muted" id="dMgrNote"></p>
<div class="verdict" id="dVerdict"></div>
<div id="aiPanel" style="display:none;margin-top:14px"><p class="lbl">AI analyst read</p>
<p class="muted">Reasons over the computed metrics - it won't invent figures. Educational only.</p>
<div style="display:flex;gap:9px;margin-top:9px"><button class="btn pri" id="aiGo">Ask the AI analyst</button><button class="btn ghost" id="aiBear">Bear case</button></div>
<div class="ai-out" id="aiOut" style="display:none"></div></div></div>

<div class="card"><p class="lbl">&#128214; Explain a term</p>
<select id="gsel"><option value="">Pick any term used on this platform...</option></select>
<div class="gloss" id="gout"></div></div>

<footer>Educational only - not investment advice. Data: AMFI via mfapi.in; metrics computed from NAV history. Past returns do not guarantee future returns.</footer>
</div><script>
const $=s=>document.querySelector(s);let risk=null,horizon=null,current=null,imode='sip',amount=10000;
const PAL=['#2b7180','#4fae9e','#9c91e1','#e7a33d','#6c9bd1','#ec6d74','#85cfc0'];
function ring(slices,hole){ // slices [[label,pct],...] -> svg ring; hole=0 gives a solid pie
  const R=25,C=2*Math.PI*R,SW=hole?16:50;let off=0,segs='';
  const total=slices.reduce((a,s)=>a+s[1],0)||100;
  slices.forEach((s,i)=>{const len=s[1]/total*C;
    segs+=`<circle cx="50" cy="50" r="${R}" fill="none" stroke="${PAL[i%PAL.length]}" stroke-width="${SW}" stroke-dasharray="${len.toFixed(2)} ${(C-len).toFixed(2)}" stroke-dashoffset="${(-off).toFixed(2)}" transform="rotate(-90 50 50)"/>`;off+=len;});
  const legend=slices.map((s,i)=>`<span style="display:flex;align-items:center;gap:6px;font-size:11.5px;color:#3d5866"><i style="width:10px;height:10px;border-radius:3px;background:${PAL[i%PAL.length]}"></i>${esc(s[0])} - ${s[1]}%</span>`).join('');
  return `<div style="display:flex;gap:14px;align-items:center;flex-wrap:wrap"><svg viewBox="0 0 100 100" style="width:130px;height:130px;flex:0 0 auto">${segs}</svg><div style="display:flex;flex-direction:column;gap:5px">${legend}</div></div>`;
}
const GLOSSARY=__GLOSSARY__;
const sel=$('#gsel');Object.keys(GLOSSARY).forEach(k=>{const o=document.createElement('option');o.value=k;o.textContent=k;sel.appendChild(o)});
sel.onchange=()=>{const g=$('#gout');if(sel.value){g.style.display='block';g.textContent=GLOSSARY[sel.value]}else g.style.display='none'};
function chips(id,cb){document.querySelectorAll('#'+id+' .chip').forEach(c=>c.onclick=()=>{document.querySelectorAll('#'+id+' .chip').forEach(x=>x.classList.remove('on'));c.classList.add('on');cb(c.dataset.v)})}
chips('risk',v=>{risk=v;syncAnalyse()});chips('horizon',v=>{horizon=v;syncAnalyse()});
chips('imode',v=>{imode=v});
$('#amt').addEventListener('change',()=>{amount=Math.max(0,Number($('#amt').value)||0)});
function syncAnalyse(){const ok=!!(risk&&horizon);$('#analyseBtn').disabled=!ok;$('#analyseHint').textContent=ok?'Ready - metrics compute live from NAV history when you click':'Pick risk appetite + horizon first'}
$('#analyseBtn').onclick=()=>maybeRecommend();
chips('cats',v=>loadCategory(v));
async function loadCategory(name){$('#recs').innerHTML='<p class="muted">Computing category metrics from NAV history...</p>';$('#recWarn').innerHTML='';
try{const r=await(await fetch(`/mf/api/category?name=${encodeURIComponent(name)}`)).json();
if(r.error){$('#recWarn').innerHTML=`<div class="warn">${esc(r.error)}</div>`;$('#recs').innerHTML='';return}
$('#recWarn').innerHTML=`<div class="warn" style="background:#eef6ff;color:#31506b">&#128202; <b>${esc(r.label)}</b> &middot; ${esc(r.note)}</div>`+(r.warnings||[]).map(w=>`<div class="warn">${esc(w)}</div>`).join('');
$('#recs').innerHTML=(r.funds||[]).map(f=>`<div class="fund" data-code="${f.schemeCode}">
<b>${esc(f.name)}</b><span class="cat">${esc(f.category||'')} - ${esc(f.fundHouse||'')}</span>
<div class="row"><span>CAGR 1y <b>${fmt(f.cagr1yPct)}${f.cagr1yPct==null?'':'%'}</b></span><span>3y <b>${fmt(f.cagr3yPct)}${f.cagr3yPct==null?'':'%'}</b></span><span>5y <b>${fmt(f.cagr5yPct)}${f.cagr5yPct==null?'':'%'}</b></span></div>
<div class="row"><span>Sharpe <b>${fmt(f.sharpe)}</b></span><span>Max DD <b>${fmt(f.maxDrawdownPct)}${f.maxDrawdownPct==null?'':'%'}</b></span></div></div>`).join('')||'<p class="muted">No funds in this category.</p>';
document.querySelectorAll('.fund').forEach(el=>el.onclick=()=>analyze(el.dataset.code));}
catch(e){$('#recs').innerHTML='<p class="warn">Could not load the category - is the network up?</p>'}}
function money(v){return 'Rs '+Number(v).toLocaleString('en-IN')}
async function maybeRecommend(){if(!risk||!horizon)return;$('#recs').innerHTML='<p class="muted">Fetching NAV histories and computing metrics...</p>';$('#recWarn').innerHTML='';
try{const qs=new URLSearchParams({risk,horizon});if(imode&&amount>0){qs.set('mode',imode);qs.set('amount',amount)}
const r=await(await fetch(`/mf/api/recommend?`+qs)).json();
if(r.warnings&&r.warnings.length)$('#recWarn').innerHTML=r.warnings.map(w=>`<div class="warn">${esc(w)}</div>`).join('');
$('#recs').innerHTML=(r.funds||[]).map(f=>`<div class="fund" data-code="${f.schemeCode}">
<span class="fit ${f.fit>=70?'hi':f.fit>=50?'mid':'lo'}">fit ${f.fit}</span><b>${esc(f.name)}</b><span class="cat">${esc(f.category||'')} - ${esc(f.fundHouse||'')}</span>
<div class="row"><span>CAGR 3y <b>${fmt(f.cagr3yPct)}%</b></span><span>Sharpe <b>${fmt(f.sharpe)}</b></span><span>Max DD <b>${fmt(f.maxDrawdownPct)}%</b></span></div>
${f.past?`<div class="row" style="margin-top:5px;color:#1e4d44"><span>${imode==='sip'?money(amount)+'/mo SIP':money(amount)+' lumpsum'} past ${f.past.periodYears}y &rarr; <b>${money(f.past.finalValue)}</b> (${f.past.xirrPct!=null?f.past.xirrPct+'% XIRR':f.past.cagrPct+'% CAGR'})</span></div>`:''}</div>`).join('')||'<p class="muted">No funds matched.</p>';
document.querySelectorAll('.fund').forEach(el=>el.onclick=()=>analyze(el.dataset.code));}
catch(e){$('#recs').innerHTML='<p class="warn">Could not fetch recommendations - is the network up?</p>'}}
let t;$('#q').addEventListener('input',()=>{clearTimeout(t);const q=$('#q').value.trim();if(q.length<3){$('#hits').innerHTML='';return}
t=setTimeout(async()=>{try{const h=await(await fetch(`/mf/api/search?q=${encodeURIComponent(q)}`)).json();
$('#hits').innerHTML=(Array.isArray(h)?h:[]).map(x=>`<div class="hit" data-code="${x.schemeCode}">${esc(x.schemeName)}</div>`).join('');
document.querySelectorAll('.hit').forEach(el=>el.onclick=()=>{$('#hits').innerHTML='';analyze(el.dataset.code)});}catch(e){}},350)});
async function analyze(code){$('#detail').style.display='block';$('#dName').textContent='Analysing...';$('#dMetrics').innerHTML='';$('#aiOut').style.display='none';$('#aiOut').textContent='';
window.scrollTo({top:$('#detail').offsetTop-20,behavior:'smooth'});
const qs=new URLSearchParams({code});if(risk)qs.set('risk',risk);if(horizon)qs.set('horizon',horizon);
const d=await(await fetch(`/mf/api/analyze?`+qs)).json();if(d.error){$('#dName').textContent=d.error;return}
current=d;$('#dCat').textContent=(d.category||'Fund')+(d.curated?' - curated universe':'');$('#dName').textContent=d.name||('Scheme '+code);
$('#dHouse').textContent=(d.fundHouse||'')+' - NAV '+fmt(d.metrics.latestNav)+' ('+(d.metrics.navDate||'')+') - source: '+d.dataSource;
const m=d.metrics,E=m.explains||{};
const cells=[['CAGR 1y',m.cagr1yPct,'%','cagr3yPct'],['CAGR 3y',m.cagr3yPct,'%','cagr3yPct'],['CAGR 5y',m.cagr5yPct,'%','cagr3yPct'],
['Volatility',m.annVolPct,'%','annVolPct'],['Sharpe',m.sharpe,'','sharpe'],['Sortino',m.sortino,'','sortino'],
['Beta',m.beta,'','beta'],['Alpha',m.alphaPct,'%','alphaPct'],['Treynor',m.treynor,'','treynor'],
['Max drawdown',m.maxDrawdownPct,'%','maxDrawdownPct'],['Positive months',m.positiveMonthsPct,'%','positiveMonthsPct'],
['Best 1y',m.best1yPct,'%',''],['Worst 1y',m.worst1yPct,'%','']];
$('#dMetrics').innerHTML=cells.map(([l,v,u,ek])=>`<div class="metric" tabindex="0"><span>${l}</span><b>${fmt(v)}${v==null?'':u}</b>${ek&&E[ek]?`<div class="exp">${esc(E[ek])}</div>`:''}</div>`).join('');
$('#dMgr').innerHTML=[['Fund manager',d.manager],['Manager since',d.managerSince],['Expense ratio (Direct)',d.expenseRatioPct==null?null:d.expenseRatioPct+'%'],['AUM',d.aumCr==null?null:'Rs '+Number(d.aumCr).toLocaleString('en-IN')+' Cr'],['Details as of',d.detailsAsOf],['Risk-free assumed',m.riskFreeAssumptionPct+'%']]
.map(([k,v])=>`<div><span>${k}</span><b>${v==null?'&mdash;':esc(String(v))}</b></div>`).join('');
$('#dMgrNote').textContent=d.detailsNote||'';
if(d.holdings&&d.holdings.length){const sum=d.holdings.reduce((a,h)=>a+h.pct,0);const fr=d.holdingsFreshness;
$('#dHold').innerHTML=(fr?`<div style="margin-bottom:8px"><span class="fit ${fr.stale?'lo':'hi'}" style="float:none">${esc(fr.asOf)} &middot; ${esc(fr.label)}</span></div>`:'')
+ring(d.holdings.map(h=>[h.name,h.pct]).concat([["Rest of portfolio",Math.round((100-sum)*100)/100]]),1);
$('#dHoldNote').innerHTML=(fr?esc(fr.note)+' ':'')+(d.holdingsSource?`<a href="${esc(d.holdingsSource)}" target="_blank" rel="noreferrer">Verify / see the full portfolio &#8599;</a>`:'Full portfolio in the AMC factsheet.');}
else{$('#dHold').innerHTML='<p class="muted">Holdings not loaded for this scheme - see the AMC factsheet for the live portfolio.</p>';$('#dHoldNote').textContent='';}
if(d.allocation){$('#dAlloc').innerHTML=ring(d.allocation.slices,0);$('#dAllocNote').textContent=d.allocation.label+' - regulatory envelope, not the live portfolio.';}
else{$('#dAlloc').innerHTML='<p class="muted">Category mandate unavailable for non-curated schemes.</p>';$('#dAllocNote').textContent='';}
$('#camt').value=amount||10000;
$('#dVerdict').innerHTML=esc(d.verdict.ifIWereYou)+`<small>Fit ${d.verdict.fit}/100 - ${esc(d.verdict.fitFormula)}. ${esc(d.verdict.disclaimer)}</small>`;initAi();}
let cm='sip';chips('cmode',v=>{cm=v});
$('#calcBtn').onclick=async()=>{if(!current)return;const o=$('#calcOut');o.innerHTML='<p class="muted">Replaying historical NAVs...</p>';
try{const r=await(await fetch(`/mf/api/backtest?code=${current.schemeCode}&mode=${cm}&amount=${Number($('#camt').value)||10000}&years=${$('#cyears').value}`)).json();
if(r.error){o.innerHTML=`<div class="warn">${esc(r.error)}</div>`;return}
const rate=r.xirrPct!=null?`${r.xirrPct}% XIRR`:`${r.cagrPct}% CAGR`;
o.innerHTML=`<div class="verdict" style="background:#eef6ff;color:#31506b">Invested <b>${money(r.invested)}</b>${r.installments?` (${r.installments} monthly instalments)`:''} from ${r.from} to ${r.to} &rarr; worth <b>${money(r.finalValue)}</b> today. Absolute gain <b>${r.absoluteGainPct}%</b>, annualised <b>${rate}</b>.<small>${esc(r.note)}</small></div>`;}
catch(e){o.innerHTML='<div class="warn">Backtest failed - try again.</div>'}};
async function initAi(){try{const r=await(await fetch('/mf/api/ai')).json();$('#aiPanel').style.display=r.enabled?'block':'none'}catch(e){$('#aiPanel').style.display='none'}}
async function askAi(mode){const o=$('#aiOut');o.style.display='block';o.textContent=mode==='bear'?'Building the bear case...':'Reading the metrics...';
try{const r=await(await fetch('/mf/api/ai',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({mode,context:{investor:{risk,horizon},fund:current}})})).json();
o.textContent=r.text||r.note||'No response.'}catch(e){o.textContent='AI request failed.'}}
$('#aiGo').onclick=()=>askAi('mf');$('#aiBear').onclick=()=>askAi('bear');
function fmt(v){return v==null?'&mdash;':(typeof v==='number'?v.toLocaleString('en-IN'):v)}
function esc(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
</script></body></html>"""
PAGE = PAGE.replace("__GLOSSARY__", json.dumps(GLOSSARY))


def _selftest():
    """Offline math checks with synthetic series; optional live smoke."""
    idx = pd.bdate_range("2019-01-01", "2025-01-01")
    steady = pd.Series(100.0 * (1.12 ** (np.arange(len(idx)) / 252.0)), index=idx)  # 12%/yr, zero noise
    m = compute_metrics(steady, None, 6.5)
    assert abs(m["cagr3yPct"] - 12.0) < 0.6, f"CAGR3 {m['cagr3yPct']} != ~12"
    assert m["maxDrawdownPct"] == 0.0, f"monotonic series must have 0 drawdown, got {m['maxDrawdownPct']}"
    assert m["positiveMonthsPct"] == 100.0, "steady growth must be positive every month"

    m2 = compute_metrics(steady, steady, 6.5)
    assert m2["beta"] is None or abs(m2["beta"] - 1.0) < 0.05, f"beta vs self {m2['beta']} != ~1"

    rng = np.random.default_rng(7)
    noisy = pd.Series(100.0 * np.cumprod(1 + rng.normal(0.0004, 0.01, len(idx))), index=idx)
    m3 = compute_metrics(noisy, None, 6.5)
    assert m3["annVolPct"] and m3["annVolPct"] > 5, "noisy series must show volatility"
    assert m3["maxDrawdownPct"] < 0, "noisy series must have a drawdown"

    fit_hi = _fit_score({"riskLevels": ["moderate"], "minHorizonYears": 3}, {"sharpe": 1.5, "maxDrawdownPct": -20}, "moderate", "gt5")
    fit_lo = _fit_score({"riskLevels": ["aggressive"], "minHorizonYears": 7}, {"sharpe": -0.5, "maxDrawdownPct": -50}, "conservative", "lt1")
    assert fit_hi >= 85 and fit_lo <= 15, f"fit ordering broken: {fit_hi} vs {fit_lo}"
    assert MATRIX[("aggressive", "gt5")] == ["midcap", "smallcap", "flexicap"]
    v = _verdict({"category": "Mid Cap", "minHorizonYears": 5}, {"sharpe": 1.2, "maxDrawdownPct": -30}, "aggressive", "lt1", 20)
    assert v["ifIWereYou"].startswith("If I were you"), "verdict must use the required phrasing"
    # Backtest & XIRR: on a perfectly steady 12%/yr series, lumpsum CAGR and SIP XIRR must both read ~12%.
    known = _xirr([(pd.Timestamp("2020-01-01"), -1000.0), (pd.Timestamp("2021-01-01"), 1100.0)])
    assert known is not None and abs(known - 0.10) < 0.005, f"xirr of 1000->1100 in 1y = {known} != ~10%"
    lump = _backtest(steady, "lumpsum", 100000, 3)
    assert abs(lump["cagrPct"] - 12.0) < 0.7, f"lumpsum backtest CAGR {lump['cagrPct']} != ~12"
    sip = _backtest(steady, "sip", 10000, 3)
    assert sip["installments"] >= 34 and abs(sip["xirrPct"] - 12.0) < 1.2, f"SIP backtest {sip} != ~12% XIRR"
    assert sip["invested"] == 10000 * sip["installments"], "SIP invested must equal amount x instalments"
    short = _backtest(steady, "sip", 10000, 0)
    assert "error" in short, "zero-year backtest must return an error, not crash"
    assert SEBI_ALLOCATION["elss"]["slices"][0][1] + SEBI_ALLOCATION["elss"]["slices"][1][1] == 100, "allocation slices must sum to 100"

    # Category browser: every mapped bucket must exist, and the None-safe sort puts missing CAGRs last.
    valid_buckets = {"debt-liquid", "debt-short", "hybrid-conservative", "hybrid-aggressive",
                     "largecap-index", "flexicap", "midcap", "smallcap", "elss"}
    for key, cat in CATEGORIES.items():
        assert cat["buckets"] is None or all(b in valid_buckets for b in cat["buckets"]), f"bad bucket in {key}"
        assert cat.get("note"), f"category {key} needs an analyst note"
    assert CATEGORIES["top-returns"]["buckets"] is None
    ordered = sorted([{"cagr3yPct": None}, {"cagr3yPct": 5.0}, {"cagr3yPct": 12.0}],
                     key=lambda r: (r["cagr3yPct"] is None, -(r["cagr3yPct"] or 0)))
    assert [r["cagr3yPct"] for r in ordered] == [12.0, 5.0, None], "category sort must rank None last"

    # Holdings freshness ages against the real clock, so a snapshot can never silently look current.
    today = date(2026, 7, 16)
    assert _holdings_freshness("Jul 2026", today)["monthsOld"] == 0
    assert _holdings_freshness("Jul 2026", today)["stale"] is False
    assert _holdings_freshness("Jun 2026", today)["label"] == "1 month old"
    assert _holdings_freshness("Jun 2026", today)["stale"] is False, "1 month is within the disclosure cycle"
    jan = _holdings_freshness("31 Jan 2026 (AMC factsheet)", today)
    assert jan["monthsOld"] == 6 and jan["stale"] is True, f"Jan snapshot must read 6 months stale, got {jan}"
    assert _holdings_freshness("10 Jul 2026 (Nifty 50 index weights)", today)["monthsOld"] == 0
    assert _holdings_freshness(None) is None and _holdings_freshness("garbage") is None
    assert _holdings_freshness("Jan 2026", date(2027, 1, 1))["monthsOld"] == 12, "must age across year boundaries"
    print("mflens selftest: all offline math checks passed")
    try:
        payload = _analyze(120716)
        print(f"live smoke: {payload['name']} CAGR3y={payload['metrics']['cagr3yPct']}% Sharpe={payload['metrics']['sharpe']}")
    except Exception as exc:
        print(f"live smoke skipped (offline?): {exc}")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        _selftest()
    else:
        print("Usage: python mflens.py selftest  (module is mounted by fundapilot.py)")
