const assert=require("node:assert/strict");
const fs=require("node:fs");
const path=require("node:path");
const vm=require("node:vm");
class Element {
  constructor(){this.nodes=new Map();this.value="";this.hidden=false;this.disabled=false;this.attributes={};}
  querySelector(selector){if(!this.nodes.has(selector))this.nodes.set(selector,new Element());return this.nodes.get(selector);}
  setAttribute(name,value){this.attributes[name]=value;}
  focus(){this.focused=true;}
}
const requests=[],selected=[],closed=[];
const api=(action,body,query)=>new Promise((resolve,reject)=>requests.push({action,body,query,resolve,reject}));
const esc=value=>String(value).replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;");
let timer=null;
const ctx={window:{},setTimeout:fn=>(timer=fn,1),clearTimeout:()=>{timer=null;}};
vm.createContext(ctx);vm.runInContext(fs.readFileSync(path.join(__dirname,"../static/crm-chat-search.js"),"utf8"),ctx);
const search=ctx.window.createCrmChatSearch({api,esc,icon:name=>`<i>${name}</i>`,icons:()=>{},onResult:seq=>selected.push(seq),onClose:()=>closed.push(true)});
let root,toggle;
function mount(contact="patient-a"){root=new Element();toggle=new Element();search.mount(root,toggle,{doctor:"doctor-a",channel:"number-a",contact});root.querySelector("[data-direction]").value="all";}
function input(value){root.querySelector("[data-query]").value=value;root.querySelector("[data-query]").oninput();const run=timer;timer=null;return run();}
const page=(ids,total=ids.length,before=0)=>({results:ids.map(seq=>({seq})),total,next_before:before,has_more:!!before});
(async()=>{
  mount();assert.equal(root.hidden,true);assert.equal(requests.length,0);
  toggle.onclick();assert.equal(root.hidden,false);assert.equal(toggle.attributes["aria-expanded"],"true");
  const pending=input("avaliacao");assert.equal(requests[0].action,"conversation-search");assert.equal(requests[0].query.contact,"patient-a");
  assert.equal(root.querySelector("[data-next]").disabled,true);
  requests[0].resolve(page([100,90],3,90));await pending;
  assert.equal(selected.at(-1),100);assert.equal(search.target(),100);assert.equal(root.querySelector("[data-search-count]").textContent,"1 de 3");
  assert.ok(!root.innerHTML.includes(" data-count "));
  assert.equal(search.highlight("AVALIAÇÃO <script>"),"<mark>AVALIAÇÃO</mark> &lt;script&gt;");
  assert.equal(search.highlight("avaliação e avaliação"),"<mark>avaliação</mark> e <mark>avaliação</mark>");
  await root.querySelector("[data-next]").onclick();assert.equal(selected.at(-1),90);
  const more=root.querySelector("[data-next]").onclick();assert.equal(requests.at(-1).query.before,90);
  requests.at(-1).resolve(page([90,80],3));await more;assert.equal(selected.at(-1),80);assert.equal(root.querySelector("[data-search-count]").textContent,"3 de 3");
  assert.equal(root.querySelector("[data-next]").disabled,true);
  await root.querySelector("[data-previous]").onclick();assert.equal(selected.at(-1),90);
  root.querySelector("[data-direction]").value="in";const onlyPatient=root.querySelector("[data-direction]").onchange();
  assert.equal(requests.at(-1).query.direction,"in");requests.at(-1).resolve(page([70]));await onlyPatient;assert.equal(search.target(),70);
  const stale=input("antiga"),staleRequest=requests.at(-1);const fresh=input("nova");requests.at(-1).resolve(page([60]));await fresh;
  staleRequest.resolve(page([999]));await stale;assert.equal(search.target(),60);
  const empty=input("ausente");requests.at(-1).resolve(page([]));await empty;
  assert.equal(selected.at(-1),0);assert.equal(root.querySelector("[data-search-count]").textContent,"Nenhum resultado");
  const failed=input("erro");requests.at(-1).reject(new Error("Busca indisponível"));await failed;assert.equal(root.querySelector("[data-search-count]").textContent,"Busca indisponível");
  const switching=input("pendente"),old=requests.at(-1);mount("patient-b");old.resolve(page([777]));await switching;
  assert.equal(search.target(),0);assert.equal(root.hidden,true);
  toggle.onclick();const next=input("teste");requests.at(-1).resolve(page([50]));await next;
  const escape={key:"Escape",preventDefault:()=>{}};root.querySelector("[data-query]").onkeydown(escape);
  assert.equal(root.hidden,true);assert.equal(search.target(),0);assert.equal(closed.length,1);assert.equal(root.querySelector("[data-query]").value,"");
  toggle.onclick();assert.equal(root.querySelector("[data-search-count]").textContent,"");assert.equal(root.querySelector("[data-next]").disabled,true);
  assert.equal(search.highlight("<img>"),"&lt;img&gt;");
  search.destroy();
  console.log("CRM conversation search: scoped read-only requests, accents, highlighting/XSS, pagination, navigation, stale queries, empty/error, switching and close OK");
})().catch(error=>{console.error(error);process.exitCode=1;});
