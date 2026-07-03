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
  business_summary:(d.summary||'').slice(0,700),officers:d.officers,
  history:d.history,business_model:d.business_model,
  recent_news:(d.news||[]).slice(0,6).map(n=>(n.source?n.source+': ':'')+n.title)};}
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
  if(USER){bar.innerHTML=`<span class="muted">${USER.email||'signed in'}</span><button id="logout">Sign out</button>`;
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
      <button id="${pfx}new" class="dl" style="margin:0">New list</button></div>
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
  box.innerHTML=h+'</table><p class="muted">Live price &amp; day change shown without analyzing. 🤍→❤️ favorite · ✕ remove from this list. Use New list to create more.</p>';
  box.querySelectorAll('.wlgo').forEach(a=>a.onclick=e=>{e.preventDefault();gotoTicker(a.dataset.t);});
  box.querySelectorAll('.wlrm').forEach(a=>a.onclick=async e=>{e.preventDefault();await SB.from('watchlist').delete().eq('user_id',USER.id).eq('ticker',a.dataset.t);loadMyWatch(pfx);});
  box.querySelectorAll('.favt').forEach(a=>a.onclick=async e=>{e.preventDefault();await setFav(a.dataset.t,a.dataset.f!=='1');loadMyWatch(pfx);});}
async function logCall(d){if(!SB||!USER)return;try{await SB.from('search_history').insert(
  {user_id:USER.id,ticker:d.ticker,name:d.name,verdict:(d.research&&d.research.verdict)||null,score:d.overall,price:d.price});}catch(e){}}
