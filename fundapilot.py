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


# reputable business/markets outlets — surfaced first and tagged in the feed
NEWS_SOURCES = ("reuters", "bloomberg", "cnbc", "moneycontrol", "economic times", "livemint", "mint",
                "business standard", "financial times", "the hindu businessline", "forbes", "wsj", "wall street journal",
                "businessworld", "ndtv profit", "zee business", "et now")


def google_news(query, n=8):
    try:
        url = "https://news.google.com/rss/search?q=" + urllib.parse.quote(query) + "+when:21d&hl=en-IN&gl=IN&ceid=IN:en"
        raw = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=10).read()
        root = ET.fromstring(raw)
        items = []
        for it in list(root.iter("item")):
            title = (it.findtext("title") or "").strip()
            if not title:
                continue
            src = (it.findtext("{*}source") or it.findtext("source") or "").strip()
            # Google often appends " - Source" to the title; recover it if the source tag is empty
            if not src and " - " in title:
                src = title.rsplit(" - ", 1)[-1].strip()
                title = title.rsplit(" - ", 1)[0].strip()
            low = title.lower()
            tone = "neg" if any(w in low for w in NEG) else ("pos" if any(w in low for w in POS) else "neutral")
            reputable = any(s in (src or "").lower() for s in NEWS_SOURCES)
            items.append({"title": title, "link": it.findtext("link") or "", "date": (it.findtext("pubDate") or "")[:16],
                          "tone": tone, "source": src or "—", "reputable": reputable})
        items.sort(key=lambda x: 0 if x["reputable"] else 1)  # reputable outlets first
        return items[:n]
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
    # dividend yield from the dividends feed (works on cloud) when info lacks it. Inject into the
    # DECIMAL field (trailingAnnualDividendYield) — div_yield_dec returns it as-is (it divides the
    # separate `dividendYield` field by 100, so injecting there would be 100x too small).
    if _g(info, "dividendYield") is None and _g(info, "trailingAnnualDividendYield") is None and price:
        try:
            divs = t.dividends
            if divs is not None and len(divs):
                last12 = float(divs[divs.index >= (divs.index.max() - pd.Timedelta(days=365))].sum())
                if last12 > 0:
                    info["trailingAnnualDividendYield"] = last12 / price  # decimal fraction
        except Exception:
            pass
    return info


