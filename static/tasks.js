(() => {
  "use strict";
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const icon = name => `<i data-lucide="${name}"></i>`;
  const statuses = {todo:"Não iniciada", doing:"Em andamento", done:"Concluída"};
  const columns = {todo:"Não iniciadas", doing:"Em andamento", done:"Concluídas"};
  const priorities = {low:"Baixa", normal:"Normal", high:"Alta"};
  const state = {clinic:new URLSearchParams(location.search).get("clinic"), session:null, tasks:[], users:[],
    mode:"board", current:null, total:0, next:null, today:"", baseline:"", busy:false, listSeq:0, openSeq:0,
    month:new Date(new Date().getFullYear(), new Date().getMonth(), 1)};
  let searchTimer, toastTimer;
  const icons = () => window.lucide?.createIcons();
  const can = action => Boolean(state.session?.user.is_master || state.session?.permissions[state.clinic]?.includes(`tasks.${action}`));
  const dateLabel = (value, long=false) => value ? new Date(value.includes("T") ? value : value + "T12:00:00").toLocaleDateString("pt-BR", long ? {day:"2-digit",month:"short",year:"numeric"} : {day:"2-digit",month:"2-digit"}) : "Sem prazo";
  const timeLabel = value => new Date(value).toLocaleString("pt-BR", {day:"2-digit",month:"2-digit",year:"numeric",hour:"2-digit",minute:"2-digit"});
  const overdue = task => task.due_date && task.due_date < state.today && task.status !== "done";
  const initials = name => name.trim().split(/\s+/).slice(0,2).map(p => p[0]).join("").toUpperCase();
  const avatars = people => `<span class="avatars">${people.slice(0,3).map(p => `<span class="avatar" title="${esc(p.name)}">${esc(initials(p.name))}</span>`).join("")}${people.length>3 ? `<span class="avatar" title="${esc(people.map(p=>p.name).join(", "))}">+${people.length-3}</span>` : ""}</span>`;
  const taskURL = (suffix="", params={}) => `/api/tasks${suffix}?${new URLSearchParams({clinic:state.clinic,...params})}`;

  async function request(url, payload, raw=false) {
    const options = payload === undefined ? {} : {method:"POST",body:raw ? payload : JSON.stringify(payload),headers:raw ? {} : {"Content-Type":"application/json"}};
    const response = await fetch(url, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.ok === false) {
      const error = new Error(data.error || "Não foi possível concluir. Tente novamente.");
      error.status = response.status;
      throw error;
    }
    return data;
  }
  function notice(message="") { $("notice").textContent=message; $("notice").hidden=!message; }
  function formError(message) { const box=$("formError"); if(box) box.textContent=message; else notice(message); }
  function toast(message) { clearTimeout(toastTimer); $("toast").textContent=message; $("toast").hidden=false; toastTimer=setTimeout(()=>$("toast").hidden=true,3500); }
  function deny(message) {
    state.openSeq++; state.listSeq++; state.tasks=[]; state.current=null; state.baseline="";
    closeDetail(true); $("taskView").innerHTML=""; $("newTask").hidden=true; $("loadMore").hidden=true;
    notice(message);
  }
  function applySession(data) {
    const previous=state.session?.permissions[state.clinic];
    state.session=data;
    const clinics=data.clinics || [];
    $("clinicSelect").innerHTML=clinics.map(c=>`<option value="${esc(c.key)}">${esc(c.name)}</option>`).join("");
    $("clinicSelect").value=state.clinic;
    $("clinicName").textContent=clinics.find(c=>c.key===state.clinic)?.name || "DOC4DOCS";
    $("moduleNav").innerHTML=Object.entries(data.modules).filter(([key]) => data.permissions[state.clinic]?.includes(`${key}.view`)).map(([key,module])=> {
      const target=key==="tasks" ? `/tasks.html?clinic=${encodeURIComponent(state.clinic)}` : key==="body_evolution" ? `/body-evolution.html?clinic=${encodeURIComponent(state.clinic)}` : `/?clinic=${encodeURIComponent(state.clinic)}&view=${encodeURIComponent(module.view)}`;
      return `<a href="${target}" ${key==="tasks" ? 'aria-current="page"' : ""}>${esc(module.label)}</a>`;
    }).join("");
    $("mobileModuleNav").innerHTML=[...$("moduleNav").querySelectorAll("a")].map(a=>`<option value="${esc(a.getAttribute("href"))}" ${a.hasAttribute("aria-current")?"selected":""}>${esc(a.textContent)}</option>`).join("");
    $("newTask").hidden=!can("create");
    if (!clinics.some(c=>c.key===state.clinic) || !can("view")) deny("Seu usuário não tem acesso a Tarefas nesta clínica.");
    else if(state.current && !state.busy && previous && JSON.stringify(previous)!==JSON.stringify(data.permissions[state.clinic])) {renderDetail();toast("Permissões atualizadas");}
    icons();
  }
  function listParams() {
    return {q:$("search").value,assignee:$("assigneeFilter").value,due:$("dueFilter").value,archived:$("archivedFilter").checked ? "1" : "0"};
  }
  async function loadTasks(more=false) {
    if (!can("view")) return;
    const seq=++state.listSeq;
    $("taskView").setAttribute("aria-busy","true"); $("refresh").disabled=true; $("loadMore").disabled=true;
    try {
      const data=await request(taskURL("", {...listParams(),offset:more ? state.next : 0}));
      if (seq!==state.listSeq) return;
      state.tasks=more ? [...state.tasks,...data.tasks] : data.tasks;
      state.users=data.users; state.total=data.total; state.next=data.next_offset; state.today=data.today;
      const selected=$("assigneeFilter").value;
      $("assigneeFilter").innerHTML='<option value="">Todos os responsáveis</option>' + `<option value="${state.session.user.id}">Minhas tarefas</option>` + state.users.filter(p=>p.id!==state.session.user.id).map(p=>`<option value="${p.id}">${esc(p.name)}</option>`).join("");
      $("assigneeFilter").value=selected;
      renderView(); notice();
    } catch(error) { if(seq===state.listSeq) notice(error.message); }
    finally { if(seq===state.listSeq) { $("taskView").setAttribute("aria-busy","false"); $("refresh").disabled=false; $("loadMore").disabled=false; } }
  }
  function card(task) {
    const completed=task.checklist.filter(i=>i.done).length;
    return `<button type="button" class="task-card" data-task="${task.id}" draggable="${can("edit") && !task.archived_at}" aria-current="${state.current?.id===task.id}">
      <span class="card-top"><span class="priority ${task.priority}">${priorities[task.priority]}</span><span class="card-created">Criada ${dateLabel(task.created_at)}</span></span>
      <span class="card-title">${esc(task.title)}</span>${task.description ? `<span class="card-description">${esc(task.description)}</span>` : ""}
      <span class="task-progress"><span style="width:${task.progress}%"></span></span>
      <span class="card-foot"><span class="due ${overdue(task)?"overdue":""}">${icon(overdue(task)?"clock-alert":"calendar")}${dateLabel(task.due_date)}${overdue(task)?" · Atrasada":""}</span>${avatars(task.assignees)}</span>
      <span class="card-meta"><span>${icon("list-checks")}${completed}/${task.checklist.length}</span><span>${icon("message-square")}${task.comment_count || 0}</span><span>${icon("paperclip")}${task.attachment_count || 0}</span><span class="percent">${task.progress}%</span></span>
    </button>`;
  }
  function renderView() {
    $("totalCount").textContent=state.total;
    $("loadedCount").textContent=state.tasks.length<state.total ? `${state.tasks.length} de ${state.total} tarefas` : state.total ? `${state.total} tarefa${state.total===1?"":"s"}` : "";
    $("loadMore").hidden=state.next===null;
    const view=$("taskView");
    if(state.mode==="board") view.innerHTML=`<div class="board">${Object.entries(columns).map(([key,label])=> {
      const tasks=state.tasks.filter(t=>t.status===key);
      return `<section class="lane" data-status="${key}" aria-label="${label}"><div class="lane-heading"><span class="status-dot ${key}"></span><h2>${label}</h2><span class="count">${tasks.length}</span>${can("create") && !$("archivedFilter").checked ? `<button type="button" data-new-status="${key}" title="Nova tarefa: ${label}" aria-label="Nova tarefa: ${label}">${icon("plus")}</button>` : ""}</div>${tasks.map(card).join("") || '<p class="empty">Nenhuma tarefa</p>'}</section>`;
    }).join("")}</div>`;
    else if(state.mode==="list") view.innerHTML=state.tasks.length ? `<div class="task-table-wrap"><table class="task-table"><thead><tr><th>Tarefa</th><th>Status</th><th>Responsáveis</th><th>Vencimento</th><th>Progresso</th><th>Criação</th></tr></thead><tbody>${state.tasks.map(t=>`<tr><td class="title-cell"><button type="button" data-task="${t.id}">${esc(t.title)}</button></td><td>${statuses[t.status]}</td><td>${avatars(t.assignees)}</td><td><span class="due ${overdue(t)?"overdue":""}">${dateLabel(t.due_date,true)}</span></td><td>${t.progress}%</td><td>${dateLabel(t.created_at,true)}</td></tr>`).join("")}</tbody></table></div>` : '<p class="empty">Nenhuma tarefa encontrada</p>';
    else renderCalendar();
    icons();
  }
  function renderCalendar() {
    const first=new Date(state.month), start=new Date(first); start.setDate(1-first.getDay());
    const iso=d=>`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,"0")}-${String(d.getDate()).padStart(2,"0")}`;
    const days=Array.from({length:42},(_,i)=> { const day=new Date(start); day.setDate(start.getDate()+i); return day; });
    $("taskView").innerHTML=`<div class="calendar-top"><h2>${esc(first.toLocaleDateString("pt-BR",{month:"long",year:"numeric"}))}</h2><button type="button" data-month="today">Hoje</button><button type="button" class="icon-button" data-month="-1" title="Mês anterior" aria-label="Mês anterior">${icon("chevron-left")}</button><button type="button" class="icon-button" data-month="1" title="Próximo mês" aria-label="Próximo mês">${icon("chevron-right")}</button></div><div class="calendar-scroll"><div class="calendar-grid">${["Dom","Seg","Ter","Qua","Qui","Sex","Sáb"].map(d=>`<div class="weekday">${d}</div>`).join("")}${days.map(day=>`<div class="calendar-day ${day.getMonth()!==first.getMonth()?"outside":""} ${iso(day)===state.today?"today":""}"><span class="day-number">${day.getDate()}</span>${state.tasks.filter(t=>t.due_date===iso(day)).map(t=>`<button type="button" class="calendar-task ${t.status} ${overdue(t)?"overdue":""}" data-task="${t.id}" title="${esc(t.title)}">${esc(t.title)}</button>`).join("")}</div>`).join("")}</div></div><p class="calendar-foot">${state.tasks.filter(t=>!t.due_date).length} tarefa(s) sem vencimento${state.tasks.length<state.total ? " · Carregue as demais tarefas para ver todos os prazos." : ""}</p>`;
  }
  function payload() {
    const form=$("taskForm"); if(!form) return null;
    return {title:form.elements.title.value,description:form.elements.description.value,status:form.elements.status.value,
      priority:form.elements.priority.value,start_date:form.elements.start_date.value,due_date:form.elements.due_date.value,
      progress:Number(form.elements.progress.value),assignees:[...form.querySelectorAll('[name="assignee"]:checked')].map(i=>Number(i.value)),
      checklist:[...form.querySelectorAll(".check-row")].map(row=>({id:row.dataset.item,text:row.querySelector('[type="text"]').value,done:row.querySelector('[type="checkbox"]').checked}))};
  }
  function dirty() { return state.baseline && JSON.stringify(payload())!==state.baseline; }
  function mayLeave() { return !state.busy && (!dirty() || confirm("Há alterações não salvas. Deseja descartá-las?")); }
  function closeDetail(force=false) {
    if(!force && !mayLeave()) return false;
    state.openSeq++; state.current=null; state.baseline="";
    $("inspector").hidden=true; $("inspector").innerHTML=""; $("workspace").classList.remove("has-detail"); document.body.classList.remove("detail-open");
    const url=new URL(location.href); url.searchParams.delete("task"); history.replaceState(null,"",url);
    renderView(); return true;
  }
  async function openTask(id, force=false) {
    if(!force && !mayLeave()) return;
    const seq=++state.openSeq;
    try {
      const data=await request(taskURL(`/${id}`));
      if(seq!==state.openSeq) return;
      state.current=data.task; renderDetail();
      const url=new URL(location.href); url.searchParams.set("task",id); history.replaceState(null,"",url); renderView();
    } catch(error) { notice(error.message); }
  }
  function newTask(status="todo") {
    if(!can("create") || !mayLeave()) return;
    state.openSeq++; state.current={title:"",description:"",status,priority:"normal",start_date:"",due_date:"",assignees:[],checklist:[],progress:0};
    const url=new URL(location.href); url.searchParams.delete("task"); history.replaceState(null,"",url);
    renderDetail(); $("taskForm").elements.title.focus();
  }
  function checkRow(item, enabled) {
    return `<div class="check-row" data-item="${esc(item.id)}"><input type="checkbox" ${item.done?"checked":""} ${enabled?"":"disabled"} aria-label="Concluir item"><input type="text" value="${esc(item.text)}" maxlength="300" required ${enabled?"":"disabled"} aria-label="Descrição do item"><button type="button" class="icon-button" data-remove-item ${enabled?"":"disabled"} title="Remover item" aria-label="Remover item">${icon("x")}</button></div>`;
  }
  function renderDetail() {
    const t=state.current, isNew=!t.id, enabled=isNew ? can("create") : can("edit") && !t.archived_at, disabled=enabled ? "" : "disabled";
    const people=[...state.users,...t.assignees.filter(p=>!state.users.some(u=>u.id===p.id))];
    $("inspector").innerHTML=`<div class="detail-top"><h2 id="detailHeading">${isNew?"Nova tarefa":"Detalhes da tarefa"}</h2><div class="detail-tools">${!isNew?`<button type="button" class="icon-button" id="shareTask" title="Copiar link da tarefa" aria-label="Copiar link da tarefa">${icon("link")}</button><button type="button" class="icon-button" id="reloadTask" title="Reabrir versão atual" aria-label="Reabrir versão atual">${icon("refresh-cw")}</button>`:""}<button type="button" class="icon-button" id="closeDetail" title="Fechar detalhes" aria-label="Fechar detalhes">${icon("x")}</button></div></div>
      ${t.archived_at?`<div class="archive-banner">Arquivada em ${dateLabel(t.archived_at,true)}${can("delete")?'<button type="button" id="restoreTask">Restaurar</button>':""}</div>`:""}
      ${!isNew?`<p class="detail-meta">Criada por ${esc(t.creator_name)} · ${timeLabel(t.created_at)}<br>Atualizada ${timeLabel(t.updated_at)}${t.completed_at?`<br>Concluída ${timeLabel(t.completed_at)}`:""}</p>`:""}
      <form id="taskForm"><label class="field title"><span>Tarefa</span><input name="title" value="${esc(t.title)}" maxlength="200" required ${disabled} placeholder="Nome da tarefa"></label>
      <label class="field"><span>Descrição</span><textarea name="description" maxlength="10000" ${disabled}>${esc(t.description)}</textarea></label>
      <div class="field-grid"><label class="field"><span>Status</span><select name="status" aria-label="Status" ${disabled}>${Object.entries(statuses).map(([key,label])=>`<option value="${key}" ${t.status===key?"selected":""}>${label}</option>`).join("")}</select></label><label class="field"><span>Prioridade</span><select name="priority" aria-label="Prioridade" ${disabled}>${Object.entries(priorities).map(([key,label])=>`<option value="${key}" ${t.priority===key?"selected":""}>${label}</option>`).join("")}</select></label>
      <label class="field"><span>Início</span><input type="date" name="start_date" value="${t.start_date}" ${disabled}></label><label class="field"><span>Vencimento</span><input type="date" name="due_date" value="${t.due_date}" ${disabled}></label></div>
      <div class="field"><span>Responsáveis</span><div class="members">${people.map(p=>`<label><input type="checkbox" name="assignee" value="${p.id}" ${t.assignees.some(u=>u.id===p.id)?"checked":""} ${disabled}>${esc(p.name)}${!state.users.some(u=>u.id===p.id)?" (acesso removido)":""}</label>`).join("") || '<span class="muted">Nenhum responsável disponível</span>'}</div></div>
      <div class="section-heading"><h3>Checklist</h3>${enabled?`<button type="button" id="addItem">${icon("plus")} Item</button>`:""}</div><div class="checklist" id="checklist">${t.checklist.map(i=>checkRow(i,enabled)).join("")}</div>
      <label class="field" style="margin-top:16px"><span>Progresso <output id="progressValue">${t.progress}%</output></span><input type="range" name="progress" min="0" max="100" value="${t.progress}" ${disabled} aria-label="Progresso"></label>
      <p id="formError" class="form-error" role="alert"></p><div class="form-actions">${enabled?`<button type="submit" class="primary">${icon("check")} ${isNew?"Criar tarefa":"Salvar alterações"}</button>`:""}${!isNew && !t.archived_at && can("delete")?`<button type="button" class="icon-button" id="archiveTask" title="Arquivar tarefa" aria-label="Arquivar tarefa">${icon("archive")}</button>`:""}</div></form>
      ${!isNew?`<div class="section-heading"><h3>Anexos <span id="fileCount"></span></h3>${enabled?`<button type="button" id="attachFile">${icon("paperclip")} Anexar</button><input type="file" id="filePicker" hidden multiple accept=".pdf,.png,.jpg,.jpeg,.webp,.xlsx,.xls,.csv,.doc,.docx,.txt,.pptx,.zip">`:""}</div><p id="fileStatus" class="muted" role="status"></p><div id="attachments"></div>
      <div class="section-heading"><h3>Atividade</h3></div>${enabled?'<form class="comment-form" id="commentForm"><textarea name="comment" required maxlength="10000" aria-label="Comentário ou atualização" placeholder="Comentário ou atualização..."></textarea><button type="submit">Publicar atualização</button></form>':""}<div class="events" id="events"></div><button type="button" id="olderEvents" hidden>Ver histórico anterior</button>`:""}`;
    $("inspector").hidden=false; $("workspace").classList.add("has-detail"); document.body.classList.add("detail-open");
    $("taskForm").addEventListener("submit",saveTask);
    $("taskForm").addEventListener("input",updateProgress);
    $("closeDetail").onclick=()=>closeDetail();
    if($("addItem")) $("addItem").onclick=()=> { if($$(".check-row").length>=100) return; $("checklist").insertAdjacentHTML("beforeend",checkRow({id:crypto.randomUUID(),text:"",done:false},true)); icons(); updateProgress(); $("checklist").lastElementChild.querySelector('[type="text"]').focus(); };
    $("checklist").onclick=event=> { const button=event.target.closest("[data-remove-item]"); if(button && !state.busy) { button.closest(".check-row").remove(); updateProgress(); } };
    if($("archiveTask")) $("archiveTask").onclick=()=>archive(true);
    if($("restoreTask")) $("restoreTask").onclick=()=>archive(false);
    if($("reloadTask")) $("reloadTask").onclick=()=>openTask(t.id);
    if($("shareTask")) $("shareTask").onclick=async()=> { try { await navigator.clipboard.writeText(location.href); toast("Link da tarefa copiado"); } catch(_) { toast("Não foi possível copiar o link."); } };
    if($("attachFile")) $("attachFile").onclick=()=>$("filePicker").click();
    if($("filePicker")) $("filePicker").onchange=uploadFiles;
    if($("commentForm")) $("commentForm").onsubmit=postComment;
    if($("olderEvents")) $("olderEvents").onclick=olderEvents;
    updateProgress(); state.baseline=JSON.stringify(payload()); renderAux(); icons();
  }
  const $$ = selector => [...$("taskForm").querySelectorAll(selector)];
  function updateProgress() {
    const form=$("taskForm"); if(!form) return;
    const checks=$$('.check-row [type="checkbox"]'), done=checks.filter(c=>c.checked).length;
    if(form.elements.status.value==="done") form.elements.progress.value=100;
    else if(checks.length) form.elements.progress.value=Math.round(done/checks.length*100);
    form.elements.progress.disabled=state.busy || (!state.current?.id ? !can("create") : !can("edit") || Boolean(state.current.archived_at)) || checks.length>0 || form.elements.status.value==="done";
    $("progressValue").textContent=form.elements.progress.value+"%";
  }
  function setBusy(value) {
    state.busy=value;
    if(!$("taskForm")) return;
    $("inspector").querySelectorAll("button").forEach(button=> { if(value) {button.dataset.wasDisabled=button.disabled?"1":"0"; button.disabled=true;} else button.disabled=button.dataset.wasDisabled==="1"; });
    $("taskForm").querySelectorAll("input,select,textarea").forEach(input=> { if(value) {input.dataset.wasDisabled=input.disabled?"1":"0";input.disabled=true;} else input.disabled=input.dataset.wasDisabled==="1"; });
    updateProgress();
  }
  async function saveTask(event) {
    event.preventDefault(); if(state.busy) return;
    const data=payload(), id=state.current.id;
    if(id) data.revision=state.current.revision;
    setBusy(true); formError("");
    try {
      const result=await request(taskURL(id?`/${id}`:""),data);
      state.current=result.task; state.busy=false; renderDetail(); renderView();
      const url=new URL(location.href); url.searchParams.set("task",result.task.id); history.replaceState(null,"",url);
      toast(id?"Tarefa atualizada":"Tarefa criada"); await loadTasks();
    } catch(error) { formError(error.message); }
    finally { if(state.busy) setBusy(false); }
  }
  async function archive(archived) {
    if(!mayLeave() || (archived && !confirm("Arquivar esta tarefa? O histórico e os anexos serão preservados."))) return;
    setBusy(true);
    try { const data=await request(taskURL(`/${state.current.id}/archive`),{archived,revision:state.current.revision}); state.current=data.task; state.busy=false; renderDetail(); await loadTasks(); toast(archived?"Tarefa arquivada":"Tarefa restaurada"); }
    catch(error) { formError(error.message); }
    finally { if(state.busy) setBusy(false); }
  }
  function renderAux() {
    const t=state.current; if(!t?.id || !$("attachments")) return;
    $("fileCount").textContent=`(${t.attachments.length})`;
    $("attachments").innerHTML=t.attachments.map(f=>`<div class="attachment">${icon("file")}<a href="${taskURL(`/${f.id}/file`)}" download="${esc(f.name)}">${esc(f.name)}</a><small>${Math.max(1,Math.round(f.size/1024))} KB</small></div>`).join("") || '<p class="muted">Nenhum anexo</p>';
    $("events").innerHTML=t.events.map(eventHTML).join(""); $("olderEvents").hidden=!t.before; icons();
  }
  function mergeAux(task) {
    // Keep the editor's original revision so concurrent changes cannot be overwritten after a comment/upload.
    if(!state.current || state.current.id!==task.id) return;
    Object.assign(state.current,{attachments:task.attachments,events:task.events,before:task.before,updated_at:task.updated_at});
    renderAux();
  }
  function valueLabel(key,value) {
    if(key==="status") return statuses[value]; if(key==="priority") return priorities[value];
    if(key==="assignees") return value.map(p=>p.name).join(", ") || "Sem responsáveis";
    if(key==="checklist") return `${value.filter(i=>i.done).length}/${value.length} concluídos`;
    if(key==="progress") return value+"%"; if(key.endsWith("_date")) return value ? dateLabel(value,true) : "Sem data";
    return String(value || "Vazio");
  }
  function eventHTML(event) {
    const actions={created:"criou a tarefa",updated:"atualizou a tarefa",comment:"publicou uma atualização",attachment:"anexou um arquivo",archived:"arquivou a tarefa",restored:"restaurou a tarefa"};
    const fields={title:"Nome",description:"Descrição",status:"Status",priority:"Prioridade",start_date:"Início",due_date:"Vencimento",assignees:"Responsáveis",checklist:"Checklist",progress:"Progresso"};
    const changes=event.action==="updated" ? `<ul>${Object.entries(event.details).map(([key,change])=>`<li>${fields[key] || esc(key)}: ${key==="description" ? "texto alterado" : `${esc(valueLabel(key,change.before))} → ${esc(valueLabel(key,change.after))}`}${key==="checklist" ? `<div>${esc(change.after.map(i=>(i.done?"✓ ":"□ ")+i.text).join(" · "))}</div>` : ""}</li>`).join("")}</ul>` : "";
    return `<div class="event"><strong>${esc(event.actor_name)}</strong> ${actions[event.action] || "atualizou a tarefa"}<time datetime="${event.created_at}">${timeLabel(event.created_at)}</time>${changes}${event.action==="comment" ? `<p>${esc(event.details.text)}</p>` : ""}${event.action==="attachment" ? `<p>${esc(event.details.name)}</p>` : ""}</div>`;
  }
  async function olderEvents() {
    const id=state.current.id, before=state.current.before; $("olderEvents").disabled=true;
    try { const data=await request(taskURL(`/${id}/events`,{before})); if(state.current?.id!==id) return; state.current.events.push(...data.events); state.current.before=data.before; renderAux(); }
    catch(error) { toast(error.message); } finally { if($("olderEvents")) $("olderEvents").disabled=false; }
  }
  async function postComment(event) {
    event.preventDefault(); if(state.busy) return;
    const form=event.currentTarget, text=form.elements.comment.value;
    setBusy(true);
    try { const data=await request(taskURL(`/${state.current.id}/comments`),{text}); mergeAux(data.task); form.reset(); toast("Atualização publicada"); await loadTasks(); }
    catch(error) { formError(error.message); } finally { setBusy(false); }
  }
  async function uploadFiles() {
    const files=[...$("filePicker").files]; if(state.busy || !files.length) return;
    setBusy(true);
    try {
      for(const file of files) {
        if(!file.size || file.size>10*1024*1024) throw new Error(`${file.name}: limite de 10 MB por arquivo.`);
        $("fileStatus").textContent=`Enviando ${file.name}...`;
        const data=await request(taskURL(`/${state.current.id}/attachments`,{name:file.name}),file,true);
        mergeAux(data.task);
      }
      $("fileStatus").textContent="Arquivos anexados"; await loadTasks();
    } catch(error) { const status=$("fileStatus"); if(status) status.textContent=error.message; else notice(error.message); }
    finally { if($("filePicker")) $("filePicker").value=""; setBusy(false); }
  }
  async function moveTask(id,status) {
    if(!can("edit") || state.busy || !mayLeave()) return;
    const task=state.tasks.find(t=>t.id===id); if(!task || task.status===status) return;
    try { await request(taskURL(`/${id}`),{status,revision:task.revision}); if(state.current?.id===id) await openTask(id,true); await loadTasks(); toast("Status atualizado"); }
    catch(error) { notice(error.message); await loadTasks(); }
  }
  $("taskView").addEventListener("click",event=> {
    const task=event.target.closest("[data-task]"), add=event.target.closest("[data-new-status]"), month=event.target.closest("[data-month]");
    if(task) openTask(task.dataset.task); if(add) newTask(add.dataset.newStatus);
    if(month) { state.month=month.dataset.month==="today" ? new Date(new Date().getFullYear(),new Date().getMonth(),1) : new Date(state.month.getFullYear(),state.month.getMonth()+Number(month.dataset.month),1); renderView(); }
  });
  $("taskView").addEventListener("dragstart",event=> { const card=event.target.closest("[data-task]"); if(card && can("edit")) { event.dataTransfer.setData("text/plain",card.dataset.task); event.dataTransfer.effectAllowed="move"; } });
  $("taskView").addEventListener("dragover",event=> { const lane=event.target.closest(".lane"); if(lane && can("edit") && !$("archivedFilter").checked) { event.preventDefault(); lane.classList.add("drag-over"); } });
  $("taskView").addEventListener("dragleave",event=>event.target.closest(".lane")?.classList.remove("drag-over"));
  $("taskView").addEventListener("drop",event=> { const lane=event.target.closest(".lane"); if(lane) {event.preventDefault();lane.classList.remove("drag-over");if(!$("archivedFilter").checked) moveTask(event.dataTransfer.getData("text/plain"),lane.dataset.status);} });
  document.querySelectorAll("[data-mode]").forEach(button=>button.onclick=()=> {state.mode=button.dataset.mode;document.querySelectorAll("[data-mode]").forEach(b=>b.setAttribute("aria-pressed",String(b===button)));renderView();});
  $("newTask").onclick=()=>newTask(); $("refresh").onclick=()=>loadTasks(); $("loadMore").onclick=()=>loadTasks(true);
  $("search").oninput=()=> {clearTimeout(searchTimer);searchTimer=setTimeout(()=>loadTasks(),300);};
  ["assigneeFilter","dueFilter","archivedFilter"].forEach(id=>$(id).onchange=()=>loadTasks());
  function navigate(href) {if(!mayLeave()) return false; state.baseline=""; location.href=href; return true;}
  $("clinicSelect").onchange=event=> { if(!navigate(`/tasks.html?clinic=${encodeURIComponent(event.target.value)}`)) event.target.value=state.clinic; };
  $("mobileModuleNav").onchange=event=> {if(!navigate(event.target.value)) event.target.value=`/tasks.html?clinic=${encodeURIComponent(state.clinic)}`;};
  window.addEventListener("beforeunload",event=> { if(dirty() || state.busy) {event.preventDefault();event.returnValue="";} });
  document.addEventListener("keydown",event=> {if(event.key==="Escape" && !$("inspector").hidden) closeDetail();});
  window.addEventListener("doc4docs-session",event=> {if(state.session) applySession(event.detail);});
  window.addEventListener("doc4docs-clinic-forbidden",()=>deny("Seu acesso a esta clínica foi removido."));
  window.addEventListener("doc4docs-permission-denied",()=> { request("/api/auth/me").then(applySession).catch(()=>{}); });
  async function boot() {
    try { const data=await request("/api/auth/me"); applySession(data); if(!can("view")) return; await loadTasks(); const task=new URLSearchParams(location.search).get("task"); if(task && /^[a-f0-9]{32}$/.test(task)) await openTask(task); }
    catch(error) { notice(error.message); }
  }
  icons(); boot();
})();
