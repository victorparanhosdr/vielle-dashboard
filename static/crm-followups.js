"use strict";
window.createCrmFollowupSettings=({state,api,esc,icon,icons,notice})=>{
  let root=null,scope="",data=null,draft=null,dirty=false,busy=false,seq=0,previewSeq=0;
  const find=selector=>root?.querySelector(selector);
  const copy=value=>JSON.parse(JSON.stringify(value));
  const active=token=>root?.isConnected && seq===token && state.mode==="followups" && scope===`${state.doctor}:${state.pipeline}`;
  const duration=minutes=>minutes && minutes%1440===0?[minutes/1440,1440]:minutes && minutes%60===0?[minutes/60,60]:[minutes,1];
  const label=minutes=>minutes<2880 && minutes%60===0?`${minutes/60}h`:minutes%1440===0?`${minutes/1440} dias`:`${minutes} min`;
  const date=stamp=>new Date(stamp*1000).toLocaleString("pt-BR",{timeZone:"America/Sao_Paulo",day:"2-digit",month:"2-digit",hour:"2-digit",minute:"2-digit"});
  function error(message=""){const el=find("#followupError");if(el){el.hidden=!message;el.textContent=message;}}
  function changes(){dirty=true;error();const el=find("#followupSaveState");if(el)el.textContent="Alterações não salvas";previewSeq++;updateTotals();}
  function updateTotals(){
    let total=0;
    draft.steps.forEach((step,i)=>{total+=step.minutes;const el=find(`[data-fu-total="${i}"]`);if(el)el.textContent=label(total);});
    const count=find("#followupStepCount");if(count)count.textContent=`${draft.steps.length} / ${data.max_steps}`;
    const add=find("#followupAdd");if(add)add.disabled=!data.can_manage || busy || data.enabled || draft.steps.length>=data.max_steps;
    const pause=find("#followupDailyPause");
    if(pause){
      const minutes=value=>{const [h,m]=value.split(":").map(Number);return h*60+m;};
      const span=1440-(minutes(draft.to)-minutes(draft.from));
      pause.textContent=span>0 && span<1440?`Pausa diária: ${draft.to}–${draft.from} · ${label(span)}`:"";
    }
  }
  function steps(){
    find("#followupSteps").innerHTML=draft.steps.map((step,i)=>{
      const [amount,unit]=duration(step.minutes);
      return `<article class="followupStep" data-fu-step="${i}"><header><h3>${icon("message-circle")} Follow-up ${i+1}</h3><div class="followupStepActions"><button type="button" class="icon-button" data-fu-up="${i}" aria-label="Mover follow-up ${i+1} para cima" title="Mover para cima" ${i===0?"disabled":""}>${icon("arrow-up")}</button><button type="button" class="icon-button" data-fu-down="${i}" aria-label="Mover follow-up ${i+1} para baixo" title="Mover para baixo" ${i===draft.steps.length-1?"disabled":""}>${icon("arrow-down")}</button><button type="button" class="icon-button" data-fu-remove="${i}" aria-label="Remover follow-up ${i+1}" title="Remover follow-up" ${draft.steps.length===1?"disabled":""}>${icon("trash-2")}</button></div></header><div class="followupTiming"><label>${i?"Após o follow-up anterior":"Após a resposta da equipe"}<span class="followupDuration"><input type="number" min="1" max="43200" step="1" value="${amount}" data-fu-wait="${i}" aria-label="Intervalo do follow-up ${i+1}" required><select data-fu-unit="${i}" aria-label="Unidade do follow-up ${i+1}">${[[1,"Minutos"],[60,"Horas"],[1440,"Dias"]].map(([value,text])=>`<option value="${value}" ${unit===value?"selected":""}>${text}</option>`).join("")}</select></span></label><div class="followupTotal"><span>Prazo acumulado</span><output data-fu-total="${i}"></output></div></div><label>Mensagem<textarea rows="3" maxlength="2000" data-fu-body="${i}" aria-label="Mensagem do follow-up ${i+1}" required>${esc(step.body)}</textarea></label><div class="followupMessageMeta"><span>{nome} · {clinica}</span><span data-fu-chars="${i}">${step.body.length} / 2000</span></div></article>`;
    }).join("");updateTotals();icons();
  }
  function previewRows(slots){
    const el=find("#followupSlots");if(el)el.innerHTML=slots.map((stamp,i)=>`<li><span>Envio ${i+1}</span><time>${esc(date(stamp))}</time></li>`).join("");
  }
  function draw(){
    const editable=data.can_manage;
    root.innerHTML=`<form id="followupForm" class="followupSettings"><header class="followupHeader"><div><h2>Régua de acompanhamento</h2><div class="followupStatus"><span class="followupDraft">Não ativada</span><span id="followupSaveState" role="status">${dirty?"Alterações não salvas":data.revision?"Rascunho salvo":"Rascunho inicial"}</span>${!editable?"<span>Somente leitura</span>":""}</div></div><div class="commands">${editable?`<button type="button" id="followupReload" class="icon-button" aria-label="Recarregar régua salva" title="Recarregar régua salva">${icon("rotate-ccw")}</button><button type="submit" class="primary">${icon("save")} Salvar régua</button>`:""}</div></header><p id="followupError" role="alert" hidden></p><fieldset id="followupFields" ${!editable?"disabled":""}><section class="followupSchedule" aria-label="Funcionamento e intervalo"><div><h3>Funcionamento</h3><div class="followupDays">${["Seg","Ter","Qua","Qui","Sex","Sáb","Dom"].map((day,i)=>`<label><input type="checkbox" data-fu-day="${i}" ${draft.weekdays.includes(i)?"checked":""}><span>${day}</span></label>`).join("")}</div></div><div class="followupWindow"><label>Início<input type="time" name="from" value="${esc(draft.from)}" required></label><label>Fim<input type="time" name="to" value="${esc(draft.to)}" required></label><span>Horário de Brasília</span></div><label class="followupGap">Intervalo entre envios por número<span><input type="number" min="1" max="1440" step="1" name="spacing" value="${draft.spacing_seconds/60}" aria-label="Intervalo entre envios por número em minutos" required><span>minutos</span></span></label></section><div class="followupLayout"><section class="followupSequence"><header><h3>Sequência</h3><output id="followupStepCount"></output></header><div id="followupSteps"></div><button type="button" id="followupAdd">${icon("plus")} Adicionar follow-up</button></section><aside class="followupSidebar"><section><h3>Etapas elegíveis</h3><div class="followupStageChoices">${data.stages.map(stage=>`<label class="check"><input type="checkbox" data-fu-stage="${esc(stage.id)}" ${draft.stage_ids.includes(stage.id)?"checked":""}>${esc(stage.name)}</label>`).join("")}</div></section><section><header><h3>Prévia da fila</h3><span class="followupSimulation">Simulação</span></header><label>A partir de<input type="datetime-local" id="followupPreviewAt" aria-label="Data e hora da simulação"></label><button type="button" id="followupPreview">${icon("calendar-clock")} Simular horários</button><ol id="followupSlots"></ol></section><section class="followupSummary"><h3>Interrupções</h3><ul><li>${icon("message-circle-reply")} Resposta do cliente</li><li>${icon("calendar-check")} Retorno combinado</li><li>${icon("user-round-check")} Atendimento humano</li><li>${icon("shield-check")} Pedido para não receber</li></ul></section></aside></div></fieldset></form>`;
    const pause=document.createElement("output");pause.id="followupDailyPause";find(".followupWindow").append(pause);
    const status=find(".followupDraft");status.textContent=data.enabled?"Ligado":"Desligado";status.classList.toggle("followupOn",data.enabled);
    if(editable){
      const power=document.createElement("label");power.className="followupPower";
      power.innerHTML=`<input type="checkbox" role="switch" id="followupPower" aria-label="${data.enabled?"Desligar":"Ligar"} follow-up" ${data.enabled?"checked":""}><span>${data.enabled?"Ligado":"Ligar"}</span>`;
      find(".followupHeader .commands").prepend(power);
      find("#followupPower").onchange=toggle;
    }
    const metrics=document.createElement("div");metrics.className="followupMetrics";
    metrics.innerHTML=[["waiting","Em acompanhamento"],["paused","Pausados"],["completed","Concluídos"]].map(([key,name])=>`<span><strong>${Number(data.metrics?.[key] || 0)}</strong>${name}</span>`).join("");find(".followupHeader").after(metrics);
    steps();previewRows(data.preview);bind();icons();
    lock(false);
  }
  function bind(){
    const form=find("#followupForm");
    form.oninput=event=>{
      const el=event.target;if(!data.can_manage || busy || el.id==="followupPreviewAt")return;
      if(el.dataset.fuWait!==undefined || el.dataset.fuUnit!==undefined){
        const i=Number(el.dataset.fuWait ?? el.dataset.fuUnit);
        draft.steps[i].minutes=Number(find(`[data-fu-wait="${i}"]`).value)*Number(find(`[data-fu-unit="${i}"]`).value);
      }else if(el.dataset.fuBody!==undefined){
        const i=Number(el.dataset.fuBody);draft.steps[i].body=el.value;
        find(`[data-fu-chars="${i}"]`).textContent=`${el.value.length} / 2000`;
      }else if(el.dataset.fuDay!==undefined){
        draft.weekdays=[...root.querySelectorAll("[data-fu-day]:checked")].map(e=>Number(e.dataset.fuDay));
      }else if(el.dataset.fuStage!==undefined){
        draft.stage_ids=[...root.querySelectorAll("[data-fu-stage]:checked")].map(e=>e.dataset.fuStage);
      }else if(el.name==="spacing")draft.spacing_seconds=Number(el.value)*60;
      else if(["from","to"].includes(el.name))draft[el.name]=el.value;
      else return;
      changes();
    };
    form.onclick=async event=>{
      const button=event.target.closest("button");if(!button || busy || !data.can_manage)return;
      if(button.id==="followupReload"){
        if(!canLeave())return;dirty=false;draft=null;return load();
      }
      if(button.id==="followupPreview")return simulate();
      if(button.id==="followupAdd"){
        if(draft.steps.length>=data.max_steps)return;
        draft.steps.push({minutes:1440,title:`Follow-up ${draft.steps.length+1}`,body:""});changes();steps();
        find(`[data-fu-body="${draft.steps.length-1}"]`).focus();return;
      }
      if(button.dataset.fuRemove!==undefined){
        if(draft.steps.length<=1)return;draft.steps.splice(Number(button.dataset.fuRemove),1);changes();steps();return;
      }
      if(button.dataset.fuUp!==undefined || button.dataset.fuDown!==undefined){
        const i=Number(button.dataset.fuUp ?? button.dataset.fuDown),j=i+(button.dataset.fuUp!==undefined?-1:1);
        if(j<0 || j>=draft.steps.length)return;
        [draft.steps[i],draft.steps[j]]=[draft.steps[j],draft.steps[i]];changes();steps();
      }
    };
    form.onsubmit=event=>{event.preventDefault();persist();};
  }
  async function persist(){
    if(!data.can_manage || busy || data.enabled)return false;
    const token=seq;busy=true;lock(true);error();
    try{
      const result=await api("followups/settings",{doctor_id:state.doctor,pipeline_id:state.pipeline,revision:data.revision,config:copy(draft)});
      if(!active(token))return false;data=result;draft=copy(result.config);dirty=false;draw();return true;
    }catch(err){if(active(token))error(err.message);return false;}finally{busy=false;if(active(token))lock(false);}
  }
  async function toggle(){
    const enabled=find("#followupPower").checked;find("#followupPower").checked=Boolean(data.enabled);
    if(busy)return;
    if(enabled && (!find("#followupForm").reportValidity() || !window.confirm("Ligar o follow-up deste funil? O acompanhamento virá marcado nos novos atendimentos elegíveis, exceto contatos pausados. Contatos antigos e tentativas interrompidas não serão retomados.")))return;
    if(enabled && (dirty || !data.revision) && !await persist())return;
    const token=seq;busy=true;lock(true);error();
    try{
      const result=await api("followups/toggle",{doctor_id:state.doctor,pipeline_id:state.pipeline,revision:data.revision,enabled,confirm:enabled});
      if(!active(token))return;data=result;draft=copy(result.config);dirty=false;draw();
    }catch(err){if(active(token))error(err.message);}finally{busy=false;if(active(token))lock(false);}
  }
  function lock(value){
    const fields=find("#followupFields");if(fields)fields.disabled=value || !data.can_manage || data.enabled;
    root?.querySelectorAll(".followupHeader button").forEach(button=>button.disabled=value || data.enabled && button.type==="submit");
    const power=find("#followupPower");if(power)power.disabled=value;
    updateTotals();
  }
  async function simulate(){
    if(!find("#followupForm").reportValidity())return;
    const token=seq,simulation=++previewSeq,button=find("#followupPreview");button.disabled=true;error();
    try{
      const result=await api("followups/preview",{doctor_id:state.doctor,pipeline_id:state.pipeline,config:copy(draft),at_local:find("#followupPreviewAt").value});
      if(active(token) && simulation===previewSeq)previewRows(result.slots);
    }catch(err){if(active(token) && simulation===previewSeq)error(err.message);}finally{if(active(token))button.disabled=false;}
  }
  async function load(){
    const token=++seq;
    root.innerHTML='<p class="connectionNote" role="status">Carregando follow-up…</p>';
    try{
      const result=await api("followups",undefined,{doctor:state.doctor,pipeline:state.pipeline});
      if(!active(token))return;data=result;draft=copy(result.config);dirty=false;draw();
    }catch(err){if(active(token))root.innerHTML=`<p role="alert">${esc(err.message)}</p><button type="button" data-fu-retry>${icon("refresh-cw")} Tentar novamente</button>`;
      root?.querySelector("[data-fu-retry]")?.addEventListener("click",load);
    }
  }
  function mount(target){
    const key=`${state.doctor}:${state.pipeline}`;
    if(root===target && key===scope)return;
    if(key!==scope){data=null;draft=null;dirty=false;busy=false;}
    root=target;scope=key;
    const pipeline=state.data.pipelines.find(p=>p.id===state.pipeline);
    if(!pipeline || pipeline.course_key || pipeline.institute_key || /regen[ -]?code|\b(curso|cursos|mentoria|instituto)\b/i.test(pipeline.name)){
      seq++;root.innerHTML='<p class="emptyState">Follow-up disponível nos funis clínicos.</p>';return;
    }
    if(data && draft){seq++;draw();return;}load();
  }
  function canLeave(){
    if(busy){notice("Aguarde a conclusão do salvamento da régua.");return false;}
    return !dirty || window.confirm("Há alterações não salvas no follow-up. Sair sem salvar?");
  }
  function unmount(){seq++;previewSeq++;root=null;scope="";data=null;draft=null;dirty=false;busy=false;}
  window.addEventListener("beforeunload",event=>{if(dirty || busy){event.preventDefault();event.returnValue="";}});
  return {mount,unmount,canLeave};
};
