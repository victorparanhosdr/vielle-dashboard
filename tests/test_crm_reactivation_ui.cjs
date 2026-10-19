const assert=require("node:assert/strict");
const fs=require("node:fs");
const path=require("node:path");
const vm=require("node:vm");
const source=fs.readFileSync(path.join(__dirname,"../static/crm.js"),"utf8");
const code=source.slice(source.indexOf("  function reactivationModal("),source.indexOf("  function campaignForm("));
let html,submit,closes,request;
const cancel={};const profiles=[];
const ctx={
  esc:v=>String(v).replaceAll("<","&lt;"),icon:name=>`<i>${name}</i>`,
  modal:(title,body)=>{assert.equal(title,"Reativar contato");html=body;},
  $:id=>{assert.equal(id,"cancelReactivation");return cancel;},
  wireForm:(callback,close)=>{submit=callback;closes=close;},
  api:async(action,payload)=>{request={action,payload};},
  profile:async id=>profiles.push(id)
};
vm.createContext(ctx);vm.runInContext(code+"; show=reactivationModal;",ctx);
const lead={id:"lead-a",doctor_id:"doctor-a",revision:7,name:"<Paciente>",phone:"5511999990001"};
ctx.show(lead);
assert.ok(html.includes("&lt;Paciente>"));
assert.match(html,/name="body"[^>]*required/);
const checkbox=html.match(/<input[^>]*name="confirm_reactivation"[^>]*>/)?.[0];
assert.ok(checkbox);assert.match(checkbox,/type="checkbox"/);assert.match(checkbox,/required/);
assert.ok(!/\bchecked\b/.test(checkbox));
assert.match(html,/não serão reenviadas/);assert.match(html,/não serão reiniciadas/);
assert.equal(closes,false);
assert.match(source,/if\(\$\("reactivateContact"\)\)/);
assert.match(source,/Contato reativado/);
(async()=>{
  await cancel.onclick();assert.deepEqual(profiles,["lead-a"]);assert.equal(request,undefined);
  await submit(new Map([["body","Cliente pediu para voltar"],["confirm_reactivation","on"]]));
  assert.equal(request.action,"activity");
  assert.deepEqual(JSON.parse(JSON.stringify(request.payload)),{id:"lead-a",doctor_id:"doctor-a",kind:"reactivate",revision:7,body:"Cliente pediu para voltar",confirm_reactivation:true});
  assert.deepEqual(profiles,["lead-a","lead-a"]);
  await submit(new Map([["body","Sem confirmacao"]]));assert.equal(request.payload.confirm_reactivation,false);
  ctx.api=async()=>{throw Error("Cadastro alterado");};
  await assert.rejects(()=>submit(new Map([["body","Cliente pediu para voltar"],["confirm_reactivation","on"]])),/Cadastro alterado/);
  assert.equal(profiles.length,3);
  console.log("CRM reactivation: explicit confirmation, reason, immutable scope, cancellation, refresh and error handling OK");
})().catch(error=>{console.error(error);process.exitCode=1;});
