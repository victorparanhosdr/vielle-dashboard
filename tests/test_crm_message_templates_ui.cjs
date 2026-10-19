const assert=require("node:assert/strict");
const fs=require("node:fs");
const path=require("node:path");
const vm=require("node:vm");
class Element {
  constructor(){this.nodes=new Map();this.listeners=new Map();this.value="";this.hidden=false;this.disabled=false;this.attributes={};this.dataset={};this.selectionStart=0;this.selectionEnd=0;this.maxLength=3000;}
  set innerHTML(value){this.html=value;this.nodes.clear();}
  get innerHTML(){return this.html || "";}
  querySelector(selector){if(!this.nodes.has(selector))this.nodes.set(selector,new Element());return this.nodes.get(selector);}
  querySelectorAll(selector){const attribute=selector.slice(1,-1);return [...this.innerHTML.matchAll(new RegExp(attribute+'="(\\d+)"',"g"))].map(m=>{const node=this.querySelector(selector+":"+m[1]);node.dataset[attribute.replace(/^data-/,"").replace(/-([a-z])/g,(_,c)=>c.toUpperCase())]=m[1];return node;});}
  setAttribute(k,v){this.attributes[k]=v;}
  focus(){this.focused=true;}
  scrollIntoView(){}
  addEventListener(k,fn){this.listeners.set(k,fn);}
  removeEventListener(k){this.listeners.delete(k);}
  dispatchEvent(e){this.oninput?.(e);this.listeners.get(e.type)?.(e);}
  setRangeText(text,start,end){this.value=this.value.slice(0,start)+text+this.value.slice(end);this.selectionStart=this.selectionEnd=start+text.length;}
  contains(e){return e===this || [...this.nodes.values()].some(n=>n.contains(e));}
}
const requests=[],documentListeners=new Map();
const api=(action,body,query)=>new Promise((resolve,reject)=>requests.push({action,body,query,resolve,reject}));
const esc=value=>String(value).replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll('"',"&quot;");
const ctx={window:{},Event,document:{addEventListener:(k,fn)=>documentListeners.set(k,fn),removeEventListener:k=>documentListeners.delete(k)}};
vm.createContext(ctx);vm.runInContext(fs.readFileSync(path.join(__dirname,"../static/crm-message-templates.js"),"utf8"),ctx);
const menu=ctx.window.createCrmMessageTemplates({api,esc,icon:name=>`<i>${name}</i>`,icons:()=>{},getContext:()=>({name:"Ana Exemplo",doctor:"Dr. Victor"}),onError:()=>{}});
const templates=[{id:"one",title:"Endereço",shortcut:"endereço",shortcut_key:"endereco",body:"Olá, {nome}! Endereço do {doutor}: Rua Exemplo, 100."},
  {id:"two",title:"Boas-vindas <teste>",shortcut:"ola",shortcut_key:"ola",body:"Olá, {nome}! Como podemos ajudar?"}];
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const tools=new Element(),textarea=new Element();let changes=0;textarea.oninput=()=>changes++;
function type(value,caret=value.length){textarea.value=value;textarea.selectionStart=textarea.selectionEnd=caret;textarea.dispatchEvent(new Event("input"));}
function key(value){let prevented=false;textarea.listeners.get("keydown")({key:value,preventDefault:()=>{prevented=true;}});return prevented;}
(async()=>{
  menu.mount(tools,textarea);assert.equal(requests[0].action,"message-templates");requests[0].resolve({templates});await tick();
  const picker=tools.querySelector(".messageTemplatesMenu"),rows=tools.querySelector(".messageTemplateRows");
  type("Bom dia /ENDERECO");assert.equal(picker.hidden,false);assert.match(rows.innerHTML,/Endereço/);assert.ok(!rows.innerHTML.includes("Boas-vindas"));
  assert.equal(key("Enter"),true);assert.equal(textarea.value,"Bom dia Olá, Ana! Endereço do Dr. Victor: Rua Exemplo, 100.");assert.equal(picker.hidden,true);assert.equal(changes,2);
  assert.ok(requests.every(r=>r.action==="message-templates" && r.body===undefined));
  requests.at(-1).resolve({templates});await tick();
  type("Veja /endereço agora",14);assert.equal(key("Tab"),true);assert.match(textarea.value,/Olá, Ana!/);assert.ok(textarea.value.endsWith(" agora"));
  type("https://exemplo.com/endereco");assert.equal(picker.hidden,true);
  type("/naoexiste");assert.match(rows.innerHTML,/Nenhum modelo encontrado/);assert.equal(key("Enter"),false);assert.equal(textarea.value,"/naoexiste");
  type("/o");assert.equal(key("Escape"),true);assert.equal(picker.hidden,true);assert.equal(textarea.value,"/o");
  textarea.maxLength=10;type("/endereco");key("Enter");assert.equal(textarea.value,"/endereco");assert.match(tools.querySelector("[data-template-status]").textContent,/10 caracteres/);
  textarea.maxLength=3000;type("/ola");assert.match(rows.innerHTML,/&lt;teste>/);
  menu.availability(false);assert.equal(tools.querySelector(".messageTemplatesToggle").disabled,true);assert.equal(picker.hidden,true);
  menu.availability(true);type("/ola");key("Enter");assert.equal(textarea.value,"Olá, Ana! Como podemos ajudar?");
  const pending=requests.at(-1);menu.destroy();assert.equal(textarea.listeners.size,0);assert.equal(documentListeners.size,0);pending.resolve({templates:[]});await tick();
  console.log("CRM templates: accent/case shortcuts, exact replacement, menu, variables, preserved suffix, no auto-send, limits, XSS, stale requests and cleanup OK");
})().catch(error=>{console.error(error);process.exitCode=1;});
