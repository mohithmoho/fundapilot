import { sampleIpo, ipoDirectory } from './data.js';
import { computeAssessment } from './analysis-core.js';

let ipo = structuredClone(sampleIpo);
let report = computeAssessment(ipo);
const $ = (selector) => document.querySelector(selector);
// Same bundle runs standalone (Node server, APIs at /api) and mounted inside FundaPilot (Flask, /ipolens/api).
const apiBase = location.pathname.includes('/ipolens') ? '/ipolens/api' : '/api';
const fmt = (number, digits = 1) => `${Number(number).toFixed(digits).replace(/\.0$/, '')}`;
const pct = (number, digits = 1) => `${fmt(number, digits)}%`;
const title = (key) => key.replace(/\b\w/g, c => c.toUpperCase());
const scoreClass = (value, good, watch) => value >= good ? 'good' : value >= watch ? 'watch' : 'risk';
const statusMeta = { upcoming: ['Upcoming', 'up'], open: ['Open now', 'open'], listed: ['Listed', 'listed'] };
const chip = (status) => { const [label, css] = statusMeta[status] || ['—', 'listed']; return `<span class="status-chip s-${css}">${label}</span>`; };
const money = () => ipo.currency || '₹';
const unitWord = () => (ipo.unit === 'M' ? 'million' : 'crore');
const unitShort = () => `${money()} ${ipo.unit || 'Cr'}`;
const exchangeGroup = (record) => (record.exchange === 'NSE' || record.exchange === 'BSE' ? record.exchange : 'INTL');

function toast(message) {
  const node = $('#toast'); node.textContent = message; node.classList.add('show');
  clearTimeout(window.toastTimer); window.toastTimer = setTimeout(() => node.classList.remove('show'), 3600);
}

function updateIdentity() {
  const { company } = ipo;
  $('#companyName').textContent = company.name;
  if (ipo.status) $('#companyName').insertAdjacentHTML('beforeend', ` ${chip(ipo.status)}`);
  $('#companyInitial').textContent = company.name.charAt(0).toUpperCase();
  $('#companySector').textContent = `${company.industry} · ${ipo.exchange || 'RHP-ready'} ${ipo.board || 'workspace'}`.toUpperCase();
  $('#businessModel').textContent = company.businessModel;
  const facts = [['Sector', company.sector], ['Founded', company.founded], ['Headquarters', company.headquarters], ['Promoters', company.promoters], ['Products', company.products], ['Revenue mix', company.revenueSources]];
  $('#companyFacts').innerHTML = facts.map(([label, value]) => `<div><span>${label}</span><b>${value}</b></div>`).join('');
  const offer = [['Price band', ipo.ipo.priceBand], ['Issue size', ipo.ipo.issueSize], ['Fresh issue / OFS', `${ipo.ipo.freshIssue} / ${ipo.ipo.ofs}`], ['Lot size', ipo.ipo.lotSize], ['Minimum investment', ipo.ipo.minimumInvestment], ['Listing date', ipo.ipo.listingDate]];
  $('#ipoFacts').innerHTML = offer.map(([label, value]) => `<div><dt>${label}</dt><dd>${value}</dd></div>`).join('');
  $('#useOfFunds').textContent = ipo.ipo.useOfFunds;
}

function updateScoreboard() {
  const { total, recommendation } = report.score;
  const { listing, shortTerm, mediumTerm, longTerm } = report.outlook;
  $('#overallScore').textContent = fmt(total, 1); $('#scoreMeterNumber').textContent = fmt(total, 0);
  $('#recommendation').textContent = recommendation;
  $('#scoreMeter').style.background = `conic-gradient(#61c5b2 0deg ${total / 80 * 360}deg,#e8efed ${total / 80 * 360}deg 360deg)`;
  $('#listingLow').textContent = `${listing.low >= 0 ? '+' : ''}${fmt(listing.low, 0)}%`;
  $('#listingHigh').textContent = `${listing.high >= 0 ? '+' : ''}${fmt(listing.high, 0)}%`;
  $('#listingBase').textContent = `${listing.base >= 0 ? '+' : ''}${pct(listing.base, 0)} modelled centre`;
  $('#listingConfidence').textContent = `Confidence: ${listing.confidence} · varies with data coverage`;
  const left = Math.max(4, Math.min(82, (listing.base + 20) * 1.7));
  $('#rangeStart').style.inset = `0 ${Math.max(0, 100 - (listing.high + 20) * 1.7)}% 0 ${Math.max(0, (listing.low + 20) * 1.7)}%`;
  $('#rangeMarker').style.left = `${left}%`;
  [['short', shortTerm], ['mid', mediumTerm], ['long', longTerm]].forEach(([name, value]) => { $(`#${name}Bar`).style.width = `${value * 10}%`; $(`#${name}Score`).textContent = fmt(value); });
}

