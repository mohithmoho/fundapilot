"""
FundaPilot — institutional-grade equity research & portfolio optimization (single file).
AI-powered valuation and portfolio analytics platform. EDUCATIONAL ONLY.

Run:  pip install flask yfinance pandas numpy requests
      python fundapilot.py    ->  open http://localhost:5000
Self-check:  python fundapilot.py selftest

Data: Yahoo Finance (prices, fundamentals, statements, holders, corporate
actions) + Google News RSS (live news & sector sentiment). No API keys, no paid
services. Every computed number is shown with the raw inputs and formula (the
"proof" panel) so you can verify it. Money is shown in the company's own
currency AND converted to INR. EDUCATIONAL ONLY — not investment advice.
"""
import os
import sys
import re
import math
import json
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from functools import lru_cache
from collections import deque
import requests
import numpy as np
import pandas as pd
import yfinance as yf
from flask import Flask, request, Response

app = Flask(__name__)
UA = {"User-Agent": "Mozilla/5.0 (FundaPilot educational)"}

_TICKER_BAD = re.compile(r"[^A-Za-z0-9.\-^=&]")


def clean_ticker(s, n=24):
    """Whitelist ticker chars and cap length — defense-in-depth before values reach yfinance/URLs."""
    return _TICKER_BAD.sub("", (s or "").strip())[:n].upper()


# tiny in-memory per-IP rate limiter (no dependency). ponytail: per-worker window; fine for 1 worker
# on Render free. Swap for Redis/flask-limiter if you scale to multiple workers.
_RL = {}


@app.before_request
def _rate_limit():
    ip = (request.headers.get("X-Forwarded-For", request.remote_addr or "?")).split(",")[0].strip()
    now = time.time()
    dq = _RL.setdefault(ip, deque())
    while dq and dq[0] < now - 60:
        dq.popleft()
    if len(dq) >= 150:               # generous: normal use (autocomplete + 60s polling) stays well under
        return Response('{"error":"Too many requests — slow down."}', status=429, mimetype="application/json")
    dq.append(now)
    if len(_RL) > 5000:              # bound memory
        for k in list(_RL)[:1000]:
            if not _RL[k]:
                _RL.pop(k, None)


@app.after_request
def _sec_headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "SAMEORIGIN"
    resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    resp.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    return resp

RISK = {"conservative": {"discount": 0.13, "max_weight": 0.05, "proj_years": 8},
        "medium":       {"discount": 0.12, "max_weight": 0.10, "proj_years": 8},
        "aggressive":   {"discount": 0.11, "max_weight": 0.20, "proj_years": 10}}
HORIZON_YEARS = {"short": 5, "medium": 8, "long": 10}

DIMS = ["Valuation", "Profitability", "Growth", "Financial health", "Momentum", "Valuation (P/E)"]
STYLE_WEIGHTS = {
    "balanced": {d: 1 for d in DIMS},
    "buffett":  {"Profitability": 2.5, "Financial health": 2, "Valuation": 1.5, "Valuation (P/E)": 1.5, "Growth": 1, "Momentum": 0.3},
    "growth":   {"Growth": 3, "Profitability": 1.5, "Momentum": 1.5, "Valuation (P/E)": 0.5, "Valuation": 0.7, "Financial health": 1},
    "value":    {"Valuation": 2.5, "Valuation (P/E)": 2.5, "Financial health": 1.5, "Profitability": 1, "Growth": 0.5, "Momentum": 0.3},
    "dividend": {"Financial health": 2, "Profitability": 2, "Valuation": 1.5, "Valuation (P/E)": 1, "Growth": 0.7, "Momentum": 0.3},
    "momentum": {"Momentum": 3, "Growth": 2, "Profitability": 1, "Valuation": 0.5, "Valuation (P/E)": 0.5, "Financial health": 0.7}}
STYLE_LABEL = {"balanced": "Balanced", "buffett": "Warren Buffett (quality/value)", "growth": "Growth (Lynch-style)",
               "value": "Deep Value", "dividend": "Dividend / Defensive", "momentum": "Momentum / Trend"}

# Benchmark each metric so a non-finance person can read it.
# higher_better: True -> bigger is better. (green_cut, amber_cut) split good/ok/poor.
BENCH = {
    "P/E (trailing)":   {"unit": "x", "hb": False, "cut": (20, 35), "what": "Price you pay for ₹1 of yearly profit. Lower = cheaper."},
    "P/E (forward)":    {"unit": "x", "hb": False, "cut": (20, 35), "what": "Same as P/E but on next year's expected profit."},
    "P/B":              {"unit": "x", "hb": False, "cut": (3, 6), "what": "Price vs net assets (book value). Lower = cheaper on assets."},
    "PEG":              {"unit": "x", "hb": False, "cut": (1, 2), "what": "P/E adjusted for growth. ~1 is fairly priced."},
    "ROE %":            {"unit": "%", "hb": True, "cut": (15, 10), "what": "Profit earned on shareholders' money. Higher = more efficient."},
    "Operating margin %": {"unit": "%", "hb": True, "cut": (15, 8), "what": "Profit from core operations per ₹100 of sales."},
    "Net/PAT margin %": {"unit": "%", "hb": True, "cut": (10, 5), "what": "Final profit kept per ₹100 of sales."},
    "Debt/Equity %":    {"unit": "%", "hb": False, "cut": (60, 120), "what": "Debt vs own capital. Below 60% is comfortable."},
    "Current ratio":    {"unit": "x", "hb": True, "cut": (1.5, 1.0), "what": "Short-term assets ÷ short-term dues. Above 1 can pay bills."},
    "Revenue growth %": {"unit": "%", "hb": True, "cut": (12, 5), "what": "How fast sales are growing year-on-year."},
    "Earnings growth %": {"unit": "%", "hb": True, "cut": (15, 5), "what": "How fast profit is growing year-on-year."},
    "Dividend yield %": {"unit": "%", "hb": True, "cut": (2, 0.7), "what": "Cash dividend as % of price. Higher = more income."},
    "Beta":             {"unit": "", "hb": None, "cut": None, "what": "Volatility vs market. <1 calmer than market, >1 swings more."},
    "EV/EBITDA":        {"unit": "x", "hb": False, "cut": (12, 20), "what": "Enterprise value vs cash operating profit. Capital-structure-neutral — the multiples desk's go-to. Lower = cheaper."},
    "FCF yield %":      {"unit": "%", "hb": True, "cut": (5, 2), "what": "Free cash flow ÷ market cap. The cash return the business throws off — >5% is attractive."},
    "P/S":              {"unit": "x", "hb": False, "cut": (3, 6), "what": "Price ÷ sales. Useful when earnings are thin; lower = cheaper on revenue."},
    "ROCE %":           {"unit": "%", "hb": True, "cut": (15, 10), "what": "Return on capital employed = EBIT ÷ (assets − current liabilities). Capital efficiency incl. debt — better than ROE."},
    "ROIC %":           {"unit": "%", "hb": True, "cut": (12, 8), "what": "Return on invested capital. >cost of capital (~10-12%) means the firm creates value."},
    "Interest coverage": {"unit": "x", "hb": True, "cut": (5, 2.5), "what": "EBIT ÷ interest expense. How easily it services debt; <2.5 is fragile."},
    "Revenue CAGR 3y %": {"unit": "%", "hb": True, "cut": (12, 5), "what": "3-year compound sales growth — durable trend, not one noisy year."},
    "Earnings CAGR 3y %": {"unit": "%", "hb": True, "cut": (12, 5), "what": "3-year compound profit growth."},
    "Altman Z":         {"unit": "", "hb": True, "cut": (3, 1.81), "what": "Bankruptcy-risk score. >2.99 safe, 1.81–2.99 grey zone, <1.81 distress."},
    "Piotroski F":      {"unit": "/9", "hb": True, "cut": (7, 4), "what": "9-point fundamental-quality checklist (profitability, leverage, efficiency). 7-9 strong, ≤3 weak."},
}
MONEY_KEYS = {"EBITDA", "PAT (net income)", "Free cash flow"}

NEG = ["fraud", "scam", "scandal", "probe", "raid", "default", "downgrade", "fine", "penalty", "lawsuit",
       "resign", "insider", "sebi", "bribery", "investigation", "fall", "plunge", "crash", "loss", "cut", "weak", "slump"]
POS = ["record", "profit", "surge", "high", "order", "win", "wins", "growth", "upgrade", "beat",
       "expansion", "dividend", "bonus", "rally", "jump", "deal", "contract", "approval"]

# ponytail: Nifty sectoral-index constituents + liquid names. Extend freely.
UNIVERSE = {
    "India": {
        "Defense": ["HAL.NS", "BEL.NS", "BDL.NS", "MAZDOCK.NS", "COCHINSHIP.NS", "DATAPATTNS.NS", "SOLARINDS.NS", "ZENTEC.NS", "PARAS.NS", "MTARTECH.NS", "BEML.NS", "GRSE.NS", "ASTRAMICRO.NS"],
        "IT / Software": ["TCS.NS", "INFY.NS", "WIPRO.NS", "HCLTECH.NS", "TECHM.NS", "LTIM.NS", "PERSISTENT.NS", "COFORGE.NS", "MPHASIS.NS", "LTTS.NS", "OFSS.NS", "KPITTECH.NS", "TATAELXSI.NS", "BSOFT.NS"],
        "Banking": ["HDFCBANK.NS", "ICICIBANK.NS", "SBIN.NS", "KOTAKBANK.NS", "AXISBANK.NS", "INDUSINDBK.NS", "BANKBARODA.NS", "PNB.NS", "FEDERALBNK.NS", "IDFCFIRSTB.NS", "AUBANK.NS", "BANDHANBNK.NS", "CANBK.NS"],
        "Pharma": ["SUNPHARMA.NS", "CIPLA.NS", "DRREDDY.NS", "DIVISLAB.NS", "LUPIN.NS", "AUROPHARMA.NS", "ALKEM.NS", "TORNTPHARM.NS", "ZYDUSLIFE.NS", "GLENMARK.NS", "BIOCON.NS", "MANKIND.NS", "LAURUSLABS.NS"],
        "Auto": ["TATAMOTORS.NS", "M&M.NS", "MARUTI.NS", "BAJAJ-AUTO.NS", "EICHERMOT.NS", "TVSMOTOR.NS", "HEROMOTOCO.NS", "ASHOKLEY.NS", "BHARATFORG.NS", "MOTHERSON.NS", "BOSCHLTD.NS", "BALKRISIND.NS"],
        "FMCG": ["HINDUNILVR.NS", "ITC.NS", "NESTLEIND.NS", "BRITANNIA.NS", "DABUR.NS", "VBL.NS", "TATACONSUM.NS", "GODREJCP.NS", "MARICO.NS", "COLPAL.NS", "UBL.NS", "PGHH.NS", "EMAMILTD.NS"],
        "Energy / Power": ["RELIANCE.NS", "ONGC.NS", "NTPC.NS", "POWERGRID.NS", "COALINDIA.NS", "TATAPOWER.NS", "ADANIGREEN.NS", "ADANIPOWER.NS", "JSWENERGY.NS", "IOC.NS", "BPCL.NS", "GAIL.NS", "NHPC.NS"],
        "Metals": ["TATASTEEL.NS", "JSWSTEEL.NS", "HINDALCO.NS", "VEDL.NS", "JINDALSTEL.NS", "NMDC.NS", "SAIL.NS", "HINDZINC.NS", "NATIONALUM.NS", "JINDALSAW.NS", "APLAPOLLO.NS"],
        "Infra / Capital Goods": ["LT.NS", "SIEMENS.NS", "ABB.NS", "BHEL.NS", "CUMMINSIND.NS", "THERMAX.NS", "HAVELLS.NS", "POLYCAB.NS", "KEI.NS", "AIAENG.NS", "SUZLON.NS", "GMRINFRA.NS"],
        "Railways": ["IRCTC.NS", "IRFC.NS", "RVNL.NS", "IRCON.NS", "TITAGARH.NS", "JWL.NS", "RAILTEL.NS", "RITES.NS", "TEXRAIL.NS", "CONCOR.NS"],
        "Realty": ["DLF.NS", "GODREJPROP.NS", "OBEROIRLTY.NS", "PHOENIXLTD.NS", "PRESTIGE.NS", "BRIGADE.NS", "LODHA.NS", "SOBHA.NS"],
    },
    "USA": {
        "Technology": ["AAPL", "MSFT", "GOOGL", "META", "ORCL", "CRM", "ADBE", "NOW", "IBM"],
        "Semiconductors": ["NVDA", "AMD", "INTC", "AVGO", "TSM", "MU", "QCOM", "TXN", "ASML"],
        "EV / Auto": ["TSLA", "F", "GM", "RIVN", "LCID", "TM", "STLA"],
        "Finance": ["JPM", "BAC", "GS", "MS", "WFC", "C", "BLK", "SCHW"],
        "Healthcare": ["JNJ", "PFE", "UNH", "MRK", "ABBV", "LLY", "TMO", "AMGN"],
        "Defense": ["LMT", "RTX", "NOC", "GD", "BA", "LHX", "HII"],
    },
    "Categories (India)": {
        "Large Cap": ["RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "ICICIBANK.NS", "INFY.NS", "HINDUNILVR.NS", "ITC.NS", "LT.NS", "SBIN.NS", "BHARTIARTL.NS", "SUNPHARMA.NS", "MARUTI.NS"],
        "Mid Cap": ["BEL.NS", "PERSISTENT.NS", "COFORGE.NS", "POLYCAB.NS", "SUPREMEIND.NS", "ASTRAL.NS", "PAGEIND.NS", "AUBANK.NS", "MAZDOCK.NS", "OBEROIRLTY.NS", "BHARATFORG.NS"],
        "Small Cap": ["DATAPATTNS.NS", "ZENTEC.NS", "MTARTECH.NS", "PARAS.NS", "JWL.NS", "TITAGARH.NS", "KEI.NS", "RITES.NS", "APLAPOLLO.NS", "LAURUSLABS.NS"],
        "High Dividend": ["COALINDIA.NS", "ONGC.NS", "IOC.NS", "POWERGRID.NS", "ITC.NS", "HINDZINC.NS", "VEDL.NS", "NTPC.NS", "BPCL.NS", "GAIL.NS"],
        "Aggressive (high beta)": ["TATAMOTORS.NS", "TATASTEEL.NS", "ADANIENT.NS", "VEDL.NS", "RVNL.NS", "SUZLON.NS", "IRFC.NS", "JINDALSTEL.NS", "PNB.NS", "ZOMATO.NS"],
    },
}

# Model equity portfolios — standard templates (sector weights). Labelled as templates, not live-trend forecasts.
MODELS = {
    "Conservative": {"note": "Capital protection tilt — large-cap, defensives, dividends.",
                     "weights": {"Banking": 22, "FMCG": 18, "IT / Software": 15, "Pharma": 12, "Energy / Power": 13, "Auto": 8, "Infra / Capital Goods": 12}},
    "Balanced": {"note": "Growth + stability mix across cyclicals and defensives.",
                 "weights": {"Banking": 20, "IT / Software": 16, "Auto": 12, "Pharma": 10, "FMCG": 10, "Infra / Capital Goods": 12, "Defense": 10, "Energy / Power": 10}},
    "Aggressive": {"note": "Higher growth/cyclical tilt — defense, capital goods, mid/small caps. More volatile.",
                   "weights": {"Defense": 18, "Infra / Capital Goods": 16, "IT / Software": 14, "Auto": 12, "Railways": 10, "Banking": 12, "Pharma": 8, "Metals": 10}},
}

RF = 0.065  # risk-free proxy (India 10Y ~6.5%) for Sharpe / VaR. ponytail: constant; wire ^TNX/India10Y if you need it live.

WATCHLISTS = {  # candidate pools screened live; ranked by the tilt below
    "Buffett (quality)": {"pool": ["HINDUNILVR.NS", "NESTLEIND.NS", "TCS.NS", "ASIANPAINT.NS", "PIDILITIND.NS", "TITAN.NS", "HDFCBANK.NS", "ITC.NS", "BRITANNIA.NS", "COLPAL.NS", "MARICO.NS", "BAJFINANCE.NS"], "tilt": "fundamental"},
    "High ROE (ROCE proxy)": {"pool": ["TCS.NS", "NESTLEIND.NS", "HINDUNILVR.NS", "COLPAL.NS", "PAGEIND.NS", "HCLTECH.NS", "ASIANPAINT.NS", "BRITANNIA.NS", "TITAN.NS", "INFY.NS", "BAJAJ-AUTO.NS", "HAL.NS"], "tilt": "fundamental"},
    "Deep Value": {"pool": ["COALINDIA.NS", "ONGC.NS", "NMDC.NS", "IOC.NS", "BPCL.NS", "GAIL.NS", "SAIL.NS", "PNB.NS", "BANKBARODA.NS", "NTPC.NS", "POWERGRID.NS", "HINDALCO.NS"], "tilt": "value"},
    "Small-cap compounders": {"pool": ["DATAPATTNS.NS", "MTARTECH.NS", "KEI.NS", "APLAPOLLO.NS", "JWL.NS", "TATAELXSI.NS", "PERSISTENT.NS", "SOLARINDS.NS", "ASTRAL.NS", "POLYCAB.NS", "SUPREMEIND.NS", "ZENTEC.NS"], "tilt": "momentum"},
}

IDX_MARKET = {"NIFTY 50": "^NSEI", "SENSEX": "^BSESN", "Bank Nifty": "^NSEBANK", "India VIX": "^INDIAVIX"}
IDX_MACRO = {"Crude Oil (WTI)": "CL=F", "Gold": "GC=F", "US Dollar (DXY)": "DX-Y.NYB", "US 10Y Yield": "^TNX", "USD/INR": "INR=X", "Brent Crude": "BZ=F"}
IDX_SECTOR = {"IT": "^CNXIT", "Pharma": "^CNXPHARMA", "Auto": "^CNXAUTO", "FMCG": "^CNXFMCG", "Metal": "^CNXMETAL", "Bank": "^NSEBANK", "Energy": "^CNXENERGY", "Realty": "^CNXREALTY"}
FRED_SERIES = {"Fed Funds Rate": "FEDFUNDS", "US CPI (YoY proxy, index)": "CPIAUCSL", "US GDP": "GDP"}


def _np_default(o):
    if isinstance(o, np.integer): return int(o)
    if isinstance(o, np.floating): return float(o)
    if isinstance(o, np.bool_): return bool(o)
    if isinstance(o, np.ndarray): return o.tolist()
    if isinstance(o, pd.Timestamp): return str(o)
    raise TypeError(str(type(o)))


def jresp(obj, status=200):
    return Response(json.dumps(obj, default=_np_default), status=status, mimetype="application/json")


# Yahoo's fundamentals endpoint (info) is heavily rate-limited from datacenter IPs (e.g. Render),
# so retry, and cache successes only (never poison the cache with an empty/blocked response).
# ponytail: process-memory cache; fine because Render restarts often. Add TTL if you self-host long-running.
_INFO_CACHE = {}


def get_info(sym):
    if sym in _INFO_CACHE:
        return _INFO_CACHE[sym]
    for attempt in range(3):
        try:
            i = yf.Ticker(sym).info or {}
            if len(i) > 5:  # a real payload, not a blocked/empty stub
                _INFO_CACHE[sym] = i
                return i
        except Exception:
            pass
        time.sleep(0.6 * (attempt + 1))
    return {}  # don't cache failures — retry next request


def _g(info, *keys):
    for k in keys:
        v = info.get(k)
        if v is not None and not (isinstance(v, float) and math.isnan(v)):
            return v
    return None


def _pct(x):
    return None if x is None else round(x * 100, 2)


def div_yield_dec(info):
    # trailingAnnualDividendYield is a reliable DECIMAL (e.g. 0.0066 = 0.66%) — prefer it.
    # dividendYield in recent yfinance is PERCENT and ambiguous (0.4 means 0.4%, not 40%) — only a fallback.
    # Non-payers report 0.0 / None, so we return 0/None and never fabricate a yield.
    ty = _g(info, "trailingAnnualDividendYield")
    if ty:                      # non-zero reliable decimal
        return ty
    dy = _g(info, "dividendYield")
    if dy:                      # percent units → decimal
        return dy / 100
    return ty                   # 0.0 or None → genuinely no dividend


@lru_cache(maxsize=16)
def fx_to_inr(code):
    if code in (None, "INR", "₹"):
        return 1.0
    code = {"$": "USD"}.get(code, code)
    try:
        h = yf.Ticker(f"{code}INR=X").history(period="5d")["Close"].dropna()
        return round(float(h.iloc[-1]), 2) if len(h) else None
    except Exception:
        return None


def rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).mask(loss == 0, 100.0)


def dcf(fcf, growth, discount, years, tg=0.03):
    # ponytail: single-stage FCF DCF; 2-stage + net-debt bridge if per-share precision needed.
    if not fcf or fcf <= 0 or discount <= tg:
        return None
    pv, cf = 0.0, fcf
    for y in range(1, years + 1):
        cf *= (1 + growth)
        pv += cf / (1 + discount) ** y
    return pv + (cf * (1 + tg) / (discount - tg)) / (1 + discount) ** years


def reverse_dcf(fcf, mktcap, discount, years):
    if not fcf or fcf <= 0 or not mktcap:
        return None
    lo, hi = -0.10, 0.50
    for _ in range(60):
        mid = (lo + hi) / 2
        v = dcf(fcf, mid, discount, years)
        if v is None:
            return None
        lo, hi = (mid, hi) if v < mktcap else (lo, mid)
    return round((lo + hi) / 2 * 100, 1)


def score(value, good, bad):
    if value is None:
        return None
    if good == bad:
        return 5
    return round(max(0, min(10, (value - bad) / (good - bad) * 10)), 1)


def rate_metric(key, value):
    """Plain-language benchmark + good/ok/poor rating for a ratio."""
    b = BENCH.get(key)
    if not b or value is None:
        return None
    if b["hb"] is None:  # informational (beta)
        return {"rating": "info", "text": b["what"]}
    g, a = b["cut"]
    if b["hb"]:
        r = "good" if value >= g else ("ok" if value >= a else "poor")
        bench = f"Good ≥ {g}{b['unit']}, OK ≥ {a}{b['unit']}"
    else:
        r = "good" if value <= g else ("ok" if value <= a else "poor")
        bench = f"Good ≤ {g}{b['unit']}, OK ≤ {a}{b['unit']}"
    return {"rating": r, "text": b["what"], "bench": bench}


def cap_category(mktcap_inr):
    if not mktcap_inr:
        return None
    cr = mktcap_inr / 1e7  # rupees -> crore
    if cr >= 20000:
        return "Large cap"
    if cr >= 5000:
        return "Mid cap"
    return "Small cap"


def derive_fcf(info, t):
    fcf = _g(info, "freeCashflow")
    if fcf and fcf > 0:
        return fcf, "Yahoo freeCashflow", {}
    try:
        cf = t.cashflow
        ocf = cf.loc["Operating Cash Flow"].iloc[0] if "Operating Cash Flow" in cf.index else None
        capex = cf.loc["Capital Expenditure"].iloc[0] if "Capital Expenditure" in cf.index else 0
        if ocf is not None and not pd.isna(ocf):
            return float(ocf) + float(capex or 0), "Operating CF − Capex", {"operating_cash_flow": float(ocf), "capex": float(capex or 0)}
    except Exception:
        pass
    ni = _g(info, "netIncomeToCommon")
    if ni and ni > 0:
        return ni * 0.9, "Net-income proxy (0.9×PAT)", {"net_income": ni}
    return None, None, {}


def google_news(query, n=8):
    try:
        url = "https://news.google.com/rss/search?q=" + urllib.parse.quote(query) + "+when:14d&hl=en-IN&gl=IN&ceid=IN:en"
        raw = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=10).read()
        root = ET.fromstring(raw)
        out = []
        for it in list(root.iter("item"))[:n]:
            title = (it.findtext("title") or "").strip()
            low = title.lower()
            tone = "neg" if any(w in low for w in NEG) else ("pos" if any(w in low for w in POS) else "neutral")
            if title:
                out.append({"title": title, "link": it.findtext("link") or "", "date": (it.findtext("pubDate") or "")[:16], "tone": tone})
        return out
    except Exception:
        return []


def corporate_actions(t):
    out = []
    try:
        cal = t.calendar
        if isinstance(cal, dict):
            for k, v in cal.items():
                val = v[0] if isinstance(v, (list, tuple)) and v else v
                if val is not None:
                    out.append({"type": k, "date": str(val)})
    except Exception:
        pass
    try:
        for d, row in t.actions.tail(5).iterrows():
            if row.get("Dividends", 0):
                out.append({"type": "Dividend (recent)", "date": str(d.date()), "value": f"{float(row['Dividends']):.2f}"})
            if row.get("Stock Splits", 0):
                out.append({"type": "Split (recent)", "date": str(d.date()), "value": f"{float(row['Stock Splits'])}"})
    except Exception:
        pass
    return out


def find_peers(ticker, sector):
    for c, secs in UNIVERSE.items():
        if c.startswith("Categories"):
            continue
        for sec, lst in secs.items():
            if ticker in lst:
                return [x for x in lst if x != ticker][:6], sec
    if sector:
        for c, secs in UNIVERSE.items():
            for sec, lst in secs.items():
                if sector.lower() in sec.lower() or sec.lower() in sector.lower():
                    return [x for x in lst if x != ticker][:6], sec
    return [], sector


def references(ticker):
    base = ticker.replace(".NS", "").replace(".BO", "")
    refs = [{"label": "Yahoo Finance — quote & ratios (data source)", "url": f"https://finance.yahoo.com/quote/{ticker}"},
            {"label": "Yahoo Finance — financial statements", "url": f"https://finance.yahoo.com/quote/{ticker}/financials"}]
    if ticker.endswith((".NS", ".BO")):
        refs += [{"label": "Screener.in — filings, ratios, shareholding", "url": f"https://www.screener.in/company/{base}/"},
                 {"label": "NSE — FII/DII shareholding pattern", "url": f"https://www.nseindia.com/get-quotes/equity?symbol={base}"},
                 {"label": "Annual reports — BSE filings", "url": f"https://www.bseindia.com/stock-share-price/{base}/"}]
    return refs


def portfolio_plan(capital, risk, horizon, style, verdict, alloc):
    if not capital:
        return {"note": "Enter capital to get a deployment plan."}
    over = (verdict == "Overvalued")
    if horizon == "long" and not over and risk != "conservative":
        method, why = "Lumpsum (now) + top-ups on dips", "Long horizon and not overvalued — time in the market beats timing. Deploy most now."
        months = 3
    elif over or risk == "aggressive":
        method, why = "DCA / staggered tranches", "Valuation/volatility is stretched — spread entry to average your cost and reduce timing risk."
        months = 6
    else:
        method, why = "SIP (monthly)", "Steady, disciplined buying smooths out volatility — ideal for medium horizon."
        months = 12
    weight = (alloc.get("suggested_weight_pct") or 10) / 100
    stock_amt = round(capital * weight)
    return {"method": method, "why": why,
            "this_stock_budget": stock_amt, "tranches": months,
            "per_tranche": round(stock_amt / months) if months else stock_amt,
            "note": f"Of your total capital, ~{round(weight*100,1)}% (≈₹{stock_amt:,}) suits this single stock. "
                    f"Deploy it via {method.split('(')[0].strip().lower()} over ~{months} step(s) (~₹{round(stock_amt/months):,} each). Keep the rest diversified across sectors."}


def _fast(t):
    out = {}
    try:
        fi = t.fast_info
        for k in ("last_price", "previous_close", "market_cap", "shares", "currency"):
            try:
                v = fi[k]
                if v is not None:
                    out[k] = v if k == "currency" else float(v)
            except Exception:
                pass
    except Exception:
        pass
    return out


def _srow(df, *names):
    """First matching row of a yfinance statement as a most-recent-first Series, else None."""
    if df is None or getattr(df, "empty", True):
        return None
    for n in names:
        if n in df.index:
            s = df.loc[n].dropna()
            if len(s):
                return s
    return None


def _compute_fundamentals(fin, bs, cf, fast, price, beta):
    """Compute the standard `info` ratio fields from statements + fast_info, used to FILL any
    field Yahoo's rate-limited `info` endpoint didn't return (keeps cloud deploys fully working).
    Same keys/units as Yahoo, so the rest of the app is unchanged."""
    f = {}
    L = lambda s: float(s.iloc[0]) if s is not None and len(s) else None
    P = lambda s: float(s.iloc[1]) if s is not None and len(s) > 1 else None
    rev, ni = _srow(fin, "Total Revenue", "Operating Revenue"), _srow(fin, "Net Income", "Net Income Common Stockholders")
    op, ebitda = _srow(fin, "Operating Income"), _srow(fin, "EBITDA", "Normalized EBITDA")
    dep = _srow(fin, "Reconciled Depreciation", "Depreciation And Amortization", "Depreciation")
    eq = _srow(bs, "Stockholders Equity", "Total Stockholder Equity", "Common Stock Equity")
    debt = _srow(bs, "Total Debt")
    ld, sd = _srow(bs, "Long Term Debt"), _srow(bs, "Current Debt", "Short Term Debt")
    ca, cl = _srow(bs, "Current Assets", "Total Current Assets"), _srow(bs, "Current Liabilities", "Total Current Liabilities")
    R, NI, OP, EB, EQ = L(rev), L(ni), L(op), L(ebitda), L(eq)
    if EB is None and OP is not None and dep is not None:
        EB = OP + L(dep)
    D = L(debt)
    if D is None and (ld is not None or sd is not None):
        D = (L(ld) or 0) + (L(sd) or 0)
    CA, CL = L(ca), L(cl)
    mc, sh = fast.get("market_cap"), fast.get("shares")
    if mc is None and price and sh:
        mc = price * sh
    if mc: f["marketCap"] = mc
    if R: f["totalRevenue"] = R
    if EB: f["ebitda"] = EB
    if NI is not None: f["netIncomeToCommon"] = NI
    if mc and NI and NI > 0: f["trailingPE"] = round(mc / NI, 2)
    if mc and EQ and EQ > 0: f["priceToBook"] = round(mc / EQ, 2)
    if NI is not None and EQ and EQ > 0: f["returnOnEquity"] = round(NI / EQ, 4)
    if OP is not None and R: f["operatingMargins"] = round(OP / R, 4)
    if NI is not None and R: f["profitMargins"] = round(NI / R, 4)
    if D is not None and EQ and EQ > 0: f["debtToEquity"] = round(D / EQ * 100, 2)
    if CA and CL: f["currentRatio"] = round(CA / CL, 2)
    Rp, NIp = P(rev), P(ni)
    if R and Rp: f["revenueGrowth"] = round(R / Rp - 1, 4)
    if NI is not None and NIp and NIp > 0: f["earningsGrowth"] = round(NI / NIp - 1, 4)
    if beta is not None: f["beta"] = beta
    return f


