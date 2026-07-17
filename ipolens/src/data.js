// IPO Lens — REAL IPO directory, seeded 16 Jul 2026 from public sources
// (Zerodha IPO page, Groww, Finnovate/RHP coverage, HDFC Sky subscription data, KPMG/Chittorgarh stats).
// Numbers are as-of the seed date; subscription/GMP move constantly — the app attempts a live NSE
// refresh where a symbol is known and falls back to these values. Verify against the RHP before acting.
// ponytail: static seed + live-refresh hook, not a full market-data pipeline. Upgrade: cached vendor adapter.

const NOT_DISCLOSED = 'Not disclosed yet';
// Fills the identity/governance shape the UI expects so thin (offer-terms-only) records never break rendering.
const thin = (record) => ({
  businessScore: 5, industryScore: 5, capacityExpansion: false,
  financials: [], peers: [],
  governance: { ceo: NOT_DISCLOSED, experience: NOT_DISCLOSED, auditor: NOT_DISCLOSED, shareholding: NOT_DISCLOSED, litigationLevel: 'medium', flags: [] },
  risks: [{ label: 'Financials not loaded — import verified RHP data for a full analysis', level: 'medium' }],
  market: { gmpPercent: 0, qibSubscription: 0, hniSubscription: 0, retailSubscription: 0, newsSentiment: 50 },
  ...record,
  company: { sector: NOT_DISCLOSED, industry: NOT_DISCLOSED, founded: NOT_DISCLOSED, headquarters: 'India', promoters: NOT_DISCLOSED, businessModel: 'Offer terms are public; audited financials have not been loaded into IPO Lens yet. Use “Import RHP text → Load a verified IPO data file” for a complete score.', products: NOT_DISCLOSED, revenueSources: NOT_DISCLOSED, ...record.company },
});

export const sampleIpo = {
  id: 'sbifm', status: 'open', exchange: 'NSE', board: 'Mainboard', openDate: '14 Jul 2026', closeDate: '16 Jul 2026', currency: '₹', unit: 'Cr',
  dataAsOf: '16 Jul 2026', aliases: ['SBI', 'SBI MF', 'SBI AMC', 'SBI Mutual Fund', 'State Bank'],
  nseSymbol: 'SBIFUNDMGT', // ponytail: tentative IPO symbol — live NSE refresh fails safe if it differs
  sources: { note: 'Seeded 16 Jul 2026 from RHP coverage (Finnovate, Groww, ipocentral), Zerodha calendar and HDFC Sky Day-3 subscription (1:54pm). Verify against the SEBI-filed RHP.' },
  company: { name: 'SBI Funds Management Limited', sector: 'Financial services', industry: 'Asset management', founded: '1992', headquarters: 'Mumbai, India', promoters: 'State Bank of India · Amundi Asset Management', businessModel: 'India’s largest asset manager by mutual-fund quarterly average AUM (over ₹10 lakh crore), running SBI Mutual Fund as an SBI–Amundi joint venture; earns management fees on mutual funds, ETFs, PMS and alternates distributed through SBI’s branch network and independent channels.', products: 'Mutual funds, ETFs/index funds, PMS, AIFs, offshore advisory', revenueSources: 'MF management fees dominate · QAAUM CAGR 18.4% FY23→FY25' },
  ipo: { priceBand: '₹545–₹574', issueSize: '₹9,812.91 Cr', freshIssue: '₹0 (no fresh issue)', ofs: '₹9,812.91 Cr (17.09 Cr shares)', listingDate: '21 Jul 2026 (NSE + BSE)', lotSize: 26, minimumInvestment: '₹14,924', useOfFunds: 'Entirely offer-for-sale — proceeds go to selling shareholders (SBI and Amundi), not to the company.', pe: 38.1 },
  businessScore: 8.5, industryScore: 8.0, capacityExpansion: false,
  financials: [
    // Consolidated, restated (₹ Cr). EPS for earlier years computed on pre-issue share count (203.68 Cr shares); FY26 EPS ₹15.08 as reported.
    { year: 'FY23', revenue: 2161.59, profit: 1339.71, ebitda: 1810.41, eps: 6.58, operatingCashFlow: null, freeCashFlow: null, debt: 0, roe: null, roce: null, ebitdaMargin: 83.8, netMargin: 62, currentRatio: null, interestCoverage: null, debtEquity: 0 },
    { year: 'FY24', revenue: 2690.56, profit: 2072.79, ebitda: 2718.82, eps: 10.18, operatingCashFlow: null, freeCashFlow: null, debt: 0, roe: null, roce: null, ebitdaMargin: 101, netMargin: 77, currentRatio: null, interestCoverage: null, debtEquity: 0 },
    { year: 'FY25', revenue: 3597.76, profit: 2540.15, ebitda: 3412.94, eps: 12.47, operatingCashFlow: null, freeCashFlow: null, debt: 0, roe: 33.8, roce: null, ebitdaMargin: 94.9, netMargin: 70.6, currentRatio: null, interestCoverage: null, debtEquity: 0 },
    { year: 'FY26', revenue: 4389.49, profit: 3067.38, ebitda: null, eps: 15.08, operatingCashFlow: null, freeCashFlow: null, debt: 0, roe: 43.02, roce: null, ebitdaMargin: null, netMargin: 69.9, currentRatio: null, interestCoverage: null, debtEquity: 0 },
  ],
  peers: [
    // Official RHP peer table (EPS basic, P/E at ₹574, RoNW %).
    { name: 'ICICI Pru AMC', revenue: null, profit: null, roe: 85.8, roce: null, pe: 49.38 },
    { name: 'HDFC AMC', revenue: null, profit: null, roe: 32.9, roce: null, pe: 41.71 },
    { name: 'Nippon Life AMC', revenue: null, profit: null, roe: 34.5, roce: null, pe: 51.1 },
    { name: 'ABSL AMC', revenue: null, profit: null, roe: 25.53, roce: null, pe: 34.46 },
    { name: 'UTI AMC', revenue: null, profit: null, roe: 11.22, roce: null, pe: 31.57 },
  ],
  governance: { ceo: 'Professional management (SBI–Amundi JV)', experience: 'Operating since 1992; largest MF franchise in India', auditor: 'As per RHP', shareholding: 'Pre-issue: SBI + Amundi ~100% of 203.68 Cr shares; ~8.4% offered via OFS', litigationLevel: 'low', flags: [] },
  risks: [
    { label: 'Fee compression: SEBI TER regulation can squeeze management fees industry-wide', level: 'medium' },
    { label: 'Revenue is market-linked — an equity drawdown shrinks AUM and fees', level: 'medium' },
    { label: 'Reliance on SBI’s distribution network (related-party concentration)', level: 'medium' },
    { label: 'FY26 RoNW of 43% is flattered by a dividend-reduced net-worth base', level: 'low' },
    { label: '100% OFS — no fresh capital enters the business', level: 'low' },
  ],
  market: { gmpPercent: 15.9, qibSubscription: 22.79, hniSubscription: 19.32, retailSubscription: 3.08, newsSentiment: 58, subscriptionAsOf: '16 Jul 2026 1:54pm — total 11.56×' },
};

