const clamp = (value, min = 0, max = 10) => Math.max(min, Math.min(max, Number.isFinite(value) ? value : min));
const round = (value, digits = 1) => Math.round(value * 10 ** digits) / 10 ** digits;
const median = (values) => { const sorted = values.filter(Number.isFinite).sort((a,b) => a-b); const half = Math.floor(sorted.length / 2); return sorted.length ? (sorted.length % 2 ? sorted[half] : (sorted[half - 1] + sorted[half]) / 2) : null; };
const numericOrNull = (value) => value !== undefined && value !== null && value !== '' && Number.isFinite(Number(value)) ? Number(value) : null;
export const cagr = (start, end, years) => (start > 0 && end > 0 && years > 0 ? ((end / start) ** (1 / years) - 1) * 100 : null);

export function scoreNews(articles = []) {
  const positive = /beat|growth|record|surge|strong|upbeat|profit|expansion|wins|approval|oversubscribed|demand/gi;
  const negative = /risk|fall|decline|loss|fraud|probe|lawsuit|weak|delay|concern|debt|volatile/gi;
  const text = articles.map(a => `${a.title} ${a.source || ''}`).join(' ');
  const pos = (text.match(positive) || []).length, neg = (text.match(negative) || []).length;
  const score = clamp(50 + (pos - neg) * 4, 0, 100);
  return { score: round(score, 0), label: score >= 60 ? 'Constructive' : score <= 40 ? 'Cautious' : 'Mixed', confidence: articles.length >= 5 ? 'medium' : 'low', evidence: { positiveTerms: pos, negativeTerms: neg, articles: articles.length } };
}

// Expected listing gain — a statistical estimate, not a promise.
// Calibration (published, India): GMP↔listing-gain correlation ≈0.8 across ~300 IPOs and most list
// within 15–20% of the grey-market-implied price (ipoguru.in); FY25 averages QIB 102× / retail 35×
// (KPMG); median listing gain 3.8% in 2025 vs 15.2% in 2024 (indmoney/Chittorgarh) — hence the wide
// base-rate band when no demand signals exist. High subscription alone is NOT sufficient:
// VMS TMT (102×) listed −4.4%, Studds (73×) −3.4%, while PhysicsWallah (1.8×) gained +33%.
export function estimateListingGain(input = {}, valuationPremium = null) {
  const market = input.market || {};
  const gmp = Number(market.gmpPercent);
  const qib = Math.max(Number(market.qibSubscription) || 0, 0);
  const retail = Math.max(Number(market.retailSubscription) || 0, 0);
  if (Number.isFinite(Number(input.actualListingGain))) {
    return { basis: 'actual', expected: Number(input.actualListingGain), low: Number(input.actualListingGain), high: Number(input.actualListingGain), confidence: 'actual outcome', formula: 'Listed — this is the realised listing-day gain, not an estimate.' };
  }
  const hasGmp = Number.isFinite(gmp) && gmp !== 0;
  const hasSubs = qib > 0 || retail > 0;
  let centre, sigma, basis;
  if (hasGmp || hasSubs) {
    centre = 0.75 * (hasGmp ? gmp : 0) + 2.2 * Math.log(1 + qib) + 1.2 * Math.log(1 + retail) - 0.1 * Math.max(Number(valuationPremium) || 0, 0);
    sigma = hasGmp && hasSubs ? 12 : 18; // GMP-implied prices historically land within ~15–20%
    basis = hasGmp && hasSubs ? 'gmp+subscription' : hasGmp ? 'gmp only' : 'subscription only';
  } else {
    centre = 10; sigma = 30; basis = 'historical base rate'; // 2024–25 medians span 3.8–15.2%, dispersion is huge
  }
  centre = clamp(centre, -30, 120);
  return {
    basis, expected: round(centre), low: round(clamp(centre - sigma, -50, 150)), high: round(clamp(centre + sigma, -50, 150)),
    confidence: basis === 'gmp+subscription' ? 'medium' : 'low',
    formula: `0.75×GMP(${hasGmp ? round(gmp) : 'n/a'}) + 2.2×ln(1+QIB ${round(qib)}×) + 1.2×ln(1+retail ${round(retail)}×) − 0.1×max(valuation premium, 0) → ${round(centre)}% ± ${sigma}`,
  };
}

