# FundaPilot + IPO Lens — Project Playbook (handoff document)

**Purpose:** This file is the complete memory of the project. If it is uploaded into a fresh
Claude session, treat everything in it as established context: decisions here were made WITH the
user and should not be re-litigated. Written 17 Jul 2026, current through commit `6ce9572`.

**Owner:** Kota Mohith Kumar (mohithmoho@gmail.com) · GitHub `mohithmoho/fundapilot`
**Live site:** https://fundapilot.onrender.com (Render blueprint, service `fundapilot`, auto-deploys
from `main`; free tier sleeps → ~30–60s cold start, deploys land ~60–90s after push)

---

## 1. What exists (two products, one platform)

**FundaPilot** — `C:\Users\Admin\OneDrive\Desktop\fin fundamental analysis\`
Single-file Flask app (`fundapilot.py`, ~3.7k lines, inline HTML/JS) for stock research and
portfolio analytics (yfinance + Google News). It is now the **all-in-one hub** with 9 tabs:
Company · Explore by sector · Model portfolios · My portfolio · Markets · Watchlists · My space ·
**🔭 IPO Lens** · **🪙 Mutual funds**.

**IPO Lens** — `C:\Users\Admin\OneDrive\Desktop\IPO lens\` (master copy)
Zero-dependency IPO research dashboard: static HTML/CSS/JS + `server.mjs` (Node, no packages).
Three run modes:
1. Standalone: `start-ipo-lens.cmd` (checks Node, auto-ports 4173–4182, opens only a server whose
   `/api/health` returns `engine: "deterministic-v2"` — never a stale one).
2. Mounted in FundaPilot at `/ipolens/` (lazy iframe tab; Flask serves a **copy** of the files).
3. `IPO-Lens-portable.html` — single self-contained file built by `node build-portable.mjs`
   (double-click anywhere; live refresh off by design).

**MF Lens** — the Mutual funds module, lives entirely in the FundaPilot folder:
`mflens.py` (engine + inline page at `/mf/`), `mf_universe.json` (curated universe),
`refresh_holdings.py` (monthly holdings updater).

**CRITICAL — duplicate copies must stay in sync:** the deployable IPO Lens lives at
`fin fundamental analysis\ipolens\` (index.html + src/). After editing the master
(`IPO lens\...`), always `cp` index.html and `src/*` into `ipolens/` before committing.
Also: the user's ORIGINAL pre-project IPO Lens sits one level up at
`C:\Users\Admin\OneDrive\Desktop\` (src/ dated 15 Jul 2026) — it is an untouched backup. Never edit it.

---

## 2. File map (exact paths)

```
fin fundamental analysis\
  fundapilot.py        # main app; EDIT WITH CARE — single-writer rule (see §7)
  mflens.py            # MF engine; mounted via mount_mflens(app, jresp=..., ai_available=...,
                       #   ai_rate_ok=..., ai_chat=..., ai_system=AI_SYSTEM)  [fail-soft try/except]
  mf_universe.json     # curated MF universe (18 schemes, live-verified codes)
  refresh_holdings.py  # --check / --auto / --set  (monthly holdings maintenance)
  static\lens-skin.css # light reskin, loaded LAST in <head>; deleting the <link> restores dark theme
  ipolens\             # deploy copy of IPO Lens (index.html, src\{app,data,analysis-core}.js, styles.css)
  render.yaml          # Render blueprint (gunicorn, 1 worker, 8 threads, timeout 120)
  requirements.txt     # flask yfinance pandas numpy requests curl_cffi gunicorn
  PLAYBOOK.md          # this file

IPO lens\              # master IPO Lens
  index.html  server.mjs  start-ipo-lens.cmd  build-portable.mjs  IPO-Lens-portable.html
  src\{app.js, data.js, analysis-core.js, styles.css}
```

Claude memory (compressed duplicates of this playbook) lives at
`C:\Users\Admin\.claude\projects\C--Users-Admin-OneDrive-Desktop-IPO-lens\memory\`.

---

## 3. Non-negotiable user preferences

1. **Real data only.** Never invent numbers for real companies/funds. Every seeded fact carries a
   source + as-of date; unverifiable fields are `null` and render as "—" (never fake zeros).
   Derived figures (e.g. back-computed from stated CAGRs) are labeled "derived" in source notes.
2. **Never remove existing features.** All work is additive; regressions are bugs.
3. **No breaks, no console errors.** Full verification gate (§8) before every commit.
4. **Ponytail mode** (lazy/minimal): stdlib-first, fewest files, `ponytail:` comments mark
   deliberate shortcuts with upgrade paths. Non-trivial logic ships with a runnable selftest.
5. Plain-language, presentable summaries (the user demos this platform to others).
6. "Commit to GitHub" means push to `main` → Render auto-deploy → share the live link.
7. Everything is educational-only, never investment advice — disclaimers stay everywhere.

---

## 4. Architecture contracts (do not change lightly)

### FundaPilot integration points
- Tabs: `.tab` divs with `data-tab`; `setMode(m)` has a **hardcoded panel array**
  `['search','explore','models','track','markets','watch','me','ipolens','mf']` — new tabs need the
  array entry + a `#m-<key>` panel div. The two module tabs are lazy iframes (`data-src` swapped to
  `src` on first open inside setMode).
- The AI stack (reused by ALL modules): `_env()`, `ai_available()` (ANTHROPIC_API_KEY or
  AI_BASE_URL+AI_API_KEY), `ai_rate_ok(ip)` (6/min, 150/day caps), `ai_chat(system,user)`
  (Anthropic first, else OpenAI-compatible), `AI_SYSTEM` persona. Default model was fixed from the
  RETIRED `claude-3-5-haiku-latest` → `claude-haiku-4-5` (fundapilot) / `claude-opus-4-8`
  (Node server.mjs), `AI_MODEL` env overrides. AI panels self-hide when no key is configured.
- Additive blocks in fundapilot.py are fenced with `=== IPO Lens module (additive) ===` and
  `=== Mutual Fund Lens module (additive) ===` markers, inserted before `if __name__`.

### IPO Lens contracts
- `src/analysis-core.js` is pure (no browser/Node deps); same file runs in browser and Node.
  8 categories ×10 → /80; bands 70/60/50/40; verdict text starts "If I were you…".
  Two historical bug fixes that must not regress: `round(v, digits)` divides by `10**digits`
  (was /10 → news sentiment 10× too small), and financials are **year-sorted** before CAGR.
- `estimateListingGain`: `0.75×GMP + 2.2×ln(1+QIB) + 1.2×ln(1+retail) − 0.1×max(premium,0)`,
  σ=12 (gmp+subs) / 18 (one signal) / base-rate 10±30; `actualListingGain` passes through for
  listed records. Calibration: GMP↔listing corr ≈0.8 (~300 IPOs); 2025 median gain 3.8% vs 15.2%
  (2024); counterexamples cited in methodology (VMS TMT 102× subbed, listed −4.4%).
- `src/app.js` picks its API root by path: `/ipolens/api` when mounted, else `/api`. Statuses:
  upcoming / open / **closed** ("Closed · lists soon") / listed. `fmt`/`pct` render null as "—".
- Node `server.mjs` extras: `/api/nse-subscription` (cookie warm-up, 4-min cache; NSE serves
  ZERO-STUBS on some networks → app only applies rows with times>0), auto-port walk, AI routes.
- Flask mirrors the same API surface under `/ipolens/api/*` (news via `google_news`, market via
  yfinance, NSE via requests.Session, AI via shared stack).

### MF Lens contracts
- Data: **api.mfapi.in** (AMFI NAVs; search + full history; newest-first, `dd-mm-YYYY`, nav as
  string). 6h in-memory cache. **Benchmark for beta/alpha/Treynor = UTI Nifty 50 Index Direct,
  scheme code 120716, from the same API** (deliberate: Yahoo rate-limits; no second dependency).
- Metrics (`compute_metrics`): CAGR 1/3/5y via `series.asof(anchor)` (no extrapolation → null),
  ann. vol ×√252, Sharpe/Sortino vs rf 6.5% (stated in payload), CAPM alpha, Treynor (null if
  |beta|<0.05), max drawdown + date, positive-months %, best/worst rolling 1y.
- Backtests: SIP buys first NAV on/after each month-start; **XIRR by bisection** on dated
  cashflows (bounds −0.95..8); lumpsum → CAGR. Selftest anchors: steady-12% series ⇒ both ≈12%;
  1000→1100 in 1y ⇒ XIRR ≈10%.
- Fit score (documented in payload): 50 +20 risk-match +15/−25 horizon, Sharpe×10 capped ±15,
  −10 deep-drawdown on short horizons. Verdict = "If I were you…" sentence.
- Risk×horizon matrix and `CATEGORIES` (8 analyst categories incl. `top-returns` = all funds
  ranked by CAGR3y, None-last sort) live at the top of mflens.py.
- Holdings donut: sourced snapshots in mf_universe.json; `_holdings_freshness()` ages them at
  request time (0–1 months = green, ≥2 = red "stale — verify at source" + link). **No free
  holdings feed exists** (verified: mfapi=NAV-only; AMFI publishes NAV only — SEBI monthly
  disclosure is per-AMC on their own sites; kuvera returns []; mfdata.in origin down/522).
  `refresh_holdings.py --set` is the working monthly path; `--auto` self-activates if mfdata.in
  ever returns.
- Allocation pie = SEBI category-mandate midpoints (regulatory envelope, labeled as such).
- Flow: pickers set state only; **"Analyse my matches" button** triggers recommend (disabled until
  risk+horizon chosen). Category chips fetch instantly into the same grid.

---

## 5. Data inventory (what is real, what ages)

**mf_universe.json** (18 schemes, codes live-verified 16 Jul 2026). Key codes: UTI Nifty 50
120716 (benchmark) · PPFAS Flexi 122639 · HDFC Flexi 118955 · SBI Small Cap 125497 · Nippon Small
Cap 118778 · Motilal Midcap 127042 · HDFC BAF 118968 · Mirae ELSS 135781 · DSP ELSS 119242.
Manager/expense/AUM filled only where web-sourced (PPFAS: Rajeev Thakkar, 0.53%, ₹1,43,388 Cr;
SBI Small Cap: R. Srinivasan, 0.79%; UTI N50: 0.18%; HDFC BAF: 0.75%; Motilal: Niket Shah) —
**13 funds still have null manager fields** (UI says "check the AMC page"). Holdings snapshots for
5 flagships (PPFAS Jul-2026, SBI SC Jun-2026, Motilal Jul-2026, UTI=Nifty weights 10-Jul-2026,
HDFC BAF Jan-2026 ← intentionally demonstrates the stale badge). HDFC Flexi holdings: names known
but no percentages found → left null (do not guess).

**IPO directory** (`ipolens/src/data.js` = copy of master; seeded 16–17 Jul 2026):
- SBI Funds Management (rich; closed 16 Jul, lists 21 Jul; ₹545–574, P/E 38.1 vs peer median
  41.7; Day-3 subs QIB 22.79/NII 19.32/Retail 3.08, total 11.56×; GMP ~15.9%; official RHP peer
  table). `nseSymbol: 'SBIFUNDMGT'` is TENTATIVE (live refresh fails safe if wrong).
- Alpine Texworld (closed; FY26 rev 350.18/PAT 21.72; P/E 18.49; subbed only 1.04×; GMP ~3%).
- Millworks Tech (closed; FY24→26 rev 9.4→22.42→153.4 — real 7× jump; GMP 89%; SME).
- Sotefin Bharat (open 16–20 Jul; FY26 118.23/17.37; FY25 derived from stated growth).
- Caliber Mining (open 17–21 Jul; FY26 1684.66/157.9, EBITDA margin 25.7%; FY24–25 derived from
  stated CAGRs; lot 35).
- Gulf Lloyds (upcoming 20–22 Jul; FIXED price ₹100; flat revenue, falling profit — flagged).
- Listed trio (IC Electricals +76%, Teja +101%, Kratikal +35%) — outcome-only records, thin by
  choice.

**Ages fast / needs periodic care:** IPO statuses & dates (calendar moves weekly — reseed via
web search when the user complains), subscription/GMP numbers, holdings snapshots (monthly),
manager/expense/AUM. NAVs and ALL computed metrics are always live — never stale.

---

## 6. Decision log (chronological, with the "why")

1. **Original vs rebuild:** The `IPO lens` folder was missing src/ → rebuilt from scratch; then
   discovered the intact ORIGINAL at Desktop root (a still-running server exposed it). User asked
   for an agent vote on whose math wins; agents died to session limits; decided on evidence:
   **original won** (continuous weighted scoring, coverage-shrinking uncertainty, methodology
   output) + fixed its 2 real bugs (round(), year-sort). Rebuild stashed, not used.
2. **Real data mandate** (user, emphatic): replaced all fictional companies with the live IPO
   calendar; SBI FM became the boot record. Everything since follows §3.1.
3. **Zero-dependency stays:** server.mjs uses raw fetch for AI (no SDK) to preserve "no npm install".
4. **FundaPilot merge = additive mount, not rewrite:** copied module folder + fenced route blocks
   + one nav element; FundaPilot's own selftest is the no-regression gate.
5. **Light reskin via one stylesheet loaded last** (lens-skin.css) — removable in one line. Its
   known blast radius: components with hardcoded dark colors need targeted overrides (`.acbox`
   autocomplete was the reported+fixed case — dark #0e1422 panel went dark-on-dark).
6. **AI everywhere = one shared stack** (FundaPilot's), panels hidden until a key exists; fixed
   its retired default model. No key is configured on the user's machine yet (`setx ANTHROPIC_API_KEY`).
7. **MF engine benchmarks against an index FUND from the same API** — eliminates the Yahoo
   dependency that rate-limits (this network AND cloud IPs).
8. **Backtest = replay actual NAVs** (SIP→XIRR, lumpsum→CAGR); shown per fund card once
   type+amount chosen, plus a full calculator per scheme.
9. **Holdings cannot auto-update** (all free sources probed & documented in refresh_holdings.py)
   → freshness badge that ages live + validated manual/auto updater instead of a fake feed.
10. **Charts drawn as inline SVG everywhere** (stroke-dasharray rings; pie = ring with hole 0) —
    no chart library anywhere in the platform.
11. **Analyse button** replaces auto-fire recompute (user request; also kills redundant API load).
12. **Agents/council:** user sometimes asks for multi-agent division; sessions kept killing
    background agents (limits/restarts). Pattern that works: parallel-safe file ownership,
    single-writer for fundapilot.py, and be ready to absorb all seats inline. Stopped agents
    cannot be resumed after user interrupt ("won't be resumed" from runtime).

Commits: `c3e395d` all-in-one merge · `1ddf1fe` holdings freshness · `6ce9572` categories +
Analyse + IPO enrichment + acbox fix. All on `main`.

---

## 7. Gotchas & environment landmines (each cost real debugging time)

- **Bash-tool heredocs COLLAPSE `\\` even quoted** → a Python injector with `"\\n"` wrote real
  newlines and briefly broke fundapilot.py. **Rule: write injector scripts with the Write tool to
  a file, then `python file.py`.** Never inline multi-line Python with escapes in Bash heredocs.
- **fundapilot.py single-writer rule:** its HTML/JS lives in one giant string; concurrent edits
  (or agents) must never touch it in parallel. Injectors assert `src.count(anchor)==1` before
  replacing, and `ast.parse` before writing.
- **Windows console is cp1252:** printing "≥"/"₹" from Python test one-liners throws
  UnicodeEncodeError — that is a CONSOLE artifact, not an app bug (payloads are fine).
- **Site blocks:** chittorgarh/investorgain 403 WebFetch; ipocentral 403; cleartax 410;
  amfiindia blocks curl-UA on some pages but works with browser UA via requests. indiainfoline,
  ipowatch, ipoji, sahi, groww blogs generally fetch fine.
- **NSE**: `ipo-active-category` answers but serves zero-stub `Total: 0` rows from datacenter-ish
  networks; the app refuses to overwrite seeds with zeros. Symbol discovery endpoint 404s here.
- **Yahoo**: rate-limits ("Edge: Too Many Requests") — never make it load-bearing.
- **Browser-pane MCP disconnects between sessions.** Fallback verification (works, use it):
  extract `<script>` from PAGE → `node --check`; regex `$('#id')` uses vs `id="..."` defined;
  curl every endpoint. `node --check` fails on ES modules — copy to `.mjs` first.
- **`node -e` on Windows Git Bash mangles `\\` in strings** → write a scratch `.mjs` and run it.
- **Search-result summaries can cross-contaminate numbers between funds** (Alpine was handed SBI's
  PAT once). Sanity-check magnitudes against subscription/GMP before writing any figure.
- **Background `(cmd &)` + quick curl races cold pandas/yfinance imports (~10s)** — sleep 12+ or
  use run_in_background properly.
- **OneDrive paths contain spaces** — always quote; `cmd /c start-ipo-lens.cmd` from Git Bash
  fails to resolve — use PowerShell `cmd /c ".\start-ipo-lens.cmd"`.
- Git CRLF warnings on commit are benign. server.log & __pycache__ are gitignored.
- Kill stale servers before boots: `netstat -ano | grep :5000` → taskkill (ports 4173-4182 for Node).

---

## 8. Verification gate (run before EVERY commit — this is the "no mistakes" ritual)

```bash
cd "C:/Users/Admin/OneDrive/Desktop/fin fundamental analysis"
python mflens.py selftest                 # offline math asserts + live smoke (timeout-tolerant)
timeout 150 python fundapilot.py selftest # ends with "inline JS OK / selftest OK"
python -c "import ast;ast.parse(open('fundapilot.py',encoding='utf-8').read());print('parses')"
# Page JS lint (mflens PAGE): extract <script> → node --check; assert no missing $('#id') targets
# IPO Lens: node --check on src/app.js copied to .mjs; import-test data.js via scratch .mjs
# Boot + curl matrix: / , /mf/ , /ipolens/ , /static/lens-skin.css ,
#   /mf/api/recommend?risk=conservative&horizon=lt1 , /mf/api/category?name=top-returns ,
#   /mf/api/backtest?code=120716&mode=sip&amount=5000&years=3 , /mf/api/ai (expect enabled:false)
# Sync check: master IPO lens files == ipolens/ copy (cp after any master edit)
git add <specific files>   # never `git add .`
# After push: poll https://fundapilot.onrender.com/<new-endpoint> until live (~60–90s)
```

Selftest philosophy: every non-trivial computation has an assert with a KNOWN answer
(12%-growth series, 1000→1100=10% XIRR, beta-vs-self=1, monotonic⇒DD=0, Jan-2026⇒6-months-stale,
None-last sorts). Extend the selftest whenever adding math.

---

## 9. Current open threads (safe next steps if the user asks)

- SBI FM lists **21 Jul 2026** → flip status to `listed` with `actualListingGain` once real;
  same for Alpine/Millworks (21 Jul), Sotefin (23 Jul), Caliber (24 Jul), Gulf Lloyds (27 Jul).
- 13 curated funds lack manager/expense/AUM; listed IPO trio is outcome-only; HDFC Flexi holdings
  percentages unknown. All are research tasks (web search → fill with source+as-of).
- Millworks/Sotefin final subscription multiples weren't published as clean numbers at seed time.
- `--auto` holdings adapter is dormant until mfdata.in's origin returns (patterns are guesses,
  validated-before-write).
- AI end-to-end never tested with a real key (no key on the machine); enable via
  `setx ANTHROPIC_API_KEY "sk-ant-…"` then relaunch — panels appear automatically.
- Paid holdings feeds (Finnworlds / RapidAPI Indian MF portfolio) are the honest path to true
  auto-updating holdings if the user wants it.
- IPO-Lens-portable.html rebuild needed after any IPO Lens change (`node build-portable.mjs`).

## 10. Tone & framing that this user responds to

Lead with the outcome; tables for status matrices; show the real numbers as proof (e.g. "PPFAS
max drawdown lands on 24-Mar-2020, the actual COVID bottom"); flag every limitation candidly
(stale snapshots, derived figures, unverified fields) — the user values honesty labels over
false completeness; keep "Educational only, not investment advice" visible in every surface.