def institutional_metrics(fin, bs, cf, mktcap, ebitda, fcf):
    """Quality / solvency / advanced-valuation metrics a CFA-level analyst computes from statements:
    EV/EBITDA, FCF yield, P/S, ROCE, ROIC, interest coverage, 3y CAGRs, Altman Z, Piotroski F."""
    L = lambda s: float(s.iloc[0]) if s is not None and len(s) else None
    Pr = lambda s: float(s.iloc[1]) if s is not None and len(s) > 1 else None
    nth = lambda s, n: float(s.iloc[n]) if s is not None and len(s) > n else None
    rev, ni = _srow(fin, "Total Revenue", "Operating Revenue"), _srow(fin, "Net Income", "Net Income Common Stockholders")
    ebit = _srow(fin, "EBIT", "Operating Income")
    intexp, gross = _srow(fin, "Interest Expense"), _srow(fin, "Gross Profit")
    ta, cl = _srow(bs, "Total Assets"), _srow(bs, "Current Liabilities", "Total Current Liabilities")
    ca, eq = _srow(bs, "Current Assets", "Total Current Assets"), _srow(bs, "Stockholders Equity", "Common Stock Equity")
    debt, ltd = _srow(bs, "Total Debt"), _srow(bs, "Long Term Debt")
    cash = _srow(bs, "Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments")
    re, totliab = _srow(bs, "Retained Earnings"), _srow(bs, "Total Liabilities Net Minority Interest", "Total Liabilities")
    shares, ocf = _srow(bs, "Share Issued", "Ordinary Shares Number"), _srow(cf, "Operating Cash Flow")
    R, NI, EBIT, TA, CL, EQ, D, C = L(rev), L(ni), L(ebit), L(ta), L(cl), L(eq), L(debt), L(cash)
    m = {}
    if mktcap and ebitda and ebitda > 0:
        m["EV/EBITDA"] = round((mktcap + (D or 0) - (C or 0)) / ebitda, 2)
    if fcf and mktcap:
        m["FCF yield %"] = round(fcf / mktcap * 100, 2)
    if mktcap and R:
        m["P/S"] = round(mktcap / R, 2)
    if EBIT and TA and CL and (TA - CL) > 0:
        m["ROCE %"] = round(EBIT / (TA - CL) * 100, 2)
    if EBIT and ((D or 0) + (EQ or 0) - (C or 0)) > 0:
        m["ROIC %"] = round(EBIT * 0.75 / ((D or 0) + (EQ or 0) - (C or 0)) * 100, 2)  # NOPAT≈EBIT×(1−25% tax)
    IE = L(intexp)
    if EBIT and IE and IE != 0:
        m["Interest coverage"] = round(EBIT / abs(IE), 2)
    r0, r3 = L(rev), nth(rev, 3)
    if r0 and r3 and r3 > 0:
        m["Revenue CAGR 3y %"] = round(((r0 / r3) ** (1 / 3) - 1) * 100, 1)
    n0, n3 = L(ni), nth(ni, 3)
    if n0 and n3 and n3 > 0:
        m["Earnings CAGR 3y %"] = round(((n0 / n3) ** (1 / 3) - 1) * 100, 1)
    if TA and TA > 0 and mktcap and totliab is not None:
        A = ((L(ca) or 0) - (CL or 0)) / TA
        m["Altman Z"] = round(1.2 * A + 1.4 * (L(re) or 0) / TA + 3.3 * (EBIT or 0) / TA
                              + 0.6 * mktcap / (L(totliab) or 1) + 1.0 * (R or 0) / TA, 2)
    # Piotroski F-score (0-9), scored only over sub-tests with available data
    f = fmax = 0
    ta1 = Pr(ta)
    roa0 = (n0 / TA) if (n0 and TA) else None
    roa1 = (Pr(ni) / ta1) if (Pr(ni) and ta1) else None
    tests = [(roa0 is not None, (roa0 or 0) > 0),
             (L(ocf) is not None, (L(ocf) or 0) > 0),
             (roa0 is not None and roa1 is not None, (roa0 or 0) > (roa1 or 0)),
             (L(ocf) is not None and n0 is not None, (L(ocf) or 0) > (n0 or 0)),
             (L(ltd) is not None and Pr(ltd) is not None and TA and ta1, (L(ltd) or 0) / (TA or 1) < (Pr(ltd) or 0) / (ta1 or 1)),
             (L(ca) and CL and Pr(ca) and Pr(cl), (L(ca) or 0) / (CL or 1) > (Pr(ca) or 0) / (Pr(cl) or 1)),
             (L(shares) and Pr(shares), (L(shares) or 0) <= (Pr(shares) or 0)),
             (L(gross) and r0 and Pr(gross) and Pr(rev), (L(gross) or 0) / (r0 or 1) > (Pr(gross) or 0) / (Pr(rev) or 1)),
             (r0 and TA and Pr(rev) and ta1, (r0 or 0) / (TA or 1) > (Pr(rev) or 0) / (ta1 or 1))]
    for avail, ok in tests:
        if avail:
            fmax += 1; f += 1 if ok else 0
    if fmax >= 5:
        m["Piotroski F"] = f
        m["_piotroski_max"] = fmax
    return m


def enriched_info(sym):
    """info dict with missing ratio fields filled from REAL statements (so screens/peers/industry-PE
    aren't blank when Yahoo's `info` feed is blocked on cloud IPs). Only fetches statements when
    `info` is actually incomplete, so it stays fast when the info feed works."""
    info = dict(get_info(sym))
    if all(_g(info, k) is not None for k in ("trailingPE", "returnOnEquity", "marketCap", "priceToBook")):
        return info  # info feed worked (e.g. residential IP) — no need for statements
    t = yf.Ticker(sym)
    fast = _fast(t)
    fin = bs = None
    try: fin = t.financials
    except Exception: pass
    try: bs = t.balance_sheet
    except Exception: pass
    price = _g(info, "currentPrice", "regularMarketPrice", "previousClose") or fast.get("last_price")
    if price is None:
        try: price = float(t.history(period="5d")["Close"].dropna().iloc[-1])
        except Exception: pass
    for k, v in _compute_fundamentals(fin, bs, None, fast, price, None).items():
        if _g(info, k) is None and v is not None:
            info[k] = v
    return info


def fetch(ticker, years):
    t = yf.Ticker(ticker)
    notes = []
    info = dict(get_info(ticker))          # copy so we never mutate the shared cache
    fast = _fast(t)
    cur_code = "INR" if ticker.endswith((".NS", ".BO")) else (_g(info, "currency") or fast.get("currency") or "USD")
    sym = {"INR": "₹", "USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥"}.get(cur_code, cur_code + " ")
    price = _g(info, "currentPrice", "regularMarketPrice", "previousClose") or fast.get("last_price")

    fin = bs = cf = None
    try: fin = t.financials
    except Exception as e: notes.append(f"income stmt: {e}")
    try: bs = t.balance_sheet
    except Exception as e: notes.append(f"balance sheet: {e}")
    try: cf = t.cashflow
    except Exception as e: notes.append(f"cashflow: {e}")

    hist = {}
    try:
        if fin is not None and not fin.empty:
            cols = list(fin.columns)[:years]
            for label, key in [("Revenue", "Total Revenue"), ("EBITDA", "EBITDA"), ("Net Income", "Net Income")]:
                if key in fin.index:
                    hist[label] = {str(c.date()): (None if pd.isna(v) else float(v)) for c, v in fin.loc[key, cols].items()}
    except Exception as e:
        notes.append(f"statements: {e}")

    tech, daily_close = {}, None
    for tf, period, interval in [("daily", "2y", "1d"), ("weekly", "5y", "1wk")]:
        try:
            px = t.history(period=period, interval=interval)["Close"].dropna()
            if tf == "daily":
                daily_close = px
            if len(px) < 30:
                continue
            r = rsi(px).iloc[-1]
            ema200 = float(px.ewm(span=200, adjust=False).mean().iloc[-1])
            last = float(px.iloc[-1])
            tech[tf] = {"rsi": None if pd.isna(r) else round(float(r), 1), "ema200": round(ema200, 2),
                        "price": round(last, 2), "above_ema": bool(last > ema200),
                        "series": [round(float(x), 2) for x in px.tail(180)],
                        "dates": [str(d.date()) for d in px.tail(180).index]}
        except Exception as e:
            notes.append(f"{tf} price: {e}")
    if price is None and daily_close is not None and len(daily_close):
        price = round(float(daily_close.iloc[-1]), 2)

    # beta from daily returns vs benchmark (fills info when Yahoo's beta is blocked)
    beta = None
    try:
        if daily_close is not None and len(daily_close) > 60:
            bench = "^NSEI" if ticker.endswith((".NS", ".BO")) else "^GSPC"
            bpx = yf.Ticker(bench).history(period="2y")["Close"].dropna().pct_change()
            j = pd.concat([daily_close.pct_change(), bpx], axis=1).dropna()
            if len(j) > 60 and float(np.var(j.iloc[:, 1])) > 0:
                beta = round(float(np.cov(j.iloc[:, 0], j.iloc[:, 1])[0, 1] / np.var(j.iloc[:, 1])), 2)
    except Exception:
        pass

    # dividend yield (decimal) from dividend history when info lacks it
    div_dec = None
    try:
        divs = t.dividends
        if divs is not None and len(divs) and price:
            last12 = float(divs[divs.index >= (divs.index.max() - pd.Timedelta(days=365))].sum())
            if last12 > 0:
                div_dec = last12 / price
    except Exception:
        pass

    # fill any missing info fields from statements/fast_info — never overwrite a real Yahoo value
    computed = _compute_fundamentals(fin, bs, cf, fast, price, beta)
    if div_dec is not None:
        computed["dividendYield"] = div_dec
    filled = []
    for k, v in computed.items():
        if _g(info, k) is None and v is not None:
            info[k] = v
            filled.append(k)
    if filled:
        notes.append("Computed from financial statements (live ratio feed unavailable): " + ", ".join(sorted(set(filled))))

    inst_pct, holders = None, []
    try:
        mh = t.major_holders
        if mh is not None and not mh.empty:
            for line in mh.itertuples(index=False):
                vals = [str(x) for x in line]
                if any("institution" in v.lower() for v in vals):
                    for v in vals:
                        if "%" in v:
                            inst_pct = v
        ih = t.institutional_holders
        if ih is not None and not ih.empty:
            holders = [{"name": str(r.get("Holder", "")), "pct": float(r.get("pctHeld", 0) or 0) * 100} for _, r in ih.head(5).iterrows()]
    except Exception as e:
        notes.append(f"holders: {e}")

    return t, info, cur_code, sym, price, hist, tech, inst_pct, holders, corporate_actions(t), notes, fin, bs, cf


def research_verdict(overall, verdict):
    if overall is None:
        return "Insufficient data"
    if overall >= 7 and verdict != "Overvalued":
        return "Buy"
    if overall >= 5:
        return "Hold"
    return "Avoid"


# ---------------------------- portfolio analytics engine ----------------------------
@lru_cache(maxsize=512)
def stock_sector(sym):
    try:
        return get_info(sym).get("sector") or "Other"
    except Exception:
        return "Other"


def _closes(syms, period="2y"):
    uniq = list(dict.fromkeys(syms))
    data = yf.download(uniq, period=period, progress=False, auto_adjust=True)
    try:
        close = data["Close"]
    except Exception:
        close = data
    if isinstance(close, pd.Series):
        close = close.to_frame(name=uniq[0])
    return close.dropna(how="all")


def max_drawdown(cum):
    cum = pd.Series(cum)
    peak = cum.cummax()
    return float((cum / peak - 1).min())


def monte_carlo(mean_d, std_d, value, days=252, sims=2000):
    rng = np.random.default_rng(42)  # seeded for reproducibility; data itself is live
    paths = rng.normal(mean_d, std_d, (sims, days))
    ending = value * np.prod(1 + paths, axis=1)
    return {"horizon_days": days, "p5": round(float(np.percentile(ending, 5))),
            "p50": round(float(np.percentile(ending, 50))), "p95": round(float(np.percentile(ending, 95))),
            "expected": round(float(ending.mean())), "prob_loss_pct": round(float((ending < value).mean() * 100), 1)}