async function renderMe(){const box=el('m-me');
  if(!SB){box.innerHTML='<section class="glass"><h2>My space</h2><p class="muted">Accounts aren\'t enabled on this deployment. Set SUPABASE_URL & SUPABASE_ANON_KEY (see README) to turn on Google sign-in, saved watchlists and your analysis journal.</p></section>';return;}
  if(!USER){box.innerHTML='<section class="glass"><h2>My space</h2><p class="muted">Sign in with Google (top-right) to see your saved watchlist and analysis journal.</p></section>';return;}
  box.innerHTML='<div id="me-watch"></div>'+
    (window.AI_ON?'<section class="glass"><h2>AI scan my watchlist</h2><button class="dl" id="ai-scan">Scan my current list for what to research now</button><div id="ai-scan-out" style="margin-top:10px"></div><p class="muted">Evaluates each name in the selected list (fundamentals + technicals), then the AI ranks what looks most actionable. Educational only.</p></section>':'')+
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
    <h3>AI memory — lessons it has learned <span class="muted">(fed into every future AI decision)</span></h3>
    <div id="ai-lessons">${AI_LESSONS.length?AI_LESSONS.map(l=>`<div class="flag f-good">• ${esc(l)}</div>`).join(''):'<p class="muted">No lessons yet — click below to have the AI review your track record and learn from it.</p>'}</div>
    <button class="dl" id="ai-coach" style="margin-top:8px">Coach me (advice)</button>
    <button class="dl" id="ai-learn" style="margin-top:8px">Self-review &amp; learn lessons</button>
    <div id="ai-coach-out" style="margin-top:8px"></div>`:'';
  box.innerHTML=(hit!=null?`<div class="rec">Directional hit-rate so far: <b>${hit}%</b> on ${scored} scored calls. ${hit>=60?'Your calls are adding value — keep the discipline.':hit>=45?'Roughly coin-flip — tighten your criteria.':'Below 50% — review what your "Buy" calls have in common.'}</div>`:'')+t+'</table>'+lessonsHtml;
  if(window.AI_ON&&el('ai-coach'))el('ai-coach').onclick=()=>aiPost('/ai_analyst',{mode:'coach',context:{hit_rate_pct:hit,scored_calls:scored,journal:journal.slice(0,40)}},'ai-coach-out','Reviewing your calls…');
  if(window.AI_ON&&el('ai-learn'))el('ai-learn').onclick=async()=>{const b=el('ai-coach-out');b.innerHTML='<div class="spin"></div><p class="muted" style="text-align:center">Reviewing outcomes & distilling lessons…</p>';
    try{const r=await(await fetch('/ai_analyst',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({mode:'lessons',context:{hit_rate_pct:hit,journal:journal.slice(0,40)}})})).json();
      if(!r.text){b.innerHTML=`<p class="muted">${esc(r.note||r.error||'No lessons.')}</p>`;return;}
      const newLessons=r.text.split('\n').map(s=>s.replace(/^[-•*\\d.\\s]+/,'').trim()).filter(s=>s.length>8);
      if(newLessons.length&&SB&&USER){try{await SB.from('ai_lessons').insert(newLessons.map(c=>({user_id:USER.id,content:c.slice(0,400)})));await loadLessons();}catch(e){b.innerHTML='<p class="muted">Lessons generated but could not be saved — run the ai_lessons migration in the README. '+esc(e.message||'')+'</p>';return;}}
      b.innerHTML=`<div class="rec" style="white-space:pre-wrap">New lessons saved — they'll now inform every future AI decision:\n${esc(newLessons.map(l=>'• '+l).join('\n'))}</div><p class="muted">${AI_DISCLAIMER}</p>`;
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
    <div class="rec" style="margin-top:14px">${d.recommendation}</div>
    ${d.summary?`<details style="margin-top:12px"><summary>Business summary</summary><p class="muted">${d.summary}</p></details>`:''}</section>`));

  // research assistant summary (Buy/Hold/Avoid)
  const rv=d.research, vc=rv.verdict==='Buy'?'v-under':rv.verdict==='Avoid'?'v-over':'v-fair';
  out.append($(`<section class="glass"><h2>Research summary</h2>
    <div class="grid two"><div><h3>Strengths</h3>${(rv.strengths||[]).length?rv.strengths.map(x=>`<div class="flag f-good">${x}</div>`).join(''):'<div class="muted">None detected.</div>'}</div>
    <div><h3>Risks</h3>${(rv.risks||[]).length?rv.risks.map(x=>`<div class="flag f-bad">${x}</div>`).join(''):'<div class="muted">None detected.</div>'}</div></div>
    <div style="margin-top:10px">Verdict: <span class="verdict ${vc}">${rv.verdict}</span> <span class="muted">— ${rv.why}</span></div></section>`));

  // AI analyst — separate, collapsible dropdown so your manual analysis stands alone
  out.append($(`<section class="glass"><details><summary style="font-size:18px;font-weight:600;cursor:pointer">AI analyst — opinion <span class="muted" style="font-weight:400">(optional · click to expand)</span></summary>
    <div style="margin-top:10px">
    ${window.AI_ON?`<p class="muted">Reasons over the computed numbers above — it won't invent figures. ${AI_DISCLAIMER}</p>
      <div style="display:flex;flex-wrap:wrap;gap:6px">
        <button class="dl" id="ai-go" style="margin:0">Decision</button>
        <button class="dl" id="ai-bear" style="margin:0">Bear case</button>
        <button class="dl" id="ai-news" style="margin:0">News digest</button>
        <button class="dl" id="ai-best" style="margin:0">Best in sector</button></div>
      <div id="ai-out" style="margin-top:10px"></div>
      <div class="ac" style="max-width:560px;margin-top:10px"><label>Ask a finance follow-up about this stock</label>
        <div style="display:flex;gap:8px"><input id="ai-q" placeholder='e.g. "is the debt a worry?", "value it for a 5-year hold"'><button class="dl" id="ai-ask" style="margin:0">Ask</button></div></div>
      <div id="ai-chat" style="margin-top:8px"></div>
      <hr style="border:0;border-top:1px solid var(--line);margin:14px 0">
      <h3>AI compare with another stock</h3>
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
        cc.innerHTML='<div class="flag" style="background:rgba(110,168,254,.1)"><b>You:</b> '+esc(q)+'</div>'+(r.text?`<div class="flag f-good" style="white-space:pre-wrap">${esc(r.text)}</div><p class="muted">${AI_DISCLAIMER}</p>`:`<div class="muted">${esc(r.error||'No answer.')}</div>`);
      }catch(e){cc.innerHTML='<p class="muted">AI call failed.</p>';}};
    let cmpB=null;acWire('ai-cmp','ai-cmpbox',sym=>{cmpB=sym;el('ai-cmp').value=sym.replace('.NS','').replace('.BO','');});
    el('ai-cmp-go').onclick=async()=>{const b=cmpB||el('ai-cmp').value.trim().toUpperCase();if(!b){alert('Pick a second company.');return;}
      const o3=el('ai-cmp-out');o3.innerHTML='<div class="spin"></div><p class="muted" style="text-align:center">Evaluating both & comparing…</p>';
      try{const r=await(await fetch(`/ai_compare?a=${encodeURIComponent(d.ticker)}&b=${encodeURIComponent(b)}`)).json();
        o3.innerHTML=r.text?`<div class="rec" style="white-space:pre-wrap;line-height:1.55">${esc(r.text)}</div><p class="muted">${AI_DISCLAIMER}</p>`:`<p class="muted">${esc(r.error||'No response.')}</p>`;
      }catch(e){o3.innerHTML='<p class="muted">Compare failed.</p>';}};
  }

  // Business model — collapsible (summary + revenue-allocation donut + standout metrics)
  const bm=d.business_model;
  const standout=[];
  if(d.quality){if((d.quality.roce||0)>=18)standout.push('High capital efficiency (ROCE '+d.quality.roce+'%)');
    if((d.quality.fcf_yield||0)>=4)standout.push('Strong cash generation (FCF yield '+d.quality.fcf_yield+'%)');
    if(d.quality.piotroski!=null&&d.quality.piotroski>=7)standout.push('Top-tier fundamental quality (Piotroski '+d.quality.piotroski+'/'+d.quality.piotroski_max+')');}
  const npmR=d.ratios&&d.ratios['Net/PAT margin %']?d.ratios['Net/PAT margin %'].value:null;
  if(npmR!=null&&npmR>=15)standout.push('Healthy net margin ('+npmR+'%)');
  out.append($(`<section class="glass"><details id="bm-det" open><summary style="font-size:18px;font-weight:600;cursor:pointer">Business model — what it does &amp; how it earns</summary>
    <div style="margin-top:10px">
      ${d.summary?`<p class="muted" style="line-height:1.6">${esc(d.summary)}</p>`:'<p class="muted">No business summary available from the data feed.</p>'}
      ${bm?`<div class="grid two" style="margin-top:12px;align-items:center">
        <div><canvas id="cBM" height="180"></canvas><div class="muted" style="text-align:center">Where each ₹100 of revenue goes</div></div>
        <div><h3 style="margin-top:0">Revenue allocation</h3>${bm.slices.map(s=>`<div class="kv">${esc(s.label)}: <b>${s.pct}%</b></div>`).join('')}<div class="kv muted" style="margin-top:6px;font-size:12px">${esc(bm.note)}</div></div></div>`:''}
      ${standout.length?`<h3>What makes it stand out</h3>${standout.map(s=>`<div class="flag f-good">${esc(s)}</div>`).join('')}`:''}
    </div></details></section>`));
  if(bm){const det=el('bm-det');const draw=()=>{if(!det._d){det._d=1;drawDonut('cBM',bm.slices);}};det.addEventListener('toggle',()=>{if(det.open)draw();});if(det.open)draw();}

  // Management & MD&A — collapsible
  out.append($(`<section class="glass"><details><summary style="font-size:18px;font-weight:600;cursor:pointer">Management &amp; MD&amp;A</summary>
    <div style="margin-top:10px">
      ${(d.officers&&d.officers.length)?`<h3 style="margin-top:0">Key people</h3><table><tr><th>Name</th><th>Title</th><th>Age</th><th>Pay</th></tr>${d.officers.map(o=>`<tr><td>${esc(o.name||'')}</td><td>${esc(o.title||'')}</td><td>${o.age||'—'}</td><td>${o.pay?cur+fmt(o.pay):'—'}</td></tr>`).join('')}</table>`:'<p class="muted">Management roster not in the data feed — use the primary sources below.</p>'}
      <p class="muted" style="margin-top:8px">Primary sources: ${(d.references||[]).map(r=>`<a href="${safeUrl(r.url)}" target="_blank" rel="noopener noreferrer">${esc(r.label.split(' —')[0].split(' (')[0])}</a>`).join(' · ')}</p>
      ${window.AI_ON?`<button class="dl" id="ai-mda">Generate management discussion &amp; analysis (MD&amp;A)</button><div id="ai-mda-out" style="margin-top:8px"></div>`:'<p class="muted">Enable the AI analyst to auto-write an MD&A here so you need not read the full annual report.</p>'}
    </div></details></section>`));
  if(window.AI_ON&&el('ai-mda'))el('ai-mda').onclick=()=>aiPost('/ai_analyst',{mode:'mda',context:aiContext(d)},'ai-mda-out','Writing the MD&A…');

  // institutional quality & solvency highlight
  const q=d.quality||{};
  if(q.piotroski!=null||q.altman_z!=null||q.roce!=null||q.ev_ebitda!=null){
    const zc=q.altman_z==null?'':q.altman_z>=3?'pos':q.altman_z<1.81?'neg':'';
    const fc=q.piotroski==null?'':q.piotroski>=7?'pos':q.piotroski<=3?'neg':'';
    out.append($(`<section class="glass"><h2>Institutional quality &amp; solvency <span class="muted">(from the statements)</span></h2>
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
  out.append($(`<section class="glass" style="padding:12px 18px"><b>Export to Excel/CSV:</b>
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
    <p class="muted" style="margin-top:6px">Assumptions used → FCF ${money(cur,val.fcf,null)} (${val.fcf_source||'n/a'}) · growth <b>${val.growth_used_pct}%/yr</b> · <b>discount rate ${val.discount_pct}%</b> (set by your Risk appetite: conservative 13% · medium 12% · aggressive 11%) · terminal growth <b>3%</b> · horizon <b>${val.proj_years} years</b>. Both DCF and reverse-DCF use this same discount rate.</p>
    <details style="margin-top:6px"><summary class="muted" style="cursor:pointer;font-weight:600">Adjust assumptions &amp; recompute — use your own numbers</summary>
      <div class="panel" style="margin-top:10px">
        <div><label>Growth %/yr <span class="tip" data-tip="How fast you believe free cash flow will grow each year over the horizon.">i</span></label><input id="dcf-g" type="number" step="0.5" value="${val.growth_used_pct}"></div>
        <div><label>Discount rate % <span class="tip" data-tip="Your required annual return (cost of capital). Higher discount = lower fair value. India equity: typically 11-14%.">i</span></label><input id="dcf-r" type="number" step="0.5" value="${val.discount_pct}"></div>
        <div><label>Terminal growth % <span class="tip" data-tip="Growth forever after the horizon. Keep at/below long-run nominal GDP (~3-5%); must be below the discount rate.">i</span></label><input id="dcf-tg" type="number" step="0.5" value="3"></div>
        <div><label>Years <span class="tip" data-tip="How many years of explicit FCF projection before the terminal value.">i</span></label><input id="dcf-y" type="number" min="1" max="30" value="${val.proj_years}"></div>
        <div><label>Free cash flow (${cur}) <span class="tip" data-tip="Starting annual FCF in absolute currency units. Override it if you disagree with the reported figure.">i</span></label><input id="dcf-fcf" type="number" value="${val.fcf==null?'':Math.round(val.fcf)}"></div>
        <button id="dcf-reset" style="background:#0e1422;color:var(--acc);border:1px solid var(--line)">Reset to defaults</button>
      </div>
      <div id="dcf-custom-out" style="margin-top:8px"><p class="muted">Change any value above — the fair value, margin of safety, verdict and reverse-DCF recompute instantly.</p></div>
    </details>
    ${val.scenarios&&Object.keys(val.scenarios).length?`<h3>Scenario DCF — bear / base / bull range</h3><div class="grid cards">
      ${Object.entries(val.scenarios).map(([k,s])=>`<div class="chip">${k}<b>${money(cur,s.fair_value,null)}</b><span class="muted">${s.mos_pct>=0?'+':''}${s.mos_pct}% · g ${s.growth_pct}%, disc ${s.discount_pct}%</span></div>`).join('')}</div>
      <p class="muted">A value range, not a single point — the honest way to quote intrinsic value.</p>`:''}
    <h3>3) Industry P/E — local vs global <span class="muted">(macro re-rating check)</span></h3>
    <div id="indpe" class="muted">Loading industry P/E…</div>
    <p class="muted" style="margin-top:8px">FCF used ${money(cur,val.fcf,null)} (${val.fcf_source||'n/a'}). A high P/E isn't automatically "overvalued" — if growth, quality and the global industry support it, paying up can be justified.</p></section>`));
  loadIndustryPE(d.ticker,d.sector);

  // user-adjustable DCF: recompute fair value / MoS / verdict / reverse-DCF live, client-side (same math as the backend)
  const dcfDef={g:val.growth_used_pct,r:val.discount_pct,tg:3,y:val.proj_years,fcf:val.fcf==null?'':Math.round(val.fcf)};
  function dcfJS(fcf,g,r,years,tg){if(!fcf||fcf<=0||r<=tg)return null;let pv=0,cf=fcf;for(let t=1;t<=years;t++){cf*=1+g;pv+=cf/Math.pow(1+r,t);}return pv+(cf*(1+tg)/(r-tg))/Math.pow(1+r,years);}
  function revDcfJS(fcf,mktcap,r,years,tg){if(!fcf||fcf<=0||!mktcap||r<=tg)return null;let lo=-0.10,hi=0.50;for(let i=0;i<60;i++){const mid=(lo+hi)/2;const v=dcfJS(fcf,mid,r,years,tg);if(v==null)return null;if(v<mktcap)lo=mid;else hi=mid;}return Math.round((lo+hi)/2*1000)/10;}
  function recomputeDCF(){const box=el('dcf-custom-out');if(!box)return;
    const g=(+el('dcf-g').value||0)/100,r=(+el('dcf-r').value||0)/100,tg=(+el('dcf-tg').value||0)/100;
    const y=Math.max(1,Math.min(30,Math.round(+el('dcf-y').value||8)));
    const fcf=+el('dcf-fcf').value||null;
    if(!fcf||fcf<=0){box.innerHTML='<p class="muted">A DCF needs a positive free cash flow — enter one above to recompute.</p>';return;}
    if(r<=tg){box.innerHTML='<p class="muted">Discount rate must be HIGHER than terminal growth (else the terminal value is infinite). Raise the discount rate or lower terminal growth.</p>';return;}
    const fair=dcfJS(fcf,g,r,y,tg),mc=val.current_mktcap;
    const mos=(fair&&mc)?Math.round((fair/mc-1)*1000)/10:null;
    const verdict=mos==null?'—':(mos>20?'Undervalued':(mos<-20?'Overvalued':'Fairly valued'));
    const ig=revDcfJS(fcf,mc,r,y,tg);
    box.innerHTML=`<div class="grid cards">
      <div class="chip">Your fair value<b>${money(cur,fair,null)}</b><span class="muted">vs mkt cap ${money(cur,mc,null)}</span></div>
      <div class="chip">Your margin of safety<b>${mos==null?'—':mos+'%'}</b></div>
      <div class="chip">Your verdict<b><span class="verdict ${vcls(verdict)}">${verdict}</span></b></div>
      <div class="chip">Implied growth at your discount<b>${ig==null?'—':ig+'%'}</b><span class="muted">priced-in</span></div></div>
      <p class="muted">Live with YOUR inputs: fair value = Σ FCF×(1+g)^t ÷ (1+r)^t for t=1..${y}, plus terminal FCF×(1+g∞) ÷ (r−g∞) discounted back. The rest of the report still uses the default assumptions shown above.</p>`;}
  ['dcf-g','dcf-r','dcf-tg','dcf-y','dcf-fcf'].forEach(id=>{if(el(id))el(id).oninput=recomputeDCF;});
  if(el('dcf-reset'))el('dcf-reset').onclick=()=>{el('dcf-g').value=dcfDef.g;el('dcf-r').value=dcfDef.r;el('dcf-tg').value=dcfDef.tg;el('dcf-y').value=dcfDef.y;el('dcf-fcf').value=dcfDef.fcf;recomputeDCF();};

  let rt='<section class="glass"><h2>Ratio analysis <span class="muted">— hover for the definition'+(window.AI_ON?'; click for an AI explanation for THIS company':'')+'</span></h2><table><tr><th>Metric</th><th>Value</th><th style="text-align:left">What it means · benchmark</th></tr>';
  for(const[k,o]of Object.entries(d.ratios)){const u=o.unit||'';
    const disp=o.value==null?'—':(o.inr!==undefined?money(cur,o.value,o.inr):(u==='%'?o.value+'%':u==='x'?o.value+'x':o.value));
    const meaning=o.text?`<span class="dot d-${o.rating}"></span>${o.text}${o.bench?' <span class="muted">('+o.bench+')</span>':''}`:'<span class="muted">—</span>';
    const aiex=window.AI_ON?` <a href="#" class="aiex" data-m="${esc(k)}" data-v="${o.value==null?'':o.value}" title="AI explain for this company" style="text-decoration:none"></a>`:'';
    rt+=`<tr><td>${tip(k)}${aiex}</td><td>${disp}</td><td style="text-align:left;font-size:12px">${meaning}</td></tr>`;}
  out.append($(rt+'</table><div id="aiex-out"></div><p class="muted">🟢 good · 🟡 ok · 🔴 watch. Benchmarks are general rules of thumb; compare within the same sector.</p></section>'));
  if(window.AI_ON){const ctx2=aiContext(d);document.querySelectorAll('.aiex').forEach(a=>a.onclick=async e=>{e.preventDefault();
    const m=a.dataset.m,v=a.dataset.v,box=el('aiex-out');box.innerHTML=`<div class="flag" style="background:rgba(110,168,254,.1)"><div class="spin" style="margin:6px auto"></div><div class="muted" style="text-align:center">AI explaining ${esc(m)}…</div></div>`;
    try{const r=await(await fetch('/ai_chat',{method:'POST',headers:{'content-type':'application/json'},
      body:JSON.stringify({q:`Explain the metric "${m}" (value: ${v||'n/a'}) specifically for ${d.name}: what it measures, whether this level is good/bad for THIS company and sector, and what it implies — 3-4 sentences.`,context:ctx2})})).json();
      box.innerHTML=r.text?`<div class="flag f-good" style="white-space:pre-wrap"><b>${esc(m)}:</b> ${esc(r.text)}</div><p class="muted">${AI_DISCLAIMER}</p>`:`<p class="muted">${esc(r.error||'No answer.')}</p>`;
    }catch(err){box.innerHTML='<p class="muted">AI call failed.</p>';}});}

  out.append($(`<section class="glass"><h2>Technical analysis</h2><div class="grid two">${tf('Daily',d.technical.daily)}${tf('Weekly',d.technical.weekly)}</div>
    <p class="muted">Benchmarks — RSI(14): below 30 = oversold 🟢, 30–70 = neutral, above 70 = overbought 🔴. EMA200: price above = long-term uptrend; below = downtrend.</p>
    <div class="grid two" style="margin-top:8px"><div><canvas id="cD" height="160"></canvas><div class="muted" style="text-align:center">Daily vs EMA200</div></div>
    <div><canvas id="cW" height="160"></canvas><div class="muted" style="text-align:center">Weekly vs EMA200</div></div></div></section>`));
  drawLine('cD',d.technical.daily);drawLine('cW',d.technical.weekly);

  // advanced CMT technicals (indicators + pattern detection, daily/weekly switch)
  out.append($(`<section class="glass"><h2>Advanced technicals (CMT) — indicators &amp; chart patterns</h2>
    <div class="seg" id="tfseg"><button class="on" data-tf="daily">Daily</button><button data-tf="weekly">Weekly</button></div>
    <div id="taout" style="margin-top:12px"><div class="spin"></div></div></section>`));
  document.getElementById('tfseg').querySelectorAll('button').forEach(b=>b.onclick=()=>{
    document.getElementById('tfseg').querySelectorAll('button').forEach(x=>x.classList.remove('on'));b.classList.add('on');loadTechnicals(d.ticker,d.currency,b.dataset.tf);});
  loadTechnicals(d.ticker,d.currency,'daily');

  // price range & period stats (high/low/avg/change over 1M…Max/ATH)
  const RNGS=[['1mo','1M'],['6mo','6M'],['1y','1Y'],['5y','5Y'],['10y','10Y'],['max','Max / ATH']];
  out.append($(`<section class="glass"><h2>Price range &amp; period stats</h2>
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
  let sh=`<section class="glass"><h2>Sector analysis — ${sa.name||''}</h2><p class="muted">Recent sector news sentiment: <span class="pos">${sa.tally.pos} positive</span> · <span class="neg">${sa.tally.neg} negative</span>. ${sa.note}</p><div class="news">`;
  sh+=sa.news.length?sa.news.map(n=>`<div class="flag ${n.tone=='neg'?'f-bad':n.tone=='pos'?'f-good':''}"><a href="${safeUrl(n.link)}" target="_blank" rel="noopener noreferrer">${esc(n.title)}</a> <small class="muted">${esc(n.source||"")}${n.source?" · ":""}${n.date}</small></div>`).join(''):'<div class="muted">No sector headlines.</div>';
  out.append($(sh+'</div></section>'));

  out.append($(`<section class="glass"><h2>Peer comparison <span class="muted">— ${d.peer_sector||''}</span></h2><div id="peerbox" class="muted">Loading peers…</div></section>`));
  if(d.peers&&d.peers.length)loadPeers(d.ticker,d.peers,cur);else el('peerbox').textContent='No mapped peers yet.';

  // allocation + portfolio plan
  const a=d.allocation,p=d.portfolio_plan;
  out.append($(`<section class="glass"><h2>Allocation &amp; portfolio plan</h2>
    ${a.shares!=null?`<div class="grid cards"><div class="chip">Suggested weight<b>${a.suggested_weight_pct}%</b></div>
      <div class="chip">Shares to buy<b>${a.shares}</b></div><div class="chip">Amount<b>${cur}${fmt(a.amount)}</b></div>
      <div class="chip">Cash left<b>${cur}${fmt(a.cash_left)}</b></div></div><p class="muted">${a.note}</p>`:`<p class="muted">${a.note}</p>`}
    ${p.method?`<h3>Deployment method: ${p.method}</h3><p class="muted">${p.why}</p>
      <div class="grid cards"><div class="chip">Budget for this stock<b>₹${fmt(p.this_stock_budget)}</b></div>
      <div class="chip">Tranches<b>${p.tranches}</b></div><div class="chip">Per tranche<b>₹${fmt(p.per_tranche)}</b></div></div>
      <p class="muted">${p.note}</p>`:`<p class="muted">${p.note||''}</p>`}</section>`));

  // corporate actions + institutions
  let ca='<section class="glass"><div class="grid two"><div><h2>Corporate actions</h2>';
  ca+=d.corporate_actions.length?d.corporate_actions.map(c=>`<div class="kv"><b>${c.type}</b> — ${c.date}${c.value?' ('+c.value+')':''}</div>`).join(''):'<div class="muted">None in Yahoo data.</div>';
  ca+=`</div><div><h2>Institutional activity</h2><p>Institutional holding: <b>${d.institutional.pct||'—'}</b></p>`;
  if(d.institutional.holders.length){ca+='<table><tr><th>Top holders</th><th>%</th></tr>';d.institutional.holders.forEach(h=>ca+=`<tr><td>${h.name}</td><td>${h.pct.toFixed(2)}%</td></tr>`);ca+='</table>';}
  out.append($(ca+`<p class="muted">${d.institutional.note}</p></div></div></section>`));

  let nh='<section class="glass"><h2>Live news</h2><div class="news">';
  nh+=d.news.length?d.news.map(n=>`<div class="flag ${n.tone=='neg'?'f-bad':n.tone=='pos'?'f-good':''}"><a href="${safeUrl(n.link)}" target="_blank" rel="noopener noreferrer">${esc(n.title)}</a> <small class="muted">${esc(n.source||"")}${n.source?" · ":""}${n.date}</small></div>`).join(''):'<div class="muted">No headlines.</div>';
  out.append($(nh+'</div><p class="muted">Live from Google News (14d). Tone is a keyword heuristic — read the source.</p></section>'));

  // proof / methodology
  const m=d.methodology;
  let pf='<section class="glass"><h2>How this was calculated (proof)</h2>';
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
function drawDonut(id,slices){const lbls=slices.map(s=>s.label),vals=slices.map(s=>Math.abs(s.pct));
  charts.push(new Chart(el(id),{type:'doughnut',data:{labels:lbls,datasets:[{data:vals,backgroundColor:['#ff6b6b','#ffd166','#b39bff','#6ea8fe','#39d98a','#5ad1c8','#f08fc0']}]},
    options:{plugins:{legend:{position:'right',labels:{color:'#8b97ad',boxWidth:10,font:{size:11}}},tooltip:{callbacks:{label:ctx=>ctx.label+': '+ctx.parsed+'%'}}}}}));}
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
  h+=`<div style="margin-top:12px"><canvas id="cTA" height="150"></canvas><div class="muted" style="text-align:center">Close + SMA50/200 + Bollinger bands; marks pattern swing points</div></div>`;
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
  let h=`<section class="glass"><h2>Model portfolios — sector allocation</h2><p class="muted">How capital is split across <b>sectors</b> for each risk profile (equity only, ₹${fmt(cap)}). Pick a ranking, then click any sector to load strong companies. Templates, not live-trend forecasts.</p>
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
  let h=`<section class="glass"><h2>My portfolio — live</h2>
    <div class="panel"><div class="ac"><label>Ticker</label><input id="t-sym" placeholder="Type e.g. HAL, Apple…" autocomplete="off"><div class="acbox" id="t-acbox"></div></div>
    <div><label>Qty</label><input id="t-qty" type="number" min="0"></div><div><label>Buy price (avg)</label><input id="t-buy" type="number" min="0"></div>
    <button id="t-add">Add holding</button></div>
    <div class="panel" style="margin-top:10px"><div><label>Total capital (₹) <span class="tip" data-tip="Your overall investable corpus. Used as the base for position-sizing suggestions.">ⓘ</span></label><input id="t-cap" type="number" min="0" value="${localStorage.getItem(CKEY)||200000}"></div>
    <div><label>Extra cash to deploy (₹) <span class="tip" data-tip="New money you want to add right now. The rebalancer routes it to the strongest / below-buy names.">ⓘ</span></label><input id="t-extra" type="number" min="0" value="0"></div>
    <div><label>Investment horizon <span class="tip" data-tip="How long you plan to hold. Sets the SL/TP window and how patiently weak names are treated.">ⓘ</span></label><select id="t-hz"><option value="short">Short (≤3y)</option><option value="medium" selected>Medium (3–7y)</option><option value="long">Long (7y+)</option></select></div></div>
    <div style="margin:10px 0"><button id="t-eval" class="full" style="margin-bottom:8px">Evaluate &amp; rebalance my portfolio</button>
    <button id="t-opt" style="background:#0e1422;color:var(--acc);border:1px solid var(--line)">Quant analytics</button>
    <button id="t-csv" style="background:#0e1422;color:var(--acc);border:1px solid var(--line)">Export CSV</button>
    <button id="t-refresh" style="background:#0e1422;color:var(--acc);border:1px solid var(--line)">Refresh now</button></div>
    <div class="muted" style="font-size:12px;margin-bottom:8px">
      <b>Evaluate &amp; rebalance</b>: rates every holding (fundamentals + technicals), final verdict, SL/TP, what to buy/sell/switch. ·
      <b>Quant analytics</b>: Sharpe, VaR, Monte-Carlo, efficient frontier, correlation. ·
      <b>Export CSV</b>: download your holdings + P&L. ·
      <b>Refresh</b>: re-pull live prices (auto every 60s).</div>
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

async function evalPort(){const p=loadPort();if(p.length<1){el('t-eval-out').innerHTML='<section class="glass">Add holdings first.</section>';el('t-eval-out').scrollIntoView({behavior:'smooth',block:'start'});return;}
  el('t-opt-out').innerHTML='';  // switch cleanly between views
  el('t-eval-out').innerHTML='<section class="glass"><div class="spin"></div><p class="muted" style="text-align:center">Evaluating each holding (fundamentals + technicals) — this takes ~20–40s.</p></section>';
  el('t-eval-out').scrollIntoView({behavior:'smooth',block:'start'});
  try{
    const res=await fetch('/portfolio_eval',{method:'POST',headers:{'content-type':'application/json'},
      body:JSON.stringify({holdings:p,horizon:el('t-hz').value,extra_capital:+el('t-extra').value||0})});
    if(!res.ok){el('t-eval-out').innerHTML=`<section class="glass">Server returned ${res.status}. The free tier may be waking up or busy — wait a few seconds and click again.</section>`;return;}
    const d=await res.json();
    if(d.error){el('t-eval-out').innerHTML=`<section class="glass">${esc(d.error)}</section>`;return;}
    renderEval(d);
  }catch(e){el('t-eval-out').innerHTML='<section class="glass">Could not reach the server (it may be waking from sleep, ~30–60s). Try again.</section>';}}
function vcl(v){return (v==='Accumulate')?'pos':(v==='Exit'||v==='Reduce')?'neg':'';}
function renderEval(d){const o=el('t-eval-out');o.innerHTML='';
  const oc=d.overall_health>=6.5?'v-under':d.overall_health>=5?'v-fair':'v-over';
  o.append($(`<section class="glass"><h2>Portfolio evaluation</h2>
    <div class="grid cards"><div class="chip">Portfolio value<b>₹${fmt(d.total_value)}</b></div>
    <div class="chip">Overall health<b>${d.overall_health}/10</b></div>
    <div class="chip">Verdict<b><span class="verdict ${oc}">${d.overall_verdict}</span></b></div>
    <div class="chip">Blended earnings CAGR<b>${d.portfolio_earnings_cagr==null?'—':d.portfolio_earnings_cagr+'%'}</b><span class="muted">vs NIFTY ~12%</span></div></div></section>`));
  // decisive final verdict (headline + numbered actions with the WHY/HOW for each)
  if(d.final_verdict){const fvh=esc(d.final_verdict.headline).replace(/\*\*(.+?)\*\*/g,'<b>$1</b>');
    o.append($(`<section class="glass"><h2>If I were you — final verdict</h2>
      <div class="rec" style="font-size:15px;line-height:1.6">${fvh}</div>
      <h3>Step by step — what I'd do &amp; why</h3>
      <div>${d.final_verdict.actions.map((a,i)=>`<div class="flag ${/sell|trim|diversify|cut|research/i.test(a.do)?'f-bad':'f-good'}" style="margin:8px 0">
        <div><b>${i+1}. ${esc(a.do)}</b></div><div style="font-size:13px;margin-top:3px" class="muted">${esc(a.why)}</div></div>`).join('')}</div>
      <p class="muted">Each call blends fundamentals (60%) and technicals (40%). A loss-making company can't score "strong" no matter how the chart looks; if a metric is missing the stock is left <b>Not rated</b> rather than guessed.</p></section>`));}
  // AI take on the whole portfolio (on demand)
  if(window.AI_ON){
    o.append($(`<section class="glass"><h2>AI take on my portfolio</h2>
      <button class="dl" id="ai-pf">Ask the AI to review my portfolio</button>
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
  o.append($(`<section class="glass"><h2>How to rebalance</h2>
    <ol style="line-height:1.7;padding-left:20px">${(d.steps||[]).map(s=>`<li>${s}</li>`).join('')}</ol>
    <h3>Right now</h3>${d.plan.map(x=>`<div class="flag ${x.indexOf('⚠')>-1?'f-bad':'f-good'}">${x}</div>`).join('')}</section>`));
  // per-stock: separate fundamentals + technicals sections
  let h='<section class="glass"><h2>Holdings evaluated</h2>';
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
  let sc='<section class="glass"><h2>Sector mix &amp; rotation</h2><div class="grid two"><div><h3>Your sector weights</h3>';
  sc+=d.sectors.map(s=>`<div class="kv">${s.sector}: <b>${s.pct}%</b>${s.pct>30?' <span class="neg">(concentrated)</span>':''}</div>`).join('');
  sc+='</div><div><h3>1-month sector leaders</h3>'+d.sector_rotation.slice(0,6).map(r=>`<div class="kv">${r.sector}: ${r.ret_1m_pct>=0?'<span class="pos">+'+r.ret_1m_pct+'%</span>':'<span class="neg">'+r.ret_1m_pct+'%</span>'}</div>`).join('')+'</div></div>';
  sc+=`<p class="muted">Largest sector ${d.concentration_pct}% of the book; keep any one under ~30% and rotate trims toward the leaders above.</p>`;
  if(d.sector_news&&Object.keys(d.sector_news).length){sc+='<h3>What\'s moving your sectors (news that can affect your horizon)</h3>';
    for(const[sec,news]of Object.entries(d.sector_news)){sc+=`<div style="margin-bottom:8px"><b>${sec}</b>`;
      sc+=(news&&news.length)?news.map(n=>`<div class="flag ${n.tone=='neg'?'f-bad':n.tone=='pos'?'f-good':''}"><a href="${safeUrl(n.link)}" target="_blank" rel="noopener noreferrer">${esc(n.title)}</a> <small class="muted">${esc(n.source||"")}${n.source?" · ":""}${n.date}</small></div>`).join(''):'<div class="muted">No recent headlines.</div>';sc+='</div>';}}
  o.append($(sc+'</section>'));}

async function optimizePort(){const p=loadPort();if(p.length<1){el('t-opt-out').innerHTML='<section class="glass">Add holdings first.</section>';el('t-opt-out').scrollIntoView({behavior:'smooth',block:'start'});return;}
  el('t-eval-out').innerHTML='';  // switch cleanly between views
  el('t-opt-out').innerHTML='<section class="glass"><div class="spin"></div><p class="muted" style="text-align:center">Computing quant analytics (Sharpe, VaR, Monte-Carlo…) — ~15–30s.</p></section>';
  el('t-opt-out').scrollIntoView({behavior:'smooth',block:'start'});
  try{
    const res=await fetch('/optimize',{method:'POST',headers:{'content-type':'application/json'},
      body:JSON.stringify({holdings:p,extra_capital:+el('t-extra').value||0})});
    if(!res.ok){el('t-opt-out').innerHTML=`<section class="glass">Server returned ${res.status}. The free tier may be waking up or busy — wait a few seconds and click again.</section>`;return;}
    const d=await res.json();
    if(d.error){el('t-opt-out').innerHTML=`<section class="glass">${esc(d.error)}</section>`;return;}
    renderOptimize(d);
  }catch(e){el('t-opt-out').innerHTML='<section class="glass">Could not reach the server (it may be waking from sleep, ~30–60s). Try again.</section>';}}
function renderOptimize(d){const m=d.mpt,r=d.risk,bm=d.benchmarks,o=el('t-opt-out');o.innerHTML='';
  const hint=t=>t?`<span class="muted" style="display:block;font-size:11px">${t}</span>`:'';
  // dividend income
  const inc=d.income;
  o.append($(`<section class="glass"><h2>Dividend income from this portfolio</h2>
    <div class="grid cards"><div class="chip">Est. annual dividend<b>₹${fmt(inc.annual_dividend)}</b></div>
    <div class="chip">Portfolio yield<b>${inc.portfolio_yield_pct}%</b>${hint(bm.dividend_yield)}</div>
    <div class="chip">Avg / month<b>₹${fmt(inc.monthly_avg)}</b></div></div>
    <table style="margin-top:8px"><tr><th>Stock</th><th>Yield</th><th>Annual ₹</th></tr>
    ${d.per_stock.map(s=>`<tr><td>${s.sym.replace('.NS','')}</td><td>${s.div_yield_pct}%</td><td>₹${fmt(s.annual_dividend)}</td></tr>`).join('')}</table>
    <p class="muted">${inc.note}</p></section>`));
  o.append($(`<section class="glass"><h2>Portfolio optimization (Modern Portfolio Theory)</h2>
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
    ${window.AI_ON?`<hr style="border:0;border-top:1px solid var(--line);margin:12px 0"><button class="dl" id="ai-risk" style="margin:0">AI risk briefing (plain English)</button><div id="ai-risk-out" style="margin-top:8px"></div>`:''}</section>`));
  if(window.AI_ON&&el('ai-risk')){const rctx={mpt:d.mpt,risk:d.risk,factor_tilt:d.factor_tilt,value:d.value,monte_carlo:d.monte_carlo};
    el('ai-risk').onclick=()=>aiPost('/ai_analyst',{mode:'risk',context:rctx},'ai-risk-out','Briefing the risk…');}
  // backtest vs benchmark
  o.append($(`<section class="glass"><h2>Backtest — portfolio vs ${r.benchmark_name} <span class="muted">(growth of ₹1, 2y)</span></h2>
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
  let st='<section class="glass"><h2>Stress test — "what if the market falls?"</h2><div class="grid cards">';
  for(const[k,v]of Object.entries(d.stress_test))st+=`<div class="chip">${k}<b class="neg">₹${fmt(Math.abs(v.expected_loss))} loss</b><span class="muted">value → ₹${fmt(v.value_after)}</span></div>`;
  o.append($(st+'</div><p class="muted">Expected loss = portfolio beta × index move × value. A 15% NIFTY fall hits a high-beta book harder.</p></section>'));
  // monte carlo + efficient frontier charts
  const mc=d.monte_carlo;
  o.append($(`<section class="glass"><h2>Monte Carlo (1-year, 2000 sims) &amp; efficient frontier</h2>
    <div class="grid cards"><div class="chip">Worst 5% (p5)<b>₹${fmt(mc.p5)}</b></div><div class="chip">Median (p50)<b>₹${fmt(mc.p50)}</b></div>
    <div class="chip">Best 5% (p95)<b>₹${fmt(mc.p95)}</b></div><div class="chip">Chance of loss<b>${mc.prob_loss_pct}%</b></div></div>
    <div class="grid two" style="margin-top:12px"><div><canvas id="cMC" height="170"></canvas><div class="muted" style="text-align:center">Simulated 1y outcomes</div></div>
    <div><canvas id="cEF" height="170"></canvas><div class="muted" style="text-align:center">Efficient frontier (risk vs return)</div></div></div></section>`));
  drawMC('cMC',mc,d.value);drawEF('cEF',d.efficient_frontier);
  // rebalancing
  const ef=d.efficient_frontier.max_sharpe;
  let rb=`<section class="glass"><h2>Rebalancing (quant — max-Sharpe target)</h2>
    <p class="muted">This is the <b>mathematical</b> rebalance: it shifts weights toward the <b>max-Sharpe portfolio</b> — the mix with the best historical return per unit of risk (target: ${ef.ret}% return, ${ef.vol}% vol, Sharpe ${ef.sharpe}). Trades use ₹${fmt(d.value+(d.extra_capital||0))} (value${d.extra_capital?' + ₹'+fmt(d.extra_capital)+' extra':''}). For the <b>fundamentals-driven</b> "what to sell/add and why", see the Evaluate &amp; rebalance section.</p>
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
  let h='<section class="glass"><h2>Portfolio news (live)</h2><div class="news">';
  h+=Array.isArray(news)&&news.length?news.map(n=>`<div class="flag ${n.tone=='neg'?'f-bad':n.tone=='pos'?'f-good':''}"><span class="tag">${esc(n.ticker)}</span> <a href="${safeUrl(n.link)}" target="_blank" rel="noopener noreferrer">${esc(n.title)}</a> <small class="muted">${esc(n.source||"")}${n.source?" · ":""}${n.date}</small></div>`).join(''):'<div class="muted">No headlines.</div>';
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
  let h=`<section class="glass"><h2>Market dashboard</h2><div class="grid cards">${grid(d.market)}</div>
    <h3>Commodities · rates · currency</h3><div class="grid cards">${grid(d.macro)}</div></section>`;
  h+=`<section class="glass"><h2>Sector rotation <span class="muted">(1-month return, leaders first)</span></h2><table><tr><th>Sector</th><th>1M return</th></tr>`;
  d.sector_rotation.forEach(s=>h+=`<tr><td>${s.sector}</td><td>${s.ret_1m_pct>=0?'<span class="pos">+'+s.ret_1m_pct+'%</span>':'<span class="neg">'+s.ret_1m_pct+'%</span>'}</td></tr>`);
  h+='</table></section>';
  h+='<section class="glass"><h2>🏦 US macro (FRED)</h2>';
  if(d.fred.enabled){h+='<div class="grid cards">'+Object.entries(d.fred.data).map(([k,v])=>`<div class="chip">${k}<b>${v.value}</b><span class="muted">${v.date}</span></div>`).join('')+'</div>';}
  else h+=`<p class="muted">${d.fred.note}</p>`;
  h+='</section><div class="disc">Indices/commodities live from Yahoo Finance. Refreshes when you open this tab.</div>';
  el('m-markets').innerHTML=h;}

// ---- watchlists ----
async function renderWatch(){const lists=['Buffett (quality)','High ROE (ROCE proxy)','Deep Value','Small-cap compounders'];
  el('m-watch').innerHTML=`<div id="ww-watch"></div><section class="glass"><h2>Curated screens</h2><p class="muted">Pre-built pools screened live and ranked. Click one to load (takes a few seconds — it fetches fundamentals).</p>
    <div>${lists.map(n=>`<button class="wlbtn" data-n="${n}" style="margin:4px;background:#0e1422;color:var(--acc);border:1px solid var(--line)">${n}</button>`).join('')}</div><div id="wl-out"></div></section>`;
  renderMyWatch(el('ww-watch'),'ww_');
  el('m-watch').querySelectorAll('.wlbtn').forEach(b=>b.onclick=async()=>{el('wl-out').innerHTML='<div class="spin"></div>';
    const d=await(await fetch('/screen?type='+encodeURIComponent(b.dataset.n))).json();
    if(d.error){el('wl-out').innerHTML=`<p class="muted">${d.error}</p>`;return;}
    let t=`<h3>${d.name} — ranked by ${d.tilt}</h3><table><tr><th>Company</th><th>Quality/10</th><th>P/E</th><th>P/B</th><th>ROE%</th><th>Div%</th><th>6m%</th><th>Beta</th><th>Tags</th></tr>`;
    d.rows.forEach(r=>t+=`<tr><td>${r.name||r.ticker} <span class="muted">${r.ticker.replace('.NS','')}</span></td><td>${r.quality??'—'}</td><td>${fmt(r.pe)}</td><td>${fmt(r.pb)}</td><td>${fmt(r.roe)}</td><td>${fmt(r.div)}</td><td>${fmt(r.momentum_6m)}</td><td>${fmt(r.beta)}</td><td>${(r.tags||[]).map(x=>`<span class="tag">${x}</span>`).join('')}</td></tr>`);
    el('wl-out').innerHTML=t+'</table><p class="muted">"High ROE" stands in for ROCE (not in free Yahoo data).</p>';});}
