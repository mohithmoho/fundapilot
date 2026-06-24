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
import math
import json
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from functools import lru_cache
import requests
import numpy as np
import pandas as pd
import yfinance as yf
from flask import Flask, request, Response

app = Flask(__name__)
UA = {"User-Agent": "Mozilla/5.0 (FundaPilot educational)"}

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
    # ponytail: recent yfinance returns dividendYield in PERCENT (e.g. 4.95), older in decimal.
    # Normalize to a decimal fraction; >1 means it's percent units. Fall back to trailing (always decimal).
    dy = _g(info, "dividendYield")
    if dy is not None:
        return dy / 100 if dy > 1 else dy
    return _g(info, "trailingAnnualDividendYield")


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


def fetch(ticker, years):
    t = yf.Ticker(ticker)
    info, notes = {}, []
    try:
        info = get_info(ticker)
    except Exception as e:
        notes.append(f"info: {e}")
    cur_code = "INR" if ticker.endswith((".NS", ".BO")) else (_g(info, "currency") or "USD")
    sym = {"INR": "₹", "USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥"}.get(cur_code, cur_code + " ")
    price = _g(info, "currentPrice", "regularMarketPrice", "previousClose")

    hist = {}
    try:
        fin = t.financials
        if fin is not None and not fin.empty:
            cols = list(fin.columns)[:years]
            for label, key in [("Revenue", "Total Revenue"), ("EBITDA", "EBITDA"), ("Net Income", "Net Income")]:
                if key in fin.index:
                    hist[label] = {str(c.date()): (None if pd.isna(v) else float(v)) for c, v in fin.loc[key, cols].items()}
    except Exception as e:
        notes.append(f"statements: {e}")

    tech = {}
    for tf, period, interval in [("daily", "2y", "1d"), ("weekly", "5y", "1wk")]:
        try:
            px = t.history(period=period, interval=interval)["Close"].dropna()
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

    return t, info, cur_code, sym, price, hist, tech, inst_pct, holders, corporate_actions(t), notes


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
        i = get_info(s)
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


def analyze(ticker, horizon, risk, capital, years, style):
    t, info, cur_code, sym, price, hist, tech, inst_pct, holders, actions, notes = fetch(ticker, years)
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

    fcf, fcf_src, fcf_detail = derive_fcf(info, t)
    g = max(0.03, min(0.18, (earn_g or rev_g or 0.08)))
    fair = dcf(fcf, g, rp["discount"], proj_years)
    implied_g = reverse_dcf(fcf, mktcap, rp["discount"], proj_years)
    verdict, mos = "Insufficient data", None
    if fair and mktcap:
        mos = round((fair / mktcap - 1) * 100, 1)
        verdict = "Undervalued" if mos > 20 else ("Overvalued" if mos < -20 else "Fairly valued")

    scores = {"Valuation": score(mos, 30, -30) if mos is not None else None,
              "Profitability": score((roe or 0) * 100, 22, 5) if roe is not None else None,
              "Growth": score((earn_g or rev_g or 0) * 100, 20, 0) if (earn_g or rev_g) is not None else None,
              "Financial health": score(de, 30, 150) if de is not None else None,
              "Momentum": _momentum(tech),
              "Valuation (P/E)": score(pe, 12, 45) if pe else None}
    valid = {k: v for k, v in scores.items() if v is not None}
    overall = round(sum(valid.values()) / len(valid), 1) if valid else None
    w = STYLE_WEIGHTS.get(style, STYLE_WEIGHTS["balanced"])
    wsum = sum(w[k] for k in valid)
    style_score = round(sum(valid[k] * w[k] for k in valid) / wsum, 1) if wsum else overall
    style_take = "Strong fit" if (style_score or 0) >= 7 else ("Moderate fit" if (style_score or 0) >= 5 else "Weak fit")

    mktcap_inr = mktcap * fx if (mktcap and fx) else None
    cap_cat = cap_category(mktcap_inr)
    tags = [c for c in [cap_cat,
                        ("Dividend payer" if (div_y and div_y > 0.015) else None),
                        ("Aggressive (high beta)" if (beta and beta > 1.3) else ("Defensive (low beta)" if (beta and beta < 0.8) else None))] if c]

    # ratios with units, benchmark, INR conversion
    raw = {"P/E (trailing)": pe, "P/E (forward)": fpe, "P/B": pb, "PEG": peg, "ROE %": _pct(roe),
           "Operating margin %": _pct(opm), "Net/PAT margin %": _pct(npm), "Debt/Equity %": de,
           "Current ratio": cur_ratio, "Revenue growth %": _pct(rev_g), "Earnings growth %": _pct(earn_g),
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
                          "verdict": verdict, "growth_used_pct": round(g * 100, 1), "discount_pct": round(rp["discount"] * 100, 1),
                          "implied_growth_pct": implied_g, "proj_years": proj_years, "fcf": fcf, "fcf_source": fcf_src},
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


