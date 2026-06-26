<p align="center">
  <img src="assets/logo.png" alt="FundaPilot" width="120" onerror="this.style.display='none'"/>
</p>

# FundaPilot — *Analyze. Value. Optimize.*

**Institutional-grade equity research & portfolio optimization platform** · AI-powered valuation and portfolio analytics · professional investment-management tooling — in **one Python file**, for **Indian (NSE/BSE) and global** stocks, with plain-English benchmarks on every number. **No API keys, no paid services.**

> ⚠️ **Educational use only. Not investment advice.** Data via Yahoo Finance + Google News. Verify before any decision.

> 🖼️ **Logo:** the header logo is drawn inline (SVG) so it always renders. To use your own, drop a `logo.png` into an `assets/` folder — the README picks it up; the in-app SVG can be swapped in `fundapilot.py` (search `FundaPilot logo`).

## Run it

```bash
pip install flask yfinance pandas numpy requests
python fundapilot.py          # open http://localhost:5000
python fundapilot.py selftest # runs the math/logic self-checks
```

That's the whole app — share `fundapilot.py` with anyone; they run those two lines. Host the repo on GitHub as-is.

## Deploy a public link (Render)

Want a URL anyone can open without installing Python? Deploy free on **Render** — it runs straight from this repo using the included `render.yaml`.

