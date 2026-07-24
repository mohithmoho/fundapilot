// IPO Lens — REAL IPO directory, seeded 16 Jul 2026 from public sources
// (Zerodha IPO page, Groww, Finnovate/RHP coverage, HDFC Sky subscription data, KPMG/Chittorgarh stats).
// Numbers are as-of the seed date; subscription/GMP move constantly — the app attempts a live NSE
// refresh where a symbol is known and falls back to these values. Verify against the RHP before acting.
// ponytail: static seed + live-refresh hook, not a full market-data pipeline. Upgrade: cached vendor adapter.

export const NOT_DISCLOSED = 'Not disclosed yet';
// Fills the identity/governance shape the UI expects so thin (offer-terms-only) records never break rendering.
export const thin = (record) => ({
  businessScore: 5, industryScore: 5, capacityExpansion: false,
  financials: [], peers: [],
  governance: { ceo: NOT_DISCLOSED, experience: NOT_DISCLOSED, auditor: NOT_DISCLOSED, shareholding: NOT_DISCLOSED, litigationLevel: 'medium', flags: [] },
  risks: [{ label: 'Financials not loaded — import verified RHP data for a full analysis', level: 'medium' }],
  market: { gmpPercent: 0, qibSubscription: 0, hniSubscription: 0, retailSubscription: 0, newsSentiment: 50 },
  ...record,
  company: { sector: NOT_DISCLOSED, industry: NOT_DISCLOSED, founded: NOT_DISCLOSED, headquarters: 'India', promoters: NOT_DISCLOSED, businessModel: 'Offer terms are public; audited financials have not been loaded into IPO Lens yet. Use “Import RHP text → Load a verified IPO data file” for a complete score.', products: NOT_DISCLOSED, revenueSources: NOT_DISCLOSED, ...record.company },
});

