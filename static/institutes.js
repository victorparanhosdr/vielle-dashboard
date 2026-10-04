"use strict";
const InstituteUI = (() => {
  const money = v => v == null ? "—" : new Intl.NumberFormat("pt-BR", {style:"currency", currency:"BRL", maximumFractionDigits:2}).format(v / 100);
  const integer = v => v == null ? "—" : new Intl.NumberFormat("pt-BR").format(v);
  const percent = v => v == null ? "—" : new Intl.NumberFormat("pt-BR", {maximumFractionDigits:1}).format(v) + "%";
  const escape = v => String(v ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const date = v => v ? new Date(v + "T12:00:00").toLocaleDateString("pt-BR", {day:"2-digit", month:"2-digit"}) : "—";
  return {money, integer, percent, escape, date};
})();
if (typeof module !== "undefined") module.exports = InstituteUI;
if (typeof document !== "undefined") (() => {
  const {money, integer, percent, escape:esc, date} = InstituteUI;
  const $ = id => document.getElementById(id);
  const state = {catalog:[], tab:"overview", report:null, request:0, scope:0, settingsRequest:0, page:1, master:false, settings:null, products:[], meta:[], charts:{}, polling:0};
  const labels = {kiwify:"Kiwify", sheets:"Google Sheets", kommo:"Kommo · REGENCODE", meta:"Meta Ads"};
  const warningLabels = {lead_missing_date:"Leads sem data válida", sale_missing_date:"Vendas sem data de aprovação", sale_invalid_amount:"Vendas com valor ou moeda não conciliados", sale_attribution:"Vendas sem campanha segura"};
  function notice(message, error=false) { $("notice").textContent=message; $("notice").classList.toggle("error",error); $("notice").hidden=!message; }
  function icons() { window.lucide?.createIcons(); }
  function query(extra={}) { return new URLSearchParams({institute:$("institute").value, course:$("course").value, from:$("from").value, to:$("to").value, ...extra}); }
  async function api(path, payload, scope=true) {
    const url = "/api/institutes" + path + (scope ? "?"+query() : "");
    const response = await fetch(url, payload === undefined ? {} : {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(payload)});
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error(data.error || "Não foi possível concluir a operação.");
    return data;
  }
  function table(headers, rows) {
    return rows.length ? `<table><thead><tr>${headers.map(h=>`<th>${esc(h)}</th>`).join("")}</tr></thead><tbody>${rows.map(row=>`<tr>${row.map(v=>`<td>${esc(v)}</td>`).join("")}</tr>`).join("")}</tbody></table>` : '<p class="empty">Nenhum registro no período selecionado.</p>';
  }
  function campaignTable(rows, full=false) {
    const headers = full ? ["Campanha","Leads","Vendas","Bruto","Líquido","Investimento","CPL","CAC","ROAS"] : ["Campanha","Leads","Vendas","Receita bruta","Investimento"];
    return table(headers, rows.map(r => full ? [r.name, integer(r.leads), integer(r.sales), money(r.gross), money(r.net), money(r.spend), money(r.cpl), money(r.cac), r.roas==null?"—":r.roas.toFixed(2)+"×"] : [r.name,integer(r.leads),integer(r.sales),money(r.gross),money(r.spend)]));
  }
  function bars(rows) {
    const max = Math.max(1,...rows.map(r=>r.count));
    return rows.length ? rows.map(r=>`<div class="stage"><span class="stage-label">${esc(r.name)}</span><div class="bar-track"><div class="bar-fill" style="width:${Math.max(1,r.count/max*100)}%"></div></div><b>${integer(r.count)}</b></div>`).join("") : '<p class="empty">Sem dados sincronizados neste período.</p>';
  }
  function chart(id, daily, counts=false) {
    state.charts[id]?.destroy();
    const green="#21674d",olive="#929c73",blue="#63899e";
    const datasets = counts ? [
      {type:"bar",label:"Leads únicos",data:daily.map(r=>r.leads),backgroundColor:"#929c7375",borderRadius:3,maxBarThickness:18},
      {type:"line",label:"Compras aprovadas",data:daily.map(r=>r.sales),borderColor:green,backgroundColor:green,pointRadius:2,tension:.25}
    ] : [
      {label:"Receita bruta",data:daily.map(r=>r.gross/100),borderColor:green,fill:true,backgroundColor:context=>{
        const area=context.chart.chartArea;if(!area)return "#21674d15";
        const gradient=context.chart.ctx.createLinearGradient(0,area.top,0,area.bottom);gradient.addColorStop(0,"#21674d48");gradient.addColorStop(1,"#21674d02");return gradient;
      }},
      {label:"Receita líquida",data:daily.map(r=>r.net/100),borderColor:olive,fill:false},
      ...(state.report.meta_coverage?[{label:"Investimento Meta",data:daily.map(r=>r.spend/100),borderColor:blue,fill:false,borderDash:[4,3]}]:[])
    ];
    state.charts[id]=new Chart($(id), {type:"line",data:{labels:daily.map(r=>date(r.day)),datasets:datasets.map(r=>({pointRadius:2,pointHoverRadius:5,borderWidth:2,tension:.25,...r}))},options:{responsive:true,maintainAspectRatio:false,animation:false,interaction:{mode:"index",intersect:false},plugins:{legend:{display:counts,labels:{usePointStyle:true,boxWidth:8,font:{size:11}}},tooltip:{backgroundColor:"#103c30",padding:12,callbacks:{label:ctx=>`${ctx.dataset.label}: ${counts?integer(ctx.raw):money(ctx.raw*100)}`}}},scales:{x:{grid:{display:false},ticks:{maxTicksLimit:7,font:{size:10},color:"#748079"},border:{display:false}},y:{beginAtZero:true,grid:{color:"#e9efeb"},border:{display:false},ticks:{maxTicksLimit:5,precision:counts?0:undefined,color:"#748079",font:{size:10},callback:v=>counts?integer(v):v>=1000?"R$ "+integer(v/1000)+" mil":"R$ "+integer(v)}}}}});
  }
  function pagination(id, data) {
    $(id).innerHTML=`<span>${integer(data.total)} registros · ${data.page} / ${data.pages}</span><button type="button" class="icon-button" data-page="${data.page-1}" ${data.page===1?"disabled":""} title="Página anterior" aria-label="Página anterior"><i data-lucide="chevron-left"></i></button><button type="button" class="icon-button" data-page="${data.page+1}" ${data.page>=data.pages?"disabled":""} title="Próxima página" aria-label="Próxima página"><i data-lucide="chevron-right"></i></button>`;
  }
  function render(report) {
    const r=report.summary, loaded=Boolean(report.sync.kiwify?.at), leadsLoaded=Boolean(report.sync.sheets?.at);
    const kpis=[
      ["Vendas aprovadas",loaded?integer(r.sales):"—","Pagamento confirmado","shopping-cart"],
      ["Receita bruta",loaded?money(r.gross):"—","Vendas aprovadas · BRL","wallet"],
      ["Receita líquida",loaded?money(r.net):"—","Valor informado pela Kiwify","chart-no-axes-column-increasing"],
      ["Leads únicos",leadsLoaded?integer(r.leads):"—","Primeira resposta no período","users"],
      ["Investimento Meta",money(r.spend),report.meta_coverage?"Campanhas selecionadas":"Conexão/período pendente","megaphone"],
      ["ROAS atribuído",r.roas==null?"—":r.roas.toFixed(2)+"×","Receita bruta atribuída ÷ anúncios","trending-up"]
    ];
    $("kpis").innerHTML=kpis.map(([label,value,caption,icon],i)=>`<article class="kpi ${i===2?"dark":""}"><span class="label"><i data-lucide="${icon}"></i>${esc(label)}</span><strong>${esc(value)}</strong><small>${esc(caption)}</small></article>`).join("")+`<div class="metric-line"><span>Conversão da captação <b>${loaded&&leadsLoaded?percent(r.conversion):"—"}</b></span><span>Ticket médio <b>${loaded?money(r.ticket):"—"}</b></span><span>Líquido menos anúncios <b>${money(r.net_after_ads)}</b></span></div>`;
    $("sourceStatus").innerHTML=Object.entries(labels).map(([key,label])=>{
      const s=report.sync[key];const status=s?.ok?"Atualizado "+new Date(s.at*1000).toLocaleString("pt-BR",{day:"2-digit",month:"2-digit",hour:"2-digit",minute:"2-digit"}):s?.error||"Não sincronizado";
      return `<span class="${s?.error?"source-error":""}" title="${esc(status)}"><i data-lucide="${s?.ok?"circle-check":"circle-dashed"}"></i>${esc(label)} · ${s?.ok?esc(status):s?.error?"Verificar conexão":"Pendente"}</span>`;
    }).join("");
    $("funnelTitle").textContent="Comercial · "+report.course.pipeline_name;
    $("commercialPipeline").textContent="Kommo · "+report.course.pipeline_name;
    $("stages").innerHTML=bars(report.stages);$("commercialStages").innerHTML=bars(report.stages);
    $("funnelResult").textContent=(loaded?integer(r.sales):"—")+" compras aprovadas · Kiwify";
    $("campaignSummary").innerHTML=campaignTable(report.campaigns.slice(0,5));$("campaignTable").innerHTML=campaignTable(report.campaigns,true);
    $("orderStatuses").innerHTML=bars(report.statuses);
    $("ordersTable").innerHTML=table(["Data","Aluno","Status","Campanha","Bruto","Líquido","Pagamento"],report.orders.rows.map(s=>[date(s.day),s.name,s.status_label,s.campaign,money(s.gross),money(s.net),s.payment_method]));
    $("leadsTable").innerHTML=table(["Data","Lead","E-mail","Campanha","Origem","Anúncio","Pontuação"],report.leads.rows.map(s=>[date(s.created_day),s.name,s.email,s.campaign||"Sem campanha",s.source,s.content,s.score]));
    pagination("ordersPages",report.orders);pagination("leadsPages",report.leads);
    const count=Object.values(report.warnings).reduce((a,b)=>a+b,0);
    $("validation").hidden=!count;$("validation").querySelector("span").textContent=`Validar ${integer(count)} pendências`;
    $("pendingList").innerHTML=Object.entries(report.warnings).map(([key,count])=>`<p><b>${esc(warningLabels[key]||key)}</b>: ${integer(count)}</p>`).join("")+report.pending.map(p=>`<div class="pending-item"><b>${esc(p.name||p.id)}</b><small>${esc(p.id)}</small>${esc(p.reason)}</div>`).join("");
    if(report.totals_partial)notice("Total parcial: há pagamentos sem data, valor ou moeda conciliados. Abra a validação para conferir.");
    chart("revenueChart",report.daily);
    if(state.tab==="leads")chart("leadsChart",report.daily,true);
    icons();
  }
  async function load() {
    const request=++state.request;
    if(!$("institute").value||!$("course").value)return;
    const filters={page:state.page, search:state.tab==="sales"?$("salesSearch").value:state.tab==="leads"?$("leadsSearch").value:"",status:state.tab==="sales"?$("orderStatus").value:""};
    try{
      const response=await fetch("/api/institutes/report?"+query(filters));const data=await response.json();
      if(request!==state.request)return;
      if(!response.ok||!data.ok)throw new Error(data.error||"Não foi possível carregar o relatório.");
      state.report=data;notice("");render(data);$("export").disabled=false;$("sync").disabled=false;
    }catch(error){if(request===state.request)notice(error.message,true);}
  }
  function showLanding() {
    state.scope++;state.request++;state.settingsRequest++;clearTimeout(state.polling);
    state.report=null;state.settings=null;
    $("instituteApp").hidden=true;$("instituteLanding").hidden=false;
    $("landingStatus").textContent=state.catalog.length?"":"Nenhum instituto liberado para seu usuário. Fale com o Master.";
    $("instituteCards").innerHTML=state.catalog.map(institute=>`<article class="institute-card"><div class="institute-card-mark"><img src="/doc4docs-logo-white.png" alt="DOC4DOCS"></div><div class="institute-card-content"><h2>${esc(institute.name)}</h2><p class="course-count">${integer(institute.courses.length)} ${institute.courses.length===1?"curso":"cursos"}</p><p class="course-names">${institute.courses.map(c=>esc(c.name)).join("<br>")}</p><button type="button" class="primary" data-institute-select="${esc(institute.key)}" ${institute.courses.length?"":"disabled"}><span>${institute.courses.length?"Acessar instituto":"Sem cursos cadastrados"}</span><i data-lucide="arrow-right"></i></button></div></article>`).join("");
    history.replaceState(null,"","/institutos");icons();
  }
  function openInstitute(key) {
    if(!state.catalog.some(institute=>institute.key===key&&institute.courses.length))return;
    $("institute").value=key;courses();switchTab("overview",false);selectCourse();
  }
  function selectCourse() {
    state.scope++;state.settingsRequest++;clearTimeout(state.polling);
    $("sync").disabled=true;$("sync").querySelector("span").textContent="Atualizar dados";$("export").disabled=true;
    const institute=state.catalog.find(r=>r.key===$("institute").value);
    $("instituteName").textContent=institute?.name||"Institutos";
    const course=institute?.courses.find(r=>r.key===$("course").value);
    $("courseName").textContent=course?.name||"Cursos";
    $("instituteLanding").hidden=true;$("instituteApp").hidden=false;
    const params=new URLSearchParams({institute:$("institute").value,course:$("course").value});history.replaceState(null,"","/institutes.html?"+params);
    state.page=1;state.settings=null;state.report=null;load();if(state.tab==="integrations")loadSettings();
  }
  function courses() {
    const institute=state.catalog.find(r=>r.key===$("institute").value);
    $("course").innerHTML=(institute?.courses||[]).map(c=>`<option value="${esc(c.key)}">${esc(c.name)}</option>`).join("");
  }
  function switchTab(tab,fetchReport=true) {
    if(tab==="integrations"&&!state.master)return;
    state.tab=tab;state.page=1;
    document.querySelectorAll("[data-tab]").forEach(b=>b.setAttribute("aria-selected",b.dataset.tab===tab));
    document.querySelectorAll("[data-panel]").forEach(p=>p.hidden=p.id!==tab);
    if(!fetchReport)return;
    if(tab==="integrations")loadSettings();else load();
  }
  function productsChoices() {
    const selected=state.settings?.courses.find(c=>c.key===$("course").value)?.product_ids||[];
    const products=[...state.products];selected.forEach(id=>{if(!products.some(p=>p.id===id))products.push({id,name:"Produto vinculado · "+id});});
    $("productChoices").innerHTML=products.length?products.map(p=>`<label><input type="checkbox" value="${esc(p.id)}" ${selected.includes(p.id)?"checked":""}>${esc(p.name)}</label>`).join(""):'<p class="empty">Nenhum produto vinculado.</p>';
  }
  function metaChoices() {
    const selected=state.settings?.campaigns||[], rows=[...state.meta];
    selected.forEach(r=>{if(!rows.some(p=>p.id===r.id))rows.push(r);});
    $("metaChoices").innerHTML=rows.length?rows.map(r=>{const saved=selected.find(s=>s.id===r.id);return `<div class="meta-choice" data-id="${esc(r.id)}"><input type="checkbox" aria-label="Selecionar ${esc(r.name)}" ${saved?"checked":""}><div><b>${esc(r.name)}</b><small>${esc(r.id)}</small></div><label>utm_campaign (separadas por ;)<input type="text" list="utmSuggestions" maxlength="2000" value="${esc((saved?.aliases||[]).join("; "))}"></label></div>`;}).join(""):'<p class="empty">Nenhuma campanha selecionada.</p>';
  }
  async function loadSettings() {
    const request=++state.settingsRequest;
    try{
      const data=await api("/settings");if(request!==state.settingsRequest)return;state.settings=data;state.products=[];state.meta=[];
      const form=$("credentialsForm");["kiwify_client_id","kiwify_account_id","meta_account_id","meta_version"].forEach(k=>form.elements[k].value=data.settings[k]||(k==="meta_version"?"v22.0":""));
      ["kiwify_client_secret","meta_access_token"].forEach(k=>{form.elements[k].value="";form.elements[k].placeholder=data.settings[k+"_configured"]?"Já configurado · em branco mantém":"Não informado";});
      const course=data.courses.find(c=>c.key===$("course").value);["name","sheet_id","sheet_gid","pipeline_name"].forEach(k=>$("courseForm").elements[k].value=course[k]);
      productsChoices();metaChoices();
      $("utmSuggestions").innerHTML=data.utms.map(v=>`<option value="${esc(v)}"></option>`).join("");
      $("memberChoices").innerHTML=data.users.filter(u=>!u.is_master).map(u=>`<label><input type="checkbox" value="${u.id}" ${data.members.includes(u.id)?"checked":""}>${esc(u.nome)}</label>`).join("")||'<p class="empty">Nenhum usuário comum ativo.</p>';
      icons();
    }catch(error){if(request===state.settingsRequest)notice(error.message,true);}
  }
  async function save(form,path,payload,after) {
    const status=form.querySelector(".form-status"),button=form.querySelector('[type="submit"]');button.disabled=true;status.textContent="Salvando...";
    try{await api(path,payload);if(after)await after();status.textContent="Salvo.";state.report=null;await load();}catch(error){status.textContent=error.message;}finally{button.disabled=false;}
  }
  $("credentialsForm").addEventListener("submit",event=>{event.preventDefault();const values=Object.fromEntries(new FormData(event.target));save(event.target,"/settings",values,loadSettings);});
  $("courseForm").addEventListener("submit",event=>{event.preventDefault();const values={key:$("course").value,...Object.fromEntries(new FormData(event.target)),product_ids:[...$("productChoices").querySelectorAll("input:checked")].map(i=>i.value)};save(event.target,"/courses",values,async()=>{await loadSettings();const info=state.settings.courses.find(c=>c.key===$("course").value);state.catalog.find(r=>r.key===$("institute").value).courses=state.settings.courses;$("courseName").textContent=info.name;});});
  $("campaignForm").addEventListener("submit",event=>{event.preventDefault();const selected=[...$("metaChoices").querySelectorAll(".meta-choice")].filter(row=>row.querySelector("input[type=checkbox]").checked).map(row=>({id:row.dataset.id,name:row.querySelector("b").textContent,aliases:row.querySelector("input[type=text]").value.split(";").map(v=>v.trim()).filter(Boolean)}));save(event.target,"/campaigns",{campaigns:selected},loadSettings);});
  $("membersForm").addEventListener("submit",event=>{event.preventDefault();save(event.target,"/members",{user_ids:[...$("memberChoices").querySelectorAll("input:checked")].map(i=>Number(i.value))});});
  $("loadProducts").addEventListener("click",async event=>{const b=event.currentTarget;b.disabled=true;try{state.products=(await api("/products")).products;productsChoices();}catch(error){notice(error.message,true);}finally{b.disabled=false;}});
  $("loadCampaigns").addEventListener("click",async event=>{const b=event.currentTarget;b.disabled=true;try{const data=await api("/meta-campaigns");state.meta=data.campaigns;metaChoices();notice("Conta Meta: "+data.account.name);}catch(error){notice(error.message,true);}finally{b.disabled=false;}});
  $("importCsv").addEventListener("click",async event=>{const b=event.currentTarget,file=$("leadCsv").files[0];if(!file)return notice("Selecione o CSV exportado da planilha.",true);if(file.size>6*1024*1024)return notice("O arquivo deve ter até 6 MB.",true);b.disabled=true;try{await api("/import-leads",{csv:await file.text()});$("leadCsv").value="";await load();notice("Leads importados do CSV.");}catch(error){notice(error.message,true);}finally{b.disabled=false;}});
  async function pollSync(scope=state.scope) {
    try{const data=await api("/sync");if(scope!==state.scope)return;if(data.running){$("sync").disabled=true;$("sync").querySelector("span").textContent="Atualizando "+(labels[data.source]||"fontes");state.polling=setTimeout(()=>pollSync(scope),2500);}else{$("sync").disabled=false;$("sync").querySelector("span").textContent="Atualizar dados";await load();if(scope!==state.scope)return;const errors=Object.entries(state.report?.sync||{}).filter(([,s])=>!s.ok&&s.error);notice(errors.length?errors.map(([key,s])=>(labels[key]||key)+": "+s.error).join(" "):"Dados atualizados.",Boolean(errors.length));}}catch(error){if(scope!==state.scope)return;$("sync").disabled=false;$("sync").querySelector("span").textContent="Atualizar dados";notice(error.message,true);}
  }
  $("sync").addEventListener("click",async()=>{const scope=state.scope;$("sync").disabled=true;try{await api("/sync",{from:$("from").value,to:$("to").value});if(scope===state.scope)pollSync(scope);}catch(error){if(scope!==state.scope)return;$("sync").disabled=false;notice(error.message,true);}});
  $("export").addEventListener("click",async event=>{const button=event.currentTarget;button.disabled=true;try{const response=await fetch("/api/institutes/export?"+query({search:state.tab==="sales"?$("salesSearch").value:"",status:state.tab==="sales"?$("orderStatus").value:""}));if(!response.ok)throw new Error((await response.json()).error||"Não foi possível exportar.");const url=URL.createObjectURL(await response.blob()),a=document.createElement("a");a.href=url;a.download="doc4docs-"+$("course").value+".xlsx";a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(error){notice(error.message,true);}finally{button.disabled=false;}});
  document.querySelectorAll("[data-tab]").forEach(b=>b.addEventListener("click",()=>switchTab(b.dataset.tab)));
  $("campaignDetails").addEventListener("click",()=>switchTab("leads"));
  $("switchInstitute").addEventListener("click",showLanding);
  $("instituteCards").addEventListener("click",event=>{const button=event.target.closest("[data-institute-select]");if(button&&!button.disabled)openInstitute(button.dataset.instituteSelect);});
  $("institute").addEventListener("change",()=>{clearTimeout(state.polling);courses();selectCourse();});$("course").addEventListener("change",()=>{clearTimeout(state.polling);selectCourse();});
  $("filters").addEventListener("submit",event=>{event.preventDefault();state.page=1;load();});
  let debounce;["salesSearch","leadsSearch"].forEach(id=>$(id).addEventListener("input",()=>{clearTimeout(debounce);debounce=setTimeout(()=>{state.page=1;load();},300);}));
  $("orderStatus").addEventListener("change",()=>{state.page=1;load();});
  ["ordersPages","leadsPages"].forEach(id=>$(id).addEventListener("click",event=>{const b=event.target.closest("[data-page]");if(b&&!b.disabled){state.page=Number(b.dataset.page);load();}}));
  $("validation").addEventListener("click",()=>$("validationDialog").showModal());$("definitions").addEventListener("click",()=>$("definitionsDialog").showModal());document.querySelectorAll("[data-close]").forEach(b=>b.addEventListener("click",()=>b.closest("dialog").close()));
  window.addEventListener("doc4docs-session",event=>{if(state.master&&!event.detail.user.is_master)location.reload();});
  async function initialize() {
    try{const meResponse=await fetch("/api/auth/me"),me=await meResponse.json();if(!meResponse.ok)throw new Error(me.error);state.master=Boolean(me.user.is_master);document.querySelectorAll("[data-master-only]").forEach(e=>e.hidden=!state.master);
      state.catalog=(await api("",undefined,false)).institutes;
      $("institute").innerHTML=state.catalog.map(i=>`<option value="${esc(i.key)}">${esc(i.name)}</option>`).join("");const params=new URLSearchParams(location.search);if(state.catalog.some(i=>i.key===params.get("institute")))$("institute").value=params.get("institute");courses();if([...$("course").options].some(c=>c.value===params.get("course")))$("course").value=params.get("course");
      const now=new Date(),local=d=>`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,"0")}-${String(d.getDate()).padStart(2,"0")}`;$("from").value=local(new Date(now.getFullYear(),now.getMonth(),1));$("to").value=local(now);
      if(state.catalog.some(i=>i.key===params.get("institute")&&i.courses.length))selectCourse();else showLanding();icons();
    }catch(error){$("landingStatus").textContent=error.message||"Não foi possível carregar seus institutos.";notice(error.message||"Não foi possível carregar seus institutos.",true);}
  }
  initialize();
})();
