import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';

const html=readFileSync(new URL('../index.html',import.meta.url),'utf8');
const app=readFileSync(new URL('../app.js',import.meta.url),'utf8');
const css=readFileSync(new URL('../styles.css',import.meta.url),'utf8');

test('shell exposes keyboard and live-region primitives without inline script',()=>{
  assert.match(html,/class="skip-link" href="#main"/);
  assert.match(html,/<main id="main" tabindex="-1">/);
  assert.match(html,/id="scan-state" role="status" aria-live="polite"/);
  assert.match(html,/id="notice" class="notice" role="status" aria-live="polite" aria-atomic="true"/);
  assert.match(html,/id="review-count" hidden/);
  assert.doesNotMatch(html+app,/onclick="/);
  assert.match(app,/import \{trapDialogTab\} from '\.\/dialog-focus\.js'/);
  assert.match(app,/reviewCount\.hidden=openReviews===0/);
  assert.match(html,/class="loading" role="status" aria-live="polite"/);
  assert.match(app,/setAttribute\('aria-current','page'\)/);
  assert.match(app,/removeAttribute\('aria-current'\)/);
  assert.match(app,/class="fatal" role="alert"/);
});

test('dynamic controls use native names, links and disclosure relationships',()=>{
  assert.match(app,/aria-label="Filtri serie"/);
  assert.match(app,/<span class="sr-only">Stato<\/span><select id="filter">/);
  assert.match(app,/class="title-cell"><a href="#\/series\//);
  assert.doesNotMatch(app,/<tr data-open-series="'\+row\.series_id\+'" tabindex="0">/);
  assert.match(app,/aria-controls="'\+bodyId\+'"/);
  assert.match(app,/class="form-error" role="alert"/);
  assert.match(app,/<caption class="sr-only">/);
  assert.match(app,/class="season-pips" role="img" aria-label="Stati stagioni:/);
  assert.match(app,/aria-hidden="true" class="'\+esc\(s\.automation\)/);
  assert.match(app,/return '<span class="coverage" aria-label="/);
  assert.doesNotMatch(app,/return '<div class="coverage"/);
  assert.match(app,/class="attention-symbol" aria-hidden="true"/);
  assert.match(app,/class="big-ok" aria-hidden="true"/);
});

test('proxy read-only capability disables every browser mutation path',()=>{
  assert.match(app,/p\?\.meta\?\.mutations_allowed/);
  assert.match(app,/state\.mutationsAllowed!==true/);
  assert.match(app,/Accesso proxy in sola lettura/);
  assert.match(app,/settings-fields" '\+\(state\.mutationsAllowed===false\?'disabled'/);
  assert.match(app,/data-override=.*state\.mutationsAllowed===false/);
});

test('executable tokens and responsive states stay aligned with DESIGN.md',()=>{
  for(const [name,value] of Object.entries({bg:'#181b20',nav:'#15181c',surface:'#20242a',surface2:'#282d35',text:'#eef0f3',muted:'#aeb7c4',blue:'#a9c5ec'})){
    assert.match(css,new RegExp('--'+name+':'+value));
  }
  assert.match(css,/font-family:"Segoe UI"/);
  assert.match(css,/grid-template-columns:216px minmax\(0,1fr\)/);
  assert.match(css,/main\{padding:32px 40px 40px;max-width:1200px/);
  assert.match(css,/@media\(max-width:1000px\)/);
  assert.match(css,/@media\(max-width:700px\)/);
  assert.match(css,/grid-template-rows:auto 1fr;align-content:start/);
  assert.match(css,/\.main-nav\{display:flex;flex-wrap:wrap/);
  assert.match(css,/@media\(prefers-reduced-motion:reduce\)/);
  assert.match(css,/:focus-visible\{outline:3px solid var\(--blue\)/);
  assert.doesNotMatch(css,/outline:none|font-family:Inter/);
});