export function computeAssessment(input = {}) {
  // Oldest-to-newest ordering underpins every CAGR below. Sort only when every year parses
  // ('FY21'→21, 2021→2021); otherwise keep the supplied order rather than risk a bad reorder.
  const history = (() => {
    const rows = Array.isArray(input.financials) ? [...input.financials] : [];
    const key = (row) => { const digits = String(row?.year ?? '').replace(/\D/g, ''); return digits ? Number(digits) : null; };
    return rows.every((row) => key(row) !== null) ? rows.sort((a, b) => key(a) - key(b)) : rows;
  })();
  const first = history[0] || {}, last = history.at(-1) || {};
  const years = Math.max(history.length - 1, 1);
  const revenueCagr = cagr(Number(first.revenue), Number(last.revenue), years);
  const profitCagr = cagr(Number(first.profit), Number(last.profit), years);
  const epsCagr = cagr(Number(first.eps), Number(last.eps), years);
  const debtEquity = Number(last.debtEquity ?? input.debtEquity ?? 0);
  const roe = Number(last.roe ?? input.roe ?? 0), roce = Number(last.roce ?? input.roce ?? 0);
  const ebitdaMargin = Number(last.ebitdaMargin ?? input.ebitdaMargin ?? 0), npm = Number(last.netMargin ?? input.netMargin ?? 0);
  const currentRatio = Number(last.currentRatio ?? input.currentRatio ?? 0), interestCoverage = Number(last.interestCoverage ?? input.interestCoverage ?? 0);
  const cashFlowPositive = history.filter(x => Number(x.operatingCashFlow) > 0).length / Math.max(history.length, 1);
  const peers = Array.isArray(input.peers) ? input.peers : [];
  const peerPe = median(peers.map(peer => Number(peer.pe)));
  const ipoPe = Number(input.ipo?.pe || 0);
  const premium = peerPe && ipoPe ? ((ipoPe / peerPe) - 1) * 100 : null;
  const newsScore = Number(input.market?.newsSentiment ?? 50);
  const qib = Number(input.market?.qibSubscription ?? 0), retail = Number(input.market?.retailSubscription ?? 0), gmp = Number(input.market?.gmpPercent ?? 0);
  const institutionalSentiment = numericOrNull(input.market?.institutionalSentiment), socialSentiment = numericOrNull(input.market?.socialSentiment);
  const riskCount = (input.risks || []).filter(r => r.level === 'high').length;
  const governanceFlags = (input.governance?.flags || []).filter(Boolean).length;
  const score = {
    business: clamp(Number(input.businessScore ?? 5)),
    industry: clamp(Number(input.industryScore ?? 5)),
    financials: clamp((revenueCagr || 0) / 5 + (profitCagr || 0) / 10 + roe / 10 + roce / 12 + cashFlowPositive * 2 + Math.min(currentRatio, 2) * .35 + Math.min(interestCoverage, 6) * .12 - debtEquity * .65),
    growth: clamp((revenueCagr || 0) / 4 + (epsCagr || 0) / 9 + Number(input.capacityExpansion ? 1.5 : 0)),
    valuation: clamp(premium === null ? 5 : 7 - premium / 15),
    management: clamp(8.5 - governanceFlags * 1.5 - Number(input.governance?.litigationLevel === 'high') * 2),
    risks: clamp(8 - riskCount * 1.5 - debtEquity * .45),
    sentiment: clamp(5 + (newsScore - 50) / 12 + Math.min(qib, 5) * .35 + Math.min(retail, 5) * .12 + clamp(gmp, -20, 30) / 15 + (institutionalSentiment === null ? 0 : (institutionalSentiment - 50) / 18) + (socialSentiment === null ? 0 : (socialSentiment - 50) / 25))
  };
  const total = round(Object.values(score).reduce((sum, item) => sum + item, 0), 1);
  // A record with no audited financials cannot be scored — say so instead of scoring it 'Avoid',
  // which would read as a negative judgement when it is really missing data.
  const unscored = !(input.financials || []).length;
  const recommendation = unscored ? 'Not scored yet'
    : total >= 70 ? 'Strong Subscribe' : total >= 60 ? 'Subscribe' : total >= 50 ? 'Neutral / Apply selectively' : total >= 40 ? 'High Risk' : 'Avoid';
  const quality = round((score.business + score.industry + score.financials + score.growth + score.management + score.risks) / 6, 1);
  const listingCenter = clamp((score.sentiment - 5) * 4 + (score.valuation - 5) * 1.5 + (score.financials - 5), -12, 35);
  const uncertainty = clamp(14 - Math.min(history.length, 5) - Math.min(peers.length, 3) - (qib > 0 ? 1 : 0), 6, 14);
  return {
    generatedAt: new Date().toISOString(), score: { categories: Object.fromEntries(Object.entries(score).map(([k,v]) => [k, round(v)])), total, recommendation },
    metrics: { revenueCagr: round(revenueCagr ?? 0), profitCagr: round(profitCagr ?? 0), epsCagr: round(epsCagr ?? 0), roe, roce, ebitdaMargin, npm, currentRatio, interestCoverage, debtEquity, cashFlowPositive: round(cashFlowPositive * 100), ipoPe, peerPe: peerPe ? round(peerPe) : null, valuationPremium: premium === null ? null : round(premium) },
    dataQuality: { financialYears: history.length, peerCount: peers.length, hasOfferTerms: Boolean(input.ipo?.priceBand && input.ipo?.issueSize), hasRiskRegister: Array.isArray(input.risks) && input.risks.length > 0, hasInstitutionalSentiment: institutionalSentiment !== null, hasSocialSentiment: socialSentiment !== null, sourceNote: input.sources?.note || 'No primary-source status supplied' },
    outlook: { listing: { low: round(listingCenter - uncertainty), base: round(listingCenter), high: round(listingCenter + uncertainty), confidence: uncertainty <= 8 ? 'medium' : 'low' }, listingGain: estimateListingGain(input, premium), shortTerm: round((score.sentiment + score.valuation) / 2), mediumTerm: round((score.financials + score.growth + score.valuation) / 3), longTerm: quality },
    verdict: { plain: total >= 60 ? 'If I were you, I would consider applying only after checking the risk flags and valuation against updated peers.' : total >= 50 ? 'If I were you, I would wait for price discovery or apply only with money I can afford to lock up and lose.' : 'If I were you, I would skip this until the financial, governance, or valuation concerns improve.', valuation: premium !== null && premium > 20 ? 'Potentially expensive versus the selected peers' : premium !== null && premium < -10 ? 'Potentially cheaper than the selected peers' : 'Around the selected peer range' },
    methodology: [
      { label: 'Financials', detail: `Revenue CAGR is computed as ((ending ÷ beginning)^(1 ÷ ${years}) − 1) × 100. The financial score combines CAGR, ROE/ROCE, margins, operating-cash-flow consistency, current ratio, interest cover and debt-to-equity. Missing values stay neutral and lower confidence; they are never assumed positive.` },
      { label: 'Valuation', detail: 'Compares IPO P/E against the median of the supplied listed-peer P/E values. It is a relative screening check, not a fair-value calculation or intrinsic valuation.' },
      { label: 'Sentiment', detail: `Combines supplied GMP and subscription observations with transparent public-headline term counts. ${institutionalSentiment !== null ? 'A supplied institutional/broker score is included at a capped weight.' : 'No institutional/broker score is included.'} ${socialSentiment !== null ? 'A supplied social score is included at a lower capped weight.' : 'No social score is included.'} Verify original sources; GMP and social sentiment are informational and may be unreliable.` },
      { label: 'Coverage', detail: `${history.length} financial year(s), ${peers.length} peer(s), ${input.risks?.length || 0} disclosed risk item(s). Inputs should be marked to their RHP or filing source before use.` },
      { label: 'Outcome range', detail: 'The listing range is a scenario indicator derived from supplied quality, valuation, demand and uncertainty inputs. It is not a price target, promise, or back-tested forecast.' },
      { label: 'Expected listing gain', detail: 'Estimated as 0.75×GMP% + 2.2×ln(1+QIB×) + 1.2×ln(1+retail×) − 0.1×max(valuation premium, 0). Grounded in published Indian IPO evidence: GMP correlates ~0.8 with listing-day returns over ~300 IPOs, and most issues list within 15–20% of the grey-market-implied price; without demand signals the estimate falls back to the historical base rate (median gains 3.8% in 2025, 15.2% in 2024) with a ±30 point band. High subscription alone is not sufficient — 100×-subscribed IPOs have listed negative. This is a statistical tendency, never a promise.' }
    ]
  };
}