def business_model(fin):
    """Where each ₹100 of revenue goes — cost structure from the income statement (real, computed).
    NOT product/geography segment revenue (not available in free data)."""
    L = lambda s: float(s.iloc[0]) if s is not None and len(s) else None
    rev = L(_srow(fin, "Total Revenue", "Operating Revenue"))
    if not rev or rev <= 0:
        return None
    cogs = L(_srow(fin, "Cost Of Revenue"))
    opex = L(_srow(fin, "Operating Expense", "Operating Expenses"))
    if opex is None:
        sga, rnd = L(_srow(fin, "Selling General And Administration")), L(_srow(fin, "Research And Development"))
        opex = (sga or 0) + (rnd or 0) if (sga or rnd) else None
    tax = L(_srow(fin, "Tax Provision", "Income Tax Expense"))
    interest = L(_srow(fin, "Interest Expense"))
    net = L(_srow(fin, "Net Income", "Net Income Common Stockholders"))
    slices, used = [], 0.0
    for label, val in [("Cost of goods/services", cogs), ("Operating expenses", opex),
                       ("Interest", interest), ("Tax", tax)]:
        if val and val > 0:
            slices.append({"label": label, "pct": round(val / rev * 100, 1)}); used += val
    if net is not None:
        slices.append({"label": "Net profit" if net >= 0 else "Net loss", "pct": round(net / rev * 100, 1)}); used += net
    other = rev - used
    if other / rev > 0.03:
        slices.append({"label": "Other / adjustments", "pct": round(other / rev * 100, 1)})
    if len(slices) < 2:
        return None
    return {"revenue": rev, "slices": slices,
            "net_margin_pct": round(net / rev * 100, 1) if net is not None else None,
            "note": "How each ₹100 of revenue is split (cost structure from the income statement). Product/geography segment revenue isn't in free data — see the annual report or ask the AI."}


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
        s = get_info(sym).get("sector")
        if s:
            return s
    except Exception:
        pass
    # fallback: curated-universe sector (sector metadata is in Yahoo's info feed, which is blocked
    # on cloud IPs — this keeps sector grouping working for known tickers).
    for cc, secs in UNIVERSE.items():
        if cc.startswith("Categories"):
            continue
        for sec, lst in secs.items():
            if sym in lst:
                return sec
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
            infos[s] = enriched_info(s)
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
            inf = enriched_info(s)
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
            "business_model": business_model(fin),
            "officers": [{"name": o.get("name"), "title": o.get("title"), "age": o.get("age"), "pay": o.get("totalPay")}
                         for o in (_g(info, "companyOfficers") or [])[:6] if o.get("name")],
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
    out["sector"] = _g(info, "sector") or stock_sector(sym)
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
            plan.append(f"You have ₹{extra_capital:,.0f} to deploy → add ≈₹{per:,} to each of: " +
                        ", ".join(t["sym"].replace(".NS", "") for t in targets) + ".")
        under = [e for e in valid if e.get("pnl_pct") is not None and e["pnl_pct"] < 0 and e["verdict"] == "Accumulate"]
        if under:
            plan.append("Average down (thesis intact but price is below your buy): " +
                        ", ".join(f"{e['sym'].replace('.NS','')} ({e['pnl_pct']}%)" for e in under) + ".")
    else:
        if to_trim:
            plan.append("No fresh cash → rebalance from within: trim/exit " +
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
    return Response(_load_html().replace("<!--SBCFG-->", cfg), mimetype="text/html")


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
    elif mode == "mda":
        user = ("Write a concise MANAGEMENT DISCUSSION & ANALYSIS for this company from the data below, so the reader "
                "needn't open the full annual report.\n\nDATA:\n" + ctx +
                "\n\nRespond (plain text, with these headers):\nBUSINESS & HOW IT MAKES MONEY: 2–3 lines\n"
                "MANAGEMENT & TRACK RECORD: who runs it and how they've executed (from the multi-year trend + well-known facts)\n"
                "PERFORMANCE REVIEW: what the recent revenue/profit/margins/CAGR say\nOUTLOOK & KEY RISKS: where management is steering and what could derail it\n"
                "Base it on the provided data + well-established facts; flag uncertainty; do NOT invent specific numbers.")
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
    try:
        holdings = [{"sym": clean_ticker(h.get("sym")), "qty": float(h.get("qty") or 0), "buy": float(h.get("buy") or 0)}
                    for h in d.get("holdings", []) if h.get("sym")]
        extra = float(d.get("extra_capital") or 0)
    except (ValueError, TypeError):
        return jresp({"error": "Quantity, buy price and extra capital must be numbers."}, 400)
    if not holdings:
        return jresp({"error": "Add at least one holding."}, 400)
    try:
        return jresp(portfolio_analytics(holdings, extra))
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
    try:
        holdings = [{"sym": clean_ticker(h.get("sym")), "qty": float(h.get("qty") or 0), "buy": float(h.get("buy") or 0)}
                    for h in d.get("holdings", []) if h.get("sym")]
        extra = float(d.get("extra_capital") or 0)
    except (ValueError, TypeError):
        return jresp({"error": "Quantity, buy price and extra capital must be numbers."}, 400)
    if not holdings:
        return jresp({"error": "Add at least one holding."}, 400)
    try:
        return jresp(portfolio_eval(holdings, d.get("horizon", "medium"), extra))
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
    # validate the inlined browser JS — a single JS syntax error breaks the whole UI.
    # Uses node if available; skips gracefully otherwise.
    import shutil, subprocess
    if shutil.which("node"):
        r = subprocess.run(["node", "--check", os.path.join(_UI_DIR, "static", "app.js")],
                           capture_output=True, text=True)
        assert r.returncode == 0, "static/app.js has a syntax error:\n" + (r.stderr[:600])
        print("app JS OK")
    else:
        print("(node not installed — JS check skipped)")
    print("selftest OK")


# ---------------------------- UI (templates/ + static/) ----------------------------
# The UI was split out of the old inline HTML string so it can be edited with normal
# front-end tools. templates/index.html links /static/style.css and /static/app.js.
# Element ids / data-* attributes are load-bearing (app.js targets them) - restyle freely,
# but do not rename or remove them.
_UI_DIR = os.path.dirname(os.path.abspath(__file__))


def _load_html():
    with open(os.path.join(_UI_DIR, "templates", "index.html"), encoding="utf-8") as f:
        return f.read()

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        _selftest()
    else:
        app.run(debug=False, port=5000)
