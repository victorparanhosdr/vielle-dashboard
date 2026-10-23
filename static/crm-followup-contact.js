"use strict";
window.crmFollowupReplyChecked=(eligible,preference,override=null)=>Boolean(eligible && preference && !preference.paused && override!==false);
window.createCrmFollowupContact=({api,icon,icons})=>{
  function mount(host,lead,{active=()=>true,changed=()=>{}}={}){
    if(!host || lead.course_key || lead.institute_key)return;
    const pane=document.createElement("section");pane.className="contactFollowupControl";host.append(pane);
    let preference=lead.followup_contact || null,busy=false;
    const valid=()=>pane.isConnected && active();
    function draw(message=""){
      if(!valid())return;
      pane.innerHTML=`<header><h3>Follow-up deste contato</h3><span data-followup-contact-status>${preference?.paused?"Pausado":preference?"Sem pausa individual":"Carregando…"}</span></header><label class="check" title="A pausa vale para os funis clínicos deste contato. Remover a pausa não retoma pendências antigas."><input type="checkbox" role="switch" aria-label="Pausar follow-ups neste contato" ${preference?.paused?"checked":""} ${busy || !preference?.can_manage?"disabled":""}>Pausar follow-ups</label><p role="alert" ${message?"":"hidden"}></p>`;
      pane.querySelector("[role=alert]").textContent=message;
      const input=pane.querySelector("input");
      if(preference && !preference.can_manage)input.title="Contato compartilhado fora do seu acesso; solicite ao Master.";
      input.onchange=async()=>{
        const paused=input.checked;input.checked=preference.paused;
        if(busy || !preference.can_manage)return;
        busy=true;draw();
        try{
          const result=await api("followups/contact",{doctor_id:lead.doctor_id,id:lead.id,revision:preference.revision,paused,confirm:true});
          if(valid())apply(result.followup_contact);
        }catch(err){if(valid())draw(err.message);}finally{busy=false;if(valid()){const control=pane.querySelector("input");if(control)control.disabled=!preference?.can_manage;}}
      };
      icons();
    }
    function apply(value){
      if(!valid() || lead.followup_contact?.revision>value.revision)return;
      preference=value;lead.followup_contact=value;draw();changed(value);
    }
    draw();
    api("followups/contact",undefined,{doctor:lead.doctor_id,id:lead.id}).then(result=>apply(result.followup_contact)).catch(err=>{if(valid())draw(err.message);});
  }
  return {mount};
};