const alpine = thin({
  id: 'alpine', status: 'open', exchange: 'NSE', board: 'Mainboard', openDate: '14 Jul 2026', closeDate: '16 Jul 2026', currency: '₹', unit: 'Cr',
  dataAsOf: '16 Jul 2026', aliases: ['Alpine'],
  sources: { note: 'Offer terms from the Zerodha IPO calendar, 16 Jul 2026. Financials not yet loaded.' },
  company: { name: 'Alpine Texworld Limited', sector: 'Consumer discretionary', industry: 'Textiles' },
  ipo: { priceBand: '₹100–₹105', issueSize: 'See RHP', freshIssue: NOT_DISCLOSED, ofs: NOT_DISCLOSED, listingDate: '21 Jul 2026', lotSize: NOT_DISCLOSED, minimumInvestment: NOT_DISCLOSED, useOfFunds: 'See RHP objects of the offer', pe: 0 },
});

const millworks = thin({
  id: 'millworks', status: 'open', exchange: 'NSE', board: 'SME', openDate: '14 Jul 2026', closeDate: '16 Jul 2026', currency: '₹', unit: 'Cr',
  dataAsOf: '16 Jul 2026', aliases: ['Millworks'],
  sources: { note: 'Offer terms from the Zerodha IPO calendar, 16 Jul 2026. Financials not yet loaded.' },
  company: { name: 'Millworks Technologies Limited', sector: 'Technology', industry: 'Engineering services' },
  ipo: { priceBand: '₹315–₹331', issueSize: 'See RHP', freshIssue: NOT_DISCLOSED, ofs: NOT_DISCLOSED, listingDate: '21 Jul 2026', lotSize: NOT_DISCLOSED, minimumInvestment: NOT_DISCLOSED, useOfFunds: 'See RHP objects of the offer', pe: 0 },
});

const sotefin = thin({
  id: 'sotefin', status: 'open', exchange: 'BSE', board: 'SME', openDate: '16 Jul 2026', closeDate: '20 Jul 2026', currency: '₹', unit: 'Cr',
  dataAsOf: '16 Jul 2026', aliases: ['Sotefin'],
  sources: { note: 'Offer terms from Zerodha/Chittorgarh calendars, 16 Jul 2026. Financials not yet loaded.' },
  company: { name: 'Sotefin Bharat Limited', sector: 'Industrials', industry: 'Automated parking systems' },
  ipo: { priceBand: '₹178–₹187', issueSize: 'See RHP', freshIssue: NOT_DISCLOSED, ofs: NOT_DISCLOSED, listingDate: '23 Jul 2026 (BSE SME)', lotSize: NOT_DISCLOSED, minimumInvestment: NOT_DISCLOSED, useOfFunds: 'See RHP objects of the offer', pe: 0 },
});

