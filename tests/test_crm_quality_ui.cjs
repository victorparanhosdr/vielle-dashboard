"use strict";
const test=require("node:test"),assert=require("node:assert/strict"),fs=require("node:fs"),vm=require("node:vm"),path=require("node:path");
const source=fs.readFileSync(path.join(__dirname,"../static/crm-quality.js"),"utf8");
const esc=value=>String(value??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
function harness(provider){
  const state={doctor:"doctor-example",mode:"quality",data:{doctors:[{id:"doctor-example",name:"Profissional Exemplo"}]}},calls=[];
  const nodes=new Map(),lists={"[data-quality-view]":[{dataset:{qualityView:"jobs"}},{dataset:{qualityView:"selections"}}]},context=vm.createContext({window:{},Date,Intl,Map,Set,clearTimeout,setTimeout,crypto:require("node:crypto").webcrypto,
    document:{getElementById(){return null;}}});
  vm.runInContext(source,context);
  const root={innerHTML:"",querySelector(selector){if(!nodes.has(selector))nodes.set(selector,{});return nodes.get(selector);},querySelectorAll(selector){return lists[selector] || [];}};
  const center=context.window.createCrmQuality({state,esc,icon:name=>`<svg data-icon="${name}"></svg>`,icons(){},modal(){},error(){},
    api:async(...args)=>{calls.push(args);return provider(...args);}});
  return{center,state,root,calls,nodes,lists};
}
const listing=()=>({summary:{count:1,reviewed:0,average:null},settings:{enabled:false,auto_closed:false},rubric:[],
  jobs:[{id:"job-example",name:'<img src=x onerror="bad()">',channel_name:"Comercial",created_at:1,status:"done",assessments:[{actor_name:"Equipe Exemplo",score:null,reviewed:false}]}],total:1,page:1,pages:1});
test("loading the panel is read-only, does not fabricate a perfect score, and escapes client content",async()=>{
  const h=harness(async()=>listing());await h.center.mount(h.root);
  assert.deepEqual(h.calls.map(x=>x[0]),["quality"]);
  assert.match(h.root.innerHTML,/Não avaliável/);
  assert.match(h.root.innerHTML,/Provisória/);
  assert.doesNotMatch(h.root.innerHTML,/100\/100/);
  assert.match(h.root.innerHTML,/&lt;img/);
  assert.doesNotMatch(h.root.innerHTML,/<img src=x/);
  assert.match(h.root.innerHTML,/Análise por IA desativada/);
});
test("late responses cannot populate another doctor's quality panel",async()=>{
  let resolve;const h=harness(()=>new Promise(done=>{resolve=done;}));
  const pending=h.center.mount(h.root);h.state.doctor="other-doctor";resolve(listing());await pending;
  assert.doesNotMatch(h.root.innerHTML,/qualityTable/);
});
test("an unmounted panel ignores outstanding responses",async()=>{
  let resolve;const h=harness(()=>new Promise(done=>{resolve=done;}));
  const pending=h.center.mount(h.root);h.center.unmount();resolve(listing());await pending;
  assert.doesNotMatch(h.root.innerHTML,/qualityTable/);
});
test("permission failures show an error, not confidential data or a success state",async()=>{
  const h=harness(async()=>{throw new Error("Acesso exclusivo do gestor.");});await h.center.mount(h.root);
  assert.match(h.root.innerHTML,/role="alert"/);assert.match(h.root.innerHTML,/Acesso exclusivo/);
  assert.doesNotMatch(h.root.innerHTML,/qualityTable/);
});
test("manual pilot exposes no automatic controls and shows its daily usage",async()=>{
  const data=listing();data.daily_used=7;data.settings={enabled:true,daily_limit:20};
  const h=harness(async()=>data);await h.center.mount(h.root);
  assert.match(h.root.innerHTML,/Piloto manual ativo/);
  assert.match(h.root.innerHTML,/Análises de hoje/);
  assert.match(h.root.innerHTML,/de 20 por profissional/);
  assert.doesNotMatch(h.root.innerHTML,/Seleções pendentes|Média revisada · amostra|data-quality-view/);
  assert.ok(h.calls.every(call=>call[1]===undefined));
});
test("only the reviewed aggregate is rendered without fabricating a value",async()=>{
  const data=listing();data.summary.average=70;data.summary.reviewed=1;
  const h=harness(async()=>data);await h.center.mount(h.root);
  assert.match(h.root.innerHTML,/Média revisada/);assert.match(h.root.innerHTML,/70,0/);
  assert.match(h.root.innerHTML,/Somente notas revisadas/);
});
