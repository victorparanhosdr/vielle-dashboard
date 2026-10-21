"use strict";
window.createCrmReminders=function({api,esc,icon,icons,getUser,onOpenLead,notice}){
  let host=null,seq=0,dialogSeq=0,items=[],filters={owner:"mine",status:"pending",search:"",page:1},dialog=null;
  const request=(action,payload,query={})=>api(`reminders${action?"/"+action:""}`,payload,{doctor:"",...query});
  const when=item=>new Date(item.due_at*1000).toLocaleString("pt-BR",{timeZone:"America/Sao_Paulo",day:"2-digit",month:"2-digit",year:"numeric",hour:"2-digit",minute:"2-digit"});
  const labels={pending:"Pendente",done:"Concluída",cancelled:"Cancelada"};
  const option=(value,label,selected)=>`<option value="${esc(value)}" ${selected?"selected":""}>${esc(label)}</option>`;
  function close(){dialogSeq++;dialog?.close();dialog?.remove();dialog=null;}
  function show(title,body){
    close();dialog=document.createElement("dialog");dialog.className="crmReminderDialog";
    dialog.setAttribute("aria-labelledby","reminderDialogTitle");
    dialog.innerHTML=`<header><h2 id="reminderDialogTitle">${esc(title)}</h2><button type="button" class="icon-button" data-close-reminder aria-label="Fechar lembrete">${icon("x")}</button></header>${body}<p data-reminder-error role="alert" hidden></p>`;
    document.body.append(dialog);dialog.querySelector("[data-close-reminder]").onclick=close;
    dialog.addEventListener("cancel",()=>{dialogSeq++;});dialog.showModal();icons();return dialog;
  }
  function localDate(add=0){
    const parts=new Intl.DateTimeFormat("en-CA",{timeZone:"America/Sao_Paulo",year:"numeric",month:"2-digit",day:"2-digit"}).formatToParts(new Date());
    const get=key=>parts.find(p=>p.type===key).value;
    const date=new Date(`${get("year")}-${get("month")}-${get("day")}T12:00:00Z`);date.setUTCDate(date.getUTCDate()+add);return date.toISOString().slice(0,10);
  }
  async function open(lead,item=null){
    const box=show(item?"Editar lembrete":"Novo lembrete",`<p class="reminderClient">${icon("contact-round")}<strong>${esc(lead.name || item?.client_name || "Cliente")}</strong></p><p role="status">Carregando responsáveis…</p>`),version=dialogSeq;
    try{
      const data=await request("options",undefined,{lead:lead.id});
      if(version!==dialogSeq || !box.open)return;
      box.querySelector('[role="status"]').remove();
      const assigned=String(item?.assignee_id || getUser()?.id || "");
      box.insertAdjacentHTML("beforeend",`<form class="reminderForm"><label>Tarefa<input name="title" maxlength="160" value="${esc(item?.title || "Retomar contato")}" required></label><label>Data e horário <small>Horário de Brasília</small><input type="datetime-local" name="due_local" value="${esc(item?.due_local || localDate(1)+"T09:00")}" required></label><div class="reminderShortcuts" role="group" aria-label="Agendar lembrete"><button type="button" data-day="1">Amanhã às 09h</button><button type="button" data-day="7">Em 7 dias</button><button type="button" data-month>Em 1 mês</button></div><label>Responsável<select name="assignee_id" required>${data.assignees.map(u=>option(u.id,u.name,u.id===assigned)).join("")}</select></label><label>Observação<textarea name="note" rows="3" maxlength="2000">${esc(item?.note || "")}</textarea></label><footer><button type="submit" class="primary">${icon("alarm-clock-check")} Salvar lembrete</button></footer></form>`);
      const form=box.querySelector("form"),field=form.elements.due_local;
      box.querySelectorAll("[data-day]").forEach(button=>button.onclick=()=>{field.value=localDate(Number(button.dataset.day))+"T09:00";});
      box.querySelector("[data-month]").onclick=()=>{const date=new Date(localDate()+"T12:00:00Z"),day=date.getUTCDate();date.setUTCDate(1);date.setUTCMonth(date.getUTCMonth()+2);date.setUTCDate(0);date.setUTCDate(Math.min(day,date.getUTCDate()));field.value=date.toISOString().slice(0,10)+"T09:00";};
      const requestId=crypto.randomUUID();
      form.onsubmit=async event=>{
        event.preventDefault();const button=form.querySelector('[type="submit"]'),error=box.querySelector("[data-reminder-error]");button.disabled=true;error.hidden=true;
        try{
          await request("save",{...Object.fromEntries(new FormData(form)),lead_id:lead.id,request_id:requestId,...(item?{id:item.id,revision:item.revision}:{})});
          if(version!==dialogSeq)return;close();notice("Lembrete salvo. O responsável será avisado no CRM no horário marcado.");await refresh();window.dispatchEvent(new Event("crm-reminders-changed"));
        }catch(e){if(box.isConnected){error.textContent=e.message;error.hidden=false;}}finally{button.disabled=false;}
      };
      icons();form.elements.title.focus();
    }catch(e){if(version===dialogSeq){box.querySelector("[data-reminder-error]").textContent=e.message;box.querySelector("[data-reminder-error]").hidden=false;}}
  }
  async function chooseLead(){
    const box=show("Lembrete para qual cliente?",'<label>Buscar cliente<input type="search" data-lead-search maxlength="160" placeholder="Nome ou telefone"></label><div data-lead-results role="status"></div>');
    let searchSeq=0,timer;
    async function search(){const version=++searchSeq;try{const data=await request("leads",undefined,{search:box.querySelector("input").value});if(!box.open || version!==searchSeq)return;box.querySelector("[data-lead-results]").innerHTML=data.leads.map(l=>`<button type="button" data-reminder-lead="${esc(l.id)}"><strong>${esc(l.name)}</strong><small>${esc(l.doctor_name)} · ${esc(l.pipeline_name)}</small>${icon("chevron-right")}</button>`).join("") || '<p class="emptyState">Nenhum cliente encontrado.</p>';box.querySelectorAll("[data-reminder-lead]").forEach(b=>b.onclick=()=>open(data.leads.find(l=>l.id===b.dataset.reminderLead)));icons();}catch(e){if(box.isConnected)box.querySelector("[data-lead-results]").textContent=e.message;}}
    box.querySelector("input").oninput=()=>{clearTimeout(timer);searchSeq++;timer=setTimeout(search,250);};await search();box.querySelector("input").focus();
  }
  async function act(action,item,button){
    button.disabled=true;
    try{await request(action,{id:item.id,revision:item.revision});await refresh();window.dispatchEvent(new Event("crm-reminders-changed"));}
    catch(e){notice(e.message);await refresh();}finally{button.disabled=false;}
  }
  function markup(data){
    const counts=data.counts || {};
    return `<header class="reminderPageHead"><div><h2>Agenda de tarefas</h2><span>Horário de Brasília</span></div><button class="primary" data-new-reminder>${icon("plus")} Lembrete</button></header><div class="reminderMetrics">${[["pending","Pendentes","list-todo"],["today","Para hoje","calendar-days"],["overdue","Vencidas","alarm-clock"],["done","Concluídas","circle-check"]].map(([key,label,symbol])=>`<button data-status="${key}" aria-pressed="${filters.status===key}" class="${key}">${icon(symbol)}<span>${label}</span><strong>${counts[key] || 0}</strong></button>`).join("")}</div><form class="reminderFilters" role="search"><label>Responsável<select name="owner">${option("mine","Minhas tarefas",filters.owner==="mine")}${option("team","Toda a equipe",filters.owner==="team")}</select></label><label>Situação<select name="status">${[["pending","Pendentes"],["today","Hoje"],["overdue","Vencidas"],["upcoming","Próximas"],["done","Concluídas"],["cancelled","Canceladas"],["all","Todas"]].map(([v,l])=>option(v,l,v===filters.status)).join("")}</select></label><label>Buscar<input name="search" type="search" value="${esc(filters.search)}" maxlength="160" placeholder="Tarefa ou cliente"></label><button class="icon-button" type="submit" aria-label="Filtrar tarefas" title="Filtrar tarefas">${icon("search")}</button><button class="icon-button" type="button" data-refresh-reminders aria-label="Atualizar tarefas" title="Atualizar tarefas">${icon("refresh-cw")}</button></form><div class="reminderList">${data.items.map(item=>`<article class="reminderRow ${item.overdue?"overdue":""}" data-reminder="${esc(item.id)}"><div class="reminderDate">${icon(item.status==="done"?"circle-check":item.overdue?"alarm-clock":"calendar-clock")}<time datetime="${new Date(item.due_at*1000).toISOString()}">${when(item)}</time><small>${item.overdue?"Vencida":labels[item.status]}</small></div><div class="reminderDescription"><h3>${esc(item.title)}</h3><button class="reminderClientLink" data-reminder-open>${esc(item.client_name)}${icon("arrow-up-right")}</button><span>${esc(item.doctor_name)} · ${esc(item.pipeline_name)} · ${esc(item.stage_name)}</span>${item.note?`<p>${esc(item.note)}</p>`:""}<small>${icon("user-round")} ${esc(item.assignee_name)}</small></div><div class="reminderActions">${item.status==="pending"?`<button class="icon-button" data-reminder-action="done" title="Concluir tarefa" aria-label="Concluir tarefa">${icon("check")}</button><button class="icon-button" data-reminder-edit title="Editar ou reagendar" aria-label="Editar ou reagendar">${icon("calendar-cog")}</button><button class="icon-button" data-reminder-action="cancel" title="Cancelar tarefa" aria-label="Cancelar tarefa">${icon("x")}</button>`:`<button data-reminder-action="reopen">${icon("rotate-ccw")} Reabrir</button>`}</div></article>`).join("") || `<div class="reminderEmpty">${icon("calendar-check-2")}<h3>Nenhuma tarefa por aqui</h3><p>${filters.status==="overdue"?"Você está em dia com este filtro.":"Os lembretes dos clientes aparecerão aqui."}</p><button data-new-reminder>${icon("plus")} Criar lembrete</button></div>`}</div><footer class="reminderPagination"><span>${data.total} tarefa(s)</span><button class="icon-button" data-page="-1" ${data.page<=1?"disabled":""} aria-label="Página anterior">${icon("chevron-left")}</button><span>Página ${data.page} de ${Math.max(1,Math.ceil(data.total/40))}</span><button class="icon-button" data-page="1" ${data.page*40>=data.total?"disabled":""} aria-label="Próxima página">${icon("chevron-right")}</button></footer>`;
  }
  async function refresh(){
    if(!host)return;const root=host,version=++seq;root.setAttribute("aria-busy","true");
    try{
      const data=await request("",undefined,filters);if(version!==seq || root!==host)return;items=data.items;root.innerHTML=markup(data);
      root.querySelectorAll("[data-new-reminder]").forEach(b=>b.onclick=chooseLead);
      root.querySelector("[data-refresh-reminders]").onclick=refresh;
      const form=root.querySelector("form");form.onsubmit=e=>{e.preventDefault();filters={...filters,...Object.fromEntries(new FormData(form)),page:1};refresh();};
      form.querySelectorAll("select").forEach(select=>select.onchange=()=>form.requestSubmit());
      root.querySelectorAll("[data-status]").forEach(b=>b.onclick=()=>{filters.status=b.dataset.status;filters.page=1;refresh();});
      root.querySelectorAll("[data-page]").forEach(b=>b.onclick=()=>{filters.page+=Number(b.dataset.page);refresh();});
      root.querySelectorAll("[data-reminder]").forEach(row=>{const item=items.find(t=>t.id===row.dataset.reminder);row.querySelector("[data-reminder-open]").onclick=()=>onOpenLead(item);row.querySelector("[data-reminder-edit]")?.addEventListener("click",()=>open({id:item.lead_id,name:item.client_name},item));row.querySelectorAll("[data-reminder-action]").forEach(b=>b.onclick=()=>act(b.dataset.reminderAction,item,b));});icons();
    }catch(e){if(root===host && version===seq){root.innerHTML='<p class="reminderLoadError" role="alert"></p>';root.firstChild.textContent=e.message;}}
    finally{if(root===host)root.setAttribute("aria-busy","false");}
  }
  setInterval(()=>{if(host && !document.hidden && !document.querySelector("dialog[open]") && host.querySelector('input[name="search"]')?.value===filters.search)refresh();},30000);
  return {open,refresh,request,when,mount:root=>{host=root;refresh();},unmount:()=>{seq++;host=null;},reset:()=>{close();seq++;host=null;items=[];filters={owner:"mine",status:"pending",search:"",page:1};},showOverdue:()=>{filters.status="overdue";filters.owner="mine";filters.page=1;refresh();}};
};