function linePoints(values, max, left = 40, top = 17, width = 680, height = 185) { return values.map((value, index) => [left + index * width / (values.length - 1), top + height - (value / max) * height]); }
function path(points) { return points.map(([x,y], index) => `${index ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`).join(' '); }
function updateFinancials() {
  const list = ipo.financials || [];
  if (list.length < 2) {
    $('#financialChart').innerHTML = '<text x="380" y="125" text-anchor="middle" class="axis">No audited financials loaded yet — offer terms only. Import verified RHP data for the full picture.</text>';
    $('#financialMetrics').innerHTML = '';
    $('#financialTable').innerHTML = '<tr><td colspan="7" style="text-align:left;color:#7a909a">Financial statements not loaded for this IPO.</td></tr>';
    if ($('#finUnit')) $('#finUnit').textContent = unitShort();
    return;
  }
  const max = Math.max(...list.map(x => x.revenue)) * 1.12;
  const revenue = linePoints(list.map(x => x.revenue), max), profit = linePoints(list.map(x => x.profit), max);
  const xLabels = list.map((entry, index) => `<text x="${revenue[index][0]}" y="224" text-anchor="middle" class="axis">${entry.year}</text>`).join('');
  const grids = [0, .25, .5, .75, 1].map(fraction => `<line x1="40" x2="720" y1="${17 + 185 * fraction}" y2="${17 + 185 * fraction}" class="grid"/>`).join('');
  const dots = (points, fill) => points.map(([x,y]) => `<circle cx="${x}" cy="${y}" r="4" fill="${fill}"/>`).join('');
  $('#financialChart').innerHTML = `<title>Revenue and profit growth across five financial years</title><desc>Revenue grows from ${money()}${list[0].revenue} ${unitWord()} to ${money()}${list.at(-1).revenue} ${unitWord()}; profit grows from ${money()}${list[0].profit} ${unitWord()} to ${money()}${list.at(-1).profit} ${unitWord()}.</desc>${grids}<path d="${path([...revenue, [revenue.at(-1)[0],202], [revenue[0][0],202]])} Z" class="revenue-area"/><path d="${path(revenue)}" class="revenue-line"/><path d="${path(profit)}" class="profit-line"/>${dots(revenue, '#62bcae')}${dots(profit, '#9b8fdb')}${xLabels}<text x="40" y="12" class="axis">${money()} ${unitWord()}</text>`;
  if ($('#finUnit')) $('#finUnit').textContent = unitShort();
  const metricData = [['Revenue CAGR', pct(report.metrics.revenueCagr)], ['Profit CAGR', pct(report.metrics.profitCagr)], ['Cash-flow positive', pct(report.metrics.cashFlowPositive, 0)]];
  $('#financialMetrics').innerHTML = metricData.map(([label,value]) => `<div><span>${label}</span><b>${value}</b></div>`).join('');
  $('#financialTable').innerHTML = list.map(x => `<tr><td>${x.year}</td><td>${fmt(x.revenue,0)}</td><td>${fmt(x.profit,0)}</td><td>${fmt(x.ebitda,0)}</td><td>${fmt(x.operatingCashFlow,0)}</td><td>${fmt(x.freeCashFlow,0)}</td><td>${fmt(x.debt,0)}</td></tr>`).join('');
}

function updateRatios() {
  const m = report.metrics;
  const rows = [['ROE', pct(m.roe), scoreClass(m.roe, 18, 12)], ['ROCE', pct(m.roce), scoreClass(m.roce, 18, 12)], ['EBITDA margin', pct(m.ebitdaMargin), scoreClass(m.ebitdaMargin, 14, 9)], ['Net profit margin', pct(m.npm), scoreClass(m.npm, 9, 5)], ['Current ratio', fmt(m.currentRatio), scoreClass(m.currentRatio, 1.2, 1)], ['Interest cover', `${fmt(m.interestCoverage)}×`, scoreClass(m.interestCoverage, 3, 1.5)], ['Debt / Equity', fmt(m.debtEquity), m.debtEquity <= .6 ? 'good' : m.debtEquity <= 1.2 ? 'watch' : 'risk'], ['EPS growth', pct(m.epsCagr), scoreClass(m.epsCagr, 18, 8)]];
  $('#ratioList').innerHTML = rows.map(([label, value, status]) => `<div class="ratio-row"><span>${label}</span><span class="${status}">${value}</span></div>`).join('');
  const peersShown = (ipo.peers || []).slice(0, 4);
  if (!peersShown.length) {
    $('#peerHead').innerHTML = '<th>Metric</th><th>IPO</th>';
    $('#peerTable').innerHTML = '<tr><td colspan="2" style="text-align:left;color:#7a909a">No peer set loaded for this IPO.</td></tr>';
  } else {
    const names = ['Revenue', 'Profit', 'ROE', 'ROCE', 'P/E']; const latest = ipo.financials.at(-1) || {};
    const value = (name, entry, isIpo) => {
      const raw = name === 'Revenue' ? entry.revenue : name === 'Profit' ? entry.profit : name === 'ROE' ? entry.roe : name === 'ROCE' ? entry.roce : (isIpo ? ipo.ipo.pe : entry.pe);
      if (raw == null || (name === 'P/E' && !(raw > 0))) return '—';
      return name === 'ROE' || name === 'ROCE' ? `${fmt(raw)}%` : fmt(raw);
    };
    $('#peerHead').innerHTML = `<th>Metric</th><th>IPO</th>${peersShown.map(p => `<th>${p.name}</th>`).join('')}`;
    $('#peerTable').innerHTML = names.map(name => `<tr><td>${name}</td><td>${value(name, latest, true)}</td>${peersShown.map(peer => `<td>${value(name, peer, false)}</td>`).join('')}</tr>`).join('');
  }
  const premiumText = m.valuationPremium === null ? 'Add peer P/E values to compare valuation.' : `IPO P/E is ${Math.abs(m.valuationPremium)}% ${m.valuationPremium > 0 ? 'above' : 'below'} the selected peer median (${fmt(m.peerPe)}×).`;
  $('#valuationLine').textContent = `${premiumText} ${report.verdict.valuation}.`;
}

