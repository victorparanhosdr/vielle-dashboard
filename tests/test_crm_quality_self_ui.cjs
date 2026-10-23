"use strict";
const test=require("node:test"),assert=require("node:assert/strict"),fs=require("node:fs"),vm=require("node:vm"),path=require("node:path");
const selfSource=fs.readFileSync(path.join(__dirname,"../static/crm-quality-self.js"),"utf8");
const managerSource=fs.readFileSync(path.join(__dirname,"../static/crm-quality.js"),"utf8");
const esc=value=>String(value??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
function harness(provider){
  const state={doctor:"doctor-example",mode:"quality",session:{user:{id:11}},data:{can_manage:false,doctors:[{id:"doctor-example",name:"Profissional Exemplo"}]}},calls=[],nodes=new Map();
  const context=vm.createContext({window:{},Date,Intl,Map,Set,clearTimeout,setTimeout,document:{getElementById(){return null;}}});
  vm.runInContext(selfSource,context);vm.runInContext(managerSource,context);
  const root={innerHTML:"",querySelector(selector){if(!nodes.has(selector))nodes.set(selector,{});return nodes.get(selector);},querySelectorAll(){return [];}};
  const center=context.window.createCrmQuality({state,esc,icon:name=>`<svg data-icon="${name}"></svg>`,icons(){},modal(){},error(){},api:async(...args)=>{calls.push(args);return provider(...args);}});
  return {center,state,root,calls,nodes};
}
function listing(){return {month:"2026-10",status:"",page:1,pages:2,total:30,summary:{average:82.5,scored:29,pending:3,contested:2},items:[{id:"own-example",contact_name:'<img src=x onerror="bad()">',channel_name:"Comercial",revision:2,received_at:1,score:null,period:{from:1,to:2},response:{status:"awaiting_ack",revision:0}}]};}
test("employee mount calls only self route and renders filtered aggregate without inventing score",async()=>{
  const h=harness(async()=>listing());await h.center.mount(h.root);
  assert.deepEqual(h.calls.map(c=>c[0]),["quality/self"]);
  assert.match(h.root.innerHTML,/Minhas avaliações/);assert.match(h.root.innerHTML,/82,5/);
  assert.match(h.root.innerHTML,/Não avaliável/);assert.match(h.root.innerHTML,/Aguardando aceite/);
  assert.match(h.root.innerHTML,/&lt;img/);assert.doesNotMatch(h.root.innerHTML,/<img src=x|Régua e IA|Analisar atendimento|Provisória/);
  assert.ok(h.calls.every(c=>c[1]===undefined));
});
test("month and status filters reset pagination and are sent to scoped self endpoint",async()=>{
  const h=harness(async()=>listing());await h.center.mount(h.root);
  h.nodes.get('[data-self-month]').onchange({target:{value:"2026-09"}});await new Promise(setImmediate);
  assert.equal(h.calls.at(-1)[2].month,"2026-09");assert.equal(h.calls.at(-1)[2].page,1);
  h.nodes.get('[data-self-status]').onchange({target:{value:"contested"}});await new Promise(setImmediate);
  assert.equal(h.calls.at(-1)[2].status,"contested");assert.equal(h.calls.at(-1)[2].page,1);
});
test("late self data is discarded after doctor or account changes or unmount",async()=>{
  for(const change of [h=>h.state.doctor="other",h=>h.state.session.user.id=22,h=>h.center.unmount()]){
    let resolve;const h=harness(()=>new Promise(done=>resolve=done));const pending=h.center.mount(h.root);
    change(h);resolve(listing());await pending;assert.doesNotMatch(h.root.innerHTML,/qualitySelfTable/);
  }
});
test("no received grades gives empty state and no fabricated perfect average",async()=>{
  const data=listing();data.items=[];data.total=0;data.summary.average=null;
  const h=harness(async()=>data);await h.center.mount(h.root);
  assert.match(h.root.innerHTML,/Nenhuma avaliação recebida/);assert.doesNotMatch(h.root.innerHTML,/100\/100/);
});
test("permission failure is displayed and never falls back to manager data",async()=>{
  const h=harness(async()=>{throw Error("Sem acesso");});await h.center.mount(h.root);
  assert.match(h.root.innerHTML,/role="alert"/);assert.doesNotMatch(h.root.innerHTML,/qualityTable/);
  assert.deepEqual(h.calls.map(c=>c[0]),["quality/self"]);
});
test("responses use grade and appeal revisions and no actor supplied by the browser",()=>{
  assert.match(selfSource,/revision:a.revision,response_revision:r.revision,action,reason/);
  assert.doesNotMatch(selfSource,/actor_id:|quality\/review|quality\/analyze/);
  assert.match(selfSource,/r.can_contest\?/);assert.match(selfSource,/Uma única ressalva por avaliação/);
  assert.match(managerSource,/contest_decision/);assert.match(managerSource,/response_revision:r.revision/);
  assert.match(managerSource,/Ressalvas pendentes · qualquer data/);
});
