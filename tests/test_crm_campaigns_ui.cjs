"use strict";
const test=require("node:test"),assert=require("node:assert/strict");
const fs=require("node:fs"),path=require("node:path"),vm=require("node:vm");
const source=fs.readFileSync(path.join(__dirname,"../static/crm-campaigns.js"),"utf8");
const escape=value=>String(value??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const settle=()=>new Promise(resolve=>setImmediate(resolve));

async function harness(changes={},check=async()=>({state:"open"})) {
  const campaign={id:"campaign-test",doctor_id:"doctor-test",channel_id:"channel-test",channel_doctor:"doctor-test",
    channel_archived:0,channel_state:"open",channel_name:"Atendimento",channel_phone:"5500000000001",
    status:"paused",last_error:"Numero desconectado.",start_blocker:"",name:"Retorno de exemplo",created_at:1,
    progress:0,counts:{queued:2},weekdays:[0,1,2,3,4],interval_seconds:180,hour_from:8,minute_from:0,hour_to:20,
    minute_to:0,daily_limit:200,daily_used:0,resume_unconfirmed:0,resume_excluded:0,...changes};
  const state={doctor:"doctor-test",mode:"campaigns",data:{campaigns_enabled:true,gateway_configured:true,
    doctors:[{id:"doctor-test",name:"Profissional de exemplo"}]}};
  const controls=new Map(),calls=[],errors=[];
  let html="",buttons=[],loads=0;
  const node=()=>({disabled:false,attributes:new Map(),setAttribute(key,value){this.attributes.set(key,value);},
    removeAttribute(key){this.attributes.delete(key);}});
  const root={get innerHTML(){return html;},set innerHTML(value){
    html=value;
    buttons=[...value.matchAll(/<button data-campaign-state="([^"]+)"[^>]*>/g)].map(match=>({
      ...node(),dataset:{campaignState:match[1]},disabled:/\sdisabled(?:\s|>)/.test(match[0])}));
  },querySelector(selector){return selector===".campaignCenter"&&html.includes('class="campaignCenter"')?{}:null;},
    querySelectorAll(selector){return selector==="[data-campaign-state]"?buttons:[];}};
  const document={getElementById(id){
    if(["enableCampaigns","campaignBack"].includes(id))return null;
    if(!controls.has(id))controls.set(id,node());
    return controls.get(id);
  }};
  const context=vm.createContext({window:{},document,Intl,Date,FormData:class {}});
  vm.runInContext(source,context);
  const center=context.window.createCrmCampaigns({state,esc:escape,icon:name=>`<svg data-icon="${name}"></svg>`,
    icons(){},modal(){throw new Error("Connection check must not open a send confirmation.");},
    error:message=>errors.push(message),load:async()=>{loads++;},api:async(action,payload,query)=>{
      calls.push({action,payload,query});
      if(action==="campaign-dashboard")return {campaigns:[campaign],daily:[],counts:{paused:1},page:1,total:1,
        from:"2026-10-01",to:"2026-10-21",metrics:{sent:0,active:0,duplicates:0,unknown:0}};
      if(action==="channel-state")return check();
      throw new Error(`Unexpected operation: ${action}`);
    }});
  center.mount(root);await settle();
  return {campaign,state,center,root,calls,errors,get button(){return buttons[0];},get loads(){return loads;}};
}

test("reconnected number is current information, not a red disconnection error",async()=>{
  const h=await harness();
  assert.match(h.root.innerHTML,/class="connectionNote campaignConnection" role="status"/);
  assert.match(h.root.innerHTML,/N\u00famero conectado/);
  assert.match(h.root.innerHTML,/Motivo da pausa anterior/);
  assert.doesNotMatch(h.root.innerHTML,/<p class="inlineWarning">Numero desconectado\./);
  const start=h.root.innerHTML.match(/<button data-campaign-command="start"[^>]*>/)[0];
  assert.doesNotMatch(start,/disabled/);
  assert.equal(h.loads,0);
  assert.deepEqual(h.calls.map(c=>c.action),["campaign-dashboard"]);
});

test("a real disconnection remains visible and cannot be resumed",async()=>{
  const h=await harness({channel_state:"close",start_blocker:"Reconecte o numero para retomar."});
  assert.match(h.root.innerHTML,/<p class="inlineWarning">Numero desconectado\./);
  assert.doesNotMatch(h.root.innerHTML,/campaignConnection/);
  assert.match(h.root.innerHTML.match(/<button data-campaign-command="start"[^>]*>/)[0],/disabled/);
  assert.ok(h.button);
});

