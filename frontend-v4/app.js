import {trapDialogTab} from './dialog-focus.js';

const main=document.querySelector('#main');
const notice=document.querySelector('#notice');
const scanButton=document.querySelector('#scan-button');
const scanState=document.querySelector('#scan-state');
const reviewCount=document.querySelector('#review-count');
const runtimeMode=document.querySelector('#runtime-mode');
const crumbs=document.querySelector('#crumbs');
const overrideDialog=document.querySelector('#override-dialog');
const transportNote=document.querySelector('#transport-note');

const state={series:null,overview:null,settings:null,query:'',filter:'all',language:'all',sort:'attention',mutationsAllowed:null,scanRunning:false};
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const num=v=>new Intl.NumberFormat('it-IT').format(v??0);
const dt=v=>v?new Intl.DateTimeFormat('it-IT',{dateStyle:'short',timeStyle:'short'}).format(new Date(v)):'—';
const labels={auto:'Auto',manual:'Manuale',review:'Review',needs_review:'Review',waiting:'Waiting',airing:'Airing',unavailable:'Non disponibile',excluded:'Esclusa',rejected:'Rifiutata'};
const badStates=new Set(['review','needs_review','unavailable','rejected']);

async function api(path,options={}){
  const r=await fetch('/api/v4'+path,{...options,headers:{...(options.body?{'Content-Type':'application/json'}:{}),...(options.headers||{})}});
  const p=await r.json();
  if(typeof p?.meta?.mutations_allowed==='boolean'){
    state.mutationsAllowed=p.meta.mutations_allowed;syncMutationAccess();
  }
  if(!r.ok)throw new Error(p?.error?.message||'Operazione non riuscita');
  return p.data;
}
function syncMutationAccess(){
  const readOnly=state.mutationsAllowed===false;
  transportNote.hidden=!readOnly;
  scanButton.disabled=state.scanRunning||state.mutationsAllowed!==true;
  scanButton.title=readOnly?'Le modifiche richiedono accesso loopback autenticato.':'';
}
function readOnlyBanner(){
  return state.mutationsAllowed===false?'<div class="info-banner" role="note">Accesso proxy in sola lettura. Scansioni, override e modifiche alle impostazioni richiedono accesso loopback autenticato.</div>':'';
}
function inlineError(target,message=''){
  target.textContent=message;target.hidden=!message;
}
function toast(message,error=false){
  notice.hidden=false;notice.className='notice'+(error?' error':'');notice.setAttribute('role',error?'alert':'status');notice.setAttribute('aria-live',error?'assertive':'polite');notice.textContent=message;
  clearTimeout(toast.t);toast.t=setTimeout(()=>notice.hidden=true,4200);
}
function route(){
  const raw=location.hash.replace(/^#\/?/,'')||'series';
  const [path,query='']=raw.split('?'),parts=path.split('/').filter(Boolean),params=new URLSearchParams(query);
  return {page:parts[0]||'series',id:parts[1]||null,season:Number(params.get('season'))||null};
}
function setNav(page,title='Serie'){
  crumbs.textContent=title;
  document.querySelectorAll('[data-nav]').forEach(a=>{
    if(a.dataset.nav===page)a.setAttribute('aria-current','page');
    else a.removeAttribute('aria-current');
  });
}
function badge(status){
  return '<span class="status '+esc(status||'neutral')+'"><i aria-hidden="true"></i>'+esc(labels[status]||status||'—')+'</span>';
}
function audioBadge(audio){
  const v=String(audio||'').toUpperCase();
  return '<span class="audio '+(v==='DUB'?'dub':v==='SUB'?'sub':'')+'">'+esc(v||'—')+'</span>';
}
function coverage(mapped,total){
  const pct=total?Math.min(100,Math.round((mapped/total)*100)):0;
  return '<span class="coverage" aria-label="'+num(mapped)+' episodi mappati su '+num(total)+'"><span><b>'+num(mapped)+'</b>/'+num(total)+'</span><i aria-hidden="true"><u style="width:'+pct+'%"></u></i></span>';
}
function sourceName(s){
  const names=s.source_names||[];
  if(!names.length)return '—';
  return names.length===1?names[0]:names[0]+' +'+(names.length-1);
}
async function loadSeriesPages(){
  const limit=100,items=[];
  let offset=0,total=0;
  do{
    const page=await api('/series?limit='+limit+'&offset='+offset);
    total=page.total;items.push(...page.items);offset+=page.items.length;
    if(!page.items.length&&offset<total)throw new Error('Paginazione serie non valida');
    if(offset>10000)throw new Error('Libreria troppo grande per questa vista');
  }while(offset<total);
  return {items,total,offset:0,limit};
}
async function loadCore(force=false){
  if(force||!state.series)state.series=await loadSeriesPages();
  if(force||!state.overview)state.overview=await api('/overview');
  const openReviews=Number(state.overview.open_reviews)||0;
  reviewCount.textContent=openReviews?num(openReviews):'';reviewCount.hidden=openReviews===0;
  runtimeMode.textContent=state.overview.external_writes?'Writer attivo':'Shadow · no writes';
}
function seriesRows(){
  const q=state.query.trim().toLocaleLowerCase('it');
  let rows=state.series.items.filter(s=>{
    const hay=[s.title,...(s.source_names||[])].join(' ').toLocaleLowerCase('it');
    const search=!q||hay.includes(q);
    const filter=state.filter==='all'
      ||(state.filter==='attention'&&s.attention_count>0)
      ||s.status===state.filter
      ||s.seasons.some(x=>x.automation===state.filter);
    const lang=state.language==='all'||(s.audio_modes||[]).includes(state.language);
    return search&&filter&&lang;
  });
  rows=[...rows].sort((a,b)=>{
    if(state.sort==='attention')return (b.attention_count-a.attention_count)||a.title.localeCompare(b.title,'it');
    if(state.sort==='coverage')return (a.coverage_percent-b.coverage_percent)||a.title.localeCompare(b.title,'it');
    if(state.sort==='updated')return String(b.updated_at||'').localeCompare(String(a.updated_at||''))||a.title.localeCompare(b.title,'it');
    return a.title.localeCompare(b.title,'it');
  });
  return rows;
}
function seasonPips(series){
  const description=series.seasons.map(s=>'S'+s.season+' '+(labels[s.automation]||s.automation)).join(', ');
  return '<span class="season-pips" role="img" aria-label="Stati stagioni: '+esc(description)+'">'+series.seasons.map(s=>'<span aria-hidden="true" class="'+esc(s.automation)+'" title="S'+s.season+' · '+esc(labels[s.automation]||s.automation)+'"></span>').join('')+'</span>';
}
function seriesTable(){
  const rows=seriesRows();
  if(!rows.length)return '<div class="empty-state compact"><h2>Nessuna serie</h2><p>Nessun risultato con questi filtri.</p></div>';
  return '<div class="library-table"><table><thead><tr><th>Serie</th><th>Stagioni</th><th>Copertura</th><th>Sorgente</th><th>Audio</th><th>Stato</th><th>Attenzione</th><th>Aggiornato</th><th></th></tr></thead><tbody>'+
    rows.map(row=>'<tr data-open-series="'+row.series_id+'">'+
      '<td class="title-cell"><a href="#/series/'+row.series_id+'"><strong>'+esc(row.title)+'</strong><small>'+num(row.season_count)+' stagioni · '+num(row.episode_count)+' episodi</small></a></td>'+
      '<td>'+seasonPips(row)+'</td>'+
      '<td>'+coverage(row.mapped_episode_count,row.episode_count)+'</td>'+
      '<td class="source-cell" title="'+esc((row.source_names||[]).join(' + '))+'">'+esc(sourceName(row))+'</td>'+
      '<td>'+((row.audio_modes||[]).length?row.audio_modes.map(audioBadge).join(' '):'—')+'</td>'+
      '<td>'+badge(row.status)+'</td>'+
      '<td>'+(row.attention_count?'<span class="attention-count">'+row.attention_count+'</span>':'<span class="ok-mark">✓</span>')+'</td>'+
      '<td class="updated">'+esc(dt(row.updated_at))+'</td><td class="chev" aria-hidden="true">›</td></tr>'
    ).join('')+'</tbody></table></div>';
}
function bindSeriesRows(){
  document.querySelectorAll('[data-open-series]').forEach(row=>{
    row.onclick=e=>{if(!e.target.closest('a,button,input,select,textarea'))location.hash='#/series/'+row.dataset.openSeries;};
  });
}
async function renderSeries(){
  await loadCore();setNav('series','Serie');
  const total=state.series.total,attention=state.series.items.reduce((n,s)=>n+s.attention_count,0);
  main.innerHTML='<header class="page-title"><div><h1>Serie</h1><p>'+num(total)+' anime monitorati · '+(attention?'<b>'+attention+' stagioni richiedono attenzione</b>':'nessuna anomalia')+'</p></div>'+
    '<div class="page-summary"><span><b>'+num(state.series.items.reduce((n,s)=>n+s.auto_ready_seasons,0))+'</b> stagioni auto</span><span><b>'+num(state.overview.current_target_count)+'</b> target</span></div></header>'+
    '<section class="filters" aria-label="Filtri serie"><label class="search"><span class="sr-only">Cerca serie o sorgente</span><span aria-hidden="true">⌕</span><input id="search" type="search" placeholder="Cerca serie o sorgente…" value="'+esc(state.query)+'"></label>'+
    '<label><span class="sr-only">Stato</span><select id="filter"><option value="all">Tutti gli stati</option><option value="attention">Solo attenzione</option><option value="auto">Auto</option><option value="manual">Manuale</option><option value="review">Review</option><option value="waiting">Waiting</option><option value="airing">Airing</option><option value="excluded">Escluse</option></select></label>'+
    '<label><span class="sr-only">Lingua</span><select id="language"><option value="all">Tutte le lingue</option><option value="SUB">SUB</option><option value="DUB">DUB</option></select></label>'+
    '<label class="sort-filter"><span class="sr-only">Ordinamento</span><select id="sort"><option value="attention">Ordina: attenzione</option><option value="title">Ordina: titolo</option><option value="coverage">Ordina: copertura</option><option value="updated">Ordina: aggiornamento</option></select></label></section>'+
    '<div id="series-table">'+seriesTable()+'</div>';
  const search=document.querySelector('#search'),filter=document.querySelector('#filter'),language=document.querySelector('#language'),sort=document.querySelector('#sort');
  filter.value=state.filter;language.value=state.language;sort.value=state.sort;
  const rerender=()=>{document.querySelector('#series-table').innerHTML=seriesTable();bindSeriesRows();};
  search.oninput=()=>{state.query=search.value;rerender();};
  filter.onchange=()=>{state.filter=filter.value;rerender();};
  language.onchange=()=>{state.language=language.value;rerender();};
  sort.onchange=()=>{state.sort=sort.value;rerender();};
  bindSeriesRows();
}
function seasonSummary(season){
  if(season.automation==='auto')return 'Mapping deterministico completo. Nessuna approvazione manuale richiesta.';
  if(season.automation==='manual')return season.effective?.manual_approved?'Override manuale forzato: operativo.':'Override manuale attivo: non operativo finché non lo forzi.';
  if(season.automation==='review')return 'Ambiguità reale: serve controllo manuale.';
  if(season.automation==='waiting')return 'In attesa di una sorgente completa.';
  if(season.automation==='airing')return 'Stagione in corso.';
  if(season.automation==='excluded')return 'Stagione esclusa.';
  return 'Stato: '+season.automation;
}
function seasonSource(season){
  const sources=season.effective?.sources||[];
  const names=[...new Set(sources.map(x=>x.title).filter(Boolean))];
  return names.join(' + ')||'—';
}
function episodeTable(season){
  const eps=season.effective?.episodes||[];
  if(!eps.length)return '<div class="empty-inline">Nessun episodio mappato.</div>';
  return '<div class="episode-table-wrap"><table class="episode-table"><thead><tr><th>Sonarr</th><th>Stato</th><th>Release AnimeWorld</th><th>Episodio sorgente</th><th></th></tr></thead><tbody>'+
    eps.map(ep=>'<tr><td><b>S'+String(season.season).padStart(2,'0')+'E'+String(ep.episode).padStart(2,'0')+'</b></td><td><span class="episode-ok">Mappato</span></td><td>'+esc(ep.source_title||'—')+'</td><td>E'+String(ep.source_episode??'—').padStart(2,'0')+'</td><td>'+(ep.source_url?'<a target="_blank" rel="noopener" href="'+esc(ep.source_url)+'">Apri ↗</a>':'—')+'</td></tr>').join('')+
    '</tbody></table></div>';
}
function evidenceHTML(season){
  const sources=season.automatic?.sources||season.sources||[];
  const bases=season.mapping_bases||[];
  return '<div class="evidence-grid"><div><span>Copertura</span><b>'+num(season.mapped_episode_count)+' / '+num(season.episode_count)+' episodi</b></div>'+
    '<div><span>Crosswalk</span><b>'+esc(bases.join(', ')||'—')+'</b></div>'+
    '<div><span>Preferenza audio</span><b>'+esc(season.audio_preference||'SUB')+'</b></div>'+
    '<div><span>Eseguibile</span><b>'+(season.can_execute?'Sì':'No')+'</b></div></div>'+
    '<div class="source-evidence">'+sources.map(s=>'<article><b>'+esc(s.title)+'</b><span>'+esc(s.premiere_date||s.release_year||'data ignota')+' · '+esc(s.episode_count??'—')+' ep · '+esc(s.reliability||'—')+'</span></article>').join('')+'</div>';
}
function seasonCard(series,season){
  const audio=season.effective?.audio||season.audio_preference||'—';
  const attention='<span class="attention-indicator">'+(season.attention?'<span class="attention-dot" aria-hidden="true"></span><span class="sr-only">Richiede attenzione</span>':'')+'</span>';
  const bodyId='season-body-'+series.series_id+'-'+season.season;
  return '<section class="season-card" data-season="'+season.season+'"><button class="season-head" type="button" aria-expanded="false" aria-controls="'+bodyId+'">'+
    '<span class="season-label">S'+String(season.season).padStart(2,'0')+'</span>'+
    '<span class="season-main"><b>Stagione '+season.season+'</b><small>'+num(season.mapped_episode_count)+' / '+num(season.episode_count)+' episodi</small></span>'+
    '<span class="season-progress">'+coverage(season.mapped_episode_count,season.episode_count)+'</span>'+
    '<span class="season-source" title="'+esc(seasonSource(season))+'">'+esc(seasonSource(season))+'</span>'+
    audioBadge(audio)+badge(season.automation)+attention+'<span class="chev">⌄</span></button>'+
    '<div class="season-body" id="'+bodyId+'" hidden><div class="season-toolbar"><div><p>'+esc(seasonSummary(season))+'</p>'+
    (season.reason_codes?.length?'<small>'+esc(season.reason_codes.join(' · '))+'</small>':'')+'</div>'+
    '<button class="button secondary" data-override="'+season.season+'" '+(state.mutationsAllowed===false?'disabled title="Richiede accesso loopback autenticato"':'')+'>Override stagione</button></div>'+
    episodeTable(season)+'<details class="evidence"><summary>Perché questo mapping?</summary>'+evidenceHTML(season)+'</details></div></section>';
}
async function renderSeriesDetail(id,openSeason=null){
  await loadCore();const detail=await api('/series/'+id);setNav('series','Serie / '+detail.title);
  const auto=detail.seasons.filter(s=>s.auto_ready).length,attention=detail.seasons.filter(s=>s.attention).length;
  main.innerHTML='<a class="back-link" href="#/series">← Tutte le serie</a>'+
    '<header class="series-hero"><div><h1>'+esc(detail.title)+'</h1><p>'+num(detail.season_count)+' stagioni · '+num(detail.mapped_episode_count)+' / '+num(detail.episode_count)+' episodi mappati</p></div>'+
    '<div class="series-hero-meta"><span><b>'+auto+'</b> auto</span><span><b>'+attention+'</b> attenzione</span>'+badge(detail.status)+'</div></header>'+
    readOnlyBanner()+'<div class="season-list">'+detail.seasons.map(s=>seasonCard(detail,s)).join('')+'</div>';
  document.querySelectorAll('.season-head').forEach(btn=>btn.onclick=()=>{
    const card=btn.closest('.season-card'),body=card.querySelector('.season-body'),open=btn.getAttribute('aria-expanded')==='true';
    btn.setAttribute('aria-expanded',String(!open));body.hidden=open;
  });
  document.querySelectorAll('[data-override]').forEach(btn=>btn.onclick=e=>{e.stopPropagation();openOverride(detail,Number(btn.dataset.override));});
  if(openSeason){
    const card=document.querySelector('.season-card[data-season="'+openSeason+'"]');
    if(card){const btn=card.querySelector('.season-head'),body=card.querySelector('.season-body');btn.setAttribute('aria-expanded','true');body.hidden=false;setTimeout(()=>card.scrollIntoView({block:'center'}),0);}
  }
}
function overridePayload(season){
  const o=season.override||{};
  return {source_urls:o.source_urls||[],audio:o.audio||null,ignore_errors:!!o.ignore_errors,manual_approved:!!o.manual_approved,excluded:!!o.excluded,episode_map:o.episode_map||[],note:o.note||''};
}
function formatEpisodeMap(rows){
  return (rows||[]).map(r=>r.sonarr_episode+'='+(r.source_index+1)+':'+r.source_episode).join('\n');
}
function parseEpisodeMap(value,urlCount){
  const lines=value.split(/\r?\n/).map(x=>x.trim()).filter(Boolean),rows=[];
  for(const line of lines){
    const m=line.match(/^(\d+)\s*=\s*(\d+)\s*:\s*(\d+)$/);
    if(!m)throw new Error('Mappa episodi non valida: usa formato 1=1:1');
    const sonarr=Number(m[1]),sourceIndex=Number(m[2])-1,source=Number(m[3]);
    if(sourceIndex<0||sourceIndex>=urlCount)throw new Error('Indice sorgente fuori range nella mappa episodi');
    rows.push({sonarr_episode:sonarr,source_index:sourceIndex,source_episode:source});
  }
  return rows;
}
function openOverride(series,seasonNo){
  if(state.mutationsAllowed!==true){toast('Gli override richiedono accesso loopback autenticato.',true);return;}
  const season=series.seasons.find(s=>s.season===seasonNo),o=overridePayload(season);
  const returnFocus=document.activeElement;
  overrideDialog.innerHTML='<form id="override-form"><header><div><span class="eyebrow">Override manuale</span><h2>'+esc(series.title)+' · S'+String(seasonNo).padStart(2,'0')+'</h2><p>Ha sempre precedenza sull’automatismo di questa stagione.</p></div><button type="button" class="dialog-x" data-close aria-label="Chiudi">×</button></header>'+
    '<div class="dialog-body"><section class="override-section"><h3>Sorgente</h3><label>Link AnimeWorld <small>uno per riga; per una sola sorgente viene mantenuta la numerazione automatica</small><textarea id="ov-urls" rows="4" placeholder="https://www.animeworld.ac/play/...">'+esc((o.source_urls||[]).join('\n'))+'</textarea></label>'+
    '<div class="two-col"><label>Audio<select id="ov-audio"><option value="">Auto / policy V3</option><option value="SUB" '+(o.audio==='SUB'?'selected':'')+'>SUB</option><option value="DUB" '+(o.audio==='DUB'?'selected':'')+'>DUB</option></select></label>'+
    '<label>Nota operatore<textarea id="ov-note" rows="2" placeholder="Perché sto forzando questo mapping">'+esc(o.note||'')+'</textarea></label></div></section>'+
    '<section class="override-section"><h3>Bypass</h3><div class="toggle-list">'+
    '<label><input id="ov-ignore" type="checkbox" '+(o.ignore_errors?'checked':'')+'><span><b>Ignora errori / Review</b><small>Nasconde l’eccezione per questa stagione, ma non abilita da solo il writer.</small></span></label>'+
    '<label><input id="ov-approved" type="checkbox" '+(o.manual_approved?'checked':'')+'><span><b>Forza come operativo</b><small>Consente al writer futuro di usare questo override manuale.</small></span></label>'+
    '<label><input id="ov-excluded" type="checkbox" '+(o.excluded?'checked':'')+'><span><b>Escludi stagione</b><small>AniDown la ignora completamente.</small></span></label></div></section>'+
    '<details class="override-section advanced-map" '+(o.episode_map?.length?'open':'')+'><summary>Mapping episodi avanzato</summary><p>Serve soprattutto con più link. Formato: <code>episodio Sonarr = sorgente : episodio sorgente</code>. Esempio <code>13=2:1</code>.</p><textarea id="ov-map" rows="7" placeholder="1=1:1\n2=1:2\n13=2:1">'+esc(formatEpisodeMap(o.episode_map))+'</textarea></details><p id="override-error" class="form-error" role="alert" hidden></p></div>'+
    '<footer><button type="button" class="button danger ghost" data-reset>Reset override</button><span></span><button type="button" class="button secondary" data-close>Annulla</button><button type="submit" class="button primary">Salva override</button></footer></form>';
  overrideDialog.showModal();
  overrideDialog.onkeydown=e=>trapDialogTab(e,overrideDialog);
  overrideDialog.addEventListener('close',()=>returnFocus?.focus(),{once:true});
  overrideDialog.querySelectorAll('[data-close]').forEach(b=>b.onclick=()=>overrideDialog.close());
  overrideDialog.querySelector('[data-close]').focus();
  overrideDialog.querySelector('[data-reset]').onclick=async()=>{
    if(!confirm('Rimuovere completamente l’override di questa stagione?'))return;
    try{
      await api('/series/'+series.series_id+'/seasons/'+seasonNo+'/override',{method:'DELETE'});
      overrideDialog.close();state.series=null;state.overview=null;toast('Override rimosso');render();
    }catch(err){inlineError(overrideDialog.querySelector('#override-error'),err.message);}
  };
  overrideDialog.querySelector('#override-form').onsubmit=async e=>{
    e.preventDefault();
    try{
      const urls=document.querySelector('#ov-urls').value.split(/\r?\n/).map(x=>x.trim()).filter(Boolean);
      const map=parseEpisodeMap(document.querySelector('#ov-map').value,urls.length);
      if(urls.length>1&&!map.length&&!document.querySelector('#ov-excluded').checked)throw new Error('Con più sorgenti devi indicare il mapping episodi avanzato.');
      const body={source_urls:urls,audio:document.querySelector('#ov-audio').value||null,
        ignore_errors:document.querySelector('#ov-ignore').checked,manual_approved:document.querySelector('#ov-approved').checked,
        excluded:document.querySelector('#ov-excluded').checked,episode_map:map,note:document.querySelector('#ov-note').value};
      await api('/series/'+series.series_id+'/seasons/'+seasonNo+'/override',{method:'PATCH',body:JSON.stringify(body)});
      overrideDialog.close();state.series=null;state.overview=null;toast('Override salvato');render();
    }catch(err){inlineError(overrideDialog.querySelector('#override-error'),err.message);}
  };
}
async function renderReviews(){
  await loadCore();setNav('reviews','Attenzione');
  const rows=state.series.items.flatMap(series=>series.seasons.filter(s=>s.attention).map(season=>({series,season})));
  const waiting=state.series.items.reduce((n,s)=>n+s.seasons.filter(x=>x.automation==='waiting').length,0);
  const airing=state.series.items.reduce((n,s)=>n+s.seasons.filter(x=>x.automation==='airing').length,0);
  main.innerHTML='<header class="page-title"><div><h1>Attenzione</h1><p>Solo eccezioni reali. I mapping deterministici non passano da qui.</p></div>'+
    '<div class="page-summary"><span><b>'+rows.length+'</b> eccezioni</span><span><b>'+waiting+'</b> waiting</span><span><b>'+airing+'</b> airing</span></div></header>'+
    (rows.length?'<div class="attention-list">'+rows.map(({series,season})=>'<a href="#/series/'+series.series_id+'?season='+season.season+'"><div class="attention-symbol" aria-hidden="true">!</div><div><b>'+esc(series.title)+' · S'+String(season.season).padStart(2,'0')+'</b><p>'+esc(season.reason_codes?.join(' · ')||seasonSummary(season))+'</p></div>'+badge(season.automation)+'<span class="chev">›</span></a>').join('')+'</div>':
    '<div class="empty-state"><div class="big-ok" aria-hidden="true">✓</div><h2>Niente da controllare</h2><p>Tutti i mapping risolvibili sono deterministici; AniDown può gestirli in autonomia.</p></div>');
}
async function renderDownloads(){
  await loadCore(true);setNav('downloads','Download');
  const d=state.overview.downloads||{active:[],history:[],enabled:false,max_concurrent:0};
  const jobRow=j=>'<tr><td><b>'+esc(j.series_title||'—')+'</b></td><td>S'+String(j.season||0).padStart(2,'0')+'E'+String(j.episode||0).padStart(2,'0')+'</td><td>'+audioBadge(j.audio)+'</td><td>'+esc(j.status||'—')+'</td><td>'+Math.round(Number(j.progress)||0)+'%</td></tr>';
  const table=(title,rows)=>'<table><caption class="sr-only">'+title+'</caption><thead><tr><th>Serie</th><th>Episodio</th><th>Audio</th><th>Stato</th><th>Progresso</th></tr></thead><tbody>'+(rows?.map(jobRow).join('')||'<tr><td colspan="5" class="empty-inline">Nessun '+(title==='Download attivi'?'download attivo':'elemento nello storico')+'</td></tr>')+'</tbody></table>';
  main.innerHTML='<header class="page-title"><div><h1>Download</h1><p>'+(d.enabled?'Writer attivo':'Writer disabilitato: modalità quasi-operativa')+'</p></div><div class="page-summary"><span><b>'+num(d.max_concurrent)+'</b> worker</span></div></header>'+
    (!d.enabled?'<div class="info-banner">I mapping automatici sono pronti, ma nessun file viene ancora scaricato o scritto su Sonarr.</div>':'')+
    '<section class="data-panel"><header><h2>Attivi</h2></header>'+table('Download attivi',d.active)+'</section>'+
    '<section class="data-panel"><header><h2>Storico</h2></header>'+table('Storico download',d.history)+'</section>';
}
async function renderActivity(){
  setNav('activity','Attività');const data=await api('/activity?limit=100');
  main.innerHTML='<header class="page-title"><div><h1>Attività</h1><p>Eventi operativi recenti, senza rumore per ogni mapping automatico.</p></div></header>'+
    '<div class="timeline">'+(data.items.map(e=>'<article><i aria-hidden="true"></i><div><b>'+esc(e.entity_title||String(e.action).replaceAll('_',' '))+'</b><p>'+esc(e.message)+(e.reason?' · '+esc(e.reason):'')+'</p><time>'+esc(dt(e.timestamp))+'</time></div></article>').join('')||'<div class="empty-inline">Nessuna attività</div>')+'</div>';
}
async function renderSettings(){
  state.settings=await api('/settings');setNav('settings','Impostazioni');const s=state.settings;
  const toggle=(id,title,desc,checked)=>'<label class="setting-toggle"><span><b>'+title+'</b><small>'+desc+'</small></span><input id="'+id+'" type="checkbox" '+(checked?'checked':'')+'></label>';
  main.innerHTML='<header class="page-title"><div><h1>Impostazioni</h1><p>Automazione, policy e notifiche.</p></div></header>'+readOnlyBanner()+'<form id="settings-form" class="settings-layout"><fieldset class="settings-fields" '+(state.mutationsAllowed===false?'disabled':'')+'>'+
    '<section class="data-panel"><header><h2>Scansione</h2></header>'+toggle('set-auto','Scansione automatica','Rileva nuove stagioni e cambi mapping senza intervento.',s.auto_scan_enabled)+
    '<label class="field">Intervallo<select id="set-interval">'+[15,30,60,180,360,720,1440].map(v=>'<option value="'+v+'" '+(s.auto_scan_interval_minutes===v?'selected':'')+'>'+v+' min</option>').join('')+'</select></label></section>'+
    '<section class="data-panel"><header><h2>Notifiche</h2><span class="backend-state">'+esc(s.telegram_status==='ready'?'Telegram pronto':'Telegram non configurato')+'</span></header>'+
    toggle('set-review','Review / attenzione','Avvisa quando nasce un dubbio che blocca l’automazione.',s.notify_review)+
    toggle('set-error','Errori','Scan, provider, download o runtime falliti.',s.notify_error)+
    toggle('set-anomaly','Anomalie mapping','Mapping cambiato o regressione inattesa.',s.notify_anomaly)+
    toggle('set-manual','Override manuali','Audit degli override applicati o rimossi; disattivato di default.',s.notify_manual_override)+
    toggle('set-new-season','Nuove stagioni','Disponibile anche qui; Sonarr può restare la sorgente principale.',s.notify_new_season)+'</section>'+
    '<p id="settings-error" class="form-error" role="alert" hidden></p><div class="settings-actions"><button class="button primary" type="submit">Salva impostazioni</button></div></fieldset></form>';
  document.querySelector('#settings-form').onsubmit=async e=>{
    e.preventDefault();
    const body={auto_scan_enabled:document.querySelector('#set-auto').checked,auto_scan_interval_minutes:Number(document.querySelector('#set-interval').value),
      notify_review:document.querySelector('#set-review').checked,notify_error:document.querySelector('#set-error').checked,
      notify_anomaly:document.querySelector('#set-anomaly').checked,notify_manual_override:document.querySelector('#set-manual').checked,
      notify_new_season:document.querySelector('#set-new-season').checked};
    try{state.settings=await api('/settings',{method:'PATCH',body:JSON.stringify(body)});toast('Impostazioni salvate');}
    catch(err){inlineError(document.querySelector('#settings-error'),err.message);}
  };
}
async function startScan(){
  if(state.mutationsAllowed!==true){toast('Le scansioni richiedono accesso loopback autenticato.',true);return;}
  state.scanRunning=true;syncMutationAccess();scanState.textContent='Avvio scan…';
  try{
    const job=await api('/scans',{method:'POST',body:JSON.stringify({dataset:'production'})});
    let done=false;
    while(!done){
      await new Promise(r=>setTimeout(r,1000));
      const s=await api('/scans/'+job.job_id);
      scanState.textContent=s.status==='running'?s.progress+'% · '+String(s.stage||'').replaceAll('_',' '):s.status;
      done=['completed','failed'].includes(s.status);
      if(done&&s.status==='failed')throw new Error('Scan fallito');
    }
    state.series=null;state.overview=null;scanState.textContent='';toast('Scan completato');await render();
  }catch(err){scanState.textContent='';toast(err.message,true);}
  finally{state.scanRunning=false;syncMutationAccess();}
}
async function render(){
  const r=route();
  try{
    if(r.page==='series'&&r.id)return await renderSeriesDetail(r.id,r.season);
    if(r.page==='series')return await renderSeries();
    if(r.page==='reviews')return await renderReviews();
    if(r.page==='downloads')return await renderDownloads();
    if(r.page==='activity')return await renderActivity();
    if(r.page==='settings')return await renderSettings();
    location.hash='#/series';
  }catch(err){
    main.innerHTML='<div class="fatal" role="alert"><h2>Impossibile caricare AniDown</h2><p>'+esc(err.message)+'</p><button class="button secondary" data-retry>Riprova</button></div>';
    main.querySelector('[data-retry]').onclick=()=>location.reload();
  }
}
scanButton.onclick=startScan;
window.addEventListener('hashchange',render);
render();