// ponytail: runnable check — `node src/analysis-core.js`. Browser never executes this block.
if (typeof process !== 'undefined' && String(process.argv?.[1] || '').replace(/\\/g, '/').endsWith('analysis-core.js')) {
  const assert = (ok, msg) => { if (!ok) { console.error('CHECK FAILED:', msg); process.exit(1); } };
  assert(Math.round(cagr(100, 200, 1)) === 100, 'cagr doubling in 1y = 100%');
  assert(cagr(0, 100, 2) === null, 'cagr guards non-positive start');
  const g = estimateListingGain({ market: { gmpPercent: 15.9, qibSubscription: 22.79, retailSubscription: 3.08 } }, -8.7);
  assert(g.expected > 15 && g.expected < 26 && g.basis === 'gmp+subscription' && g.confidence === 'medium', 'SBI-like inputs → ~20% medium confidence, got ' + g.expected);
  const base = estimateListingGain({});
  assert(base.basis === 'historical base rate' && base.low < 0 && base.high > 30, 'no signals → wide base rate');
  const listed = estimateListingGain({ actualListingGain: 76 });
  assert(listed.basis === 'actual' && listed.expected === 76, 'listed record passes through actual gain');
  const full = computeAssessment({ market: { gmpPercent: 10, qibSubscription: 5 } });
  assert(Number.isFinite(full.outlook.listingGain.expected), 'listingGain integrated in assessment');
  console.log('analysis-core checks passed. Sample estimate:', g.expected + '% [' + g.low + ', ' + g.high + ']');
}

