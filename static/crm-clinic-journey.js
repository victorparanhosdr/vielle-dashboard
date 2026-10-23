"use strict";
window.createCrmClinicJourney=function({api,esc,icon,icons,modal,error,load,notice}){
  function fields(reasons){
    return `<fieldset class="clinicClosureFields"><legend>Classificação do encerramento</legend><label>Motivo<select name="closure_reason" required><option value="">Selecionar motivo</option>${reasons.map(r=>`<option value="${esc(r.id)}">${esc(r.name)}</option>`).join("")}</select></label><label>Observação<textarea name="closure_note" rows="3" maxlength="1200"></textarea></label><label class="check" data-attempts hidden><input type="checkbox" name="closure_attempts">Tentativas conferidas, sem resposta e sem retorno combinado</label><label class="check" data-professional hidden><input type="checkbox" name="closure_professional">Decisão validada pelo profissional</label></fieldset>`;
  }
  function toggle(form){
    const reason=form.elements.closure_reason?.value;
    for(const [key,name,visible] of [["attempts","closure_attempts",reason==="no_response"],["professional","closure_professional",reason==="not_indicated"]]){
      const label=form.querySelector(`[data-${key}]`);if(label){label.hidden=!visible;form.elements[name].required=visible;}
    }
    if(form.elements.closure_note)form.elements.closure_note.required=["other","no_response","not_indicated"].includes(reason);
  }
  function read(form){
    return {reason_id:form.elements.closure_reason?.value || "",note:form.elements.closure_note?.value || "",
      confirm_attempts:form.elements.closure_attempts?.checked===true,confirm_professional:form.elements.closure_professional?.checked===true};
  }
  function bind(form,select,pipeline,lead=null){
    if(!pipeline?.clinic_journey)return {read:()=>undefined};
    const host=document.createElement("div");host.className="clinicClosureHost";
    select.closest("label").after(host);
    function draw(){
      const ids=pipeline.clinic_journey.stage_ids,outcome=select.value===ids.closed?"closed":select.value===ids.noncommercial?"noncommercial":"";
      host.innerHTML=outcome?fields(pipeline.clinic_journey.reasons.filter(r=>r.outcome===outcome)):"";
      if(outcome){
        if(lead?.stage_id===select.value){form.elements.closure_reason.value=lead.journey_state?.reason_id || "";form.elements.closure_note.value=lead.journey_state?.note || "";}
        form.elements.closure_reason.onchange=()=>toggle(form);toggle(form);
      }
    }
    select.addEventListener("change",draw);draw();
    return {read:()=>host.childElementCount?read(form):form.elements.journey_confirm?.checked?{review_confirmed:true}:undefined};
  }
  async function choose(lead,stage,pipeline){
    const ids=pipeline?.clinic_journey?.stage_ids;
    if(!ids || ![ids.closed,ids.noncommercial].includes(stage) || lead.stage_id===stage)return undefined;
    return new Promise((resolve,reject)=>{
      const dialog=document.createElement("dialog"),outcome=stage===ids.closed?"closed":"noncommercial";
      dialog.className="clinicClosureDialog";
      dialog.setAttribute("aria-label","Classificar encerramento");
      dialog.innerHTML=`<header><h2>${outcome==="noncommercial"?"Contato não comercial":"Encerrar negociação"}</h2><button type="button" data-cancel class="icon-button" aria-label="Cancelar">${icon("x")}</button></header><form><p>${esc(lead.name)}</p>${fields(pipeline.clinic_journey.reasons.filter(r=>r.outcome===outcome))}<div class="formActions"><button type="submit" class="primary">${icon("check")} Confirmar</button></div></form>`;
      document.body.append(dialog);const form=dialog.querySelector("form");let done=false;
      dialog.addEventListener("close",()=>{dialog.remove();if(!done)reject(new Error("Movimentação cancelada."));},{once:true});
      dialog.querySelector("[data-cancel]").onclick=()=>dialog.close();
      form.elements.closure_reason.onchange=()=>toggle(form);toggle(form);
      form.onsubmit=e=>{e.preventDefault();done=true;const value=read(form);dialog.close();resolve(value);};
      dialog.showModal();icons();
    });
  }
  async function migrate(pipeline,doctor){
    modal("Organizar funil da clínica",'<p role="status">Preparando prévia…</p>');
    const host=document.getElementById("modalBody"),valid=()=>host.isConnected && document.getElementById("crmDialog").open;
    const waiting=host.firstElementChild;
    try{
      const result=await api("clinic-journey/preview",{doctor_id:doctor,pipeline_id:pipeline.id},{doctor});
      if(!valid() || !waiting.isConnected)return;
      modal("Organizar funil da clínica",`<form id="clinicMigration"><p><strong>${esc(result.pipeline)}</strong> · ${result.count} cartões</p><div class="clinicMigrationRows">${result.stages.map(s=>`<div><span>${esc(s.name)}</span><strong>${s.count}</strong></div>`).join("")}</div><p class="inlineWarning">${result.review} registro(s) precisam de revisão. Etapas antigas, histórico, conversas e vendas serão preservados. Robôs antigos deste funil ficarão inativos; campanhas não serão iniciadas ou retomadas. Cursos não serão alterados.</p><label class="check"><input name="confirm" type="checkbox" required>Aplicar esta organização com cópia de segurança</label><div class="formActions"><button type="submit" class="primary">${icon("check")} Aplicar novo funil</button></div><p data-error role="alert" hidden></p></form>`);
      const form=document.getElementById("clinicMigration");
      form.onsubmit=async e=>{
        e.preventDefault();const b=form.querySelector('[type="submit"]');b.disabled=true;
        try{const applied=await api("clinic-journey/apply",{doctor_id:doctor,id:result.id,confirm:form.elements.confirm.checked},{doctor});
          if(!form.isConnected || !valid())return;document.getElementById("crmDialog").close();await load();notice(`${applied.count} cartões organizados; ${applied.review} para revisão. Histórico preservado.`);
        }catch(err){if(form.isConnected){const p=form.querySelector("[data-error]");p.hidden=false;p.textContent=err.message;}}finally{if(b.isConnected)b.disabled=false;}
      };icons();
    }catch(err){if(valid())error(err.message);}
  }
  return {bind,read,fields,toggle,choose,migrate};
};
