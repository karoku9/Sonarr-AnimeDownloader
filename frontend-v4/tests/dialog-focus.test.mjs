import test from 'node:test';
import assert from 'node:assert/strict';
import {trapDialogTab} from '../dialog-focus.js';
function context(){const document={activeElement:null};const controls=['close','reason','cancel','approve'].map(name=>({name,disabled:false,tabIndex:0,closest:()=>null,focus(){document.activeElement=this;}}));const dialog={ownerDocument:document,querySelectorAll:()=>controls,focus(){document.activeElement=this;}};return {document,controls,dialog};}
function event(key='Tab',shiftKey=false){return {key,shiftKey,prevented:false,preventDefault(){this.prevented=true;}};}
test('Tab from final action cycles to first dialog control',()=>{const c=context();c.document.activeElement=c.controls.at(-1);const e=event();trapDialogTab(e,c.dialog);assert.equal(c.document.activeElement,c.controls[0]);assert.equal(e.prevented,true);});
test('Shift Tab from first control stays within dialog',()=>{const c=context();c.document.activeElement=c.controls[0];const e=event('Tab',true);trapDialogTab(e,c.dialog);assert.equal(c.document.activeElement,c.controls.at(-1));assert.equal(e.prevented,true);});
test('Disabled and hidden controls are never focus destinations',()=>{const c=context();c.controls[0].disabled=true;c.controls[1].closest=()=>({hidden:true});c.document.activeElement=c.controls.at(-1);trapDialogTab(event(),c.dialog);assert.equal(c.document.activeElement,c.controls[2]);});
test('Intermediate Tab and Escape retain native behavior',()=>{const c=context();c.document.activeElement=c.controls[1];for(const key of ['Tab','Escape']){const e=event(key);trapDialogTab(e,c.dialog);assert.equal(e.prevented,false);assert.equal(c.document.activeElement,c.controls[1]);}});
