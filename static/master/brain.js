(() => {
  "use strict";
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const date = value => value ? new Date(Number(value) * 1000).toLocaleString("pt-BR") : "Sem registro";
  const icons = {experts:"hospital",kommo:"messages-square",meta:"megaphone",sync:"refresh-cw",db:"database",report:"workflow",ai:"sparkles",pdf:"file-text",exams:"scan-line",body:"heart-pulse",dashboard:"chart-no-axes-combined",commercial:"handshake",financial:"wallet",patient_followup:"users",budget_followup:"clipboard-list",paid_traffic:"chart-column",whatsapp_review:"message-circle-check",body_evolution:"person-standing"};
  const drawIcons = () => window.lucide?.createIcons({attrs:{width:18,height:18}});
  const flows = {all:null,revenue:["experts","sync","db","report","dashboard"],body:["experts","pdf","exams","body","body_evolution"],whatsapp:["kommo","sync","db","ai","whatsapp_review"]};
  const {FOCUSES, compareAnalyses, compareCode, technicalReport, syncSample, datasetDate, historyNotice, filterInventory, inventoryMetrics} = window.BrainData;
  let data=null, flow="all", selected="report", requestId=0, analyzing=false, controller=null, selectedAnalysisId=null;
  const requested = new URLSearchParams(location.search).get("clinic");
  if ([...$("clinic").options].some(o=>o.value===requested)) $("clinic").value=requested;
  try {document.documentElement.dataset.theme=localStorage.getItem("brain-theme") || "light";} catch (_) {}
  $("theme").addEventListener("click",()=>{const value=document.documentElement.dataset.theme==="dark"?"light":"dark";document.documentElement.dataset.theme=value;try{localStorage.setItem("brain-theme",value);}catch(_){};});
  async function api(url, body, signal) {
    const response=await fetch(url,{method:body?"POST":"GET",headers:body?{"Content-Type":"application/json"}:{},body:body?JSON.stringify(body):undefined,signal});
    const result=await response.json().catch(()=>({error:"Resposta inesperada do servidor."}));
    if(!response.ok) throw new Error(result.error || "Não foi possível concluir a solicitação.");
    return result;
  }
  function setTab(name) {
    document.querySelectorAll("[data-tab]").forEach(button=>{const active=button.dataset.tab===name;button.setAttribute("aria-selected",String(active));button.tabIndex=active?0:-1;$(button.getAttribute("aria-controls")).hidden=!active;});
    if(name==="map") requestAnimationFrame(drawWires);
  }
  document.querySelectorAll("[data-tab]").forEach((button,index)=>{
    button.addEventListener("click",()=>setTab(button.dataset.tab));
    button.addEventListener("keydown",event=>{if(!["ArrowLeft","ArrowRight","Home","End"].includes(event.key))return;event.preventDefault();const tabs=[...document.querySelectorAll("[data-tab]")];const next=event.key==="Home"?0:event.key==="End"?tabs.length-1:(index+(event.key==="ArrowRight"?1:-1)+tabs.length)%tabs.length;tabs[next].focus();setTab(tabs[next].dataset.tab);});
  });
  function graph() {
    const nodes=data.architecture.nodes;
    if(!nodes.some(n=>n.id===selected))selected="report";
    document.querySelector('[data-flow="body"]').hidden=!nodes.some(n=>n.id==="body");
    if(flow==="body"&&!nodes.some(n=>n.id==="body"))flow="all";
    $("lanes").innerHTML=["Fontes","Entrada e dados","Processamento","Módulos"].map((name,lane)=>`<div class="lane"><h2><span>0${lane+1}</span>${name}</h2>${nodes.filter(n=>n.lane===lane).map(n=>{
      const status=data.health.integrations[n.id];
      return `<button class="node" data-node="${esc(n.id)}" aria-pressed="false"><i data-lucide="${icons[n.id]||"box"}" aria-hidden="true"></i><span><strong>${esc(n.label)}</strong><small>${esc(n.subtitle)}</small>${status?`<span class="nodeStatus ${esc(status.state)}">${esc(status.label)}</span>`:""}</span></button>`;
    }).join("")}</div>`).join("");
    document.querySelectorAll("[data-node]").forEach(button=>button.addEventListener("click",()=>{selected=button.dataset.node;selectNode();}));
    selectNode();drawIcons();requestAnimationFrame(drawWires);
  }
  function selectNode() {
    if(!data)return;
    const chosen=flows[flow],node=data.architecture.nodes.find(n=>n.id===selected);
    document.querySelectorAll("[data-flow]").forEach(b=>b.setAttribute("aria-pressed",String(b.dataset.flow===flow)));
    document.querySelectorAll("[data-node]").forEach(b=>{const id=b.dataset.node;b.setAttribute("aria-pressed",String(id===selected));b.classList.toggle("inPath",Boolean(chosen?.includes(id)));b.classList.toggle("faded",Boolean(chosen&&!chosen.includes(id)&&id!==selected));});
    const linked=data.architecture.edges.filter(e=>e.includes(selected)).map(e=>data.architecture.nodes.find(n=>n.id===e.find(id=>id!==selected))?.label).filter(Boolean);
    $("nodeDetail").innerHTML=`<div><span class="kicker">Componente selecionado</span><h3>${esc(node.label)}</h3><p>${esc(node.rule)}</p></div><div><span class="kicker">Código ${node.files_verified?"verificado":"a revisar"}</span><ul>${node.files.map(f=>`<li><code>${esc(f)}</code></li>`).join("")}</ul></div><div><span class="kicker">Conexões</span><ul>${linked.map(l=>`<li>${esc(l)}</li>`).join("")}</ul></div>`;
    drawWires();
  }
  function drawWires() {
    if(!data||$("mapPanel").hidden)return;
    const map=$("systemMap"),bounds=map.getBoundingClientRect(),svg=$("wires"),chosen=flows[flow];
    svg.setAttribute("viewBox",`0 0 ${bounds.width} ${bounds.height}`);svg.replaceChildren();
    for(const [from,to] of data.architecture.edges){
      const a=document.querySelector(`[data-node="${CSS.escape(from)}"]`),b=document.querySelector(`[data-node="${CSS.escape(to)}"]`);if(!a||!b)continue;
      const ar=a.getBoundingClientRect(),br=b.getBoundingClientRect();
      const same=data.architecture.nodes.find(n=>n.id===from).lane===data.architecture.nodes.find(n=>n.id===to).lane;
      const x1=(same?ar.left+ar.width/2:ar.right)-bounds.left,y1=(same?ar.bottom:ar.top+ar.height/2)-bounds.top,x2=(same?br.left+br.width/2:br.left)-bounds.left,y2=(same?br.top:br.top+br.height/2)-bounds.top;
      const path=document.createElementNS("http://www.w3.org/2000/svg","path");path.setAttribute("d",same?`M${x1} ${y1} L${x2} ${y2}`:`M${x1} ${y1} C${x1+18} ${y1},${x2-18} ${y2},${x2} ${y2}`);
      path.setAttribute("class",(chosen?chosen.includes(from)&&chosen.includes(to):from===selected||to===selected)?"lit":chosen?"dim":"");svg.append(path);
    }
  }
  document.querySelectorAll("[data-flow]").forEach(b=>b.addEventListener("click",()=>{flow=b.dataset.flow;selected=flow==="body"?"body":flow==="whatsapp"?"ai":"report";selectNode();}));
  new ResizeObserver(drawWires).observe($("systemMap"));
  function health() {
    const names={experts:"Clínica Experts",kommo:"Kommo",meta:"Meta Ads"};
    $("integrations").innerHTML=Object.entries(names).map(([key,name])=>{
      const s=data.health.integrations[key]||{state:"unknown",label:"Sem histórico",recent:[]};
      const sample=syncSample(s);
      return `<article class="integration"><h3><i data-lucide="${icons[key]}" aria-hidden="true"></i>${name}</h3><span class="state ${esc(s.state)}">${esc(s.label)}</span><dl><dt>Último sucesso</dt><dd>${esc(date(s.last_success))}</dd><dt>Duração da última tentativa</dt><dd>${s.duration_seconds==null?"Não registrada":esc(s.duration_seconds)+" s"}</dd><dt>Falhas na amostra</dt><dd>${esc(sample.failures)}</dd></dl><p class="sampleNote">${esc(sample.label)}${sample.inconsistent?` · ${esc(sample.inconsistent)}`:""}</p>${s.recent?.length?`<details><summary>Histórico de tentativas</summary><ul>${s.recent.map(r=>`<li>${esc(date(r.started_at))} · ${r.valid===false?"Registro inconsistente":r.finished_at?(r.ok===1?"Concluída":"Falhou"):"Sem conclusão registrada"}</li>`).join("")}</ul></details>`:""}</article>`;
    }).join("");
    const alerts=data.evidence.filter(e=>!["ok","info"].includes(e.state));
    $("attentionCount").textContent=alerts.length?String(alerts.length):"";
    $("evidence").innerHTML=(alerts.length?alerts:data.evidence.filter(e=>e.state==="ok")).map(e=>`<div class="evidenceRow"><i data-lucide="${e.state==="error"?"circle-alert":e.state==="ok"?"check-check":"scan-eye"}" aria-hidden="true"></i><div><p>${esc(e.message)}</p><code>${esc(e.id)}</code></div></div>`).join("")||'<p class="empty">Sem medições disponíveis.</p>';
    $("datasets").innerHTML=data.health.datasets.map(row=>`<tr><td><code>${esc(row.table)}</code>${row.date_column?`<span class="dateSource">${esc(row.date_column)}</span>`:""}</td><td>${typeof row.records==="number"?row.records.toLocaleString("pt-BR"):"Não disponível"}</td><td>${esc(datasetDate(row,"first_date"))}</td><td>${esc(datasetDate(row,"last_date"))}</td></tr>`).join("")||'<tr><td colspan="4">Base sem dados de diagnóstico disponíveis.</td></tr>';
    $("limitations").innerHTML=data.limitations.map(item=>`<li>${esc(item)}</li>`).join("");
  }
  function renderAnalysis(analysis) {
    if(!analysis){$("analysis").innerHTML=`<p class="empty">${historyNotice(data?.history_status)?"Nenhuma análise legível disponível nesta leitura.":"Nenhuma análise salva para esta clínica."}</p>`;return;}
    const labels={observacao:"Observação",hipotese:"Hipótese",melhoria:"Melhoria",alta:"alta",media:"média",baixa:"baixa"};
    const evidence=analysis.evidence||[];
    const changed=compareCode(analysis,{fingerprint:data?.inventory.fingerprint,inventory_status:data?.inventory.status});
    const version=changed===null?"Versão do código não comparável":changed?"Versão diferente do código":"Mesma versão do código";
    $("analysis").innerHTML=`<div class="analysisSummary"><span class="kicker">${esc(analysis.model)} · ${version}</span><p>${esc(analysis.summary)}</p><time>Gerada em ${esc(date(analysis.created_at))} · Dados observados em ${esc(date(analysis.observed_at))}</time></div>${analysis.findings.map(f=>`<article class="finding"><span class="type">${esc(labels[f.kind]||"Hipótese")} · Prioridade ${esc(labels[f.priority]||f.priority)}</span><h3>${esc(f.title)}</h3><p>${esc(f.detail)}</p><p class="recommendation">${esc(f.recommendation)}</p><details><summary>Evidências (${f.evidence_ids.length})</summary><ul>${f.evidence_ids.map(id=>{const e=evidence.find(e=>e.id===id);return `<li><code>${esc(id)}</code> · ${esc(e?.message||"Evidência não disponível")}${e?.last_success?" · "+esc(date(e.last_success)):""}</li>`;}).join("")}</ul></details></article>`).join("")}`;
  }
  function savedAnalyses() { return data?.analyses || (data?.analysis ? [data.analysis] : []); }
  function currentAnalysis() {
    const history=savedAnalyses();
    return history.find(item=>String(item.id)===selectedAnalysisId) || history[0] || null;
  }
  function renderHistory() {
    const history=savedAnalyses(), current=currentAnalysis();
    const notice=historyNotice(data?.history_status);
    $("historyNotice").textContent=notice;$("historyNotice").hidden=!notice;
    selectedAnalysisId=current?String(current.id):null;
    $("historyToolbar").hidden=!history.length;
    $("analysisHistory").innerHTML=history.map(item=>`<option value="${esc(item.id)}">${esc(date(item.created_at))} · ${esc(FOCUSES[item.focus]||"Análise")}</option>`).join("");
    if(current)$("analysisHistory").value=selectedAnalysisId;
    $("historyCount").textContent=`${history.length} ${history.length===1?"análise disponível":"análises disponíveis"}`;
    renderAnalysis(current);
    $("analysisComparison").hidden=!current;
    const previous=history[history.indexOf(current)+1], comparison=compareAnalyses(previous,current);
    $("comparisonDates").textContent=comparison?`${date(comparison.from)} → ${date(comparison.to)}`:"";
    if(!comparison){$("comparisonStatus").textContent=previous?"Comparação indisponível: as datas das leituras não permitem confirmar a ordem cronológica.":"Sem análise anterior disponível para comparação.";$("comparisonChanges").replaceChildren();return;}
    const notices=[comparison.codeChanged===null?"Comparação de código indisponível: uma das leituras não tem inventário completo.":comparison.codeChanged?"O código mudou entre as duas leituras.":"Mesma versão do código nas duas leituras."];
    if(!comparison.datasetComparisonAvailable)notices.push("Uma das análises não possui contagens de base salvas.");
    notices.push(comparison.changes.length?"Variações são diferenças entre leituras, não confirmação de falha ou correção.":"Nenhuma variação nas medições comparáveis.");
    $("comparisonStatus").textContent=notices.join(" ");
    const value=(change,key)=>change[key]==null?"Não disponível":change.kind==="sync"?date(change[key]):typeof change[key]==="number"?change[key].toLocaleString("pt-BR"):change[key];
    $("comparisonChanges").innerHTML=comparison.changes.map(change=>`<div class="comparisonRow"><div><strong>${esc(change.label)}</strong><code>${esc(change.id)}</code></div><div class="comparisonValues"><span>${esc(value(change,"before"))}</span><i data-lucide="arrow-right" aria-hidden="true"></i><span>${esc(value(change,"after"))}</span>${change.delta!=null?`<b class="delta">${change.delta>0?"+":""}${esc(change.delta.toLocaleString("pt-BR"))}</b>`:""}</div></div>`).join("");
    drawIcons();
  }
  $("analysisHistory").addEventListener("change",()=>{selectedAnalysisId=$("analysisHistory").value;renderHistory();});
  $("exportReport").addEventListener("click",()=>{
    if(!data)return;
    const report=technicalReport(data,currentAnalysis());
    const url=URL.createObjectURL(new Blob([JSON.stringify(report,null,2)],{type:"application/json;charset=utf-8"}));
    const link=document.createElement("a");link.href=url;link.download=`doc4docs-diagnostico-${data.clinic}-${new Date(data.generated_at*1000).toISOString().slice(0,10)}.json`;
    document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
  });
  function renderInventory(revealMatches=false) {
    const query=$("codeSearch").value, result=filterInventory(data?.inventory,query);
    $("codeSearch").disabled=!data;
    $("clearCodeSearch").disabled=!data||!query;
    const count=data?`${result.files.length} de ${result.totalFiles} arquivos · ${result.routes.length} de ${result.totalRoutes} rotas${data.inventory.status==="partial"?" · Leitura parcial":""}`:"Inventário aguardando leitura.";
    if($("inventoryCount").textContent!==count)$("inventoryCount").textContent=count;
    $("files").innerHTML=result.files.map(f=>{const metrics=inventoryMetrics(f);return `<tr><td><code>${esc(f.name)}</code></td><td>${esc(metrics.functions)}</td><td>${esc(metrics.dependencies)}</td><td><code>${esc(metrics.hash)}</code></td></tr>`;}).join("")||`<tr><td colspan="4" class="empty">${data?"Nenhum arquivo encontrado.":"Aguardando inventário."}</td></tr>`;
    $("routeCount").textContent=data?`(${result.routes.length} de ${result.totalRoutes})`:"";
    $("routes").innerHTML=result.routes.map(route=>`<code>${esc(route)}</code>`).join("")||`<p class="muted">${data?"Nenhuma rota encontrada.":"Aguardando inventário."}</p>`;
    if(revealMatches&&query.trim()&&result.routes.length)$("detectedRoutes").open=true;
  }
  $("codeSearch").addEventListener("input",()=>renderInventory(true));
  function clearInventorySearch() {$("codeSearch").value="";renderInventory();$("codeSearch").focus();}
  $("clearCodeSearch").addEventListener("click",clearInventorySearch);
  $("codeSearch").addEventListener("keydown",event=>{if(event.key==="Escape"&&$("codeSearch").value){event.preventDefault();clearInventorySearch();}});
  function render() {
    $("snapshotTime").textContent=`${data.clinic_name} · Observado em ${date(data.generated_at)}`;
    $("fingerprint").textContent=`Mapa ${data.inventory.fingerprint}`;
    $("revision").textContent=data.inventory.revision?`Deploy ${data.inventory.revision}`:`Código ${data.inventory.fingerprint}`;
    renderInventory();
    $("aiConfiguration").textContent=data.ai.configured?`${data.ai.model} · Configuração: ${data.ai.source}`:"Chave OpenAI não configurada. Configure em Configurações da clínica ou da Vielle.";
    $("analyze").disabled=analyzing||!data.ai.configured||data.history_status?.state==="unavailable";
    $("exportReport").disabled=false;
    graph();health();renderHistory();drawIcons();
  }
  async function load() {
    if(analyzing)return;
    const id=++requestId;controller?.abort();controller=new AbortController();$("refresh").disabled=true;$("error").hidden=true;
    try{const result=await api(`/api/master/brain?clinic=${encodeURIComponent($("clinic").value)}`,null,controller.signal);if(id!==requestId)return;data=result;render();}
    catch(error){if(error.name!=="AbortError"&&id===requestId){$("error").textContent=error.message;$("error").hidden=false;$("snapshotTime").textContent=data?`Não atualizado · última leitura: ${date(data.generated_at)}`:"Diagnóstico indisponível";}}
    finally{if(id===requestId)$("refresh").disabled=false;}
  }
  $("refresh").addEventListener("click",load);
  $("clinic").addEventListener("change",()=>{data=null;selectedAnalysisId=null;renderInventory();$("revision").textContent="";$("analysis").replaceChildren();$("historyNotice").hidden=true;$("historyToolbar").hidden=true;$("analysisComparison").hidden=true;$("exportReport").disabled=true;$("lanes").innerHTML='<p class="muted">Carregando...</p>';$("wires").replaceChildren();$("nodeDetail").replaceChildren();$("integrations").replaceChildren();$("datasets").replaceChildren();$("evidence").replaceChildren();$("analyze").disabled=true;$("aiStatus").textContent="";history.replaceState(null,"",`?clinic=${encodeURIComponent($("clinic").value)}`);load();});
  $("analyze").addEventListener("click",async()=>{
    if(!data||analyzing)return;analyzing=true;++requestId;controller?.abort();$("refresh").disabled=true;$("analyze").disabled=true;$("clinic").disabled=true;$("focus").disabled=true;$("aiStatus").classList.remove("errorText");$("aiStatus").textContent="Analisando os registros técnicos. Isso pode levar até um minuto...";
    const clinic=data.clinic;
    try{const result=await api(`/api/master/brain/analyze?clinic=${encodeURIComponent(clinic)}`,{focus:$("focus").value});data.analyses=[result.analysis,...savedAnalyses().filter(item=>item.id!==result.analysis.id)].slice(0,20);data.analysis=result.analysis;selectedAnalysisId=String(result.analysis.id);renderHistory();$("aiStatus").textContent="Análise concluída e salva. Nenhuma alteração foi executada no sistema.";}
    catch(error){$("aiStatus").textContent=error.message;$("aiStatus").classList.add("errorText");}
    finally{analyzing=false;$("analyze").disabled=!data?.ai.configured||data?.history_status?.state==="unavailable";$("clinic").disabled=false;$("focus").disabled=false;$("refresh").disabled=false;}
  });
  setTab("map");drawIcons();load();
  setInterval(()=>{if(!document.hidden&&!analyzing)load();},60000);
})();