# ---------------------------- routes ----------------------------
@app.route("/")
def index():
    return Response(HTML, mimetype="text/html")


@app.route("/universe")
def universe():
    return jresp({"universe": UNIVERSE, "models": MODELS})


@app.route("/search")
def search():
    q = (request.args.get("q") or "").strip()
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
    syms = [s for s in (request.args.get("tickers") or "").split(",") if s][:7]
    rows = []
    for s in syms:
        i = get_info(s)
        rows.append({"ticker": s, "name": _g(i, "shortName", "longName") or s, "pe": _g(i, "trailingPE"),
                     "pb": _g(i, "priceToBook"), "roe": _pct(_g(i, "returnOnEquity")), "margin": _pct(_g(i, "profitMargins")),
                     "rev_growth": _pct(_g(i, "revenueGrowth")), "marketCap": _g(i, "marketCap")})
    return jresp(rows)


@app.route("/quote")
def quote():
    syms = [s for s in (request.args.get("tickers") or "").split(",") if s][:30]
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
    holdings = [{"sym": (h.get("sym") or "").strip().upper(), "qty": float(h.get("qty") or 0), "buy": float(h.get("buy") or 0)}
                for h in d.get("holdings", []) if h.get("sym")]
    if not holdings:
        return jresp({"error": "Add at least one holding."}, 400)
    try:
        return jresp(portfolio_analytics(holdings, float(d.get("extra_capital") or 0)))
    except Exception as e:
        return jresp({"error": f"Optimization failed: {e}"}, 500)


@app.route("/portfolio_news")
def portfolio_news():
    syms = [s for s in (request.args.get("tickers") or "").split(",") if s][:8]
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


@app.route("/analyze", methods=["POST"])
def do_analyze():
    d = request.get_json(force=True)
    market = d.get("market", "NSE")
    sym = (d.get("ticker") or "").strip().upper()
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
    print("selftest OK")


HTML = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>FundaPilot — institutional-grade equity research & portfolio optimization</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
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
</style></head><body>
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
  </div>
  <div id="m-search" class="panel">
    <div class="full ac"><label>Company / ticker</label><input id="ticker" placeholder="Type e.g. Reliance, HAL, Apple…" autocomplete="off">
      <div class="acbox" id="acbox"></div><div class="hint">Pick a suggestion, or type a symbol. NSE adds .NS automatically.</div></div>
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
<script>
const $=h=>{const d=document.createElement('div');d.innerHTML=h;return d.firstElementChild};
const el=id=>document.getElementById(id), out=el('out');
const fmt=n=>n==null?'—':(Math.abs(n)>=1e7?(n/1e7).toFixed(2)+' Cr':Math.abs(n)>=1e5?(n/1e5).toFixed(2)+' L':Number(n).toLocaleString(undefined,{maximumFractionDigits:2}));
let charts=[],mode='search',UNI={},MOD={};
function setMode(m){mode=m;document.querySelectorAll('.tab').forEach(x=>x.classList.toggle('on',x.dataset.tab===m));
  ['search','explore','models','track','markets','watch'].forEach(k=>el('m-'+k).style.display=k===m?(k==='search'||k==='explore'?'grid':'block'):'none');
  el('filters').style.display=(m==='search'||m==='explore')?'grid':'none';
  if(m==='models')renderModels();if(m==='track')renderTracker();if(m==='markets')renderMarkets();if(m==='watch')renderWatch();}
document.querySelectorAll('.tab').forEach(t=>t.onclick=()=>setMode(t.dataset.tab));

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
  el('market').value=sym.endsWith('.BO')?'BSE':sym.endsWith('.NS')?'NSE':'Global';});
