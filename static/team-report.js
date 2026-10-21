(() => {
  "use strict";
  const host = document.getElementById("teamReport"), tools = window.Doc4docsTeam;
  if (!host || !tools) return;
  const {request, escape:esc, duration, initials, labels} = tools;
  const clinic = new URLSearchParams(location.search).get("clinic") || "";
  const icon = name => `<i data-lucide="${name}" aria-hidden="true"></i>`;
  const date = new Intl.DateTimeFormat("en-CA",{timeZone:"America/Sao_Paulo",year:"numeric",month:"2-digit",day:"2-digit"}).format(new Date());
  const stamp = (n,day=false) => new Date(n*1000).toLocaleString("pt-BR",{timeZone:"America/Sao_Paulo",...(day?{day:"2-digit",month:"2-digit"}:{}),hour:"2-digit",minute:"2-digit"});
  const dayLabel = n => new Date(n*1000).toLocaleDateString("pt-BR",{timeZone:"America/Sao_Paulo",day:"2-digit",month:"2-digit"});
  const total = n => { const m=Math.floor(n/60);return `${Math.floor(m/60)}h${String(m%60).padStart(2,"0")}`; };
  let filters={date,days:"1"}, data=null, selected=null, view="table", seq=0, block=null, session=window.doc4docsSession;
  function status(p) { return `<span class="teamStatus" data-team-status="${esc(p.status)}"><i class="teamDot"></i>${labels[p.status]}<time>${p.status_since==null?'Ainda não entrou':`há ${duration(data.server_time-p.status_since)}`}</time></span>`; }
  function metrics(items) { return `<div class="teamReportMetrics">${[["Tempo logado","logged_seconds"],["Em atividade","active_seconds"],["Ociosidade","idle_seconds"]].map(([label,key])=>`<div class="teamMetric"><span>${label}</span><strong>${total(items.reduce((a,p)=>a+p[key],0))}</strong></div>`).join("")}</div>`; }
  function timeline(items) {
    const meaningful=items.flatMap(p=>p.intervals).filter(i=>i.status!=='offline');
    const start=filters.days==='1'&&meaningful.length?Math.max(data.start,Math.floor(Math.min(...meaningful.map(i=>i.start))/3600)*3600):data.start;
    const end=Math.max(start+3600,data.end),range=end-start;
    const note=block?`${block.name} · ${labels[block.status]} · ${stamp(block.start,true)} a ${stamp(block.end,true)} · ${duration(block.end-block.start)}`:'Saídas do sistema não entram no tempo logado.';
    return `<div class="teamLegend"><span><i></i>Em atividade</span><span><i class="idle"></i>Ocioso</span><span><i class="offline"></i>Desconectado</span></div><div class="teamTimeAxis">${[0,1,2,3].map(i=>`<span>${filters.days==='1'?stamp(start+range*i/3):dayLabel(start+range*i/3)}</span>`).join("")}</div>${items.map(p=>`<div class="teamTimeRow"><button type="button" class="teamTimeName" data-person="${p.id}">${esc(p.name)}</button><div class="teamTimeTrack" aria-label="Jornada de ${esc(p.name)}">${p.intervals.map((b,i)=>`<button type="button" class="teamTimeBlock ${b.status}" data-block="${p.id}:${i}" aria-pressed="${block?.person===p.id&&block?.index===i}" style="left:${Math.max(0,(b.start-start)/range*100)}%;width:${Math.max(0,(b.end-Math.max(start,b.start))/range*100)}%" title="${esc(labels[b.status])} · ${stamp(b.start,true)} a ${stamp(b.end,true)}" aria-label="${esc(p.name)}: ${labels[b.status]}, ${stamp(b.start,true)} a ${stamp(b.end,true)}"></button>`).join("")}</div></div>`).join("")}<p class="teamTimeDetail" aria-live="polite">${esc(note)}</p>`;
  }
  function render() {
    if (!data) return;
    const p=data.members.find(p=>p.id===selected),items=p?[p]:data.members;
    const clinicName=session?.clinics?.find(c=>c.key===clinic)?.name || clinic;
    host.innerHTML=`<header class="teamReportHeader"><div><p>Gestão / ${esc(clinicName)}</p><h1>Presença e atividade</h1></div><form class="teamReportControls"><label>Até<input name="date" type="date" value="${esc(filters.date)}" max="${date}" required></label><label>Período<select name="days">${[["1","Dia"],["7","Últimos 7 dias"],["30","Últimos 30 dias"]].map(([value,label])=>`<option value="${value}" ${filters.days===value?'selected':''}>${label}</option>`).join("")}</select></label><button type="submit" class="teamIcon" title="Atualizar relatório" aria-label="Atualizar relatório">${icon("refresh-cw")}</button></form></header>${p?`<button type="button" class="teamBack" data-team-report-back>${icon("arrow-left")}Toda a equipe</button><div class="teamDetailHeader"><span class="teamAvatar">${esc(initials(p.name))}</span><div><h1>${esc(p.name)}</h1>${status(p)}</div></div>`:''}${metrics(items)}${p?'':`<div class="teamReportTabs" role="tablist" aria-label="Relatório de presença"><button type="button" role="tab" aria-selected="${view==='table'}" data-team-view="table">${icon("users-round")}Equipe</button><button type="button" role="tab" aria-selected="${view==='timeline'}" data-team-view="timeline">${icon("chart-no-axes-gantt")}Linha do tempo</button></div>`}${!items.length?'<p class="teamLoadState">Nenhum colaborador nesta clínica.</p>':p||view==='timeline'?timeline(items):`<table class="teamTable"><thead><tr><th>Colaborador</th><th>Status atual</th><th>Logado</th><th>Atividade</th><th>Ocioso</th><th></th></tr></thead><tbody>${items.map(p=>`<tr><td><button type="button" class="teamReportPerson" data-person="${p.id}"><span class="teamAvatar">${esc(initials(p.name))}</span><span>${esc(p.name)}</span></button></td><td>${status(p)}</td><td data-label="Logado">${total(p.logged_seconds)}</td><td data-label="Atividade">${total(p.active_seconds)}</td><td data-label="Ocioso">${total(p.idle_seconds)}</td><td><button type="button" class="teamIcon" data-person="${p.id}" aria-label="Ver jornada de ${esc(p.name)}" title="Ver jornada">${icon("chevron-right")}</button></td></tr>`).join("")}</tbody></table>`}${p?`<h2>Entradas e saídas</h2>${p.sessions.map(event=>`<div class="teamSessionEvent"><time>${stamp(event.time,true)}</time>${icon(event.kind==='login'?'log-in':'log-out')}<span>${event.kind==='login'?'Entrou no sistema':'Saiu do sistema / conexão encerrada'}</span></div>`).join("")||'<p class="teamReportNote">Sem sessões registradas no período.</p>'}`:''}<p class="teamReportNote">Ociosidade: 5 minutos sem interação. Este registro não mede produtividade nem substitui controle de ponto.</p><p class="teamReportNote">${data.first_record_at?`Dados desde ${stamp(data.first_record_at,true)}. `:'Os dados começam a ser registrados após a publicação. '}Horário de Brasília · atualizado às ${stamp(data.server_time)}.</p>`;
    host.querySelector("form").onsubmit=e=>{e.preventDefault();filters=Object.fromEntries(new FormData(e.target));block=null;load();};
    host.querySelectorAll("form input,form select").forEach(field=>field.onchange=()=>host.querySelector("form").requestSubmit());
    window.lucide?.createIcons({root:host});
  }
  host.addEventListener("click",event=>{const b=event.target.closest("button");if(!b)return;if(b.dataset.person){selected=Number(b.dataset.person);block=null;render();}else if(b.hasAttribute("data-team-report-back")){selected=null;block=null;render();}else if(b.dataset.teamView){view=b.dataset.teamView;block=null;render();}else if(b.dataset.block){const [id,index]=b.dataset.block.split(":").map(Number),person=data.members.find(p=>p.id===id);block={...person.intervals[index],person:id,index,name:person.name};render();}else if(b.hasAttribute("data-team-report-retry"))load();});
  async function load() {
    if (!session) return;
    if (!session.permissions?.[clinic]?.includes("team.view")) {
      seq++;data=null;host.innerHTML='<p class="teamError" role="alert">Relatório exclusivo dos gestores autorizados.</p>';host.setAttribute("aria-busy","false");return;
    }
    const version=++seq;host.setAttribute("aria-busy","true");
    try {const result=await request(clinic,"report",undefined,filters);if(version!==seq)return;data=result;render();}
    catch(e){if(version===seq){host.innerHTML=`<p class="teamError" role="alert">${esc(e.message)}<button type="button" class="teamIcon" data-team-report-retry title="Tentar novamente" aria-label="Tentar novamente">${icon("refresh-cw")}</button></p>`;window.lucide?.createIcons({root:host});}}
    finally{if(version===seq)host.setAttribute("aria-busy","false");}
  }
  window.addEventListener("doc4docs-session",event=>{session=event.detail;load();});
  let initialPresence=false;
  window.addEventListener("doc4docs-team-presence",event=>{if(event.detail?.clinic===clinic&&!initialPresence){initialPresence=true;load();}});
  setInterval(()=>{if(!document.hidden&&!host.contains(document.activeElement))load();},60000);
  load();
})();
