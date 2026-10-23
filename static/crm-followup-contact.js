"use strict";
window.crmFollowupReplyChecked=(eligible,preference,override=null)=>Boolean(eligible && preference && !preference.paused && override!==false);
window.crmFollowupProgressBadge=(value,esc,icon)=>{
  if(!value || value.status==="none")return "";
  const labels={waiting:"Em acompanhamento",paused:"Pausado",review:"Conferir envio",completed:"Concluído",responded:"Cliente respondeu",cancelled:"Encerrado"};
  const total=Number(value.total_steps)||0,count=Number(value.completed_steps)||0;
  const summary=total?`Follow-up ${count}/${total}`:"Follow-up";
  const when=value.next_at?new Date(value.next_at*1000).toLocaleString("pt-BR",{timeZone:"America/Sao_Paulo",day:"2-digit",month:"2-digit",hour:"2-digit",minute:"2-digit"}):"";
  const pending={queued:"na fila",sending:"em envio",unknown:"sem confirmação",robot:"robô em andamento"}[value.pending_status];
  const status=labels[value.status] || "Conferir";
  const detail=`${count} passo(s) concluído(s). Mensagens contam após aceite do conector; robôs, após conclusão. Entrega e leitura não confirmadas.${value.next_step?` Próximo passo: ${value.next_step}/${total}${pending?` · ${pending}`:""}${when?` · ${when}`:""}.`:""}${value.reason?` ${value.reason}`:""}`;
  return `<span class="leadFollowupBadge ${esc(value.status)}" title="${esc(detail)}" aria-label="${esc(summary+' · '+status+'. '+detail)}">${icon(value.status==="review"?"circle-alert":value.status==="paused"?"pause-circle":"timer-reset")}<strong>${esc(summary)}</strong><span>${esc(status)}</span></span>`;
};
window.createCrmFollowupContact=({api,icon,icons,esc})=>{
  function mount(host,lead,{active=()=>true,changed=()=>{}}={}){
    if(!host || lead.course_key || lead.institute_key)return;
    const pane=document.createElement("section");pane.className="contactFollowupControl";host.append(pane);
    let preference=lead.followup_contact || null,busy=false;
    const valid=()=>pane.isConnected && active();
    function draw(message=""){
      if(!valid())return;
      pane.innerHTML=`<header><h3>Follow-up deste contato</h3><span data-followup-contact-status>${preference?.paused?"Pausado":preference?"Sem pausa individual":"Carregando…"}</span></header><label class="check" title="A pausa vale para os funis clínicos deste contato. Remover a pausa não retoma pendências antigas."><input type="checkbox" role="switch" aria-label="Pausar follow-ups neste contato" ${preference?.paused?"checked":""} ${busy || !preference?.can_manage?"disabled":""}>Pausar follow-ups</label><p role="alert" ${message?"":"hidden"}></p>`;
      pane.querySelector("[role=alert]").textContent=message;
      pane.querySelector("header").insertAdjacentHTML("afterend",window.crmFollowupProgressBadge(lead.followup_progress,esc,icon));
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
      preference=value;lead.followup_contact=value;
      if(value.paused && lead.followup_progress)lead.followup_progress={...lead.followup_progress,status:"paused",paused_individually:true,reason:"Pausado na ficha do contato",next_at:0};
      draw();changed(value);
    }
    draw();
    api("followups/contact",undefined,{doctor:lead.doctor_id,id:lead.id}).then(result=>apply(result.followup_contact)).catch(err=>{if(valid())draw(err.message);});
  }
  return {mount};
};
