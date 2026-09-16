"use strict";
(() => {
  const $ = id => document.getElementById(id);
  const params = new URLSearchParams(location.search);
  const clinic = params.get("clinic");
  const sources = {manual: "Registro manual", inbody: "Bioimpedância", handymet: "Calorimetria"};
  const primary = ["weight_kg", "height_cm", "muscle_kg", "fat_free_kg", "fat_kg", "fat_pct", "waist_cm", "hip_cm"];
  let permissions = [], user = {}, catalog = {}, detail = null, selectedId = "", chartMetric = "weight_kg";
  let formRecord = null, pendingPdf = null, previewUrl = "", importSequence = 0, listSequence = 0, searchSequence = 0, patientSequence = 0;
  let lastFocus = null, saveBusy = false;
  const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const number = value => value == null ? "—" : Number(value).toLocaleString("pt-BR", {maximumFractionDigits: 2});
  const date = (value, time = false) => value ? new Date(value).toLocaleString("pt-BR", time ? {dateStyle:"short", timeStyle:"short"} : {dateStyle:"short"}) : "—";
  const groupKey = record => JSON.stringify([record.source, record.method]);
  const can = action => permissions.includes(`body_evolution.${action}`);
  const icons = () => window.lucide?.createIcons();
  const unit = key => catalog[key]?.[1] || "";
  const measure = (value, key) => value == null ? "Não informado" : `${number(value)} ${unit(key)}`.trim();
  const selected = () => detail?.evaluations.find(item => item.id === selectedId);

  function notify(message = "", error = false) {
    $("notice").textContent = message;
    $("notice").classList.toggle("error", error);
  }
  function showDialog(id) { lastFocus = document.activeElement; $(id).showModal(); icons(); }
  function closeDialog(id) {
    if (id === "assessmentDialog" && saveBusy) return;
    $(id).close();
    if (id === "assessmentDialog") { importSequence++; releasePreview(); pendingPdf = null; }
    lastFocus?.focus();
  }
  function releasePreview() { if (previewUrl) URL.revokeObjectURL(previewUrl); previewUrl = ""; }

  async function api(action, {query = {}, body, ...init} = {}) {
    const q = new URLSearchParams({clinic, ...query});
    const response = await fetch(`/api/body/${action}?${q}`, {credentials:"same-origin", ...init,
      ...(body !== undefined ? {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body)} : {})});
    const value = await response.json();
    if (!response.ok) throw new Error(value.error || "Não foi possível concluir. Tente novamente.");
    return value;
  }

  function applyAccess(data) {
    user = data.user || {};
    permissions = data.permissions?.[clinic] || [];
    $("addPatient").hidden = !can("create");
    $("importExam").hidden = !can("create");
    $("newAssessment").hidden = !can("create");
    $("editSelected").hidden = !can("edit");
    if (!can("view")) {
      detail = null;
      $("patientPage").hidden = true;
      $("patientList").hidden = true;
      document.querySelectorAll("dialog[open]").forEach(dialog => dialog.close());
      notify("Seu usuário não tem acesso à evolução corporal da Inspire. Solicite a liberação ao Master.", true);
    } else if (detail) renderDetail();
  }

  async function loadList() {
    const sequence = ++listSequence;
    const result = await api("patients", {query:{registered:"1", q:$("listSearch").value}});
    if (sequence !== listSequence) return;
    catalog = result.catalog;
    $("listCount").textContent = `${result.patients.length} paciente(s) nesta busca · ${result.synced_patients} cadastros na base sincronizada`;
    $("patientRows").innerHTML = result.patients.map(patient => `<tr><td class="nameCell">${esc(patient.name)}</td><td>${date(patient.last_exam)}</td><td>${patient.evaluations}</td><td><button data-patient="${esc(patient.id)}" aria-label="Abrir ficha de ${esc(patient.name)}">Abrir ficha<i data-lucide="arrow-up-right"></i></button></td></tr>`).join("");
    $("listEmpty").hidden = result.patients.length > 0;
    icons();
  }

  async function openPatient(id, push = true) {
    const sequence = ++patientSequence;
    notify();
    const data = await api("patient", {query:{id}});
    if (sequence !== patientSequence) return;
    detail = data;
    selectedId = data.evaluations.at(-1)?.id || "";
    $("patientList").hidden = true;
    $("patientPage").hidden = false;
    $("patientCrumb").textContent = `/ ${data.patient.display_name}`;
    $("patientName").textContent = data.patient.display_name;
    $("patientMeta").textContent = `${data.evaluations.length} avaliação(ões) · Clínica Inspire`;
    if (push) history.pushState({}, "", `/body-evolution.html?${new URLSearchParams({clinic, patient:id})}`);
    renderDetail();
  }

  function showList(push = true) {
    patientSequence++;
    detail = null;
    $("patientPage").hidden = true;
    $("patientList").hidden = false;
    $("patientCrumb").textContent = "";
    notify();
    if (push) history.pushState({}, "", `/body-evolution.html?clinic=${clinic}`);
    return loadList();
  }

  function renderDetail() {
    if (!detail) return;
    const entries = detail.evaluations;
    $("noAssessments").hidden = !!entries.length;
    $("assessmentView").hidden = !entries.length;
    $("timeline").hidden = !entries.length;
    $("timeline").innerHTML = entries.map(item => `<button data-select="${esc(item.id)}" aria-pressed="${item.id === selectedId}">${date(item.exam_at, true)}<small>${esc(sources[item.source])}</small></button>`).join("");
    if (!entries.length) return;
    const current = selected();
    const first = entries.find(item => groupKey(item) === groupKey(current));
    $("selectedContext").textContent = `${sources[current.source]} · ${current.method} · Exame de ${date(current.exam_at, true)}`;
    $("editSelected").hidden = !can("edit");
    $("firstDate").textContent = date(first.exam_at);
    $("currentDate").textContent = date(current.exam_at);
    $("metrics").innerHTML = ["weight_kg", "fat_pct", "muscle_kg", "waist_cm"].map(key => {
      const value = current.fields[key], base = first.fields[key];
      const diff = value != null && base != null ? value - base : null;
      const delta = diff == null || current.id === first.id ? "Sem comparação anterior" : `${diff > 0 ? "+" : ""}${number(diff)} ${key === "fat_pct" ? "p.p." : unit(key)} desde ${date(first.exam_at)}`;
      return `<dl class="metric"><dt>${esc(catalog[key][0])}</dt><dd>${number(value)}${value == null ? "" : ` <small style="display:inline;font-size:16px;color:inherit">${esc(unit(key))}</small>`}</dd><small>${esc(delta)}</small></dl>`;
    }).join("");
    for (const [prefix, item] of [["first",first],["current",current]]) {
      for (const [suffix,key,label] of [["Waist","waist_cm","Abdômen"],["Hip","hip_cm","Quadril"]]) {
        $(prefix+suffix).innerHTML = `<span>${label}</span><strong>${item.fields[key] == null ? "—" : esc(measure(item.fields[key],key))}</strong>`;
      }
    }
    const groups = [...new Map(entries.map(item => [groupKey(item), `${sources[item.source]} · ${item.method}`])).entries()];
    $("chartSource").innerHTML = groups.map(([key,label]) => `<option value="${esc(key)}">${esc(label)}</option>`).join("");
    $("chartSource").value = groupKey(current);
    renderChart();
    $("metabolicResults").innerHTML = ["inbody", "handymet"].map(source => {
      const item = entries.filter(value => value.source === source && value.exam_at <= current.exam_at).at(-1);
      return `<div><p class="sourceName">${sources[source]}</p><p class="muted">${item ? esc(date(item.exam_at, true)) : "Sem exame até esta data"}</p><strong>${item ? esc(measure(item.fields.bmr_kcal,"bmr_kcal")) : "—"}</strong><p>${source === "inbody" ? "TMB estimada · InBody" : "TMB informada · HandyMet"}</p>${source === "handymet" && item ? `<p class="muted">GET: ${esc(measure(item.fields.tdee_kcal,"tdee_kcal"))}</p><p class="muted">Laudo: TMB estimada a partir de TMR − 10%.</p>` : ""}</div>`;
    }).join("");
    $("allMeasures").innerHTML = Object.entries(current.fields).map(([key,value]) => `<div><dt>${esc(catalog[key]?.[0] || key)}</dt><dd>${esc(measure(value,key))}</dd></div>`).join("");
    $("assessmentNotes").textContent = current.notes || "";
    $("historyRows").innerHTML = [...entries].reverse().map(item => `<tr class="${item.id === selectedId ? "selectedRow" : ""}"><td><button class="quiet" data-select="${esc(item.id)}">${date(item.exam_at,true)}</button></td><td>${esc(sources[item.source])}<br><span class="muted">${esc(item.method)}</span></td><td>${esc(measure(item.fields.weight_kg,"weight_kg"))}</td><td>${esc(measure(item.fields.fat_pct,"fat_pct"))}</td><td>${esc(measure(item.fields.muscle_kg,"muscle_kg"))}</td><td>${esc(item.professional)}</td><td>${item.document_id && can("export") ? `<a class="quiet" title="Baixar PDF original" aria-label="Baixar PDF original" href="${documentUrl(item)}"><i data-lucide="file-down"></i></a>` : "—"}</td><td><div class="rowActions">${can("edit") ? `<button data-edit="${esc(item.id)}" class="iconButton" title="Editar avaliação" aria-label="Editar avaliação"><i data-lucide="pencil"></i></button>` : ""}<button data-revisions="${esc(item.id)}" class="iconButton" title="Ver registro de alterações" aria-label="Ver registro de alterações"><i data-lucide="history"></i></button></div></td></tr>`).join("");
    icons();
  }

  function documentUrl(item) { return `/api/body/document?${new URLSearchParams({clinic,id:item.document_id,patient:detail.patient.id})}`; }

  function renderChart() {
    const cutoff = selected().exam_at;
    const rows = detail.evaluations.filter(item => groupKey(item) === $("chartSource").value && item.exam_at <= cutoff && item.fields[chartMetric] != null);
    $("chartReadout").textContent = "";
    document.querySelectorAll("[data-metric]").forEach(button => button.setAttribute("aria-selected", String(button.dataset.metric === chartMetric)));
    if (!rows.length) { $("chart").innerHTML = '<p class="muted">Sem medidas desta origem até o exame selecionado.</p>'; return; }
    const values = rows.map(row=>row.fields[chartMetric]), times = rows.map(row=>new Date(row.exam_at).getTime());
    const lo = Math.min(...values), hi = Math.max(...values), pad = Math.max((hi-lo)*.2,1);
    const min = Math.max(0,lo-pad), max = hi+pad, left=54, right=620, top=22, bottom=215;
    const point = (value,i) => [times.at(-1)===times[0] ? (left+right)/2 : left+(times[i]-times[0])/(times.at(-1)-times[0])*(right-left), bottom-(value-min)/(max-min)*(bottom-top)];
    const points = values.map(point);
    const grid = Array.from({length:5},(_,i)=>{ const y=top+i*(bottom-top)/4;return `<line x1="${left}" x2="${right}" y1="${y}" y2="${y}" stroke="#e2e9e4"/><text x="44" y="${y+4}" text-anchor="end">${number(max-i*(max-min)/4)}</text>`;}).join("");
    const labels = [...new Set([0,Math.floor((rows.length-1)/2),rows.length-1])].map(i=>`<text x="${points[i][0]}" y="243" text-anchor="${i===0?"start":i===rows.length-1?"end":"middle"}">${date(rows[i].exam_at)}</text>`).join("");
    const circles = points.map(([x,y],i)=>`<circle cx="${x}" cy="${y}" r="5" fill="#1a6352" stroke="white" stroke-width="2" tabindex="0" data-point="${i}" aria-label="${esc(date(rows[i].exam_at,true)+': '+measure(values[i],chartMetric))}"><title>${esc(date(rows[i].exam_at,true)+': '+measure(values[i],chartMetric))}</title></circle>`).join("");
    $("chart").innerHTML = `<svg viewBox="0 0 655 260" role="img" aria-label="${esc(catalog[chartMetric][0])} por data do exame"><text x="${left}" y="12">${esc(unit(chartMetric))}</text>${grid}<polyline points="${points.map(p=>p.join(',')).join(' ')}" fill="none" stroke="#1a6352" stroke-width="2.5"/>${circles}${labels}</svg>`;
    const announce = i => { $("chartReadout").textContent = `${date(rows[i].exam_at,true)} · ${measure(values[i],chartMetric)} · ${rows[i].method}`; };
    $("chart").querySelectorAll("[data-point]").forEach(circle=>{
      circle.addEventListener("mouseenter",()=>announce(Number(circle.dataset.point)));
      circle.addEventListener("focus",()=>announce(Number(circle.dataset.point)));
      circle.addEventListener("click",()=>announce(Number(circle.dataset.point)));
    });
    announce(rows.length-1);
  }

  function makeFields() {
    const input = ([key,[label,unit,min,max]]) => `<label>${esc(label)}${unit ? ` (${esc(unit)})` : ""}<input type="number" step="any" inputmode="decimal" min="${min}" max="${max}" data-field="${key}" aria-label="${esc(label)}"></label>`;
    $("primaryFields").innerHTML = Object.entries(catalog).filter(([key])=>primary.includes(key)).map(input).join("");
    $("secondaryFields").innerHTML = Object.entries(catalog).filter(([key])=>!primary.includes(key)).map(input).join("");
  }
  function fillFields(fields) { document.querySelectorAll("[data-field]").forEach(input=>{input.value = fields[input.dataset.field] ?? "";}); }
  function localNow() { const d=new Date();return new Date(d.getTime()-d.getTimezoneOffset()*60000).toISOString().slice(0,16); }
  function openForm(record=null, importing=false) {
    if (!detail || !(record ? can("edit") : can("create"))) return;
    const form = $("assessmentForm");
    form.reset(); makeFields(); releasePreview(); pendingPdf=null; formRecord=record;
    $("saveAssessment").disabled=false;
    $("formTitle").textContent=record ? "Editar avaliação" : importing ? "Importar exame" : "Nova avaliação";
    $("formPatient").textContent=detail.patient.display_name;
    $("uploadArea").hidden=!importing;
    $("formError").textContent=""; $("importStatus").textContent=""; $("importWarnings").textContent="";
    $("previewOriginal").hidden=true; $("extraFields").open=false;
    $("formSource").value=sources[record?.source||"manual"];
    form.elements.exam_at.value=record?.exam_at || (importing ? "" : localNow());
    form.elements.exam_at.max=localNow();
    form.elements.method.value=record?.method || (importing ? "" : "Medidas manuais");
    form.elements.professional.value=record?.professional || user.nome || "";
    form.elements.notes.value=record?.notes || "";
    fillFields(record?.fields || {});
    $("saveAssessment").disabled=importing;
    showDialog("assessmentDialog");
  }

  async function importFile(file) {
    const sequence=++importSequence;
    pendingPdf=null; fillFields({}); releasePreview(); $("saveAssessment").disabled=true;
    $("importWarnings").textContent=""; $("previewOriginal").hidden=true; $("formError").textContent="";
    $("assessmentForm").elements.exam_at.value="";
    try {
      if (!file || !/\.pdf$/i.test(file.name) || file.size>8*1024*1024) throw new Error("Selecione um PDF de até 8 MB.");
      $("importStatus").textContent="Lendo o exame...";
      const encoded=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result.split(',')[1]);reader.onerror=reject;reader.readAsDataURL(file);});
      const result=await api("import",{body:{pdf:encoded}});
      if (sequence!==importSequence || !$("assessmentDialog").open) return;
      pendingPdf={encoded,exam:result.exam};
      const exam=result.exam, form=$("assessmentForm");
      form.elements.exam_at.value=exam.exam_at;form.elements.method.value=exam.method;
      $("formSource").value=sources[exam.source];fillFields(exam.fields);
      form.elements.confirmed.checked=false;
      $("extraFields").open=exam.source==="handymet";
      $("importStatus").textContent=`${sources[exam.source]} identificado · ${date(exam.exam_at,true)}`;
      $("importWarnings").textContent=`Paciente no PDF: ${exam.patient_name || "Não identificado"}. Ficha selecionada: ${detail.patient.display_name}. ${exam.warnings.join(" ")}`;
      previewUrl=URL.createObjectURL(file);$("previewOriginal").href=previewUrl;$("previewOriginal").hidden=false;
      $("saveAssessment").disabled=false;
    } catch(error) { if(sequence===importSequence){$("importStatus").textContent="";$("formError").textContent=error.message || "Não foi possível ler o arquivo.";} }
  }

  async function save(event) {
    event.preventDefault(); if(saveBusy) return;
    const form=$("assessmentForm"), fields={};
    form.querySelectorAll("[data-field]").forEach(input=>{if(input.value!=="")fields[input.dataset.field]=Number(input.value);});
    const body={patient_id:detail.patient.id,exam_at:form.elements.exam_at.value,method:form.elements.method.value,
      professional:form.elements.professional.value,notes:form.elements.notes.value,confirmed:form.elements.confirmed.checked,
      source:formRecord?.source || pendingPdf?.exam.source || "manual",fields};
    if(formRecord){body.id=formRecord.id;body.version=formRecord.version;}
    if(pendingPdf)body.pdf=pendingPdf.encoded;
    saveBusy=true;$("saveAssessment").disabled=true;$("formError").textContent="";
    let saved = false;
    try {
      const result=await api("evaluation",{body});
      saved=true;saveBusy=false;closeDialog("assessmentDialog");
      await openPatient(body.patient_id,false);selectedId=result.id;renderDetail();
      notify("Avaliação salva. Histórico e arquivo original preservados.");
    } catch(error){
      if(saved) notify("A avaliação foi salva, mas a ficha não pôde ser atualizada. Recarregue a página; não é necessário salvar novamente.",true);
      else $("formError").textContent=error.message;
    }
    finally{saveBusy=false;$("saveAssessment").disabled=false;}
  }

  async function searchExperts() {
    const sequence=++searchSequence, q=$("expertsSearch").value.trim();
    $("searchResults").innerHTML="";
    if(q.length<2){$("searchStatus").textContent="Digite ao menos 2 caracteres.";return;}
    $("searchStatus").textContent="Buscando na base sincronizada...";
    try {
      const data=await api("patients",{query:{q}});
      if(sequence!==searchSequence)return;
      $("searchStatus").textContent=data.patients.length ? `${data.patients.length} resultado(s) · Selecione o cadastro correto.` : "Nenhum cadastro encontrado. Confira o nome ou peça ao Master para atualizar a base do Clínica Experts.";
      $("searchResults").innerHTML=data.patients.map(patient=>`<button data-enroll="${esc(patient.uuid)}" ${patient.enrolled_id?`data-existing="${esc(patient.enrolled_id)}"`:""}><span>${esc(patient.name)}<small>${esc(patient.phone || "Sem telefone")}</small></span><span>${patient.enrolled_id?"Abrir ficha":"Selecionar"}</span></button>`).join("");
    }catch(error){if(sequence===searchSequence)$("searchStatus").textContent=error.message;}
  }

  async function revisions(id) {
    $("revisionContent").textContent="Carregando...";showDialog("revisionDialog");
    try {
      const data=await api("revisions",{query:{patient:detail.patient.id,id}});
      $("revisionContent").innerHTML=data.revisions.map(item=>{
        const previous=item.before ? JSON.parse(item.before.fields_json) : {};
        const changes=Object.keys(catalog).filter(key=>previous[key]!==item.after.fields[key]).map(key=>`<li>${esc(catalog[key][0])}: ${esc(measure(previous[key],key))} → ${esc(measure(item.after.fields[key],key))}</li>`);
        for(const key of ["exam_at","method","professional","notes"]){if(item.before && item.before[key]!==item.after[key])changes.push(`<li>${esc({exam_at:"Data do exame",method:"Método",professional:"Profissional",notes:"Observações"}[key])}: ${esc(item.before[key])} → ${esc(item.after[key])}</li>`);}
        return `<article><strong>${item.action==="create"?"Registro criado":"Registro corrigido"}</strong><p>${esc(item.actor_name)} · ${date(item.recorded_at,true)}</p><ul>${changes.join("")}</ul></article>`;
      }).join("") || "Nenhum registro.";
    } catch(error){$("revisionContent").textContent=error.message;}
  }

  function debounce(fn,ms=250){let timer;return()=>{clearTimeout(timer);timer=setTimeout(fn,ms);};}
  const run=fn=>Promise.resolve().then(fn).catch(error=>notify(error.message,true));
  document.addEventListener("click",event=>{
    const close=event.target.closest("[data-close]");if(close){closeDialog(close.dataset.close);return;}
    const patient=event.target.closest("[data-patient]");if(patient){run(()=>openPatient(patient.dataset.patient));return;}
    const select=event.target.closest("[data-select]");if(select){selectedId=select.dataset.select;renderDetail();return;}
    const metric=event.target.closest("[data-metric]");if(metric){chartMetric=metric.dataset.metric;renderChart();return;}
    const edit=event.target.closest("[data-edit]");if(edit){openForm(detail.evaluations.find(item=>item.id===edit.dataset.edit));return;}
    const rev=event.target.closest("[data-revisions]");if(rev){revisions(rev.dataset.revisions);return;}
    const enroll=event.target.closest("[data-enroll]");if(enroll){run(async()=>{enroll.disabled=true;try{const id=enroll.dataset.existing || (await api("enroll",{body:{experts_uuid:enroll.dataset.enroll}})).id;closeDialog("patientDialog");await openPatient(id);}finally{enroll.disabled=false;}});}
  });
  $("addPatient").addEventListener("click",()=>{$("expertsSearch").value="";$("searchResults").innerHTML="";$("searchStatus").textContent="Digite ao menos 2 caracteres.";showDialog("patientDialog");$("expertsSearch").focus();});
  $("expertsSearch").addEventListener("input",debounce(searchExperts));
  $("listSearch").addEventListener("input",debounce(()=>run(loadList)));
  $("refreshList").addEventListener("click",()=>run(loadList));
  $("backList").addEventListener("click",()=>run(()=>showList()));
  $("newAssessment").addEventListener("click",()=>openForm());
  $("importExam").addEventListener("click",()=>openForm(null,true));
  $("editSelected").addEventListener("click",()=>openForm(selected()));
  $("examFile").addEventListener("change",event=>importFile(event.target.files[0]));
  $("assessmentForm").addEventListener("submit",save);
  $("assessmentDialog").addEventListener("cancel",event=>{if(saveBusy)event.preventDefault();});
  $("assessmentDialog").addEventListener("close",()=>{importSequence++;releasePreview();pendingPdf=null;});
  $("chartSource").addEventListener("change",renderChart);
  $("copyLink").addEventListener("click",()=>run(async()=>{await navigator.clipboard.writeText(location.origin+`/body-evolution.html?${new URLSearchParams({clinic,patient:detail.patient.id})}`);notify("Link da ficha copiado. O acesso continua protegido por login e permissão.");}));
  window.addEventListener("popstate",()=>{const id=new URLSearchParams(location.search).get("patient");run(()=>id?openPatient(id,false):showList(false));});
  window.addEventListener("doc4docs-session",event=>applyAccess(event.detail));
  window.addEventListener("doc4docs-permission-denied",()=>run(async()=>{const r=await fetch('/api/auth/me');if(r.ok)applyAccess(await r.json());}));
  window.addEventListener("doc4docs-clinic-forbidden",()=>applyAccess({}));
  run(async()=>{
    icons();
    if(clinic!=="inspire")throw new Error("Evolução corporal disponível apenas na Inspire.");
    const response=await fetch("/api/auth/me");if(!response.ok)throw new Error("Entre para continuar.");
    applyAccess(await response.json());if(!can("view"))return;
    await loadList();const id=params.get("patient");if(id)await openPatient(id,false);
  });
})();