function updateRiskAndGovernance() {
  const governance = [['CEO', ipo.governance.ceo], ['Experience', ipo.governance.experience], ['Auditor', ipo.governance.auditor], ['Shareholding', ipo.governance.shareholding], ['Litigation', title(ipo.governance.litigationLevel)]];
  $('#governanceFacts').innerHTML = governance.map(([label,value]) => `<div><span>${label}</span><b>${value}</b></div>`).join('');
  const highGovernanceRisk = ipo.governance.litigationLevel === 'high' || ipo.governance.flags.some(Boolean);
  $('#governanceHeadline').textContent = highGovernanceRisk ? 'Governance concern flagged — inspect the RHP notes' : 'No governance flag in the current sample';
  $('#riskList').innerHTML = ipo.risks.map(r => `<div class="risk-item ${r.level}"><span>${r.label}</span><div><i></i></div><span>${r.level}</span></div>`).join('');
  $('#swotStrength').textContent = `${fmt(report.metrics.revenueCagr,0)}% revenue CAGR and improving returns.`;
  $('#swotWeakness').textContent = `Debt/equity at ${fmt(report.metrics.debtEquity)}× still needs monitoring.`;
  $('#swotOpportunity').textContent = ipo.capacityExpansion ? 'Capacity expansion may broaden revenue capacity.' : 'Growth depends on disclosed industry demand.';
  $('#swotThreat').textContent = ipo.risks[1]?.label || 'Review risk-factor disclosures.';
}

function updateSentiment(source = 'local sample') {
  const market = ipo.market, score = market.newsSentiment; const label = score >= 60 ? 'Constructive' : score <= 40 ? 'Cautious' : 'Mixed';
  $('#sentimentNumber').textContent = fmt(score, 0); $('#sentimentLabel').textContent = label;
  const hasInstitutional = Number.isFinite(Number(market.institutionalSentiment));
  const hasSocial = Number.isFinite(Number(market.socialSentiment));
  $('#sentimentDetail').textContent = `A lightweight text signal, checked alongside subscription data${hasInstitutional ? ', verified institutional research' : ''}${hasSocial ? ', and supplied social context' : ''}—not a trading signal.`;
  const subscriptions = [['GMP', `${pct(market.gmpPercent)}*`], ['QIB', `${fmt(market.qibSubscription)}×`], ['HNI', `${fmt(market.hniSubscription)}×`], ['Retail', `${fmt(market.retailSubscription)}×`], ...(hasInstitutional ? [['Institutional', `${fmt(market.institutionalSentiment, 0)}/100`]] : []), ...(hasSocial ? [['Social', `${fmt(market.socialSentiment, 0)}/100*`]] : [])];
  $('#subscriptionGrid').innerHTML = subscriptions.map(([label,value]) => `<div><span>${label}</span><b>${value}</b></div>`).join('');
  $('#newsSource').textContent = `Source: ${source} · ${ipo.market.subscriptionAsOf ? `subscription as of ${ipo.market.subscriptionAsOf} · ` : ipo.dataAsOf ? `data seeded ${ipo.dataAsOf} · ` : ''}refreshed ${new Date().toLocaleTimeString()} · *Informational only`;
}

function updateVerdict() {
  $('#verdictTitle').textContent = report.score.recommendation; $('#verdictScore').textContent = fmt(report.score.total, 1); $('#plainVerdict').textContent = report.verdict.plain;
  const pros = [`Revenue CAGR of ${pct(report.metrics.revenueCagr)} with ${pct(report.metrics.cashFlowPositive,0)} positive operating-cash-flow years`, `ROCE of ${pct(report.metrics.roce)} and EBITDA margin of ${pct(report.metrics.ebitdaMargin)}`, report.metrics.valuationPremium <= 10 ? 'Valuation is close to the selected peer range.' : 'Use-of-funds includes productive business investment.'];
  const cons = [report.metrics.valuationPremium > 10 ? `P/E is ${pct(report.metrics.valuationPremium,0)} above the selected peer median.` : 'Peer valuation should be updated at launch.', ...ipo.risks.filter(r => r.level !== 'low').slice(0,2).map(r => r.label), 'Demand data and headlines can change quickly.'];
  $('#prosList').innerHTML = pros.map(item => `<li>${item}</li>`).join(''); $('#consList').innerHTML = cons.map(item => `<li>${item}</li>`).join('');
  const coverage = report.dataQuality;
  $('#analysisBreakdown').innerHTML = `<article class="method-box"><b>Primary-source status</b><p>${coverage.sourceNote}</p></article>${report.methodology.map(item => `<article class="method-box"><b>${item.label}</b><p>${item.detail}</p></article>`).join('')}`;
  renderListingGain();
}