def efficient_frontier(mu, Sigma, syms, n=3000):
    k = len(syms)
    if k == 1:
        v = float(np.sqrt(Sigma[0, 0]))
        return {"scatter": [{"vol": round(v * 100, 1), "ret": round(float(mu[0]) * 100, 1)}],
                "max_sharpe": {"weights": {syms[0]: 100.0}, "vol": round(v * 100, 1), "ret": round(float(mu[0]) * 100, 1),
                               "sharpe": round((float(mu[0]) - RF) / v, 2) if v else None},
                "min_vol": {"vol": round(v * 100, 1), "ret": round(float(mu[0]) * 100, 1)}}
    rng = np.random.default_rng(1)
    pts = []
    for _ in range(n):
        w = rng.random(k); w /= w.sum()
        r = float(w @ mu); v = float(np.sqrt(w @ Sigma @ w))
        pts.append((v, r, (r - RF) / v if v else 0, w))
    best = max(pts, key=lambda x: x[2]); minv = min(pts, key=lambda x: x[0])
    step = max(1, n // 140)
    scatter = [{"vol": round(p[0] * 100, 1), "ret": round(p[1] * 100, 1)} for p in pts[::step]]
    return {"scatter": scatter,
            "max_sharpe": {"weights": {syms[i]: round(float(best[3][i]) * 100, 1) for i in range(k)},
                           "vol": round(best[0] * 100, 1), "ret": round(best[1] * 100, 1), "sharpe": round(best[2], 2)},
            "min_vol": {"vol": round(minv[0] * 100, 1), "ret": round(minv[1] * 100, 1)}}


def portfolio_analytics(holdings, extra_capital=0):
    syms = [h["sym"] for h in holdings if h.get("sym")]
    if not syms:
        return {"error": "No holdings."}
    bench = "^NSEI" if any(s.endswith((".NS", ".BO")) for s in syms) else "^GSPC"
    closes = _closes(syms + [bench])
    have = [s for s in syms if s in closes.columns]
    missing = [s for s in syms if s not in have]
    if len(have) < 1 or bench not in closes.columns:
        return {"error": f"Could not fetch price history for {syms}."}
    rets = closes[have + [bench]].pct_change().dropna()
    last = closes.iloc[-1]
    ann = 252
    mu = rets[have].mean() * ann
    vol = rets[have].std() * np.sqrt(ann)
    bvar = float(np.var(rets[bench]))
    mv = {h["sym"]: h.get("qty", 0) * float(last.get(h["sym"], np.nan)) for h in holdings if h["sym"] in have}
    total = float(np.nansum(list(mv.values())))
    w = np.array([(mv.get(s, 0) if not math.isnan(mv.get(s, np.nan)) else 0) / total for s in have]) if total else np.zeros(len(have))

    infos = {}
    for s in have:
        try:
            infos[s] = get_info(s)
        except Exception:
            infos[s] = {}

    betas, perstock = {}, []
    div_total = 0.0
    for s in have:
        beta = float(np.cov(rets[s], rets[bench])[0, 1] / bvar) if bvar else None
        betas[s] = beta or 1.0
        dwn = rets[s][rets[s] < 0]
        dy = div_yield_dec(infos[s]) or 0
        h = next((x for x in holdings if x["sym"] == s), {})
        price_s = float(last.get(s, 0) or 0)
        div_s = h.get("qty", 0) * price_s * dy
        div_total += div_s
        perstock.append({"sym": s, "weight_pct": round(float(w[have.index(s)]) * 100, 1),
                         "beta": round(beta, 2) if beta is not None else None,
                         "vol_pct": round(float(vol[s]) * 100, 1),
                         "downside_dev_pct": round(float(dwn.std() * np.sqrt(ann)) * 100, 1) if len(dwn) > 1 else None,
                         "max_drawdown_pct": round(max_drawdown((1 + rets[s]).cumprod()) * 100, 1),
                         "exp_return_pct": round(float(mu[s]) * 100, 1),
                         "div_yield_pct": round(dy * 100, 2), "annual_dividend": round(div_s)})

    Sigma = (rets[have].cov() * ann).values
    port_ret = float(w @ mu.values)
    port_vol = float(np.sqrt(w @ Sigma @ w))
    sharpe = round((port_ret - RF) / port_vol, 2) if port_vol else None
    port_beta = round(float(w @ np.array([betas[s] for s in have])), 2)
    port_daily = rets[have].values @ w
    var95_pct = round(float(-(np.percentile(port_daily, 5))) * 100, 2)          # 1-day 95% historical VaR
    var95_amt = round(total * var95_pct / 100)
    port_mdd = round(max_drawdown((1 + pd.Series(port_daily)).cumprod()) * 100, 1)
    wavg_vol = float(w @ vol.values)
    div_ratio = round(wavg_vol / port_vol, 2) if port_vol else 1.0
    eff_n = round(1 / float(np.sum(w ** 2)), 1) if np.sum(w ** 2) else 0
    div_score = round(min(10, (div_ratio - 1) * 10 + min(eff_n, 5)), 1)

    secw = {}
    for s in have:
        secw[stock_sector(s)] = secw.get(stock_sector(s), 0) + float(w[have.index(s)])
    sec_conc = round(max(secw.values()) * 100, 1) if secw else 0
    top_sectors = sorted(({"sector": k, "pct": round(v * 100, 1)} for k, v in secw.items()), key=lambda x: -x["pct"])

    # --- advanced quant metrics ---
    neg = port_daily[port_daily < 0]
    downside_vol = float(neg.std() * np.sqrt(ann)) if len(neg) > 1 else None
    sortino = round((port_ret - RF) / downside_vol, 2) if downside_vol else None
    cutoff = np.percentile(port_daily, 5)
    tail = port_daily[port_daily <= cutoff]
    cvar_pct = round(float(-tail.mean()) * 100, 2) if len(tail) else var95_pct
    cvar_amt = round(total * cvar_pct / 100)
    bench_ann = float(rets[bench].mean() * ann)
    alpha = round((port_ret - (RF + port_beta * (bench_ann - RF))) * 100, 1)  # Jensen's annual alpha
    n_days = len(port_daily)
    port_cum = float((1 + pd.Series(port_daily)).prod())
    cagr = round((port_cum ** (ann / n_days) - 1) * 100, 1) if n_days > 0 else None
    corr = rets[have].corr().round(2)
    correlation = {a: {b: float(corr.loc[a, b]) for b in have} for a in have}
    pc = (1 + pd.Series(port_daily, index=rets.index)).cumprod()
    bc = (1 + rets[bench]).cumprod()
    step = max(1, len(pc) // 120)
    backtest = {"dates": [str(d.date()) if hasattr(d, "date") else str(d) for d in rets.index[::step]],
                "portfolio": [round(float(x), 3) for x in pc.values[::step]],
                "benchmark": [round(float(x), 3) for x in bc.values[::step]], "benchmark_name": bench}
    port_yield = round(div_total / total * 100, 2) if total else 0

    mc = monte_carlo(float(port_daily.mean()), float(port_daily.std()), total)
    ef = efficient_frontier(mu.values, Sigma, have)

    # rebalancing toward max-Sharpe target, deploying any extra capital too
    deploy = total + extra_capital
    tgt = ef["max_sharpe"]["weights"]
    cur = {s: round(float(w[have.index(s)]) * 100, 1) for s in have}
    rebal = [{"sym": s, "current_pct": cur[s], "target_pct": tgt.get(s, 0),
              "action": "Buy" if tgt.get(s, 0) >= cur[s] else "Trim",
              "amount": round(abs(tgt.get(s, 0) - cur[s]) / 100 * deploy)} for s in have]

    # factor tilt (approximate, descriptive) — reuse already-fetched infos
    factor = _factor_tilt(have, w, infos)

    stress = {f"NIFTY {d}%": {"expected_loss": round(total * port_beta * d / 100), "value_after": round(total * (1 + port_beta * d / 100))}
              for d in (-10, -15, -20)}

    benchmarks = {
        "sharpe": "Risk-adjusted return. >1 good, >2 excellent, <0 poor.",
        "sortino": "Like Sharpe but penalizes only downside. >1 good, >2 excellent.",
        "alpha": "Return above what beta predicts (Jensen's α). >0 = beating market risk-adjusted.",
        "cagr": "Compounded annual growth over the lookback. Compare to NIFTY (~12% long-run).",
        "beta": "~1 moves with market; <1 defensive; >1 amplifies market moves.",
        "var": "1-day 95% loss — exceeded ~1 day in 20. Smaller is safer.",
        "cvar": "Average loss on the worst 5% of days (Expected Shortfall) — worse than VaR.",
        "max_drawdown": "Worst peak-to-trough fall. Smaller = calmer ride.",
        "diversification": ">5 effective holdings and ratio >1.2 is healthy.",
        "sector_concentration": "Keep any single sector under ~30% to limit concentration risk.",
        "dividend_yield": ">2% is income-friendly; <1% is growth-oriented.",
    }

    return {"value": round(total), "holdings_used": have, "missing": missing, "per_stock": perstock,
            "income": {"annual_dividend": round(div_total), "portfolio_yield_pct": port_yield,
                       "monthly_avg": round(div_total / 12),
                       "note": "Estimated forward annual dividend = Σ(shares × price × current yield). Actual payouts vary."},
            "mpt": {"expected_return_pct": round(port_ret * 100, 1), "cagr_pct": cagr, "volatility_pct": round(port_vol * 100, 1),
                    "sharpe": sharpe, "sortino": sortino, "diversification_score": div_score, "diversification_ratio": div_ratio,
                    "effective_holdings": eff_n, "rf_pct": round(RF * 100, 1)},
            "risk": {"portfolio_beta": port_beta, "alpha_pct": alpha, "var_1d_95_pct": var95_pct, "var_1d_95_amount": var95_amt,
                     "cvar_1d_95_pct": cvar_pct, "cvar_1d_95_amount": cvar_amt,
                     "max_drawdown_pct": port_mdd, "sector_concentration_pct": sec_conc, "top_sectors": top_sectors,
                     "benchmark_name": bench, "benchmark_return_pct": round(bench_ann * 100, 1)},
            "correlation": {"order": have, "matrix": correlation}, "backtest": backtest,
            "factor_tilt": factor, "monte_carlo": mc, "efficient_frontier": ef,
            "stress_test": stress, "rebalance": rebal, "extra_capital": extra_capital, "benchmarks": benchmarks,
            "note": "2y daily data. Expected return / CAGR are historical (NOT forecasts). VaR & CVaR are 1-day 95% historical. Educational only."}


def _factor_tilt(syms, w, infos=None):
    pe = pb = mc = roe = bt = wsum = 0.0
    for i, s in enumerate(syms):
        inf = (infos or {}).get(s)
        if inf is None:
            inf = get_info(s)
        wi = float(w[i])
        pe += wi * (_g(inf, "trailingPE") or 0); pb += wi * (_g(inf, "priceToBook") or 0)
        mc += wi * (_g(inf, "marketCap") or 0); roe += wi * (_g(inf, "returnOnEquity") or 0)
        bt += wi * (_g(inf, "beta") or 1); wsum += wi
    cr = (mc / 1e7) if mc else 0
    return {"size": "Large-cap tilt" if cr >= 20000 else ("Mid-cap tilt" if cr >= 5000 else "Small-cap tilt"),
            "value_growth": "Value tilt (cheap)" if pe and pe < 18 else ("Growth tilt (premium)" if pe and pe > 30 else "Blend"),
            "quality": f"Weighted ROE {roe*100:.0f}%" if roe else "n/a",
            "volatility": "Defensive (beta<1)" if bt < 1 else "Aggressive (beta>1)",
            "wavg_pe": round(pe, 1) if pe else None, "wavg_pb": round(pb, 1) if pb else None}


def quality_score(roe, de, margin, pe, fcf):
    s, n = 0.0, 0
    if roe is not None: s += score(roe, 22, 5); n += 1
    if margin is not None: s += score(margin, 18, 2); n += 1
    if de is not None: s += score(de, 30, 150); n += 1
    if pe: s += score(pe, 12, 45); n += 1
    if fcf is not None: s += (8 if fcf > 0 else 2); n += 1
    return round(s / n, 1) if n else None


def fund_rating(roe_pct, roce_pct, de, pb, npm_pct, eg_pct, pe, profitable):
    """Strict fundamental rating: rate ONLY when the core metrics are all present; otherwise
    return (None, reason) so we never inflate a score by silently dropping a missing metric.
    Loss-makers (negative profit) are hard-capped so a stock like Vodafone Idea can't look 'strong'."""
    core = {"ROE": roe_pct, "ROCE": roce_pct, "Debt/Equity": de, "Net margin": npm_pct, "P/B": pb}
    missing = [k for k, v in core.items() if v is None]
    if missing:
        return None, "Not rated — missing " + ", ".join(missing)
    parts = [score(roe_pct, 22, 5), score(roce_pct, 18, 6), score(de, 30, 150),
             score(npm_pct, 15, 2), score(pb, 1, 8)]
    if eg_pct is not None:
        parts.append(score(eg_pct, 20, 0))
    if pe:
        parts.append(score(pe, 12, 45))
    s = round(sum(parts) / len(parts), 1)
    if not profitable:                      # loss-making → cannot be "fundamentally strong"
        return min(s, 3.0), "Loss-making — score capped"
    return s, None


def screen_pool(syms, tilt="fundamental"):
    syms = list(dict.fromkeys(syms))[:12]
    mom = {}
    try:
        c = _closes(syms, "6mo")
        for s in syms:
            if s in c.columns:
                ser = c[s].dropna()
                if len(ser) > 5:
                    mom[s] = round(float(ser.iloc[-1] / ser.iloc[0] - 1) * 100, 1)
    except Exception:
        pass
    rows = []
    for s in syms:
        i = enriched_info(s)
        pe, pb = _g(i, "trailingPE"), _g(i, "priceToBook")
        roe, de = _pct(_g(i, "returnOnEquity")), _g(i, "debtToEquity")
        dv, beta = _pct(div_yield_dec(i)), _g(i, "beta")
        mg = _pct(_g(i, "profitMargins"))
        q = quality_score(roe, de, mg, pe, _g(i, "freeCashflow"))
        tags = []
        if dv and dv > 1.5: tags.append("Dividend")
        if beta and beta < 0.9: tags.append("Low beta")
        if mom.get(s, 0) > 12: tags.append("Momentum")
        rows.append({"ticker": s, "name": _g(i, "shortName", "longName") or s, "pe": pe, "pb": pb, "roe": roe,
                     "de": de, "div": dv, "beta": beta, "margin": mg, "marketCap": _g(i, "marketCap"),
                     "momentum_6m": mom.get(s), "quality": q, "tags": tags})
    keys = {"fundamental": lambda r: -(r["quality"] or 0), "dividend": lambda r: -(r["div"] or 0),
            "momentum": lambda r: -(r["momentum_6m"] if r["momentum_6m"] is not None else -999),
            "value": lambda r: (r["pe"] or 999) + (r["pb"] or 999), "lowbeta": lambda r: (r["beta"] or 999)}
    rows.sort(key=keys.get(tilt, keys["fundamental"]))
    return rows


def fred_latest():
    key = os.environ.get("FRED_API_KEY")
    if not key:
        return {"enabled": False, "note": "Set FRED_API_KEY (free at fred.stlouisfed.org) for Fed Funds, CPI, GDP."}
    out = {}
    for label, sid in FRED_SERIES.items():
        try:
            r = requests.get("https://api.stlouisfed.org/fred/series/observations",
                             params={"series_id": sid, "api_key": key, "file_type": "json", "sort_order": "desc", "limit": 1}, timeout=8)
            obs = r.json()["observations"][0]
            out[label] = {"value": obs["value"], "date": obs["date"]}
        except Exception as e:
            out[label] = {"value": "n/a", "date": str(e)[:40]}
    return {"enabled": True, "data": out}


# ---------------------------- AI analyst (reasons over the REAL computed data) ----------------------------
AI_SYSTEM = (
    "You are FundaPilot's AI analyst. Persona: a CFA and CMT charterholder, a PhD in mathematics & statistics, "
    "and a senior portfolio manager at a BlackRock-tier asset manager. Apply rigorous methods: for valuation use "
    "DCF/reverse-DCF and relative multiples (P/E, EV/EBITDA, PEG, P/B, FCF yield) and growth-adjusted fair value; "
    "for portfolios use Modern Portfolio Theory and risk analytics (Sharpe, VaR, drawdown, beta, correlation, "
    "diversification) to value and rebalance; for timing use CMT technicals (trend, RSI/MACD, relative strength). "
    "STRICT RULES: (1) Use ONLY the structured data provided plus well-established general knowledge — NEVER invent "
    "specific numbers, prices, dividends or events not in the data; if a figure is missing, say so rather than guess. "
    "(2) Be precise and show brief, quantitative reasoning. (3) Stay STRICTLY within investment/finance analysis of "
    "the stock(s)/portfolio in the provided context — if asked anything off-topic (politics, coding, personal, general "
    "chit-chat), decline in one line and steer back to the analysis. (4) This is EDUCATIONAL ONLY, not investment "
    "advice; acknowledge uncertainty and that you can be wrong. Be decisive but honest about confidence. Keep it tight.")
AI_DISCLAIMER = "⚠️ AI can make mistakes and may misread the data. Educational use only — not investment advice. Verify independently."


def _env(name, default=""):
    return (os.environ.get(name, default) or "").strip()  # strip copy-paste whitespace/newlines


def ai_available():
    return bool(_env("ANTHROPIC_API_KEY") or (_env("AI_API_KEY") and _env("AI_BASE_URL")))


# Protect a PERSONAL API key on a public URL: tight per-IP + global daily caps so nobody can
# burn your credits. Override with env AI_PER_MIN / AI_DAILY_CAP.
_AI_RL, _AI_DAY = {}, {"date": "", "count": 0}


def ai_rate_ok(ip):
    per_min = int(os.environ.get("AI_PER_MIN", "6"))
    daily = int(os.environ.get("AI_DAILY_CAP", "150"))
    today = time.strftime("%Y-%m-%d")
    if _AI_DAY["date"] != today:
        _AI_DAY.update(date=today, count=0)
    if _AI_DAY["count"] >= daily:
        return False, "Daily AI limit reached for this app — try again tomorrow."
    now = time.time()
    dq = _AI_RL.setdefault(ip, deque())
    while dq and dq[0] < now - 60:
        dq.popleft()
    if len(dq) >= per_min:
        return False, "Too many AI requests — wait a minute."
    dq.append(now)
    _AI_DAY["count"] += 1
    return True, None


def ai_chat(system, user, max_tokens=900):
    """Provider-flexible LLM call. Prefers Anthropic if its key is set; otherwise any OpenAI-compatible
    endpoint (Groq/OpenRouter/OpenAI/Together) via AI_BASE_URL + AI_API_KEY (+ AI_MODEL)."""
    ak = _env("ANTHROPIC_API_KEY")
    if ak:
        model = _env("AI_MODEL") or "claude-3-5-haiku-latest"
        r = requests.post("https://api.anthropic.com/v1/messages",
                          headers={"x-api-key": ak, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                          json={"model": model, "max_tokens": max_tokens,
                                "system": system, "messages": [{"role": "user", "content": user}]}, timeout=60)
        if r.status_code >= 400:  # provider error text never contains the key (key is a header)
            raise RuntimeError(f"Anthropic {r.status_code} (model={model}): {r.text[:280]}")
        return r.json()["content"][0]["text"]
    base, key = _env("AI_BASE_URL"), _env("AI_API_KEY")
    if base and key:
        model = _env("AI_MODEL") or "llama-3.3-70b-versatile"
        r = requests.post(base.rstrip("/") + "/chat/completions",
                          headers={"Authorization": "Bearer " + key, "content-type": "application/json"},
                          json={"model": model, "max_tokens": max_tokens,
                                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}, timeout=60)
        if r.status_code >= 400:
            raise RuntimeError(f"AI {r.status_code} (model={model}): {r.text[:280]}")
        return r.json()["choices"][0]["message"]["content"]
    raise RuntimeError("AI not configured")


def recommendation(overall, verdict, tech, style_take, weight):
    d = tech.get("daily") or {}
    if overall is None:
        return "If I were you: not enough data for a confident call — keep it on a watchlist only."
    rsi_v, up = d.get("rsi"), d.get("above_ema")
    if overall >= 7 and verdict != "Overvalued":
        act = "I'd accumulate — start a position now"
        if rsi_v and rsi_v > 70:
            act = "I'd accumulate, but wait for the daily RSI to cool from overbought before adding"
    elif overall >= 5.5:
        act = "I'd take a small starter position and add on dips / via SIP"
    elif verdict == "Overvalued" or overall < 5:
        act = "I'd wait — keep it on watchlist for a better price or improving numbers"
    else:
        act = "I'd watchlist it"
    trend = "price is above its 200-EMA (long-term uptrend)" if up else "price is below its 200-EMA (be cautious)"
    return f"If I were you: {act}. The {trend}, overall quality is {overall}/10 ({style_take.lower()} for your style). Cap it at ~{weight}% of capital."


def _style_cat(key):
    """Map an expanded scorecard key to the broad category a style weights (keeps style scoring working)."""
    k = key.lower()
    if "p/e" in k: return "Valuation (P/E)"
    if "valuation" in k or "p/b" in k or "peg" in k: return "Valuation"
    if "roe" in k or "margin" in k or "profit" in k or "cash" in k: return "Profitability"
    if "growth" in k: return "Growth"
    if "momentum" in k: return "Momentum"
    return "Financial health"


def analyze(ticker, horizon, risk, capital, years, style):
    t, info, cur_code, sym, price, hist, tech, inst_pct, holders, actions, notes, fin, bs, cf = fetch(ticker, years)
    rp = RISK[risk]
    proj_years = max(HORIZON_YEARS.get(horizon, 8), rp["proj_years"])
    name = _g(info, "longName", "shortName") or ticker
    sector, industry = _g(info, "sector"), _g(info, "industry")
    fx = fx_to_inr(cur_code)

    pe, fpe, pb = _g(info, "trailingPE"), _g(info, "forwardPE"), _g(info, "priceToBook")
    roe, de = _g(info, "returnOnEquity"), _g(info, "debtToEquity")
    opm, npm = _g(info, "operatingMargins"), _g(info, "profitMargins")
    rev_g, earn_g = _g(info, "revenueGrowth"), _g(info, "earningsGrowth", "earningsQuarterlyGrowth")
    ebitda, pat = _g(info, "ebitda"), _g(info, "netIncomeToCommon")
    mktcap, div_y = _g(info, "marketCap"), div_yield_dec(info)
    cur_ratio, beta = _g(info, "currentRatio"), _g(info, "beta")
    peg = _g(info, "pegRatio")
    if peg is None and pe and earn_g and earn_g > 0:
        peg = round(pe / (earn_g * 100), 2)
    rev_ttm = _g(info, "totalRevenue")
    ebitda_margin = (ebitda / rev_ttm) if (ebitda and rev_ttm) else None

    fcf, fcf_src, fcf_detail = derive_fcf(info, t)
    inst = institutional_metrics(fin, bs, cf, mktcap, ebitda, fcf)  # ROCE, ROIC, EV/EBITDA, Z, F-score...
    g = max(0.03, min(0.18, (earn_g or rev_g or 0.08)))
    fair = dcf(fcf, g, rp["discount"], proj_years)
    implied_g = reverse_dcf(fcf, mktcap, rp["discount"], proj_years)
    dcf_verdict, mos = "Insufficient data", None
    if fair and mktcap:
        mos = round((fair / mktcap - 1) * 100, 1)
        dcf_verdict = "Undervalued" if mos > 20 else ("Overvalued" if mos < -20 else "Fairly valued")

    # --- scenario DCF: bear / base / bull (range, not a single point — how analysts actually quote value) ---
    scenarios = {}
    if fcf and mktcap:
        for nm, gadj, dadj in [("Bear", -0.03, 0.02), ("Base", 0.0, 0.0), ("Bull", 0.03, -0.01)]:
            gg = max(0.02, min(0.20, g + gadj)); dd = rp["discount"] + dadj
            fv = dcf(fcf, gg, dd, proj_years)
            if fv:
                scenarios[nm] = {"fair_value": fv, "mos_pct": round((fv / mktcap - 1) * 100, 1),
                                 "growth_pct": round(gg * 100, 1), "discount_pct": round(dd * 100, 1)}

    # --- growth-adjusted valuation: what P/E is justified by growth + quality (investors pay up for the future) ---
    gr = (earn_g if earn_g is not None else rev_g)
    gr_pct = round(gr * 100, 1) if gr is not None else None
    fair_pe = None
    if gr_pct is not None:
        fair_pe = 8 + 0.9 * max(gr_pct, 0)            # Lynch-style: base + growth premium (PEG≈1 anchor)
        if roe and roe > 0.18: fair_pe *= 1.10        # quality earns a premium
        if de is not None and de < 40: fair_pe *= 1.05
        fair_pe = round(min(fair_pe, 60), 1)
    pe_growth_verdict, pe_gap = None, None
    if pe and fair_pe:
        pe_gap = round((fair_pe / pe - 1) * 100, 1)   # +ve = market paying less than growth justifies
        pe_growth_verdict = "Undervalued vs growth" if pe_gap > 15 else ("Overvalued vs growth" if pe_gap < -15 else "Fairly priced vs growth")

    # --- combined verdict: blends DCF + growth-adjusted P/E + PEG (not just P/E & P/B) ---
    votes, reasons = 0, []
    if mos is not None:
        votes += 1 if mos > 20 else (-1 if mos < -20 else 0)
        reasons.append(f"DCF {('cheap' if mos>20 else 'rich' if mos<-20 else 'fair')} ({mos:+.0f}% vs price)")
    if pe_gap is not None:
        votes += 1 if pe_gap > 15 else (-1 if pe_gap < -15 else 0)
        reasons.append(f"P/E {pe:.0f} vs growth-fair {fair_pe:.0f}")
    if peg is not None:
        votes += 1 if peg < 1 else (-1 if peg > 2 else 0)
        reasons.append(f"PEG {peg:.2f}")
    verdict = "Undervalued" if votes >= 1 else ("Overvalued" if votes <= -1 else "Fairly valued")
    if mos is None and pe_gap is None and peg is None:
        verdict = "Insufficient data"

    scores = {
        "Valuation (DCF)": score(mos, 30, -30) if mos is not None else None,
        "Valuation (P/E)": score(pe, 12, 45) if pe else None,
        "Valuation (P/B)": score(pb, 1, 8) if pb else None,
        "Valuation (PEG)": score(peg, 1, 3) if peg else None,
        "Growth-adjusted P/E": score(pe_gap, 25, -25) if pe_gap is not None else None,
        "Profitability (ROE)": score((roe or 0) * 100, 22, 5) if roe is not None else None,
        "Operating margin": score((opm or 0) * 100, 20, 5) if opm is not None else None,
        "Net / PAT margin": score((npm or 0) * 100, 15, 2) if npm is not None else None,
        "EBITDA margin": score((ebitda_margin or 0) * 100, 22, 6) if ebitda_margin is not None else None,
        "Revenue growth": score((rev_g or 0) * 100, 18, 0) if rev_g is not None else None,
        "Earnings growth": score((earn_g or 0) * 100, 20, 0) if earn_g is not None else None,
        "Financial health (D/E)": score(de, 30, 150) if de is not None else None,
        "Liquidity (current ratio)": score(cur_ratio, 2, 0.8) if cur_ratio is not None else None,
        "Cash flow (FCF)": (8.0 if (fcf and fcf > 0) else 2.0) if fcf is not None else None,
        "Dividend": score((div_y or 0) * 100, 3, 0) if div_y is not None else None,
        "Momentum / trend": _momentum(tech),
        "Capital efficiency (ROCE)": score(inst.get("ROCE %"), 18, 6) if inst.get("ROCE %") is not None else None,
        "Cash valuation (FCF yield)": score(inst.get("FCF yield %"), 6, 1) if inst.get("FCF yield %") is not None else None,
        "Quality (Piotroski)": round(inst["Piotroski F"] / inst["_piotroski_max"] * 10, 1) if inst.get("Piotroski F") is not None else None,
        "Solvency (Altman Z)": score(inst.get("Altman Z"), 3, 1.0) if inst.get("Altman Z") is not None else None,
    }
    valid = {k: v for k, v in scores.items() if v is not None}
    overall = round(sum(valid.values()) / len(valid), 1) if valid else None
    w = STYLE_WEIGHTS.get(style, STYLE_WEIGHTS["balanced"])
    wsum = sum(w.get(_style_cat(k), 1) for k in valid)
    style_score = round(sum(valid[k] * w.get(_style_cat(k), 1) for k in valid) / wsum, 1) if wsum else overall
    style_take = "Strong fit" if (style_score or 0) >= 7 else ("Moderate fit" if (style_score or 0) >= 5 else "Weak fit")

    mktcap_inr = mktcap * fx if (mktcap and fx) else None
    cap_cat = cap_category(mktcap_inr)
    tags = [c for c in [cap_cat,
                        ("Dividend payer" if (div_y and div_y > 0.015) else None),
                        ("Aggressive (high beta)" if (beta and beta > 1.3) else ("Defensive (low beta)" if (beta and beta < 0.8) else None))] if c]

    # ratios with units, benchmark, INR conversion (+ institutional metrics merged in)
    raw = {"P/E (trailing)": pe, "P/E (forward)": fpe, "P/B": pb, "PEG": peg,
           "EV/EBITDA": inst.get("EV/EBITDA"), "P/S": inst.get("P/S"), "FCF yield %": inst.get("FCF yield %"),
           "ROE %": _pct(roe), "ROCE %": inst.get("ROCE %"), "ROIC %": inst.get("ROIC %"),
           "Operating margin %": _pct(opm), "Net/PAT margin %": _pct(npm), "Debt/Equity %": de,
           "Interest coverage": inst.get("Interest coverage"), "Current ratio": cur_ratio,
           "Revenue growth %": _pct(rev_g), "Earnings growth %": _pct(earn_g),
           "Revenue CAGR 3y %": inst.get("Revenue CAGR 3y %"), "Earnings CAGR 3y %": inst.get("Earnings CAGR 3y %"),
           "Altman Z": inst.get("Altman Z"), "Piotroski F": inst.get("Piotroski F"),
           "EBITDA": ebitda, "PAT (net income)": pat, "Free cash flow": fcf, "Dividend yield %": _pct(div_y), "Beta": beta}
    ratios = {}
    for k, v in raw.items():
        item = {"value": v, "unit": BENCH.get(k, {}).get("unit", "")}
        if k in MONEY_KEYS:
            item["unit"] = sym
            item["inr"] = (v * fx) if (v is not None and fx) else None
        rm = rate_metric(k, v)
        if rm:
            item.update(rm)
        ratios[k] = item

    peers, sec_name = find_peers(ticker, sector)
    news = google_news(name.split(" Ltd")[0].split(" Limited")[0] + " stock")
    sector_news = google_news((sec_name or sector or "Indian stock market") + " sector India outlook", n=6)
    sec_tally = {"pos": sum(1 for n in sector_news if n["tone"] == "pos"), "neg": sum(1 for n in sector_news if n["tone"] == "neg")}

    greens, reds = _flags(roe, de, fcf, rev_g, earn_g, pe, pb, npm, cur_ratio, news, tech, mos)
    alloc = _allocation(capital, price, overall, rp, sym)
    plan = portfolio_plan(capital, risk, horizon, style, verdict, alloc)
    rec = recommendation(overall, verdict, tech, style_take, alloc.get("suggested_weight_pct", "—"))

    methodology = {
        "ratios_source": "Yahoo Finance fundamentals (trailing-twelve-month). Cross-check on screener.in (linked).",
        "statements_used": hist,
        "fcf": {"value": fcf, "source": fcf_src, "components": fcf_detail},
        "dcf": {"fcf": fcf, "growth_pct": round(g * 100, 1), "discount_pct": round(rp["discount"] * 100, 1),
                "years": proj_years, "terminal_growth_pct": 3.0, "fair_value": fair,
                "formula": f"Fair value = Σ[ FCF×(1+{g:.0%})^t ÷ (1+{rp['discount']:.0%})^t ] for t=1..{proj_years}, "
                           f"plus terminal = last-year FCF×1.03 ÷ ({rp['discount']:.0%}−3%), discounted back."},
        "reverse_dcf": {"implied_growth_pct": implied_g, "meaning": "The annual FCF growth the current market cap already assumes."},
        "fx_used": {"pair": f"{cur_code}INR", "rate": fx},
    }

    return {"ticker": ticker, "name": name, "sector": sector, "industry": industry, "tags": tags,
            "cap_category": cap_cat, "currency": sym, "currency_code": cur_code, "fx_inr": fx,
            "price": price, "price_inr": (price * fx) if (price and fx) else None,
            "marketCap": mktcap, "marketCap_inr": mktcap_inr, "summary": _g(info, "longBusinessSummary"),
            "ratios": ratios, "scores": scores, "overall": overall,
            "style": {"id": style, "label": STYLE_LABEL.get(style, style), "score": style_score, "take": style_take},
            "styles_fit": _styles_fit(roe, de, pe, pb, peg, earn_g, rev_g, fcf, div_y, beta, opm),
            "valuation": {"fair_value_mktcap": fair, "current_mktcap": mktcap, "margin_of_safety_pct": mos,
                          "verdict": verdict, "dcf_verdict": dcf_verdict, "growth_used_pct": round(g * 100, 1),
                          "discount_pct": round(rp["discount"] * 100, 1), "implied_growth_pct": implied_g,
                          "proj_years": proj_years, "fcf": fcf, "fcf_source": fcf_src,
                          "growth_pct": gr_pct, "fair_pe": fair_pe, "current_pe": pe, "pe_gap_pct": pe_gap,
                          "pe_growth_verdict": pe_growth_verdict, "peg": peg,
                          "combined_reason": " · ".join(reasons),
                          "scenarios": scenarios,
                          "method_note": "Combined verdict blends DCF, growth-adjusted fair P/E and PEG — not just P/E & P/B. A high P/E can still be 'fair' if growth/quality justify paying up for the future."},
            "quality": {"piotroski": inst.get("Piotroski F"), "piotroski_max": inst.get("_piotroski_max"),
                        "altman_z": inst.get("Altman Z"), "roce": inst.get("ROCE %"), "roic": inst.get("ROIC %"),
                        "ev_ebitda": inst.get("EV/EBITDA"), "fcf_yield": inst.get("FCF yield %"),
                        "interest_coverage": inst.get("Interest coverage"),
                        "note": "Institutional quality & solvency screens computed from the financial statements."},
            "history": hist, "technical": tech,
            "institutional": {"pct": inst_pct, "holders": holders,
                              "note": "Total institutional % from Yahoo. FII vs DII split → NSE shareholding pattern (linked below)."},
            "corporate_actions": actions, "news": news,
            "sector_analysis": {"name": sec_name or sector, "news": sector_news, "tally": sec_tally,
                                "note": "Sector sentiment from free Google News (last 14 days). Twitter/X is excluded (paid API)."},
            "peers": peers, "peer_sector": sec_name,
            "allocation": alloc, "portfolio_plan": plan, "recommendation": rec,
            "green_flags": greens, "red_flags": reds, "methodology": methodology,
            "research": {"verdict": research_verdict(overall, verdict), "strengths": greens, "risks": reds,
                         "why": f"Overall {overall}/10, DCF says {verdict.lower()}, {style_take.lower()} for a {STYLE_LABEL.get(style, style)} approach."},
            "data_quality": notes, "references": references(ticker)}


def _momentum(tech):
    d = tech.get("daily")
    if not d:
        return None
    s = 5 + (2 if d.get("above_ema") else 0)
    r = d.get("rsi")
    if r is not None:
        s += 1.5 if r < 30 else (-1.5 if r > 70 else 0)
    return round(max(0, min(10, s)), 1)


def _styles_fit(roe, de, pe, pb, peg, earn_g, rev_g, fcf, div_y, beta, opm):
    c = bool
    defs = {STYLE_LABEL["buffett"]: {"ROE > 15%": c(roe and roe > 0.15), "Debt/Equity < 60%": c(de is not None and de < 60),
                                     "Operating margin > 15%": c(opm and opm > 0.15), "Positive FCF": c(fcf and fcf > 0), "P/E < 25": c(pe and pe < 25)},
            STYLE_LABEL["growth"]: {"Revenue growth > 15%": c(rev_g and rev_g > 0.15), "Earnings growth > 15%": c(earn_g and earn_g > 0.15),
                                    "PEG < 1.5": c(peg and peg < 1.5), "Operating margin > 12%": c(opm and opm > 0.12)},
            STYLE_LABEL["value"]: {"P/B < 1.5": c(pb and pb < 1.5), "P/E < 15": c(pe and pe < 15), "Pays dividend": c(div_y and div_y > 0), "D/E < 50%": c(de is not None and de < 50)},
            STYLE_LABEL["dividend"]: {"Beta < 1": c(beta and beta < 1), "Pays dividend": c(div_y and div_y > 0), "D/E < 40%": c(de is not None and de < 40), "Positive FCF": c(fcf and fcf > 0)}}
    return {n: {"checks": d, "fit_pct": round(sum(1 for v in d.values() if v) / len(d) * 100)} for n, d in defs.items()}


def _allocation(capital, price, overall, rp, sym):
    if not price or not capital:
        return {"note": "Need a valid price and capital to size the position.", "currency": sym}
    conviction = (overall or 5) / 10
    weight = round(rp["max_weight"] * conviction, 4)
    shares = int((capital * weight) // price)
    spent = round(shares * price, 2)
    return {"currency": sym, "capital": capital, "price": price, "suggested_weight_pct": round(weight * 100, 1),
            "conviction_from_score": round(conviction * 100), "shares": shares, "amount": spent, "cash_left": round(capital - spent, 2),
            "note": f"Capped at {round(rp['max_weight']*100)}% (your risk band), scaled by {round(conviction*100)}% conviction from the overall score."}


def _flags(roe, de, fcf, rev_g, earn_g, pe, pb, npm, cur_ratio, news, tech, mos):
    g, r = [], []
    if roe and roe > 0.18: g.append(f"Strong ROE {roe*100:.0f}%")
    if fcf and fcf > 0: g.append("Positive free cash flow")
    if (earn_g or 0) > 0.15: g.append(f"Earnings growing {earn_g*100:.0f}% YoY")
    if de is not None and de < 50: g.append(f"Low leverage (D/E {de:.0f}%)")
    if mos is not None and mos > 20: g.append(f"~{mos:.0f}% below DCF fair value")
    if npm and npm > 0.15: g.append(f"Healthy net margin {npm*100:.0f}%")
    if de is not None and de > 120: r.append(f"High leverage (D/E {de:.0f}%)")
    if fcf is not None and fcf < 0: r.append("Negative free cash flow")
    if pe and pe > 45: r.append(f"Expensive (P/E {pe:.0f})")
    if pb and pb > 8: r.append(f"Rich on book (P/B {pb:.1f})")
    if cur_ratio is not None and cur_ratio < 1: r.append(f"Current ratio < 1 ({cur_ratio:.2f})")
    if mos is not None and mos < -20: r.append(f"~{abs(mos):.0f}% above DCF fair value")
    negs = [n for n in news if n["tone"] == "neg"]
    if negs: r.append(f"{len(negs)} negative-toned headline(s)")
    d = tech.get("daily")
    if d and d.get("rsi") and d["rsi"] > 75: r.append(f"Daily RSI {d['rsi']:.0f} (overbought)")
    return g[:5], r[:5]


# ---------------------------- CMT-style technical engine ----------------------------
def _adx(high, low, close, n=14):
    up, down = high.diff(), -low.diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=high.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=high.index)
    tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / n, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1 / n, adjust=False).mean() / atr
    minus_di = 100 * minus_dm.ewm(alpha=1 / n, adjust=False).mean() / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False).mean(), plus_di, minus_di, atr


def _swings(arr, order=4):
    hi, lo = [], []
    for i in range(order, len(arr) - order):
        w = arr[i - order:i + order + 1]
        if arr[i] == w.max(): hi.append(i)
        if arr[i] == w.min(): lo.append(i)
    return hi, lo


def _slope(idx, vals):
    if len(idx) < 2:
        return 0.0
    m = np.polyfit(idx, vals, 1)[0]
    return float(m / (np.mean(vals) or 1))  # normalized per-bar slope


def detect_patterns(o, h, l, c):
    """Heuristic but rule-based pattern detection. Returns list of {name,bias,confidence,detail,points}.
    points are indices into the series (for charting). Conservative — only flags when criteria met."""
    out = []
    n = len(c)
    if n < 30:
        return out
    body = abs(c - o)
    rng = (h - l).replace(0, np.nan)
    up5 = c.iloc[-1] / c.iloc[-6] - 1 if n > 6 else 0
    # --- candlesticks (last 1-2 bars) ---
    o1, c1, h1, l1 = o.iloc[-1], c.iloc[-1], h.iloc[-1], l.iloc[-1]
    o2, c2 = o.iloc[-2], c.iloc[-2]
    b1 = abs(c1 - o1)
    if c2 < o2 and c1 > o1 and c1 >= o2 and o1 <= c2:
        out.append({"name": "Bullish Engulfing", "bias": "bullish", "confidence": "medium", "points": [n - 2, n - 1],
                    "detail": "Today's green candle fully engulfs yesterday's red body — buyers took control."})
    if c2 > o2 and c1 < o1 and c1 <= o2 and o1 >= c2:
        out.append({"name": "Bearish Engulfing", "bias": "bearish", "confidence": "medium", "points": [n - 2, n - 1],
                    "detail": "Today's red candle engulfs yesterday's green body — sellers took control."})
    low_sh, up_sh = min(o1, c1) - l1, h1 - max(o1, c1)
    if b1 > 0 and low_sh > 2 * b1 and up_sh < b1 and up5 < 0:
        out.append({"name": "Hammer", "bias": "bullish", "confidence": "low", "points": [n - 1],
                    "detail": "Long lower wick after a fall — buyers rejected lower prices (reversal hint)."})
    if b1 > 0 and up_sh > 2 * b1 and low_sh < b1 and up5 > 0:
        out.append({"name": "Shooting Star", "bias": "bearish", "confidence": "low", "points": [n - 1],
                    "detail": "Long upper wick after a rise — sellers rejected higher prices."})
    # --- SMA50/200 cross ---
    sma50, sma200 = c.rolling(50).mean(), c.rolling(200).mean()
    if n > 205 and not (pd.isna(sma200.iloc[-2]) or pd.isna(sma200.iloc[-1])):
        if sma50.iloc[-2] <= sma200.iloc[-2] and sma50.iloc[-1] > sma200.iloc[-1]:
            out.append({"name": "Golden Cross", "bias": "bullish", "confidence": "high", "points": [n - 1],
                        "detail": "50-day average crossed above the 200-day — classic long-term uptrend signal."})
        if sma50.iloc[-2] >= sma200.iloc[-2] and sma50.iloc[-1] < sma200.iloc[-1]:
            out.append({"name": "Death Cross", "bias": "bearish", "confidence": "high", "points": [n - 1],
                        "detail": "50-day average crossed below the 200-day — classic downtrend warning."})
    # --- swing-based chart patterns (last ~80 bars) ---
    seg = min(n, 80)
    cc = c.iloc[-seg:].values
    base = n - seg
    hi, lo = _swings(cc, order=4)
    ph = [(i, cc[i]) for i in hi]
    pl = [(i, cc[i]) for i in lo]
    last = cc[-1]
    if len(ph) >= 2:
        (i1, p1), (i2, p2) = ph[-2], ph[-1]
        trough = min(cc[i1:i2]) if i2 > i1 else None
        if abs(p1 - p2) / p1 < 0.04 and trough and (min(p1, p2) - trough) / min(p1, p2) > 0.03:
            conf = "medium" if last < trough else "low"
            out.append({"name": "Double Top", "bias": "bearish", "confidence": conf,
                        "points": [base + i1, base + i2], "neckline": float(trough),
                        "detail": "Two peaks at a similar level — a break below the middle trough confirms the top." +
                                  (" Confirmed (price below neckline)." if last < trough else " Forming — watch the neckline.")})
    if len(pl) >= 2:
        (i1, p1), (i2, p2) = pl[-2], pl[-1]
        peak = max(cc[i1:i2]) if i2 > i1 else None
        if abs(p1 - p2) / p1 < 0.04 and peak and (peak - max(p1, p2)) / peak > 0.03:
            conf = "medium" if last > peak else "low"
            out.append({"name": "Double Bottom", "bias": "bullish", "confidence": conf,
                        "points": [base + i1, base + i2], "neckline": float(peak),
                        "detail": "Two troughs at a similar level — a break above the middle peak confirms the bottom." +
                                  (" Confirmed (price above neckline)." if last > peak else " Forming — watch the neckline.")})
    if len(ph) >= 3:
        (ia, a), (ib, b), (ic, cP) = ph[-3], ph[-2], ph[-1]
        if b > a and b > cP and abs(a - cP) / a < 0.05 and (b - max(a, cP)) / b > 0.03:
            out.append({"name": "Head & Shoulders", "bias": "bearish", "confidence": "medium",
                        "points": [base + ia, base + ib, base + ic],
                        "detail": "Three peaks, the middle (head) highest with two even shoulders — a topping pattern."})
    if len(pl) >= 3:
        (ia, a), (ib, b), (ic, cP) = pl[-3], pl[-2], pl[-1]
        if b < a and b < cP and abs(a - cP) / a < 0.05 and (min(a, cP) - b) / min(a, cP) > 0.03:
            out.append({"name": "Inverse Head & Shoulders", "bias": "bullish", "confidence": "medium",
                        "points": [base + ia, base + ib, base + ic],
                        "detail": "Three troughs, the middle (head) lowest with even shoulders — a bottoming pattern."})
    # --- triangles / wedges from swing-line slopes ---
    if len(ph) >= 3 and len(pl) >= 3:
        sh = _slope([i for i, _ in ph[-3:]], [p for _, p in ph[-3:]])
        sl = _slope([i for i, _ in pl[-3:]], [p for _, p in pl[-3:]])
        flat = 0.001
        if sh < -flat and sl > flat:
            out.append({"name": "Symmetric Triangle", "bias": "neutral", "confidence": "low", "points": [],
                        "detail": "Lower highs and higher lows converging — energy building; trade the breakout direction."})
        elif abs(sh) <= flat and sl > flat:
            out.append({"name": "Ascending Triangle", "bias": "bullish", "confidence": "low", "points": [],
                        "detail": "Flat highs with rising lows — buyers pressing; usually breaks upward."})
        elif sh < -flat and abs(sl) <= flat:
            out.append({"name": "Descending Triangle", "bias": "bearish", "confidence": "low", "points": [],
                        "detail": "Falling highs with flat lows — sellers pressing; usually breaks downward."})
        elif sh > flat and sl > flat and sl > sh:
            out.append({"name": "Rising Wedge", "bias": "bearish", "confidence": "low", "points": [],
                        "detail": "Both lines rising but converging — momentum fading; often resolves down."})
        elif sh < -flat and sl < -flat and sh > sl:
            out.append({"name": "Falling Wedge", "bias": "bullish", "confidence": "low", "points": [],
                        "detail": "Both lines falling but converging — selling exhausting; often resolves up."})
    return out


def technical_analysis(ticker, tf="daily"):
    t = yf.Ticker(ticker)
    period, interval = ("2y", "1d") if tf == "daily" else ("6y", "1wk")
    df = t.history(period=period, interval=interval).dropna()
    if len(df) < 60:
        return {"error": "Not enough price history for technical analysis."}
    o, h, l, c, v = df["Open"], df["High"], df["Low"], df["Close"], df["Volume"]
    last = float(c.iloc[-1])

    def lastf(s, nd=2):
        x = s.iloc[-1]
        return None if pd.isna(x) else round(float(x), nd)

    rsi14 = lastf(rsi(c))
    ema12, ema26 = c.ewm(span=12, adjust=False).mean(), c.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    hist_macd = macd - signal
    mid = c.rolling(20).mean(); sd = c.rolling(20).std()
    bb_up, bb_lo = mid + 2 * sd, mid - 2 * sd
    pctb = lastf((c - bb_lo) / (bb_up - bb_lo).replace(0, np.nan), 2)
    lk, hk = l.rolling(14).min(), h.rolling(14).max()
    k = 100 * (c - lk) / (hk - lk).replace(0, np.nan)
    d_st = k.rolling(3).mean()
    adx, pdi, mdi = _adx(h, l, c)[0:3]
    roc = lastf(c.pct_change(10) * 100, 1)
    obv = (np.sign(c.diff()).fillna(0) * v).cumsum()
    obv_slope = "rising" if obv.iloc[-1] > obv.iloc[-10] else "falling"
    sma50, sma200 = lastf(c.rolling(50).mean()), lastf(c.rolling(200).mean())
    atr = lastf(_adx(h, l, c)[3])

    # relative strength vs benchmark (CMT): is the stock out/under-performing the index?
    rs6 = None
    look = 126 if tf == "daily" else 26
    try:
        bench = "^NSEI" if ticker.endswith((".NS", ".BO")) else "^GSPC"
        bc = yf.Ticker(bench).history(period=period, interval=interval)["Close"].dropna()
        if len(c) > look and len(bc) > look:
            stock_ret = float(c.iloc[-1] / c.iloc[-look] - 1) * 100
            idx_ret = float(bc.iloc[-1] / bc.iloc[-look] - 1) * 100
            rs6 = round(stock_ret - idx_ret, 1)
    except Exception:
        pass

    def sig(cond_bull, cond_bear, bull="Bullish", bear="Bearish", neut="Neutral"):
        return bull if cond_bull else (bear if cond_bear else neut)

    indicators = {
        "RSI (14)": {"value": rsi14, "signal": sig(rsi14 is not None and rsi14 < 30, rsi14 is not None and rsi14 > 70, "Oversold (bullish)", "Overbought (bearish)"),
                     "use": "Momentum oscillator. <30 oversold (bounce odds), >70 overbought (pullback odds)."},
        "MACD": {"value": lastf(macd), "signal_line": lastf(signal), "hist": lastf(hist_macd),
                 "signal": sig(macd.iloc[-1] > signal.iloc[-1], macd.iloc[-1] < signal.iloc[-1]),
                 "use": "Trend+momentum. MACD above its signal line = bullish; histogram shows momentum strength."},
        "Stochastic %K/%D": {"value": lastf(k), "d": lastf(d_st),
                             "signal": sig(k.iloc[-1] < 20, k.iloc[-1] > 80, "Oversold", "Overbought"),
                             "use": "Where price sits in its recent range. <20 oversold, >80 overbought."},
        "ADX (14)": {"value": lastf(adx), "plus_di": lastf(pdi), "minus_di": lastf(mdi),
                     "signal": ("Strong trend" if (adx.iloc[-1] or 0) > 25 else "Weak/!trend") + (" up" if pdi.iloc[-1] > mdi.iloc[-1] else " down"),
                     "use": "Trend STRENGTH (not direction). >25 = trending; +DI vs −DI shows which side."},
        "Bollinger %B": {"value": pctb, "signal": sig(pctb is not None and pctb < 0.05, pctb is not None and pctb > 0.95, "At lower band", "At upper band"),
                         "use": "Position within volatility bands. Near 0 = lower band (cheap), near 1 = upper band (stretched)."},
        "Momentum / ROC (10)": {"value": roc, "signal": sig((roc or 0) > 0, (roc or 0) < 0, "Positive", "Negative"),
                                "use": "10-bar rate of change. Positive = upward momentum."},
        "OBV": {"value": obv_slope, "signal": "Bullish" if obv_slope == "rising" else "Bearish",
                "use": "On-Balance Volume — is volume confirming price? Rising OBV backs an uptrend."},
        "ATR (14)": {"value": atr, "signal": "Volatility", "use": "Average True Range — typical bar size; size stops/positions off it."},
        "SMA 50 / 200": {"value": sma50, "sma200": sma200, "signal": sig(sma50 and sma200 and sma50 > sma200, sma50 and sma200 and sma50 < sma200, "Uptrend (50>200)", "Downtrend (50<200)"),
                         "use": "Trend backbone. Price & 50-day above 200-day = healthy long-term uptrend."},
        "Relative Strength vs index": {"value": rs6, "signal": sig((rs6 or 0) > 2, (rs6 or 0) < -2, "Outperforming", "Underperforming"),
                                       "use": "Stock return minus index return over ~6 months. Positive = leadership (CMT relative strength)."},
    }
    patterns = detect_patterns(o, h, l, c)
    tailN = 140
    chart = {"dates": [str(d.date()) for d in c.tail(tailN).index],
             "close": [round(float(x), 2) for x in c.tail(tailN)],
             "sma50": [None if pd.isna(x) else round(float(x), 2) for x in c.rolling(50).mean().tail(tailN)],
             "sma200": [None if pd.isna(x) else round(float(x), 2) for x in c.rolling(200).mean().tail(tailN)],
             "bb_up": [None if pd.isna(x) else round(float(x), 2) for x in bb_up.tail(tailN)],
             "bb_lo": [None if pd.isna(x) else round(float(x), 2) for x in bb_lo.tail(tailN)],
             "base": len(c) - min(len(c), tailN)}  # to map pattern indices into the tail
    # net read
    bull = sum(1 for v in indicators.values() if "ullish" in v["signal"] or "Oversold" in v["signal"] or "Uptrend" in v["signal"] or "Positive" in v["signal"] or "up" in v["signal"])
    bear = sum(1 for v in indicators.values() if "earish" in v["signal"] or "Overbought" in v["signal"] or "Downtrend" in v["signal"] or "Negative" in v["signal"] or "down" in v["signal"])
    bias = "Bullish" if bull > bear + 1 else ("Bearish" if bear > bull + 1 else "Mixed / Neutral")
    return {"tf": tf, "price": last, "indicators": indicators, "patterns": patterns,
            "chart": chart, "net_bias": bias, "bull_count": bull, "bear_count": bear,
            "note": "CMT-style indicators + rule-based pattern detection. Patterns are heuristics to confirm visually, not guarantees."}


def industry_pe(ticker, sector):
    """Median P/E of local (Indian) sector peers vs the mapped global (US) sector — connects micro to macro."""
    GLOBAL_MAP = {"Defense": "Defense", "IT / Software": "Technology", "Banking": "Finance", "Pharma": "Healthcare",
                  "Auto": "EV / Auto", "Energy / Power": "Technology", "Metals": "Semiconductors", "FMCG": "Healthcare"}
    local_peers, sec_name = find_peers(ticker, sector)
    def median_pe(syms):
        pes = []
        for s in syms[:6]:
            pe = _g(enriched_info(s), "trailingPE")
            if pe and 0 < pe < 200:
                pes.append(pe)
        return round(float(np.median(pes)), 1) if pes else None, len(pes)
    local_med, ln = median_pe(local_peers)
    g_sector = GLOBAL_MAP.get(sec_name)
    g_syms = UNIVERSE["USA"].get(g_sector, []) if g_sector else []
    global_med, gn = median_pe(g_syms)
    return {"local_sector": sec_name, "local_industry_pe": local_med, "local_n": ln,
            "global_sector": g_sector, "global_industry_pe": global_med, "global_n": gn,
            "note": "Median trailing P/E of sector peers. Local vs global shows whether the whole industry is re-rated worldwide."}


# ---------------------------- portfolio evaluation & smart rebalancing ----------------------------
HORIZON_DAYS = {"short": 60, "medium": 180, "long": 365}


def evaluate_holding(sym, buy, horizon, bench_ret6m):
    """Evaluate one holding like a standalone stock: fundamentals + technicals → health/verdict,
    plus statistical SL/TP (ATR + 1σ horizon move) and P&L vs the average buy price.
    Fundamentals are reconstructed from the actual financial statements when Yahoo's `info`
    feed is blocked (cloud IPs), so D/E, P/E, ROE etc. are real numbers, never blank/hypothetical."""
    t = yf.Ticker(sym)
    info = dict(get_info(sym))
    fast = _fast(t)
    fin = bs = None
    try: fin = t.financials
    except Exception: pass
    try: bs = t.balance_sheet
    except Exception: pass
    out = {"sym": sym, "name": _g(info, "shortName", "longName") or sym, "buy": buy}
    try:
        df = t.history(period="1y").dropna()
    except Exception:
        df = None
    if df is None or len(df) < 60:
        out["error"] = "no price data"; return out
    c, h, l = df["Close"], df["High"], df["Low"]
    price = float(c.iloc[-1])
    # fill any missing fundamentals from the real statements (works even when info is blocked)
    for k, v in _compute_fundamentals(fin, bs, None, fast, price, None).items():
        if _g(info, k) is None and v is not None:
            info[k] = v
    inst = institutional_metrics(fin, bs, None, _g(info, "marketCap"), _g(info, "ebitda"), None)
    out["sector"] = _g(info, "sector")
    rsi14 = float(rsi(c).iloc[-1])
    sma50 = float(c.rolling(50).mean().iloc[-1])
    sma200 = float(c.rolling(200).mean().iloc[-1]) if len(c) >= 200 else None
    above200 = bool(sma200 is not None and price > sma200)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = float(tr.rolling(14).mean().iloc[-1])
    dvol = float(c.pct_change().std())
    ret6m = float(c.iloc[-1] / c.iloc[-126] - 1) * 100 if len(c) > 126 else None
    rs = round(ret6m - bench_ret6m, 1) if (ret6m is not None and bench_ret6m is not None) else None
    pe, roe = _g(info, "trailingPE"), _g(info, "returnOnEquity")
    eg, de, pb = _g(info, "earningsGrowth", "earningsQuarterlyGrowth"), _g(info, "debtToEquity"), _g(info, "priceToBook")
    npm = _pct(_g(info, "profitMargins"))
    roce, ev_ebitda = inst.get("ROCE %"), inst.get("EV/EBITDA")
    cagr = inst.get("Earnings CAGR 3y %")
    profitable = (npm is not None and npm > 0)
    fund, fund_note = fund_rating(_pct(roe), roce, de, pb, npm, _pct(eg), pe, profitable)
    tech = 5.0 + (2 if above200 else 0) + (1 if (sma50 and sma200 and sma50 > sma200) else 0)
    tech += (1 if rsi14 < 30 else (-1.5 if rsi14 > 70 else 0)) + (1 if (rs and rs > 0) else 0)
    tech = round(max(0, min(10, tech)), 1)
    if fund is None:                                  # incomplete fundamentals → don't fake a verdict
        health, verdict = None, "Insufficient data"
    else:
        health = round(fund * 0.6 + tech * 0.4, 1)    # fundamentals weighted for an investor
        verdict = "Accumulate" if health >= 7 else ("Hold" if health >= 5.5 else ("Reduce" if health >= 4 else "Exit"))
    T = HORIZON_DAYS.get(horizon, 180)
    exp_move_pct = round(dvol * math.sqrt(min(T, 252)) * 100, 1)  # 1σ expected move over the horizon
    sl, tp = round(price - 2 * atr, 2), round(price + 3 * atr, 2)  # ATR stop/target, ~1.5 reward:risk
    out.update({"price": round(price, 2), "rsi": round(rsi14, 1), "above_200dma": above200,
                "sma_uptrend": bool(sma50 and sma200 and sma50 > sma200), "atr": round(atr, 2),
                "rel_strength_6m": rs, "pe": pe, "pb": pb, "roe_pct": _pct(roe), "roce_pct": roce,
                "net_margin_pct": npm, "ev_ebitda": ev_ebitda, "marketCap": _g(info, "marketCap"),
                "earnings_growth_pct": _pct(eg), "de": de, "earnings_cagr_3y": cagr, "profitable": profitable,
                "fund_score": fund, "fund_note": fund_note, "tech_score": tech, "health": health, "verdict": verdict,
                "pnl_pct": round((price / buy - 1) * 100, 1) if buy else None,
                "sl": sl, "sl_pct": round((sl / price - 1) * 100, 1),
                "tp": tp, "tp_pct": round((tp / price - 1) * 100, 1), "expected_move_pct": exp_move_pct,
                "sl_buy": round(buy - 2 * atr, 2) if buy else None,
                "tp_buy": round(buy + 3 * atr, 2) if buy else None})
    return out


def peer_metrics(sym):
    """Fundamentals for a peer, computed from REAL statements (works on cloud) and rated only
    when the full metric set is present (else quality=None — never a partial/inflated score)."""
    t = yf.Ticker(sym)
    info = dict(get_info(sym))
    fast = _fast(t)
    fin = bs = None
    try: fin = t.financials
    except Exception: pass
    try: bs = t.balance_sheet
    except Exception: pass
    price, mom = fast.get("last_price"), None
    try:
        hh = t.history(period="6mo")["Close"].dropna()
        if len(hh) > 5:
            if price is None:
                price = float(hh.iloc[-1])
            mom = round(float(hh.iloc[-1] / hh.iloc[0] - 1) * 100, 1)
    except Exception:
        pass
    for k, v in _compute_fundamentals(fin, bs, None, fast, price, None).items():
        if _g(info, k) is None and v is not None:
            info[k] = v
    inst = institutional_metrics(fin, bs, None, _g(info, "marketCap"), _g(info, "ebitda"), None)
    pe, roe, roce = _g(info, "trailingPE"), _pct(_g(info, "returnOnEquity")), inst.get("ROCE %")
    de, pb, npm = _g(info, "debtToEquity"), _g(info, "priceToBook"), _pct(_g(info, "profitMargins"))
    eg = _pct(_g(info, "earningsGrowth", "earningsQuarterlyGrowth"))
    q, note = fund_rating(roe, roce, de, pb, npm, eg, pe, (npm is not None and npm > 0))
    return {"ticker": sym, "name": _g(info, "shortName", "longName") or sym, "pe": pe, "roe": roe,
            "roce": roce, "de": de, "pb": pb, "net_margin": npm, "momentum_6m": mom, "quality": q, "rating_note": note}


def portfolio_eval(holdings, horizon, extra_capital):
    holdings = holdings[:10]
    indian = any(h["sym"].endswith((".NS", ".BO")) for h in holdings)
    bench = "^NSEI" if indian else "^GSPC"
    bench_ret6m = None
    try:
        bc = yf.Ticker(bench).history(period="1y")["Close"].dropna()
        if len(bc) > 126:
            bench_ret6m = float(bc.iloc[-1] / bc.iloc[-126] - 1) * 100
    except Exception:
        pass
    evals = [evaluate_holding(h["sym"], h.get("buy", 0), horizon, bench_ret6m) for h in holdings]
    valid = [e for e in evals if "error" not in e]
    if not valid:
        return {"error": "Couldn't fetch data for these holdings."}
    qty = {h["sym"]: h.get("qty", 0) for h in holdings}
    for e in valid:
        e["value"] = round(e["price"] * qty.get(e["sym"], 0))
    total = sum(e["value"] for e in valid) or 1
    for e in valid:
        e["weight_pct"] = round(e["value"] / total * 100, 1)
    rated = [e for e in valid if e["health"] is not None]
    rval = sum(e["value"] for e in rated) or 1
    overall_health = round(sum(e["health"] * e["value"] for e in rated) / rval, 1) if rated else None
    overall_verdict = ("Not rated — incomplete data" if overall_health is None else
                       "Healthy" if overall_health >= 6.5 else ("Mixed — prune the weak names" if overall_health >= 5 else "Needs work — several holdings underperform"))
    blended_cagr = [e["earnings_cagr_3y"] for e in valid if e.get("earnings_cagr_3y") is not None]
    port_cagr = round(float(np.median(blended_cagr)), 1) if blended_cagr else None

    secw = {}
    for e in valid:
        secw[e.get("sector") or "Other"] = secw.get(e.get("sector") or "Other", 0) + e["value"]
    sectors = [{"sector": k, "pct": round(v / total * 100, 1)} for k, v in sorted(secw.items(), key=lambda x: -x[1])]
    conc = sectors[0]["pct"] if sectors else 0
    rotation = []
    for label, sym in IDX_SECTOR.items():
        try:
            hh = yf.Ticker(sym).history(period="1mo")["Close"].dropna()
            if len(hh) > 2:
                rotation.append({"sector": label, "ret_1m_pct": round(float(hh.iloc[-1] / hh.iloc[0] - 1) * 100, 1)})
        except Exception:
            pass
    rotation.sort(key=lambda x: -x["ret_1m_pct"])

    to_trim = [e for e in valid if e["verdict"] in ("Reduce", "Exit")]
    to_add = [e for e in valid if e["verdict"] == "Accumulate"]
    weak = [e for e in valid if e["verdict"] in ("Reduce", "Exit")]
    freed = sum(e["value"] * (0.5 if e["verdict"] == "Reduce" else 1.0) for e in to_trim)
    # Full peer comparison is done on demand per sector via /peer_eval (real statements, fast).

    have = extra_capital > 0
    plan = []
    if have:
        targets = to_add or [e for e in valid if (e["health"] or 0) >= 6]
        if targets:
            per = round(extra_capital / len(targets))
            plan.append(f"💰 You have ₹{extra_capital:,.0f} to deploy → add ≈₹{per:,} to each of: " +
                        ", ".join(t["sym"].replace(".NS", "") for t in targets) + ".")
        under = [e for e in valid if e.get("pnl_pct") is not None and e["pnl_pct"] < 0 and e["verdict"] == "Accumulate"]
        if under:
            plan.append("📉 Average down (thesis intact but price is below your buy): " +
                        ", ".join(f"{e['sym'].replace('.NS','')} ({e['pnl_pct']}%)" for e in under) + ".")
    else:
        if to_trim:
            plan.append("🔁 No fresh cash → rebalance from within: trim/exit " +
                        ", ".join(e["sym"].replace(".NS", "") for e in to_trim) +
                        f" (frees ≈₹{round(freed):,}), then redeploy into " +
                        (", ".join(t["sym"].replace(".NS", "") for t in to_add) or "your strongest holdings") + ".")
        else:
            plan.append("✅ No fresh cash and no weak names — hold. Only rebalance if a single sector exceeds ~30%.")
    if conc > 30 and rotation:
        plan.append(f"⚠️ {sectors[0]['sector']} is {conc}% of the book — concentration risk. Leading sectors now: " +
                    ", ".join(f"{r['sector']} ({r['ret_1m_pct']:+}%)" for r in rotation[:3]) + ". Tilt trims toward leaders.")

    # explicit step-by-step rebalancing recipe
    horizon_word = {"short": "≤3-year", "medium": "3–7-year", "long": "7-year+"}.get(horizon, "")
    steps = [
        "Score — read each holding's verdict (Accumulate / Hold / Reduce / Exit) in the table below.",
        ("Trim first — sell the Exit names and halve the Reduce names" + (f" ({', '.join(e['sym'].replace('.NS','') for e in to_trim)})" if to_trim else " (none right now)") + f"; that frees ≈₹{round(freed):,}."),
        f"Fix concentration — keep any one sector under ~30% (yours: {sectors[0]['sector']} {conc}%). Trim the excess.",
        ("Redeploy — put freed cash" + (f" + your ₹{extra_capital:,.0f} new cash" if have else "") +
         " into the Accumulate names, preferring those trading below your buy price (average down) and in the leading sectors above."),
        f"Set levels — use each holding's SL/TP (from current price AND from your avg buy). For a {horizon_word} horizon the real exit is a thesis break (verdict turns Reduce/Exit or the sector outlook sours), not a small price wobble.",
    ]
    # sector news for the top sectors (horizon-framed: what could move them in coming months)
    sector_news = {}
    for s in sectors[:3]:
        nm = s["sector"]
        sector_news[nm] = google_news(f"{nm} sector India outlook policy", n=3)

    # --- decisive, explained "if I were you" final verdict ---
    def nm(e): return e["sym"].replace(".NS", "").replace(".BO", "")
    sell = [e for e in valid if e["verdict"] == "Exit"]
    trim = [e for e in valid if e["verdict"] == "Reduce"]
    avg_down = [e for e in to_add if e.get("pnl_pct") is not None and e["pnl_pct"] < 0]
    add_more = [e for e in to_add if e not in avg_down]
    hold = [e for e in valid if e["verdict"] == "Hold"]
    unrated = [e for e in valid if e["verdict"] == "Insufficient data"]
    lead = rotation[0]["sector"] if rotation else None
    fv = []
    if sell:
        fv.append({"do": "Sell fully: " + ", ".join(nm(e) for e in sell),
                   "why": "These failed the fundamental bar (loss-making, or weak ROCE/ROE) AND the chart is below its 200-day average. You're tying capital up in a falling, low-quality asset. Exit and move the money to your strongest names below."})
    if trim:
        fv.append({"do": "Trim ~half: " + ", ".join(nm(e) for e in trim),
                   "why": "Mediocre quality or stretched technicals — not a conviction buy. Halving banks some gains and cuts risk while keeping a small stake in case the story improves."})
    if conc > 30:
        fv.append({"do": f"Diversify — cut {sectors[0]['sector']} from {conc}% to ≤30%",
                   "why": f"Over a third of your book rides on one sector; one bad cycle or policy there hits everything at once. Sell the weakest name in that sector first, then spread into the current leaders ({', '.join(r['sector'] for r in rotation[:2]) if rotation else 'stronger sectors'})."})
    if avg_down:
        adl = ", ".join(nm(e) + " (" + str(e["pnl_pct"]) + "%)" for e in avg_down)
        fv.append({"do": "Average down (in 2–3 tranches): " + adl,
                   "why": "These score well on fundamentals AND trade below your buy price — adding lowers your average cost on a name you'd happily buy fresh today. Stagger the buys; don't deploy it all in one go."})
    if add_more:
        fv.append({"do": "Add / let run: " + ", ".join(nm(e) for e in add_more),
                   "why": "Your strongest names (high health, in an uptrend). Put new cash here first, and resist trimming a winner just because it's up."})
    if unrated:
        fv.append({"do": "Research before acting: " + ", ".join(nm(e) for e in unrated),
                   "why": "I couldn't fetch the full set of fundamentals for these, so I won't fake a verdict. Pull the latest annual report / quarterly result before deciding."})
    if not (sell or trim or avg_down or add_more) and hold:
        fv.append({"do": "Hold: " + ", ".join(nm(e) for e in hold),
                   "why": "Decent but not compelling in either direction — no action needed. Activity for its own sake just adds cost and tax. Re-check after the next results season."})
    if not fv:
        fv.append({"do": "Hold everything", "why": "The book is healthy and balanced; nothing to do right now."})
    hl = "This is a **" + (overall_verdict.split("—")[0].strip().lower()) + "** book"
    if overall_health is not None:
        hl += f" scoring **{overall_health}/10**"
    hl += ". Your single biggest move: **" + fv[0]["do"] + "**. "
    if conc > 30:
        hl += f"Your biggest *risk* is the **{conc}% weight in {sectors[0]['sector']}** — fix concentration before chasing returns. "
    if lead:
        hl += f"Point any new money at the leading sector right now (**{lead}**). "
    hl += "Set each holding's SL/TP below; for your horizon the real reason to exit is the thesis breaking (verdict turns Reduce/Exit), not a normal price wobble."

    return {"horizon": horizon, "extra_capital": extra_capital, "total_value": round(total),
            "overall_health": overall_health, "overall_verdict": overall_verdict, "portfolio_earnings_cagr": port_cagr,
            "holdings": sorted(valid, key=lambda e: -(e["health"] if e["health"] is not None else -1)),
            "errors": [e["sym"] for e in evals if "error" in e],
            "sectors": sectors, "concentration_pct": conc, "sector_rotation": rotation,
            "to_trim": [e["sym"] for e in to_trim], "to_add": [e["sym"] for e in to_add],
            "freed_capital": round(freed),
            "final_verdict": {"headline": hl, "actions": fv}, "plan": plan, "steps": steps, "sector_news": sector_news,
            "note": "Per-stock health = 60% fundamentals + 40% technicals. SL = price−2×ATR(14), TP = price+3×ATR (≈1.5 reward:risk); "
                    "expected move = daily σ × √(horizon trading days) = a 1-standard-deviation range. News/policy is qualitative — use it to "
                    "widen/tighten these levels and to confirm a Reduce/Exit. Educational only."}


# ---------------------------- routes ----------------------------
@app.route("/")
def index():
    # Supabase URL + anon key are public-by-design (RLS protects data). Injected from env so they
    # aren't hardcoded in the repo; if unset, the app runs exactly as before (no login UI).
    cfg = ("<script>window.SB_URL=%s;window.SB_KEY=%s;window.AI_ON=%s;</script>"
           % (json.dumps(os.environ.get("SUPABASE_URL", "")), json.dumps(os.environ.get("SUPABASE_ANON_KEY", "")),
              "true" if ai_available() else "false"))
    return Response(HTML.replace("<!--SBCFG-->", cfg), mimetype="text/html")


@app.route("/ai_analyst", methods=["POST"])
def ai_analyst():
    if not ai_available():
        return jresp({"enabled": False, "note": "AI is off on this deployment. Set ANTHROPIC_API_KEY, or a free OpenAI-compatible key (AI_BASE_URL + AI_API_KEY, e.g. Groq) — see README."})
    ip = (request.headers.get("X-Forwarded-For", request.remote_addr or "?")).split(",")[0].strip()
    ok, why = ai_rate_ok(ip)
    if not ok:
        return jresp({"enabled": True, "note": why}, 429)
    d = request.get_json(force=True) or {}
    mode = d.get("mode", "stock")
    ctx = json.dumps(d.get("context") or {})[:6500]
    lessons = (d.get("memory") or {}).get("lessons") or []
    mem_txt = ("YOUR HARD-WON LESSONS from your past calls — APPLY these and do NOT repeat these mistakes:\n- "
               + "\n- ".join(str(x)[:280] for x in lessons[:12]) + "\n\n") if lessons else ""
    if mode == "lessons":
        user = ("Review your PAST CALLS and their actual outcomes below, then distill DURABLE LESSONS so you decide "
                "better next time.\n\nTRACK RECORD (verdict, score, and return since the call):\n" + ctx +
                "\n\nReturn 3–6 SHORT, SPECIFIC, ACTIONABLE lessons — one per line, each starting with '- ' — about what to do "
                "differently (e.g. position sizing, over-weighting momentum, ignoring debt, selling winners early). If the "
                "record is thin, say what to track more. No preamble, just the lessons.")
        try:
            return jresp({"enabled": True, "text": ai_chat(AI_SYSTEM, user, 600)})
        except Exception as e:
            return jresp({"enabled": False, "note": "AI request failed — " + str(e)[:300]}, 502)
    if mode == "portfolio":
        user = ("Act as my PM and review this WHOLE PORTFOLIO from the evaluation below.\n\nDATA:\n" + ctx +
                "\n\nRespond (plain text, concise):\nOVERALL STANCE: 1–2 sentences\nTOP 3 ACTIONS: prioritized bullets (sell/trim/add/average/diversify) with why\n"
                "BIGGEST RISK: 1 line\nVERDICT: one decisive line. Base only on the data + general knowledge; don't invent numbers.")
    elif mode == "scan":
        user = ("These are WATCHLIST candidates with their evaluation. Act as my PM.\n\nDATA:\n" + ctx +
                "\n\nRespond (plain text):\nTOP PICKS TO RESEARCH NOW: rank up to 3 with a one-line reason each\nAVOID/WAIT: any clear passes and why\n"
                "ONE-LINE TAKEAWAY. Base only on the data given; don't invent numbers.")
    elif mode == "bear":
        user = ("Play devil's advocate on this stock — argue ONLY the BEAR CASE.\n\nDATA:\n" + ctx +
                "\n\nRespond (plain text):\nBEAR THESIS: why this could disappoint or be a value trap\nKEY DOWNSIDE RISKS: 3–4 bullets\n"
                "RED FLAGS IN THE DATA: anything weak (debt, margins, valuation, trend)\nWHAT BULLS MAY BE IGNORING: 1–2 bullets\n"
                "STRONGEST REASON NOT TO OWN IT: one line. Use only the data + general knowledge; don't invent numbers.")
    elif mode == "news":
        user = ("Summarize what the recent NEWS means for this stock.\n\nDATA (headlines + analysis):\n" + ctx +
                "\n\nRespond (plain text):\nWHAT MATTERS: 2–3 bullets drawn from the headlines\nNET READ: positive / neutral / negative + why\n"
                "WATCH NEXT: 1–2 things. If the headlines are thin, say so. Summarize ONLY the given headlines — do not invent news.")
    elif mode == "risk":
        user = ("Give a plain-English RISK BRIEFING for this PORTFOLIO from the metrics.\n\nDATA:\n" + ctx +
                "\n\nRespond (plain text):\nBIGGEST RISKS: ranked, drawn from beta / VaR / max-drawdown / sector concentration / correlation\n"
                "WHAT A BAD MONTH LOOKS LIKE: 1–2 lines grounded in the numbers\nWAYS TO CUT RISK: 2–3 concrete moves. Use only the numbers given.")
    elif mode == "coach":
        user = ("Act as a coach reviewing this investor's PAST CALLS (analysis journal).\n\nJOURNAL DATA:\n" + ctx +
                "\n\nRespond (plain text):\nWHAT'S WORKING: patterns in the wins\nWHAT'S NOT: patterns in the misses\n"
                "3 CONCRETE HABITS TO IMPROVE. Be candid but constructive. Use only the data given; if it's thin, say what to log more of.")
    else:
        user = ("Act as my PM and make a call on this stock from the analysis below.\n\nANALYSIS DATA:\n" + ctx +
                "\n\nRespond (plain text, concise):\nDECISION: Buy / Accumulate / Hold / Reduce / Avoid\nTHESIS: 2–3 sentences\n"
                "KEY RISKS: 3 bullets\nWHAT WOULD CHANGE MY MIND: 1–2 bullets\nCONFIDENCE: low/medium/high + one line. "
                "Base only on the data above + general knowledge; don't invent numbers.")
    if mode in ("stock", "bear", "portfolio", "scan") and mem_txt:
        user = mem_txt + user   # feed the AI its own lessons so it learns from past mistakes
    try:
        return jresp({"enabled": True, "text": ai_chat(AI_SYSTEM, user)})
    except Exception as e:
        return jresp({"enabled": False, "note": "AI request failed — " + str(e)[:300]}, 502)


@app.route("/ai_chat", methods=["POST"])
def ai_chat_route():
    if not ai_available():
        return jresp({"error": "AI not configured."}, 400)
    ip = (request.headers.get("X-Forwarded-For", request.remote_addr or "?")).split(",")[0].strip()
    ok, why = ai_rate_ok(ip)
    if not ok:
        return jresp({"error": why}, 429)
    d = request.get_json(force=True) or {}
    q = (d.get("q") or "")[:1000]
    ctx = json.dumps(d.get("context") or {})[:6000]
    user = f"Here is the analysis context:\n{ctx}\n\nUser question: {q}\n\nAnswer as the analyst, grounded in this context; if it's outside the data, say what you'd need."
    try:
        return jresp({"text": ai_chat(AI_SYSTEM, user, 700)})
    except Exception as e:
        return jresp({"error": "AI request failed — " + str(e)[:300]}, 502)


@app.route("/ai_compare")
def ai_compare():
    if not ai_available():
        return jresp({"error": "AI not configured."}, 400)
    ip = (request.headers.get("X-Forwarded-For", request.remote_addr or "?")).split(",")[0].strip()
    ok, why = ai_rate_ok(ip)
    if not ok:
        return jresp({"error": why}, 429)
    a, b = clean_ticker(request.args.get("a")), clean_ticker(request.args.get("b"))
    if not a or not b:
        return jresp({"error": "Need two tickers."}, 400)

    def compact(e):
        keep = ["sym", "name", "sector", "price", "health", "verdict", "fund_score", "tech_score", "pe", "pb",
                "roe_pct", "roce_pct", "ev_ebitda", "net_margin_pct", "earnings_growth_pct", "earnings_cagr_3y",
                "de", "rsi", "above_200dma", "rel_strength_6m", "fund_note"]
        return {k: e.get(k) for k in keep if k in e}
    try:
        ea, eb = evaluate_holding(a, 0, "medium", None), evaluate_holding(b, 0, "medium", None)
        ctx = json.dumps({"A": compact(ea), "B": compact(eb)})[:6500]
        user = ("Compare these two stocks as a PM and pick the better BUY right now.\n\nDATA:\n" + ctx +
                "\n\nRespond (plain text):\nWINNER: A or B (ticker) — one line why\nVALUATION EDGE: which is cheaper for its quality/growth\n"
                "QUALITY & RISK EDGE: which is the better business / lower risk\nTECHNICAL EDGE: which has the better trend now\n"
                "VERDICT: a 2-line call, noting for whom (value vs growth vs safety). Use only the data; don't invent numbers.")
        return jresp({"text": ai_chat(AI_SYSTEM, user)})
    except Exception as e:
        return jresp({"error": "AI compare failed — " + str(e)[:300]}, 502)


@app.route("/ai_sector_pick")
def ai_sector_pick():
    if not ai_available():
        return jresp({"error": "AI not configured."}, 400)
    ip = (request.headers.get("X-Forwarded-For", request.remote_addr or "?")).split(",")[0].strip()
    ok, why = ai_rate_ok(ip)
    if not ok:
        return jresp({"error": why}, 429)
    ticker = clean_ticker(request.args.get("ticker"))
    peers, sec = find_peers(ticker, request.args.get("sector"))
    syms = [ticker] + [p for p in peers if p != ticker][:5]
    bench = "^NSEI" if any(s.endswith((".NS", ".BO")) for s in syms) else "^GSPC"
    bret = None
    try:
        bc = yf.Ticker(bench).history(period="1y")["Close"].dropna()
        if len(bc) > 126:
            bret = float(bc.iloc[-1] / bc.iloc[-126] - 1) * 100
    except Exception:
        pass

    def compact(e):
        keep = ["sym", "name", "health", "verdict", "fund_score", "tech_score", "pe", "pb", "roe_pct", "roce_pct",
                "ev_ebitda", "net_margin_pct", "earnings_cagr_3y", "de", "rsi", "above_200dma", "rel_strength_6m"]
        return {k: e.get(k) for k in keep if k in e}
    try:
        cands = [compact(e) for e in (evaluate_holding(s, 0, "medium", bret) for s in syms) if "error" not in e]
        ctx = json.dumps({"sector": sec, "your_stock": ticker.replace(".NS", "").replace(".BO", ""), "candidates": cands})[:6800]
        user = ("Across these SAME-SECTOR names, pick the best on FUNDAMENTALS and TECHNICALS combined.\n\nDATA:\n" + ctx +
                "\n\nRespond (plain text):\nBEST OVERALL: ticker — why (quality + valuation + trend)\nBEST VALUE: ticker — cheapest for its quality\n"
                "BEST MOMENTUM/TECHNICAL: ticker — strongest trend\nHOW YOUR STOCK RANKS: where the user's stock sits vs peers\n"
                "ONE-LINE TAKEAWAY. Use only the data; don't invent numbers.")
        return jresp({"text": ai_chat(AI_SYSTEM, user), "sector": sec})
    except Exception as e:
        return jresp({"error": "AI sector pick failed — " + str(e)[:300]}, 502)


@app.route("/universe")
def universe():
    return jresp({"universe": UNIVERSE, "models": MODELS})


@app.route("/search")
def search():
    q = (request.args.get("q") or "").strip()[:48]
    if len(q) < 2:
        return jresp([])
    try:
        url = f"https://query2.finance.yahoo.com/v1/finance/search?q={urllib.parse.quote(q)}&quotesCount=12&newsCount=0"
        raw = requests.get(url, headers=UA, timeout=8).json()
        quotes = [x for x in raw.get("quotes", []) if x.get("symbol")]
        quotes.sort(key=lambda x: 0 if str(x.get("symbol", "")).endswith((".NS", ".BO")) else 1)  # India first
        return jresp([{"symbol": x.get("symbol"), "name": x.get("shortname") or x.get("longname") or "",
                       "exch": x.get("exchDisp") or x.get("exchange") or "",
                       "india": str(x.get("symbol", "")).endswith((".NS", ".BO"))} for x in quotes[:8]])
    except Exception as e:
        return jresp({"error": str(e)}, 500)


@app.route("/peers")
def peers_compare():
    syms = [clean_ticker(s) for s in (request.args.get("tickers") or "").split(",") if s][:7]
    rows = []
    for s in syms:
        i = enriched_info(s)
        rows.append({"ticker": s, "name": _g(i, "shortName", "longName") or s, "pe": _g(i, "trailingPE"),
                     "pb": _g(i, "priceToBook"), "roe": _pct(_g(i, "returnOnEquity")), "margin": _pct(_g(i, "profitMargins")),
                     "rev_growth": _pct(_g(i, "revenueGrowth")), "marketCap": _g(i, "marketCap")})
    return jresp(rows)


@app.route("/quote")
def quote():
    syms = [clean_ticker(s) for s in (request.args.get("tickers") or "").split(",") if s][:30]
    out = {}
    for s in syms:
        try:
            fi = yf.Ticker(s).fast_info
            out[s] = {"price": float(fi["last_price"]), "prev": float(fi["previous_close"])}
        except Exception:
            out[s] = None
    return jresp(out)


@app.route("/optimize", methods=["POST"])
def optimize():
    d = request.get_json(force=True)
    holdings = [{"sym": clean_ticker(h.get("sym")), "qty": float(h.get("qty") or 0), "buy": float(h.get("buy") or 0)}
                for h in d.get("holdings", []) if h.get("sym")]
    if not holdings:
        return jresp({"error": "Add at least one holding."}, 400)
    try:
        return jresp(portfolio_analytics(holdings, float(d.get("extra_capital") or 0)))
    except Exception as e:
        return jresp({"error": f"Optimization failed: {e}"}, 500)


@app.route("/peer_eval")
def peer_eval_route():
    country = request.args.get("country", "India")
    sector = request.args.get("sector")
    exclude = clean_ticker(request.args.get("exclude"))
    peers, sec = find_peers(exclude, sector)
    syms = [p for p in peers if p != exclude][:5]
    rows = [peer_metrics(s) for s in syms]
    rows.sort(key=lambda r: -(r["quality"] if r["quality"] is not None else -1))
    return jresp({"sector": sec, "exclude": exclude, "rows": rows})


@app.route("/portfolio_eval", methods=["POST"])
def portfolio_eval_route():
    d = request.get_json(force=True)
    holdings = [{"sym": clean_ticker(h.get("sym")), "qty": float(h.get("qty") or 0), "buy": float(h.get("buy") or 0)}
                for h in d.get("holdings", []) if h.get("sym")]
    if not holdings:
        return jresp({"error": "Add at least one holding."}, 400)
    try:
        return jresp(portfolio_eval(holdings, d.get("horizon", "medium"), float(d.get("extra_capital") or 0)))
    except Exception as e:
        return jresp({"error": f"Evaluation failed: {e}"}, 500)


@app.route("/portfolio_news")
def portfolio_news():
    syms = [clean_ticker(s) for s in (request.args.get("tickers") or "").split(",") if s][:8]
    out = []
    for s in syms:
        base = s.replace(".NS", "").replace(".BO", "")
        for n in google_news(base + " stock", n=3):
            n["ticker"] = base
            out.append(n)
    return jresp(out)


@app.route("/sector_top")
def sector_top():
    country, sector = request.args.get("country", "India"), request.args.get("sector")
    tilt = request.args.get("tilt", "fundamental")
    pool = UNIVERSE.get(country, {}).get(sector, [])
    if not pool:
        return jresp({"error": "Unknown sector."}, 400)
    return jresp({"sector": sector, "tilt": tilt, "rows": screen_pool(pool, tilt)})


@app.route("/screen")
def screen():
    name = request.args.get("type", "Buffett (quality)")
    wl = WATCHLISTS.get(name)
    if not wl:
        return jresp({"error": "Unknown watchlist."}, 400)
    return jresp({"name": name, "tilt": wl["tilt"], "rows": screen_pool(wl["pool"], wl["tilt"])})


@app.route("/dashboard")
def dashboard():
    def block(d):
        out = {}
        for label, sym in d.items():
            try:
                fi = yf.Ticker(sym).fast_info
                p, pv = float(fi["last_price"]), float(fi["previous_close"])
                out[label] = {"price": round(p, 2), "change_pct": round((p - pv) / pv * 100, 2)}
            except Exception:
                out[label] = None
        return out
    # sector rotation: 1-month return, ranked
    rot = []
    for label, sym in IDX_SECTOR.items():
        try:
            h = yf.Ticker(sym).history(period="1mo")["Close"].dropna()
            if len(h) > 2:
                rot.append({"sector": label, "ret_1m_pct": round(float(h.iloc[-1] / h.iloc[0] - 1) * 100, 1)})
        except Exception:
            pass
    rot.sort(key=lambda x: -x["ret_1m_pct"])
    return jresp({"market": block(IDX_MARKET), "macro": block(IDX_MACRO),
                  "sector_rotation": rot, "fred": fred_latest(), "watchlists": list(WATCHLISTS.keys())})


@app.route("/technicals")
def technicals():
    sym = clean_ticker(request.args.get("ticker"))
    market = request.args.get("market", "NSE")
    tf = request.args.get("tf", "daily")
    if market in ("NSE", "BSE") and not sym.endswith((".NS", ".BO")):
        sym += ".NS" if market == "NSE" else ".BO"
    if not sym:
        return jresp({"error": "No ticker."}, 400)
    try:
        return jresp(technical_analysis(sym, tf if tf in ("daily", "weekly") else "daily"))
    except Exception as e:
        return jresp({"error": f"Technicals failed: {e}"}, 500)


@app.route("/price_stats")
def price_stats():
    """Period high / low / average / change over a chosen range (1M…Max/ATH) + a price series."""
    sym = clean_ticker(request.args.get("ticker"))
    market = request.args.get("market", "NSE")
    if market in ("NSE", "BSE") and not sym.endswith((".NS", ".BO")):
        sym += ".NS" if market == "NSE" else ".BO"
    rng = request.args.get("rng", "1y")
    period, interval = {"1mo": ("1mo", "1d"), "3mo": ("3mo", "1d"), "6mo": ("6mo", "1d"), "1y": ("1y", "1d"),
                        "5y": ("5y", "1wk"), "10y": ("10y", "1wk"), "max": ("max", "1mo")}.get(rng, ("1y", "1d"))
    try:
        df = yf.Ticker(sym).history(period=period, interval=interval).dropna()
        if df.empty:
            return jresp({"error": "No data for this range."}, 404)
        c, hi, lo = df["Close"], df["High"], df["Low"]
        n = min(len(c), 220)
        return jresp({"rng": rng, "high": round(float(hi.max()), 2), "low": round(float(lo.min()), 2),
                      "avg": round(float(c.mean()), 2), "last": round(float(c.iloc[-1]), 2),
                      "change_pct": round(float(c.iloc[-1] / c.iloc[0] - 1) * 100, 1),
                      "high_date": str(hi.idxmax().date()), "low_date": str(lo.idxmin().date()),
                      "series": [round(float(x), 2) for x in c.tail(n)],
                      "dates": [str(d.date()) for d in c.tail(n).index]})
    except Exception as e:
        return jresp({"error": str(e)}, 500)


@app.route("/industry_pe")
def industry_pe_route():
    sym = clean_ticker(request.args.get("ticker"))
    market = request.args.get("market", "NSE")
    if market in ("NSE", "BSE") and not sym.endswith((".NS", ".BO")):
        sym += ".NS" if market == "NSE" else ".BO"
    try:
        return jresp(industry_pe(sym, request.args.get("sector")))
    except Exception as e:
        return jresp({"error": str(e)}, 500)


@app.route("/analyze", methods=["POST"])
def do_analyze():
    d = request.get_json(force=True)
    market = d.get("market", "NSE")
    sym = clean_ticker(d.get("ticker"))
    if not sym:
        return jresp({"error": "Enter a ticker."}, 400)
    if market in ("NSE", "BSE") and not sym.endswith((".NS", ".BO")):
        sym += ".NS" if market == "NSE" else ".BO"
    try:
        return jresp(analyze(sym, d.get("horizon", "medium"), d.get("risk", "medium"),
                             float(d.get("capital") or 0), int(d.get("years") or 5), d.get("style", "balanced")))
    except Exception as e:
        return jresp({"error": f"Analysis failed for {sym}: {e}"}, 500)


def _selftest():
    s = pd.Series(range(1, 17), dtype=float)
    assert abs(rsi(s).iloc[-1] - 100) < 1e-6
    v = dcf(100, 0.10, 0.12, 10)
    assert v and v > 100 and dcf(-5, 0.1, 0.12, 10) is None
    assert abs(reverse_dcf(100, v, 0.12, 10) - 10) < 1.0
    assert score(20, 12, 45) > 5
    assert rate_metric("ROE %", 20)["rating"] == "good" and rate_metric("P/E (trailing)", 60)["rating"] == "poor"
    assert cap_category(30000 * 1e7) == "Large cap" and cap_category(100 * 1e7) == "Small cap"
    f, src, det = derive_fcf({"freeCashflow": 500}, None)
    assert f == 500
    p, sec = find_peers("HAL.NS", "Aerospace & Defense")
    assert "BEL.NS" in p and sec == "Defense"
    pl = portfolio_plan(100000, "aggressive", "short", "growth", "Overvalued", {"suggested_weight_pct": 10})
    assert pl["tranches"] == 6 and "DCA" in pl["method"]
    assert research_verdict(8, "Undervalued") == "Buy" and research_verdict(3, "Overvalued") == "Avoid"
    assert quality_score(20, 30, 18, 15, 100) > 6  # strong company scores high
    # analytics math on synthetic correlated returns (no network)
    rng = np.random.default_rng(0)
    idx = rng.normal(0.0004, 0.01, 400)
    df = pd.DataFrame({"A.NS": 1.2 * idx + rng.normal(0, 0.005, 400),
                       "B.NS": 0.8 * idx + rng.normal(0, 0.004, 400), "^NSEI": idx})
    closes = (1 + df).cumprod() * 100
    glob = globals()
    orig = glob["_closes"]
    glob["_closes"] = lambda syms, period="2y": closes[[c for c in syms if c in closes.columns]]
    glob["stock_sector"] = lambda s: "Test"
    try:
        r = portfolio_analytics([{"sym": "A.NS", "qty": 10, "buy": 100}, {"sym": "B.NS", "qty": 5, "buy": 100}])
        assert r["mpt"]["volatility_pct"] > 0 and r["risk"]["portfolio_beta"] > 0
        assert r["monte_carlo"]["p5"] < r["monte_carlo"]["p95"]
        assert abs(sum(x["target_pct"] for x in r["rebalance"]) - 100) < 2
        assert r["stress_test"]["NIFTY -15%"]["expected_loss"] < 0  # a fall produces a loss
        assert "annual_dividend" in r["income"] and r["risk"]["cvar_1d_95_pct"] >= r["risk"]["var_1d_95_pct"]
        assert r["mpt"]["cagr_pct"] is not None and "sortino" in r["mpt"]
        assert len(r["backtest"]["portfolio"]) > 0 and r["correlation"]["matrix"]["A.NS"]["A.NS"] == 1.0
    finally:
        glob["_closes"] = orig
    # technicals math + pattern detection (synthetic, no network)
    base = pd.Series(np.linspace(100, 110, 60))
    o, c = base.copy(), base + 0.5
    o.iloc[-2], c.iloc[-2] = 110.0, 108.0      # red bar
    o.iloc[-1], c.iloc[-1] = 107.5, 111.0      # green bar engulfing the red
    h = pd.concat([o, c], axis=1).max(axis=1) + 0.5
    lo2 = pd.concat([o, c], axis=1).min(axis=1) - 0.5
    pats = detect_patterns(o, h, lo2, c)
    assert any("Bullish Engulfing" == p["name"] for p in pats), "engulfing not detected"
    adxv, pdi, mdi, atrv = _adx(h, lo2, c)
    assert adxv.iloc[-1] >= 0 and atrv.iloc[-1] > 0
    hi, lows = _swings(np.array([1, 3, 2, 5, 1, 4, 0]), order=1)
    assert 3 in hi and 4 in lows
    assert _style_cat("Earnings growth") == "Growth" and _style_cat("Valuation (P/E)") == "Valuation (P/E)"
    # institutional metrics on synthetic statements (2 years)
    cols = pd.to_datetime(["2024-03-31", "2023-03-31", "2022-03-31", "2021-03-31"])  # most-recent first
    fin = pd.DataFrame([[1000, 900, 820, 750], [250, 220, 200, 180], [150, 130, 115, 100],
                        [400, 360, 330, 300], [20, 22, 24, 26]], columns=cols,
                       index=["Total Revenue", "Operating Income", "Net Income", "Gross Profit", "Interest Expense"])
    bs = pd.DataFrame([[2000, 1900, 1800, 1700], [500, 480, 470, 460], [700, 680, 650, 600],
                       [900, 880, 860, 840], [300, 320, 340, 360], [400, 380, 360, 340],
                       [600, 600, 600, 600], [1100, 1080, 1060, 1020], [100, 100, 100, 100]], columns=cols,
                      index=["Total Assets", "Current Liabilities", "Current Assets", "Stockholders Equity",
                             "Total Debt", "Retained Earnings", "Long Term Debt", "Total Liabilities Net Minority Interest", "Cash And Cash Equivalents"])
    cfd = pd.DataFrame([[180, 160, 150, 140]], columns=cols, index=["Operating Cash Flow"])
    im = institutional_metrics(fin, bs, cfd, mktcap=3000, ebitda=300, fcf=160)
    assert im["EV/EBITDA"] > 0 and im["ROCE %"] > 0 and im["FCF yield %"] > 0
    assert im["Altman Z"] > 0 and 0 <= im["Piotroski F"] <= im["_piotroski_max"] <= 9
    assert im["Revenue CAGR 3y %"] > 0
    print("selftest OK")


HTML = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>FundaPilot — institutional-grade equity research & portfolio optimization</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<script src="https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2"></script>
<!--SBCFG-->
<style>
:root{--bg:#0b0f1a;--card:rgba(255,255,255,.05);--line:rgba(255,255,255,.10);--txt:#e7ecf3;--mut:#8b97ad;--acc:#6ea8fe;--good:#39d98a;--bad:#ff6b6b;--warn:#ffd166}
*{box-sizing:border-box}body{margin:0;font:15px/1.5 system-ui,Segoe UI,Roboto,sans-serif;color:var(--txt);background:radial-gradient(1200px 600px at 20% -10%,#16243f,transparent),radial-gradient(900px 500px at 90% 0%,#1a1330,transparent),var(--bg);min-height:100vh}
header{padding:26px 22px 6px;text-align:center}h1{margin:0;font-size:30px;letter-spacing:-.5px;background:linear-gradient(90deg,#9ec5ff,#c8a9ff);-webkit-background-clip:text;background-clip:text;color:transparent}
.sub{color:var(--mut);font-size:13px}.wrap{max-width:1080px;margin:0 auto;padding:18px}
.glass{background:var(--card);border:1px solid var(--line);border-radius:16px;backdrop-filter:blur(8px);box-shadow:0 8px 30px rgba(0,0,0,.25)}
.tabs{display:flex;gap:8px;margin-bottom:12px;flex-wrap:wrap}.tab{flex:1;min-width:120px;padding:10px;text-align:center;border-radius:10px;cursor:pointer;border:1px solid var(--line);background:#0e1422;color:var(--mut)}
.tab.on{color:#06122a;background:linear-gradient(90deg,#7db4ff,#b39bff);font-weight:600;border:0}
.panel{padding:0;display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:14px;align-items:end}
label{display:block;font-size:12px;color:var(--mut);margin-bottom:5px}
input,select{width:100%;padding:10px;border-radius:10px;border:1px solid var(--line);background:#0e1422;color:var(--txt);font-size:14px}
button{padding:13px;border:0;border-radius:12px;cursor:pointer;font-size:15px;font-weight:600;color:#06122a;background:linear-gradient(90deg,#7db4ff,#b39bff)}
button:disabled{opacity:.6;cursor:wait}.full{grid-column:1/-1}.hint{font-size:11px;color:var(--mut);margin-top:4px}
.ac{position:relative}.acbox{position:absolute;z-index:9;left:0;right:0;top:100%;margin-top:4px;background:#0e1422;border:1px solid var(--line);border-radius:10px;overflow:hidden;display:none}
.acbox div{padding:9px 12px;cursor:pointer;font-size:13px;border-bottom:1px solid var(--line)}.acbox div:hover{background:#16243f}.acbox small{color:var(--mut)}
section{margin-top:18px;padding:18px}h2{margin:0 0 12px;font-size:18px}h3{margin:14px 0 6px;font-size:14px;color:var(--mut)}
.grid{display:grid;gap:12px}.cards{grid-template-columns:repeat(auto-fit,minmax(150px,1fr))}.two{grid-template-columns:1fr 1fr}@media(max-width:680px){.two{grid-template-columns:1fr}}
.chip{padding:12px;border-radius:12px;border:1px solid var(--line);background:#0e1422}.chip b{display:block;font-size:20px;margin-top:2px}
.muted{color:var(--mut);font-size:12px}.tag{display:inline-block;font-size:11px;padding:3px 9px;border-radius:20px;margin:2px;background:rgba(110,168,254,.15);color:var(--acc)}
table{width:100%;border-collapse:collapse;font-size:14px}td,th{padding:8px 6px;border-bottom:1px solid var(--line);text-align:left}td:not(:first-child),th:not(:first-child){text-align:right}
.verdict{display:inline-block;padding:6px 12px;border-radius:20px;font-weight:600}.v-under{background:rgba(57,217,138,.15);color:var(--good)}.v-over{background:rgba(255,107,107,.15);color:var(--bad)}.v-fair{background:rgba(255,209,102,.15);color:var(--warn)}
.bar{height:8px;border-radius:6px;background:#0e1422;overflow:hidden}.bar>i{display:block;height:100%;border-radius:6px;background:linear-gradient(90deg,#ff6b6b,#ffd166,#39d98a)}
.flag{padding:8px 10px;border-radius:10px;margin:6px 0;font-size:13px}.f-good{background:rgba(57,217,138,.10);border-left:3px solid var(--good)}.f-bad{background:rgba(255,107,107,.10);border-left:3px solid var(--bad)}
.dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:6px}.d-good{background:var(--good)}.d-ok{background:var(--warn)}.d-poor{background:var(--bad)}.d-info{background:var(--acc)}
.pill{display:inline-block;font-size:11px;padding:2px 8px;border-radius:20px;margin-left:6px}.yes{background:rgba(57,217,138,.18);color:var(--good)}.no{background:rgba(255,107,107,.15);color:var(--bad)}
.tone-neg{color:var(--bad)}.tone-pos{color:var(--good)}a{color:var(--acc)}
.spin{width:34px;height:34px;border:3px solid var(--line);border-top-color:var(--acc);border-radius:50%;margin:30px auto;animation:r 1s linear infinite}@keyframes r{to{transform:rotate(360deg)}}
details summary{cursor:pointer;color:var(--mut);font-size:13px}.disc{font-size:11px;color:var(--mut);text-align:center;padding:20px}
.kv{margin:6px 0}.kv b{color:var(--txt)}.rec{font-size:16px;line-height:1.6;padding:14px;border-radius:12px;background:linear-gradient(90deg,rgba(110,168,254,.12),rgba(179,155,255,.12));border:1px solid var(--line)}
.news{max-height:320px;overflow:auto}.pos{color:var(--good)}.neg{color:var(--bad)}
.tip{border-bottom:1px dotted var(--mut);cursor:help}
.tip:hover::after{content:attr(data-tip);position:absolute;left:auto;margin-top:18px;margin-left:-8px;z-index:30;max-width:260px;background:#0e1422;border:1px solid var(--acc);border-radius:8px;padding:9px 11px;font-size:12px;font-weight:400;line-height:1.45;color:var(--txt);white-space:normal;box-shadow:0 8px 24px rgba(0,0,0,.5)}
.seg{display:inline-flex;border:1px solid var(--line);border-radius:10px;overflow:hidden}.seg button{background:#0e1422;color:var(--mut);border:0;padding:8px 16px;font-weight:600;border-radius:0}.seg button.on{background:linear-gradient(90deg,#7db4ff,#b39bff);color:#06122a}
.dl{background:#0e1422;color:var(--acc);border:1px solid var(--line);padding:9px 13px;border-radius:10px;font-size:13px;margin:3px}
/* mobile: let wide tables scroll inside their card instead of being clipped (desktop unchanged) */
@media(max-width:680px){
  .wrap{padding:10px}section{padding:14px}h1{font-size:24px}
  .cards{grid-template-columns:repeat(auto-fit,minmax(130px,1fr))}
  .panel{grid-template-columns:1fr 1fr}
  .glass{overflow-x:auto}
  table{display:block;width:auto;min-width:100%;overflow-x:auto;-webkit-overflow-scrolling:touch;white-space:nowrap;font-size:13px}
  td,th{padding:7px 5px}
  .rec{font-size:14px}
}
@media(max-width:420px){.cards,.panel{grid-template-columns:1fr}}
.creator{text-align:center;font-size:13px;letter-spacing:1px;opacity:.32;padding:6px 0 26px}
#authbar{position:absolute;top:14px;right:16px;font-size:13px;display:flex;gap:8px;align-items:center}
#authbar button{background:#0e1422;color:var(--acc);border:1px solid var(--line);padding:7px 12px;border-radius:10px;font-size:13px}
@media(max-width:680px){#authbar{position:static;justify-content:center;margin-top:8px}}
</style></head><body>
<div id="authbar"></div>
<header>
  <div style="display:flex;align-items:center;justify-content:center;gap:14px;flex-wrap:wrap">
    <svg width="64" height="64" viewBox="0 0 100 100" aria-label="FundaPilot logo">
      <circle cx="52" cy="44" r="33" fill="none" stroke="#9ec5ff" stroke-width="4"/>
      <rect x="34" y="48" width="8" height="16" rx="1.5" fill="#39d98a"/>
      <rect x="46" y="40" width="8" height="24" rx="1.5" fill="#39d98a"/>
      <rect x="58" y="30" width="8" height="34" rx="1.5" fill="#39d98a"/>
      <path d="M32 56 L48 44 L60 50 L78 26" fill="none" stroke="#39d98a" stroke-width="4.5" stroke-linecap="round" stroke-linejoin="round"/>
      <path d="M72 24 L80 24 L80 32" fill="none" stroke="#39d98a" stroke-width="4.5" stroke-linecap="round" stroke-linejoin="round"/>
      <path d="M20 70 h34 M24 78 h26" stroke="#9ec5ff" stroke-width="4" stroke-linecap="round"/>
      <text x="48" y="92" font-family="system-ui,Segoe UI" font-size="34" font-weight="800" fill="#9ec5ff">P</text>
    </svg>
    <div style="text-align:left">
      <h1 style="margin:0">Funda<span style="background:none;-webkit-text-fill-color:#39d98a;color:#39d98a">Pilot</span></h1>
      <div class="sub" style="letter-spacing:2px;font-weight:600">ANALYZE · VALUE · OPTIMIZE</div>
    </div>
  </div>
  <div class="sub" style="margin-top:6px">Institutional-grade equity research &amp; portfolio optimization · AI-powered valuation &amp; portfolio analytics · educational only · Indian &amp; global</div>
</header>
<div class="wrap">
<div class="glass" style="padding:18px">
  <div class="tabs">
    <div class="tab on" data-tab="search">🔍 Company</div>
    <div class="tab" data-tab="explore">🧭 Explore by sector</div>
    <div class="tab" data-tab="models">📊 Model portfolios</div>
    <div class="tab" data-tab="track">📈 My portfolio (live)</div>
    <div class="tab" data-tab="markets">🌐 Markets</div>
    <div class="tab" data-tab="watch">⭐ Watchlists</div>
    <div class="tab" data-tab="me" id="metab" style="display:none">👤 My space</div>
  </div>
  <div id="m-search" class="panel">
    <div class="full ac"><label>Company / ticker</label><input id="ticker" placeholder="Type e.g. Reliance, HAL, Apple…" autocomplete="off">
      <div class="acbox" id="acbox"></div><div class="hint">Pick a suggestion, or type a symbol. NSE adds .NS automatically.</div>
      <div id="quoteprev" style="margin-top:6px;font-size:14px"></div></div>
    <div><label>Market</label><select id="market"><option>NSE</option><option>BSE</option><option>Global</option></select></div>
  </div>
  <div id="m-explore" class="panel" style="display:none">
    <div><label>Country / group</label><select id="ex-country"></select></div>
    <div><label>Sector / category</label><select id="ex-sector"></select></div>
    <div><label>Company</label><select id="ex-company"></select></div>
  </div>
  <div id="m-models" style="display:none"></div>
  <div id="m-track" style="display:none"></div>
  <div id="m-markets" style="display:none"></div>
  <div id="m-watch" style="display:none"></div>
  <div id="m-me" style="display:none"></div>
  <div id="filters" class="panel" style="margin-top:14px">
    <div><label>Time horizon</label><select id="horizon"><option value="short">Short (≤3y)</option><option value="medium" selected>Medium (3–7y)</option><option value="long">Long (7y+)</option></select></div>
    <div><label>Risk appetite</label><select id="risk"><option value="conservative">Conservative</option><option value="medium" selected>Medium</option><option value="aggressive">Aggressive</option></select></div>
    <div><label>Investing style</label><select id="style"><option value="balanced">Balanced</option><option value="buffett">Warren Buffett</option><option value="growth">Growth (Lynch)</option><option value="value">Deep Value</option><option value="dividend">Dividend/Defensive</option><option value="momentum">Momentum</option></select></div>
    <div><label>Capital (₹)</label><input id="capital" type="number" value="100000" min="0"></div>
    <div><label>Years of statements</label><input id="years" type="number" value="5" min="1" max="10"></div>
    <button id="go" class="full">Analyze</button>
  </div>
</div>
<div id="out"></div>
</div>
<div class="disc">⚠️ Educational use only. Not investment advice. Data: Yahoo Finance + Google News. Verify before any decision.</div>
<div class="creator">creator: mohith</div>
<script>
const $=h=>{const d=document.createElement('div');d.innerHTML=h;return d.firstElementChild};
const el=id=>document.getElementById(id), out=el('out');
const fmt=n=>n==null?'—':(Math.abs(n)>=1e7?(n/1e7).toFixed(2)+' Cr':Math.abs(n)>=1e5?(n/1e5).toFixed(2)+' L':Number(n).toLocaleString(undefined,{maximumFractionDigits:2}));
const esc=s=>String(s==null?'':s).replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));  // XSS-safe text for innerHTML
const safeUrl=u=>/^https?:\/\//i.test(String(u||''))?u:'#';  // block javascript:/data: links
const AI_DISCLAIMER='⚠️ AI can make mistakes and may misread the data. Educational only — not investment advice. Verify independently.';
async function aiPost(url,body,targetId,loading){const b=el(targetId);if(!b)return;
  b.innerHTML=`<div class="spin"></div><p class="muted" style="text-align:center">${loading||'Thinking…'}</p>`;
  try{const r=await(await fetch(url,body?{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body)}:{})).json();
    b.innerHTML=r.text?`<div class="rec" style="white-space:pre-wrap;line-height:1.55">${esc(r.text)}</div><p class="muted">${AI_DISCLAIMER}</p>`:`<p class="muted">${esc(r.note||r.error||'No response.')}</p>`;
  }catch(e){b.innerHTML='<p class="muted">AI call failed.</p>';}}
function aiContext(d){return {name:d.name,ticker:d.ticker,sector:d.sector,industry:d.industry,price:d.price,currency:d.currency,
  cap_category:d.cap_category,tags:d.tags,overall_score:d.overall,style:d.style,
  valuation:{verdict:d.valuation.verdict,dcf_verdict:d.valuation.dcf_verdict,margin_of_safety_pct:d.valuation.margin_of_safety_pct,
    fair_pe:d.valuation.fair_pe,current_pe:d.valuation.current_pe,peg:d.valuation.peg,growth_pct:d.valuation.growth_pct,reverse_dcf_implied_growth:d.valuation.implied_growth_pct},
  scores:d.scores,
  ratios:Object.fromEntries(Object.entries(d.ratios||{}).map(([k,v])=>[k,v.value])),
  quality:d.quality,
  technical:{daily:d.technical&&d.technical.daily?{rsi:d.technical.daily.rsi,above_ema200:d.technical.daily.above_ema}:null,
             weekly:d.technical&&d.technical.weekly?{rsi:d.technical.weekly.rsi,above_ema200:d.technical.weekly.above_ema}:null},
  green_flags:d.green_flags,red_flags:d.red_flags,research:d.research,
  recent_news:(d.news||[]).slice(0,6).map(n=>n.title)};}
let charts=[],mode='search',UNI={},MOD={};
function setMode(m){mode=m;document.querySelectorAll('.tab').forEach(x=>x.classList.toggle('on',x.dataset.tab===m));
  ['search','explore','models','track','markets','watch','me'].forEach(k=>el('m-'+k).style.display=k===m?(k==='search'||k==='explore'?'grid':'block'):'none');
  el('filters').style.display=(m==='search'||m==='explore')?'grid':'none';
  el('out').style.display=(m==='search'||m==='explore')?'block':'none';  // keep single-stock analysis out of other tabs
  if(m==='models')renderModels();if(m==='track')renderTracker();if(m==='markets')renderMarkets();if(m==='watch')renderWatch();if(m==='me')renderMe();}
document.querySelectorAll('.tab').forEach(t=>t.onclick=()=>setMode(t.dataset.tab));

// ---------- Supabase auth + per-user data (graceful: app works fully without it) ----------
let SB=null, USER=null, AI_LESSONS=[];
if(window.SB_URL&&window.SB_KEY&&window.supabase){
  SB=window.supabase.createClient(window.SB_URL,window.SB_KEY);
  SB.auth.getSession().then(({data})=>{USER=data.session?data.session.user:null;paintAuth();loadLessons();});
  SB.auth.onAuthStateChange((_e,s)=>{USER=s?s.user:null;paintAuth();loadLessons();});
}
paintAuth();
async function loadLessons(){if(!SB||!USER){AI_LESSONS=[];return;}
  try{const r=await SB.from('ai_lessons').select('content').order('at',{ascending:false}).limit(20);AI_LESSONS=(r.data||[]).map(x=>x.content);}catch(e){AI_LESSONS=[];}}
function aiMemory(){return AI_LESSONS.length?{lessons:AI_LESSONS}:undefined;}  // feed past lessons into new AI calls
function paintAuth(){const bar=el('authbar');if(!bar)return;
  if(!SB){bar.innerHTML='';return;}
  el('metab').style.display='block';
  if(USER){bar.innerHTML=`<span class="muted">👤 ${USER.email||'signed in'}</span><button id="logout">Sign out</button>`;
    el('logout').onclick=()=>SB.auth.signOut();}
  else{bar.innerHTML=`<button id="login">🔐 Sign in with Google</button>`;
    el('login').onclick=()=>SB.auth.signInWithOAuth({provider:'google',options:{redirectTo:window.location.origin}});}}
function gotoTicker(t){el('ticker').value=t.replace('.NS','').replace('.BO','');el('market').value=t.endsWith('.BO')?'BSE':t.endsWith('.NS')?'NSE':'Global';setMode('search');el('go').click();}
let activeList=localStorage.getItem('fp_activelist')||'My Watchlist';
async function addWatch(ticker,name,fav,quiet){if(!SB){alert('Sign in (top-right) to save watchlists.');return false;}if(!USER){alert('Please sign in with Google first.');return false;}
  let row={user_id:USER.id,ticker,name:name||null,list_name:activeList};
  let {error}=await SB.from('watchlist').upsert(row,{onConflict:'user_id,ticker'});
  if(error&&/list_name|column/i.test(error.message)){delete row.list_name;({error}=await SB.from('watchlist').upsert(row,{onConflict:'user_id,ticker'}));}
  if(error){alert('Could not save: '+error.message);return false;}
  if(fav){await setFav(ticker,true,true);}
  if(!quiet)alert((fav?'Added to favorites: ':'Saved to “'+activeList+'”: ')+ticker);
  return true;}
async function setFav(ticker,on,quiet){if(!SB||!USER)return;
  const {error}=await SB.from('watchlist').update({fav:on}).eq('user_id',USER.id).eq('ticker',ticker);
  if(error&&!quiet)alert('To use favorites, run the one-line SQL in the README (add "fav" column). '+error.message);}
function saveWatch(t,n){return addWatch(t,n,false);}      // back-compat
function favWatch(t,n){return addWatch(t,n,true);}
// reusable personal-watchlist block: multiple named lists + live price/day% + favorites. pfx keeps ids unique.
function renderMyWatch(box,pfx){
  if(!SB){box.innerHTML='<section class="glass"><h2>⭐ My watchlists</h2><p class="muted">Sign in (top-right) to build personal watchlists & favorites that sync across your devices.</p></section>';return;}
  if(!USER){box.innerHTML='<section class="glass"><h2>⭐ My watchlists</h2><p class="muted">Sign in with Google (top-right) to add companies.</p></section>';return;}
  box.innerHTML=`<section class="glass"><h2>⭐ My watchlists &amp; favorites</h2>
    <div class="panel" style="grid-template-columns:1fr auto;align-items:end;max-width:560px">
      <div><label>List</label><select id="${pfx}lst"></select></div>
      <button id="${pfx}new" class="dl" style="margin:0">＋ New list</button></div>
    <div class="ac" style="max-width:460px;margin-top:10px"><label>Search a company to add to this list</label>
      <input id="${pfx}sym" placeholder="Type e.g. Reliance, HAL, Apple…" autocomplete="off"><div class="acbox" id="${pfx}box"></div></div>
    <div id="${pfx}list" style="margin-top:12px"><div class="spin"></div></div></section>`;
  acWire(pfx+'sym',pfx+'box',async sym=>{el(pfx+'sym').value='';const ok=await addWatch(sym,null,false,true);if(ok)loadMyWatch(pfx);});
  el(pfx+'new').onclick=()=>{const n=(prompt('Name your new watchlist:')||'').trim();if(n){activeList=n;localStorage.setItem('fp_activelist',n);loadMyWatch(pfx);}};
  loadMyWatch(pfx);}
async function loadMyWatch(pfx){const box=el(pfx+'list');if(!box)return;
  const wl=await SB.from('watchlist').select('*').order('added_at',{ascending:false});const rows=wl.data||[];
  const lists=[...new Set(rows.map(r=>r.list_name||'My Watchlist'))];if(!lists.includes(activeList))lists.unshift(activeList);
  const sel=el(pfx+'lst');if(sel){sel.innerHTML=lists.map(l=>`<option ${l===activeList?'selected':''}>${esc(l)}</option>`).join('');
    sel.onchange=()=>{activeList=sel.value;localStorage.setItem('fp_activelist',activeList);loadMyWatch(pfx);};}
  const mine=rows.filter(r=>(r.list_name||'My Watchlist')===activeList);
  if(!mine.length){box.innerHTML='<p class="muted">“'+esc(activeList)+'” is empty — search above to add, or hit ★/❤️ on a stock you analyze.</p>';return;}
  let q={};try{q=await(await fetch('/quote?tickers='+encodeURIComponent(mine.map(r=>r.ticker).join(',')))).json();}catch(e){}
  let h='<table><tr><th>Ticker</th><th>Name</th><th>Price</th><th>Day%</th><th>Fav</th><th></th><th></th></tr>';
  mine.forEach(r=>{const x=q[r.ticker];const cur=/\.(NS|BO)$/.test(r.ticker)?'₹':'';const day=(x&&x.prev)?((x.price-x.prev)/x.prev*100):null;
    h+=`<tr><td>${r.ticker.replace('.NS','').replace('.BO','')}</td><td>${esc(r.name||'')}</td><td>${x?cur+fmt(x.price):'—'}</td><td class="${(day||0)>=0?'pos':'neg'}">${day==null?'—':(day>=0?'+':'')+day.toFixed(2)+'%'}</td><td><a href="#" class="favt" data-t="${r.ticker}" data-f="${r.fav?1:0}" style="text-decoration:none">${r.fav?'❤️':'🤍'}</a></td><td><a href="#" class="wlgo" data-t="${r.ticker}">Analyze</a></td><td><a href="#" class="wlrm" data-t="${r.ticker}">✕</a></td></tr>`;});
  box.innerHTML=h+'</table><p class="muted">Live price &amp; day change shown without analyzing. 🤍→❤️ favorite · ✕ remove from this list. Use ＋ New list to create more.</p>';
  box.querySelectorAll('.wlgo').forEach(a=>a.onclick=e=>{e.preventDefault();gotoTicker(a.dataset.t);});
  box.querySelectorAll('.wlrm').forEach(a=>a.onclick=async e=>{e.preventDefault();await SB.from('watchlist').delete().eq('user_id',USER.id).eq('ticker',a.dataset.t);loadMyWatch(pfx);});
  box.querySelectorAll('.favt').forEach(a=>a.onclick=async e=>{e.preventDefault();await setFav(a.dataset.t,a.dataset.f!=='1');loadMyWatch(pfx);});}
async function logCall(d){if(!SB||!USER)return;try{await SB.from('search_history').insert(
  {user_id:USER.id,ticker:d.ticker,name:d.name,verdict:(d.research&&d.research.verdict)||null,score:d.overall,price:d.price});}catch(e){}}
async function renderMe(){const box=el('m-me');
  if(!SB){box.innerHTML='<section class="glass"><h2>👤 My space</h2><p class="muted">Accounts aren\'t enabled on this deployment. Set SUPABASE_URL & SUPABASE_ANON_KEY (see README) to turn on Google sign-in, saved watchlists and your analysis journal.</p></section>';return;}
  if(!USER){box.innerHTML='<section class="glass"><h2>👤 My space</h2><p class="muted">Sign in with Google (top-right) to see your saved watchlist and analysis journal.</p></section>';return;}
  box.innerHTML='<div id="me-watch"></div>'+
    (window.AI_ON?'<section class="glass"><h2>🧠 AI scan my watchlist</h2><button class="dl" id="ai-scan">🧠 Scan my current list for what to research now</button><div id="ai-scan-out" style="margin-top:10px"></div><p class="muted">Evaluates each name in the selected list (fundamentals + technicals), then the AI ranks what looks most actionable. Educational only.</p></section>':'')+
    '<section class="glass"><h2>📓 Analysis journal &amp; calibration</h2><p class="muted">Your past calls scored against live price — this is how you see which signals actually work for you (no black-box prediction).</p><div id="caloutput"><div class="spin"></div></div></section>';
  renderMyWatch(el('me-watch'),'mw_');
  if(window.AI_ON&&el('ai-scan'))el('ai-scan').onclick=aiScanWatchlist;
  const hist=await SB.from('search_history').select('*').order('at',{ascending:false}).limit(40);
  calibrate(hist.data||[]);}
async function aiScanWatchlist(){const out=el('ai-scan-out');
  const wl=await SB.from('watchlist').select('*');const rows=(wl.data||[]).filter(r=>(r.list_name||'My Watchlist')===activeList);
  if(!rows.length){out.innerHTML='<p class="muted">“'+esc(activeList)+'” is empty — add some names first.</p>';return;}
  out.innerHTML='<div class="spin"></div><p class="muted" style="text-align:center">Evaluating '+rows.length+' names then asking the AI… (~20–40s)</p>';
  try{
    const ev=await(await fetch('/portfolio_eval',{method:'POST',headers:{'content-type':'application/json'},
      body:JSON.stringify({holdings:rows.map(r=>({sym:r.ticker,qty:1,buy:0})),horizon:'medium',extra_capital:0})})).json();
    if(ev.error){out.innerHTML=`<p class="muted">${esc(ev.error)}</p>`;return;}
    const ctx={list:activeList,candidates:(ev.holdings||[]).map(e=>({sym:e.sym,health:e.health,verdict:e.verdict,fund:e.fund_score,tech:e.tech_score,pe:e.pe,roe:e.roe_pct,roce:e.roce_pct,cagr:e.earnings_cagr_3y,rsi:e.rsi,above_200dma:e.above_200dma,rel_strength_6m:e.rel_strength_6m}))};
    const r=await(await fetch('/ai_analyst',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({mode:'scan',context:ctx})})).json();
    out.innerHTML=r.text?`<div class="rec" style="white-space:pre-wrap;line-height:1.55">${esc(r.text)}</div><p class="muted">${AI_DISCLAIMER}</p>`:`<p class="muted">${esc(r.note||'No response.')}</p>`;
  }catch(e){out.innerHTML='<p class="muted">Scan failed.</p>';}}
async function calibrate(hr){const box=el('caloutput');if(!box)return;if(!hr.length){box.innerHTML='<p class="muted">No calls logged yet — analyze some stocks while signed in.</p>';return;}
  const syms=[...new Set(hr.map(r=>r.ticker))];const q=await(await fetch('/quote?tickers='+encodeURIComponent(syms.join(',')))).json();
  let wins=0,scored=0,t='<table><tr><th>Date</th><th>Ticker</th><th>Verdict</th><th>Score</th><th>Price then</th><th>Now</th><th>Return</th></tr>';
  const journal=[];
  hr.forEach(r=>{const now=q[r.ticker]?q[r.ticker].price:null;const ret=(now&&r.price)?(now/r.price-1)*100:null;
    const good=(ret!=null)&&((/Buy|Under/i.test(r.verdict||'')&&ret>0)||(/Avoid|Over/i.test(r.verdict||'')&&ret<0));
    if(ret!=null&&/Buy|Avoid|Under|Over/i.test(r.verdict||'')){scored++;if(good)wins++;}
    const rc=ret==null?'':ret>=0?'pos':'neg';
    journal.push({date:(r.at||'').slice(0,10),ticker:r.ticker.replace('.NS',''),verdict:r.verdict,score:r.score,return_since_pct:ret==null?null:Math.round(ret*10)/10});
    t+=`<tr><td>${(r.at||'').slice(0,10)}</td><td>${r.ticker.replace('.NS','')}</td><td>${r.verdict||'—'}</td><td>${r.score??'—'}</td><td>${fmt(r.price)}</td><td>${now==null?'—':fmt(now)}</td><td class="${rc}">${ret==null?'—':(ret>=0?'+':'')+ret.toFixed(1)+'%'}</td></tr>`;});
  const hit=scored?Math.round(wins/scored*100):null;
  const lessonsHtml=window.AI_ON?`<hr style="border:0;border-top:1px solid var(--line);margin:14px 0">
    <h3>🧠 AI memory — lessons it has learned <span class="muted">(fed into every future AI decision)</span></h3>
    <div id="ai-lessons">${AI_LESSONS.length?AI_LESSONS.map(l=>`<div class="flag f-good">• ${esc(l)}</div>`).join(''):'<p class="muted">No lessons yet — click below to have the AI review your track record and learn from it.</p>'}</div>
    <button class="dl" id="ai-coach" style="margin-top:8px">🧠 Coach me (advice)</button>
    <button class="dl" id="ai-learn" style="margin-top:8px">📚 Self-review &amp; learn lessons</button>
    <div id="ai-coach-out" style="margin-top:8px"></div>`:'';
  box.innerHTML=(hit!=null?`<div class="rec">🎯 Directional hit-rate so far: <b>${hit}%</b> on ${scored} scored calls. ${hit>=60?'Your calls are adding value — keep the discipline.':hit>=45?'Roughly coin-flip — tighten your criteria.':'Below 50% — review what your "Buy" calls have in common.'}</div>`:'')+t+'</table>'+lessonsHtml;
  if(window.AI_ON&&el('ai-coach'))el('ai-coach').onclick=()=>aiPost('/ai_analyst',{mode:'coach',context:{hit_rate_pct:hit,scored_calls:scored,journal:journal.slice(0,40)}},'ai-coach-out','Reviewing your calls…');
  if(window.AI_ON&&el('ai-learn'))el('ai-learn').onclick=async()=>{const b=el('ai-coach-out');b.innerHTML='<div class="spin"></div><p class="muted" style="text-align:center">Reviewing outcomes & distilling lessons…</p>';
    try{const r=await(await fetch('/ai_analyst',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({mode:'lessons',context:{hit_rate_pct:hit,journal:journal.slice(0,40)}})})).json();
      if(!r.text){b.innerHTML=`<p class="muted">${esc(r.note||r.error||'No lessons.')}</p>`;return;}
      const newLessons=r.text.split('\n').map(s=>s.replace(/^[-•*\\d.\\s]+/,'').trim()).filter(s=>s.length>8);
      if(newLessons.length&&SB&&USER){try{await SB.from('ai_lessons').insert(newLessons.map(c=>({user_id:USER.id,content:c.slice(0,400)})));await loadLessons();}catch(e){b.innerHTML='<p class="muted">Lessons generated but could not be saved — run the ai_lessons migration in the README. '+esc(e.message||'')+'</p>';return;}}
      b.innerHTML=`<div class="rec" style="white-space:pre-wrap">📚 New lessons saved — they'll now inform every future AI decision:\n${esc(newLessons.map(l=>'• '+l).join('\n'))}</div><p class="muted">${AI_DISCLAIMER}</p>`;
      renderMe();
    }catch(e){b.innerHTML='<p class="muted">Self-review failed.</p>';}};}

fetch('/universe').then(r=>r.json()).then(u=>{UNI=u.universe;MOD=u.models;
  el('ex-country').innerHTML=Object.keys(UNI).map(c=>`<option>${c}</option>`).join('');fillSectors();});
el('ex-country').onchange=fillSectors;el('ex-sector').onchange=fillCompanies;
function fillSectors(){const c=el('ex-country').value;el('ex-sector').innerHTML=Object.keys(UNI[c]).map(s=>`<option>${s}</option>`).join('');fillCompanies();}
function fillCompanies(){const c=el('ex-country').value,s=el('ex-sector').value;el('ex-company').innerHTML=(UNI[c][s]||[]).map(t=>`<option value="${t}">${t.replace('.NS','').replace('.BO','')}</option>`).join('');}

// generic autocomplete: wires an input + dropdown box to /search; onPick(fullSymbol)
function acWire(inputId,boxId,onPick){let t;const inp=el(inputId),box=el(boxId);if(!inp||!box)return;
  inp.addEventListener('input',e=>{clearTimeout(t);const q=e.target.value.trim();
    if(q.length<2){box.style.display='none';return;}
    t=setTimeout(async()=>{const list=await(await fetch('/search?q='+encodeURIComponent(q))).json();
      if(!Array.isArray(list)||!list.length){box.style.display='none';return;}
      box.innerHTML=list.map(x=>`<div data-sym="${x.symbol}">${x.india?'🇮🇳 ':''}${x.name||x.symbol} <small>· ${x.symbol} · ${x.exch}</small></div>`).join('');
      box.style.display='block';
      box.querySelectorAll('div').forEach(d=>d.onclick=()=>{box.style.display='none';onPick(d.dataset.sym);});},250);});}
acWire('ticker','acbox',sym=>{el('ticker').value=sym.replace('.NS','').replace('.BO','');
  el('market').value=sym.endsWith('.BO')?'BSE':sym.endsWith('.NS')?'NSE':'Global';showQuotePreview();});
document.addEventListener('click',e=>{if(!e.target.closest('.ac'))document.querySelectorAll('.acbox').forEach(b=>b.style.display='none');});
function resolveTicker(){let t=(el('ticker').value||'').trim().toUpperCase();const m=el('market').value;
  if((m==='NSE'||m==='BSE')&&t&&!/\.(NS|BO)$/.test(t))t+=(m==='NSE'?'.NS':'.BO');return t;}
let qpT;
async function showQuotePreview(){const box=el('quoteprev');if(!box)return;const t=resolveTicker();
  if(!t){box.innerHTML='';return;}box.innerHTML='<span class="muted">Fetching live price…</span>';
  try{const q=await(await fetch('/quote?tickers='+encodeURIComponent(t))).json();const x=q[t];
    if(!x||x.price==null){box.innerHTML='<span class="muted">No live quote — press Analyze.</span>';return;}
    const cur=/\.(NS|BO)$/.test(t)?'₹':'';const day=x.prev?((x.price-x.prev)/x.prev*100):0;
    box.innerHTML=`<b>${esc(t.replace('.NS','').replace('.BO',''))}</b> · <b>${cur}${fmt(x.price)}</b> <span class="${day>=0?'pos':'neg'}">${day>=0?'▲ +':'▼ '}${day.toFixed(2)}% today</span> <span class="muted">— press Analyze for the full report</span>`;
  }catch(e){box.innerHTML='';}}
el('ticker').addEventListener('blur',()=>{clearTimeout(qpT);qpT=setTimeout(showQuotePreview,150);});
el('market').addEventListener('change',showQuotePreview);

el('go').onclick=async()=>{let ticker,market;
  if(mode==='explore'){const s=el('ex-company').value;ticker=s;market=s.endsWith('.NS')?'NSE':s.endsWith('.BO')?'BSE':'Global';}
  else{ticker=el('ticker').value;market=el('market').value;}
  if(!ticker){out.innerHTML='<section class="glass">Pick or type a company first.</section>';return;}
  const b=el('go');b.disabled=true;b.textContent='Analyzing…';charts.forEach(c=>c.destroy());charts=[];out.innerHTML='<div class="spin"></div>';
  try{const d=await(await fetch('/analyze',{method:'POST',headers:{'content-type':'application/json'},
    body:JSON.stringify({ticker,market,horizon:el('horizon').value,risk:el('risk').value,style:el('style').value,capital:+el('capital').value,years:+el('years').value})})).json();
    d.error?out.innerHTML=`<section class="glass">❌ ${d.error}</section>`:render(d);
  }catch(e){out.innerHTML=`<section class="glass">❌ ${e}</section>`;}finally{b.disabled=false;b.textContent='Analyze';}};

function vcls(v){return v=='Undervalued'?'v-under':v=='Overvalued'?'v-over':'v-fair'}
function money(cur,v,inr){if(v==null)return '—';let s=cur+fmt(v);if(inr!=null&&cur!=='₹')s+=` <span class="muted">(₹${fmt(inr)})</span>`;return s;}
const GLOSSARY={
"P/E (trailing)":"Price ÷ earnings per share. How many ₹ you pay for ₹1 of yearly profit. Lower = cheaper; compare within the sector.",
"P/E (forward)":"Same as P/E but on next year's expected profit.",
"P/B":"Price ÷ book value (net assets). Below ~1 can mean cheap-on-assets; banks/finance are read on P/B.",
"PEG":"P/E ÷ growth rate. ~1 means price and growth are balanced; <1 cheap for the growth, >2 expensive.",
"ROE %":"Return on equity = profit ÷ shareholders' money. >15% is efficient. Buffett's favourite quality gauge.",
"Operating margin %":"Profit from core operations per ₹100 of sales. Higher = pricing power / efficiency.",
"Net / PAT margin":"Final profit kept per ₹100 of sales after everything.",
"EBITDA margin":"Profit before interest, tax, depreciation per ₹100 sales — compares operating profitability across companies.",
"Debt/Equity %":"Borrowed money vs own capital. Below 60% is comfortable; high debt adds risk in downturns.",
"Current ratio":"Short-term assets ÷ short-term dues. Above 1 means it can pay near-term bills.",
"Beta":"How much the stock swings vs the market. 1 = moves with market, <1 calmer, >1 more volatile. Use it to size risk.",
"Free cash flow":"Cash left after running and investing in the business — what truly funds dividends/buybacks/growth.",
"Dividend yield %":"Annual dividend ÷ price. Your cash income rate from holding it.",
"Revenue growth":"How fast sales grow year-on-year.","Earnings growth":"How fast profit grows year-on-year.",
"RSI (14)":"Momentum 0–100. <30 oversold (bounce odds), >70 overbought (pullback odds). Confirm with trend.",
"MACD":"Two moving averages' gap vs its signal line. Above signal = bullish momentum; histogram shows strength.",
"ADX (14)":"Trend STRENGTH (not direction). >25 = a real trend; pair with +DI/−DI for the side.",
"Stochastic %K/%D":"Where price sits in its recent range. <20 oversold, >80 overbought.",
"Bollinger %B":"Position inside volatility bands. ~0 at lower band (stretched down), ~1 at upper band (stretched up).",
"Momentum / ROC (10)":"Rate of change over 10 bars. Positive = upward momentum.",
"OBV":"On-Balance Volume — adds volume on up days, subtracts on down. Rising OBV confirms an uptrend.",
"ATR (14)":"Average True Range — typical bar size (volatility). Set stops/position size off it.",
"SMA 50 / 200":"50- and 200-day averages. Price above both, 50 above 200 = healthy long-term uptrend.",
"Sharpe ratio":"Return per unit of total risk. >1 good, >2 excellent.","Sortino ratio":"Like Sharpe but counts only downside risk.",
"Alpha (vs ^NSEI)":"Return above what beta predicts — skill vs the index.","Portfolio beta":"Your whole book's sensitivity to the market.",
"1-day VaR (95%)":"A typical bad-day loss — exceeded about 1 day in 20.","CVaR / Exp. Shortfall":"Average loss on the worst 5% of days (worse than VaR).",
"Max drawdown":"Worst peak-to-trough drop — the deepest pain you'd have sat through.","Golden Cross":"50-day average crosses above the 200-day — classic long-term bullish signal.","Death Cross":"50-day crosses below 200-day — long-term bearish warning.",
"EV/EBITDA":"Enterprise value (mkt cap + debt − cash) ÷ EBITDA. The capital-structure-neutral multiple deal desks use; lower = cheaper.",
"FCF yield %":"Free cash flow ÷ market cap. The actual cash return the business generates for owners; >5% is attractive.",
"P/S":"Price ÷ sales. A fallback multiple when earnings are thin or volatile.",
"ROCE %":"Return on capital employed = EBIT ÷ (assets − current liabilities). Capital efficiency including debt — sharper than ROE.",
"ROIC %":"Return on invested capital. If it beats the ~10–12% cost of capital, the company is genuinely value-creating.",
"Interest coverage":"EBIT ÷ interest expense — how many times over it can pay its interest. <2.5× is fragile.",
"Revenue CAGR 3y %":"3-year compound annual sales growth — a durable trend, not one noisy year.",
"Earnings CAGR 3y %":"3-year compound annual profit growth.",
"Altman Z":"Bankruptcy-risk score from 5 ratios. >2.99 safe, 1.81–2.99 grey zone, <1.81 distress.",
"Piotroski F":"9-point fundamental-quality checklist (profitability, leverage, efficiency). 7–9 strong, ≤3 weak.",
"Relative Strength vs index":"Stock return minus index return (~6 months). Positive = it's leading the market (CMT relative strength).",
"EBITDA":"Earnings before interest, tax, depreciation & amortization — a clean read on core operating profit.",
"PAT (net income)":"Profit After Tax — the bottom-line profit left for shareholders.",
"Net/PAT margin %":"Final profit kept per ₹100 of sales.","Operating margin %":"Core-operations profit per ₹100 of sales.",
"Revenue growth %":"Year-on-year sales growth.","Earnings growth %":"Year-on-year profit growth.","Dividend yield %":"Annual dividend ÷ price — your cash income rate.",
"Current ratio":"Short-term assets ÷ short-term dues. Above 1 means it can cover near-term bills.","Debt/Equity %":"Borrowings vs own capital. <60% is comfortable.",
// scorecard dimension names
"Valuation (DCF)":"Cheapness on discounted-cash-flow fair value vs price.","Valuation (P/E)":"Cheapness on the price-to-earnings multiple.","Valuation (P/B)":"Cheapness on price-to-book.","Valuation (PEG)":"P/E adjusted for growth (~1 is fair).",
"Growth-adjusted P/E":"How the current P/E compares to the P/E its growth & quality justify.","Profitability (ROE)":"Return on shareholders' equity — quality of profits.",
"Operating margin":"Core operating profitability.","Net / PAT margin":"Bottom-line profitability.","EBITDA margin":"Operating profitability before non-cash & financing items.",
"Revenue growth":"Sales growth trend.","Earnings growth":"Profit growth trend.","Financial health (D/E)":"Leverage — lower debt is safer.","Liquidity (current ratio)":"Ability to pay short-term bills.",
"Cash flow (FCF)":"Whether the business generates positive free cash flow.","Dividend":"Income returned to shareholders.","Momentum / trend":"Price trend & momentum (above EMA200, RSI position).",
"Capital efficiency (ROCE)":"Return on capital employed — how well it turns capital into profit.","Cash valuation (FCF yield)":"Free cash flow ÷ market cap — the cash return you earn.",
"Quality (Piotroski)":"9-point fundamental-quality checklist, scaled to /10.","Solvency (Altman Z)":"Bankruptcy-risk score, scaled to /10 (higher = safer).",
"Diversification":"How well risk is spread — higher effective holdings & lower correlation is better.","Portfolio beta":"Your whole book's sensitivity to the market.",
"Expected return":"Annualized historical mean return (NOT a forecast).","CAGR":"Compound annual growth of the portfolio over the lookback.","Volatility":"Annualized standard deviation of returns — the swing size.",
"Sector concentration":"Largest single-sector weight. Keep under ~30% to limit concentration risk.","Annual dividend":"Estimated forward dividend cash from the whole portfolio."};
function tip(term){const g=GLOSSARY[term];return g?`<span class="tip" data-tip="${g.replace(/"/g,'&quot;')}">${term}</span>`:term;}
function csvCell(x){x=(x==null?'':String(x));return /[",\n]/.test(x)?'"'+x.replace(/"/g,'""')+'"':x;}
function dl(name,rows){const csv=rows.map(r=>r.map(csvCell).join(',')).join('\n');const b=new Blob([csv],{type:'text/csv'});const a=document.createElement('a');a.href=URL.createObjectURL(b);a.download=name;a.click();}
function dlFundamental(){const d=window.LAST;if(!d)return;const r=[['FundaPilot — Fundamental analysis',d.name,d.ticker],[],['Metric','Value','Benchmark']];
  for(const[k,o]of Object.entries(d.ratios))r.push([k,o.value,o.bench||'']);
  r.push([],['Scorecard (/10)','Score']);for(const[k,v]of Object.entries(d.scores))if(v!=null)r.push([k,v]);
  r.push([],['Valuation','']);const v=d.valuation;[['Combined verdict',v.verdict],['DCF verdict',v.dcf_verdict],['DCF fair value (mkt cap)',v.fair_value_mktcap],['Margin of safety %',v.margin_of_safety_pct],['Growth %',v.growth_pct],['Fair P/E (growth-adjusted)',v.fair_pe],['Current P/E',v.current_pe],['PEG',v.peg],['Reverse-DCF implied growth %',v.implied_growth_pct]].forEach(x=>r.push(x));
  dl(d.ticker.replace('.','_')+'_fundamental.csv',r);}
function dlTechnical(){const d=window.LAST,t=window.LASTTA;if(!t){alert('Open the Advanced technicals section first.');return;}
  const r=[['FundaPilot — Technical analysis',d.name,d.ticker,'timeframe: '+t.tf],['Net bias',t.net_bias],[],['Indicator','Value','Signal']];
  for(const[k,o]of Object.entries(t.indicators))r.push([k,o.value,o.signal]);
  r.push([],['Pattern','Bias','Confidence','Detail']);(t.patterns||[]).forEach(p=>r.push([p.name,p.bias,p.confidence,p.detail]));
  dl(d.ticker.replace('.','_')+'_technical_'+t.tf+'.csv',r);}
function render(d){const cur=d.currency;out.innerHTML='';window.LAST=d;window.LASTTA=null;logCall(d);
  const _nm=(d.name||'').replace(/'/g,'');
  out.append($(`<section class="glass"><h2>${d.name} <span class="muted">${d.ticker}</span>
      <span style="float:right"><button class="dl" onclick="favWatch('${d.ticker}','${_nm}')">❤️ Favorite</button><button class="dl" onclick="saveWatch('${d.ticker}','${_nm}')">★ Watchlist</button></span></h2>
    <div class="muted">${d.sector||''} · ${d.industry||''}</div><div style="margin:8px 0">${(d.tags||[]).map(t=>`<span class="tag">${t}</span>`).join('')}</div>
    <div class="grid cards" style="margin-top:6px">
      <div class="chip">Price<b>${money(cur,d.price,d.price_inr)}</b></div>
      <div class="chip">Market cap<b>${money(cur,d.marketCap,d.marketCap_inr)}</b></div>
      <div class="chip">Overall<b>${d.overall??'—'} / 10</b></div>
      <div class="chip">${d.style.label}<b>${d.style.score??'—'}/10</b><span class="muted">${d.style.take}</span></div>
      <div class="chip">Valuation<b><span class="verdict ${vcls(d.valuation.verdict)}">${d.valuation.verdict}</span></b></div></div>
    <div class="rec" style="margin-top:14px">💡 ${d.recommendation}</div>
    ${d.summary?`<details style="margin-top:12px"><summary>Business summary</summary><p class="muted">${d.summary}</p></details>`:''}</section>`));

  // research assistant summary (Buy/Hold/Avoid)
  const rv=d.research, vc=rv.verdict==='Buy'?'v-under':rv.verdict==='Avoid'?'v-over':'v-fair';
  out.append($(`<section class="glass"><h2>🤖 Research summary</h2>
    <div class="grid two"><div><h3>Strengths</h3>${(rv.strengths||[]).length?rv.strengths.map(x=>`<div class="flag f-good">${x}</div>`).join(''):'<div class="muted">None detected.</div>'}</div>
    <div><h3>Risks</h3>${(rv.risks||[]).length?rv.risks.map(x=>`<div class="flag f-bad">${x}</div>`).join(''):'<div class="muted">None detected.</div>'}</div></div>
    <div style="margin-top:10px">Verdict: <span class="verdict ${vc}">${rv.verdict}</span> <span class="muted">— ${rv.why}</span></div></section>`));

  // 🧠 AI analyst — separate, collapsible dropdown so your manual analysis stands alone
  out.append($(`<section class="glass"><details><summary style="font-size:18px;font-weight:600;cursor:pointer">🧠 AI analyst — opinion <span class="muted" style="font-weight:400">(optional · click to expand)</span></summary>
    <div style="margin-top:10px">
    ${window.AI_ON?`<p class="muted">Reasons over the computed numbers above — it won't invent figures. ${AI_DISCLAIMER}</p>
      <div style="display:flex;flex-wrap:wrap;gap:6px">
        <button class="dl" id="ai-go" style="margin:0">🧠 Decision</button>
        <button class="dl" id="ai-bear" style="margin:0">🐻 Bear case</button>
        <button class="dl" id="ai-news" style="margin:0">📰 News digest</button>
        <button class="dl" id="ai-best" style="margin:0">🏆 Best in sector</button></div>
      <div id="ai-out" style="margin-top:10px"></div>
      <div class="ac" style="max-width:560px;margin-top:10px"><label>Ask a finance follow-up about this stock</label>
        <div style="display:flex;gap:8px"><input id="ai-q" placeholder='e.g. "is the debt a worry?", "value it for a 5-year hold"'><button class="dl" id="ai-ask" style="margin:0">Ask</button></div></div>
      <div id="ai-chat" style="margin-top:8px"></div>
      <hr style="border:0;border-top:1px solid var(--line);margin:14px 0">
      <h3>⚔️ AI compare with another stock</h3>
      <div class="ac" style="max-width:460px"><input id="ai-cmp" placeholder="Type a 2nd company to compare…" autocomplete="off"><div class="acbox" id="ai-cmpbox"></div></div>
      <button class="dl" id="ai-cmp-go" style="margin-top:6px">Compare which is the better buy ▸</button>
      <div id="ai-cmp-out" style="margin-top:8px"></div>`
    :`<p class="muted">The AI analyst is <b>off</b> on this deployment. It thinks like a CFA·CMT·PhD·BlackRock PM over the real metrics/valuation/technicals this tool computes. To switch it on (your choice of provider, incl. a <b>free</b> one):<br>
      • <b>Free</b>: a Groq key → set <code>AI_BASE_URL=https://api.groq.com/openai/v1</code>, <code>AI_API_KEY=…</code>, <code>AI_MODEL=llama-3.3-70b-versatile</code><br>
      • <b>Claude</b>: set <code>ANTHROPIC_API_KEY=…</code> (optionally <code>AI_MODEL=claude-3-5-haiku-latest</code>)<br>
      Add these as Render env vars (see README) — everything else keeps working without it.</p>`}
    </div></details></section>`));
  if(window.AI_ON){
    const ctx=aiContext(d);
    el('ai-go').onclick=()=>aiPost('/ai_analyst',{mode:'stock',context:ctx,memory:aiMemory()},'ai-out','Thinking…');
    el('ai-bear').onclick=()=>aiPost('/ai_analyst',{mode:'bear',context:ctx,memory:aiMemory()},'ai-out','Building the bear case…');
    el('ai-news').onclick=()=>aiPost('/ai_analyst',{mode:'news',context:ctx},'ai-out','Reading the news…');
    el('ai-best').onclick=()=>aiPost(`/ai_sector_pick?ticker=${encodeURIComponent(d.ticker)}&sector=${encodeURIComponent(d.sector||'')}`,null,'ai-out','Evaluating sector peers…');
    el('ai-ask').onclick=async()=>{const q=el('ai-q').value.trim();if(!q)return;const cc=el('ai-chat');
      cc.innerHTML='<div class="flag" style="background:rgba(110,168,254,.1)"><b>You:</b> '+esc(q)+'</div><div class="spin"></div>';
      try{const r=await(await fetch('/ai_chat',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({q,context:ctx})})).json();
        cc.innerHTML='<div class="flag" style="background:rgba(110,168,254,.1)"><b>You:</b> '+esc(q)+'</div>'+(r.text?`<div class="flag f-good" style="white-space:pre-wrap">🧠 ${esc(r.text)}</div><p class="muted">${AI_DISCLAIMER}</p>`:`<div class="muted">${esc(r.error||'No answer.')}</div>`);
      }catch(e){cc.innerHTML='<p class="muted">AI call failed.</p>';}};
    let cmpB=null;acWire('ai-cmp','ai-cmpbox',sym=>{cmpB=sym;el('ai-cmp').value=sym.replace('.NS','').replace('.BO','');});
    el('ai-cmp-go').onclick=async()=>{const b=cmpB||el('ai-cmp').value.trim().toUpperCase();if(!b){alert('Pick a second company.');return;}
      const o3=el('ai-cmp-out');o3.innerHTML='<div class="spin"></div><p class="muted" style="text-align:center">Evaluating both & comparing…</p>';
      try{const r=await(await fetch(`/ai_compare?a=${encodeURIComponent(d.ticker)}&b=${encodeURIComponent(b)}`)).json();
        o3.innerHTML=r.text?`<div class="rec" style="white-space:pre-wrap;line-height:1.55">${esc(r.text)}</div><p class="muted">${AI_DISCLAIMER}</p>`:`<p class="muted">${esc(r.error||'No response.')}</p>`;
      }catch(e){o3.innerHTML='<p class="muted">Compare failed.</p>';}};
  }

  // institutional quality & solvency highlight
  const q=d.quality||{};
  if(q.piotroski!=null||q.altman_z!=null||q.roce!=null||q.ev_ebitda!=null){
    const zc=q.altman_z==null?'':q.altman_z>=3?'pos':q.altman_z<1.81?'neg':'';
    const fc=q.piotroski==null?'':q.piotroski>=7?'pos':q.piotroski<=3?'neg':'';
    out.append($(`<section class="glass"><h2>🏛️ Institutional quality &amp; solvency <span class="muted">(from the statements)</span></h2>
      <div class="grid cards">
        <div class="chip">${tip('Piotroski F')}<b class="${fc}">${q.piotroski==null?'—':q.piotroski+' / '+q.piotroski_max}</b><span class="muted">fundamental quality</span></div>
        <div class="chip">${tip('Altman Z')}<b class="${zc}">${q.altman_z??'—'}</b><span class="muted">${q.altman_z==null?'':q.altman_z>=3?'Safe':q.altman_z<1.81?'Distress zone':'Grey zone'}</span></div>
        <div class="chip">${tip('ROCE %')}<b>${q.roce==null?'—':q.roce+'%'}</b></div>
        <div class="chip">${tip('ROIC %')}<b>${q.roic==null?'—':q.roic+'%'}</b></div>
        <div class="chip">${tip('EV/EBITDA')}<b>${q.ev_ebitda==null?'—':q.ev_ebitda+'x'}</b></div>
        <div class="chip">${tip('FCF yield %')}<b>${q.fcf_yield==null?'—':q.fcf_yield+'%'}</b></div>
        <div class="chip">${tip('Interest coverage')}<b>${q.interest_coverage==null?'—':q.interest_coverage+'x'}</b></div></div>
      <p class="muted">${q.note} Hover any term for what it is &amp; how to use it.</p></section>`));}

  // download bar
  out.append($(`<section class="glass" style="padding:12px 18px"><b>⬇️ Export to Excel/CSV:</b>
    <button class="dl" onclick="dlFundamental()">Fundamental analysis</button>
    <button class="dl" onclick="dlTechnical()">Technical analysis</button>
    <span class="muted">CSV opens directly in Excel/Sheets — keep your own records.</span></section>`));

  let s='<section class="glass"><h2>Scorecard (/10) <span class="muted">— '+Object.values(d.scores).filter(x=>x!=null).length+' dimensions, hover a name for the definition</span></h2><div class="grid cards">';
  for(const[k,v]of Object.entries(d.scores)){if(v==null)continue;const col=v>=7?'var(--good)':v>=4?'var(--warn)':'var(--bad)';
    s+=`<div class="chip"><div style="display:flex;justify-content:space-between"><span style="font-size:13px">${tip(k)}</span><b style="color:${col};font-size:16px">${v}</b></div><div class="bar" style="margin-top:6px"><i style="width:${v*10}%"></i></div></div>`;}
  out.append($(s+'</div></section>'));

  const val=d.valuation;
  out.append($(`<section class="glass"><h2>Valuation — combined verdict <span class="muted">(DCF + growth-adjusted P/E + PEG)</span></h2>
    <div class="grid cards">
      <div class="chip">Final verdict<b><span class="verdict ${vcls(val.verdict)}">${val.verdict}</span></b></div>
      <div class="chip">${tip('PEG')} & growth read<b style="font-size:14px">${val.pe_growth_verdict||'—'}</b></div>
      <div class="chip">Growth used<b>${val.growth_pct==null?'—':val.growth_pct+'%'}</b></div></div>
    <p class="muted" style="margin-top:8px">${val.combined_reason||''}. <b>${val.method_note}</b></p>
    <h3>1) Growth-adjusted P/E (do investors pay up for the future?)</h3>
    <div class="grid cards">
      <div class="chip">Current P/E<b>${fmt(val.current_pe)}</b></div>
      <div class="chip">Fair P/E for its growth<b>${val.fair_pe??'—'}</b><span class="muted">8 + 0.9×growth, +quality</span></div>
      <div class="chip">Gap<b>${val.pe_gap_pct==null?'—':val.pe_gap_pct+'%'}</b><span class="muted">+ = room to re-rate</span></div></div>
    <h3>2) DCF &amp; reverse DCF (cash-flow value)</h3>
    <div class="grid cards">
      <div class="chip">DCF fair value<b>${money(cur,val.fair_value_mktcap,null)}</b><span class="muted">${val.dcf_verdict}</span></div>
      <div class="chip">Margin of safety<b>${val.margin_of_safety_pct==null?'—':val.margin_of_safety_pct+'%'}</b></div>
      <div class="chip">Reverse-DCF implied growth<b>${val.implied_growth_pct==null?'—':val.implied_growth_pct+'%'}</b><span class="muted">priced-in</span></div></div>
    ${val.scenarios&&Object.keys(val.scenarios).length?`<h3>Scenario DCF — bear / base / bull range</h3><div class="grid cards">
      ${Object.entries(val.scenarios).map(([k,s])=>`<div class="chip">${k}<b>${money(cur,s.fair_value,null)}</b><span class="muted">${s.mos_pct>=0?'+':''}${s.mos_pct}% · g ${s.growth_pct}%, disc ${s.discount_pct}%</span></div>`).join('')}</div>
      <p class="muted">A value range, not a single point — the honest way to quote intrinsic value.</p>`:''}
    <h3>3) Industry P/E — local vs global <span class="muted">(macro re-rating check)</span></h3>
    <div id="indpe" class="muted">Loading industry P/E…</div>
    <p class="muted" style="margin-top:8px">FCF used ${money(cur,val.fcf,null)} (${val.fcf_source||'n/a'}). A high P/E isn't automatically "overvalued" — if growth, quality and the global industry support it, paying up can be justified.</p></section>`));
  loadIndustryPE(d.ticker,d.sector);

  let rt='<section class="glass"><h2>Ratio analysis <span class="muted">— hover for the definition'+(window.AI_ON?'; click 🧠 for an AI explanation for THIS company':'')+'</span></h2><table><tr><th>Metric</th><th>Value</th><th style="text-align:left">What it means · benchmark</th></tr>';
  for(const[k,o]of Object.entries(d.ratios)){const u=o.unit||'';
    const disp=o.value==null?'—':(o.inr!==undefined?money(cur,o.value,o.inr):(u==='%'?o.value+'%':u==='x'?o.value+'x':o.value));
    const meaning=o.text?`<span class="dot d-${o.rating}"></span>${o.text}${o.bench?' <span class="muted">('+o.bench+')</span>':''}`:'<span class="muted">—</span>';
    const aiex=window.AI_ON?` <a href="#" class="aiex" data-m="${esc(k)}" data-v="${o.value==null?'':o.value}" title="AI explain for this company" style="text-decoration:none">🧠</a>`:'';
    rt+=`<tr><td>${tip(k)}${aiex}</td><td>${disp}</td><td style="text-align:left;font-size:12px">${meaning}</td></tr>`;}
  out.append($(rt+'</table><div id="aiex-out"></div><p class="muted">🟢 good · 🟡 ok · 🔴 watch. Benchmarks are general rules of thumb; compare within the same sector.</p></section>'));
  if(window.AI_ON){const ctx2=aiContext(d);document.querySelectorAll('.aiex').forEach(a=>a.onclick=async e=>{e.preventDefault();
    const m=a.dataset.m,v=a.dataset.v,box=el('aiex-out');box.innerHTML=`<div class="flag" style="background:rgba(110,168,254,.1)"><div class="spin" style="margin:6px auto"></div><div class="muted" style="text-align:center">AI explaining ${esc(m)}…</div></div>`;
    try{const r=await(await fetch('/ai_chat',{method:'POST',headers:{'content-type':'application/json'},
      body:JSON.stringify({q:`Explain the metric "${m}" (value: ${v||'n/a'}) specifically for ${d.name}: what it measures, whether this level is good/bad for THIS company and sector, and what it implies — 3-4 sentences.`,context:ctx2})})).json();
      box.innerHTML=r.text?`<div class="flag f-good" style="white-space:pre-wrap"><b>🧠 ${esc(m)}:</b> ${esc(r.text)}</div><p class="muted">${AI_DISCLAIMER}</p>`:`<p class="muted">${esc(r.error||'No answer.')}</p>`;
    }catch(err){box.innerHTML='<p class="muted">AI call failed.</p>';}});}

  out.append($(`<section class="glass"><h2>Technical analysis</h2><div class="grid two">${tf('Daily',d.technical.daily)}${tf('Weekly',d.technical.weekly)}</div>
    <p class="muted">Benchmarks — RSI(14): below 30 = oversold 🟢, 30–70 = neutral, above 70 = overbought 🔴. EMA200: price above = long-term uptrend; below = downtrend.</p>
    <div class="grid two" style="margin-top:8px"><div><canvas id="cD" height="160"></canvas><div class="muted" style="text-align:center">Daily vs EMA200</div></div>
    <div><canvas id="cW" height="160"></canvas><div class="muted" style="text-align:center">Weekly vs EMA200</div></div></div></section>`));
  drawLine('cD',d.technical.daily);drawLine('cW',d.technical.weekly);

  // advanced CMT technicals (indicators + pattern detection, daily/weekly switch)
  out.append($(`<section class="glass"><h2>📐 Advanced technicals (CMT) — indicators &amp; chart patterns</h2>
    <div class="seg" id="tfseg"><button class="on" data-tf="daily">Daily</button><button data-tf="weekly">Weekly</button></div>
    <div id="taout" style="margin-top:12px"><div class="spin"></div></div></section>`));
  document.getElementById('tfseg').querySelectorAll('button').forEach(b=>b.onclick=()=>{
    document.getElementById('tfseg').querySelectorAll('button').forEach(x=>x.classList.remove('on'));b.classList.add('on');loadTechnicals(d.ticker,d.currency,b.dataset.tf);});
  loadTechnicals(d.ticker,d.currency,'daily');

  // price range & period stats (high/low/avg/change over 1M…Max/ATH)
  const RNGS=[['1mo','1M'],['6mo','6M'],['1y','1Y'],['5y','5Y'],['10y','10Y'],['max','Max / ATH']];
  out.append($(`<section class="glass"><h2>📅 Price range &amp; period stats</h2>
    <div class="seg" id="rngseg">${RNGS.map(([r,l],i)=>`<button data-r="${r}" class="${i==2?'on':''}">${l}</button>`).join('')}</div>
    <div id="rngout" style="margin-top:10px"><div class="spin"></div></div></section>`));
  document.getElementById('rngseg').querySelectorAll('button').forEach(b=>b.onclick=()=>{
    document.getElementById('rngseg').querySelectorAll('button').forEach(x=>x.classList.remove('on'));b.classList.add('on');loadRange(d.ticker,d.currency,b.dataset.r);});
  loadRange(d.ticker,d.currency,'1y');

  if(d.history&&Object.keys(d.history).length){out.append($('<section class="glass"><h2>Historical statements <span class="muted">— in '+cur+' Crore (Cr) / Lakh (L)</span></h2><canvas id="cH" height="120"></canvas></section>'));drawHist('cH',d.history,cur);}

  let st='<section class="glass"><h2>Investor-style fit</h2><div class="grid two">';
  for(const[n,o]of Object.entries(d.styles_fit)){st+=`<div class="chip"><b>${n}</b> <span class="muted">— ${o.fit_pct}% fit</span><div style="margin-top:6px">`;
    for(const[c,ok]of Object.entries(o.checks))st+=`<div style="font-size:13px">${c}<span class="pill ${ok?'yes':'no'}">${ok?'✓':'✗'}</span></div>`;st+='</div></div>';}
  out.append($(st+'</div></section>'));

  let fl='<section class="glass"><div class="grid two"><div><h2>🟢 Green flags</h2>';
  fl+=d.green_flags.length?d.green_flags.map(x=>`<div class="flag f-good">${x}</div>`).join(''):'<div class="muted">None.</div>';
  fl+='</div><div><h2>🔴 Red flags</h2>'+(d.red_flags.length?d.red_flags.map(x=>`<div class="flag f-bad">${x}</div>`).join(''):'<div class="muted">None.</div>')+'</div></div></section>';
  out.append($(fl));

  // sector analysis
  const sa=d.sector_analysis;
  let sh=`<section class="glass"><h2>🧭 Sector analysis — ${sa.name||''}</h2><p class="muted">Recent sector news sentiment: <span class="pos">${sa.tally.pos} positive</span> · <span class="neg">${sa.tally.neg} negative</span>. ${sa.note}</p><div class="news">`;
  sh+=sa.news.length?sa.news.map(n=>`<div class="flag ${n.tone=='neg'?'f-bad':n.tone=='pos'?'f-good':''}"><a href="${safeUrl(n.link)}" target="_blank" rel="noopener noreferrer">${esc(n.title)}</a> <small class="muted">${n.date}</small></div>`).join(''):'<div class="muted">No sector headlines.</div>';
  out.append($(sh+'</div></section>'));

  out.append($(`<section class="glass"><h2>⚔️ Peer comparison <span class="muted">— ${d.peer_sector||''}</span></h2><div id="peerbox" class="muted">Loading peers…</div></section>`));
  if(d.peers&&d.peers.length)loadPeers(d.ticker,d.peers,cur);else el('peerbox').textContent='No mapped peers yet.';

  // allocation + portfolio plan
  const a=d.allocation,p=d.portfolio_plan;
  out.append($(`<section class="glass"><h2>💰 Allocation &amp; portfolio plan</h2>
    ${a.shares!=null?`<div class="grid cards"><div class="chip">Suggested weight<b>${a.suggested_weight_pct}%</b></div>
      <div class="chip">Shares to buy<b>${a.shares}</b></div><div class="chip">Amount<b>${cur}${fmt(a.amount)}</b></div>
      <div class="chip">Cash left<b>${cur}${fmt(a.cash_left)}</b></div></div><p class="muted">${a.note}</p>`:`<p class="muted">${a.note}</p>`}
    ${p.method?`<h3>Deployment method: ${p.method}</h3><p class="muted">${p.why}</p>
      <div class="grid cards"><div class="chip">Budget for this stock<b>₹${fmt(p.this_stock_budget)}</b></div>
      <div class="chip">Tranches<b>${p.tranches}</b></div><div class="chip">Per tranche<b>₹${fmt(p.per_tranche)}</b></div></div>
      <p class="muted">${p.note}</p>`:`<p class="muted">${p.note||''}</p>`}</section>`));

  // corporate actions + institutions
  let ca='<section class="glass"><div class="grid two"><div><h2>📅 Corporate actions</h2>';
  ca+=d.corporate_actions.length?d.corporate_actions.map(c=>`<div class="kv"><b>${c.type}</b> — ${c.date}${c.value?' ('+c.value+')':''}</div>`).join(''):'<div class="muted">None in Yahoo data.</div>';
  ca+=`</div><div><h2>🏛️ Institutional activity</h2><p>Institutional holding: <b>${d.institutional.pct||'—'}</b></p>`;
  if(d.institutional.holders.length){ca+='<table><tr><th>Top holders</th><th>%</th></tr>';d.institutional.holders.forEach(h=>ca+=`<tr><td>${h.name}</td><td>${h.pct.toFixed(2)}%</td></tr>`);ca+='</table>';}
  out.append($(ca+`<p class="muted">${d.institutional.note}</p></div></div></section>`));

  let nh='<section class="glass"><h2>📰 Live news</h2><div class="news">';
  nh+=d.news.length?d.news.map(n=>`<div class="flag ${n.tone=='neg'?'f-bad':n.tone=='pos'?'f-good':''}"><a href="${safeUrl(n.link)}" target="_blank" rel="noopener noreferrer">${esc(n.title)}</a> <small class="muted">${n.date}</small></div>`).join(''):'<div class="muted">No headlines.</div>';
  out.append($(nh+'</div><p class="muted">Live from Google News (14d). Tone is a keyword heuristic — read the source.</p></section>'));

  // proof / methodology
  const m=d.methodology;
  let pf='<section class="glass"><h2>🔬 How this was calculated (proof)</h2>';
  pf+=`<div class="kv"><b>Ratios.</b> ${m.ratios_source}</div>`;
  pf+=`<div class="kv"><b>Free cash flow.</b> ${money(cur,m.fcf.value,null)} via ${m.fcf.source}. ${Object.keys(m.fcf.components||{}).length?'Components: '+JSON.stringify(m.fcf.components):''}</div>`;
  pf+=`<div class="kv"><b>DCF.</b> ${m.dcf.formula}<br>Inputs → FCF ${money(cur,m.dcf.fcf,null)}, growth ${m.dcf.growth_pct}%, discount ${m.dcf.discount_pct}%, ${m.dcf.years}y, terminal ${m.dcf.terminal_growth_pct}% ⇒ fair value ${money(cur,m.dcf.fair_value,null)}.</div>`;
  pf+=`<div class="kv"><b>Reverse DCF.</b> Implied growth ${m.reverse_dcf.implied_growth_pct}% — ${m.reverse_dcf.meaning}</div>`;
  pf+=`<div class="kv"><b>FX.</b> ${m.fx_used.pair} = ${m.fx_used.rate}</div>`;
  if(m.statements_used&&Object.keys(m.statements_used).length){pf+='<details style="margin-top:8px"><summary>Raw statement numbers used (from annual reports via Yahoo)</summary><pre style="white-space:pre-wrap;font-size:12px">'+JSON.stringify(m.statements_used,null,2)+'</pre></details>';}
  out.append($(pf+'</section>'));

  let ref='<section class="glass"><h2>📎 References &amp; proofs</h2><ul>'+d.references.map(r=>`<li><a href="${r.url}" target="_blank">${r.label}</a></li>`).join('')+'</ul>';
  if(d.data_quality&&d.data_quality.length)ref+=`<details><summary>Data quality notes (${d.data_quality.length})</summary><ul>${d.data_quality.map(x=>'<li class="muted">'+x+'</li>').join('')}</ul></details>`;
  out.append($(ref+'</section>'));
}
async function loadPeers(ticker,peers,cur){const rows=await(await fetch('/peers?tickers='+encodeURIComponent([ticker,...peers].join(',')))).json();
  let t='<table><tr><th>Company</th><th>P/E</th><th>P/B</th><th>ROE%</th><th>Margin%</th><th>Rev gr%</th><th>Mkt cap</th></tr>';
  rows.forEach(x=>{const me=x.ticker===ticker;t+=`<tr style="${me?'background:rgba(110,168,254,.10)':''}"><td>${me?'⭐ ':''}${x.name||x.ticker}</td><td>${fmt(x.pe)}</td><td>${fmt(x.pb)}</td><td>${fmt(x.roe)}</td><td>${fmt(x.margin)}</td><td>${fmt(x.rev_growth)}</td><td>${cur}${fmt(x.marketCap)}</td></tr>`;});
  el('peerbox').outerHTML='<div>'+t+'</table><p class="muted">⭐ = analyzed company. Peers from the sector universe — compare ratios side by side.</p></div>';}
function tf(label,t){if(!t)return `<div class="chip"><b>${label}</b><div class="muted">no data</div></div>`;
  const r=t.rsi,zone=r==null?'—':r<30?'Oversold 🟢':r>70?'Overbought 🔴':'Neutral 🟡';
  return `<div class="chip"><b>${label}</b><div>RSI(14): <b>${r??'—'}</b> <span class="muted">${zone}</span></div><div>Trend: <b>${t.above_ema?'Above EMA200 ▲':'Below EMA200 ▼'}</b></div><div class="muted">EMA200 ${t.ema200}</div></div>`;}
function drawLine(id,t){if(!t)return;charts.push(new Chart(el(id),{type:'line',data:{labels:t.dates,datasets:[{label:'Price',data:t.series,borderColor:'#6ea8fe',borderWidth:1.5,pointRadius:0,tension:.2},{label:'EMA200',data:t.dates.map(()=>t.ema200),borderColor:'#ffd166',borderWidth:1,pointRadius:0,borderDash:[5,4]}]},options:{plugins:{legend:{labels:{color:'#8b97ad'}}},scales:{x:{ticks:{color:'#8b97ad',maxTicksLimit:6}},y:{ticks:{color:'#8b97ad'}}}}}));}
function drawHist(id,h,cur){cur=cur||'₹';const keys=Object.keys(h),labels=Object.keys(h[keys[0]]).reverse(),col={'Revenue':'#6ea8fe','EBITDA':'#b39bff','Net Income':'#39d98a'};
  // values are raw (full currency units). Show axis & tooltips in Cr/L via fmt() so they're readable.
  charts.push(new Chart(el(id),{type:'bar',data:{labels,datasets:keys.map(k=>({label:k,data:labels.map(l=>h[k][l]),backgroundColor:col[k]||'#888'}))},
    options:{plugins:{legend:{labels:{color:'#8b97ad'}},tooltip:{callbacks:{label:ctx=>ctx.dataset.label+': '+cur+fmt(ctx.parsed.y)}}},
      scales:{x:{ticks:{color:'#8b97ad'}},y:{ticks:{color:'#8b97ad',callback:v=>cur+fmt(v)}}}}}));}

async function loadIndustryPE(ticker,sector){const box=el('indpe');if(!box)return;
  try{const d=await(await fetch(`/industry_pe?ticker=${encodeURIComponent(ticker)}&market=Global&sector=${encodeURIComponent(sector||'')}`)).json();
    const me=(window.LAST&&window.LAST.valuation.current_pe);
    box.innerHTML=`<div class="grid cards">
      <div class="chip">This stock P/E<b>${fmt(me)}</b></div>
      <div class="chip">${d.local_sector||'Local'} industry P/E<b>${d.local_industry_pe??'—'}</b><span class="muted">India median (n=${d.local_n})</span></div>
      <div class="chip">${d.global_sector||'Global'} industry P/E<b>${d.global_industry_pe??'—'}</b><span class="muted">US median (n=${d.global_n})</span></div></div>
      <p class="muted">${me&&d.local_industry_pe?(me<d.local_industry_pe?'Cheaper than its Indian peers':'Pricier than its Indian peers'):''}. ${d.note}</p>`;
  }catch(e){box.textContent='Industry P/E unavailable.';}}

async function loadTechnicals(ticker,cur,tf){const box=el('taout');if(!box)return;box.innerHTML='<div class="spin"></div>';
  let t;try{t=await(await fetch(`/technicals?ticker=${encodeURIComponent(ticker)}&market=Global&tf=${tf}`)).json();}catch(e){box.innerHTML='Failed to load.';return;}
  if(t.error){box.innerHTML=`<p class="muted">${t.error}</p>`;return;}
  window.LASTTA=t;
  const bcl=t.net_bias.indexOf('ull')>-1?'v-under':t.net_bias.indexOf('ear')>-1?'v-over':'v-fair';
  let h=`<div style="margin-bottom:10px">Net read (${tf}): <span class="verdict ${bcl}">${t.net_bias}</span> <span class="muted">${t.bull_count} bullish vs ${t.bear_count} bearish signals</span></div>`;
  h+='<h3>Indicators <span class="muted">— hover for how to use</span></h3><table><tr><th>Indicator</th><th>Value</th><th>Signal</th></tr>';
  for(const[k,o]of Object.entries(t.indicators)){const sc=/ullish|Oversold|Uptrend|Positive|up$|lower band|rising/i.test(o.signal)?'pos':/earish|Overbought|Downtrend|Negative|down$|upper band|falling/i.test(o.signal)?'neg':'';
    h+=`<tr><td><span class="tip" data-tip="${(o.use||'').replace(/"/g,'&quot;')}">${k}</span></td><td>${o.value??'—'}${o.signal_line!==undefined?' / sig '+o.signal_line:''}${o.d!==undefined?' / %D '+o.d:''}${o.sma200!==undefined?' / 200: '+(o.sma200??'—'):''}</td><td class="${sc}">${o.signal}</td></tr>`;}
  h+='</table><h3>Chart patterns detected</h3>';
  if(t.patterns&&t.patterns.length){h+=t.patterns.map(p=>{const c=p.bias==='bullish'?'f-good':p.bias==='bearish'?'f-bad':'';return `<div class="flag ${c}"><b>${p.name}</b> <span class="pill ${p.bias==='bullish'?'yes':p.bias==='bearish'?'no':''}">${p.bias} · ${p.confidence}</span><div style="font-size:12px;margin-top:3px">${p.detail}</div></div>`;}).join('');}
  else h+='<div class="muted">No clear textbook pattern right now (that itself is information — the trend is undecided).</div>';
  h+=`<div style="margin-top:12px"><canvas id="cTA" height="150"></canvas><div class="muted" style="text-align:center">Close + SMA50/200 + Bollinger bands; ◆ marks pattern swing points</div></div>`;
  h+=`<p class="muted">${t.note}</p>`;
  box.innerHTML=h;drawTA('cTA',t);}

async function loadRange(ticker,cur,rng){const box=el('rngout');if(!box)return;box.innerHTML='<div class="spin"></div>';
  let s;try{s=await(await fetch(`/price_stats?ticker=${encodeURIComponent(ticker)}&market=Global&rng=${rng}`)).json();}catch(e){box.innerHTML='<p class="muted">Unavailable.</p>';return;}
  if(s.error){box.innerHTML=`<p class="muted">${esc(s.error)}</p>`;return;}
  const lbl={'1mo':'1 month','6mo':'6 months','1y':'1 year','5y':'5 years','10y':'10 years','max':'all time'}[rng]||rng;
  const ath=rng==='max';
  box.innerHTML=`<div class="grid cards">
    <div class="chip">${ath?'All-time high':'Period high'}<b>${cur}${fmt(s.high)}</b><span class="muted">${s.high_date}</span></div>
    <div class="chip">${ath?'All-time low':'Period low'}<b>${cur}${fmt(s.low)}</b><span class="muted">${s.low_date}</span></div>
    <div class="chip">Average price<b>${cur}${fmt(s.avg)}</b></div>
    <div class="chip">Change (${lbl})<b class="${s.change_pct>=0?'pos':'neg'}">${s.change_pct>=0?'+':''}${s.change_pct}%</b><span class="muted">now ${cur}${fmt(s.last)}</span></div></div>
    <canvas id="cRNG" height="130" style="margin-top:10px"></canvas>`;
  charts.push(new Chart(el('cRNG'),{type:'line',data:{labels:s.dates,datasets:[{label:'Close',data:s.series,borderColor:'#6ea8fe',borderWidth:1.5,pointRadius:0,tension:.15}]},
    options:{plugins:{legend:{display:false},tooltip:{callbacks:{label:ctx=>cur+fmt(ctx.parsed.y)}}},scales:{x:{ticks:{color:'#8b97ad',maxTicksLimit:6}},y:{ticks:{color:'#8b97ad',callback:v=>cur+fmt(v)}}}}}));}
async function loadPeerEval(sector,sym,hq,cid,cache){const box=el(cid);if(!box)return;
  let data=cache[sector];
  if(!data){try{data=await(await fetch(`/peer_eval?country=India&sector=${encodeURIComponent(sector)}&exclude=${encodeURIComponent(sym)}`)).json();}catch(e){box.innerHTML='<span class="muted">Peers unavailable.</span>';return;}cache[sector]=data;}
  const rows=(data.rows||[]).filter(r=>r.ticker!==sym);
  if(!rows.length){box.innerHTML='<span class="muted">No mapped sector peers.</span>';return;}
  let t=`<details><summary>⚖️ Compare with best in ${esc(data.sector||sector)} (peers)</summary><table style="margin-top:6px"><tr><th>Peer</th><th>Quality</th><th>P/E</th><th>ROE%</th><th>ROCE%</th><th>D/E%</th><th>6m%</th></tr>`;
  rows.forEach(p=>{const better=(p.quality!=null&&hq!=null&&p.quality>hq);
    t+=`<tr><td>${better?'⭐ ':''}${esc(p.name||p.ticker)} <span class="muted">${p.ticker.replace('.NS','')}</span></td><td class="${better?'pos':''}">${p.quality==null?'<span class="muted">n/r</span>':p.quality}</td><td>${fmt(p.pe)}</td><td>${fmt(p.roe)}</td><td>${fmt(p.roce)}</td><td>${fmt(p.de)}</td><td>${fmt(p.momentum_6m)}</td></tr>`;});
  box.innerHTML=t+`</table><div class="muted" style="margin-top:4px">⭐ = higher fundamental quality than ${esc(sym.replace('.NS','').replace('.BO',''))} (${hq==null?'not rated':hq+'/10'}). "n/r" = peer left unrated (missing a metric). All from real statements.</div></details>`;}
function drawTA(id,t){const c=t.chart,base=c.base;
  const pts=[];(t.patterns||[]).forEach(p=>(p.points||[]).forEach(idx=>{const j=idx-base;if(j>=0&&j<c.close.length)pts.push({x:c.dates[j],y:c.close[j]});}));
  charts.push(new Chart(el(id),{data:{labels:c.dates,datasets:[
    {type:'line',label:'Close',data:c.close,borderColor:'#e7ecf3',borderWidth:1.6,pointRadius:0,tension:.15},
    {type:'line',label:'SMA50',data:c.sma50,borderColor:'#39d98a',borderWidth:1,pointRadius:0},
    {type:'line',label:'SMA200',data:c.sma200,borderColor:'#ffd166',borderWidth:1,pointRadius:0},
    {type:'line',label:'BB upper',data:c.bb_up,borderColor:'rgba(110,168,254,.4)',borderWidth:1,pointRadius:0,borderDash:[3,3]},
    {type:'line',label:'BB lower',data:c.bb_lo,borderColor:'rgba(110,168,254,.4)',borderWidth:1,pointRadius:0,borderDash:[3,3]},
    {type:'scatter',label:'Pattern point',data:pts,backgroundColor:'#ff6b6b',pointRadius:6,pointStyle:'rectRot'}]},
    options:{plugins:{legend:{labels:{color:'#8b97ad',boxWidth:10}}},scales:{x:{ticks:{color:'#8b97ad',maxTicksLimit:6}},y:{ticks:{color:'#8b97ad'}}}}}));}

// ---- model portfolios: sector allocation, then drill into strong companies ----
let curSec=null;
function renderModels(){const cap=+el('capital').value||100000;
  let h=`<section class="glass"><h2>📊 Model portfolios — sector allocation</h2><p class="muted">How capital is split across <b>sectors</b> for each risk profile (equity only, ₹${fmt(cap)}). Pick a ranking, then click any sector to load strong companies. Templates, not live-trend forecasts.</p>
    <div style="margin:6px 0 14px"><label>Rank strong picks by</label><select id="tilt" style="max-width:260px">
      <option value="fundamental">Fundamentally strong (quality)</option><option value="dividend">Dividend yield</option>
      <option value="momentum">Momentum (6-month)</option><option value="lowbeta">Low beta (stable)</option></select>
      <span class="muted" id="tilthint"> — changing this re-ranks the picks instantly</span></div>`;
  for(const[name,m]of Object.entries(MOD)){h+=`<h3>${name} — <span class="muted">${m.note}</span></h3><table><tr><th>Sector</th><th>Weight</th><th>Amount</th><th></th></tr>`;
    for(const[sec,wt]of Object.entries(m.weights))
      h+=`<tr><td>${sec}</td><td>${wt}%</td><td>₹${fmt(cap*wt/100)}</td><td><button class="secbtn dl" data-sec="${sec}" style="padding:6px 12px;font-size:12px;margin:0">View strong picks ▸</button></td></tr>`;
    h+='</table>';}
  h+='<div id="secpicks" style="margin-top:8px"></div></section>';
  el('m-models').innerHTML=h;
  el('m-models').querySelectorAll('.secbtn').forEach(b=>b.onclick=()=>{curSec=b.dataset.sec;loadSectorPicks(curSec);});
  el('tilt').onchange=()=>{if(curSec)loadSectorPicks(curSec);};}
async function loadSectorPicks(sec){const tilt=el('tilt')?el('tilt').value:'fundamental';
  const box=el('secpicks');box.innerHTML=`<div class="spin"></div>`;
  const d=await(await fetch(`/sector_top?country=India&sector=${encodeURIComponent(sec)}&tilt=${tilt}`)).json();
  if(d.error){box.innerHTML=`<p class="muted">${esc(d.error)}</p>`;return;}
  const tlabel={fundamental:'fundamental quality',dividend:'dividend yield',momentum:'6-month momentum',lowbeta:'low beta'}[tilt]||tilt;
  let t=`<h3>${esc(sec)} — ranked by ${tlabel}</h3><table><tr><th>Company</th><th>Quality/10</th><th>P/E</th><th>ROE%</th><th>Div%</th><th>6m%</th><th>Beta</th><th>Tags</th></tr>`;
  d.rows.forEach(r=>t+=`<tr><td>${esc(r.name||r.ticker)} <span class="muted">${r.ticker.replace('.NS','')}</span></td><td>${r.quality??'—'}</td><td>${fmt(r.pe)}</td><td>${fmt(r.roe)}</td><td>${fmt(r.div)}</td><td>${fmt(r.momentum_6m)}</td><td>${fmt(r.beta)}</td><td>${(r.tags||[]).map(x=>`<span class="tag">${esc(x)}</span>`).join('')}</td></tr>`);
  box.innerHTML=t+'</table><p class="muted">Quality = blend of ROE, margin, low debt, valuation, free cash flow. Change the “Rank by” dropdown above to re-rank instantly.</p>';}

// ---- live portfolio tracker ----
const PKEY='stocklens_portfolio';
function loadPort(){try{return JSON.parse(localStorage.getItem(PKEY))||[]}catch(e){return[]}}
function savePort(p){localStorage.setItem(PKEY,JSON.stringify(p))}
let trackTimer;
const CKEY='stocklens_capital';
function renderTracker(){const p=loadPort();
  let h=`<section class="glass"><h2>📈 My portfolio — live</h2>
    <div class="panel"><div class="ac"><label>Ticker</label><input id="t-sym" placeholder="Type e.g. HAL, Apple…" autocomplete="off"><div class="acbox" id="t-acbox"></div></div>
    <div><label>Qty</label><input id="t-qty" type="number" min="0"></div><div><label>Buy price (avg)</label><input id="t-buy" type="number" min="0"></div>
    <button id="t-add">Add holding</button></div>
    <div class="panel" style="margin-top:10px"><div><label>Total capital (₹) <span class="tip" data-tip="Your overall investable corpus. Used as the base for position-sizing suggestions.">ⓘ</span></label><input id="t-cap" type="number" min="0" value="${localStorage.getItem(CKEY)||200000}"></div>
    <div><label>Extra cash to deploy (₹) <span class="tip" data-tip="New money you want to add right now. The rebalancer routes it to the strongest / below-buy names.">ⓘ</span></label><input id="t-extra" type="number" min="0" value="0"></div>
    <div><label>Investment horizon <span class="tip" data-tip="How long you plan to hold. Sets the SL/TP window and how patiently weak names are treated.">ⓘ</span></label><select id="t-hz"><option value="short">Short (≤3y)</option><option value="medium" selected>Medium (3–7y)</option><option value="long">Long (7y+)</option></select></div></div>
    <div style="margin:10px 0"><button id="t-eval" class="full" style="margin-bottom:8px">🎯 Evaluate &amp; rebalance my portfolio</button>
    <button id="t-opt" style="background:#0e1422;color:var(--acc);border:1px solid var(--line)">🧮 Quant analytics</button>
    <button id="t-csv" style="background:#0e1422;color:var(--acc);border:1px solid var(--line)">⬇️ Export CSV</button>
    <button id="t-refresh" style="background:#0e1422;color:var(--acc);border:1px solid var(--line)">↻ Refresh now</button></div>
    <div class="muted" style="font-size:12px;margin-bottom:8px">
      <b>🎯 Evaluate &amp; rebalance</b>: rates every holding (fundamentals + technicals), final verdict, SL/TP, what to buy/sell/switch. ·
      <b>🧮 Quant analytics</b>: Sharpe, VaR, Monte-Carlo, efficient frontier, correlation. ·
      <b>⬇️ Export CSV</b>: download your holdings + P&L. ·
      <b>↻ Refresh</b>: re-pull live prices (auto every 60s).</div>
    <div id="t-summary"></div>
    <div id="t-table"><div class="muted">No holdings yet — add one above.</div></div>
    <div class="grid two" style="margin-top:8px"><div><canvas id="t-alloc" height="150"></canvas><div class="muted" style="text-align:center">Allocation by value</div></div><div id="t-movers"></div></div></section>
    <div id="t-eval-out"></div><div id="t-opt-out"></div><div id="t-news"></div>`;
  el('m-track').innerHTML=h;
  acWire('t-sym','t-acbox',sym=>{el('t-sym').value=sym;});
  el('t-cap').onchange=()=>localStorage.setItem(CKEY,el('t-cap').value);
  el('t-add').onclick=()=>{const s=el('t-sym').value.trim().toUpperCase();if(!s)return;
    p.push({sym:s,qty:+el('t-qty').value||0,buy:+el('t-buy').value||0});savePort(p);renderTracker();};
  el('t-refresh').onclick=refreshPort;
  el('t-opt').onclick=optimizePort;
  el('t-eval').onclick=evalPort;
  el('t-csv').onclick=()=>{const p2=loadPort();if(!p2.length){alert('No holdings.');return;}
    const Q=window.LASTQ||{};const rows=[['FundaPilot — portfolio'],[],['Ticker','Qty','Avg buy','Now','Day%','P&L','Since buy%']];
    p2.forEach(hh=>{const x=Q[hh.sym];const now=x?x.price:'';const day=(x&&x.prev)?((x.price-x.prev)/x.prev*100).toFixed(2):'';
      const pl=x?Math.round((x.price-hh.buy)*hh.qty):'';const since=(x&&hh.buy)?((x.price/hh.buy-1)*100).toFixed(1):'';
      rows.push([hh.sym,hh.qty,hh.buy,now,day,pl,since]);});
    dl('my_portfolio.csv',rows);};
  clearInterval(trackTimer);if(p.length){refreshPort();loadPortNews();trackTimer=setInterval(refreshPort,60000);}}

async function evalPort(){const p=loadPort();if(p.length<1){el('t-eval-out').innerHTML='<section class="glass">Add holdings first.</section>';return;}
  el('t-eval-out').innerHTML='<div class="spin"></div><p class="muted" style="text-align:center">Evaluating each holding (fundamentals + technicals) — this takes ~20–40s.</p>';
  const d=await(await fetch('/portfolio_eval',{method:'POST',headers:{'content-type':'application/json'},
    body:JSON.stringify({holdings:p,horizon:el('t-hz').value,extra_capital:+el('t-extra').value||0})})).json();
  if(d.error){el('t-eval-out').innerHTML=`<section class="glass">❌ ${d.error}</section>`;return;}
  renderEval(d);}
function vcl(v){return (v==='Accumulate')?'pos':(v==='Exit'||v==='Reduce')?'neg':'';}
function renderEval(d){const o=el('t-eval-out');o.innerHTML='';
  const oc=d.overall_health>=6.5?'v-under':d.overall_health>=5?'v-fair':'v-over';
  o.append($(`<section class="glass"><h2>🎯 Portfolio evaluation</h2>
    <div class="grid cards"><div class="chip">Portfolio value<b>₹${fmt(d.total_value)}</b></div>
    <div class="chip">Overall health<b>${d.overall_health}/10</b></div>
    <div class="chip">Verdict<b><span class="verdict ${oc}">${d.overall_verdict}</span></b></div>
    <div class="chip">Blended earnings CAGR<b>${d.portfolio_earnings_cagr==null?'—':d.portfolio_earnings_cagr+'%'}</b><span class="muted">vs NIFTY ~12%</span></div></div></section>`));
  // decisive final verdict (headline + numbered actions with the WHY/HOW for each)
  if(d.final_verdict){const fvh=esc(d.final_verdict.headline).replace(/\*\*(.+?)\*\*/g,'<b>$1</b>');
    o.append($(`<section class="glass"><h2>🧠 If I were you — final verdict</h2>
      <div class="rec" style="font-size:15px;line-height:1.6">${fvh}</div>
      <h3>Step by step — what I'd do &amp; why</h3>
      <div>${d.final_verdict.actions.map((a,i)=>`<div class="flag ${/sell|trim|diversify|cut|research/i.test(a.do)?'f-bad':'f-good'}" style="margin:8px 0">
        <div><b>${i+1}. ${esc(a.do)}</b></div><div style="font-size:13px;margin-top:3px" class="muted">${esc(a.why)}</div></div>`).join('')}</div>
      <p class="muted">Each call blends fundamentals (60%) and technicals (40%). A loss-making company can't score "strong" no matter how the chart looks; if a metric is missing the stock is left <b>Not rated</b> rather than guessed.</p></section>`));}
  // 🧠 AI take on the whole portfolio (on demand)
  if(window.AI_ON){
    o.append($(`<section class="glass"><h2>🧠 AI take on my portfolio</h2>
      <button class="dl" id="ai-pf">🧠 Ask the AI to review my portfolio</button>
      <div id="ai-pf-out" style="margin-top:10px"></div></section>`));
    const pctx={overall_health:d.overall_health,overall_verdict:d.overall_verdict,total_value:d.total_value,
      concentration_pct:d.concentration_pct,sectors:d.sectors,sector_rotation:(d.sector_rotation||[]).slice(0,4),
      holdings:(d.holdings||[]).map(e=>({sym:e.sym,health:e.health,verdict:e.verdict,fund:e.fund_score,tech:e.tech_score,pnl_pct:e.pnl_pct,weight_pct:e.weight_pct})),
      blended_cagr:d.portfolio_earnings_cagr};
    el('ai-pf').onclick=async()=>{const b=el('ai-pf-out');b.innerHTML='<div class="spin"></div><p class="muted" style="text-align:center">Thinking…</p>';
      try{const r=await(await fetch('/ai_analyst',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({mode:'portfolio',context:pctx})})).json();
        b.innerHTML=r.text?`<div class="rec" style="white-space:pre-wrap;line-height:1.55">${esc(r.text)}</div><p class="muted">${AI_DISCLAIMER}</p>`:`<p class="muted">${esc(r.note||'No response.')}</p>`;
      }catch(e){b.innerHTML='<p class="muted">AI call failed.</p>';}};
  }
  // how to rebalance — explicit steps + the situational plan
  o.append($(`<section class="glass"><h2>🔁 How to rebalance</h2>
    <ol style="line-height:1.7;padding-left:20px">${(d.steps||[]).map(s=>`<li>${s}</li>`).join('')}</ol>
    <h3>Right now</h3>${d.plan.map(x=>`<div class="flag ${x.indexOf('⚠')>-1?'f-bad':'f-good'}">${x}</div>`).join('')}</section>`));
  // per-stock: separate fundamentals + technicals sections
  let h='<section class="glass"><h2>🔬 Holdings evaluated</h2>';
  d.holdings.forEach((e,i)=>{const sym=e.sym.replace('.NS','').replace('.BO','');
    const vcls2=e.verdict==='Accumulate'?'v-under':(e.verdict==='Exit'||e.verdict==='Reduce')?'v-over':'v-fair';
    h+=`<div class="chip" style="margin-bottom:12px;background:#0c1426">
      <div style="display:flex;justify-content:space-between;flex-wrap:wrap;align-items:center">
        <b style="font-size:16px">${esc(sym)} <span class="muted" style="font-weight:400">${e.weight_pct}% of book</span></b>
        <span>Health <b>${e.health==null?'—':e.health+'/10'}</b> · <span class="verdict ${vcls2}">${esc(e.verdict)}</span> · P&L <span class="${e.pnl_pct>=0?'pos':'neg'}">${e.pnl_pct==null?'—':(e.pnl_pct>=0?'+':'')+e.pnl_pct+'%'}</span></span></div>
      ${e.fund_note?`<div class="muted" style="margin-top:4px">⚠️ ${esc(e.fund_note)}${e.profitable===false?' (loss-making)':''}</div>`:''}
      <div class="grid two" style="margin-top:10px">
        <div><h3 style="margin-top:0">Fundamentals — ${e.fund_score==null?'not rated':e.fund_score+'/10'}</h3>
          <div class="kv">P/E: <b>${fmt(e.pe)}</b> · P/B: <b>${fmt(e.pb)}</b></div>
          <div class="kv">ROE: <b>${e.roe_pct==null?'—':e.roe_pct+'%'}</b> · ROCE: <b>${e.roce_pct==null?'—':e.roce_pct+'%'}</b></div>
          <div class="kv">Net margin: <b>${e.net_margin_pct==null?'—':e.net_margin_pct+'%'}</b> · EV/EBITDA: <b>${fmt(e.ev_ebitda)}</b></div>
          <div class="kv">Earnings growth (1y): <b>${e.earnings_growth_pct==null?'—':e.earnings_growth_pct+'%'}</b> · CAGR 3y: <b>${e.earnings_cagr_3y==null?'—':e.earnings_cagr_3y+'%'}</b></div>
          <div class="kv">Debt/Equity: <b>${e.de==null?'—':e.de+'%'}</b></div></div>
        <div><h3 style="margin-top:0">Technicals — ${e.tech_score}/10</h3>
          <div class="kv">Trend: <b>${e.above_200dma?'▲ above 200-DMA':'▼ below 200-DMA'}${e.sma_uptrend?' · 50&gt;200':''}</b></div>
          <div class="kv">RSI(14): <b>${e.rsi}</b> <span class="muted">${e.rsi<30?'oversold':e.rsi>70?'overbought':'neutral'}</span></div>
          <div class="kv">Relative strength (6m): <b class="${(e.rel_strength_6m||0)>=0?'pos':'neg'}">${e.rel_strength_6m==null?'—':(e.rel_strength_6m>=0?'+':'')+e.rel_strength_6m+'%'}</b> <span class="muted">vs index</span></div>
          <div class="kv">ATR(14): <b>${fmt(e.atr)}</b> · 1σ move/horizon ±${e.expected_move_pct}%</div></div></div>
      <div class="grid two" style="margin-top:6px">
        <div class="kv">From <b>current ₹${fmt(e.price)}</b>: SL <span class="neg">₹${fmt(e.sl)} (${e.sl_pct}%)</span> · TP <span class="pos">₹${fmt(e.tp)} (+${e.tp_pct}%)</span></div>
        <div class="kv">From <b>your avg ₹${fmt(e.buy)}</b>: SL <span class="neg">₹${fmt(e.sl_buy)}</span> · TP <span class="pos">₹${fmt(e.tp_buy)}</span></div></div>
      <div id="peer-${i}" style="margin-top:8px"><span class="muted">Loading sector peers…</span></div>
    </div>`;});
  o.append($(h+'<p class="muted">Each holding scored on its own: fundamentals (60%) + technicals (40%). A loss-making name is capped (can\'t be "strong"); if a core metric is missing it\'s left <b>not rated</b> rather than guessed. SL/TP from ATR(14), shown from current price AND your average buy.</p></section>'));
  // per-holding peer comparison loaded on demand (real statements, each sector fetched once)
  const peerCache={};
  d.holdings.forEach((e,i)=>{const sec=e.sector;if(!sec){el('peer-'+i).innerHTML='<span class="muted">No sector mapped.</span>';return;}
    loadPeerEval(sec,e.sym,e.fund_score,'peer-'+i,peerCache);});
  // sector mix + rotation + news
  let sc='<section class="glass"><h2>🧭 Sector mix &amp; rotation</h2><div class="grid two"><div><h3>Your sector weights</h3>';
  sc+=d.sectors.map(s=>`<div class="kv">${s.sector}: <b>${s.pct}%</b>${s.pct>30?' <span class="neg">(concentrated)</span>':''}</div>`).join('');
  sc+='</div><div><h3>1-month sector leaders</h3>'+d.sector_rotation.slice(0,6).map(r=>`<div class="kv">${r.sector}: ${r.ret_1m_pct>=0?'<span class="pos">+'+r.ret_1m_pct+'%</span>':'<span class="neg">'+r.ret_1m_pct+'%</span>'}</div>`).join('')+'</div></div>';
  sc+=`<p class="muted">Largest sector ${d.concentration_pct}% of the book; keep any one under ~30% and rotate trims toward the leaders above.</p>`;
  if(d.sector_news&&Object.keys(d.sector_news).length){sc+='<h3>What\'s moving your sectors (news that can affect your horizon)</h3>';
    for(const[sec,news]of Object.entries(d.sector_news)){sc+=`<div style="margin-bottom:8px"><b>${sec}</b>`;
      sc+=(news&&news.length)?news.map(n=>`<div class="flag ${n.tone=='neg'?'f-bad':n.tone=='pos'?'f-good':''}"><a href="${safeUrl(n.link)}" target="_blank" rel="noopener noreferrer">${esc(n.title)}</a> <small class="muted">${n.date}</small></div>`).join(''):'<div class="muted">No recent headlines.</div>';sc+='</div>';}}
  o.append($(sc+'</section>'));}

async function optimizePort(){const p=loadPort();if(p.length<1){el('t-opt-out').innerHTML='<section class="glass">Add holdings first.</section>';return;}
  el('t-opt-out').innerHTML='<div class="spin"></div>';
  const d=await(await fetch('/optimize',{method:'POST',headers:{'content-type':'application/json'},
    body:JSON.stringify({holdings:p,extra_capital:+el('t-extra').value||0})})).json();
  if(d.error){el('t-opt-out').innerHTML=`<section class="glass">❌ ${d.error}</section>`;return;}
  renderOptimize(d);}
function renderOptimize(d){const m=d.mpt,r=d.risk,bm=d.benchmarks,o=el('t-opt-out');o.innerHTML='';
  const hint=t=>t?`<span class="muted" style="display:block;font-size:11px">${t}</span>`:'';
  // dividend income
  const inc=d.income;
  o.append($(`<section class="glass"><h2>💵 Dividend income from this portfolio</h2>
    <div class="grid cards"><div class="chip">Est. annual dividend<b>₹${fmt(inc.annual_dividend)}</b></div>
    <div class="chip">Portfolio yield<b>${inc.portfolio_yield_pct}%</b>${hint(bm.dividend_yield)}</div>
    <div class="chip">Avg / month<b>₹${fmt(inc.monthly_avg)}</b></div></div>
    <table style="margin-top:8px"><tr><th>Stock</th><th>Yield</th><th>Annual ₹</th></tr>
    ${d.per_stock.map(s=>`<tr><td>${s.sym.replace('.NS','')}</td><td>${s.div_yield_pct}%</td><td>₹${fmt(s.annual_dividend)}</td></tr>`).join('')}</table>
    <p class="muted">${inc.note}</p></section>`));
  o.append($(`<section class="glass"><h2>🧮 Portfolio optimization (Modern Portfolio Theory)</h2>
    <div class="grid cards"><div class="chip">Portfolio value<b>₹${fmt(d.value)}</b></div>
    <div class="chip">${tip('Expected return')}<b>${m.expected_return_pct}%</b><span class="muted">annual, historical</span></div>
    <div class="chip">${tip('CAGR')}<b>${m.cagr_pct}%</b>${hint(bm.cagr)}</div>
    <div class="chip">${tip('Volatility')}<b>${m.volatility_pct}%</b></div>
    <div class="chip">${tip('Sharpe ratio')}<b>${m.sharpe??'—'}</b>${hint(bm.sharpe)}</div>
    <div class="chip">${tip('Sortino ratio')}<b>${m.sortino??'—'}</b>${hint(bm.sortino)}</div>
    <div class="chip">${tip('Diversification')}<b>${m.diversification_score}/10</b><span class="muted">${m.effective_holdings} eff. holdings</span>${hint(bm.diversification)}</div></div>
    <p class="muted">${d.note}</p></section>`));
  o.append($(`<section class="glass"><h2>⚠️ Risk analytics</h2>
    <div class="grid cards"><div class="chip">${tip('Portfolio beta')}<b>${r.portfolio_beta}</b>${hint(bm.beta)}</div>
    <div class="chip"><span class="tip" data-tip="Return above what beta predicts (Jensen's alpha) vs the index — skill, not just market exposure.">Alpha (vs ${r.benchmark_name})</span><b>${r.alpha_pct}%</b>${hint(bm.alpha)}</div>
    <div class="chip">${tip('1-day VaR (95%)')}<b>₹${fmt(r.var_1d_95_amount)}</b><span class="muted">${r.var_1d_95_pct}%</span>${hint(bm.var)}</div>
    <div class="chip">${tip('CVaR / Exp. Shortfall')}<b>₹${fmt(r.cvar_1d_95_amount)}</b><span class="muted">${r.cvar_1d_95_pct}%</span>${hint(bm.cvar)}</div>
    <div class="chip">${tip('Max drawdown')}<b>${r.max_drawdown_pct}%</b>${hint(bm.max_drawdown)}</div>
    <div class="chip">Sector concentration<b>${r.sector_concentration_pct}%</b>${hint(bm.sector_concentration)}</div></div>
    <h3>Per-stock risk</h3><table><tr><th>Stock</th><th>Weight</th><th>Beta</th><th>Volatility</th><th>Downside dev</th><th>Max DD</th><th>Exp ret</th></tr>
    ${d.per_stock.map(s=>`<tr><td>${s.sym.replace('.NS','')}</td><td>${s.weight_pct}%</td><td>${s.beta??'—'}</td><td>${s.vol_pct}%</td><td>${s.downside_dev_pct??'—'}%</td><td>${s.max_drawdown_pct}%</td><td>${s.exp_return_pct}%</td></tr>`).join('')}</table>
    <h3>Sector concentration</h3>${r.top_sectors.map(s=>`<div class="kv">${s.sector}: <b>${s.pct}%</b></div>`).join('')}
    ${window.AI_ON?`<hr style="border:0;border-top:1px solid var(--line);margin:12px 0"><button class="dl" id="ai-risk" style="margin:0">🧠 AI risk briefing (plain English)</button><div id="ai-risk-out" style="margin-top:8px"></div>`:''}</section>`));
  if(window.AI_ON&&el('ai-risk')){const rctx={mpt:d.mpt,risk:d.risk,factor_tilt:d.factor_tilt,value:d.value,monte_carlo:d.monte_carlo};
    el('ai-risk').onclick=()=>aiPost('/ai_analyst',{mode:'risk',context:rctx},'ai-risk-out','Briefing the risk…');}
  // backtest vs benchmark
  o.append($(`<section class="glass"><h2>📈 Backtest — portfolio vs ${r.benchmark_name} <span class="muted">(growth of ₹1, 2y)</span></h2>
    <canvas id="cBT" height="130"></canvas><p class="muted">Benchmark annual return ${r.benchmark_return_pct}%. Past performance ≠ future results.</p></section>`));
  drawBT('cBT',d.backtest);
  // correlation matrix
  const co=d.correlation;
  let cm='<section class="glass"><h2>🔗 Correlation matrix <span class="muted">(lower = better diversified)</span></h2><table><tr><th></th>'+co.order.map(s=>`<th>${s.replace('.NS','')}</th>`).join('')+'</tr>';
  co.order.forEach(a=>{cm+=`<tr><td>${a.replace('.NS','')}</td>`+co.order.map(b=>{const v=co.matrix[a][b];const g=v>0.7?'#ff6b6b':v>0.4?'#ffd166':'#39d98a';return `<td style="color:${g}">${v}</td>`}).join('')+'</tr>';});
  o.append($(cm+'</table><p class="muted">🟢 low (<0.4) diversifies well · 🟡 moderate · 🔴 high (>0.7) move together.</p></section>'));
  const f=d.factor_tilt;
  o.append($(`<section class="glass"><h2>🧬 Factor exposure (approximate)</h2>
    <div class="grid cards"><div class="chip">Size<b style="font-size:15px">${f.size}</b></div>
    <div class="chip">Value/Growth<b style="font-size:15px">${f.value_growth}</b></div>
    <div class="chip">Quality<b style="font-size:15px">${f.quality}</b></div>
    <div class="chip">Volatility<b style="font-size:15px">${f.volatility}</b></div></div>
    <p class="muted">Weighted P/E ${f.wavg_pe??'—'}, P/B ${f.wavg_pb??'—'}. Descriptive tilt from holdings, not a regression factor model.</p></section>`));
  // stress test
  let st='<section class="glass"><h2>🌪️ Stress test — "what if the market falls?"</h2><div class="grid cards">';
  for(const[k,v]of Object.entries(d.stress_test))st+=`<div class="chip">${k}<b class="neg">₹${fmt(Math.abs(v.expected_loss))} loss</b><span class="muted">value → ₹${fmt(v.value_after)}</span></div>`;
  o.append($(st+'</div><p class="muted">Expected loss = portfolio beta × index move × value. A 15% NIFTY fall hits a high-beta book harder.</p></section>'));
  // monte carlo + efficient frontier charts
  const mc=d.monte_carlo;
  o.append($(`<section class="glass"><h2>🎲 Monte Carlo (1-year, 2000 sims) &amp; efficient frontier</h2>
    <div class="grid cards"><div class="chip">Worst 5% (p5)<b>₹${fmt(mc.p5)}</b></div><div class="chip">Median (p50)<b>₹${fmt(mc.p50)}</b></div>
    <div class="chip">Best 5% (p95)<b>₹${fmt(mc.p95)}</b></div><div class="chip">Chance of loss<b>${mc.prob_loss_pct}%</b></div></div>
    <div class="grid two" style="margin-top:12px"><div><canvas id="cMC" height="170"></canvas><div class="muted" style="text-align:center">Simulated 1y outcomes</div></div>
    <div><canvas id="cEF" height="170"></canvas><div class="muted" style="text-align:center">Efficient frontier (risk vs return)</div></div></div></section>`));
  drawMC('cMC',mc,d.value);drawEF('cEF',d.efficient_frontier);
  // rebalancing
  const ef=d.efficient_frontier.max_sharpe;
  let rb=`<section class="glass"><h2>♻️ Rebalancing (quant — max-Sharpe target)</h2>
    <p class="muted">This is the <b>mathematical</b> rebalance: it shifts weights toward the <b>max-Sharpe portfolio</b> — the mix with the best historical return per unit of risk (target: ${ef.ret}% return, ${ef.vol}% vol, Sharpe ${ef.sharpe}). Trades use ₹${fmt(d.value+(d.extra_capital||0))} (value${d.extra_capital?' + ₹'+fmt(d.extra_capital)+' extra':''}). For the <b>fundamentals-driven</b> "what to sell/add and why", see the 🎯 Evaluate &amp; rebalance section.</p>
    <table><tr><th>Stock</th><th>Current</th><th>Target</th><th>Action</th><th>Amount</th></tr>`;
  d.rebalance.forEach(x=>rb+=`<tr><td>${x.sym.replace('.NS','')}</td><td>${x.current_pct}%</td><td>${x.target_pct}%</td><td>${x.action==='Buy'?'<span class="pos">Buy</span>':'<span class="neg">Trim</span>'}</td><td>₹${fmt(x.amount)}</td></tr>`);
  o.append($(rb+`</table><table style="margin-top:10px"><tr><th>Benchmark</th><th>This portfolio</th><th>Healthy range</th></tr>
    <tr><td>Sharpe ratio</td><td>${m.sharpe??'—'}</td><td>&gt;1 good, &gt;2 excellent</td></tr>
    <tr><td>Volatility (annual)</td><td>${m.volatility_pct}%</td><td>lower is calmer; equity ~15–25%</td></tr>
    <tr><td>Max drawdown</td><td>${r.max_drawdown_pct}%</td><td>smaller is safer</td></tr>
    <tr><td>Diversification</td><td>${m.effective_holdings} eff. holdings</td><td>&gt;5 is well-spread</td></tr>
    <tr><td>Sector concentration</td><td>${r.sector_concentration_pct}%</td><td>keep any sector &lt;30%</td></tr></table>
    <p class="muted">Max-Sharpe weights are unconstrained (can concentrate). Treat as a direction, not a mandate; keep position limits.</p></section>`));}

async function loadPortNews(){const p=loadPort();if(!p.length){el('t-news').innerHTML='';return;}
  const news=await(await fetch('/portfolio_news?tickers='+encodeURIComponent(p.map(h=>h.sym).join(',')))).json();
  let h='<section class="glass"><h2>📰 Portfolio news (live)</h2><div class="news">';
  h+=Array.isArray(news)&&news.length?news.map(n=>`<div class="flag ${n.tone=='neg'?'f-bad':n.tone=='pos'?'f-good':''}"><span class="tag">${esc(n.ticker)}</span> <a href="${safeUrl(n.link)}" target="_blank" rel="noopener noreferrer">${esc(n.title)}</a> <small class="muted">${n.date}</small></div>`).join(''):'<div class="muted">No headlines.</div>';
  el('t-news').innerHTML=h+'</div><p class="muted">Live Google News for each holding (14d). Read the source.</p></section>';}
function drawMC(id,mc,value){const bins=[mc.p5,mc.p50,mc.p95];charts.push(new Chart(el(id),{type:'bar',
  data:{labels:['Worst 5%','Median','Best 5%'],datasets:[{label:'Ending value ₹',data:bins,backgroundColor:['#ff6b6b','#ffd166','#39d98a']}]},
  options:{plugins:{legend:{display:false}},scales:{x:{ticks:{color:'#8b97ad'}},y:{ticks:{color:'#8b97ad'}}}}}));}
function drawBT(id,bt){if(!bt)return;charts.push(new Chart(el(id),{type:'line',data:{labels:bt.dates,datasets:[
  {label:'Your portfolio',data:bt.portfolio,borderColor:'#39d98a',borderWidth:1.8,pointRadius:0,tension:.15},
  {label:bt.benchmark_name,data:bt.benchmark,borderColor:'#6ea8fe',borderWidth:1.4,pointRadius:0,borderDash:[5,4],tension:.15}]},
  options:{plugins:{legend:{labels:{color:'#8b97ad'}}},scales:{x:{ticks:{color:'#8b97ad',maxTicksLimit:6}},y:{ticks:{color:'#8b97ad'}}}}}));}
function drawEF(id,ef){const pts=ef.scatter.map(p=>({x:p.vol,y:p.ret}));
  charts.push(new Chart(el(id),{type:'scatter',data:{datasets:[
    {label:'Random portfolios',data:pts,backgroundColor:'rgba(110,168,254,.5)',pointRadius:2},
    {label:'Max Sharpe',data:[{x:ef.max_sharpe.vol,y:ef.max_sharpe.ret}],backgroundColor:'#39d98a',pointRadius:6},
    {label:'Min volatility',data:[{x:ef.min_vol.vol,y:ef.min_vol.ret}],backgroundColor:'#ffd166',pointRadius:6}]},
  options:{plugins:{legend:{labels:{color:'#8b97ad'}}},scales:{x:{title:{display:true,text:'Volatility %',color:'#8b97ad'},ticks:{color:'#8b97ad'}},y:{title:{display:true,text:'Return %',color:'#8b97ad'},ticks:{color:'#8b97ad'}}}}}));}
async function refreshPort(){const p=loadPort();if(!p.length){el('t-table').innerHTML='<div class="muted">No holdings yet.</div>';if(el('t-summary'))el('t-summary').innerHTML='';return;}
  const q=await(await fetch('/quote?tickers='+encodeURIComponent(p.map(h=>h.sym).join(',')))).json();
  window.LASTQ=q;
  let inv=0,curv=0,dayPL=0,prevVal=0;const rowsData=[];
  p.forEach((h,i)=>{const Q=q[h.sym];const price=Q?Q.price:null,prev=Q?Q.prev:null;
    const day=(price!=null&&prev)?((price-prev)/prev*100):null;
    const pl=price!=null?(price-h.buy)*h.qty:null,since=price!=null&&h.buy?((price-h.buy)/h.buy*100):null;
    const val=price!=null?price*h.qty:0;
    if(price!=null){inv+=h.buy*h.qty;curv+=val;if(prev){dayPL+=(price-prev)*h.qty;prevVal+=prev*h.qty;}}
    rowsData.push({h,i,price,day,pl,since,val});});
  savePort(p);
  const tot=curv-inv,totp=inv?tot/inv*100:0,dayp=prevVal?dayPL/prevVal*100:0;
  const c=v=>v==null?'—':(v>=0?'<span class="pos">+'+v.toFixed(2)+'</span>':'<span class="neg">'+v.toFixed(2)+'</span>');
  let t='<table><tr><th>Holding</th><th>Qty</th><th>Avg buy</th><th>Now</th><th>Day%</th><th>Wt%</th><th>P&L</th><th>Since buy%</th><th></th></tr>';
  rowsData.forEach(r=>{const wt=curv?(r.val/curv*100):0;
    t+=`<tr><td>${r.h.sym.replace('.NS','').replace('.BO','')}</td><td>${r.h.qty}</td><td>${fmt(r.h.buy)}</td><td>${r.price==null?'—':fmt(r.price)}</td><td>${c(r.day)}</td><td>${r.price==null?'—':wt.toFixed(1)+'%'}</td><td>${c(r.pl)}</td><td>${c(r.since)}</td><td><a href="#" data-i="${r.i}" class="rm">✕</a></td></tr>`;});
  el('t-table').innerHTML=t+'</table>';
  el('t-summary').innerHTML=`<div class="grid cards"><div class="chip">Invested<b>₹${fmt(inv)}</b></div><div class="chip">Current<b>₹${fmt(curv)}</b></div>
    <div class="chip">Today's P&L<b>${dayPL>=0?'<span class="pos">+':'<span class="neg">'}₹${fmt(Math.abs(dayPL))} (${dayp>=0?'+':''}${dayp.toFixed(2)}%)</span></b></div>
    <div class="chip">Total P&L<b>${tot>=0?'<span class="pos">+':'<span class="neg">'}₹${fmt(Math.abs(tot))} (${totp>=0?'+':''}${totp.toFixed(1)}%)</span></b></div></div>`;
  el('t-table').querySelectorAll('.rm').forEach(a=>a.onclick=e=>{e.preventDefault();const pp=loadPort();pp.splice(+a.dataset.i,1);savePort(pp);renderTracker();});
  // best/worst movers today
  const moved=rowsData.filter(r=>r.day!=null).sort((a,b)=>b.day-a.day);
  if(moved.length&&el('t-movers')){const top=moved[0],bot=moved[moved.length-1];
    el('t-movers').innerHTML=`<h3 style="margin-top:0">Today's movers</h3>
      <div class="flag f-good">▲ Best: <b>${top.h.sym.replace('.NS','')}</b> ${c(top.day)}%</div>
      <div class="flag f-bad">▼ Worst: <b>${bot.h.sym.replace('.NS','')}</b> ${c(bot.day)}%</div>
      <div class="muted">Since-buy leader: ${[...rowsData].filter(r=>r.since!=null).sort((a,b)=>b.since-a.since).map(r=>r.h.sym.replace('.NS','')+' '+(r.since>=0?'+':'')+r.since.toFixed(0)+'%').slice(0,1)[0]||'—'}</div>`;}
  // allocation doughnut by current value
  if(el('t-alloc')){const lbls=rowsData.filter(r=>r.val>0).map(r=>r.h.sym.replace('.NS','').replace('.BO',''));
    const vals=rowsData.filter(r=>r.val>0).map(r=>Math.round(r.val));
    if(window._allocChart)window._allocChart.destroy();
    if(vals.length)window._allocChart=new Chart(el('t-alloc'),{type:'doughnut',data:{labels:lbls,datasets:[{data:vals,backgroundColor:['#6ea8fe','#39d98a','#ffd166','#b39bff','#ff6b6b','#5ad1c8','#f08fc0','#9ec5ff','#c8a9ff','#ffb86b']}]},options:{plugins:{legend:{position:'right',labels:{color:'#8b97ad',boxWidth:10,font:{size:11}}}}}});}}

// ---- markets dashboard ----
async function renderMarkets(){el('m-markets').innerHTML='<div class="spin"></div>';
  const d=await(await fetch('/dashboard')).json();
  const chg=v=>v==null?'—':(v.change_pct>=0?`<span class="pos">${fmt(v.price)} (+${v.change_pct}%)</span>`:`<span class="neg">${fmt(v.price)} (${v.change_pct}%)</span>`);
  const grid=obj=>Object.entries(obj).map(([k,v])=>`<div class="chip">${k}<b style="font-size:16px">${chg(v)}</b></div>`).join('');
  let h=`<section class="glass"><h2>🌐 Market dashboard</h2><div class="grid cards">${grid(d.market)}</div>
    <h3>Commodities · rates · currency</h3><div class="grid cards">${grid(d.macro)}</div></section>`;
  h+=`<section class="glass"><h2>🔄 Sector rotation <span class="muted">(1-month return, leaders first)</span></h2><table><tr><th>Sector</th><th>1M return</th></tr>`;
  d.sector_rotation.forEach(s=>h+=`<tr><td>${s.sector}</td><td>${s.ret_1m_pct>=0?'<span class="pos">+'+s.ret_1m_pct+'%</span>':'<span class="neg">'+s.ret_1m_pct+'%</span>'}</td></tr>`);
  h+='</table></section>';
  h+='<section class="glass"><h2>🏦 US macro (FRED)</h2>';
  if(d.fred.enabled){h+='<div class="grid cards">'+Object.entries(d.fred.data).map(([k,v])=>`<div class="chip">${k}<b>${v.value}</b><span class="muted">${v.date}</span></div>`).join('')+'</div>';}
  else h+=`<p class="muted">${d.fred.note}</p>`;
  h+='</section><div class="disc">Indices/commodities live from Yahoo Finance. Refreshes when you open this tab.</div>';
  el('m-markets').innerHTML=h;}

// ---- watchlists ----
async function renderWatch(){const lists=['Buffett (quality)','High ROE (ROCE proxy)','Deep Value','Small-cap compounders'];
  el('m-watch').innerHTML=`<div id="ww-watch"></div><section class="glass"><h2>📚 Curated screens</h2><p class="muted">Pre-built pools screened live and ranked. Click one to load (takes a few seconds — it fetches fundamentals).</p>
    <div>${lists.map(n=>`<button class="wlbtn" data-n="${n}" style="margin:4px;background:#0e1422;color:var(--acc);border:1px solid var(--line)">${n}</button>`).join('')}</div><div id="wl-out"></div></section>`;
  renderMyWatch(el('ww-watch'),'ww_');
  el('m-watch').querySelectorAll('.wlbtn').forEach(b=>b.onclick=async()=>{el('wl-out').innerHTML='<div class="spin"></div>';
    const d=await(await fetch('/screen?type='+encodeURIComponent(b.dataset.n))).json();
    if(d.error){el('wl-out').innerHTML=`<p class="muted">${d.error}</p>`;return;}
    let t=`<h3>${d.name} — ranked by ${d.tilt}</h3><table><tr><th>Company</th><th>Quality/10</th><th>P/E</th><th>P/B</th><th>ROE%</th><th>Div%</th><th>6m%</th><th>Beta</th><th>Tags</th></tr>`;
    d.rows.forEach(r=>t+=`<tr><td>${r.name||r.ticker} <span class="muted">${r.ticker.replace('.NS','')}</span></td><td>${r.quality??'—'}</td><td>${fmt(r.pe)}</td><td>${fmt(r.pb)}</td><td>${fmt(r.roe)}</td><td>${fmt(r.div)}</td><td>${fmt(r.momentum_6m)}</td><td>${fmt(r.beta)}</td><td>${(r.tags||[]).map(x=>`<span class="tag">${x}</span>`).join('')}</td></tr>`);
    el('wl-out').innerHTML=t+'</table><p class="muted">"High ROE" stands in for ROCE (not in free Yahoo data).</p>';});}
</script></body></html>"""

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        _selftest()
    else:
        app.run(debug=False, port=5000)
