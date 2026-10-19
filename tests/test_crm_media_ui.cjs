const assert=require("node:assert/strict");
const fs=require("node:fs");
const path=require("node:path");
const vm=require("node:vm");
const {File}=require("node:buffer");
class Element {
  constructor(){this.nodes=new Map();this.hidden=false;this.disabled=false;this.attributes={};this.files=[];}
  set innerHTML(value){this.html=value;this.nodes.clear();}
  get innerHTML(){return this.html || "";}
  querySelector(selector){if(!this.nodes.has(selector)){const e=new Element();e.hidden=selector==="[data-menu]" || selector==="[data-file]";this.nodes.set(selector,e);}return this.nodes.get(selector);}
  querySelectorAll(selector){if(selector!=="[data-kind]")return [];return ["image","video","document","audio"].map(kind=>{const e=this.querySelector(kind);e.dataset={kind};return e;});}
  setAttribute(name,value){this.attributes[name]=value;}
  contains(e){return [...this.nodes.values()].includes(e);}
  click(){this.clicked=true;this.onclick?.();}
  focus(){this.focused=true;}
}
const requests=[],revoked=[],errors=[],changes=[],listeners=new Set();
let permissionResolve,permissionCalls=0,stops=0,recorder;
class XHR {
  constructor(){this.upload={};this.headers={};requests.push(this);}
  open(method,url){this.method=method;this.url=url;}
  setRequestHeader(k,v){this.headers[k]=v;}
  send(file){this.file=file;}
  abort(){this.aborted=true;}
  complete(media){this.status=200;this.responseText=JSON.stringify({ok:true,media});this.onload();}
}
class Recorder {
  static isTypeSupported(mime){return mime.startsWith("audio/webm");}
  constructor(stream,options){this.stream=stream;this.mimeType=options.mimeType;this.state="inactive";recorder=this;}
  start(){this.state="recording";}
  stop(){if(this.state==="inactive")return;this.state="inactive";this.ondataavailable({data:new Blob(["audio"],{type:this.mimeType})});this.onstop();}
}
const ctx={window:{},URL:{createObjectURL:()=>"blob:private",revokeObjectURL:url=>revoked.push(url)},
  URLSearchParams,XMLHttpRequest:XHR,crypto:{randomUUID:()=>"request-id"},File,Blob,MediaRecorder:Recorder,
  navigator:{mediaDevices:{getUserMedia:()=>{permissionCalls++;return new Promise(resolve=>{permissionResolve=resolve;});}}},
  document:{addEventListener:(type,fn)=>listeners.add(fn),removeEventListener:(type,fn)=>listeners.delete(fn)},
  setInterval:()=>123,clearInterval:()=>{}};
