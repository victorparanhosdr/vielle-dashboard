"use strict";
window.createCrmQualitySelf = function ({state,api,esc,icon,icons,modal,error}) {
  let root=null,page=1,month="",status="",pageSeq=0,dialogSeq=0;
  const labels={awaiting_ack:"Aguardando aceite",accepted:"Aceita",contested:"Em análise pelo líder",resolved:"Ressalva respondida"};
  const when=value=>value?new Date(value*1000).toLocaleString("pt-BR",{dateStyle:"short",timeStyle:"short",timeZone:"America/Sao_Paulo"}):"—";
  const grade=value=>value===null?"Não avaliável":`${value}/100`;
  const average=value=>value===null?"—":Number(value).toLocaleString("pt-BR",{minimumFractionDigits:1,maximumFractionDigits:1});
  const identity=()=>`${state.session?.user.id}:${state.doctor}`;
  const active=(key,seq)=>root && key===identity() && seq===pageSeq && state.mode==="quality";
  const badge=r=>`<span class="qualityResponseState ${esc(r.status)}">${esc(labels[r.status] || "Não publicada")}</span>`;
  function unmount(){root=null;pageSeq++;dialogSeq++;}
  async function mount(target){root=target;page=1;month="";status="";await refresh();}
  async function refresh(){
    if(!root)return;
    const key=identity(),seq=++pageSeq,doc=state.doctor;
    root.innerHTML='<p class="emptyState" role="status">Carregando suas avaliações…</p>';
    try{
      const data=await api("quality/self",undefined,{doctor:doc,month,status,page});
      if(!active(key,seq))return;
      month=data.month;page=data.page;
      root.innerHTML=`<section class="qualityWorkspace qualitySelf" aria-label="Minhas avaliações"><header class="qualityHeading"><div><h2>Notas e feedbacks</h2><span>${esc(state.data.doctors.find(d=>d.id===doc)?.name || "")} · notas revisadas pelo líder</span></div><button class="icon-button" data-self-refresh title="Atualizar avaliações" aria-label="Atualizar avaliações">${icon("refresh-cw")}</button></header>
        <div class="qualitySelfFilters"><label>Mês de recebimento<input type="month" data-self-month value="${esc(month)}" min="2000-01" max="2099-12"></label><label>Situação<select data-self-status><option value="">Todas</option>${Object.entries(labels).map(([key,label])=>`<option value="${key}" ${status===key?"selected":""}>${label}</option>`).join("")}</select></label></div>
        <dl class="qualityMetrics"><div><dt title="Todas as notas numéricas do filtro, incluindo as em ressalva até a decisão do líder">Média do filtro</dt><dd>${average(data.summary.average)}<small>${data.summary.scored} ${data.summary.scored===1?"nota numérica":"notas numéricas"}</small></dd></div><div><dt>Avaliações recebidas</dt><dd>${data.total}</dd></div><div><dt>Aguardando seu aceite</dt><dd>${data.summary.pending}</dd></div><div><dt>Ressalvas em análise</dt><dd>${data.summary.contested}</dd></div></dl>
        <div class="qualityTableWrap"><table class="qualityTable qualitySelfTable"><thead><tr><th>Atendimento avaliado</th><th>Recebida em</th><th>Situação</th><th>Nota atual</th><th><span class="visuallyHidden">Ações</span></th></tr></thead><tbody>${data.items.map(a=>`<tr><td><strong>${esc(a.contact_name || "Atendimento")}</strong><small>${esc(a.channel_name)} · ${esc(when(a.period.from))}</small></td><td>${esc(when(a.received_at))}</td><td>${badge(a.response)}</td><td><span class="qualityScore ${a.score===null?"unknown":a.score<=50?"low":a.score<80?"medium":"high"}">${grade(a.score)}</span></td><td><button class="icon-button" data-self-item="${esc(a.id)}" title="Ver avaliação" aria-label="Ver avaliação de ${esc(a.contact_name || when(a.period.from))}">${icon("arrow-up-right")}</button></td></tr>`).join("") || '<tr><td colspan="5" class="qualityEmpty">Nenhuma avaliação recebida neste filtro.</td></tr>'}</tbody></table></div>
        <footer class="qualityPagination"><span>${data.total} ${data.total===1?"avaliação":"avaliações"} · página ${page} de ${data.pages}</span><div class="commands"><button class="icon-button" data-self-prev ${page<=1?"disabled":""} title="Página anterior" aria-label="Página anterior">${icon("chevron-left")}</button><button class="icon-button" data-self-next ${page>=data.pages?"disabled":""} title="Próxima página" aria-label="Próxima página">${icon("chevron-right")}</button></div></footer></section>`;
      root.querySelector("[data-self-refresh]").onclick=refresh;
      root.querySelector("[data-self-month]").onchange=event=>{month=event.target.value;page=1;refresh();};
      root.querySelector("[data-self-status]").onchange=event=>{status=event.target.value;page=1;refresh();};
      root.querySelector("[data-self-prev]").onclick=()=>{page--;refresh();};
      root.querySelector("[data-self-next]").onclick=()=>{page++;refresh();};
      root.querySelectorAll("[data-self-item]").forEach(button=>button.onclick=()=>open(doc,button.dataset.selfItem));icons();
    }catch(e){if(active(key,seq))root.innerHTML=`<p class="emptyState" role="alert">${esc(e.message)}</p>`;}
  }
  async function open(doc,id){
    const token=++dialogSeq,key=identity();
    modal("Minha avaliação",'<div id="qualitySelfDetail" class="qualityDetail"><p role="status">Carregando avaliação…</p></div>');
    const live=()=>key===identity() && token===dialogSeq && root && state.mode==="quality" && document.getElementById("qualitySelfDetail");
    async function load(success=""){
      try{
        const {assessment:a}=await api("quality/self/item",undefined,{doctor:doc,id});
        if(!live())return;
        const host=document.getElementById("qualitySelfDetail"),r=a.response;
        host.innerHTML=`${success?`<p class="qualitySuccess" role="status">${esc(success)}</p>`:""}<div class="qualityDetailHeading">${badge(r)}<span class="qualityScore">${grade(a.score)}</span></div><h3>${esc(a.contact_name || "Atendimento")}</h3><p class="qualityScope">${esc(a.channel_name)} · Recebida em ${esc(when(a.received_at))}<br>Atendimento: ${esc(when(a.period.from))} a ${esc(when(a.period.to))}</p>
          <div class="qualityReviewNote"><strong>Parecer do líder</strong><p>${esc(a.reviews.at(-1)?.reason || "Avaliação revisada")}</p></div><p>${esc(a.result.summary)}</p><div class="qualityFeedback"><strong>Orientação da análise revisada</strong><p>${esc(a.result.feedback)}</p></div>
          <details class="qualityOwnCriteria"><summary>Critérios da avaliação</summary>${a.result.items.map(i=>`<details class="qualityCriterion ${esc(i.outcome)}"><summary><span>${icon(i.outcome==="fail"?"circle-alert":i.outcome==="pass"?"circle-check":"circle-minus")}${esc(i.label)}</span><strong>${i.outcome==="fail"?`−${i.penalty}`:i.outcome==="pass"?"Sem desvio":"Não avaliável"}</strong></summary><p>${esc(i.reason)}</p></details>`).join("")}</details>
          ${r.contest_used?`<div class="qualityAppeal"><strong>Sua ressalva · ${esc(when(r.contested_at))}</strong><p>${esc(r.contest_reason)}</p>${r.decision?`<strong>${r.decision==="accept"?"Ressalva acolhida":"Avaliação mantida"} · ${esc(when(r.decided_at))}</strong><p>${esc(r.decision_reason)}</p>`:'<span class="qualityScope">Aguardando resposta do líder.</span>'}</div>`:""}
          ${r.can_accept?`<div class="qualityOwnActions"><button class="primary" data-self-accept>${icon("check-check")} ${r.contest_used?"Dar ciência da resposta":"Aceitar avaliação"}</button></div>`:""}
          ${r.can_contest?`<details class="qualityContestForm"><summary>Enviar uma ressalva</summary><form><label>Motivo da contestação<textarea name="reason" rows="4" required maxlength="2000" placeholder="Explique o ponto que o líder deve reavaliar"></textarea></label><p class="qualityScope">Uma única ressalva por avaliação. Após enviar, aguarde a decisão do líder.</p><button type="submit">${icon("message-square-warning")} Enviar para análise</button></form></details>`:""}
          ${r.status==="accepted"?`<p class="qualitySuccess">${icon("check-check")} Aceite registrado em ${esc(when(r.accepted_at))}</p>`:""}
          <details class="qualityReviewHistory"><summary>Histórico de notas e respostas</summary>${a.reviews.map(h=>`<p>${esc(when(h.created_at))} · ${grade(h.score)}<br>${esc(h.reason)}</p>`).join("")}${a.events.map(h=>`<p>${esc(when(h.created_at))} · ${esc({accepted:"Aceite registrado",contested:"Ressalva enviada",resolved:"Líder respondeu à ressalva"}[h.kind] || h.kind)}${h.reason?`<br>${esc(h.reason)}`:""}</p>`).join("")}</details>`;
        async function send(action,reason=""){
          const buttons=host.querySelectorAll("button");buttons.forEach(b=>b.disabled=true);
          try{
            await api("quality/self/respond",{doctor_id:doc,id,revision:a.revision,response_revision:r.revision,action,reason},{doctor:doc});
            if(!live())return;
            await load(action==="accept"?"Aceite registrado.":"Ressalva enviada ao líder.");refresh();
          }catch(e){if(live()){error(e.message);buttons.forEach(b=>b.disabled=false);}}
        }
        host.querySelector("[data-self-accept]")?.addEventListener("click",()=>send("accept"));
        const form=host.querySelector(".qualityContestForm form");
        if(form)form.onsubmit=event=>{event.preventDefault();send("contest",form.querySelector("textarea").value);};
        icons();
      }catch(e){if(live())error(e.message);}
    }
    await load();
  }
  return {mount,unmount};
};
