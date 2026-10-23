"use strict";
const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../static/crm-media.js'),'utf8');
const inbox=fs.readFileSync(path.join(__dirname,'../static/crm-inbox.js'),'utf8');
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const flush=()=>new Promise(setImmediate);
function harness(load=async()=>{}){
  const observers=[],calls=[];let current='conversation-a';
  class Observer{
    constructor(callback,options){this.callback=callback;this.options=options;this.nodes=new Set();observers.push(this);}
    observe(node){this.nodes.add(node);} unobserve(node){this.nodes.delete(node);} disconnect(){this.nodes.clear();}
    emit(nodes,visible=true){this.callback(nodes.map(target=>({target,isIntersecting:visible})));}
  }
  const ctx=vm.createContext({window:{},IntersectionObserver:Observer});vm.runInContext(source,ctx);
  const previews=ctx.window.createCrmStickerPreviews({active:key=>key===current,load:async button=>{calls.push(button.dataset.loadMedia);return load(button);}});
  const button=id=>({dataset:{loadMedia:id},isConnected:true,disabled:false});
  const watch=(nodes,key=current)=>{previews.watch({querySelectorAll:()=>nodes},key);return observers.at(-1);};
  return {ctx,previews,button,watch,calls,setCurrent:key=>{current=key;}};
}
test('stickers use private image markup with stable transparent sizing, not document downloads',()=>{
  const h=harness(),media={id:'sticker-a',kind:'image',is_sticker:true,state:'ready',name:'<unsafe>.webp',size:300,url:'/api/crm/media?clinic=vielle&id=sticker-a'};
  const markup=h.ctx.window.crmMediaMarkup(media,esc,()=>'<svg></svg>');
  assert.match(markup,/class="chatSticker"/);assert.match(markup,/alt="Figurinha"/);assert.match(markup,/width="168" height="168"/);
  assert.doesNotMatch(markup,/chatDocument|download=/);
  const pending=h.ctx.window.crmMediaMarkup({...media,state:'remote'},esc,()=>'<svg></svg>');
  assert.match(pending,/data-sticker-preview/);assert.match(pending,/Carregar figurinha/);assert.doesNotMatch(pending,/<unsafe>|src=/);
  const photo=h.ctx.window.crmMediaMarkup({...media,is_sticker:false},esc,()=>'<svg></svg>');
  assert.match(photo,/chatImage/);assert.doesNotMatch(photo,/chatSticker/);
  assert.equal(h.ctx.window.crmMediaMarkup({...media,url:'https://provider.invalid/private'},esc,()=>''),'');
});
test('only visible stickers load and the queue permits one request at a time',async()=>{
  const releases=[];const h=harness(()=>new Promise(resolve=>releases.push(resolve))),a=h.button('a'),b=h.button('b');
  const obs=h.watch([a,b]);obs.emit([a,b],false);assert.deepEqual(h.calls,[]);
  obs.emit([a,b]);assert.deepEqual(h.calls,['a']);
  releases.shift()();await flush();assert.deepEqual(h.calls,['a','b']);
  releases.shift()();await flush();obs.emit([a,b]);assert.deepEqual(h.calls,['a','b']);
});
test('failed preview is not automatically retried on polling; manual load remains rendered',async()=>{
  const h=harness(async()=>{throw Error('Unavailable');}),a=h.button('a');
  h.watch([a]).emit([a]);await flush();h.watch([h.button('a')]).emit([h.button('a')]);await flush();
  assert.deepEqual(h.calls,['a']);
  assert.equal(a.disabled,false);
});
test('switching contacts cancels old queued and late observer work',async()=>{
  let release;const h=harness(()=>new Promise(resolve=>release=resolve)),a=h.button('a'),b=h.button('b');
  const old=h.watch([a,b]);old.emit([a,b]);assert.deepEqual(h.calls,['a']);
  h.setCurrent('conversation-b');h.previews.reset();old.emit([h.button('late')]);release();await flush();
  assert.deepEqual(h.calls,['a']);
});
test('disconnected or disabled nodes are skipped and legacy browsers keep manual loading',async()=>{
  const h=harness(),a=h.button('a'),b=h.button('b');a.isConnected=false;b.disabled=true;
  h.watch([a,b]).emit([a,b]);await flush();assert.deepEqual(h.calls,[]);
  const ctx=vm.createContext({window:{}});vm.runInContext(source,ctx);
  assert.doesNotThrow(()=>ctx.window.createCrmStickerPreviews({load(){throw Error('no automatic loading');},active:()=>true}).watch({querySelectorAll:()=>[]},'scope'));
});
test('inbox resets preview lifecycle and hides only genuine sticker placeholders',()=>{
  assert.match(inbox,/stickerPreviews\?\.watch\(timeline,signature\)/);
  assert.match(inbox,/function unmount\(\)\{stickerPreviews\?\.reset\(\)/);
  assert.match(inbox,/m.media\?\.is_sticker && messageText\(m\)==='\[Figurinha\]'/);
});
