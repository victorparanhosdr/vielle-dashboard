(() => {
  "use strict";
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? "").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const icon = name => `<i data-lucide="${name}" aria-hidden="true"></i>`;
  const icons = () => window.lucide?.createIcons();
  const money = value => Number(value || 0).toLocaleString("pt-BR",{style:"currency",currency:"BRL"});
  const count = value => Number(value || 0).toLocaleString("pt-BR");
  const dateLabel = (value,year=true) => value ? new Date(value+"T12:00:00").toLocaleDateString("pt-BR",year?{}:{day:"2-digit",month:"2-digit"}) : "—";
  const timeLabel = value => new Date(value).toLocaleString("pt-BR",{day:"2-digit",month:"2-digit",year:"numeric",hour:"2-digit",minute:"2-digit"});
  const initialMonth = new Intl.DateTimeFormat("sv-SE",{timeZone:"America/Sao_Paulo",year:"numeric",month:"2-digit"}).format(new Date());
  const state = {clinic:new URLSearchParams(location.search).get("clinic"),session:null,report:null,page:1,direction:"asc",seq:0,detailSeq:0,historySeq:0,history:null,detail:null,giftYear:null,busy:false,charts:[],started:false};
  let searchTimer, toastTimer, historyTimer, focusReturn;
  const can = action => Boolean(state.session?.user.is_master || state.session?.permissions[state.clinic]?.includes(`birthdays.${action}`));
  const url = (suffix="",params={}) => `/api/birthdays${suffix}?${new URLSearchParams({clinic:state.clinic,...params})}`;
  const refreshController = new ClinicRefreshController({button:$("syncBtn"),status:$("refreshStatus"),getPeriod:()=>({}),onComplete:async clinic=>{if(clinic===state.clinic)await load();}});

  async function request(target,payload) {
    const response = await fetch(target,payload===undefined?{}:{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
    const data = await response.json().catch(()=>({}));
    if(!response.ok || data.ok===false) throw new Error(data.error || "Não foi possível concluir. Tente novamente.");
    return data;
  }
  function notice(message="") {$("notice").textContent=message;$("notice").hidden=!message;}
  function toast(message) {clearTimeout(toastTimer);$("toast").textContent=message;$("toast").hidden=false;toastTimer=setTimeout(()=>$("toast").hidden=true,4000);}
  function destroyCharts() {state.charts.forEach(c=>c.destroy());state.charts=[];}
  function deny() {
    state.seq++;state.detailSeq++;state.historySeq++;clearTimeout(historyTimer);state.history=null;state.report=null;state.detail=null;state.started=false;destroyCharts();
    $("historySync").hidden=true;
    $("patients").replaceChildren();$("metrics").replaceChildren();$("giftBody").replaceChildren();
    $("giftDialog").close();$("validationDialog").close();$("content").hidden=true;$("loading").hidden=true;$("exportBtn").hidden=true;
    notice("Seu usuário não tem acesso aos aniversários desta clínica.");
  }
  function applySession(data) {
    state.session=data;
    const clinic=data.clinics?.find(c=>c.key===state.clinic);
    $("clinicEyebrow").textContent=clinic?.name || "DOC4DOCS";
    $("settingsLink").href=`/settings.html?clinic=${encodeURIComponent(state.clinic)}`;
    $("syncBtn").hidden=!clinic;
    refreshController.setClinic(clinic?state.clinic:"");
    const order=["dashboard","commercial","financial","patient_followup","budget_followup","whatsapp_review","paid_traffic","body_evolution","tasks","birthdays"];
    $("viewTabs").innerHTML=order.filter(key=>data.modules[key] && data.permissions[state.clinic]?.includes(`${key}.view`)).map(key=>{
      const module=data.modules[key], page={tasks:"tasks.html",body_evolution:"body-evolution.html",birthdays:"birthdays.html"}[key];
      const target=page?`/${page}?clinic=${encodeURIComponent(state.clinic)}`:`/?clinic=${encodeURIComponent(state.clinic)}&view=${encodeURIComponent(module.view)}`;
      return `<button type="button" class="tabBtn${key==="birthdays"?" active":""}" data-target="${esc(target)}" ${key==="birthdays"?'aria-current="page"':""}>${esc(module.label)}</button>`;
    }).join("");
    $("exportBtn").hidden=!can("export");
    if(!clinic || !can("view")){deny();return;}
    if(!state.started){state.started=true;load();loadHistory();}
    else if(state.report){renderList();if(state.detail && !state.busy)renderGift();}
    icons();
  }
  function params() {return {month:$("month").value,status:$("giftFilter").value,purchases:$("purchaseFilter").value,q:$("search").value,sort:$("sort").value,direction:state.direction,page:state.page};}
  function renderHistory(data) {
    const completed=`${data.completed_months} de ${data.total_months} meses`;
    $("historySync").hidden=false;
    $("historyBtn").hidden=!data.connected;
    $("historyBtn").disabled=data.running || data.retry_after>0;
    $("historyBtn").querySelector("span").textContent=data.running?"Carregando histórico...":data.complete?"Atualizar histórico":"Carregar histórico desde 2024";
    $("historyStatus").textContent=data.running?`${completed} · ${data.message}`:data.complete?`Histórico consultado: 01/01/2024 a ${dateLabel(data.through)}.`:!data.connected?"Conecte o Clínica Experts para carregar o histórico.":`Histórico parcial: ${completed} consultados desde 2024.${data.phase==="error"?" A importação foi interrompida. Tente novamente para retomar.":""}`;
    icons();
  }
  async function loadHistory() {
    if(!can("view"))return;
    clearTimeout(historyTimer);
    const seq=++state.historySeq;
    try {
      const data=await request(url("/history"));
      if(seq!==state.historySeq || !can("view"))return;
      const finished=state.history?.running && !data.running;
      state.history=data;renderHistory(data);
      if(finished)await load();
      if(seq===state.historySeq && (data.running || data.retry_after>0))historyTimer=setTimeout(loadHistory,3000);
    } catch(error) {if(seq===state.historySeq){$("historySync").hidden=false;$("historyStatus").textContent=error.message;historyTimer=setTimeout(loadHistory,10000);}}
  }
  async function startHistory() {
    if(!can("view"))return;
    $("historyBtn").disabled=true;
    const seq=state.historySeq;
    try {
      const data=await request(url("/history"),{});
      if(seq!==state.historySeq || !can("view"))return;
      state.history={...state.history,...data};
      await loadHistory();
    } catch(error) {if(seq===state.historySeq){$("historyStatus").textContent=error.message;$("historyBtn").disabled=false;}}
  }
  async function load() {
    if(!can("view"))return;
    const seq=++state.seq;
    $("content").setAttribute("aria-busy","true");$("loading").hidden=Boolean(state.report);
    try {
      const data=await request(url("",params()));
      if(seq!==state.seq)return;
      state.report=data;
      if(data.page>data.pages){state.page=data.pages;return load();}
      $("content").hidden=false;render();notice();
    } catch(error) {if(seq===state.seq){notice(error.message);$("content").hidden=true;}}
    finally {if(seq===state.seq){$("loading").hidden=true;$("content").setAttribute("aria-busy","false");}}
  }
  function render() {
    const data=state.report, totals=data.totals;
    $("monthLabel").textContent=new Date(data.month+"-01T12:00:00").toLocaleDateString("pt-BR",{month:"long",year:"numeric"});
    $("metrics").innerHTML=[["calendar-days","Aniversariantes do mês",totals.patients],["shopping-bag","Pacientes com compras",totals.with_purchases],["gift","Presentes enviados",totals.sent],["clock-3","Presentes pendentes",totals.pending]].map(([symbol,label,value])=>`<div class="metric">${icon(symbol)}<div><p>${label}</p><strong>${count(value)}</strong></div></div>`).join("");
    $("todayCount").textContent=data.month===data.today.slice(0,7)?`${count(totals.today)} aniversariante${totals.today===1?"":"s"} hoje`:"";
    $("basis").textContent=data.basis;
    $("syncLabel").textContent=data.synced_at?`Pacientes atualizados ${timeLabel(data.synced_at*1000)}`:"Pacientes ainda não sincronizados";
    const n=data.warnings.missing_birth_dates+data.warnings.missing_sale_amounts;
    $("validation").hidden=!n;$("validation").querySelector("span").textContent=`Conferir dados (${count(n)})`;
    renderList();renderCharts();icons();
  }
  function renderList() {
    const data=state.report;if(!data)return;
    $("listCount").textContent=`${count(data.filtered_count)} de ${count(data.totals.patients)} pacientes`;
    $("giftHeading").textContent=`Presente · ${data.year}`;
    document.querySelectorAll("[data-sort]").forEach(button=>{
      const active=button.dataset.sort===data.sort, direction=data.direction==="desc"?"descending":"ascending";
      button.closest("th").setAttribute("aria-sort",active?direction:"none");
      button.querySelector("svg,i")?.remove();
      button.insertAdjacentHTML("beforeend",icon(active?(data.direction==="desc"?"arrow-down":"arrow-up"):"arrow-up-down"));
    });
    $("patients").innerHTML=data.patients.map(row=>{
      const initials=row.name.trim().split(/\s+/).slice(0,2).map(n=>n[0]).join("").toUpperCase();
      let phone=row.phone.replace(/\D/g,"");if(phone.length===10 || phone.length===11)phone="55"+phone;
      const contact=/^\d{12,15}$/.test(phone)?`<a href="https://wa.me/${phone}" target="_blank" rel="noopener noreferrer" title="Abrir contato de ${esc(row.name)}" aria-label="Abrir contato de ${esc(row.name)}">${icon("message-circle")}</a>`:"";
      const label=(row.gift_sent?can("edit"):can("create") || Boolean(row.gift && can("edit")))?`${row.gift_sent?"Editar":"Registrar"} presente para ${row.name}`:`Ver histórico de ${row.name}`;
      return `<tr class="${row.is_today?"today":""}"><td><div class="patientName"><span class="initials" aria-hidden="true">${esc(initials)}</span><div><strong>${esc(row.name)}${row.is_today?'<span class="todayTag">HOJE</span>':""}</strong><small>${esc(row.phone || row.email || "")}</small></div></div></td>
        <td class="birthDate" title="${row.shifted?"Aniversário em 29/02; considerado em 28/02 neste ano.":""}">${row.shifted?"29/02":dateLabel(row.birthday,false)}<span class="age">${row.age} anos${row.shifted?" · neste ano: 28/02":""}</span></td>
        <td class="number">${count(row.sales)}</td><td class="number" title="${row.missing_amount?`${row.missing_amount} venda(s) sem valor confirmado`:"Compras na base sincronizada"}">${row.missing_amount?"≥ ":""}${money(row.amount)}</td><td>${dateLabel(row.last_sale)}</td>
        <td><span class="giftStatus ${row.gift_sent?"sent":""}">${icon(row.gift_sent?"circle-check":"clock-3")}${row.gift_sent?`Enviado · ${dateLabel(row.gift.sent_at,false)}`:"Não enviado"}</span></td>
        <td><div class="rowActions">${contact}<button type="button" data-gift="${esc(row.uuid)}" title="${esc(label)}" aria-label="${esc(label)}">${icon("gift")}</button></div></td></tr>`;
    }).join("") || '<tr><td colspan="7" class="empty">Nenhum aniversariante encontrado com estes filtros.</td></tr>';
    $("pageLabel").textContent=`Página ${data.page} de ${data.pages}`;
    $("prevPage").disabled=data.page<=1;$("nextPage").disabled=data.page>=data.pages;
    icons();
  }
  function renderCharts() {
    destroyCharts();if(!window.Chart)return;
    const data=state.report, current=data.month===data.today.slice(0,7), today=Number(data.today.slice(-2));
    const reduced=window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const base={responsive:true,maintainAspectRatio:false,animation:reduced?false:{duration:220},plugins:{legend:{display:false},tooltip:{backgroundColor:"#123b30",padding:12,displayColors:false}},scales:{x:{grid:{display:false},border:{display:false},ticks:{font:{size:10},color:"#717f78"}},y:{beginAtZero:true,border:{display:false},grid:{color:"#e6eee9"},ticks:{precision:0,font:{size:10},color:"#717f78"}}}};
    const todayLine={id:"birthdayToday",afterDraw(chart){if(!current)return;const x=chart.scales.x.getPixelForValue(today-1),c=chart.ctx;c.save();c.setLineDash([4,4]);c.strokeStyle="#639fa3";c.lineWidth=1;c.beginPath();c.moveTo(x,chart.chartArea.top);c.lineTo(x,chart.chartArea.bottom);c.stroke();c.restore();}};
    state.charts.push(new Chart($("birthdayChart"),{type:"bar",data:{labels:data.daily.map((_,i)=>String(i+1).padStart(2,"0")),datasets:[{data:data.daily,backgroundColor:data.daily.map((_,i)=>current&&i+1===today?"#3b9291":i%3===0?"#639fa3":"#2f7257"),borderRadius:3,maxBarThickness:16}]},options:{...base,plugins:{...base.plugins,tooltip:{...base.plugins.tooltip,callbacks:{title:items=>`${items[0].label}/${data.month.slice(-2)}`,label:item=>`${item.raw} aniversariante${item.raw===1?"":"s"}`}}}},plugins:[todayLine]}));
    state.charts.push(new Chart($("purchaseChart"),{type:"bar",data:{labels:["Sem compras","1 venda","2 a 4 vendas","5 ou mais vendas"],datasets:[{data:data.distribution,backgroundColor:["#6e9c98","#969e78","#2f7257","#466987"],borderRadius:3,maxBarThickness:25}]},options:{...base,indexAxis:"y",scales:{x:{...base.scales.y,ticks:{...base.scales.y.ticks,precision:0}},y:{...base.scales.x,ticks:{...base.scales.x.ticks,font:{size:12}}}},plugins:{...base.plugins,tooltip:{...base.plugins.tooltip,callbacks:{label:item=>`${item.raw} paciente${item.raw===1?"":"s"}`}}}}}));
  }
  async function openGift(uuid) {
    const seq=++state.detailSeq;focusReturn=document.activeElement;
    try {
      const data=await request(url("/patient",{id:uuid}));if(seq!==state.detailSeq)return;
      state.detail=data;state.giftYear=state.report.year;renderGift();$("giftDialog").showModal();
    } catch(error) {notice(error.message);}
  }
  function closeGift() {if(state.busy)return;state.detailSeq++;$("giftDialog").close();state.detail=null;focusReturn?.focus();}
  function renderGift() {
    const data=state.detail;if(!data)return;
    const year=state.giftYear, existing=data.history.find(r=>r.year===year), enabled=(existing?can("edit"):can("create")) && year<=Number(state.report.today.slice(0,4));
    const years=[...new Set([state.report.year,...data.history.map(r=>r.year),...Array.from({length:5},(_,i)=>Number(state.report.today.slice(0,4))-i)])].sort((a,b)=>b-a);
    const defaultDate=year===Number(state.report.today.slice(0,4))?state.report.today:`${year}-12-31`;
    $("giftDialogTitle").innerHTML=icon("gift")+(existing?.sent_at?"Presente registrado":"Registrar presente");
    $("giftBody").innerHTML=`<p class="dialogPatient">${esc(data.patient.name)}</p><div class="detailMetrics"><span>Vendas vinculadas<strong>${count(data.purchases.sales)}</strong></span><span>Total comprado<strong>${data.purchases.missing_amount?"≥ ":""}${money(data.purchases.amount)}</strong></span></div>
      <form id="giftForm"><div class="fieldGrid"><label class="field">Ano<select name="year" id="giftYear">${years.map(y=>`<option value="${y}" ${y===year?"selected":""}>${y}</option>`).join("")}</select></label><label class="field">Data do envio<input name="sent_at" type="date" required min="${year}-01-01" max="${year===Number(state.report.today.slice(0,4))?state.report.today:`${year}-12-31`}" value="${existing?.sent_at || (enabled?defaultDate:"")}" ${enabled?"":"disabled"}></label></div>
      <label class="field">Presente<input name="description" maxlength="200" required value="${esc(existing?.description || "")}" placeholder="Ex.: kit de cuidados" ${enabled?"":"disabled"}></label><label class="field">Observação<textarea name="note" maxlength="2000" ${enabled?"":"disabled"}>${esc(existing?.note || "")}</textarea></label><p id="giftError" class="formError" role="alert"></p>
      <div class="formActions">${enabled?`<button type="submit" class="primary">${icon("check")}Salvar registro</button>`:""}${existing?.sent_at && can("edit")?`<button type="button" id="undoGift" title="Desfazer envio registrado" aria-label="Desfazer envio registrado">${icon("undo-2")}</button>`:""}<button type="button" id="cancelGift">Fechar</button></div></form>
      <div class="historyHeading"><h3>Histórico de presentes</h3></div>${data.history.map(g=>`<div class="historyItem"><strong>${g.year} · ${esc(g.description)}</strong><p>${g.sent_at?`Enviado em ${dateLabel(g.sent_at)}`:"Envio desfeito"}</p><small>${esc(g.note)}${g.note?"\n":""}${esc(g.actor_name)} · ${timeLabel(g.updated_at)}</small></div>`).join("") || '<p class="age">Nenhum presente registrado.</p>'}
      <div class="historyHeading"><h3>Últimas compras</h3></div>${data.purchases.items.slice(0,5).map(s=>`<div class="historyItem">${dateLabel(s.date)} · <strong>${s.amount===null?"Valor não confirmado":money(s.amount)}</strong></div>`).join("") || '<p class="age">Nenhuma venda vinculada na base sincronizada.</p>'}
      ${data.events.length?`<details class="historyHeading"><summary>Atividade do registro</summary></details><div id="activity" hidden>${data.events.map(e=>`<div class="historyItem"><strong>${e.year} · ${({sent:"Envio registrado",updated:"Registro atualizado",undone:"Envio desfeito"})[e.action] || "Atualização"}</strong><small>${esc(e.actor_name)} · ${timeLabel(e.at)}</small></div>`).join("")}</div>`:""}`;
    $("giftYear").onchange=()=>{state.giftYear=Number($("giftYear").value);renderGift();};
    $("giftForm").onsubmit=save;
    $("cancelGift").onclick=closeGift;
    if($("undoGift"))$("undoGift").onclick=undo;
    const details=$("giftBody").querySelector("details");if(details)details.ontoggle=()=>{$("activity").hidden=!details.open;};
    icons();
  }
  async function save(event) {
    event.preventDefault();if(state.busy)return;
    const form=event.currentTarget, existing=state.detail.history.find(r=>r.year===state.giftYear);
    await mutate("/gift",{patient_uuid:state.detail.patient.uuid,year:state.giftYear,revision:existing?.revision || 0,sent_at:form.elements.sent_at.value,description:form.elements.description.value,note:form.elements.note.value},"Presente registrado.");
  }
  async function undo() {
    if(state.busy || !confirm("Desfazer este envio? O histórico será mantido."))return;
    const reason=prompt("Motivo da correção:");if(reason===null)return;
    const existing=state.detail.history.find(r=>r.year===state.giftYear);
    await mutate("/gift/undo",{patient_uuid:state.detail.patient.uuid,year:state.giftYear,revision:existing.revision,note:reason},"Envio desfeito. Histórico preservado.");
  }
  async function mutate(suffix,payload,message) {
    const seq=state.detailSeq;
    state.busy=true;$("giftForm").querySelectorAll("button,input,textarea,select").forEach(e=>e.disabled=true);$("closeGift").disabled=true;
    try {const data=await request(url(suffix),payload);if(seq!==state.detailSeq || !can("view"))return;state.detail=data;renderGift();await load();toast(message);}
    catch(error){if(seq===state.detailSeq && $("giftError"))$("giftError").textContent=error.message;}
    finally {state.busy=false;$("closeGift").disabled=false;if(state.detail){const enabled=can(state.detail.history.some(r=>r.year===state.giftYear)?"edit":"create") && state.giftYear<=Number(state.report.today.slice(0,4));$("giftForm").querySelectorAll("button,input,textarea,select").forEach(e=>{if(enabled || e.id==="cancelGift" || e.id==="giftYear")e.disabled=false;});}}
  }
  function validation() {
    const w=state.report.warnings;
    $("validationBody").innerHTML=`<div class="validationItem"><strong>${count(w.missing_birth_dates)} pacientes sem nascimento válido</strong><p>Confira a data no cadastro do Clínica Experts e atualize os dados. Cadastros sem data não entram na lista de aniversários.</p></div><div class="validationItem"><strong>${count(w.missing_sale_amounts)} vendas sem valor confirmado</strong><p>A quantidade de vendas é preservada. O total comprado soma apenas os valores confirmados.</p></div><div class="validationItem"><strong>Base do histórico de compras</strong><p>${esc(state.report.basis)} Orçamentos, vendas canceladas ou excluídas não entram na contagem. Em anos não bissextos, aniversários de 29/02 são considerados em 28/02.</p></div>`;
    $("validationDialog").showModal();
  }
  async function exportList() {
    $("exportBtn").disabled=true;
    try {const response=await fetch(url("/export",params()));if(!response.ok){const d=await response.json();throw new Error(d.error || "Não foi possível exportar.");}const blob=await response.blob(),link=document.createElement("a"),target=URL.createObjectURL(blob);link.href=target;link.download=`aniversariantes-${state.clinic}-${$("month").value}.xlsx`;link.click();setTimeout(()=>URL.revokeObjectURL(target),10000);}
    catch(error){notice(error.message);}finally{$("exportBtn").disabled=false;}
  }
  function monthStep(amount) {const d=new Date($("month").value+"-01T12:00:00");d.setMonth(d.getMonth()+amount);$("month").value=`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,"0")}`;state.page=1;load();}
  $("month").value=initialMonth;
  $("month").min="2000-01";$("month").max=`${Number(initialMonth.slice(0,4))+1}-12`;
  $("filters").onsubmit=event=>{event.preventDefault();state.page=1;load();};
  ["month","giftFilter","purchaseFilter"].forEach(id=>$(id).onchange=()=>{state.page=1;load();});
  const defaultDirection = key => ["amount","sales","last_sale"].includes(key)?"desc":"asc";
  $("sort").onchange=()=>{state.direction=defaultDirection($("sort").value);state.page=1;load();};
  document.querySelector("thead").onclick=event=>{
    const button=event.target.closest("[data-sort]");if(!button)return;
    const key=button.dataset.sort;
    state.direction=$("sort").value===key?(state.direction==="asc"?"desc":"asc"):defaultDirection(key);
    $("sort").value=key;state.page=1;load();
  };
  $("historyBtn").onclick=startHistory;
  $("search").oninput=()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>{state.page=1;load();},250);};
  $("previousMonth").onclick=()=>monthStep(-1);$("nextMonth").onclick=()=>monthStep(1);
  $("currentMonth").onclick=()=>{$("month").value=state.report?.today.slice(0,7) || initialMonth;state.page=1;load();};
  $("prevPage").onclick=()=>{state.page--;load();};$("nextPage").onclick=()=>{state.page++;load();};
  $("patients").onclick=event=>{const button=event.target.closest("[data-gift]");if(button)openGift(button.dataset.gift);};
  $("closeGift").onclick=closeGift;$("giftDialog").addEventListener("cancel",event=>{event.preventDefault();closeGift();});
  $("validation").onclick=validation;$("closeValidation").onclick=()=>$("validationDialog").close();
  $("exportBtn").onclick=exportList;$("changeClinicBtn").onclick=()=>location.assign("/");
  $("viewTabs").onclick=event=>{const button=event.target.closest("[data-target]");if(button)location.assign(button.dataset.target);};
  $("mobileTabsToggle").onclick=()=>{const open=$("viewTabs").classList.toggle("open");$("mobileTabsToggle").setAttribute("aria-expanded",String(open));};
  window.addEventListener("doc4docs-session",event=>applySession(event.detail));
  window.addEventListener("doc4docs-clinic-forbidden",deny);
  window.addEventListener("doc4docs-permission-denied",()=>request("/api/auth/me").then(applySession).catch(deny));
  request("/api/auth/me").then(applySession).catch(error=>{notice(error.message);$("loading").hidden=true;});
  icons();
})();