const caliber = thin({
  id: 'caliber', status: 'upcoming', exchange: 'BSE', board: 'Mainboard', openDate: '17 Jul 2026', closeDate: '21 Jul 2026', currency: '₹', unit: 'Cr',
  dataAsOf: '16 Jul 2026', aliases: ['Caliber', 'Calibre Mining'],
  sources: { note: 'Offer terms from Zerodha/Groww calendars, 16 Jul 2026 (₹450 Cr book-build, BSE+NSE). Financials not yet loaded.' },
  company: { name: 'Caliber Mining and Logistics Limited', sector: 'Materials', industry: 'Mining & logistics' },
  ipo: { priceBand: '₹402–₹424', issueSize: '₹450 Cr', freshIssue: NOT_DISCLOSED, ofs: NOT_DISCLOSED, listingDate: '24 Jul 2026', lotSize: NOT_DISCLOSED, minimumInvestment: NOT_DISCLOSED, useOfFunds: 'See RHP objects of the offer', pe: 0 },
});

const gulfLloyds = thin({
  id: 'gulflloyds', status: 'upcoming', exchange: 'NSE', board: 'SME', openDate: '20 Jul 2026', closeDate: '22 Jul 2026', currency: '₹', unit: 'Cr',
  dataAsOf: '16 Jul 2026', aliases: ['Gulf Lloyds', 'Gulf'],
  sources: { note: 'Dates from Zerodha/Groww calendars, 16 Jul 2026; price band not announced. Financials not yet loaded.' },
  company: { name: 'Gulf Lloyds (India) Limited', sector: 'Industrials', industry: 'Engineering' },
  ipo: { priceBand: 'To be announced', issueSize: 'See RHP', freshIssue: NOT_DISCLOSED, ofs: NOT_DISCLOSED, listingDate: '27 Jul 2026', lotSize: NOT_DISCLOSED, minimumInvestment: NOT_DISCLOSED, useOfFunds: 'See RHP objects of the offer', pe: 0 },
});

// Recently listed (real outcomes — these also anchor the listing-gain model's reference set).
const icElectricals = thin({
  id: 'icelectricals', status: 'listed', exchange: 'BSE', board: 'SME', openDate: '07 Jul 2026', closeDate: '09 Jul 2026', currency: '₹', unit: 'Cr',
  dataAsOf: '16 Jul 2026', aliases: ['IC Electricals'], actualListingGain: 76,
  sources: { note: 'Listing outcome from the Zerodha IPO calendar, 16 Jul 2026.' },
  company: { name: 'IC Electricals Company', sector: 'Industrials', industry: 'Electrical equipment' },
  ipo: { priceBand: '₹94–₹99', issueSize: 'See RHP', freshIssue: NOT_DISCLOSED, ofs: NOT_DISCLOSED, listingDate: 'Listed 10 Jul 2026 · +76%', lotSize: NOT_DISCLOSED, minimumInvestment: NOT_DISCLOSED, useOfFunds: 'See RHP', pe: 0 },
});
const teja = thin({
  id: 'teja', status: 'listed', exchange: 'NSE', board: 'SME', openDate: '01 Jul 2026', closeDate: '03 Jul 2026', currency: '₹', unit: 'Cr',
  dataAsOf: '16 Jul 2026', aliases: ['Teja Engineering'], actualListingGain: 101,
  sources: { note: 'Listing outcome from the Zerodha IPO calendar, 16 Jul 2026.' },
  company: { name: 'Teja Engineering Industries', sector: 'Industrials', industry: 'Engineering' },
  ipo: { priceBand: '₹220 (fixed)', issueSize: 'See RHP', freshIssue: NOT_DISCLOSED, ofs: NOT_DISCLOSED, listingDate: 'Listed 07 Jul 2026 · +101%', lotSize: NOT_DISCLOSED, minimumInvestment: NOT_DISCLOSED, useOfFunds: 'See RHP', pe: 0 },
});
const kratikal = thin({
  id: 'kratikal', status: 'listed', exchange: 'NSE', board: 'SME', openDate: '01 Jul 2026', closeDate: '03 Jul 2026', currency: '₹', unit: 'Cr',
  dataAsOf: '16 Jul 2026', aliases: ['Kratikal', 'Kratikal Tech'], actualListingGain: 35,
  sources: { note: 'Listing outcome from the Zerodha IPO calendar, 16 Jul 2026.' },
  company: { name: 'Kratikal Tech', sector: 'Technology', industry: 'Cybersecurity' },
  ipo: { priceBand: '₹128–₹135', issueSize: 'See RHP', freshIssue: NOT_DISCLOSED, ofs: NOT_DISCLOSED, listingDate: 'Listed 07 Jul 2026 · +35%', lotSize: NOT_DISCLOSED, minimumInvestment: NOT_DISCLOSED, useOfFunds: 'See RHP', pe: 0 },
});

export const ipoDirectory = [sampleIpo, alpine, millworks, sotefin, caliber, gulfLloyds, icElectricals, teja, kratikal];
