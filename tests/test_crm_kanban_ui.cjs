const assert=require("node:assert/strict");
const fs=require("node:fs");
const path=require("node:path");
const vm=require("node:vm");
const source=fs.readFileSync(path.join(__dirname,"../static/crm-kanban.js"),"utf8");
const observers=[];
class Observer {
  constructor(callback,options){this.callback=callback;this.options=options;observers.push(this);}
  observe(target){this.target=target;}
  disconnect(){this.disconnected=true;}
  trigger(){if(!this.disconnected)this.callback([{isIntersecting:true,target:this.target}]);}
}
const ctx={window:{},CSS:{escape:s=>s},IntersectionObserver:Observer};
vm.createContext(ctx);vm.runInContext(source,ctx);
const lane=()=>{
  const nodes={".columnLoadMore":{hidden:false,disabled:false},"[data-column-count]":{textContent:"20 de 45"},"[data-column-status]":{hidden:true}};
  const cards={ids:Array.from({length:20},(_,i)=>"lead-"+i),
    querySelectorAll(){return this.ids.map(id=>({dataset:{lead:id}}));},
    insertAdjacentHTML(position,html){assert.equal(position,"beforeend");this.ids.push(...[...html.matchAll(/data-lead="([^"]+)"/g)].map(m=>m[1]));}};
  const value={isConnected:true,scrollTop:0,attributes:{},nodes,cards,
    querySelector:s=>s===".columnCards"?cards:nodes[s],setAttribute(name,value){this.attributes[name]=value;}};
  return value;
};
let requests=[],pending=[],added=[],refreshes=0;
const kanban=ctx.window.createCrmKanban({
  api:async(action,payload,query)=>{requests.push({action,payload,query});return new Promise((resolve,reject)=>pending.push({resolve,reject}));},
  esc:String,icon:n=>`<i>${n}</i>`,icons:()=>{},leadCard:l=>`<button data-lead="${l.id}"></button>`,
  onLeads:rows=>added.push(...rows),onRefresh:()=>refreshes++
});
const settings={doctor:"doctor-a",pipeline:"pipeline-a",filters:{search:"ana",sort:"spent"},columns:[{stage_id:"stage-a",loaded:20,total:45,next_cursor:"cursor-one"}]};
let current;
let boardTop=0;
function mount(){current=lane();kanban.mount({getBoundingClientRect:()=>({top:boardTop}),querySelector:()=>({querySelector:()=>current})},settings);return current;}
const batch=(start,end,next="cursor-two")=>({pipeline_id:"pipeline-a",stage_id:"stage-a",leads:Array.from({length:end-start},(_,i)=>({id:"lead-"+(start+i)})),loaded:end,total:45,next_cursor:next});
(async()=>{
  const first=mount();assert.equal(kanban.interacting(),false);
  assert.equal(observers.at(-1).options.root,null);
  boardTop=-100;assert.equal(kanban.interacting(),true);boardTop=0;
  const initial=first.nodes[".columnLoadMore"].onclick();
  await first.nodes[".columnLoadMore"].onclick();assert.equal(requests.length,1);
  assert.equal(kanban.interacting(),true);assert.equal(first.attributes["aria-busy"],"true");
  assert.equal(requests[0].action,"kanban-page");assert.equal(requests[0].query.doctor,"doctor-a");
  assert.equal(requests[0].query.pipeline,"pipeline-a");assert.equal(requests[0].query.stage,"stage-a");
  assert.deepEqual(JSON.parse(requests[0].query.filters),settings.filters);
  pending.shift().resolve({...batch(20,40),leads:[{id:"lead-1"},...batch(20,40).leads]});await initial;
  assert.equal(first.cards.ids.length,40);assert.equal(new Set(first.cards.ids).size,40);assert.equal(added.length,20);
  assert.equal(first.nodes["[data-column-count]"].textContent,"40 de 45");assert.equal(kanban.interacting(),true);
  assert.equal(first.attributes["aria-busy"],"false");
  const last=first.nodes[".columnLoadMore"].onclick();assert.equal(requests[1].query.cursor,"cursor-two");
  pending.shift().resolve(batch(40,45,""));await last;
  assert.equal(first.cards.ids.length,45);assert.equal(first.nodes[".columnLoadMore"].hidden,true);
  assert.ok(observers.at(-1).disconnected);
  const stale=mount();const delayed=stale.nodes[".columnLoadMore"].onclick();
  const replacement=mount();pending.shift().resolve(batch(20,40));await delayed;
  assert.equal(stale.cards.ids.length,20);assert.equal(replacement.cards.ids.length,20);
  const conflict=replacement.nodes[".columnLoadMore"].onclick();
  pending.shift().reject(Object.assign(Error("Esta fase mudou"),{status:409}));await conflict;
  assert.equal(replacement.nodes[".columnLoadMore"].textContent,"Atualizar funil");
  assert.equal(replacement.nodes["[data-column-status]"].hidden,false);
  await replacement.nodes[".columnLoadMore"].onclick();assert.equal(refreshes,1);
  const retry=mount();const failing=retry.nodes[".columnLoadMore"].onclick();
  pending.shift().reject(Error("Falha temporaria"));await failing;
  assert.equal(retry.nodes[".columnLoadMore"].textContent,"Tentar novamente");
  const recovering=retry.nodes[".columnLoadMore"].onclick();pending.shift().resolve(batch(20,40));await recovering;
  assert.equal(retry.cards.ids.length,40);
  kanban.destroy();assert.equal(kanban.interacting(),false);
  assert.match(kanban.footer({loaded:0,total:0,next_cursor:""}),/hidden/);
  const css=fs.readFileSync(path.join(__dirname,"../static/crm.css"),"utf8");
  const boardRules=[...css.matchAll(/\.crmBoard\s*\{([^}]+)\}/g)].map(m=>m[1]);
  assert.ok(boardRules.length>=2);assert.ok(boardRules.every(rule=>rule.includes("height:auto")));
  const laneRule=css.match(/\.columnLeads\s*\{([^}]+)\}/)[1];
  assert.match(laneRule,/overflow:visible/);assert.ok(!laneRule.includes("overflow-y:auto"));
  console.log("Kanban: bounded batches, fixed scope, duplicate guard, stale-response cancellation, retry and refresh OK");
})().catch(error=>{console.error(error);process.exitCode=1;});