document.addEventListener('click',e=>{if(!e.target.closest('.ac'))document.querySelectorAll('.acbox').forEach(b=>b.style.display='none');});

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
function render(d){const cur=d.currency;out.innerHTML='';
  out.append($(`<section class="glass"><h2>${d.name} <span class="muted">${d.ticker}</span></h2>
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

  let s='<section class="glass"><h2>Scorecard (/10)</h2><div class="grid">';
  for(const[k,v]of Object.entries(d.scores)){if(v==null)continue;s+=`<div><div style="display:flex;justify-content:space-between"><span>${k}</span><b>${v}</b></div><div class="bar"><i style="width:${v*10}%"></i></div></div>`;}
  out.append($(s+'</div></section>'));

  const val=d.valuation;
  out.append($(`<section class="glass"><h2>Valuation — DCF &amp; reverse DCF</h2><div class="grid cards">
    <div class="chip">DCF fair value<b>${money(cur,val.fair_value_mktcap,null)}</b><span class="muted">mkt-cap basis</span></div>
    <div class="chip">Margin of safety<b>${val.margin_of_safety_pct==null?'—':val.margin_of_safety_pct+'%'}</b><span class="muted">+ = undervalued</span></div>
    <div class="chip">Growth used<b>${val.growth_used_pct}%</b><span class="muted">disc ${val.discount_pct}%, ${val.proj_years}y</span></div>
    <div class="chip">Reverse-DCF implied growth<b>${val.implied_growth_pct==null?'—':val.implied_growth_pct+'%'}</b><span class="muted">priced-in</span></div></div>
    <p class="muted">FCF used ${money(cur,val.fcf,null)} (${val.fcf_source||'n/a'}). Undervalued if fair value &gt; market cap. If reverse-DCF growth &gt; what the company can deliver, it's priced for perfection.</p></section>`));

  let rt='<section class="glass"><h2>Ratio analysis <span class="muted">— with plain-English benchmarks</span></h2><table><tr><th>Metric</th><th>Value</th><th style="text-align:left">What it means · benchmark</th></tr>';
  for(const[k,o]of Object.entries(d.ratios)){const u=o.unit||'';
    const disp=o.value==null?'—':(o.inr!==undefined?money(cur,o.value,o.inr):(u==='%'?o.value+'%':u==='x'?o.value+'x':o.value));
    const meaning=o.text?`<span class="dot d-${o.rating}"></span>${o.text}${o.bench?' <span class="muted">('+o.bench+')</span>':''}`:'<span class="muted">—</span>';
    rt+=`<tr><td>${k}</td><td>${disp}</td><td style="text-align:left;font-size:12px">${meaning}</td></tr>`;}
  out.append($(rt+'</table><p class="muted">🟢 good · 🟡 ok · 🔴 watch. Benchmarks are general rules of thumb; compare within the same sector.</p></section>'));

  out.append($(`<section class="glass"><h2>Technical analysis</h2><div class="grid two">${tf('Daily',d.technical.daily)}${tf('Weekly',d.technical.weekly)}</div>
    <p class="muted">Benchmarks — RSI(14): below 30 = oversold 🟢, 30–70 = neutral, above 70 = overbought 🔴. EMA200: price above = long-term uptrend; below = downtrend.</p>
    <div class="grid two" style="margin-top:8px"><div><canvas id="cD" height="160"></canvas><div class="muted" style="text-align:center">Daily vs EMA200</div></div>
    <div><canvas id="cW" height="160"></canvas><div class="muted" style="text-align:center">Weekly vs EMA200</div></div></div></section>`));
  drawLine('cD',d.technical.daily);drawLine('cW',d.technical.weekly);

  if(d.history&&Object.keys(d.history).length){out.append($('<section class="glass"><h2>Historical statements</h2><canvas id="cH" height="120"></canvas></section>'));drawHist('cH',d.history);}

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
  sh+=sa.news.length?sa.news.map(n=>`<div class="flag ${n.tone=='neg'?'f-bad':n.tone=='pos'?'f-good':''}"><a href="${n.link}" target="_blank">${n.title}</a> <small class="muted">${n.date}</small></div>`).join(''):'<div class="muted">No sector headlines.</div>';
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
  nh+=d.news.length?d.news.map(n=>`<div class="flag ${n.tone=='neg'?'f-bad':n.tone=='pos'?'f-good':''}"><a href="${n.link}" target="_blank">${n.title}</a> <small class="muted">${n.date}</small></div>`).join(''):'<div class="muted">No headlines.</div>';
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
function drawHist(id,h){const keys=Object.keys(h),labels=Object.keys(h[keys[0]]).reverse(),col={'Revenue':'#6ea8fe','EBITDA':'#b39bff','Net Income':'#39d98a'};
  charts.push(new Chart(el(id),{type:'bar',data:{labels,datasets:keys.map(k=>({label:k,data:labels.map(l=>h[k][l]),backgroundColor:col[k]||'#888'}))},options:{plugins:{legend:{labels:{color:'#8b97ad'}}},scales:{x:{ticks:{color:'#8b97ad'}},y:{ticks:{color:'#8b97ad'}}}}}));}

// ---- model portfolios: sector allocation, then drill into strong companies ----
function renderModels(){const cap=+el('capital').value||100000;
  let h=`<section class="glass"><h2>📊 Model portfolios — sector allocation</h2><p class="muted">How capital is split across <b>sectors</b> for each risk profile (equity only, ₹${fmt(cap)}). Click a sector to load <b>fundamentally strong</b> companies in it. Templates, not live-trend forecasts.</p>`;
  for(const[name,m]of Object.entries(MOD)){h+=`<h3>${name} — <span class="muted">${m.note}</span></h3><table><tr><th>Sector</th><th>Weight</th><th>Amount</th><th></th></tr>`;
    for(const[sec,wt]of Object.entries(m.weights))
      h+=`<tr><td>${sec}</td><td>${wt}%</td><td>₹${fmt(cap*wt/100)}</td><td><button class="secbtn" data-sec="${sec}" style="padding:5px 10px;font-size:12px">View strong picks ▸</button></td></tr>`;
    h+='</table>';}
  h+=`<div style="margin-top:14px"><label>Rank strong picks by</label><select id="tilt" style="max-width:240px">
    <option value="fundamental">Fundamentally strong (quality)</option><option value="dividend">Dividend</option>
    <option value="momentum">Momentum (6-month)</option><option value="lowbeta">Low beta (stable)</option></select></div>
    <div id="secpicks"></div></section>`;
  el('m-models').innerHTML=h;
  el('m-models').querySelectorAll('.secbtn').forEach(b=>b.onclick=()=>loadSectorPicks(b.dataset.sec));}
async function loadSectorPicks(sec){const tilt=el('tilt')?el('tilt').value:'fundamental';
  el('secpicks').innerHTML=`<div class="spin"></div>`;
  const d=await(await fetch(`/sector_top?country=India&sector=${encodeURIComponent(sec)}&tilt=${tilt}`)).json();
  if(d.error){el('secpicks').innerHTML=`<p class="muted">${d.error}</p>`;return;}
  let t=`<h3>${sec} — strong companies (by ${tilt})</h3><table><tr><th>Company</th><th>Quality/10</th><th>P/E</th><th>ROE%</th><th>Div%</th><th>6m%</th><th>Beta</th><th>Tags</th></tr>`;
  d.rows.forEach(r=>t+=`<tr><td>${r.name||r.ticker} <span class="muted">${r.ticker.replace('.NS','')}</span></td><td>${r.quality??'—'}</td><td>${fmt(r.pe)}</td><td>${fmt(r.roe)}</td><td>${fmt(r.div)}</td><td>${fmt(r.momentum_6m)}</td><td>${fmt(r.beta)}</td><td>${(r.tags||[]).map(x=>`<span class="tag">${x}</span>`).join('')}</td></tr>`);
  el('secpicks').innerHTML=t+'</table><p class="muted">Quality = blend of ROE, margin, low debt, valuation, free cash flow. Click a sector again after changing the rank dropdown.</p>';}

// ---- live portfolio tracker ----
const PKEY='stocklens_portfolio';
function loadPort(){try{return JSON.parse(localStorage.getItem(PKEY))||[]}catch(e){return[]}}
function savePort(p){localStorage.setItem(PKEY,JSON.stringify(p))}
let trackTimer;
const CKEY='stocklens_capital';
function renderTracker(){const p=loadPort();
  let h=`<section class="glass"><h2>📈 My portfolio — live</h2>
    <div class="panel"><div class="ac"><label>Ticker</label><input id="t-sym" placeholder="Type e.g. HAL, Apple…" autocomplete="off"><div class="acbox" id="t-acbox"></div></div>
    <div><label>Qty</label><input id="t-qty" type="number" min="0"></div><div><label>Buy price</label><input id="t-buy" type="number" min="0"></div>
    <div><label>Alert if ± %</label><input id="t-alert" type="number" value="10" min="0"></div><button id="t-add">Add holding</button></div>
    <div class="panel" style="margin-top:10px"><div><label>Total capital for rebalancing (₹, manual)</label><input id="t-cap" type="number" min="0" value="${localStorage.getItem(CKEY)||200000}"></div>
    <div><label>Extra cash to deploy (₹)</label><input id="t-extra" type="number" min="0" value="0"></div>
    <div style="align-self:end"><button id="t-opt" class="full">🧮 Analyze &amp; optimize portfolio</button></div></div>
    <div style="margin:10px 0"><button id="t-notif" style="background:#0e1422;color:var(--acc);border:1px solid var(--line)">🔔 Enable alerts</button> <button id="t-refresh" style="background:#0e1422;color:var(--acc);border:1px solid var(--line)">↻ Refresh now</button> <span class="muted">Auto-refreshes every 60s.</span></div>
    <div id="t-table"><div class="muted">No holdings yet — add one above.</div></div></section>
    <div id="t-opt-out"></div><div id="t-news"></div>`;
  el('m-track').innerHTML=h;
  acWire('t-sym','t-acbox',sym=>{el('t-sym').value=sym;});
  el('t-cap').onchange=()=>localStorage.setItem(CKEY,el('t-cap').value);
  el('t-add').onclick=()=>{const s=el('t-sym').value.trim().toUpperCase();if(!s)return;
    p.push({sym:s,qty:+el('t-qty').value||0,buy:+el('t-buy').value||0,alert:+el('t-alert').value||0,alerted:false});savePort(p);renderTracker();};
  el('t-notif').onclick=()=>Notification.requestPermission();
  el('t-refresh').onclick=refreshPort;
  el('t-opt').onclick=optimizePort;
  clearInterval(trackTimer);if(p.length){refreshPort();loadPortNews();trackTimer=setInterval(refreshPort,60000);}}

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
    <div class="chip">Expected return<b>${m.expected_return_pct}%</b><span class="muted">annual, historical</span></div>
    <div class="chip">CAGR<b>${m.cagr_pct}%</b>${hint(bm.cagr)}</div>
    <div class="chip">Volatility<b>${m.volatility_pct}%</b></div>
    <div class="chip">Sharpe ratio<b>${m.sharpe??'—'}</b>${hint(bm.sharpe)}</div>
    <div class="chip">Sortino ratio<b>${m.sortino??'—'}</b>${hint(bm.sortino)}</div>
    <div class="chip">Diversification<b>${m.diversification_score}/10</b><span class="muted">${m.effective_holdings} eff. holdings</span>${hint(bm.diversification)}</div></div>
    <p class="muted">${d.note}</p></section>`));
  o.append($(`<section class="glass"><h2>⚠️ Risk analytics</h2>
    <div class="grid cards"><div class="chip">Portfolio beta<b>${r.portfolio_beta}</b>${hint(bm.beta)}</div>
    <div class="chip">Alpha (vs ${r.benchmark_name})<b>${r.alpha_pct}%</b>${hint(bm.alpha)}</div>
    <div class="chip">1-day VaR (95%)<b>₹${fmt(r.var_1d_95_amount)}</b><span class="muted">${r.var_1d_95_pct}%</span>${hint(bm.var)}</div>
    <div class="chip">CVaR / Exp. Shortfall<b>₹${fmt(r.cvar_1d_95_amount)}</b><span class="muted">${r.cvar_1d_95_pct}%</span>${hint(bm.cvar)}</div>
    <div class="chip">Max drawdown<b>${r.max_drawdown_pct}%</b>${hint(bm.max_drawdown)}</div>
    <div class="chip">Sector concentration<b>${r.sector_concentration_pct}%</b>${hint(bm.sector_concentration)}</div></div>
    <h3>Per-stock risk</h3><table><tr><th>Stock</th><th>Weight</th><th>Beta</th><th>Volatility</th><th>Downside dev</th><th>Max DD</th><th>Exp ret</th></tr>
    ${d.per_stock.map(s=>`<tr><td>${s.sym.replace('.NS','')}</td><td>${s.weight_pct}%</td><td>${s.beta??'—'}</td><td>${s.vol_pct}%</td><td>${s.downside_dev_pct??'—'}%</td><td>${s.max_drawdown_pct}%</td><td>${s.exp_return_pct}%</td></tr>`).join('')}</table>
    <h3>Sector concentration</h3>${r.top_sectors.map(s=>`<div class="kv">${s.sector}: <b>${s.pct}%</b></div>`).join('')}</section>`));
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
  let rb=`<section class="glass"><h2>♻️ Rebalancing — toward max-Sharpe mix</h2><p class="muted">Target = the max-Sharpe portfolio (best return per unit risk: ${ef.ret}% return, ${ef.vol}% vol, Sharpe ${ef.sharpe}). Trades use ₹${fmt(d.value+(d.extra_capital||0))} (value${d.extra_capital?' + ₹'+fmt(d.extra_capital)+' extra':''}).</p>
    <table><tr><th>Stock</th><th>Current</th><th>Target</th><th>Action</th><th>Amount</th></tr>`;
  d.rebalance.forEach(x=>rb+=`<tr><td>${x.sym.replace('.NS','')}</td><td>${x.current_pct}%</td><td>${x.target_pct}%</td><td>${x.action==='Buy'?'<span class="pos">Buy</span>':'<span class="neg">Trim</span>'}</td><td>₹${fmt(x.amount)}</td></tr>`);
  o.append($(rb+'</table><p class="muted">Max-Sharpe weights are unconstrained (can concentrate). Treat as a direction, not a mandate; keep position limits.</p></section>'));}

async function loadPortNews(){const p=loadPort();if(!p.length){el('t-news').innerHTML='';return;}
  const news=await(await fetch('/portfolio_news?tickers='+encodeURIComponent(p.map(h=>h.sym).join(',')))).json();
  let h='<section class="glass"><h2>📰 Portfolio news (live)</h2><div class="news">';
  h+=Array.isArray(news)&&news.length?news.map(n=>`<div class="flag ${n.tone=='neg'?'f-bad':n.tone=='pos'?'f-good':''}"><span class="tag">${n.ticker}</span> <a href="${n.link}" target="_blank">${n.title}</a> <small class="muted">${n.date}</small></div>`).join(''):'<div class="muted">No headlines.</div>';
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
async function refreshPort(){const p=loadPort();if(!p.length){el('t-table').innerHTML='<div class="muted">No holdings yet.</div>';return;}
  const q=await(await fetch('/quote?tickers='+encodeURIComponent(p.map(h=>h.sym).join(',')))).json();
  let inv=0,curv=0,t='<table><tr><th>Holding</th><th>Qty</th><th>Buy</th><th>Now</th><th>Day%</th><th>P&L</th><th>Since buy%</th><th></th></tr>';
  p.forEach((h,i)=>{const Q=q[h.sym];const price=Q?Q.price:null,day=Q?((Q.price-Q.prev)/Q.prev*100):null;
    const pl=price!=null?(price-h.buy)*h.qty:null,since=price!=null&&h.buy?((price-h.buy)/h.buy*100):null;
    if(price!=null){inv+=h.buy*h.qty;curv+=price*h.qty;}
    if(since!=null&&h.alert&&Math.abs(since)>=h.alert&&!h.alerted){h.alerted=true;
      if(Notification.permission==='granted')new Notification('FundaPilot alert',{body:`${h.sym} moved ${since.toFixed(1)}% from your buy price.`});}
    if(since!=null&&Math.abs(since)<h.alert)h.alerted=false;
    const c=v=>v==null?'—':(v>=0?'<span class="pos">+'+v.toFixed(2)+'</span>':'<span class="neg">'+v.toFixed(2)+'</span>');
    t+=`<tr><td>${h.sym}</td><td>${h.qty}</td><td>${fmt(h.buy)}</td><td>${price==null?'—':fmt(price)}</td><td>${c(day)}</td><td>${c(pl)}</td><td>${c(since)}</td><td><a href="#" data-i="${i}" class="rm">✕</a></td></tr>`;});
  savePort(p);
  const tot=curv-inv,totp=inv?tot/inv*100:0;
  t+=`</table><div class="grid cards" style="margin-top:10px"><div class="chip">Invested<b>₹${fmt(inv)}</b></div><div class="chip">Current<b>₹${fmt(curv)}</b></div><div class="chip">Total P&L<b>${tot>=0?'<span class="pos">+':'<span class="neg">'}${fmt(tot)} (${totp.toFixed(1)}%)</span></b></div></div>`;
  el('t-table').innerHTML=t;
  el('t-table').querySelectorAll('.rm').forEach(a=>a.onclick=e=>{e.preventDefault();const pp=loadPort();pp.splice(+a.dataset.i,1);savePort(pp);renderTracker();});}

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
  el('m-watch').innerHTML=`<section class="glass"><h2>⭐ Watchlists</h2><p class="muted">Curated pools screened live and ranked. Click one to load (takes a few seconds — it fetches fundamentals).</p>
    <div>${lists.map(n=>`<button class="wlbtn" data-n="${n}" style="margin:4px;background:#0e1422;color:var(--acc);border:1px solid var(--line)">${n}</button>`).join('')}</div><div id="wl-out"></div></section>`;
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