export const sampleIpo = {
  id: 'sbifm', status: 'closed', exchange: 'NSE', board: 'Mainboard', openDate: '14 Jul 2026', closeDate: '16 Jul 2026', currency: '₹', unit: 'Cr',
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

const alpine = {
  id: 'alpine', status: 'closed', exchange: 'NSE', board: 'Mainboard', openDate: '14 Jul 2026', closeDate: '16 Jul 2026', currency: '₹', unit: 'Cr',
  dataAsOf: '17 Jul 2026', aliases: ['Alpine'],
  sources: { note: 'Offer terms and FY25/FY26 financials from India Infoline IPO coverage, 17 Jul 2026. EPS derived from the stated ₹401.59 Cr post-issue market cap at ₹105 (3.82 Cr shares) — consistent with the stated post-IPO P/E of 18.49×.' },
  company: { name: 'Alpine Texworld Limited', sector: 'Consumer discretionary', industry: 'Textiles', founded: '2016', headquarters: 'India', promoters: 'Alpine promoter group', businessModel: 'Dyes, processes and manufactures textile fabrics — denim, shirting, suiting and ready-for-dyeing — across two facilities running 112 high-speed looms.', products: 'Denim, shirting, suiting, RFD fabrics', revenueSources: 'Fabric processing & manufacturing' },
  ipo: { priceBand: '₹100–₹105', issueSize: '₹126.25 Cr', freshIssue: '₹126.25 Cr (1.20 Cr shares)', ofs: 'None — 100% fresh issue', listingDate: '21 Jul 2026', lotSize: 142, minimumInvestment: '₹14,910', useOfFunds: 'Capacity expansion (₹32.08 Cr), debt repayment (₹52.20 Cr), general corporate purposes.', pe: 18.49 },
  businessScore: 5.0, industryScore: 4.5, capacityExpansion: true,
  financials: [
    { year: 'FY25', revenue: 237.66, profit: 8.63, ebitda: 27.0, eps: 2.26, operatingCashFlow: null, freeCashFlow: null, debt: null, roe: null, roce: null, ebitdaMargin: 11.4, netMargin: 3.6, currentRatio: null, interestCoverage: null, debtEquity: null },
    { year: 'FY26', revenue: 350.18, profit: 21.72, ebitda: 47.45, eps: 5.68, operatingCashFlow: null, freeCashFlow: null, debt: null, roe: null, roce: null, ebitdaMargin: 13.5, netMargin: 6.2, currentRatio: null, interestCoverage: null, debtEquity: null },
  ],
  peers: [],
  governance: { ceo: 'See RHP', experience: 'Operating since 2016', auditor: 'See RHP', shareholding: 'See RHP', litigationLevel: 'medium', flags: [] },
  risks: [
    { label: 'Cyclical, commodity-like textile market with thin margins', level: 'high' },
    { label: 'Tepid demand: issue subscribed just 1.04× at close', level: 'medium' },
    { label: 'Large slice of proceeds goes to debt repayment, not growth', level: 'medium' },
  ],
  market: { gmpPercent: 2.9, qibSubscription: null, hniSubscription: null, retailSubscription: null, newsSentiment: 48, subscriptionAsOf: 'close 16 Jul 2026 — total 1.04×' },
};

const millworks = {
  id: 'millworks', status: 'closed', exchange: 'BSE', board: 'SME', openDate: '14 Jul 2026', closeDate: '16 Jul 2026', currency: '₹', unit: 'Cr',
  dataAsOf: '17 Jul 2026', aliases: ['Millworks'],
  sources: { note: 'Offer terms and FY24–FY26 financials from IPO Watch coverage, 17 Jul 2026. Verify against the RHP — SME disclosures are thinner than mainboard.' },
  company: { name: 'Millworks Technologies Limited', sector: 'Industrials', industry: 'Precision engineering', founded: 'See RHP', headquarters: 'Bengaluru, India', promoters: 'Millworks promoter group', businessModel: 'Manufactures high-accuracy machined components, sheet-metal parts and integrated assemblies for railways, aerospace, defence and semiconductor customers across four Bengaluru facilities. Revenue jumped ~7× in FY26 on order-book execution.', products: 'Machined components, sheet metal, assemblies', revenueSources: 'Railways · aerospace · defence · semiconductor' },
  ipo: { priceBand: '₹315–₹331', issueSize: '₹160.34 Cr', freshIssue: '₹160.34 Cr', ofs: 'None — 100% fresh issue', listingDate: '21 Jul 2026 (BSE SME)', lotSize: 400, minimumInvestment: '₹1,32,400', useOfFunds: 'See RHP objects of the offer', pe: null },
  businessScore: 6.5, industryScore: 7.0, capacityExpansion: true,
  financials: [
    { year: 'FY24', revenue: 9.4, profit: 1.95, ebitda: null, eps: null, operatingCashFlow: null, freeCashFlow: null, debt: null, roe: null, roce: null, ebitdaMargin: null, netMargin: 20.7, currentRatio: null, interestCoverage: null, debtEquity: null },
    { year: 'FY25', revenue: 22.42, profit: 5.25, ebitda: null, eps: null, operatingCashFlow: null, freeCashFlow: null, debt: null, roe: null, roce: null, ebitdaMargin: null, netMargin: 23.4, currentRatio: null, interestCoverage: null, debtEquity: null },
    { year: 'FY26', revenue: 153.4, profit: 37.06, ebitda: null, eps: null, operatingCashFlow: null, freeCashFlow: null, debt: null, roe: null, roce: null, ebitdaMargin: null, netMargin: 24.2, currentRatio: null, interestCoverage: null, debtEquity: null },
  ],
  peers: [],
  governance: { ceo: 'See RHP', experience: 'Four manufacturing facilities in Bengaluru', auditor: 'See RHP', shareholding: 'See RHP', litigationLevel: 'medium', flags: [] },
  risks: [
    { label: 'SME listing: low liquidity and wide post-listing swings', level: 'high' },
    { label: 'A ~7× revenue jump in one year needs scrutiny — check order-book durability in the RHP', level: 'high' },
    { label: '89% GMP means expectations are already extreme', level: 'medium' },
  ],
  market: { gmpPercent: 89, qibSubscription: null, hniSubscription: null, retailSubscription: null, newsSentiment: 62, subscriptionAsOf: 'closed 16 Jul 2026 — strong demand reported' },
};

const sotefin = {
  id: 'sotefin', status: 'open', exchange: 'BSE', board: 'SME', openDate: '16 Jul 2026', closeDate: '20 Jul 2026', currency: '₹', unit: 'Cr',
  dataAsOf: '17 Jul 2026', aliases: ['Sotefin'],
  sources: { note: 'Offer terms and FY26 financials from IPO coverage (myfinology/ipowatch), 17 Jul 2026. FY25 figures derived from the company-stated +26% revenue / +54% PAT growth — verify against the RHP.' },
  company: { name: 'Sotefin Bharat Limited', sector: 'Industrials', industry: 'Automated parking systems', founded: 'See RHP', headquarters: 'India', promoters: 'Sotefin promoter group', businessModel: 'Designs and delivers automated multi-level car parking systems for real-estate and infrastructure clients.', products: 'Automated parking towers, puzzle parking, stackers', revenueSources: 'Project execution & maintenance' },
  ipo: { priceBand: '₹178–₹187', issueSize: 'See RHP', freshIssue: NOT_DISCLOSED, ofs: NOT_DISCLOSED, listingDate: '23 Jul 2026 (BSE SME)', lotSize: 600, minimumInvestment: '₹1,12,200 (1 lot)', useOfFunds: 'See RHP objects of the offer', pe: null },
  businessScore: 6.0, industryScore: 6.0, capacityExpansion: true,
  financials: [
    { year: 'FY25', revenue: 93.8, profit: 11.28, ebitda: null, eps: null, operatingCashFlow: null, freeCashFlow: null, debt: null, roe: null, roce: null, ebitdaMargin: null, netMargin: 12.0, currentRatio: null, interestCoverage: null, debtEquity: null },
    { year: 'FY26', revenue: 118.23, profit: 17.37, ebitda: null, eps: null, operatingCashFlow: null, freeCashFlow: null, debt: null, roe: null, roce: null, ebitdaMargin: null, netMargin: 14.7, currentRatio: null, interestCoverage: null, debtEquity: null },
  ],
  peers: [],
  governance: { ceo: 'See RHP', experience: 'See RHP', auditor: 'See RHP', shareholding: 'See RHP', litigationLevel: 'medium', flags: [] },
  risks: [
    { label: 'SME listing: low liquidity and wide post-listing swings', level: 'high' },
    { label: 'Project-based revenue depends on real-estate capex cycles', level: 'medium' },
    { label: 'Large lot size (₹1.12L minimum) concentrates retail risk', level: 'medium' },
  ],
  market: { gmpPercent: 0, qibSubscription: null, hniSubscription: null, retailSubscription: null, newsSentiment: 52, subscriptionAsOf: 'open 16–20 Jul 2026' },
};

const caliber = {
  id: 'caliber', status: 'open', exchange: 'BSE', board: 'Mainboard', openDate: '17 Jul 2026', closeDate: '21 Jul 2026', currency: '₹', unit: 'Cr',
  dataAsOf: '17 Jul 2026', aliases: ['Caliber', 'Calibre Mining'],
  sources: { note: 'Offer terms + FY26 financials and margins from Business Today / Business Standard coverage, 17 Jul 2026. FY24–FY25 figures derived from the company-stated growth rates (rev +17.4% FY26, 32.7% CAGR FY24–26) — verify against the RHP.' },
  company: { name: 'Caliber Mining and Logistics Limited', sector: 'Materials', industry: 'Mining services & logistics', founded: 'See RHP', headquarters: 'India', promoters: 'Caliber promoter group', businessModel: 'Provides mine development, overburden removal and integrated logistics services to coal and mineral producers; FY24–26 revenue compounded at ~33%.', products: 'Mining contracts, haulage, logistics', revenueSources: 'Mine services & transport contracts' },
  ipo: { priceBand: '₹402–₹424', issueSize: '₹450 Cr', freshIssue: NOT_DISCLOSED, ofs: NOT_DISCLOSED, listingDate: '24 Jul 2026', lotSize: 35, minimumInvestment: '₹14,840', useOfFunds: 'See RHP objects of the offer', pe: null },
  businessScore: 6.0, industryScore: 5.5, capacityExpansion: true,
  financials: [
    { year: 'FY24', revenue: 956.6, profit: 92.6, ebitda: null, eps: null, operatingCashFlow: null, freeCashFlow: null, debt: null, roe: null, roce: null, ebitdaMargin: null, netMargin: 9.7, currentRatio: null, interestCoverage: null, debtEquity: null },
    { year: 'FY25', revenue: 1434.9, profit: 131.6, ebitda: null, eps: null, operatingCashFlow: null, freeCashFlow: null, debt: null, roe: null, roce: null, ebitdaMargin: null, netMargin: 9.2, currentRatio: null, interestCoverage: null, debtEquity: null },
    { year: 'FY26', revenue: 1684.66, profit: 157.9, ebitda: 432.9, eps: null, operatingCashFlow: null, freeCashFlow: null, debt: null, roe: null, roce: null, ebitdaMargin: 25.7, netMargin: 9.4, currentRatio: null, interestCoverage: null, debtEquity: null },
  ],
  peers: [],
  governance: { ceo: 'See RHP', experience: 'See RHP', auditor: 'See RHP', shareholding: 'See RHP', litigationLevel: 'medium', flags: [] },
  risks: [
    { label: 'Client and commodity concentration: coal/mineral capex cycles', level: 'high' },
    { label: 'Capital-heavy contract mining; margins hinge on diesel and equipment costs', level: 'medium' },
    { label: 'Thin single-digit net margins leave little buffer', level: 'medium' },
  ],
  market: { gmpPercent: 0, qibSubscription: null, hniSubscription: null, retailSubscription: null, newsSentiment: 55, subscriptionAsOf: 'opens 17 Jul 2026' },
};

const gulfLloyds = {
  id: 'gulflloyds', status: 'upcoming', exchange: 'BSE', board: 'SME', openDate: '20 Jul 2026', closeDate: '22 Jul 2026', currency: '₹', unit: 'Cr',
  dataAsOf: '17 Jul 2026', aliases: ['Gulf Lloyds', 'Gulf'],
  sources: { note: 'Offer terms and FY25/FY26 financials from ipoji/ipowatch coverage, 17 Jul 2026. Fixed-price issue at ₹100. Verify against the RHP.' },
  company: { name: 'Gulf Lloyds (India) Limited', sector: 'Industrials', industry: 'Engineering', founded: 'See RHP', headquarters: 'India', promoters: 'Gulf Lloyds promoter group', businessModel: 'Engineering services firm; revenue flat around ₹36 Cr with profit slipping in FY26 — read the RHP for the order pipeline.', products: 'Engineering services', revenueSources: 'Project contracts' },
  ipo: { priceBand: '₹100 (fixed price)', issueSize: '₹18.19 Cr', freshIssue: NOT_DISCLOSED, ofs: NOT_DISCLOSED, listingDate: '27 Jul 2026 (BSE SME)', lotSize: 1200, minimumInvestment: '₹1,20,000 (1 lot)', useOfFunds: 'See RHP objects of the offer', pe: null },
  businessScore: 4.5, industryScore: 5.0, capacityExpansion: false,
  financials: [
    { year: 'FY25', revenue: 35.88, profit: 4.67, ebitda: null, eps: null, operatingCashFlow: null, freeCashFlow: null, debt: null, roe: null, roce: null, ebitdaMargin: null, netMargin: 13.0, currentRatio: null, interestCoverage: null, debtEquity: null },
    { year: 'FY26', revenue: 35.97, profit: 4.3, ebitda: 7.9, eps: null, operatingCashFlow: null, freeCashFlow: null, debt: null, roe: null, roce: null, ebitdaMargin: 22.0, netMargin: 12.0, currentRatio: null, interestCoverage: null, debtEquity: null },
  ],
  peers: [],
  governance: { ceo: 'See RHP', experience: 'See RHP', auditor: 'See RHP', shareholding: 'See RHP', litigationLevel: 'medium', flags: [] },
  risks: [
    { label: 'Flat revenue and falling profit going into the issue', level: 'high' },
    { label: 'Tiny ₹18 Cr SME issue: very low liquidity after listing', level: 'high' },
    { label: 'Large ₹1.2L minimum lot concentrates retail risk', level: 'medium' },
  ],
  market: { gmpPercent: 0, qibSubscription: null, hniSubscription: null, retailSubscription: null, newsSentiment: 45, subscriptionAsOf: 'opens 20 Jul 2026' },
};

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