vm.createContext(ctx);vm.runInContext(fs.readFileSync(path.join(__dirname,"../static/crm-media.js"),"utf8"),ctx);
const esc=v=>String(v).replaceAll("&","&amp;").replaceAll('"',"&quot;").replaceAll("<","&lt;");
const icon=name=>`<i>${name}</i>`;
const media=ctx.window.createCrmMedia({esc,icon,icons:()=>{},onChange:value=>changes.push(value),onBusy:()=>{},onError:e=>errors.push(e)});
let tools,preview;
const scope={doctor:"doctor-a",channel:"number-a",contact:"contact-a"};
function mount(initial=null){tools=new Element();preview=new Element();media.mount(tools,preview,scope,initial);media.availability(true);}
function choose(kind,file){tools.querySelectorAll("[data-kind]").find(n=>n.dataset.kind===kind).click();const input=tools.querySelector("[data-file]");input.files=[file];input.onchange();}
const ready={id:"private-id",name:'<photo>.png',kind:"image",size:3,state:"ready",url:"/api/crm/media?clinic=vielle&doctor=doctor-a&id=private-id"};
const flush=()=>new Promise(resolve=>setImmediate(resolve));
const inboxSource=fs.readFileSync(path.join(__dirname,"../static/crm-inbox.js"),"utf8");
const captions=vm.runInNewContext(`${inboxSource.slice(inboxSource.indexOf("  const messageText="),inboxSource.indexOf("  function chatError"))};({messageText,messagePreview})`);
for(const [kind,label] of [["image","Imagem"],["video","Vídeo"],["audio","Áudio"],["document","Arquivo"]]){
  const message={sender_name:"Jane Equipe",body:"*Jane Equipe*",media_kind:kind};
  assert.equal(captions.messageText(message),"");
  assert.equal(captions.messagePreview(message),label);
  assert.equal(captions.messagePreview({...message,body:"*Jane Equipe*\n\nSegue o material."}),"Segue o material.");
}
assert.equal(captions.messageText({sender_name:"",body:"Mensagem do cliente"}),"Mensagem do cliente");
assert.equal(captions.messageText({sender_name:"Jane Equipe",body:"*Jane Equipe*\n\n[Arquivo] texto digitado"}),"[Arquivo] texto digitado");
(async()=>{
  mount();assert.equal(requests.length,0);assert.equal(permissionCalls,0);assert.equal(preview.hidden,true);
  tools.querySelector("[data-attach]").click();assert.equal(tools.querySelector("[data-menu]").hidden,false);
  tools.onkeydown({key:"Escape"});assert.equal(tools.querySelector("[data-menu]").hidden,true);
  choose("image",new File(["png"],'<photo>.png',{type:"image/png"}));
  assert.equal(requests.length,1);assert.equal(media.busy(),true);assert.equal(media.value(),null);assert.equal(media.hasDraft(),true);
  assert.match(requests[0].url,/doctor=doctor-a&channel=number-a&contact=contact-a&kind=image/);
  assert.equal(requests[0].headers["X-File-Name"],"%3Cphoto%3E.png");assert.match(preview.innerHTML,/&lt;photo>/);
  assert.equal(requests[0].headers["X-DOC4DOCS-Request"],"1");
  requests[0].complete(ready);assert.equal(media.value().id,"private-id");assert.equal(media.busy(),false);
  assert.equal(requests.length,1);assert.match(preview.innerHTML,/Pronto para enviar/);
  media.clear();assert.equal(media.value(),null);assert.equal(media.hasDraft(),false);assert.equal(preview.hidden,true);assert.ok(revoked.includes("blob:private"));
  choose("document",new File(["bad"],"bad.exe"));assert.equal(requests.length,1);assert.match(errors.at(-1),/Formato/);
  choose("audio",new File(["wave"],"nota.wav"));const stale=requests.at(-1);media.destroy();assert.equal(stale.aborted,true);stale.complete(ready);assert.equal(media.value(),null);assert.equal(listeners.size,0);
  mount(ready);assert.equal(media.value().id,"private-id");assert.match(preview.innerHTML,/api\/crm\/media/);assert.equal(requests.length,2);
  media.availability(false);assert.equal(tools.querySelector("[data-attach]").disabled,true);media.destroy();
  mount();tools.querySelector("[data-record]").click();assert.equal(permissionCalls,1);media.destroy();permissionResolve({getTracks:()=>[{stop:()=>stops++}]});await flush();assert.equal(stops,1);assert.equal(requests.length,2);
  mount();tools.querySelector("[data-record]").click();permissionResolve({getTracks:()=>[{stop:()=>stops++}]});await flush();assert.match(preview.innerHTML,/Gravando/);assert.equal(media.busy(),true);
  preview.querySelector("[data-stop]").click();assert.equal(requests.length,3);assert.equal(requests.at(-1).file.type,"audio/webm");assert.match(requests.at(-1).file.name,/\.webm$/);assert.equal(media.busy(),true);
  requests.at(-1).complete({...ready,kind:"audio",name:"nota.webm"});assert.equal(media.value().kind,"audio");assert.match(preview.innerHTML,/<audio controls/);media.clear();
  tools.querySelector("[data-record]").click();permissionResolve({getTracks:()=>[{stop:()=>stops++}]});await flush();preview.querySelector("[data-cancel]").click();assert.equal(requests.length,3);assert.equal(media.hasDraft(),false);media.destroy();
  for(const kind of ["image","video","audio","document"]){
    const html=ctx.window.crmMediaMarkup({...ready,kind},esc,icon);
    assert.match(html,kind==="image"?/<img /:kind==="video"?/<video.*controls/:kind==="audio"?/<audio.*controls/:/download=/);
    const remote=ctx.window.crmMediaMarkup({...ready,kind,state:"remote"},esc,icon);assert.match(remote,/data-load-media="private-id"/);assert.ok(!remote.includes('src="'));
  }
  assert.equal(ctx.window.crmMediaMarkup({...ready,url:"https://provider.invalid/private"},esc,icon),"");
  assert.equal(listeners.size,0);
  console.log("CRM media: private uploads, explicit sending, previews, all formats, recording/cancel, stale scope, cleanup and received players OK");
})().catch(error=>{console.error(error);process.exitCode=1;});