function renderListingGain() {
  const gain = report.outlook.listingGain, box = $('#listingGainBox');
  if (!gain) { box.hidden = true; return; }
  box.hidden = false;
  const sign = (v) => `${v >= 0 ? '+' : ''}${fmt(v, 0)}%`;
  if (gain.basis === 'actual') {
    $('#lgNumber').textContent = `${sign(gain.expected)} — actual listing-day gain`;
    $('#lgLow').textContent = ''; $('#lgHigh').textContent = '';
    $('#lgFill').style.left = '49%'; $('#lgFill').style.right = '49%'; $('#lgMarker').style.left = '50%';
    $('#lgNote').textContent = gain.formula;
    return;
  }
  $('#lgNumber').textContent = `${sign(gain.expected)} expected · ${gain.confidence} confidence`;
  $('#lgLow').textContent = sign(gain.low);
  $('#lgHigh').textContent = sign(gain.high);
  const pos = (v) => Math.max(2, Math.min(98, (v + 50) / 2)); // map −50…+150 → 0…100% of the track
  $('#lgFill').style.left = `${pos(gain.low)}%`; $('#lgFill').style.right = `${100 - pos(gain.high)}%`;
  $('#lgMarker').style.left = `${pos(gain.expected)}%`;
  $('#lgNote').textContent = `Basis: ${gain.basis}. ${gain.formula}. A statistical tendency from historical Indian IPOs (GMP↔listing correlation ≈0.8) — not a promise; even 100×-subscribed issues have listed negative. Method details in Analysis breakdown.`;
}

// ---- Optional AI analyst (mirrors FundaPilot's /ai_analyst pattern) ----
// The panel stays hidden unless the server reports an API key is configured; the portable
// single-file build has no server, so the probe fails silently and the page stays clean.
async function initAi() {
  try {
    const response = await fetch(`${apiBase}/ai`);
    const data = await response.json();
    if (data.enabled) $('#aiPanel').hidden = false;
  } catch { /* no server or no key — panel stays hidden */ }
}
function aiContext() {
  return {
    company: ipo.company.name, exchange: ipo.exchange, board: ipo.board, status: ipo.status,
    offer: ipo.ipo, subscription: ipo.market, dataAsOf: ipo.dataAsOf,
    score: report.score, metrics: report.metrics, outlook: report.outlook,
    dataQuality: report.dataQuality, risks: ipo.risks, verdict: report.verdict,
  };
}
async function askAi(mode) {
  const out = $('#aiOut');
  out.hidden = false;
  out.textContent = mode === 'bear' ? 'Building the bear case…' : 'Reading the analysis…';
  $('#aiGo').disabled = true; $('#aiBear').disabled = true;
  try {
    const response = await fetch(`${apiBase}/ai`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ mode, context: aiContext() }) });
    const data = await response.json();
    out.textContent = data.text || data.note || 'No response received.';
  } catch {
    out.textContent = 'AI request failed — the local service may be offline.';
  } finally {
    $('#aiGo').disabled = false; $('#aiBear').disabled = false;
  }
}

// Live NSE subscription for open issues with a known symbol; seeded values stay if unreachable.
async function refreshLiveSubscription() {
  if (!ipo.nseSymbol || ipo.status !== 'open') return;
  try {
    const response = await fetch(`${apiBase}/nse-subscription?symbol=${encodeURIComponent(ipo.nseSymbol)}`);
    const data = await response.json();
    if (!data || data.warning || !Array.isArray(data.categories) || !data.categories.length) return;
    let applied = 0;
    for (const row of data.categories) {
      if (!(Number(row.times) > 0)) continue; // NSE serves zero-stubs to some networks — never overwrite real seeds with those
      if (/qib|qualified/i.test(row.category)) { ipo.market.qibSubscription = row.times; applied++; }
      else if (/non.?inst|nii|hni/i.test(row.category)) { ipo.market.hniSubscription = row.times; applied++; }
      else if (/retail|rii|individual/i.test(row.category)) { ipo.market.retailSubscription = row.times; applied++; }
    }
    if (!applied) return;
    ipo.market.subscriptionAsOf = `live NSE · ${new Date().toLocaleTimeString()}`;
    report = computeAssessment(ipo);
    updateScoreboard(); updateSentiment('NSE (live)'); updateVerdict(); updateAnalytics();
    toast('Live NSE subscription numbers applied.');
  } catch { /* offline or blocked — seeded snapshot remains, which is the documented fallback */ }
}