1. Push this repo to GitHub (done).
2. Go to **[render.com](https://render.com)** → sign up / log in (use "Sign in with GitHub").
3. **New ▸ Blueprint** → connect your GitHub → pick the **`fundapilot`** repo → **Apply**. Render reads `render.yaml` and configures everything.
4. Wait ~3–5 min for the first build. You get a permanent link like `https://fundapilot.onrender.com` — forward that to anyone.

Notes: the **free tier sleeps after 15 min idle**, so the first visit after a nap takes ~30–60s to wake. Live data fetches (optimize/screen) take 15–30s. To enable Fed Funds/CPI/GDP, add a `FRED_API_KEY` env var in the Render dashboard.

> Production server: `gunicorn` (in `requirements.txt`, configured in `render.yaml`). Locally you still just run `python fundapilot.py` — gunicorn is Linux-only and unused on Windows.

## Optional: accounts, Google login & per-user data (Supabase)

FundaPilot runs fully **without** any of this. Turn it on to give users **Google sign-in**, **saved watchlists**, and an **analysis journal** (which powers the calibration/“learning” view). Auth + data live in **Supabase** (free tier; the Flask app is untouched). The Supabase URL + anon key are public-by-design — **Row-Level Security** keeps each user's data private.

**One-time setup (≈15 min, free):**
1. Create a project at **supabase.com** → copy the **Project URL** and **anon public key** (Settings → API).
2. **SQL editor → run this** (creates the tables + privacy rules):
   ```sql
   create table if not exists public.watchlist (
     user_id uuid not null references auth.users on delete cascade,
     ticker text not null, name text, added_at timestamptz default now(),
     primary key (user_id, ticker));
   alter table public.watchlist enable row level security;
   create policy "own watchlist" on public.watchlist for all
     using (auth.uid() = user_id) with check (auth.uid() = user_id);

   create table if not exists public.search_history (
     id bigint generated always as identity primary key,
     user_id uuid not null references auth.users on delete cascade,
     ticker text, name text, verdict text, score numeric, price numeric,
     at timestamptz default now());
   alter table public.search_history enable row level security;
   create policy "own history" on public.search_history for all
     using (auth.uid() = user_id) with check (auth.uid() = user_id);

   -- enables the ❤️ favorites toggle and 📂 multiple named watchlists (two extra lines):
   alter table public.watchlist add column if not exists fav boolean default false;
   alter table public.watchlist add column if not exists list_name text default 'My Watchlist';
   ```
3. **Enable Google login:** Supabase → Authentication → Providers → **Google** → enable. It asks for a Google **Client ID + Secret** — make them in **Google Cloud Console → Credentials → OAuth client (Web)**, with **Authorized redirect URI** = `https://<your-project-ref>.supabase.co/auth/v1/callback`. Paste them back into Supabase.
4. Supabase → Authentication → **URL Configuration**: set **Site URL** to `https://fundapilot.onrender.com` and add it to **Redirect URLs**.
5. **Render → your service → Environment**, add:
   - `SUPABASE_URL` = your Project URL
   - `SUPABASE_ANON_KEY` = your anon public key
   Save → it redeploys. The **🔐 Sign in with Google** button and **👤 My space** tab now appear; unset the vars and they vanish (app still works).

> Privacy: only the user's own watchlist/history rows are readable (RLS). You store no passwords — Google handles auth. Add a short privacy note to your repo before sharing widely.

## Optional: AI analyst (reasons over the real numbers)

The whole tool is deterministic by default. Turn on the **🧠 AI analyst** to get a portfolio-manager-style *decision + thesis + risks* and a follow-up chat — it reasons **only over the metrics/valuation/technicals this tool already computed** (it's told never to invent numbers). Off by default; the app is unchanged without it.

Pick a provider and set env vars on Render:

- **Free (Groq)** — get a key at console.groq.com, then:
  - `AI_BASE_URL=https://api.groq.com/openai/v1`
  - `AI_API_KEY=gsk_…`
  - `AI_MODEL=llama-3.3-70b-versatile`
- **Free (OpenRouter)** — `AI_BASE_URL=https://openrouter.ai/api/v1`, `AI_API_KEY=…`, `AI_MODEL=` any free model.
- **Claude (paid, best quality)** — `ANTHROPIC_API_KEY=…` (optionally `AI_MODEL=claude-haiku-4-5` for cheap, `claude-sonnet-4-6` for stronger).

Calls are **on demand** (only when you click "Ask the AI analyst"), so cost stays controlled. The AI's output is educational reasoning over the data — verify before acting.

## Security posture

- **No secrets in the repo.** All keys (Supabase, optional FRED/Anthropic) come from environment variables on Render, never committed. The Supabase **anon key is public-by-design** — data is protected by **Row-Level Security** (each user reads only their own rows).
- **Auth** is handled by Supabase/Google OAuth; the Flask app never sees or stores passwords.
- **No database on the app server** — the Flask backend is stateless and never builds SQL, so there's no SQL-injection surface. The browser talks to Supabase via its client (parameterized).
- **Input hardening** — ticker inputs are whitelisted (`[A-Za-z0-9.\-^=&]`, length-capped) before reaching yfinance/URLs; list endpoints cap item counts.
- **Rate limiting** — a per-IP limiter (150 req/min) throttles abuse of the data proxy.
- **Security headers** — `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Permissions-Policy` on every response.
- **XSS** — all externally-sourced text (news titles, company names) is HTML-escaped before rendering, and links are restricted to `http(s)`.
- **Transport** — Render serves over HTTPS by default.

Residual notes: the free tier runs one worker, so the rate limiter is per-process (fine at this scale; use Redis/flask-limiter if you scale out). There's no strict Content-Security-Policy because the UI uses inline scripts + CDNs — add one if you later split the JS into a file.

## What it does

- **Find a stock** — live autocomplete with **🇮🇳 Indian results ranked first**, or **Explore by Country → Sector → Company**, or by **Category** (Large / Mid / Small cap, High-dividend, Aggressive).
- **Filters** — time horizon, risk appetite, **investing style** (Buffett / Growth / Value / Dividend / Momentum / Balanced), capital, years of statements.
- **Ratios with benchmarks** — P/E, P/B, PEG, ROE, margins, D/E, current ratio, EBITDA, PAT, FCF, dividend yield, beta — each shown with units (% or ×), a 🟢/🟡/🔴 rating, a one-line "what it means", and the benchmark range. Money is shown in the company's currency **and** converted to ₹.
- **Valuation** — DCF + reverse-DCF → Undervalued / Fair / Overvalued + margin of safety. Robust FCF (falls back to Operating CF − Capex, then a net-income proxy) so you always get a verdict.
- **Technical** — RSI(14) zones + EMA200 trend on daily & weekly, with benchmark notes and price charts.
- **Scorecard /10** + a style-weighted score for your chosen investing style.
- **Company / sector / peer analysis** — cap-category & tags, sector news-sentiment tally, side-by-side peer ratio table.
- **Allocation & portfolio plan** — suggested weight, shares you can buy, and a **deployment method** (Lumpsum / SIP / DCA) chosen from your capital, risk, horizon, style and the valuation.
- **Research summary** — auto Strengths / Risks / **Buy-Hold-Avoid** verdict per company.
- **Model portfolios** — Conservative / Balanced / Aggressive **sector-allocation** templates; click a sector → live-screened **fundamentally strong** companies (rank by quality / dividend / momentum / low-beta).
- **My portfolio (live)** — autocomplete add (India-first), live prices + P&L, 60s auto-refresh, **browser alerts** on ± % moves, a **live portfolio-news** feed, and manual total-capital input.
- **Dividend income** — estimated **total annual dividend** the portfolio throws off, portfolio yield %, monthly average, and per-stock breakdown.
- **Portfolio optimization & quant risk analytics** (Modern Portfolio Theory) — expected return, **CAGR**, volatility, **Sharpe** & **Sortino** ratios, **Jensen's alpha** vs NIFTY, diversification score, **efficient frontier**, **Monte Carlo** (1y, 2000 sims), **VaR** + **CVaR / Expected Shortfall** (1-day 95%), portfolio beta, max drawdown, sector concentration, **correlation matrix**, factor tilt, per-stock risk (beta / volatility / downside deviation / drawdown), **stress test** ("if NIFTY falls 15% → ₹ loss"), a **backtest vs NIFTY**, and **rebalancing** toward the max-Sharpe mix using current + extra capital. Every metric carries a plain-English benchmark.
- **Markets dashboard** — NIFTY / SENSEX / Bank Nifty / India VIX, crude / gold / DXY / US-10Y / USD-INR, and **sector rotation** (1-month leaders). Optional **FRED** key adds Fed Funds / CPI / GDP.
- **Watchlists** — Buffett-quality, High-ROE, Deep-Value, Small-cap compounders (screened live).
- **Proof panel** — raw annual-statement numbers, exact DCF formula + inputs, FX rate, source links.
- **"If I were you…"** — a final plain-language call from the whole analysis.

## Data sources (all free, no keys)

| Source | Used for | How |
|---|---|---|
| **Yahoo Finance** (`yfinance`) | Prices, ratios, statements, holders, corporate actions, FX, live quotes, indices, history for risk/MPT | Auto — `.NS`/`.BO` for NSE/BSE, plain symbol for global |
| **Google News RSS** | Live company + sector + portfolio news (tone-tagged) | Auto, no key |
| **FRED** (optional) | US Fed Funds / CPI / GDP on the Markets tab | Free key at fred.stlouisfed.org → `set FRED_API_KEY=...` |

**Honest notes:** **Twitter/X** is excluded (its API is paid) — sentiment uses free news. **ROCE** isn't in free Yahoo data, so "High ROE" stands in (labelled). **CPI/GDP/Fed-Funds** need the optional free FRED key; everything else on the Markets tab is live from Yahoo. **MPT expected return** is the historical 2y mean, **not** a forecast — labelled in-app. **"All companies in a sector"** = sectoral-index constituents + liquid names (browsable); autocomplete reaches *any* listed stock.

## How verdicts are computed (the proof, in short)
- **DCF**: projects FCF at a faded growth rate over your horizon, discounts at a rate set by your risk band, adds a 3% terminal value → a fair market cap vs the current one.
- **Reverse-DCF**: binary-searches the growth the current price is pricing in.
- **RSI(14)** <30 oversold / >70 overbought; **EMA200** above = uptrend.
- **Allocation**: capped at your risk band's max single-stock weight, scaled by conviction (the /10 score).

- **Quant**: Sharpe/Sortino from annualized return vs risk; Jensen's alpha = port return − [rf + β·(benchmark − rf)]; VaR/CVaR from the historical return distribution; Monte Carlo simulates 252 days from the portfolio's mean/vol; efficient frontier samples 3000 random weightings.

Limitations are marked `ponytail:` in the code (single-stage DCF, keyword news sentiment, dividend-yield unit normalization) with upgrade paths.

## Files
```
fundapilot.py       # the entire app — backend + UI inlined (the only file you need to run)
requirements.txt    # flask, yfinance, pandas, numpy, requests
assets/logo.png     # optional — drop your logo here for the README
```

## How to make changes / add features

Everything lives in `fundapilot.py`, in this order — find the section, edit, run `python fundapilot.py selftest`, then refresh the browser:

| Want to… | Edit |
|---|---|
| **Add stocks to a sector / new sector** | the `UNIVERSE` dict near the top — just add tickers (`.NS`/`.BO` for India). |
| **Add a watchlist** | the `WATCHLISTS` dict (give it a `pool` of tickers + a `tilt`). |
| **Change a model portfolio** | the `MODELS` dict (sector → weight %). |
| **Change a ratio benchmark / add a ratio** | the `BENCH` dict (units, good/ok cutoffs, plain-English text). |
| **Add a market/macro tile** | the `IDX_MARKET` / `IDX_MACRO` / `IDX_SECTOR` dicts (label → Yahoo symbol). |
| **Tweak a quant metric** | `portfolio_analytics()` (search the function) — returns one big JSON the UI renders. |
| **Add an API endpoint** | add a `@app.route(...)` function near the other routes; return `jresp(...)`. |
| **Change the UI** | the `HTML = r"""…"""` string — CSS at the top, JS at the bottom. Each tab has a `render*()` function. |
| **Risk-free rate, cap thresholds, risk bands** | the `RF`, `cap_category()`, `RISK` constants. |

**Workflow:** the app is plain Flask + vanilla JS (no build step). Edit → save → the server auto-reloads if you set `debug=True` in the last line, or just restart `python fundapilot.py`. Add a one-line `assert` to `_selftest()` for any new math so it stays correct.

To version it: `git add -A && git commit -m "describe change"` then `git push`.
