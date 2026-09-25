import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {escapeHTML,planHTML,reason,stateLabel,targetsLabel,audioLabel,date,releaseDate} from '../presentation.js';
const samples=JSON.parse(readFileSync(new URL('./contract-samples.json',import.meta.url),'utf8'));
test('Black Lagoon real DTO renders the two destination episode ranges',()=>{const html=planHTML(samples.black.plan);assert.match(html,/24 episodi/);assert.match(html,/aria-label="Piano di mapping"/);assert.match(planHTML(samples.black.plan,"en"),/aria-label="Mapping plan"/);assert.match(html,/Black Lagoon/);assert.match(html,/Second Barrage/);assert.match(html,/ep 1–12/);assert.match(html,/ep 13–24/);assert.doesNotMatch(html,/https?:|target_id|sonarr_episode/);});
test('Crystal real DTO explains the unverifiable 14/12 boundary',()=>{assert.equal(reason(samples.crystal),'La release contiene 26 episodi compatibili con S1+S2, ma il confine 14/12 non è verificabile.');assert.match(planHTML(samples.crystal.plan),/confine tra stagioni da confermare/);});
test('Crystal numerical coverage is never described as verified complete coverage',()=>{assert.doesNotMatch(planHTML(samples.crystal.plan),/Copertura completa/);});
test('Nadia real DTO remains a supported 39 episode proposal',()=>{assert.equal(samples.nadia.actions.approve,true);assert.match(planHTML(samples.nadia.plan),/39 episodi/);});
test('Metadata HTML is escaped including quotes and apostrophes',()=>{assert.equal(escapeHTML('<img onerror="bad"> & \''),'&lt;img onerror=&quot;bad&quot;&gt; &amp; &#39;');const copy=structuredClone(samples.black.plan);copy.segments[0].title='<script>alert(1)</script>';assert.doesNotMatch(planHTML(copy),/<script>/);});
test('Unknown plans have actionable empty presentation',()=>{assert.match(planHTML({segments:[]}),/Nessun piano verificabile/);assert.doesNotMatch(planHTML({segments:[]}),/undefined|null/);});
test('Seasons and state labels remain distinct from human approval',()=>{assert.equal(stateLabel('proposed'),'Proposta');assert.equal(stateLabel('approved'),'Approvato');assert.equal(targetsLabel(samples.crystal.targets),'Stagioni 1 + 2');});
test('Language variants and missing language are handled without identifiers',()=>{assert.equal(audioLabel([]),'—');assert.equal(audioLabel([{audio:'DUB',audio_language:'it'}]), 'DUB · IT');assert.equal(audioLabel([{audio:'UNKNOWN'}]),'Lingua ignota');});
test('Localization covers boundary uncertainty and missing dates',()=>{assert.match(reason(samples.crystal,'en'),/14\/12 boundary cannot be verified/);assert.equal(date(null),'Non ancora eseguito');assert.equal(date('bad'),'—');});
test('Frontend has no external assets or legacy dependency',()=>{const html=readFileSync(new URL('../index.html',import.meta.url),'utf8'),app=readFileSync(new URL('../app.js',import.meta.url),'utf8');assert.doesNotMatch(html,/frontend_OLD|https?:\/\//);assert.doesNotMatch(app,/src\/v4|sqlite|table\.json/);assert.match(html,/aria-label="Navigazione principale"/);assert.match(app,/<summary>Perché questo mapping\?<\/summary>/);assert.doesNotMatch(app,/<details class="evidence" open/);});

test('Ranma 2024 real plan cannot display the contaminated V3 Senko mapping',()=>{const html=planHTML(samples.ranma.plan);assert.match(html,/2024/);assert.doesNotMatch(html,/Senko/i);});

test('Dark normal text and meaningful controls have measured contrast',()=>{
 const css=readFileSync(new URL('../styles.css',import.meta.url),'utf8');
 const token=name=>css.match(new RegExp('--'+name+':(#[0-9a-fA-F]{6})'))[1];
 const luminance=color=>{const rgb=color.slice(1).match(/../g).map(v=>parseInt(v,16)/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4);return rgb[0]*.2126+rgb[1]*.7152+rgb[2]*.0722;};
 const contrast=(a,b)=>{const values=[luminance(a),luminance(b)].sort((a,b)=>b-a);return (values[0]+.05)/(values[1]+.05);};
 for(const surface of ['bg','nav','surface','surface2'])for(const foreground of ['text','muted'])assert.ok(contrast(token(foreground),token(surface))>=4.5,foreground+' on '+surface);
 assert.ok(contrast(token('blue'),token('bg'))>=3);
 assert.ok(contrast(token('on-accent'),token('blue'))>=4.5);
 assert.match(css,/textarea:focus\{border-color:var\(--blue\)\}/);
});

test('Release dates use localized date-only presentation and preserve unknown facts',()=>{assert.match(releaseDate({premiere_date:'2022-10-11',release_year:2022},'en'),/Oct 11, 2022/);assert.equal(releaseDate({release_year:2024}),'2024');assert.equal(releaseDate({}),'Data ignota');});
test('Frontend real sample source hash remains bound to the sanitized production fixture',()=>{const manifest=JSON.parse(readFileSync(new URL('./sample-manifest.json',import.meta.url),'utf8'));const source=readFileSync(new URL('../../tests/v4/fixtures/production_metadata_v1.json',import.meta.url));assert.equal(manifest.sha256,createHash('sha256').update(source).digest('hex'));});
