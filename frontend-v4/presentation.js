/** Pure DTO presentation. All metadata is untrusted text; no internal files/models. */
export const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
export const text = (locale, it, en) => locale === 'en' ? en : it;
export const number = (value, locale='it') => new Intl.NumberFormat(locale).format(value ?? 0);
export const date = (value, locale='it') => {
  if (!value) return text(locale,'Non ancora eseguito','Not run yet');
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? '—' : new Intl.DateTimeFormat(locale,{dateStyle:'medium',timeStyle:'short'}).format(parsed);
};
export function stateLabel(state,locale='it') {
  const labels={proposed:['Proposta','Proposed'],approved:['Approvato','Approved'],needs_review:['Da verificare','Needs review'],rejected:['Rifiutato','Rejected'],superseded:['Sostituito','Superseded'],dismissed:['Archiviato','Dismissed'],open:['Aperta','Open'],resolved:['Risolta','Resolved']};
  const pair=labels[state] || [state,state];return text(locale,...pair);
}
export function targetsLabel(targets,locale='it') {
  return targets.length === 1 ? `${text(locale,'Stagione','Season')} ${targets[0].season}` : `${text(locale,'Stagioni','Seasons')} ${targets.map(t=>t.season).join(' + ')}`;
}
export function reason(item,locale='it') {
  const codes = new Set(item.reasons.map(r=>r.code));
  if(codes.has('episode_boundary_unverified')) {
    const total=item.plan?.episode_count || item.targets.reduce((n,t)=>n+(t.episode_count || 0),0);
    const boundary=item.targets.map(t=>t.episode_count ?? '?').join('/');
    return text(locale,`La release contiene ${total} episodi compatibili con ${item.targets.map(t=>'S'+t.season).join('+')}, ma il confine ${boundary} non è verificabile.`,`The release contains ${total} episodes compatible with ${item.targets.map(t=>'S'+t.season).join('+')}, but the ${boundary} boundary cannot be verified.`);
  }
  const messages={multiple_equivalent_candidates:['Più piani supportati restano equivalenti.','Multiple supported plans remain equivalent.'],season_conflict:['La stagione della release non corrisponde alla richiesta.','Release season identity conflicts with the target.'],release_date_conflict:['La data o l’anno della release non corrispondono alla stagione.','The release date or year conflicts with the season.'],episode_count_conflict:['Gli episodi disponibili non confermano la copertura richiesta.','Available episodes do not confirm the requested coverage.'],duplicate_url_across_seasons:['Gli stessi episodi verrebbero assegnati a stagioni diverse.','The same source episodes would be assigned to different seasons.'],crosswalk_missing:['La corrispondenza tra stagione e release non è verificata.','The correspondence between season and release is not verified.'],insufficient_metadata:['I metadata disponibili non bastano per confermare un piano.','Available metadata cannot establish a complete plan.'],language_mismatch:['L’audio differisce dalla preferenza richiesta.','Audio differs from the requested preference.'],low_confidence_title_match:['L’identità del titolo resta incerta.','Title identity remains uncertain.'],mapping_plan_unavailable:['Il titolo corrisponde, ma la copertura non è verificata.','Title matching succeeded, but coverage is not verified.'],composite_coverage:['Le release insieme coprono tutti gli episodi.','The releases together cover all episodes.'],metadata_supported:['Titolo, data e numerazione supportano la proposta.','Title, date and numbering support the proposal.'],scene_identity_supported:['La numerazione Sonarr conferma l’identità della stagione.','Sonarr scene numbering supports season identity.']};
  const priority=['multiple_equivalent_candidates','season_conflict','release_date_conflict','episode_count_conflict','duplicate_url_across_seasons','crosswalk_missing','insufficient_metadata'];
  const code=priority.find(c=>codes.has(c)) || item.reasons[0]?.code;
  return messages[code] ? text(locale,...messages[code]) : text(locale,'Servono ulteriori evidenze dalla sorgente.','Additional source evidence is required.');
}
export function audioLabel(variants,locale='it') {
  const names=[...new Set((variants || []).map(v=>v.audio==='UNKNOWN' ? text(locale,'Lingua ignota','Unknown language') : v.audio+(v.audio_language ? ' · '+v.audio_language.toUpperCase() : '')))];
  return names.length ? names.join(' / ') : '—';
}
function escDestination(d,plan,locale){return escapeHTML(`${plan.targets.length>1?'S'+d.season+' · ':''}ep ${d.episode_range[0]}–${d.episode_range[1]}`);}
export function planHTML(plan,locale='it') {
  if(!plan?.segments.length) return `<div class="empty-plan"><p>${text(locale,'Nessun piano verificabile disponibile.','No supported mapping plan is available.')}</p><small>${text(locale,'Approva solo dopo aver acquisito nuove evidenze.','Acquire new evidence before approving.')}</small></div>`;
  const expected=plan.targets.reduce((n,t)=>n+(t.episode_count || 0),0);
  return `<section class="mapping-plan" aria-label="${escapeHTML(text(locale,'Piano di mapping','Mapping plan'))}"><div class="plan-heading"><strong>${escapeHTML(targetsLabel(plan.targets,locale))}</strong><span>${number(expected,locale)} ${text(locale,'episodi','episodes')}</span></div><ol class="release-track">${plan.segments.map(segment=>{
    const destination=segment.destinations.map(d=>`<span class="destination-range">${escDestination(d,plan,locale)}</span>`).join(' / ');
    const selected=segment.variants.filter(v=>v.selected);
    const variants=selected.length ? selected : segment.variants;
    return `<li><span class="release-marker" aria-hidden="true"></span><div class="release-title">${escapeHTML(segment.title)}<small>${escapeHTML(audioLabel(variants,locale))}</small></div><span class="episode-span">${destination}</span></li>`;
  }).join('')}</ol>${plan.requires_uncertainty_acknowledgement ? `<p class="coverage-caution">${text(locale,'Copertura proposta · confine tra stagioni da confermare','Proposed coverage · season boundary unverified')}</p>` : `<p class="coverage-note">${text(locale,'Copertura completa','Complete coverage')} · ${number(plan.episode_count,locale)} ${text(locale,'episodi','episodes')}</p>`}</section>`;
}
export function evidenceMessage(message,locale='it') {
  if(locale==='en')return message;
  const map={'Observed numbering supports the proposed combined coverage.':'La numerazione osservata supporta la copertura combinata proposta.','The internal season boundary cannot independently be verified.':'Il confine interno tra stagioni non è verificabile in modo indipendente.','Season identity supported by Sonarr scene numbering and release metadata.':'La numerazione scene di Sonarr e i metadata della release supportano l’identità della stagione.','Observed source episodes agree with the mapped scene segment.':'Gli episodi osservati corrispondono al segmento scene proposto.','Title, premiere date and observed episode numbering support this target.':'Titolo, data di uscita e numerazione osservata supportano questa stagione.','Release date/year conflicts with the requested season.':'La data o l’anno della release non corrispondono alla stagione richiesta.','Release date agrees with the requested target.':'La data della release corrisponde alla stagione richiesta.'};
  return map[message] || message;
}

export function releaseDate(segment,locale='it') {
 if(!segment.premiere_date)return segment.release_year?String(segment.release_year):text(locale,'Data ignota','Unknown date');
 const parsed=new Date(segment.premiere_date);return Number.isNaN(parsed.getTime())?text(locale,'Data ignota','Unknown date'):new Intl.DateTimeFormat(locale,{dateStyle:'medium',timeZone:'UTC'}).format(parsed);
}