export function extractRhpSignals(text = '') {
  const source = String(text).replace(/\s+/g, ' ').trim();
  const pick = (...patterns) => { for (const pattern of patterns) { const hit = source.match(pattern); if (hit?.[1]) return hit[1].trim(); } return null; };
  return {
    companyName: pick(/(?:name of the company|company name)\s*[:\-]?\s*([A-Z][A-Za-z0-9 &,'.-]{2,80}?)(?:\.\s*(?=(?:price band|issue size|offer size|lot size|risk)\b)|\s+(?=(?:price band|issue size|offer size|lot size|risk)\b)|\.$|$)/i),
    priceBand: pick(/price band\s*(?:of|:)?\s*((?:₹|Rs\.?)?\s*[\d,]+\s*(?:to|[-–])\s*(?:₹|Rs\.?)?\s*[\d,]+)/i),
    issueSize: pick(/(?:issue size|offer size)\s*(?:of|:)?\s*((?:₹|Rs\.?)?\s*[\d,.]+\s*(?:crore|million|lakh))/i),
    lotSize: pick(/lot size\s*(?:of|:)?\s*([\d,]+)/i),
    useOfFunds: pick(/(?:objects of the offer|use of proceeds)\s*[:\-]?\s*([^\.]{30,240})/i),
    risks: [...source.matchAll(/(?:risk factor|risk)\s*[:\-]?\s*([^\.]{20,180})/gi)].slice(0, 5).map(m => m[1].trim()),
    note: 'This parser extracts visible labels from copied RHP text. Confirm every value against the original RHP before acting.'
  };
}
