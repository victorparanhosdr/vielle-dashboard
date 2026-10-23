"use strict";
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {railScrollState}=require('../static/crm-shell.js');
test('no arrow is shown when all navigation fits or desktop rail is hidden',()=>{
  assert.deepEqual(railScrollState({scrollHeight:400,clientHeight:500,scrollTop:0},500),{overflow:false,up:false,down:false});
  assert.deepEqual(railScrollState({scrollHeight:600,clientHeight:0,scrollTop:0},0),{overflow:false,up:false,down:false});
});
test('top, middle and bottom show only relevant scroll directions',()=>{
  assert.deepEqual(railScrollState({scrollHeight:650,clientHeight:450,scrollTop:0},500),{overflow:true,up:false,down:true});
  assert.deepEqual(railScrollState({scrollHeight:650,clientHeight:450,scrollTop:90},500),{overflow:true,up:true,down:true});
  assert.deepEqual(railScrollState({scrollHeight:650,clientHeight:450,scrollTop:200},500),{overflow:true,up:true,down:false});
});
test('subpixel positions do not leave a false arrow at the edge',()=>{
  const state=railScrollState({scrollHeight:650,clientHeight:450,scrollTop:199.5},500);
  assert.equal(state.down,false);
});
test('hints are semantic buttons outside tabs, react to permission and size changes, and respect reduced motion',()=>{
  const source=fs.readFileSync(path.join(__dirname,'../static/crm-shell.js'),'utf8');
  const html=fs.readFileSync(path.join(__dirname,'../static/crm.html'),'utf8');
  assert.match(html,/id="crmRailDown"[^>]*aria-label="Mostrar mais áreas abaixo"/);
  assert.match(html,/aria-controls="crmRailModuleList"/);
  assert.match(source,/prefers-reduced-motion/);assert.match(source,/ResizeObserver\(schedule\)/);
  assert.match(source,/attributeFilter:\['hidden'\]/);assert.match(source,/modules.scrollBy/);
  assert.match(source,/getClientRects\(\).length/);
});