/* ---------- Directory + search (NSE / BSE / international) ---------- */
function loadIpoById(id, announce = true) {
  const record = ipoDirectory.find((entry) => entry.id === id);
  if (!record) return;
  ipo = structuredClone(record);
  report = computeAssessment(ipo);
  render();
  $('#aiOut').hidden = true; $('#aiOut').textContent = ''; // stale AI text belongs to the previous IPO
  refreshLiveSubscription();
  if (announce) { toast(`${ipo.company.name} loaded · ${ipo.exchange} ${ipo.board}`); $('#overview').scrollIntoView({ behavior: 'smooth', block: 'start' }); }
}
function updateDirectory() {
  $('#ipoOptions').innerHTML = ipoDirectory.map(r => `<option value="${r.company.name}"></option>`).join('');
  const live = ipoDirectory.filter(r => r.status === 'upcoming' || r.status === 'open');
  $('#upcomingList').innerHTML = live.map(r => `<button class="up-card${r.id === ipo.id ? ' active' : ''}" data-ipo="${r.id}"><span class="up-name">${r.company.name}</span><span class="up-meta">${r.exchange} · ${r.board} · ${r.company.industry}</span><span class="up-dates">${chip(r.status)} ${r.openDate} → ${r.closeDate}</span></button>`).join('');
}
// Typo-tolerant relevance search: exact > prefix > substring > word-prefix > small edit distance.
function editDistance(a, b) {
  if (Math.abs(a.length - b.length) > 2) return 3;
  const dp = Array.from({ length: a.length + 1 }, (_, i) => [i]);
  for (let j = 0; j <= b.length; j++) dp[0][j] = j;
  for (let i = 1; i <= a.length; i++) for (let j = 1; j <= b.length; j++)
    dp[i][j] = Math.min(dp[i - 1][j] + 1, dp[i][j - 1] + 1, dp[i - 1][j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
  return dp[a.length][b.length];
}
function relevance(record, rawQuery) {
  const tokens = rawQuery.toLowerCase().split(/\s+/).filter(Boolean);
  if (!tokens.length) return 0;
  const fields = [record.company.name, ...(record.aliases || []), record.company.industry, record.company.sector, record.exchange, record.board]
    .filter(f => f && f !== 'Not disclosed yet').map(f => String(f).toLowerCase());
  const phrase = tokens.join(' ');
  let best = 0;
  for (const field of fields) {
    if (field === phrase) best = Math.max(best, 100);
    else if (field.startsWith(phrase)) best = Math.max(best, 90);
    else if (field.includes(phrase)) best = Math.max(best, 75);
  }
  if (best) return best;
  // Token pass: every word of the query must hit some word of some field (prefix or ≤1–2 edits).
  const words = fields.flatMap(f => f.split(/[^a-z0-9]+/)).filter(Boolean);
  let total = 0;
  for (const token of tokens) {
    let tokenBest = 0;
    for (const word of words) {
      if (word === token) tokenBest = Math.max(tokenBest, 90);
      else if (word.startsWith(token)) tokenBest = Math.max(tokenBest, 80);
      else if (token.length > 2 && editDistance(word, token) <= (token.length > 5 ? 2 : 1)) tokenBest = Math.max(tokenBest, 60);
    }
    if (!tokenBest) return 0;
    total += tokenBest;
  }
  return (total / tokens.length) * 0.9;
}
function filterDirectory() {
  const query = $('#ipoSearch').value.trim();
  const exchange = $('#exchangeFilter').value, status = $('#statusFilter').value;
  const box = $('#searchResults');
  if (!query && !exchange && !status) { box.innerHTML = ''; box.classList.remove('open'); return; }
  let hits = ipoDirectory.filter(r => (!exchange || exchangeGroup(r) === exchange) && (!status || r.status === status));
  if (query) hits = hits.map(r => [relevance(r, query), r]).filter(([s]) => s >= 55).sort((a, b) => b[0] - a[0]).map(([, r]) => r);
  box.classList.add('open');
  box.innerHTML = hits.length
    ? hits.slice(0, 8).map(r => `<button class="result-row" data-ipo="${r.id}" role="option"><span class="r-name">${r.company.name}</span><span class="r-tag">${r.exchange}</span><span class="r-sector">${r.company.industry}</span>${chip(r.status)}</button>`).join('')
    : '<p class="no-hit">No match in the local directory. To analyse any other IPO, open <b>Import RHP text → Load a verified IPO data file</b> and paste its JSON.</p>';
}

/* ---------- Power BI-style visuals (drawn from the same deterministic report) ---------- */
function biKpis() {
  const m = report.metrics, s = report.score, listing = report.outlook.listing;
  const arrow = (good, ok) => (good ? 'up' : ok ? 'flat' : 'down');
  const tiles = [
    ['Overall score', `${fmt(s.total, 1)}/80`, s.recommendation, arrow(s.total >= 60, s.total >= 50)],
    ['Revenue CAGR', pct(m.revenueCagr), `${pct(m.cashFlowPositive, 0)} cash-positive years`, arrow(m.revenueCagr >= 15, m.revenueCagr >= 8)],
    ['IPO P/E', `${fmt(m.ipoPe)}×`, m.peerPe ? `Peer median ${fmt(m.peerPe)}×` : 'No peer median supplied', arrow(m.valuationPremium !== null && m.valuationPremium <= 0, m.valuationPremium !== null && m.valuationPremium <= 15)],
    ['Listing scenario', `${listing.base >= 0 ? '+' : ''}${fmt(listing.base, 0)}%`, `Confidence ${listing.confidence}`, arrow(listing.base > 0, listing.base >= -3)]
  ];
  $('#kpiRow').innerHTML = tiles.map(([label, value, sub, dir]) => `<div class="kpi kpi-${dir}"><span class="kpi-label">${label}</span><b class="kpi-val">${value}</b><span class="kpi-sub"><i class="tri tri-${dir}"></i>${sub}</span></div>`).join('');
}
function biTrend() {
  const list = ipo.financials || [];
  if (list.length < 2) { $('#biTrend').innerHTML = '<text x="220" y="105" text-anchor="middle" class="axis">Financials not loaded for this IPO</text>'; $('#biTrendTag').textContent = unitShort(); return; }
  const W = 440, H = 210, padL = 34, padR = 12, padT = 14, padB = 26;
  const iw = W - padL - padR, ih = H - padT - padB;
  const maxRevenue = Math.max(...list.map(x => x.revenue)) * 1.1;
  const profits = list.map(x => x.profit), maxProfit = Math.max(...profits) * 1.15, minProfit = Math.min(0, ...profits);
  const x = (i) => padL + (iw / list.length) * (i + .5);
  const yRevenue = (v) => padT + ih - (v / maxRevenue) * ih;
  const yProfit = (v) => padT + ih - ((v - minProfit) / ((maxProfit - minProfit) || 1)) * ih;
  const barWidth = (iw / list.length) * .5;
  const grid = [0, .33, .66, 1].map(f => `<line x1="${padL}" x2="${W - padR}" y1="${(padT + ih * f).toFixed(1)}" y2="${(padT + ih * f).toFixed(1)}" class="grid"/>`).join('');
  const bars = list.map((r, i) => `<rect x="${(x(i) - barWidth / 2).toFixed(1)}" y="${yRevenue(r.revenue).toFixed(1)}" width="${barWidth.toFixed(1)}" height="${Math.max(padT + ih - yRevenue(r.revenue), 0).toFixed(1)}" rx="3" class="bi-bar"/>`).join('');
  const line = profits.map((p, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${yProfit(p).toFixed(1)}`).join(' ');
  const dots = profits.map((p, i) => `<circle cx="${x(i).toFixed(1)}" cy="${yProfit(p).toFixed(1)}" r="3.4" class="bi-dot"/>`).join('');
  const labels = list.map((r, i) => `<text x="${x(i).toFixed(1)}" y="${H - 8}" text-anchor="middle" class="axis">${r.year}</text>`).join('');
  $('#biTrend').innerHTML = `<title>Revenue bars with a profit trend line</title>${grid}${bars}<path d="${line}" class="bi-line"/>${dots}${labels}`;
  $('#biTrendTag').textContent = unitShort();
}
function biDonut() {
  const market = ipo.market, cx = 100, cy = 100, r = 62, sw = 24, circumference = 2 * Math.PI * r;
  const parts = [['QIB', Number(market.qibSubscription) || 0, 'a'], ['HNI', Number(market.hniSubscription) || 0, 'b'], ['Retail', Number(market.retailSubscription) || 0, 'c']];
  const total = parts.reduce((sum, part) => sum + part[1], 0);
  if (total <= 0) {
    $('#biDonut').innerHTML = `<circle cx="${cx}" cy="${cy}" r="${r}" fill="none" class="donut-track" stroke-width="${sw}"/><text x="${cx}" y="${cy - 2}" text-anchor="middle" class="donut-center">—</text><text x="${cx}" y="${cy + 16}" text-anchor="middle" class="donut-sub">no bids yet</text>`;
    $('#biDonutLegend').innerHTML = '<span class="donut-note">Subscription figures appear once bidding opens.</span>';
    return;
  }
  let offset = 0, svg = `<circle cx="${cx}" cy="${cy}" r="${r}" fill="none" class="donut-track" stroke-width="${sw}"/>`;
  parts.forEach(([, value, css]) => {
    const length = (value / total) * circumference;
    svg += `<circle cx="${cx}" cy="${cy}" r="${r}" fill="none" class="donut-seg seg-${css}" stroke-width="${sw}" stroke-dasharray="${length.toFixed(2)} ${(circumference - length).toFixed(2)}" stroke-dashoffset="${(-offset).toFixed(2)}" transform="rotate(-90 ${cx} ${cy})"/>`;
    offset += length;
  });
  svg += `<text x="${cx}" y="${cy - 2}" text-anchor="middle" class="donut-center">${fmt(total / parts.length)}×</text><text x="${cx}" y="${cy + 16}" text-anchor="middle" class="donut-sub">avg of books</text>`;
  $('#biDonut').innerHTML = svg;
  $('#biDonutLegend').innerHTML = parts.map(([label, value, css]) => `<span class="donut-key"><i class="seg-${css}"></i>${label} · ${fmt(value)}×</span>`).join('');
}
function biPeers() {
  const W = 440, H = 210, padL = 96, padR = 34, padT = 12;
  const rows = [['This IPO', Number(ipo.ipo.pe) || 0, true], ...ipo.peers.slice(0, 3).map(p => [p.name, Number(p.pe) || 0, false])].filter(row => row[1] > 0);
  if (!rows.length) { $('#biPeers').innerHTML = `<text x="${W / 2}" y="${H / 2}" text-anchor="middle" class="axis">Add peer P/E values to compare</text>`; return; }
  const max = Math.max(...rows.map(row => row[1]));
  const rowHeight = (H - padT * 2) / rows.length, barHeight = Math.min(rowHeight * .55, 22);
  $('#biPeers').innerHTML = rows.map(([name, value, mine], i) => {
    const y = padT + rowHeight * i + (rowHeight - barHeight) / 2, width = Math.max((value / max) * (W - padL - padR), 2);
    return `<text x="${padL - 8}" y="${(y + barHeight / 2 + 4).toFixed(1)}" text-anchor="end" class="axis">${String(name).slice(0, 13)}</text><rect x="${padL}" y="${y.toFixed(1)}" width="${width.toFixed(1)}" height="${barHeight.toFixed(1)}" rx="4" class="${mine ? 'bi-bar-mine' : 'bi-bar-peer'}"/><text x="${(padL + width + 6).toFixed(1)}" y="${(y + barHeight / 2 + 4).toFixed(1)}" class="bi-val">${fmt(value)}×</text>`;
  }).join('');
}
function biRadar() {
  const categories = report.score.categories, cx = 120, cy = 106, R = 74;
  const keys = [['Biz', 'business'], ['Ind', 'industry'], ['Fin', 'financials'], ['Grw', 'growth'], ['Val', 'valuation'], ['Mgt', 'management'], ['Rsk', 'risks'], ['Snt', 'sentiment']];
  const angle = (i) => (-90 + i * (360 / keys.length)) * Math.PI / 180;
  const point = (i, radius) => [cx + radius * Math.cos(angle(i)), cy + radius * Math.sin(angle(i))];
  const polygon = (radiusOf) => keys.map((key, i) => point(i, radiusOf(key, i)).map(n => n.toFixed(1)).join(',')).join(' ');
  let svg = '';
  for (let ring = 1; ring <= 4; ring++) svg += `<polygon points="${polygon(() => R * ring / 4)}" class="radar-grid"/>`;
  keys.forEach((key, i) => { const [px, py] = point(i, R); svg += `<line x1="${cx}" y1="${cy}" x2="${px.toFixed(1)}" y2="${py.toFixed(1)}" class="radar-spoke"/>`; });
  svg += `<polygon points="${polygon(([, k]) => R * (Number(categories[k]) || 0) / 10)}" class="radar-area"/>`;
  keys.forEach(([label], i) => { const [px, py] = point(i, R + 13); svg += `<text x="${px.toFixed(1)}" y="${(py + 3).toFixed(1)}" text-anchor="middle" class="radar-label">${label}</text>`; });
  $('#biRadar').innerHTML = `<title>Score by category, each out of 10</title>${svg}`;
}
function updateAnalytics() { biKpis(); biTrend(); biDonut(); biPeers(); biRadar(); }

function render() { updateIdentity(); updateScoreboard(); updateFinancials(); updateRatios(); updateRiskAndGovernance(); updateSentiment(); updateVerdict(); updateAnalytics(); updateDirectory(); $('#freshness').textContent = new Date().toLocaleTimeString(); }
async function refreshNews() {
  const button = $('#refreshNews'); button.disabled = true; button.textContent = '↻ Checking public headlines…';
  try { const response = await fetch(`${apiBase}/news?company=${encodeURIComponent(ipo.company.name)}`); const feed = await response.json(); if (feed.warning) throw new Error(feed.warning); ipo.market.newsSentiment = feed.sentiment.score; report = computeAssessment(ipo); updateScoreboard(); updateSentiment(feed.source); updateVerdict(); toast(`${feed.articles.length} public headlines checked. Sentiment is ${feed.sentiment.label.toLowerCase()}.`); }
  catch { toast('Live news is unavailable; your local analysis remains usable.'); }
  finally { button.disabled = false; button.textContent = '↻ Refresh news pulse'; }
}
async function extractRhp() {
  const text = $('#rhpText').value.trim(); if (!text) return toast('Paste an RHP excerpt first.'); const result = $('#extractionResult'); result.classList.add('show'); result.textContent = 'Extracting visible labels…';
  try { const response = await fetch(`${apiBase}/rhp-extract`, { method:'POST', headers:{'content-type':'application/json'}, body:JSON.stringify({text}) }); const data = await response.json(); const details = [['Company',data.companyName],['Price band',data.priceBand],['Issue size',data.issueSize],['Lot size',data.lotSize],['Use of funds',data.useOfFunds]].filter(([,v]) => v); result.innerHTML = `<b>Found:</b> ${details.map(([k,v]) => `${k}: ${v}`).join(' · ') || 'No standard offer labels found.'}<br><br>${data.note}`; } catch { result.textContent = 'Could not extract RHP text right now. Your source text stays only in this browser session.'; }
}
function normalizeIpoInput(candidate) {
  if (!candidate || typeof candidate !== 'object' || Array.isArray(candidate)) throw new Error('The IPO input must be a JSON object.');
  if (!candidate.company?.name) throw new Error('Add company.name before loading this IPO.');
  if (!candidate.ipo?.priceBand || !candidate.ipo?.issueSize) throw new Error('Add the verified IPO price band and issue size.');
  if (!Array.isArray(candidate.financials) || candidate.financials.length < 2) throw new Error('Add at least two financial years; five years is preferred.');
  if (!Array.isArray(candidate.peers) || candidate.peers.length < 1) throw new Error('Add at least one listed peer for valuation context.');
  const requiredFields = ['year', 'revenue', 'profit', 'ebitda', 'eps', 'operatingCashFlow', 'freeCashFlow', 'debt'];
  const incomplete = candidate.financials.find((row) => requiredFields.some((field) => row[field] === undefined || row[field] === null || row[field] === ''));
  if (incomplete) throw new Error('Each financial year needs year, revenue, profit, EBITDA, EPS, operating cash flow, free cash flow, and debt.');
  return {
    ...candidate,
    company: { sector: 'Not supplied', industry: 'Not supplied', founded: 'Not supplied', headquarters: 'Not supplied', promoters: 'Not supplied', businessModel: 'Not supplied', products: 'Not supplied', revenueSources: 'Not supplied', ...candidate.company },
    ipo: { freshIssue: 'Not supplied', ofs: 'Not supplied', listingDate: 'To be confirmed', lotSize: 'Not supplied', minimumInvestment: 'Not supplied', useOfFunds: 'Not supplied', pe: 0, ...candidate.ipo },
    governance: { ceo: 'Not supplied', experience: 'Not supplied', auditor: 'Not supplied', shareholding: 'Not supplied', litigationLevel: 'medium', flags: [], ...candidate.governance },
    market: { gmpPercent: 0, qibSubscription: 0, hniSubscription: 0, retailSubscription: 0, newsSentiment: 50, ...candidate.market },
    risks: Array.isArray(candidate.risks) ? candidate.risks : [],
    sources: { note: 'Imported locally. Verify every value against the RHP or cited filing.', ...candidate.sources },
    businessScore: Number.isFinite(Number(candidate.businessScore)) ? Number(candidate.businessScore) : 5,
    industryScore: Number.isFinite(Number(candidate.industryScore)) ? Number(candidate.industryScore) : 5,
    capacityExpansion: Boolean(candidate.capacityExpansion)
  };
}
async function loadVerifiedIpoData() {
  const pasted = $('#ipoJson').value.trim();
  const file = $('#ipoFile').files[0];
  if (!pasted && !file) return toast('Paste an IPO JSON object or choose a local .json file.');
  try {
    const raw = pasted || await file.text();
    ipo = normalizeIpoInput(JSON.parse(raw));
    report = computeAssessment(ipo);
    render();
    $('#importDialog').close();
    toast(`${ipo.company.name} loaded from local verified-data input.`);
  } catch (error) { toast(error.message || 'That JSON file could not be loaded.'); }
}
function exportSnapshot() { const payload = { exportedAt:new Date().toISOString(), input:ipo, analysis:report, disclaimer:'Educational research snapshot. Not investment advice.' }; const blob = new Blob([JSON.stringify(payload,null,2)], {type:'application/json'}); const href = URL.createObjectURL(blob); const link = Object.assign(document.createElement('a'), {href, download:`ipo-lens-${ipo.company.name.toLowerCase().replace(/[^a-z0-9]+/g,'-')}.json`}); link.click(); URL.revokeObjectURL(href); toast('Editable JSON snapshot saved.'); }

$('#openImport').addEventListener('click', () => $('#importDialog').showModal()); $('#extractRhp').addEventListener('click', extractRhp); $('#loadIpoData').addEventListener('click', loadVerifiedIpoData); $('#refreshNews').addEventListener('click', refreshNews); $('#exportSnapshot').addEventListener('click', exportSnapshot); $('#useSample').addEventListener('click', () => $('#overview').scrollIntoView({behavior:'smooth'}));
$('#ipoSearch').addEventListener('input', filterDirectory);
$('#exchangeFilter').addEventListener('change', filterDirectory);
$('#statusFilter').addEventListener('change', filterDirectory);
$('#ipoSearch').addEventListener('keydown', (event) => {
  if (event.key !== 'Enter') return;
  const hit = ipoDirectory.find(r => r.company.name.toLowerCase() === event.target.value.trim().toLowerCase());
  if (hit) { loadIpoById(hit.id); $('#searchResults').classList.remove('open'); }
});
document.addEventListener('click', (event) => {
  const card = event.target.closest('[data-ipo]');
  if (!card) return;
  loadIpoById(card.dataset.ipo);
  $('#searchResults').classList.remove('open');
});
fetch(`${apiBase}/health`).then(res => res.json()).then(data => { $('#liveStatus').textContent = `Local model online · ${new Date(data.time).toLocaleTimeString()}`; }).catch(() => { $('#liveStatus').textContent = 'Offline mode · local sample'; });
$('#aiGo').addEventListener('click', () => askAi('ipo'));
$('#aiBear').addEventListener('click', () => askAi('bear'));
render();
refreshNews();
refreshLiveSubscription();
initAi();