test("other errors and send blockers are not hidden by an open connection",async()=>{
  const h=await harness({last_error:"Envio sem confirmacao.",start_blocker:"Revise o envio anterior."});
  assert.match(h.root.innerHTML,/<p class="inlineWarning">Envio sem confirmacao\./);
  assert.match(h.root.innerHTML,/Revise o envio anterior/);
  assert.match(h.root.innerHTML.match(/<button data-campaign-command="start"[^>]*>/)[0],/disabled/);
});

test("archived or transferred numbers are not presented as ready to resume",async()=>{
  for(const changes of [{channel_archived:1},{channel_doctor:"other-doctor"}]){
    const h=await harness({...changes,start_blocker:"Conexao indisponivel para este profissional."});
    assert.equal(h.button,undefined);
    assert.doesNotMatch(h.root.innerHTML,/campaignConnection/);
    assert.match(h.root.innerHTML.match(/<button data-campaign-command="start"[^>]*>/)[0],/disabled/);
  }
});

test("checking a connection only refreshes status, never starts or connects a number",async()=>{
  const h=await harness();
  await h.button.onclick();
  assert.deepEqual(h.calls.map(c=>c.action),["campaign-dashboard","channel-state"]);
  assert.equal(h.calls[1].payload.id,"channel-test");
  assert.equal(h.calls[1].payload.doctor_id,"doctor-test");
  assert.equal(h.loads,1);
  assert.equal(h.campaign.status,"paused");
  assert.equal(h.campaign.counts.queued,2);
  assert.equal(h.button.disabled,false);
  assert.equal(h.button.attributes.has("aria-busy"),false);
});

test("failed checks do not confirm reconnection or refresh the campaign as successful",async()=>{
  const h=await harness({channel_state:"close"},async()=>{throw new Error("Nao foi possivel confirmar a conexao.");});
  await h.button.onclick();
  assert.deepEqual(h.errors,["Nao foi possivel confirmar a conexao."]);
  assert.equal(h.loads,0);
  assert.equal(h.button.disabled,false);
  assert.equal(h.campaign.channel_state,"close");
});

test("late connection responses do not repaint another doctor's screen",async()=>{
  let resolve;
  const h=await harness({},()=>new Promise(done=>{resolve=done;}));
  const checking=h.button.onclick();
  assert.equal(h.button.disabled,true);
  h.state.doctor="other-doctor";
  resolve({state:"open"});await checking;
  assert.equal(h.loads,0);
  assert.deepEqual(h.errors,[]);
});

test("connection checks are disabled if no gateway is configured",async()=>{
  const h=await harness();
  h.state.data.gateway_configured=false;
  h.center.mount(h.root);await settle();
  assert.equal(h.button.disabled,true);
});

test("a running queue explains individual uncertainty without presenting a pause",async()=>{
  const h=await harness({status:"running",last_error:"",counts:{queued:3,unknown:1},resume_unconfirmed:1});
  assert.match(h.root.innerHTML,/A fila segue para os demais destinatários/);
  assert.match(h.root.innerHTML,/não serão repetidos automaticamente/);
  assert.doesNotMatch(h.root.innerHTML,/data-campaign-command="start"/);
  assert.match(h.root.innerHTML,/data-campaign-command="pause"/);
});

test("finished pending sends are not misrepresented as confirmed or silently removed",async()=>{
  const h=await harness({status:"done",last_error:"",counts:{sent:7,unknown:1},resume_unconfirmed:1,progress:100});
  assert.match(h.root.innerHTML,/Concluída com pendências/);
  assert.match(h.root.innerHTML,/7 aceitas/);
  assert.match(h.root.innerHTML,/1 sem confirmação/);
  assert.match(h.root.innerHTML,/Confira os destinatários sem confirmação/);
  assert.doesNotMatch(h.root.innerHTML,/data-campaign-command="start"/);
  assert.deepEqual(h.calls.map(c=>c.action),["campaign-dashboard"]);
});

test("a fully accepted campaign still uses the usual finished label",async()=>{
  const h=await harness({status:"done",last_error:"",counts:{sent:8},progress:100});
  assert.match(h.root.innerHTML,/<span class="statusPill done">Concluída<\/span>/);
  assert.doesNotMatch(h.root.innerHTML,/Concluída com pendências/);
});
