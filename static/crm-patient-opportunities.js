"use strict";
window.createCrmPatientOpportunities=function({api,esc,icon,icons,modal,error,journey}){
  const statuses={open:"Aberta",negotiation:"Negociação de Orçamento",won:"Convertida",lost:"Encerrada"};
  const money=value=>(value/100).toLocaleString("pt-BR",{style:"currency",currency:"BRL"});
  const when=value=>new Date(value*1000).toLocaleString("pt-BR",{dateStyle:"short",timeStyle:"short"});
  function mount(host,lead,{active,onReturn}){
    let seq=0,filter="all",reasons=[];
    const valid=token=>host.isConnected && !host.hidden && active() && token===seq;
    async function show(){
      const token=++seq;host.innerHTML='<p class="emptyState" role="status">Carregando oportunidades…</p>';
      try{
        const data=await api("patient-opportunities",undefined,{doctor:lead.doctor_id,lead:lead.id});
        if(!valid(token))return;
        reasons=data.reasons;
        const items=data.items.filter(item=>filter==="all" || filter==="active" && ["open","negotiation"].includes(item.status) || item.status===filter);
        host.innerHTML=`<section class="patientOpportunities"><header><div><h3>Oportunidades do paciente</h3><span>${esc(lead.name)}</span></div><button type="button" class="primary" data-deal-new>${icon("plus")} Oportunidade</button></header><dl><div><dt>Em aberto</dt><dd>${data.summary.open}</dd></div><div><dt>Potencial estimado</dt><dd>${money(data.summary.estimated_open_cents)}</dd></div><div><dt>Convertidas</dt><dd>${data.summary.won}</dd></div></dl><div class="commercialToolbar"><label>Situação<select data-deal-filter><option value="all">Todas</option><option value="active">Em aberto</option><option value="won">Convertidas</option><option value="lost">Encerradas</option></select></label><span>CRM · valores estimados</span></div><div class="commercialItems">${items.map(item=>`<article class="commercialItem patientOpportunity"><header><span class="commercialBadge ${item.status==="lost"?"lost":item.status==="won"?"closed":"active"}">${esc(statuses[item.status])}</span><button type="button" class="icon-button" data-deal-edit="${esc(item.id)}" title="Editar oportunidade" aria-label="Editar ${esc(item.title)}">${icon("square-pen")}</button></header><h3>${esc(item.title)}</h3><strong>${money(item.value_cents)}</strong><p>${esc(item.pipeline_name)} · ${esc(when(item.created_at))}</p>${item.note?`<p class="patientOpportunityNote">${esc(item.note)}</p>`:""}<details><summary>Histórico · ${item.events.length}</summary>${item.events.map(e=>`<div class="eventRow"><small>${esc(when(e.created_at))} · ${esc(e.actor_name)}</small><strong>${esc(statuses[e.after.status])}</strong><p>${esc(e.after.title)} · ${money(e.after.value_cents)}</p>${e.after.note?`<p>${esc(e.after.note)}</p>`:""}</div>`).join("")}</details></article>`).join("") || '<p class="emptyState">Nenhuma oportunidade nesta situação.</p>'}</div></section>`;
        host.querySelector("[data-deal-new]").onclick=()=>edit(null);
        const select=host.querySelector("[data-deal-filter]");select.value=filter;select.onchange=()=>{filter=select.value;show();};
        host.querySelectorAll("[data-deal-edit]").forEach(b=>b.onclick=()=>edit(data.items.find(item=>item.id===b.dataset.dealEdit)));
        icons();
      }catch(e){if(valid(token))host.innerHTML=`<p class="emptyState" role="alert">${esc(e.message)}</p>`;}
    }
    function edit(item){
      seq++;const request=crypto.randomUUID();
      modal(item?"Editar oportunidade":"Nova oportunidade do paciente",`<form id="patientOpportunityForm" class="patientOpportunityForm"><p class="connectionNote">${esc(lead.name)}</p><label>Tratamento / negociação<input name="title" maxlength="160" required value="${esc(item?.title || "")}"></label><div class="formGrid"><label>Valor estimado (R$)<input name="value" type="number" min="0" max="1000000" step="0.01" value="${(item?.value_cents || 0)/100}" required></label><label>Situação<select name="status">${Object.entries(statuses).map(([key,label])=>`<option value="${key}" ${key===(item?.status || "open")?"selected":""}>${esc(label)}</option>`).join("")}</select></label></div><label>Observações / motivo de encerramento<textarea name="note" maxlength="1200" rows="3">${esc(item?.note || "")}</textarea></label>${!item?'<label class="check"><input name="confirm" type="checkbox" required>É uma nova negociação, não uma sessão ou retorno de tratamento já contratado</label>':""}<div class="formActions"><button type="button" data-deal-cancel>Cancelar</button><button type="submit" class="primary">${icon("check")} Salvar oportunidade</button></div></form>`);
      const form=document.getElementById("patientOpportunityForm");
      const closure=document.createElement("div");form.querySelector(".formActions").before(closure);
      const closureFields=()=>{closure.innerHTML=form.elements.status.value==="lost"?journey.fields(reasons):"";
        if(closure.childElementCount){form.elements.closure_reason.value=item?.reason_id || "";form.elements.closure_note.value=item?.note || "";form.elements.closure_reason.onchange=()=>journey.toggle(form);journey.toggle(form);}};
      form.elements.status.onchange=closureFields;closureFields();
      form.querySelector("[data-deal-cancel]").onclick=onReturn;
      form.onsubmit=async event=>{
        event.preventDefault();if(!active())return;
        const button=form.querySelector('[type="submit"]'),values=new FormData(form);button.disabled=true;
        try{
          await api("patient-opportunities",{doctor_id:lead.doctor_id,lead_id:item?.lead_id || lead.id,
            id:item?.id || "",revision:item?.revision,patient_revision:lead.revision,
            request_id:request,confirm_new_negotiation:values.has("confirm"),title:values.get("title"),
            value_cents:Math.round(Number(values.get("value"))*100),status:values.get("status"),note:values.get("note"),closure:closure.childElementCount?journey.read(form):undefined}, {doctor:lead.doctor_id});
          if(active() && form.isConnected && document.getElementById("crmDialog").open)await onReturn();
        }catch(e){if(active() && form.isConnected)error(e.message);}finally{if(form.isConnected)button.disabled=false;}
      };icons();
    }
    return {show,cancel(){seq++;}};
  }
  return {mount};
};
