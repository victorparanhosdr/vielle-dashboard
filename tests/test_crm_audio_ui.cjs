"use strict";
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../static/crm-audio.js'),'utf8');
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const context=vm.createContext({window:{}});vm.runInContext(source,context);
test('transcript stays collapsed, escaped and marked automatic without removing audio',()=>{
  const result=context.window.crmAudioTranscriptMarkup({id:'audio-example',kind:'audio',transcript:{status:'done',text:'<img src=x onerror=bad()>'}},esc,()=>'<svg></svg>');
  assert.match(result,/<details class="audioTranscript"/);assert.doesNotMatch(result,/<details[^>]* open/);
  assert.match(result,/&lt;img/);assert.doesNotMatch(result,/<img src=x/);
  assert.match(result,/Automática · pode conter erros/);assert.match(result,/Copiar transcrição/);
  assert.doesNotMatch(result,/score|assessments|quality\/item/);
});
test('pending, quota and failure states never fabricate a transcript',()=>{
  for(const status of ['queued','running','limited','failed','cancelled']){
    const result=context.window.crmAudioTranscriptMarkup({id:'a',kind:'audio',transcript:{status,text:'private'}},esc,()=>'<svg></svg>');
    assert.doesNotMatch(result,/private|<details/);assert.match(result,/audioTranscriptState/);
  }
  assert.equal(context.window.crmAudioTranscriptMarkup({kind:'audio'},esc,()=>''),'');
});
test('controls require separate opt-in for automatic use and each historical batch or retry',()=>{
  assert.match(source,/name="confirm"/);assert.match(source,/data-batch-confirm/);assert.match(source,/row.querySelector\('input'\).checked/);
  assert.match(source,/audio\/batch-preview/);assert.match(source,/audio\/batch-apply/);
  assert.match(source,/state.doctor===doctor/);assert.match(source,/Áudios do histórico/);
});
test('media markup retains remote loading and audio controls with optional transcript module',()=>{
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../static/crm-media.js'),'utf8'),context);
  const result=context.window.crmMediaMarkup({id:'audio-example',kind:'audio',url:'/api/crm/media?id=a',name:'Áudio',state:'ready',transcript:{status:'done',text:'Fala de exemplo'}},esc,()=>'<svg></svg>');
  assert.match(result,/<audio[^>]*controls/);assert.match(result,/Fala de exemplo/);
  const remote=context.window.crmMediaMarkup({id:'a',kind:'audio',url:'/api/crm/media?id=a',name:'Áudio',state:'remote',transcript:{status:'queued'}},esc,()=>'<svg></svg>');
  assert.match(remote,/data-load-media/);assert.match(remote,/Transcrição na fila/);
});
