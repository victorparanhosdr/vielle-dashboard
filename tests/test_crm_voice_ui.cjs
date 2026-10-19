const assert=require("node:assert/strict");
const fs=require("node:fs");
const path=require("node:path");
const vm=require("node:vm");
const source=fs.readFileSync(path.join(__dirname,"../static/crm-voice.js"),"utf8");
const code=source.slice(source.indexOf("  function callRestriction("),source.indexOf("  function message("));
assert.ok(code.includes("callRestriction"));
const ctx={terminal:new Set(["completed","failed","canceled"]),recordings:{waiting:"Aguardando atendimento",recording:"Gravando"}};
vm.createContext(ctx);vm.runInContext(code+"; result={callRestriction,recordingLabel};",ctx);
const check=ctx.result.callRestriction;
const contact={phone:"5511999990001",experts_status:"matched",opted_out:0,consent_at:null};
assert.equal(check(contact),"");
assert.equal(check({...contact,experts_status:"new"}),"");
assert.equal(check({...contact,consent_at:123}),"");
assert.match(check({...contact,opted_out:1}),/descadastrado/);
assert.match(check({...contact,experts_status:"review"}),/vínculo/);
assert.match(check({...contact,phone:"5511"}),/telefone/);
assert.match(check({...contact,phone:"12125550000"}),/telefone/);
assert.match(check(undefined),/telefone/);
assert.ok(!source.includes("lead?.consent_at"));
assert.ok(!source.includes("!lead.consent_at"));
const authorizationInput=source.match(/<input[^>]*id="voiceRecordingConsent"[^>]*>/)?.[0];
assert.ok(authorizationInput);assert.ok(!/\bchecked\b/.test(authorizationInput));
assert.equal(ctx.result.recordingLabel({notice_mode:"staff",recording_status:"waiting",status:"in-progress"}),"Gravação não iniciada");
assert.equal(ctx.result.recordingLabel({notice_mode:"staff",recording_status:"waiting",status:"completed"}),"Sem gravação");
assert.equal(ctx.result.recordingLabel({notice_mode:"staff",recording_status:"recording",status:"in-progress"}),"Gravando");
console.log("Voice contact availability: default policy, opt-out, identity review and phone validation OK");

(async()=>{
  let requests=0,resolveRequest;
  const nodes={voiceRecordingConsent:{checked:false},voiceRecord:{disabled:true}};
  Object.assign(ctx,{
    recordBusy:false,current:{id:"call-a",status:"in-progress",notice_mode:"staff"},lead:{doctor_id:"doctor-a"},
    $:id=>nodes[id],message:()=>{},render:()=>{},
    endpoint:async(action,payload)=>{
      requests++;assert.equal(action,"start-recording");assert.equal(payload.verbal_authorization,true);
      assert.equal(payload.id,"call-a");assert.equal(payload.doctor_id,"doctor-a");
      return new Promise(resolve=>{resolveRequest=()=>resolve({call:{...ctx.current,consent_at:123,recording_status:"recording"}});});
    }
  });
  vm.runInContext(source.slice(source.indexOf("  async function startRecording()"),source.indexOf("  async function end()"))+"; record=startRecording;",ctx);
  await ctx.record();assert.equal(requests,0);
  nodes.voiceRecordingConsent.checked=true;
  ctx.current.status="ringing";await ctx.record();assert.equal(requests,0);
  ctx.current.status="in-progress";
  const pending=ctx.record();await ctx.record();assert.equal(requests,1);
  resolveRequest();await pending;assert.equal(ctx.current.recording_status,"recording");
  assert.equal(ctx.recordBusy,false);
  console.log("Voice recording: explicit unchecked attestation, connected-call gate and duplicate-click guard OK");
})().catch(error=>{console.error(error);process.exitCode=1;});
