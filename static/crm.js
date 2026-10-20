(() => {
  "use strict";
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const icon = name => `<i data-lucide="${name}" aria-hidden="true"></i>`;
  const icons = () => window.lucide?.createIcons();
  const money = value => (Number(value || 0) / 100).toLocaleString("pt-BR", {style:"currency",currency:"BRL",maximumFractionDigits:0});
  const date = value => value ? new Date(typeof value === "number" ? value * 1000 : value + "T12:00:00").toLocaleDateString("pt-BR", {day:"2-digit",month:"2-digit"}) : "Sem data";
  const requestedMode=new URLSearchParams(location.search).get("mode");
  const state = {data:null,doctor:new URLSearchParams(location.search).get("doctor") || "",pipeline:new URLSearchParams(location.search).get("pipeline") || "",business:"all",mode:["dashboard","board","inbox","campaigns","channels","templates","forms","bots"].includes(requestedMode)?requestedMode:"board",seq:0,search:"",filters:{},sort:"updated",session:null};
  let presentation="board",listPicker=null,listSeed=null,listScope="";
  let radarCollapsed=false;
  try{radarCollapsed=sessionStorage.getItem("doc4docs.crm.radarCollapsed")==="1";}catch{}
  const tierLabels={bronze:"Bronze",silver:"Prata",gold:"Ouro"};
  const locationLabels={capelinha:"Capelinha","belo-horizonte":"Belo Horizonte","sao-paulo":"São Paulo"};
  function serviceLocationBadge(lead) {
    const label=locationLabels[lead.service_location],pending=!label;
    const source=lead.service_location_source==="ddd"?" · pré-definido pelo DDD":lead.service_location_source==="manual"?" · definido manualmente":"";
    const title=`Local de Atendimento: ${label || "não definido · pendente"}${source}`;
    return `<span class="serviceLocation ${pending?"pending":""}" title="${esc(title)}" aria-label="${esc(title)}">${icon(pending?"map-pin-off":"map-pin")}<span>${esc(label || "Definir local")}</span></span>`;
  }
  const patientRefreshes=new Set();
  const initials=name=>String(name).split(/\s+/).slice(0,2).map(n=>n[0]).join("").toUpperCase();
  const exactMoney=value=>(Number(value || 0)/100).toLocaleString("pt-BR",{style:"currency",currency:"BRL"});
  function pendingQuotesSummary(leads) {
    const contacts=[...new Map(leads.map(l=>[l.contact_id,l])).values()];
    const ready=l=>l.customer_history?.quotes_state==="ready" && Number.isSafeInteger(l.customer_history.pending_quotes_cents);
    return {cents:contacts.filter(ready).reduce((sum,l)=>sum+l.customer_history.pending_quotes_cents,0),
      pending:contacts.filter(l=>l.customer_history?.quotes_state!=="unlinked" && !ready(l)).length};
  }
  function pendingQuotesValue(lead, compact=false) {
    const h=lead.customer_history || {},cls=compact?"leadQuoteValue":"opportunityQuotes";
    if(h.quotes_state==="unlinked")return "";
    if(h.quotes_state!=="ready" || !Number.isSafeInteger(h.pending_quotes_cents))return `<span class="${cls} pending" title="Orçamentos do Clínica Experts indisponíveis ou com dados a conferir">${icon("circle-help")}<small>Orç. a conferir</small></span>`;
    if(!h.pending_quotes_count)return compact?"":'<span class="opportunityQuotes empty">Sem orçamentos pendentes</span>';
    const title=`${h.open_quotes_count} aberto(s) e ${h.expired_quotes_count} vencido(s) com ${lead.customer_value?.doctor_name || doctor()?.name || "este doutor"} · Clínica Experts · ${exactMoney(h.pending_quotes_cents)}`;
    return `<span class="${cls}" title="${esc(title)}"><small>${compact?"Orç. pendentes":`${h.pending_quotes_count} orçamento(s) pendente(s)`}</small><strong>${exactMoney(h.pending_quotes_cents)}</strong></span>`;
  }
  function customerValueDescription(data) {
    return data?.tier?`Cliente ${tierLabels[data.tier]} · ${exactMoney(data.sales_cents)} em ${data.sales_count} venda(s) válida(s) com ${data.doctor_name} · Clínica Experts`:data?.state==="unlinked"?"Cliente sem histórico vinculado":"Classificação a conferir · histórico indisponível ou parcial";
  }
  function customerValueBadge(data) {
    if(!data?.tier){
      const label=data?.state==="unlinked"?"Novo":"A conferir";
      return `<span class="customerTier unclassified" title="${esc(data?.state==="unlinked"?"Sem histórico de compras vinculado ao Clínica Experts":"Histórico indisponível ou parcial. Classificação não atribuída.")}">${icon(data?.state==="unlinked"?"user-round-plus":"circle-help")}${label}</span>`;
    }
    const title=customerValueDescription(data);
    return `<span class="customerTier ${data.tier}" title="${esc(title)}" aria-label="${esc(title)}">${icon(data.tier==="gold"?"crown":"medal")}${tierLabels[data.tier]}</span>`;
  }
  function customerValuePanel(data) {
    if(!data?.tier)return `<section class="customerValueSummary unclassified">${customerValueBadge(data)}<p>${esc(data?.state==="unlinked"?"Sem compras vinculadas":"Histórico a conferir")}</p><small>Clínica Experts · classificação pendente</small></section>`;
    const base=data.tier==="silver"?state.data.customer_tier_rules.silver_cents:0;
    const progress=data.next_cents?Math.max(0,Math.min(100,(data.sales_cents-base)/(data.next_cents-base)*100)):100;
    return `<section class="customerValueSummary ${data.tier}"><div class="customerValueHeading">${customerValueBadge(data)}<span>${data.sales_count} ${data.sales_count===1?"compra":"compras"}</span></div><span class="customerValueLabel">Total comprado</span><strong>${exactMoney(data.sales_cents)}</strong><small>Com este doutor · Clínica Experts</small><progress value="${progress}" max="100" aria-label="Progresso da classificação ${tierLabels[data.tier]}"></progress><p>${data.next_tier?`${exactMoney(data.remaining_cents)} para ${tierLabels[data.next_tier]}`:"Maior faixa de relacionamento"}</p></section>`;
  }
  const statusLabels = {draft:"Rascunho",running:"Em andamento",paused:"Pausada",done:"Concluída",cancelled:"Cancelada",open:"Conectado",close:"Desconectado",disconnected:"Não conectado",connecting:"Conectando",sent:"Enviada ao conector",received:"Recebida",unknown:"Sem confirmação",queued:"Na fila",skipped:"Não enviada",sending:"Em envio"};
  const patientLabel = value => ({matched:"Paciente do Clínica Experts",new:"Lead novo · não localizado na base",review:"Cadastro a conferir · telefone compartilhado",unchecked:"Cadastro a conferir · base indisponível"}[value] || "Cadastro a conferir");
  const appointmentTime=item=>`${new Date(`${item.date}T12:00:00`).toLocaleDateString("pt-BR")} · ${item.time || "Horário não informado"}`;
  function appointmentBadge(data, compact=false) {
    if(!data || ["unlinked","identity_pending"].includes(data.state))return "";
    if(compact){
      if(data.state!=="ready")return `<span class="appointmentInline muted" title="Agenda do Clínica Experts indisponível">${icon("calendar-clock")}Agenda indisponível</span>`;
      if(!data.item)return Object.values(data.warnings || {}).some(Boolean)?`<span class="appointmentInline muted" title="Dados de agendamento precisam de conferência">${icon("calendar-clock")}Agenda a conferir</span>`:"";
      const item=data.item,label={next:"Próximo agendamento",last:"Último agendamento",ongoing:"Agendamento em andamento",completed:"Atendimento realizado no período",scheduled:"Agendado no período",missed:"Não compareceu no período",matched:"Agendamento no período"}[data.kind];
      const when=new Date(`${item.date}T12:00:00`).toLocaleDateString("pt-BR",{day:"2-digit",month:"2-digit",...(item.date.slice(0,4)!==state.data?.today?.slice(0,4)?{year:"numeric"}:{})});
      const description=`${label}: ${appointmentTime(item)} · ${item.procedure} · ${item.status_label} · Clínica Experts`;
      return `<span class="appointmentInline ${esc(data.kind)}" title="${esc(description)}" aria-label="${esc(description)}">${icon(data.kind==="completed"?"calendar-check":"calendar-days")}<span>${esc({next:"Próx.",last:"Último",ongoing:"Agora",completed:"Realizado",scheduled:"Agendado",missed:"Faltou",matched:"Agenda"}[data.kind])} ${esc(when)} · ${esc(item.time || "sem horário")}</span></span>`;
    }
    if(data.state!=="ready")return `<span class="appointmentEmpty">${icon("calendar-clock")} Agenda indisponível</span>`;
    if(!data.item)return `<span class="appointmentEmpty">${icon("calendar-days")} ${Object.values(data.warnings || {}).some(Boolean)?"Agenda a conferir":"Sem agendamento ativo"}</span>`;
    const item=data.item;
    return `<span class="leadAppointment ${esc(data.kind)}" title="Clínica Experts · Doutor(a) selecionado(a)"><span class="appointmentHeading">${icon("calendar-check")}${esc({next:"Próximo agendamento",last:"Último agendamento",ongoing:"Agendamento em andamento"}[data.kind])}</span><strong class="appointmentTime">${esc(appointmentTime(item))}</strong><span class="appointmentProcedure">${esc(item.procedure)}</span><span class="appointmentStatus">${esc(item.status_label)} · Clínica Experts</span></span>`;
  }
  const pill = value => `<span class="statusPill ${esc(value)}">${esc(statusLabels[value] || value)}</span>`;
  const option = (value,label,selected=false) => `<option value="${esc(value)}" ${selected?"selected":""}>${esc(label)}</option>`;
  const pipeline = () => state.data?.pipelines.find(p=>p.id===state.pipeline);
  const doctor = () => state.data?.doctors.find(p=>p.id===state.doctor);
  const selectedLeads = () => (state.data?.leads || []).filter(l=>l.pipeline_id===state.pipeline);
  const importer=window.createCrmImporter({api,modal,error,icons,esc,load});
  const courses=window.createCrmCourses({api,esc,icon,icons});
  const courseScope=()=>state.mode==="board"?Boolean(pipeline()?.course_key):state.mode==="inbox" && state.business==="course";
  const avatars=window.createCrmAvatars({api,esc,enabled:doc=>doc===state.doctor && state.data?.gateway_configured && state.data.channels.some(c=>c.state==="open")});
  const clientMode=()=>["board","inbox"].includes(state.mode);
  const filterNames={tier:"Classificação",identity:"Cadastro",service_location:"Local de Atendimento",sales:"Vendas",quotes:"Orçamentos",min_spent_cents:"Comprado a partir de",max_spent_cents:"Comprado até",min_sales:"Mínimo de vendas",appointment_status:"Agendamentos",appointment_from:"Agenda de",appointment_to:"Agenda até",appointment_no_upcoming:"Retorno",course_status:"Pagamento Kiwify"};
  const filterChoices={tier:tierLabels,service_location:{...locationLabels,pending:"Pendente de definição"},identity:{matched:"Vinculado",new:"Novo",review:"A conferir"},sales:{yes:"Com venda fechada",no:"Sem venda fechada"},quotes:{active:"Em aberto",pending:"Pendentes",expired:"Vencidos",lost:"Perdidos",none:"Sem pendentes"},appointment_status:{any:"Com agendamento",completed:"Realizados",scheduled:"Agendados",missed:"Não compareceu",none:"Sem agendamento"},appointment_no_upcoming:{true:"Sem próximo agendamento"}};
  filterChoices.course_status={paid:"Pagamento aprovado",pending:"Aguardando pagamento",refunded:"Reembolso / chargeback",none:"Sem pedido localizado"};
  function appointmentRange(kind) {
    const today=state.data?.today || new Date().toLocaleDateString("en-CA",{timeZone:"America/Sao_Paulo"});
    const anchor=new Date(`${today}T12:00:00Z`), year=anchor.getUTCFullYear(), month=anchor.getUTCMonth();
    const iso=d=>d.toISOString().slice(0,10);
    if(kind==="last_month")return [iso(new Date(Date.UTC(year,month-1,1))),iso(new Date(Date.UTC(year,month,0)))];
    if(kind==="this_month")return [iso(new Date(Date.UTC(year,month,1))),iso(new Date(Date.UTC(year,month+1,0)))];
    const end=new Date(anchor);end.setUTCDate(end.getUTCDate()+30);return [today,iso(end)];
  }
  function syncAppointmentRange() {
    $("appointmentRange").value=["last_month","this_month","next_30"].find(kind=>{const [start,end]=appointmentRange(kind);return $("filterAppointmentFrom").value===start && $("filterAppointmentTo").value===end;}) || "";
  }
  function fillFilterForm() {
    Object.keys(filterNames).forEach(key=>{
      const field=$("crmFilterForm").elements.namedItem(key),value=state.filters[key];
      if(field.type==="checkbox")field.checked=Boolean(value);
      else field.value=value==null?"":key.endsWith("_cents")?value/100:value;
    });
    syncAppointmentRange();
  }
  function paintFilters() {
    document.querySelectorAll(".crmFilterGroup").forEach(group=>{group.hidden=group.hasAttribute("data-course-filters")?!courseScope():courseScope();});
    $("crmFilterForm").querySelector(".crmFilterActions>span").textContent=courseScope()?"Kiwify · pedidos do curso":"Clínica Experts · histórico sincronizado · doutor selecionado";
    const entries=Object.entries(state.filters).filter(([,v])=>v!=="" && v!=null);
    $("filterCount").hidden=!entries.length;$("filterCount").textContent=entries.length;
    $("activeCrmFilters").hidden=!entries.length;
    $("activeCrmFilters").innerHTML=entries.map(([key,value])=>{
      const display=key==="appointment_from" || key==="appointment_to"?value.split("-").reverse().join("/"):key.endsWith("_cents")?exactMoney(value):value;
      const label=`${filterNames[key]}: ${filterChoices[key]?.[value] || display}`;
      return `<button class="filterChip" data-remove-filter="${key}" title="Remover ${esc(label)}" aria-label="Remover ${esc(label)}">${esc(label)}${icon("x")}</button>`;
    }).join("");
    $("filterDoctorScope").textContent=doctor()?.name || "Doutor(a) selecionado(a)";
    $("crmFilterCoverage").hidden=!state.data?.filter_incomplete;
    $("crmFilterCoverage").textContent=`${state.data?.filter_incomplete || 0} cliente(s) com histórico indisponível ou parcial não incluído(s) nos filtros selecionados.`;
    icons();
  }
  function clearFilters() {
    state.filters={};fillFilterForm();paintFilters();$("crmFilterError").hidden=true;load();
  }
  function notice(message="") { $("notice").textContent=message;$("notice").hidden=!message; }
  async function api(action="", payload, query={}) {
    const filters=payload===undefined && ["","inbox"].includes(action) && clientMode()?{filters:JSON.stringify({...state.filters,search:state.search,sort:state.sort})}:{};
    const view=!action && payload===undefined && state.mode==="board" && presentation==="board"?{view:"kanban",pipeline:state.pipeline}:{};
    const res=await fetch(`/api/crm${action?"/"+action:""}?${new URLSearchParams({clinic:"vielle",doctor:state.doctor,...filters,...view,...query})}`, payload===undefined ? {} : {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
    const data=await res.json().catch(()=>({}));
    if(!res.ok || !data.ok){const error=new Error(data.error || "Não foi possível concluir a operação.");error.status=res.status;throw error;}
    return data;
  }
  async function load() {
    const seq=++state.seq;
    kanban.destroy();
    bulk.unmount();
    $("crmView").setAttribute("aria-busy","true");
    try {
      const data=await api();
      if(seq!==state.seq) return;
      state.data=data;state.doctor=data.doctor_id;
      $("setup").hidden=Boolean(data.doctors.length);$("crmApp").hidden=!data.doctors.length;
      $("addDoctor").hidden=!data.can_manage;$("customerTierRules").hidden=!data.can_manage;
      if(!data.doctors.length && !data.can_manage)$("setup").hidden=true;
      $("doctor").innerHTML=data.doctors.map(d=>option(d.id,d.name,d.id===state.doctor)).join("");
      if(!data.pipelines.some(p=>p.id===state.pipeline)) state.pipeline=data.pipelines[0]?.id || "";
      $("pipeline").innerHTML=data.pipelines.length ? data.pipelines.map(p=>option(p.id,p.name,p.id===state.pipeline)).join("") : option("","Nenhum funil");
      renderGame();render();paintFilters();notice(!data.doctors.length && !data.can_manage?"Nenhum profissional ou funil liberado para seu usuario no CRM. Consulte o Master.":"");
    } catch(error) {notice(error.message);}
    finally {if(seq===state.seq) $("crmView").setAttribute("aria-busy","false");}
  }
  function patientRefreshMessage(lead) {
    return lead.experts_status==="matched"?"Contato vinculado ao Clínica Experts.":lead.experts_status==="new"?"Telefone não encontrado na base atualizada.":"Vínculo a conferir: não houve associação automática.";
  }
  async function patientRefreshJob(action, payload, query) {
    let job=await api(action,payload,query);
    if(!job.started && !job.running && job.retry_after)throw new Error(`Aguarde ${job.retry_after}s para buscar novamente.`);
    const deadline=Date.now()+180000;
    do {
      if(Date.now()>deadline)throw new Error("A busca continua no servidor. Confira novamente em instantes.");
      await new Promise(resolve=>setTimeout(resolve,1500));
      job=await api(action,undefined,query);
    } while(job.running);
    if(job.phase!=="done")throw new Error("Não foi possível atualizar o Clínica Experts. Os dados anteriores foram preservados.");
    return job;
  }
  async function refreshPatient(lead,button,status) {
    if(patientRefreshes.has(lead.id))return null;
    const doc=lead.doctor_id;
    patientRefreshes.add(lead.id);button.disabled=true;
    status.hidden=false;status.textContent="Buscando no Clínica Experts…";
    try {
      const job=await patientRefreshJob("patient-refresh",{id:lead.id,doctor_id:doc},{id:lead.id,doctor:doc});
      if(state.doctor===doc)await load();
      return job.lead;
    } finally {
      patientRefreshes.delete(lead.id);
      if(button.isConnected)button.disabled=false;
    }
  }
  function renderGame() {
    const g=state.data.game;
    $("xp").value=g.progress;$("xpLabel").textContent=`${g.xp} XP`;
    $("levelLabel").textContent=`Nível ${g.level} · ${["Primeiros passos","Ritmo de equipe","Conexões que avançam","Equipe em sintonia"][Math.min(g.level-1,3)]}`;
    [["followupMission",g.followups,5],["advanceMission",g.advances,3],["winMission",g.wins,1]].forEach(([id,count,total])=>{
      $(id).textContent=`${Math.min(count,total)} / ${total}`;$(id).title=`${count} realizados hoje`;$(id).closest(".mission").classList.toggle("done",count>=total);
    });
  }
  function leadCard(l) {
    if(l.course_key)return `<button class="leadCard courseLeadCard" draggable="true" data-lead="${esc(l.id)}" data-tier="unclassified" aria-label="${esc(l.name+" · "+courses.label(l))}"><span class="leadCardHead">${avatars.markup(l.name,l.contact_id,state.doctor,"","leadAvatar")}<span class="leadPerson"><strong>${esc(l.name)}</strong>${courses.badge(l)}</span></span><span class="leadMeta"><span class="leadTitle" title="${esc(l.title)}">${esc(l.email || l.title)}</span></span><span class="cardBottom"><small>${icon("phone-forwarded")}Contato ${date(l.next_action)}</small><strong>${l.course_data?.paid_count?exactMoney(l.course_data.paid_cents):l.value_cents?exactMoney(l.value_cents):""}</strong></span></button>`;
    const agenda=l.appointment_filter?.item?l.appointment_filter:l.appointment;
    return `<button class="leadCard" draggable="true" data-tier="${esc(l.customer_value?.tier || "unclassified")}" data-lead="${esc(l.id)}" title="${esc(customerValueDescription(l.customer_value))}" aria-label="${esc(l.name+" · "+customerValueDescription(l.customer_value))}">
      <span class="leadCardHead">${avatars.markup(l.name,l.contact_id,state.doctor,"","leadAvatar")}<span class="leadPerson"><strong>${esc(l.name)}</strong><span class="patientIdentity ${l.experts_status==="matched"?"matched":""}" title="${esc(patientLabel(l.experts_status))}">${icon(l.experts_status==="matched"?"user-check":"user-round")}${l.experts_status==="matched"?"Clínica Experts":l.experts_status==="new"?"Primeiro contato":"Cadastro a conferir"}</span></span><span class="leadCardAside">${customerValueBadge(l.customer_value)}${pendingQuotesValue(l,true)}</span></span>
      <span class="leadMeta"><span class="leadTitle" title="${esc(l.title)}">${esc(l.title)}</span>${serviceLocationBadge(l)}</span>${appointmentBadge(agenda,true)}
      <span class="cardBottom"><small title="Próximo contato comercial" class="${l.next_action && l.next_action<state.data.today?"overdue":""}">${icon("phone-forwarded")}Contato ${date(l.next_action)}</small><small title="${l.opted_out?"Descadastrado · mensagens bloqueadas":"Mensagens disponíveis · padrão da clínica"}">${icon(l.opted_out?"shield-off":"shield-check")}</small></span></button>`;
  }
  function board() {
    const p=pipeline(), leads=selectedLeads(), data=state.data.kanban;
    const pendingLocations=state.data.service_location_pending?.[state.pipeline] || 0;
    if(!p) return '<p class="emptyState">Nenhum funil cadastrado para este doutor.</p>';
    if(data?.pipeline_id!==p.id)return '<p class="emptyState" role="status">Carregando funil…</p>';
    if(p.course_key)return courseBoard(p,data,leads);
    const summary=data.summary,{cents:quoteTotal,pending:quotePending}=summary.quotes;
    const tiers=["gold","silver","bronze"].map(tier=>({tier,count:summary.tiers[tier]})),knownCount=tiers.reduce((total,t)=>total+t.count,0);
    const leaderboard=`<section class="teamLeaderboard"><h2>${icon("trophy")} Conquistas da equipe</h2>${state.data.game.team.map((m,i)=>`<div class="rankRow"><b class="rankPosition">${String(i+1).padStart(2,"0")}</b><span>${esc(m.actor)}</span><strong>${m.points} XP</strong></div>`).join("") || '<p>As conquistas aparecerão com os primeiros avanços.</p>'}</section>`;
    return `<div class="boardSummary"><span>${icon("users-round")}<span><strong>${summary.total}</strong> oportunidades</span></span><span title="Clínica Experts · Abertos e vencidos · ${esc(doctor().name)} · funil e filtros atuais${quotePending?` · Total parcial: ${quotePending} cadastro(s) a conferir`:""}">${icon("file-clock")}<span><strong>${exactMoney(quoteTotal)}${quotePending?"*":""}</strong> em orçamentos pendentes${quotePending?` · ${quotePending} a conferir`:""}</span></span><span>${icon("badge-check")}<span><strong>${summary.won}</strong> ganhos</span></span><button id="toggleSalesRadar" class="icon-button" aria-controls="crmSalesRadar" aria-expanded="${!radarCollapsed}" aria-label="${radarAction()}" title="${radarAction()}">${icon(radarCollapsed?"panel-right-open":"panel-right-close")}</button></div><div class="crmWorkspace ${radarCollapsed?"radarCollapsed":""}"><div class="crmBoard" data-pipeline="${esc(p.id)}" aria-label="Funil ${esc(p.name)}">${p.stages.map((s,index)=>{
      const items=leads.filter(l=>l.stage_id===s.id);
      const column=data.columns.find(c=>c.stage_id===s.id),quotes=column.quotes;
      return `<section class="crmColumn" data-stage="${esc(s.id)}" data-kind="${esc(s.kind)}" aria-label="${esc(s.name)}"><div class="columnHead"><div><i class="stageDot" aria-hidden="true"></i><strong>${esc(s.name)}</strong><span>${column.total}</span></div><small title="Orçamentos pendentes · Abertos e vencidos · Clínica Experts${quotes.pending?` · Parcial: ${quotes.pending} cadastro(s) a conferir`:""}">${exactMoney(quotes.cents)}${quotes.pending?"*":""}<b>${String(index+1).padStart(2,"0")}</b></small></div><div class="columnLeads" tabindex="0" role="region" aria-label="Oportunidades em ${esc(s.name)}"><div class="columnCards">${items.map(leadCard).join("") || `<div class="emptyColumn">${icon(s.kind==="won"?"flag":"circle-dashed")}<span>Nenhuma oportunidade</span></div>`}</div>${kanban.footer(column)}</div></section>`;
    }).join("")}</div><aside id="crmSalesRadar" class="salesRadar" ${radarCollapsed?"hidden":""}><div class="radarBlock"><h2>${icon("radar")} Radar comercial</h2><div class="radarFocus"><div class="radarNumber">${summary.overdue}</div><span>próximos passos<br>vencidos</span></div><p>${summary.stale} oportunidades sem atualização há 7 dias.</p>${pendingLocations?`<button id="pendingLocations" class="locationPendingLink">${icon("map-pin-off")} ${pendingLocations} com Local de Atendimento pendente</button>`:""}</div><div class="radarBlock customerPortfolio"><h2>${icon("gem")} Relacionamento</h2>${tiers.map(({tier,count})=>`<div class="tierDistribution ${tier}"><div><span>${icon(tier==="gold"?"crown":"medal")}${tierLabels[tier]}</span><strong>${count}</strong></div><meter min="0" max="${Math.max(knownCount,1)}" value="${count}" aria-label="${count} clientes ${tierLabels[tier]}"></meter></div>`).join("")}<p>${summary.unclassified} sem classificação · funil atual</p></div>${leaderboard}</aside></div>`;
  }
  function courseBoard(p,data,leads){
    const summary=data.summary,c=summary.course;
    return `<div class="boardSummary"><span>${icon("users-round")}<strong>${summary.total}</strong> oportunidades</span><span>${icon("graduation-cap")}<strong>${c.paid_students}</strong> com pagamento aprovado</span><span title="Pagamentos aprovados vinculados aos contatos deste filtro · Kiwify">${icon("wallet-cards")}<strong>${exactMoney(c.paid_cents)}</strong> Kiwify</span><span>${icon("clock-3")}<strong>${c.pending}</strong> pedidos pendentes</span><button id="toggleSalesRadar" class="icon-button" aria-controls="crmSalesRadar" aria-expanded="${!radarCollapsed}" aria-label="${radarAction()}" title="${radarAction()}">${icon(radarCollapsed?"panel-right-open":"panel-right-close")}</button></div><div class="crmWorkspace ${radarCollapsed?"radarCollapsed":""}"><div class="crmBoard" data-pipeline="${esc(p.id)}" aria-label="Funil ${esc(p.name)}">${p.stages.map((s,index)=>{const column=data.columns.find(c=>c.stage_id===s.id);return `<section class="crmColumn" data-stage="${esc(s.id)}" data-kind="${esc(s.kind)}" aria-label="${esc(s.name)}"><div class="columnHead"><div><i class="stageDot" aria-hidden="true"></i><strong>${esc(s.name)}</strong><span>${column.total}</span></div><small title="Valores das oportunidades no funil; não equivalem a pagamentos aprovados">${exactMoney(column.value_cents)}<b>${String(index+1).padStart(2,"0")}</b></small></div><div class="columnLeads" tabindex="0" role="region" aria-label="Oportunidades em ${esc(s.name)}"><div class="columnCards">${leads.filter(l=>l.stage_id===s.id).map(leadCard).join("") || '<div class="emptyColumn">Nenhuma oportunidade</div>'}</div>${kanban.footer(column)}</div></section>`;}).join("")}</div><aside id="crmSalesRadar" class="salesRadar" ${radarCollapsed?"hidden":""}><div class="radarBlock"><h2>${icon("graduation-cap")} Jornada do curso</h2>${p.stages.map(s=>`<div class="courseStageTotal"><span>${esc(s.name)}</span><strong>${data.columns.find(c=>c.stage_id===s.id).total}</strong></div>`).join("")}</div><div class="radarBlock"><h2>${icon("calendar-clock")} Próximos contatos</h2><div class="radarNumber">${summary.overdue}</div><p>contatos vencidos</p><p>${c.refunds} reembolso(s) / chargeback(s) · Kiwify</p><button id="refreshCourse" ${leads.length?"":"disabled"}>${icon("refresh-cw")} Atualizar Kiwify</button><p id="courseRefreshStatus" role="status" hidden></p><a class="courseInstituteLink" href="/institutes.html?institute=${encodeURIComponent(p.institute_key)}&course=${encodeURIComponent(p.course_key)}" target="_blank" rel="noopener">${icon("arrow-up-right")} Relatórios do Instituto</a></div></aside></div>`;
  }
  function radarAction(){return radarCollapsed?"Mostrar Radar comercial":"Recolher Radar comercial";}
  function toggleRadar(){
    radarCollapsed=!radarCollapsed;
    try{sessionStorage.setItem("doc4docs.crm.radarCollapsed",radarCollapsed?"1":"0");}catch{}
    const panel=$("crmSalesRadar"),button=$("toggleSalesRadar");
    panel.hidden=radarCollapsed;panel.closest(".crmWorkspace").classList.toggle("radarCollapsed",radarCollapsed);
    button.setAttribute("aria-expanded",String(!radarCollapsed));button.setAttribute("aria-label",radarAction());button.title=radarAction();
    button.innerHTML=icon(radarCollapsed?"panel-right-open":"panel-right-close");icons();
  }
  function channels() {
    return `<article class="record" id="voiceSettings"><p class="connectionNote">Carregando telefonia…</p></article>${!state.data.gateway_configured?'<p class="connectionNote">Servidor WhatsApp ainda não configurado. Os números podem ser cadastrados, mas não serão conectados nem enviarão mensagens.</p>':""}${state.data.channels.map(c=>`<article class="record"><div class="recordMain"><span class="recordIcon">${icon("smartphone")}</span><div><h3>${esc(c.name)} ${pill(c.state)}</h3><p>+${esc(c.phone)} · ${esc(doctor()?.name)}</p><div class="recordMetrics"><span>Atendimento manual <strong>sem cota diária</strong></span><span>${state.data.campaigns_enabled?`Campanhas <strong>${c.daily_limit}</strong>/dia`:"Campanhas desativadas"}</span></div></div></div><div class="commands"><button data-connect="${c.id}" ${state.data.gateway_configured?"":"disabled"}>${icon("qr-code")} Conectar</button><button data-channel-state="${c.id}" class="icon-button" title="Conferir conexão" aria-label="Conferir conexão ${esc(c.name)}" ${state.data.gateway_configured?"":"disabled"}>${icon("refresh-cw")}</button></div></article>`).join("") || '<p class="emptyState">Nenhum número cadastrado para este doutor.</p>'}<p class="inlineWarning">Conexão não oficial. Intervalos e limites não garantem proteção contra bloqueio.</p>`;
  }
  function render() {
    if(!state.data) return;
    if(state.mode!=="board")bulk.reset();
    document.body.dataset.crmMode=state.mode;
    const sortChoices=courseScope()?[["updated","Mais recentes"],["course_paid","Maior valor pago · Kiwify"],["course_value","Maior valor da oportunidade"]]:[["updated","Mais recentes"],["spent","Maior total comprado"],["sales","Mais vendas fechadas"]];
    if(!sortChoices.some(([key])=>key===state.sort))state.sort="updated";
    $("crmSort").innerHTML=sortChoices.map(([key,label])=>option(key,label,key===state.sort)).join("");
    $("crmModeTitle").textContent=({dashboard:"Dashboard",board:"Funis",inbox:"Atendimento",campaigns:"Campanhas",channels:"Números",templates:"Modelos de mensagens",forms:"Formulários",bots:"Automações"})[state.mode];
    const url=new URL(location.href);url.searchParams.set("mode",state.mode);url.searchParams.set("doctor",state.doctor);if(state.pipeline)url.searchParams.set("pipeline",state.pipeline);history.replaceState(null,"",url);
    $("newLead").hidden=!["board","inbox"].includes(state.mode);
    $("addDoctor").hidden=!state.data.can_manage || !["board","channels"].includes(state.mode);
    kanban.destroy();
    if(listPicker){listSeed=listPicker.payload();listPicker.destroy();listPicker=null;}
    const oldBoard=$("crmView").querySelector(".crmBoard"), oldPipeline=oldBoard?.dataset.pipeline, scrollX=oldBoard?.scrollLeft || 0;
    $("pipelineField").hidden=state.mode!=="board";$("newLead").disabled=!pipeline();
    $("crmSearchTools").hidden=!clientMode();
    document.querySelector('.journey').hidden=!document.body.classList.contains("crmJourneyOpen");
    document.querySelector('.crmSort').hidden=state.mode==="board" && presentation==="list";
    document.querySelector('.crmSearchBar').classList.toggle('columnSorting',state.mode==="board" && presentation==="list");
    $("viewActions").innerHTML=state.mode==="board" ? `<div class="crmPresentation" role="group" aria-label="Visualização dos funis"><button id="showKanban" aria-pressed="${presentation==='board'}" title="Kanban">${icon("columns-3")} Kanban</button><button id="showList" aria-pressed="${presentation==='list'}" title="Lista">${icon("list")} Lista</button></div><button id="editPipeline" ${pipeline()?"":"disabled"}>${icon("sliders-horizontal")} Fases</button><button id="newPipeline">${icon("plus")} Funil</button>` : state.mode==="campaigns" ? `<button class="primary" id="newCampaign">${icon("plus")} Campanha</button>` : state.mode==="channels" ? `<button class="primary" id="newChannel">${icon("plus")} Número</button>` : "";
    if(state.mode==="board" && ["organico paranhos","regencode","trafego paranhos"].includes(pipeline()?.name.normalize("NFD").replace(/[\u0300-\u036f]/g,"").trim().toLowerCase()) && doctor()?.name==="Victor Paranhos de Andrade")$("viewActions").insertAdjacentHTML("beforeend",`<button id="importKommo">${icon("download")} Importar Kommo</button>`);
    if(!state.data.can_manage){
      ["editPipeline","newPipeline","newChannel","importKommo"].forEach(id=>{if($(id))$(id).hidden=true;});
    }
    if(state.mode==="inbox") inbox.mount($("crmView"));
    else {
      inbox.unmount();
    $("crmView").innerHTML=state.mode==="board"?presentation==="list"?'<div id="crmFunnelList"></div>':board():["campaigns","dashboard"].includes(state.mode)?"":state.mode==="forms"?'<div id="crmFormsHost"></div>':state.mode==="bots"?'<div id="crmBotsHost"></div>':state.mode==="templates"?'<div id="crmTemplatesLibrary" class="crmTemplatesLibrary"></div>':channels();
    if(!state.data.can_manage)$("crmView").querySelectorAll("[data-connect]").forEach(button=>button.hidden=true);
    if(state.mode==="board" && presentation==="list"){
      const filters={...state.filters,search:state.search,sort:state.sort},key=JSON.stringify([state.doctor,state.pipeline,filters]);
      if(key!==listScope){listSeed=null;listScope=key;}
      listPicker=audiencePicker($("crmFunnelList"),{doctor:state.doctor,pipelines:state.data.pipelines,initial:listSeed || {pipeline_id:state.pipeline,filters},manual:true,editable:false,onOpen:detail,onCampaign:payload=>campaignCenter.create(payload),onBulk:(action,selection)=>bulk.open(action,selection)});
    }
    const newBoard=$("crmView").querySelector(".crmBoard");if(newBoard && newBoard.dataset.pipeline===oldPipeline)newBoard.scrollLeft=scrollX;
    if(newBoard)bulk.mountBoard(newBoard);else bulk.reset();
    if(state.mode==="board" && pipeline())$("viewActions").insertAdjacentHTML("beforeend",`<button id="openLeadTrash" class="icon-button" title="Lixeira do funil" aria-label="Lixeira do funil">${icon("trash-2")}</button>`);
    if(newBoard && state.data.kanban?.pipeline_id===state.pipeline)kanban.mount(newBoard,{doctor:state.doctor,pipeline:state.pipeline,filters:{...state.filters,search:state.search,sort:state.sort},columns:state.data.kanban.columns});
    if($("refreshCourse"))$("refreshCourse").onclick=()=>{const l=selectedLeads()[0];if(l)courses.refresh(l,$("refreshCourse"),$("courseRefreshStatus"),()=>load());};
    if(state.mode==="channels"){voice.settings($("voiceSettings"),state.doctor);channelSettings.mount($("crmView"),state.data.channels,state.data.pipelines,state.doctor,state.data.can_manage);}
    }
    if(state.mode==="campaigns")campaignCenter.mount($("crmView"));else campaignCenter.unmount();
    if(state.mode==="dashboard")dashboard.mount($("crmView"),state.doctor);else dashboard.unmount();
    if(state.mode==="templates")templateLibrary.manage($("crmTemplatesLibrary"));else templateLibrary.destroy();
    if(state.mode==="forms")formsCenter.mount($("crmFormsHost"));else formsCenter.unmount();
    if(state.mode==="bots")botsCenter.mount($("crmBotsHost"));else botsCenter.unmount();
    document.querySelectorAll("[data-mode]").forEach(b=>b.setAttribute("aria-selected",String(b.dataset.mode===state.mode)));
    icons();
  }
  function modal(title,html) {$("modalTitle").textContent=title;$("modalBody").innerHTML=html;$("formError").hidden=true;if(!$("crmDialog").open) $("crmDialog").showModal();icons();}
  function error(message) {$("formError").textContent=message;$("formError").hidden=false;}
  function wireForm(callback, close=true) {
    const form=$("crmDialog").querySelector("form");
    form.onsubmit=async event=>{
      event.preventDefault();const button=form.querySelector('[type="submit"]');button.disabled=true;$("formError").hidden=true;
      try {await callback(new FormData(form),form);if(close) $("crmDialog").close();await load();}
      catch(e){error(e.message);}finally{button.disabled=false;}
    };
  }
  const actions = label => `<div class="formActions"><button type="submit" class="primary">${icon("check")} ${label}</button></div>`;
  function customerTierForm() {
    const rules=state.data.customer_tier_rules;
    modal("Classificação de clientes",`<form><div class="tierRulePreview">${["bronze","silver","gold"].map(tier=>`<span class="customerTier ${tier}">${icon(tier==="gold"?"crown":"medal")}${tierLabels[tier]}</span>`).join("")}</div><p class="connectionNote">Total comprado com o doutor selecionado, em todo o histórico sincronizado do Clínica Experts. Orçamentos, vendas canceladas e excluídas não entram. Não é uma confirmação de pagamento.</p><div class="formGrid"><label>Prata a partir de (R$)<input name="silver" type="number" step="0.01" min="0.01" max="100000000" value="${rules.silver_cents/100}" required></label><label>Ouro a partir de (R$)<input name="gold" type="number" step="0.01" min="0.02" max="100000000" value="${rules.gold_cents/100}" required></label></div><p class="connectionNote">Bronze: abaixo da faixa Prata. As mesmas faixas valem para todos os doutores da Vielle; o histórico continua separado por profissional. Cadastros sem vínculo seguro ou com histórico parcial ficam sem classificação.</p>${actions("Salvar classificação")}</form>`);
    wireForm(f=>api("customer-tiers",{silver_cents:Math.round(Number(f.get("silver"))*100),gold_cents:Math.round(Number(f.get("gold"))*100)}));
  }
  function doctorForm() {
    modal("Adicionar doutor(a)",`<form><label>Nome<input name="name" required maxlength="160" autocomplete="name"></label>${actions("Adicionar doutor(a)")}</form>`);
    wireForm(f=>api("doctor",{name:f.get("name")}));
  }
  function channelForm() {
    modal("Adicionar número",`<form><label>Identificação<input name="name" required maxlength="160" placeholder="Comercial / recepção"></label><div class="formGrid"><label>WhatsApp com código do país<input name="phone" inputmode="tel" required placeholder="5533999999999"></label><label>Limite diário de campanhas<input name="daily_limit" type="number" min="1" max="200" value="200" required></label></div><p class="connectionNote">${esc(doctor().name)}</p><p class="inlineWarning">O cadastro não conecta o WhatsApp e não inicia disparos. Nenhum limite garante evitar bloqueio.</p>${actions("Cadastrar número")}</form>`);
    wireForm(f=>api("channel",{doctor_id:state.doctor,name:f.get("name"),phone:f.get("phone"),daily_limit:Number(f.get("daily_limit"))}));
  }
  function pipelineForm(edit=false) {
    const current=edit?pipeline():null;
    const rows=current?current.stages.map(s=>({...s})):[{name:"Novo contato",kind:"open"},{name:"Em conversa",kind:"open"},{name:"Ganho",kind:"won"},{name:"Perdido",kind:"lost"}];
    modal(edit?"Fases do funil":"Criar funil",`<form><label>Nome do funil<input name="name" value="${esc(current?.name || "")}" maxlength="160" required></label><div id="stageRows"></div><button id="addStage" type="button">${icon("plus")} Fase</button>${actions("Salvar funil")}</form>`);
    const read=()=>document.querySelectorAll("#stageRows .stageRow").forEach((row,i)=>{rows[i].name=row.querySelector("input").value;rows[i].kind=row.querySelector("select").value;});
    const draw=()=>{$("stageRows").innerHTML=rows.map((r,i)=>`<div class="stageRow"><input aria-label="Nome da fase ${i+1}" value="${esc(r.name)}" maxlength="60" required><select aria-label="Tipo da fase ${i+1}">${[["open","Aberta"],["won","Ganha"],["lost","Perdida"]].map(([v,n])=>option(v,n,r.kind===v)).join("")}</select><button type="button" data-stage-up="${i}" ${i?"":"disabled"} title="Mover fase para cima" aria-label="Mover fase ${i+1} para cima">${icon("arrow-up")}</button><button type="button" data-stage-down="${i}" ${i===rows.length-1?"disabled":""} title="Mover fase para baixo" aria-label="Mover fase ${i+1} para baixo">${icon("arrow-down")}</button><button type="button" data-stage-remove="${i}" ${rows.length<=2?"disabled":""} title="Remover fase" aria-label="Remover fase ${i+1}">${icon("trash-2")}</button></div>`).join("");icons();};
    draw();$("addStage").onclick=()=>{read();if(rows.length<12){rows.push({name:"Nova fase",kind:"open"});draw();}};
    $("stageRows").onclick=event=>{const b=event.target.closest("button");if(!b)return;read();if(b.dataset.stageRemove!==undefined)rows.splice(Number(b.dataset.stageRemove),1);else{const i=Number(b.dataset.stageUp ?? b.dataset.stageDown),j=i+(b.dataset.stageUp!==undefined?-1:1);[rows[i],rows[j]]=[rows[j],rows[i]];}draw();};
    wireForm(f=>{read();return api("pipeline",{id:current?.id,revision:current?.revision,doctor_id:state.doctor,name:f.get("name"),stages:rows});});
  }
  function leadForm() {
    const p=pipeline();if(!p)return;
    if(p.course_key){
      modal("Nova oportunidade do curso",`<form><p class="connectionNote">${esc(p.name)} · ${esc(doctor().name)}</p><div class="formGrid"><label>Nome do contato<input name="name" required maxlength="160"></label><label>WhatsApp com código do país<input name="phone" required inputmode="tel" placeholder="5511999999999"></label></div><label>E-mail<input name="email" type="email" maxlength="200"></label><label>Oportunidade<input name="title" required maxlength="160" value="${esc(p.name)}"></label><div class="formGrid"><label>Fase<select name="stage_id">${p.stages.map(s=>option(s.id,s.name)).join("")}</select></label><label>Valor da oportunidade (R$)<input name="value" type="number" step="0.01" min="0" max="1000000" value="0" required></label></div><label>Próximo contato<input name="next_action" type="date"></label>${actions("Criar oportunidade")}</form>`);
      wireForm(f=>api("lead",{doctor_id:state.doctor,pipeline_id:p.id,stage_id:f.get("stage_id"),name:f.get("name"),phone:f.get("phone"),email:f.get("email"),title:f.get("title"),value_cents:Math.round(Number(f.get("value"))*100),next_action:f.get("next_action")}));return;
    }
    const doc=state.doctor;
    modal("Nova oportunidade",`<form><p class="connectionNote">${esc(doctor().name)} · ${esc(p.name)}</p><fieldset class="patientSource"><legend class="visuallyHidden">Origem do cliente</legend><label><input type="radio" name="patient_source" value="new" checked>${icon("user-round-plus")} Novo contato</label><label><input type="radio" name="patient_source" value="experts">${icon("database")} Clínica Experts</label></fieldset><section id="patientPicker" hidden><div class="patientSearchBar"><label>Buscar paciente<input id="patientSearch" type="search" placeholder="Nome ou telefone" autocomplete="off"></label><button type="button" id="refreshPatientDirectory" class="icon-button" title="Atualizar pacientes do Clínica Experts" aria-label="Atualizar pacientes do Clínica Experts">${icon("refresh-cw")}</button></div><p id="patientSearchStatus" role="status" class="connectionNote"></p><div id="patientResults" class="patientResults" aria-label="Pacientes do Clínica Experts"></div><div class="patientPagination"><button type="button" id="patientPrev" class="icon-button" title="Página anterior" aria-label="Página anterior de pacientes">${icon("chevron-left")}</button><span id="patientPage"></span><button type="button" id="patientNext" class="icon-button" title="Próxima página" aria-label="Próxima página de pacientes">${icon("chevron-right")}</button></div><label id="patientPhoneChoice" hidden>Telefone cadastrado<select id="patientPhone"></select></label></section><div class="formGrid"><label>Nome do cliente<input name="name" required maxlength="160" autocomplete="name"></label><label>Telefone com código do país<input name="phone" inputmode="tel" required placeholder="5533999999999"></label></div><label>Oportunidade<input name="title" required maxlength="160" placeholder="Interesse / próximo objetivo"></label><div class="formGrid"><label>Fase<select name="stage_id">${p.stages.map(s=>option(s.id,s.name)).join("")}</select></label><label>Valor estimado (R$)<input name="value" type="number" min="0" max="1000000" step="0.01" value="0" required></label></div><label>Próximo contato<input name="next_action" type="date"></label><details class="consentEvidence"><summary>Registro de consentimento (opcional)</summary><label class="check"><input name="consent" type="checkbox">Cliente informou consentimento para mensagens</label><label>Origem e data do registro<input name="consent_source" maxlength="300" placeholder="Ex.: formulário ou conversa"></label></details>${actions("Criar oportunidade")}</form>`);
    const form=$("crmDialog").querySelector("form"),name=form.elements.name,phone=form.elements.phone;
    if(state.data.can_search_patients===false)form.querySelector('[name="patient_source"][value="experts"]').closest("label").hidden=true;
    const consent=form.elements.consent,source=form.elements.consent_source;
    let mode="new", selected=null, rows=[], page=1, pages=1, request=0, timer, manual={name:"",phone:""};
    const active=()=>form.isConnected && $("crmDialog").open;
    consent.onchange=()=>{source.required=consent.checked;};
    function clearSelection(){selected=null;name.value="";phone.value="";$("patientPhoneChoice").hidden=true;}
    async function searchPatients(){
      const seq=++request;$("patientResults").setAttribute("aria-busy","true");
      $("patientSearchStatus").textContent="Buscando pacientes…";
      $("patientPrev").disabled=true;$("patientNext").disabled=true;
      try{
        const data=await api("patients",undefined,{doctor:doc,search:$("patientSearch").value,page});
        if(!active() || seq!==request || mode!=="experts")return;
        rows=data.items;page=data.page;pages=data.pages;
        $("patientSearchStatus").textContent=!data.available?"Base indisponível. Atualize os pacientes para consultar.":`${data.total} paciente(s)${data.synced_at?` · Base de ${new Date(data.synced_at*1000).toLocaleString("pt-BR",{day:"2-digit",month:"2-digit",hour:"2-digit",minute:"2-digit"})}`:""}`;
        $("patientResults").innerHTML=rows.map((r,i)=>`<button type="button" class="patientResult" data-patient-index="${i}" aria-pressed="${selected?.uuid===r.uuid}" ${!r.name || !r.phones.length?"disabled":""}><span class="patientResultIcon">${icon("user-round")}</span><span><strong>${esc(r.name || "Sem nome")}</strong><small>${esc(r.phones.length?r.phones.map(n=>`+${n}`).join(" · "):r.shared_phone?"Telefone compartilhado · conferir cadastro":"Sem telefone cadastrado")}</small></span>${icon(selected?.uuid===r.uuid?"circle-check":"plus")}</button>`).join("") || '<p class="connectionNote">Nenhum paciente encontrado.</p>';
        $("patientPage").textContent=`${page} de ${pages}`;
        $("patientPrev").disabled=page<=1;$("patientNext").disabled=page>=pages;icons();
      }catch(e){if(active() && seq===request)$("patientSearchStatus").textContent=e.message;}
      finally{if(active() && seq===request)$("patientResults").setAttribute("aria-busy","false");}
    }
    form.querySelectorAll('[name="patient_source"]').forEach(radio=>radio.onchange=()=>{
      if(mode==="new")manual={name:name.value,phone:phone.value};
      mode=radio.value;request++;clearSelection();
      $("patientPicker").hidden=mode!=="experts";name.readOnly=phone.readOnly=mode==="experts";
      if(mode==="new"){name.value=manual.name;phone.value=manual.phone;}
      else{page=1;searchPatients();}
    });
    $("patientSearch").oninput=()=>{clearTimeout(timer);page=1;request++;timer=setTimeout(()=>{if(active() && mode==="experts")searchPatients();},250);};
    $("patientPrev").onclick=()=>{page--;searchPatients();};$("patientNext").onclick=()=>{page++;searchPatients();};
    $("patientResults").onclick=event=>{
      const button=event.target.closest("[data-patient-index]");if(!button || button.disabled)return;
      selected=rows[Number(button.dataset.patientIndex)];name.value=selected.name;phone.value=selected.phones[0];
      $("patientPhone").innerHTML=selected.phones.map(n=>option(n,`+${n}`)).join("");
      $("patientPhoneChoice").hidden=selected.phones.length<2;
      form.querySelectorAll("[data-patient-index]").forEach(b=>b.setAttribute("aria-pressed",String(b===button)));
    };
    $("patientPhone").onchange=e=>{phone.value=e.target.value;};
    $("refreshPatientDirectory").onclick=async()=>{
      const button=$("refreshPatientDirectory");button.disabled=true;request++;
      $("patientSearchStatus").textContent="Atualizando cadastros no Clínica Experts…";
      try{
        await patientRefreshJob("patient-directory-refresh",{doctor_id:doc},{doctor:doc});
        if(active() && mode==="experts"){clearSelection();page=1;await searchPatients();}
      }catch(e){if(active())$("patientSearchStatus").textContent=e.message;}
      finally{if(button.isConnected)button.disabled=false;}
    };
    wireForm(f=>{
      if(mode==="experts" && !selected)throw new Error("Selecione um paciente do Clínica Experts.");
      return api("lead",{doctor_id:doc,pipeline_id:p.id,stage_id:f.get("stage_id"),name:f.get("name"),phone:f.get("phone"),experts_uuid:mode==="experts"?selected.uuid:"",title:f.get("title"),value_cents:Math.round(Number(f.get("value"))*100),next_action:f.get("next_action"),consent:f.has("consent"),consent_source:f.get("consent_source")},{doctor:doc});
    });
  }
  async function detail(id) {
    const owner=state.doctor;
    let lead=state.data.leads.find(l=>l.id===id);
    if(!lead){try{lead=(await api("lead",undefined,{id,doctor:owner})).lead;}catch(e){notice(e.message);return;}}
    if(state.doctor!==owner)return;
    state.mode="inbox";state.search="";$("search").value="";render();inbox.openLead(lead);
  }
  let feedbackTimer, transitionTimer;
  function stageFeedback(lead, stage, xp=0) {
    const stages=state.data.pipelines.find(p=>p.id===lead.pipeline_id)?.stages || [];
    const index=stages.findIndex(s=>s.id===stage.id), kind=stage.kind;
    const symbol=kind==="won"?"trophy":kind==="lost"?"archive":["message-circle","messages-square","calendar-check","file-check-2","handshake"][Math.max(index,0)%5];
    const box=$("stageFeedback");clearTimeout(feedbackTimer);box.hidden=true;
    box.className=`stageFeedback ${kind}`;
    box.innerHTML=`<span class="stageEmblem">${icon(symbol)}</span><div><strong>${kind==="won"?"Mais uma conquista!":kind==="lost"?"Jornada atualizada":"Próximo capítulo"}</strong><span>${esc(lead.name)} · ${esc(stage.name)}</span><div class="stageTrack">${stages.map((s,i)=>`<b class="${i<=index?"reached":""}"></b>`).join("")}</div></div>${xp>0?`<b class="earnedXp">+${xp} XP</b>`:""}${kind==="won"?`<div class="winRibbons" aria-hidden="true">${Array.from({length:14},(_,i)=>`<i style="--n:${i}"></i>`).join("")}</div>`:""}`;
    box.hidden=false;icons();const col=document.querySelector(`[data-stage="${CSS.escape(stage.id)}"]`);col?.classList.add("stagePulse");
    setTimeout(()=>col?.classList.remove("stagePulse"),1000);feedbackTimer=setTimeout(()=>{box.hidden=true;},kind==="won"?4500:2800);
    const scene=$("stageTransition");clearTimeout(transitionTimer);scene.hidden=true;
    if(!matchMedia("(prefers-reduced-motion:reduce)").matches){
      const previous=stages.find(s=>s.id===lead.stage_id), duration=kind==="won"?2100:1500;
      scene.className=`stageTransition ${kind}`;scene.style.setProperty("--scene-duration",`${duration}ms`);
      scene.innerHTML=`<div class="stageScene"><div class="sceneEmblem">${icon(symbol)}</div><p class="sceneEyebrow">${kind==="won"?"CONQUISTA DA EQUIPE":"JORNADA DO CLIENTE"}</p><h2>${kind==="won"?"Mais uma história de sucesso!":kind==="lost"?"Ciclo registrado":"Uma nova etapa começa"}</h2><p class="sceneClient">${esc(lead.name)}</p><div class="stageFlight"><div class="flightFrom"><small>De</small><strong>${esc(previous?.name || "Fase anterior")}</strong></div><div class="flightBridge"><span class="bridgeLine"></span><span class="flightToken">${icon(kind==="won"?"trophy":"user-round")}</span>${icon("arrow-right")}</div><div class="flightTo"><small>Para</small><strong>${esc(stage.name)}</strong></div></div>${xp>0?`<p class="sceneXp">${icon("sparkles")} +${xp} XP para a equipe</p>`:""}</div>${kind==="won"?`<div class="winRibbons sceneRibbons">${Array.from({length:22},(_,i)=>`<i style="--n:${i}"></i>`).join("")}</div>`:""}`;
      scene.hidden=false;icons();transitionTimer=setTimeout(()=>{scene.hidden=true;},duration);
    }
  }
  async function moveLead(lead, stageId, changes={}) {
    const before=state.data.game.xp, doc=state.doctor;
    await api("lead",{...lead,...changes,doctor_id:doc,stage_id:stageId});
    await load();
    if(doc!==state.doctor)return;
    if(stageId!==lead.stage_id){const stage=state.data.pipelines.find(p=>p.id===lead.pipeline_id)?.stages.find(s=>s.id===stageId);if(stage)stageFeedback(lead,stage,Math.max(0,state.data.game.xp-before));}
  }
  function commercialView(host, lead) {
    let kind="sales", scope="doctor", page=1, request=0;
    const owner=state.doctor;
    const currency=n=>n===null || n===undefined?"Valor não informado":(n/100).toLocaleString("pt-BR",{style:"currency",currency:"BRL",minimumFractionDigits:2});
    const fullDate=d=>d?new Date(`${d}T12:00:00`).toLocaleDateString("pt-BR"):"Sem data";
    async function draw() {
      const seq=++request;
      host.setAttribute("aria-busy","true");
      host.innerHTML='<p class="connectionNote">Consultando o histórico no Clínica Experts…</p>';
      try {
        const data=await api("commercial",undefined,{doctor:owner,id:lead.id,scope,kind,page});
        if(!host.isConnected || seq!==request)return;
        if(data.state!=="ready"){
          host.innerHTML=`<p class="commercialState">${esc({unlinked:"Paciente ainda não vinculado ao Clínica Experts.",identity_pending:"O vínculo deste paciente precisa de conferência. Histórico oculto para evitar associações incorretas.",unavailable:"Histórico indisponível na base sincronizada do Clínica Experts."}[data.state] || "Histórico indisponível.")}</p>`;
          return;
        }
        page=data.page;const s=data.summary,w=data.warnings;
        host.innerHTML=`<div class="commercialToolbar"><span>${icon("database")} Clínica Experts</span><label>Histórico<select id="commercialScope"><option value="doctor" ${scope==="doctor"?"selected":""}>Doutor(a) selecionado(a)</option><option value="clinic" ${scope==="clinic"?"selected":""}>Toda a clínica</option></select></label></div><div class="commercialTotals"><div><span>Vendas fechadas · ${s.sales_count}</span><strong>${currency(s.sales_cents)}</strong></div><div><span>Orçamentos pendentes · ${s.pending_quotes_count}</span><strong>${currency(s.pending_quotes_cents)}</strong></div></div><p class="commercialSource">${scope==="doctor"?esc(data.doctor_name):"Todos os profissionais da Vielle"} · Valores vendidos, não recebimentos${data.synced_at?` · Base de ${esc(new Date(data.synced_at*1000).toLocaleString("pt-BR",{timeZone:"America/Sao_Paulo",day:"2-digit",month:"2-digit",year:"numeric",hour:"2-digit",minute:"2-digit"}))}`:""}</p>${w.professional?`<p class="commercialWarning">${w.professional} registro(s) sem vínculo seguro com este doutor não incluído(s).</p>`:""}${w.identity+w.malformed?`<p class="commercialWarning">${w.identity+w.malformed} registro(s) inconsistente(s) não incluído(s).</p>`:""}${w.amount?`<p class="commercialWarning">Totais parciais: ${w.amount} registro(s) sem valor confirmado.</p>`:""}<div class="commercialItems">${data.items.map(item=>`<article class="commercialItem"><div class="commercialItemHead"><span class="commercialBadge ${esc(item.status)}">${esc({closed:"Venda fechada",active:"Aberto",expired:"Vencido",lost:"Perdido"}[item.status] || item.status)}</span><time>${fullDate(item.date)}</time><strong>${currency(item.amount_cents)}</strong></div><h3>${esc(item.title)}</h3><p>${esc(item.professional)} · #${esc(item.uuid)}</p>${item.procedures.length?`<details><summary>Procedimentos e produtos (${item.procedures.length})</summary><ul>${item.procedures.map(name=>`<li>${esc(name)}</li>`).join("")}</ul></details>`:""}</article>`).join("") || `<p class="commercialState">${kind==="sales"?"Nenhuma venda fechada":"Nenhum orçamento pendente"} na base sincronizada para este filtro.</p>`}</div><div class="commercialPagination"><span>${data.total} registros · Página ${data.page} de ${data.pages}</span><div class="commands"><button id="commercialPrev" class="icon-button" aria-label="Página anterior do histórico" title="Página anterior" ${data.page<=1?"disabled":""}>${icon("chevron-left")}</button><button id="commercialNext" class="icon-button" aria-label="Próxima página do histórico" title="Próxima página" ${data.page>=data.pages?"disabled":""}>${icon("chevron-right")}</button></div></div>`;
        if(state.data.can_view_clinic_history===false)host.querySelector('#commercialScope option[value="clinic"]').remove();
        host.querySelector("#commercialScope").onchange=e=>{scope=e.target.value;page=1;draw();};
        host.querySelector("#commercialPrev").onclick=()=>{page--;draw();};
        host.querySelector("#commercialNext").onclick=()=>{page++;draw();};icons();
      }catch(e){if(host.isConnected && seq===request)host.innerHTML=`<p class="commercialState" role="alert">${esc(e.message)}</p>`;}
      finally{if(seq===request)host.setAttribute("aria-busy","false");}
    }
    return {show(value){kind=value;page=1;draw();},cancel(){request++;}};
  }
  function appointmentsView(host, lead) {
    let scope="doctor", period="all", page=1, request=0;const owner=state.doctor;
    async function draw() {
      const seq=++request;host.setAttribute("aria-busy","true");host.innerHTML='<p class="connectionNote">Consultando os agendamentos…</p>';
      try {
        const data=await api("appointments",undefined,{doctor:owner,id:lead.id,scope,period,page});
        if(!host.isConnected || seq!==request)return;
        if(data.state!=="ready"){
          host.innerHTML=`<p class="commercialState">${esc({unlinked:"Paciente ainda não vinculado ao Clínica Experts.",identity_pending:"O vínculo do paciente precisa de conferência. Agenda não exibida para evitar associações incorretas.",unavailable:"Agenda indisponível na base sincronizada do Clínica Experts."}[data.state] || "Agenda indisponível.")}</p>`;return;
        }
        page=data.page;
        host.innerHTML=`<div class="commercialToolbar"><span>${icon("calendar-days")} Clínica Experts</span><label>Histórico<select id="appointmentScope"><option value="doctor" ${scope==="doctor"?"selected":""}>Doutor(a) selecionado(a)</option><option value="clinic" ${scope==="clinic"?"selected":""}>Toda a clínica</option></select></label><label>Agendamentos<select id="appointmentPeriod"><option value="all" ${period==="all"?"selected":""}>Todos</option><option value="upcoming" ${period==="upcoming"?"selected":""}>Próximos</option><option value="past" ${period==="past"?"selected":""}>Anteriores</option></select></label></div><div class="appointmentHighlight">${appointmentBadge(data)}</div><p class="commercialSource">${scope==="doctor"?esc(data.doctor_name):"Todos os profissionais da Vielle"}${data.synced_at?` · Base de ${esc(new Date(data.synced_at*1000).toLocaleString("pt-BR",{timeZone:"America/Sao_Paulo",day:"2-digit",month:"2-digit",year:"numeric",hour:"2-digit",minute:"2-digit"}))}`:""}</p>${Object.values(data.warnings || {}).some(Boolean)?`<p class="commercialWarning">${Object.values(data.warnings).reduce((a,b)=>a+b,0)} agendamento(s) com data ou vínculo inconsistente não incluído(s).</p>`:""}<div class="commercialItems appointmentItems">${data.items.map(item=>`<article class="commercialItem appointmentItem"><div class="commercialItemHead"><span class="commercialBadge ${item.phase==="cancelled"?"lost":item.phase==="past"?"past":"active"}">${esc(item.status_label)}</span><strong>${esc(appointmentTime(item))}</strong></div><h3>${esc(item.procedure)}</h3><p>${esc(item.professional)}</p>${item.phase==="ongoing"?'<span class="appointmentOngoing">Em andamento agora</span>':""}</article>`).join("") || '<p class="commercialState">Nenhum agendamento válido na base sincronizada para este filtro.</p>'}</div><div class="commercialPagination"><span>${data.total} registros · Página ${data.page} de ${data.pages}</span><div class="commands"><button id="appointmentPrev" class="icon-button" aria-label="Página anterior dos agendamentos" title="Página anterior" ${data.page<=1?"disabled":""}>${icon("chevron-left")}</button><button id="appointmentNext" class="icon-button" aria-label="Próxima página dos agendamentos" title="Próxima página" ${data.page>=data.pages?"disabled":""}>${icon("chevron-right")}</button></div></div>`;
        if(state.data.can_view_clinic_history===false)host.querySelector('#appointmentScope option[value="clinic"]').remove();
        host.querySelector("#appointmentScope").onchange=e=>{scope=e.target.value;page=1;draw();};host.querySelector("#appointmentPeriod").onchange=e=>{period=e.target.value;page=1;draw();};
        host.querySelector("#appointmentPrev").onclick=()=>{page--;draw();};host.querySelector("#appointmentNext").onclick=()=>{page++;draw();};icons();
      }catch(e){if(host.isConnected && seq===request)host.innerHTML=`<p class="commercialState" role="alert">${esc(e.message)}</p>`;}
      finally{if(seq===request)host.setAttribute("aria-busy","false");}
    }
    return {show(){page=1;draw();},cancel(){request++;}};
  }
  async function profile(id, initialTab="record") {
    try {
      const res=await fetch(`/api/crm/lead?${new URLSearchParams({clinic:"vielle",doctor:state.doctor,id})}`), data=await res.json();
      if(!res.ok)throw new Error(data.error);
      const l=data.lead,p=state.data.pipelines.find(p=>p.id===l.pipeline_id);
      const isCourse=Boolean(l.course_key);
      modal(l.name,`<div class="leadDetail"><div><p class="connectionNote">+${esc(l.phone)} · ${esc(p.name)}</p><form id="leadEdit"><label>Oportunidade<input name="title" value="${esc(l.title)}" maxlength="160" required></label><label>Fase<select name="stage_id">${p.stages.map(s=>option(s.id,s.name,l.stage_id===s.id)).join("")}</select></label><div class="formGrid"><label>Valor estimado (R$)<input name="value" type="number" step="0.01" min="0" max="1000000" value="${l.value_cents/100}" required></label><label>Próximo contato<input name="next_action" type="date" value="${esc(l.next_action)}"></label></div>${actions("Salvar oportunidade")}</form><h3>Mensagens</h3><p class="connectionNote">${l.opted_out?"Descadastrado · envios bloqueados":"Disponível para envio · padrão da clínica"}</p>${l.consent_at?`<p class="connectionNote">Consentimento informado: ${esc(l.consent_source)}</p>`:""}<div class="commands">${!l.opted_out?`<button id="recordConsent">${icon("file-check")} Registrar consentimento</button><button id="optout">Descadastrar</button>`:`<button id="reactivateContact">${icon("user-round-check")} Reativar contato</button>`}</div><h3>Registrar atividade</h3><form id="activityForm"><label>Tipo<select name="kind"><option value="followup">Follow-up realizado</option><option value="note">Nota interna</option></select></label><label>Resumo<textarea name="body" required maxlength="2000"></textarea></label>${actions("Registrar")}</form></div><div><h3>Histórico</h3>${data.events.map(e=>`<div class="eventRow"><small>${date(e.created_at)} · ${esc(e.actor)} · ${esc(e.kind==="reactivate"?"Contato reativado":e.kind)}</small>${esc(e.body)}</div>`).join("")}<h3>Conversas</h3><div class="conversation">${data.messages.map(m=>`<div class="eventRow ${esc(m.direction)}"><small>${date(m.created_at)} · ${esc(m.channel_name)} · ${esc(statusLabels[m.status] || m.status)}</small>${esc(m.body)}</div>`).join("") || '<p class="connectionNote">Nenhuma mensagem neste doutor.</p>'}</div></div></div>`);
      const identity=document.createElement("p");identity.className="connectionNote";identity.textContent=patientLabel(l.experts_status)+(l.experts_uuid?` · ID ${l.experts_uuid}`:"");$("leadEdit").before(identity);
      const runBot=document.createElement("button");runBot.type="button";runBot.innerHTML=`${icon("workflow")} Iniciar robô`;$("leadEdit").after(runBot);runBot.onclick=()=>botsCenter.startForLead(l).catch(e=>error(e.message));
      const removeLead=document.createElement("button");removeLead.type="button";removeLead.className="icon-button";removeLead.title="Excluir lead";removeLead.setAttribute("aria-label","Excluir lead");removeLead.innerHTML=icon("trash-2");runBot.after(removeLead);removeLead.onclick=()=>bulk.open("delete",{lead_ids:[l.id]},{doctor:l.doctor_id,pipeline:l.pipeline_id});
      const origin=document.createElement("div");origin.innerHTML=window.CrmAttribution.lead(data.attribution,esc);$("leadEdit").before(origin);
      if(isCourse)identity.textContent=courses.label(l);
      const refresh=document.createElement("div");refresh.className="patientRefreshControls";
      refresh.innerHTML=`<button type="button" title="Buscar novamente no Clínica Experts" ${patientRefreshes.has(l.id)?"disabled":""}>${icon("refresh-cw")} Atualizar contato</button><p role="status" hidden></p>`;identity.after(refresh);
      refresh.querySelector("button").onclick=async()=>{
        const button=refresh.querySelector("button"),status=refresh.querySelector("p");
        try {
          const updated=await refreshPatient(l,button,status);
          if(!updated || !refresh.isConnected || state.doctor!==l.doctor_id)return;
          Object.assign(l,updated);$("modalTitle").textContent=l.name;
          identity.textContent=patientLabel(l.experts_status)+(l.experts_uuid?` · ID ${l.experts_uuid}`:"");
          value.innerHTML=customerValuePanel(l.customer_value);appointment.innerHTML=appointmentBadge(l.appointment);
          status.textContent=patientRefreshMessage(l);icons();
        } catch(e){if(status.isConnected){status.hidden=false;status.textContent=e.message;}}
      };
      if(isCourse){refresh.querySelector("button").innerHTML=`${icon("refresh-cw")} Atualizar Kiwify`;refresh.querySelector("button").title="Atualizar pedidos do curso";refresh.querySelector("button").onclick=()=>courses.refresh(l,refresh.querySelector("button"),refresh.querySelector("p"),updated=>{Object.assign(l,updated);value.innerHTML=courses.panel(l);identity.textContent=courses.label(l);commerce.innerHTML=courses.orders(l);},()=>$("crmDialog").open && refresh.isConnected && state.doctor===l.doctor_id);}
      const value=document.createElement("div");value.className="profileCustomerValue";value.innerHTML=isCourse?courses.panel(l):customerValuePanel(l.customer_value);$("leadEdit").before(value);
      const appointment=document.createElement("div");appointment.className="profileAppointment";appointment.innerHTML=isCourse?"":appointmentBadge(l.appointment);$("leadEdit").before(appointment);
      const locationForm=document.createElement("form");locationForm.className="serviceLocationForm";
      locationForm.innerHTML=`<label>Local de Atendimento<select name="service_location">${option("","Não definido · pendente",!l.service_location)}${Object.entries(locationLabels).map(([key,label])=>option(key,label,l.service_location===key)).join("")}</select></label><p class="connectionNote">${!l.service_location?"Pendência: Local de Atendimento não definido.":l.service_location_source==="ddd"?`Pré-definido pelo DDD ${esc(l.phone.slice(2,4))}.`:"Definido manualmente."}</p><div class="commands"><button type="submit">${icon("check")} Salvar local</button><button type="submit" id="useDddLocation" class="icon-button" title="Reaplicar regra do DDD" aria-label="Reaplicar regra do DDD">${icon("rotate-ccw")}</button></div>`;
      $("leadEdit").after(locationForm);
      locationForm.hidden=isCourse;
      if(isCourse){const emailLabel=document.createElement("label");emailLabel.textContent="E-mail do aluno / interessado";const emailInput=document.createElement("input");emailInput.name="email";emailInput.type="email";emailInput.value=l.email || "";emailInput.maxLength=200;emailLabel.append(emailInput);$("leadEdit").prepend(emailLabel);}
      locationForm.onsubmit=async e=>{
        e.preventDefault();const buttons=locationForm.querySelectorAll("button");buttons.forEach(b=>b.disabled=true);$("formError").hidden=true;
        try {await api("service-location",{id:l.id,doctor_id:l.doctor_id,service_location_revision:l.service_location_revision,service_location:new FormData(locationForm).get("service_location"),use_ddd:e.submitter?.id==="useDddLocation"});await load();await profile(l.id);}
        catch(err){error(err.message);buttons.forEach(b=>b.disabled=false);}
      };
      const record=$("modalBody").querySelector(".leadDetail");record.id="profileRecord";
      const tabs=document.createElement("div");tabs.className="profileTabs";tabs.setAttribute("role","tablist");tabs.setAttribute("aria-label","Ficha do cliente");
      tabs.innerHTML=(isCourse?[["record","notebook-pen","Ficha do curso"],["orders","shopping-bag","Pedidos Kiwify"],["calls","phone-call","Ligações"]]:[["record","notebook-pen","Ficha"],["sales","shopping-bag","Vendas fechadas"],["quotes","file-clock","Orçamentos pendentes"],["appointments","calendar-days","Agendamentos"],["calls","phone-call","Ligações"]]).map(([key,symbol,label])=>`<button role="tab" data-profile-tab="${key}">${icon(symbol)} ${label}</button>`).join("");
      const commerce=document.createElement("section");commerce.id="profileCommerce";commerce.hidden=true;
      const agenda=document.createElement("section");agenda.id="profileAppointments";agenda.hidden=true;
      const calls=document.createElement("section");calls.id="profileCalls";calls.hidden=true;
      $("modalBody").prepend(tabs);$("modalBody").append(commerce,agenda,calls);
      const history=commercialView(commerce,l);
      const bookings=appointmentsView(agenda,l);
      const callHistory=voice.history(calls,l);
      const showTab=tab=>{record.hidden=tab!=="record";commerce.hidden=!(isCourse?tab==="orders":["sales","quotes"].includes(tab));agenda.hidden=isCourse || tab!=="appointments";calls.hidden=tab!=="calls";tabs.querySelectorAll("button").forEach(b=>b.setAttribute("aria-selected",String(b.dataset.profileTab===tab)));if(!commerce.hidden){if(isCourse)commerce.innerHTML=courses.orders(l);else history.show(tab);}else history.cancel();if(!agenda.hidden)bookings.show();else bookings.cancel();if(!calls.hidden)callHistory.show();else callHistory.cancel();};
      tabs.onclick=e=>{const b=e.target.closest("[data-profile-tab]");if(b)showTab(b.dataset.profileTab);};showTab(initialTab);icons();
      wireForm(async f=>{await moveLead(l,f.get("stage_id"),{title:f.get("title"),value_cents:Math.round(Number(f.get("value"))*100),next_action:f.get("next_action"),...(isCourse?{email:f.get("email")}:{})});});
      $("activityForm").onsubmit=async e=>{e.preventDefault();const b=e.currentTarget.querySelector('[type="submit"]');b.disabled=true;const f=new FormData(e.currentTarget);try{await api("activity",{id:l.id,doctor_id:state.doctor,kind:f.get("kind"),body:f.get("body")});await load();await profile(l.id);}catch(err){error(err.message);b.disabled=false;}};
      if($("optout")) $("optout").onclick=()=>activityModal(l,"optout");
      if($("recordConsent")) $("recordConsent").onclick=()=>activityModal(l,"consent");
      if($("reactivateContact")) $("reactivateContact").onclick=()=>reactivationModal(l);
    } catch(e){notice(e.message);}
  }
  function activityModal(l,kind) {
    modal(kind==="consent"?"Registrar autorização":"Descadastrar contato",`<form><p class="connectionNote">${esc(l.name)} · +${esc(l.phone)}${kind==="optout"?". O descadastro impede campanhas de todos os doutores da clínica.":""}</p><label>${kind==="consent"?"Origem e data da autorização":"Motivo"}<textarea name="body" maxlength="${kind==="consent"?300:2000}" required></textarea></label>${actions(kind==="consent"?"Registrar autorização":"Confirmar descadastro")}</form>`);
    wireForm(f=>api("activity",{id:l.id,doctor_id:state.doctor,kind,body:f.get("body")}));
  }
  function reactivationModal(l) {
    modal("Reativar contato",`<form><p class="connectionNote">${esc(l.name)} · +${esc(l.phone)}</p><p class="connectionNote">A reativação vale para todos os doutores da clínica. Mensagens antigas não serão reenviadas e campanhas não serão reiniciadas.</p><label>Motivo da reativação<textarea name="body" maxlength="2000" required></textarea></label><label class="check"><input name="confirm_reactivation" type="checkbox" required>Confirmei com o cliente que ele deseja voltar a receber contato.</label><div class="formActions"><button type="button" id="cancelReactivation">Cancelar</button><button type="submit" class="primary">${icon("user-round-check")} Reativar contato</button></div></form>`);
    $("cancelReactivation").onclick=()=>profile(l.id);
    wireForm(async f=>{
      await api("activity",{id:l.id,doctor_id:l.doctor_id,kind:"reactivate",revision:l.revision,body:f.get("body"),confirm_reactivation:f.has("confirm_reactivation")});
      await profile(l.id);
    },false);
  }
  function campaignForm() {return campaignCenter.create();}
  async function connect(id) {
    modal("Conectar WhatsApp",'<p class="connectionNote">Preparando conexão...</p>');
    try {
      const data=await api("connect",{id,doctor_id:state.doctor});
      const channel=state.data.channels.find(ch=>ch.id===id);
      modal("Conectar WhatsApp",`<p>${esc(channel.name)} · +${esc(channel.phone)}</p>${data.qr?`<img class="qrImage" alt="QR Code para vincular o WhatsApp deste doutor" src="${esc(data.qr)}">`:'<p class="connectionNote">QR indisponível. Confira se o número já está conectado.</p>'}<div class="formActions"><button id="verifyConnection">${icon("refresh-cw")} Conferir conexão</button></div>`);
      $("verifyConnection").onclick=async()=>{try{const r=await api("channel-state",{id,doctor_id:state.doctor});if(r.state==="open"){$("crmDialog").close();await load();}else error("O número ainda não está conectado.");}catch(e){error(e.message);}};
    } catch(e){error(e.message);}
  }
  const voice=window.createCrmVoice({api,esc,icon,icons});
  const channelSettings=window.createCrmChannelSettings({api,esc,icon,icons,modal,error,onDone:load,onTransferred:async result=>{
    inbox.unmount();state.doctor=result.doctor_id;state.pipeline=result.inbound_pipeline_id;await load();
    notice("Conexão transferida para o novo profissional, sem alterar o pareamento. Os leads existentes foram preservados nos funis de origem.");
  }});
  const kanban=window.createCrmKanban({api,esc,icon,icons,leadCard,onRefresh:load,onLeads:rows=>{
    const known=new Set(state.data.leads.map(l=>l.id));state.data.leads.push(...rows.filter(l=>!known.has(l.id)));
  }});
  const bulk=window.createCrmBulk({api,esc,icon,icons,modal,error,notice,getScope:()=>({doctor:state.doctor,pipeline:state.pipeline,pipelines:state.data?.pipelines || [],filters:{...state.filters,search:state.search,sort:state.sort}}),onDone:async()=>{listPicker?.destroy();listPicker=null;listSeed=null;listScope="";await load();}});
  const audiencePicker=window.createCrmAudience({api,esc,icon,icons});
  const campaignCenter=window.createCrmCampaigns({state,api,esc,icon,icons,modal,error,load,audiencePicker});
  const inbox=window.createCrmInbox({state,api,esc,icon,icons,patientLabel,money,date,profile,moveLead,appointmentBadge,customerValueBadge,customerValuePanel,pendingQuotesValue,serviceLocationBadge,refreshPatient,patientRefreshMessage,patientRefreshes,voice,avatars,courses,onBusinessChange:()=>{state.filters={};fillFilterForm();paintFilters();}});
  const formsCenter=window.createCrmForms({state,api,esc,icon,icons,notice,profile});
  const botsCenter=window.createCrmBots({state,api,esc,icon,icons,notice,profile});
  const templateLibrary=window.createCrmMessageTemplates({api,esc,icon,icons,getContext:()=>({doctor:doctor()?.name || ""}),onError:notice});
  const dashboard=window.createCrmDashboard({api,esc,icon,icons,onWaiting:()=>{state.mode="inbox";render();inbox.setFilter("waiting");load();}});
  window.addEventListener("doc4docs-open-attendance",()=>{
    if(!state.data)return;
    if(state.mode!=="inbox"){state.mode="inbox";render();load();}
    inbox.focus();
  });
  $("closeDialog").onclick=()=>$("crmDialog").close();
  $("crmDialog").addEventListener("close",()=>$("crmDialog").querySelectorAll("audio").forEach(audio=>audio.pause()));
  $("addDoctor").onclick=doctorForm;$("newLead").onclick=leadForm;$("reload").onclick=load;
  $("bootstrap").onclick=async e=>{e.currentTarget.disabled=true;try{await api("bootstrap",{});await load();}catch(err){notice(err.message);}finally{e.target.disabled=false;}};
  $("doctor").onchange=()=>{if(state.mode==="forms" && !formsCenter.canLeave() || state.mode==="bots" && !botsCenter.canLeave()){$("doctor").value=state.doctor;return;}inbox.unmount();$("crmView").replaceChildren();state.doctor=$("doctor").value;state.pipeline="";load();};
  $("pipeline").onchange=()=>{state.pipeline=$("pipeline").value;state.filters={};state.sort="updated";fillFilterForm();if(presentation==="board")load();else{render();paintFilters();}};
  $("customerTierRules").onclick=customerTierForm;
  let searchTimer;
  $("search").oninput=()=>{state.search=$("search").value.trim();clearTimeout(searchTimer);searchTimer=setTimeout(load,300);};
  $("search").onkeydown=e=>{if(e.key==="Enter"){e.preventDefault();clearTimeout(searchTimer);load();}};
  $("crmSort").onchange=()=>{state.sort=$("crmSort").value;load();};
  $("toggleCrmFilters").onclick=()=>{const open=$("crmFilterPanel").hidden;$("crmFilterPanel").hidden=!open;$("toggleCrmFilters").setAttribute("aria-expanded",String(open));if(open)fillFilterForm();};
  $("clearCrmFilters").onclick=clearFilters;
  $("activeCrmFilters").onclick=e=>{const b=e.target.closest("[data-remove-filter]");if(b){delete state.filters[b.dataset.removeFilter];fillFilterForm();paintFilters();load();}};
  $("crmFilterForm").oninput=()=>$("crmFilterError").hidden=true;
  $("appointmentRange").onchange=()=>{if($("appointmentRange").value){const [start,end]=appointmentRange($("appointmentRange").value);$("filterAppointmentFrom").value=start;$("filterAppointmentTo").value=end;$("crmFilterError").hidden=true;}};
  $("filterAppointmentFrom").onchange=syncAppointmentRange;$("filterAppointmentTo").onchange=syncAppointmentRange;
  $("previousMonthReturns").onclick=()=>{
    const [start,end]=appointmentRange("last_month");$("filterAppointmentStatus").value="completed";$("filterAppointmentFrom").value=start;$("filterAppointmentTo").value=end;$("filterAppointmentNoUpcoming").checked=true;
    $("crmFilterForm").requestSubmit();
  };
  $("crmFilterForm").onsubmit=e=>{
    e.preventDefault();const data=new FormData(e.currentTarget),next={};
    for(const key of Object.keys(filterNames)){
      const value=data.get(key);
      if(key==="appointment_no_upcoming"){if(data.has(key))next[key]=true;}
      else if(value!=="" && value!=null)next[key]=key.endsWith("_cents")?Math.round(Number(value)*100):key==="min_sales"?Number(value):value;
    }
    if(next.min_spent_cents!=null && next.max_spent_cents!=null && next.min_spent_cents>next.max_spent_cents){$("crmFilterError").textContent="O valor mínimo não pode superar o máximo.";$("crmFilterError").hidden=false;return;}
    if(next.appointment_from && next.appointment_to && next.appointment_from>next.appointment_to){$("crmFilterError").textContent="A data inicial não pode ser posterior à final.";$("crmFilterError").hidden=false;return;}
    $("crmFilterError").hidden=true;clearTimeout(searchTimer);state.filters=next;paintFilters();$("crmFilterPanel").hidden=true;$("toggleCrmFilters").setAttribute("aria-expanded","false");load();
  };
  $("gameHelp").onclick=()=>modal("Regras da jornada",'<h3>Avanços, não volume de mensagens</h3><p class="connectionNote">Follow-up registrado: 3 XP, uma vez por oportunidade por dia. Avanço para uma nova fase aberta: 5 XP, uma única vez por fase. Ganho: 25 XP, uma única vez por oportunidade. Voltar e avançar na mesma fase não gera novos pontos. Criar clientes, anotar ou disparar mensagens não gera XP.</p><p class="connectionNote">As missões reiniciam a cada dia, no horário de São Paulo. O XP e os níveis ficam separados por doutor. Ganhos são os negócios marcados como ganhos no CRM, não pagamentos confirmados.</p>');
  document.querySelectorAll("[data-mode]").forEach(b=>b.onclick=()=>{if(state.mode==="forms" && (b.dataset.mode==="forms" || !formsCenter.canLeave()) || state.mode==="bots" && (b.dataset.mode==="bots" || !botsCenter.canLeave()))return;state.mode=b.dataset.mode;state.filters={};fillFilterForm();render();paintFilters();if(state.mode==="inbox")inbox.focus();load();});
  $("viewActions").onclick=e=>{const b=e.target.closest("button");if(!b)return;({showKanban:()=>{presentation="board";load();},showList:()=>{presentation="list";render();},openLeadTrash:()=>bulk.trash(),newPipeline:()=>pipelineForm(),editPipeline:()=>pipelineForm(true),newCampaign:campaignForm,newChannel:channelForm,importKommo:()=>importer.open(state.doctor,state.pipeline,pipeline()?.name)}[b.id])?.();};
  $("crmView").onclick=async e=>{
    const b=e.target.closest("button");if(!b)return;
    if(b.id==="toggleSalesRadar")return toggleRadar();
    if(b.id==="pendingLocations"){state.filters={service_location:"pending"};state.search="";$("search").value="";fillFilterForm();paintFilters();return load();}
    if(b.dataset.lead) return bulk.toggle(b.dataset.lead) || detail(b.dataset.lead);
    if(b.dataset.connect) return connect(b.dataset.connect);
    if(b.dataset.channelState){b.disabled=true;try{await api("channel-state",{id:b.dataset.channelState,doctor_id:state.doctor});await load();}catch(err){notice(err.message);b.disabled=false;}}
  };
  let dragging=false;
  $("crmView").ondragstart=e=>{if(bulk.isSelecting()){e.preventDefault();return;}const card=e.target.closest("[data-lead]");if(card){dragging=true;e.dataTransfer.setData("text/plain",card.dataset.lead);}};
  $("crmView").ondragend=()=>{dragging=false;};
  $("crmView").ondragover=e=>{if(e.target.closest("[data-stage]"))e.preventDefault();};
  $("crmView").ondrop=async e=>{
    const col=e.target.closest("[data-stage]");if(!col)return;e.preventDefault();const l=state.data.leads.find(l=>l.id===e.dataTransfer.getData("text/plain"));
    if(!l || l.stage_id===col.dataset.stage || l.pipeline_id!==state.pipeline)return;
    try{await moveLead(l,col.dataset.stage);}catch(err){notice(err.message);}
  };
  function sessionAllowed(data){return Boolean(data?.clinics?.some(c=>c.key==="vielle") && data.permissions?.vielle?.includes("crm.view"));}
  function session(data){
    const changed=state.session && JSON.stringify(state.session.user.crm_scope)!==JSON.stringify(data.user.crm_scope);
    state.session=data;
    if(changed){inbox.unmount();dashboard.unmount();kanban.destroy();state.data=null;state.doctor="";state.pipeline="";$("crmDialog").close();$("crmView").replaceChildren();}
    if(!sessionAllowed(data)){inbox.unmount();dashboard.unmount();templateLibrary.destroy();state.data=null;$("crmApp").hidden=true;$("setup").hidden=true;$("addDoctor").disabled=true;$("newLead").disabled=true;notice("CRM não liberado para seu usuário na Vielle. Consulte o Master.");return;}
    $("newLead").disabled=false;$("addDoctor").disabled=!data.user.is_master;if(!state.data)load();
  }
  window.addEventListener("doc4docs-session",e=>session(e.detail));
  if(window.doc4docsSession)session(window.doc4docsSession);
  document.addEventListener("visibilitychange",()=>{if(!document.hidden && !["forms","bots"].includes(state.mode) && sessionAllowed(state.session) && !$("crmDialog").open && !kanban.interacting())load();});
  setInterval(()=>{if(!document.hidden && !$("crmDialog").open && state.data && state.mode==="campaigns")load();},15000);
  setInterval(()=>{if(!document.hidden && !$("crmDialog").open && !dragging && !bulk.isSelecting() && !kanban.interacting() && state.data && ["board","inbox"].includes(state.mode))load();},60000);
  icons();
})();
