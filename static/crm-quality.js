"use strict";
window.createCrmQuality = function ({state,api,esc,icon,icons,modal,error}) {
  let root=null,page=1,pageSeq=0,dialogSeq=0,timer=0,view="recent";
  const self=window.createCrmQualitySelf?.({state,api,esc,icon,icons,modal,error});
  const responseLabels={awaiting_ack:"Aguardando aceite",accepted:"Aceita pelo atendente",contested:"Ressalva em análise",resolved:"Ressalva respondida"};
  const responseBadge=r=>r && r.status!=="unpublished"?`<small class="qualityResponseState ${esc(r.status)}">${esc(responseLabels[r.status] || "")}</small>`:"";
  const labels={queued:"Na fila",running:"Analisando",done:"Análise pronta",failed:"Não avaliada",cancelled:"Cancelada"};
  const when=value=>new Date(value*1000).toLocaleString("pt-BR",{dateStyle:"short",timeStyle:"short"});
  const badge=(value,reviewed)=>`<span class="qualityScore ${value===null?"unknown":value<=50?"low":value<80?"medium":"high"}">${value===null?"Não avaliável":`${value}/100`}</span><small>${reviewed?"Revisada pelo gestor":"Provisória · IA"}</small>`;
  const average=value=>value===null || value===undefined?"—":Number(value).toLocaleString("pt-BR",{minimumFractionDigits:1,maximumFractionDigits:1});
  function jobRows(jobs){
    return jobs.map(j=>`<tr><td><strong>${esc(j.name)}</strong><small>${esc(j.channel_name)}</small></td><td>${esc(when(j.created_at))}</td><td><span class="qualityJobState">${labels[j.status] || esc(j.status)}</span>${j.error?`<small class="qualityError">${esc(j.error)}</small>`:""}</td><td>${j.assessments.map(a=>`<div class="qualityMiniScore"><strong>${esc(a.actor_name)}</strong><span>${badge(a.score,a.reviewed)}</span>${responseBadge(a.response)}</div>`).join("") || "—"}</td><td><button type="button" class="icon-button" data-quality-job="${esc(j.id)}" title="Abrir avaliação" aria-label="Abrir avaliação de ${esc(j.name)}">${icon("arrow-up-right")}</button></td></tr>`).join("") || '<tr><td colspan="5" class="qualityEmpty">Nenhuma avaliação neste período.</td></tr>';
  }
  const mounted=(seq,doc)=>root && seq===pageSeq && state.mode==="quality" && state.doctor===doc;
  async function mount(target){
    if(state.data.can_manage===false){unmount();await self?.mount(target);return;}
    self?.unmount();root=target;page=1;view="recent";await refresh();
  }
  function unmount(){root=null;pageSeq++;dialogSeq++;clearTimeout(timer);self?.unmount();}
  async function refresh(){
    if(!root)return;
    const seq=++pageSeq,doc=state.doctor;
    root.innerHTML='<p class="emptyState" role="status">Carregando qualidade do atendimento…</p>';
    try{
      const data=await api("quality",undefined,{doctor:doc,page,view});if(!mounted(seq,doc))return;
      const s=data.summary;
      root.innerHTML=`<section class="qualityWorkspace"><header class="qualityHeading"><div><h2>Monitoria da equipe</h2><span>Últimos 30 dias · ${esc(state.data.doctors.find(d=>d.id===doc)?.name || "")}</span></div><div class="commands"><button type="button" data-quality-settings>${icon("sliders-horizontal")} Régua e IA</button><button type="button" class="icon-button" data-quality-refresh title="Atualizar avaliações" aria-label="Atualizar avaliações">${icon("refresh-cw")}</button></div></header>
        <dl class="qualityMetrics"><div><dt>Média revisada</dt><dd>${average(s.average)}<small>Somente notas revisadas</small></dd></div><div><dt>Aguardando revisão</dt><dd>${Math.max(0,(s.count || 0)-(s.reviewed || 0))}</dd></div><div><dt>Notas revisadas</dt><dd>${s.reviewed || 0}</dd></div><div><dt>Análises de hoje</dt><dd>${data.daily_used || 0}<small>de ${data.settings.daily_limit || 20} por profissional</small></dd></div></dl>
        <p class="${data.settings.enabled?"qualityScope":"inlineWarning"}">${data.settings.enabled?"Piloto manual ativo · revisão do gestor antes dos indicadores":"Análise por IA desativada. Nenhuma conversa é enviada até a autorização na régua."}</p>
        ${data.provider_ready===false?'<p class="inlineWarning">Configure a API OpenAI da clínica para executar as análises manuais.</p>':""}\n        <div class="qualityTableWrap"><table class="qualityTable"><thead><tr><th>Conversa</th><th>Data da análise</th><th>Situação</th><th>Atendente e nota</th><th><span class="visuallyHidden">Ações</span></th></tr></thead><tbody>${jobRows(data.jobs)}</tbody></table></div><footer class="qualityPagination"><span>${data.total} ${data.total===1?"análise":"análises"} · página ${page} de ${data.pages}</span><div class="commands"><button class="icon-button" data-quality-prev ${page<=1?"disabled":""} title="Página anterior" aria-label="Página anterior">${icon("chevron-left")}</button><button class="icon-button" data-quality-next ${page>=data.pages?"disabled":""} title="Próxima página" aria-label="Próxima página">${icon("chevron-right")}</button></div></footer></section>`;
      root.querySelector("[data-quality-settings]").onclick=()=>configure(doc,data.settings,data.rubric,refresh);
      root.querySelector('.qualityHeading').insertAdjacentHTML('afterend',`<label class="qualityManagerFilter">Exibir<select data-quality-list-view><option value="recent">Todas · últimos 30 dias</option><option value="contested" ${view==='contested'?'selected':''}>Ressalvas pendentes · qualquer data</option></select></label>`);
      root.querySelector('[data-quality-list-view]').onchange=event=>{view=event.target.value;page=1;refresh();};
      if(view==='contested')root.querySelector('.qualityHeading span').textContent=`Ressalvas pendentes · ${state.data.doctors.find(d=>d.id===doc)?.name || ''}`;
      root.querySelector("[data-quality-refresh]").onclick=refresh;
      root.querySelector("[data-quality-prev]").onclick=()=>{page--;refresh();};
      root.querySelector("[data-quality-next]").onclick=()=>{page++;refresh();};
      root.querySelectorAll("[data-quality-job]").forEach(b=>b.onclick=()=>openJob(doc,b.dataset.qualityJob));icons();
    }catch(e){if(mounted(seq,doc))root.innerHTML=`<p class="emptyState" role="alert">${esc(e.message)}</p>`;}
  }
  function configure(doc,settings,rubric,onDone){
    const token=++dialogSeq;clearTimeout(timer);
    modal("Régua de qualidade e IA",`<form id="qualitySettings" class="qualitySettings"><label class="check"><input name="enabled" type="checkbox" ${settings.enabled?"checked":""}>Habilitar análises manuais por IA</label><label>Limite diário por profissional (São Paulo)<input type="number" name="daily_limit" min="1" max="20" value="${settings.daily_limit}" required></label><p class="qualityScope">Piloto manual · sem análise automática, tempo de resposta ou limite de interações. Valores, descontos e isenções não compõem a nota.</p><label>Padrão de comunicação da clínica<textarea name="scripts" rows="4" maxlength="4000" placeholder="Saudação, linguagem e roteiro esperado">${esc(settings.scripts)}</textarea></label><label>Referência de serviços e procedimentos<textarea name="services" rows="4" maxlength="4000" placeholder="Informações aprovadas pela clínica, sem dados de pacientes">${esc(settings.services)}</textarea></label><details class="qualityRubric"><summary>Critérios e descontos</summary>${[100,50,30].map(p=>`<h3>${p===100?"Gravíssimas":p===50?"Graves":"Moderadas"} · −${p} pontos</h3><ul>${rubric.filter(r=>r.penalty===p).map(r=>`<li><strong>${esc(r.label)}</strong><span>${esc(r.description)}</span>${!r.enabled?`<small>${esc(r.disabled_reason)}</small>`:""}</li>`).join("")}</ul>`).join("")}</details><p class="inlineWarning">Cada análise depende da seleção e confirmação do gestor. Os textos serão processados pela API OpenAI da clínica, com consumo de saldo. Nomes e telefones da ficha não são enviados; o texto livre ainda pode conter dados pessoais. Áudios e anexos não são analisados.</p><label class="check"><input type="checkbox" name="confirm">Confirmo a autorização de processamento e o consumo da API nas análises que eu solicitar</label><div class="formActions"><button type="submit" class="primary">${icon("check")} Salvar régua</button></div></form>`);
    const form=document.getElementById("qualitySettings");
    form.querySelector('.inlineWarning').textContent="Cada análise exige seleção e confirmação do gestor. Textos e, quando selecionados, áudios serão processados pela OpenAI com consumo da API. Nomes e telefones da ficha não são enviados como cadastro, mas o conteúdo pode conter dados pessoais. Documentos, imagens e vídeos continuam fora da análise.";
    form.onsubmit=async event=>{
      event.preventDefault();const button=form.querySelector('[type="submit"]');button.disabled=true;
      const values=new FormData(form);
      try{
        await api("quality/settings",{doctor_id:doc,revision:settings.revision,enabled:values.has("enabled"),auto_closed:false,selection_enabled:false,daily_limit:Number(values.get("daily_limit")),max_interactions:0,scripts:values.get("scripts"),services:values.get("services"),confirm:values.has("confirm")},{doctor:doc});
        if(token!==dialogSeq || !form.isConnected || state.doctor!==doc)return;
        document.getElementById("crmDialog").close();onDone?.();
      }catch(e){if(token===dialogSeq && form.isConnected)error(e.message);}finally{if(form.isConnected)button.disabled=false;}
    };icons();
  }
  async function openConversation(context){
    const doc=context.doctor_id,token=++dialogSeq;clearTimeout(timer);
    const active=()=>token===dialogSeq && (!context.active || context.active());
    modal("Avaliação do atendimento",'<div id="crmQualityDetail" class="qualityDetail"><p role="status">Carregando avaliações…</p></div>');
    try{
      const data=await api("quality",undefined,{doctor:doc,channel:context.channel_id,contact:context.contact_id});
      if(!active() || !document.getElementById("crmQualityDetail"))return;
      const host=document.getElementById("crmQualityDetail");
      host.innerHTML=`<p class="qualityScope">${esc(context.name || "Conversa selecionada")} · textos dos últimos 7 dias, até 250 mensagens</p><p>Notas da IA são provisórias. Critérios sem evidência ficam sem avaliação; áudios e anexos não geram descontos por ausência de contexto.</p>${!data.settings.enabled?'<p class="inlineWarning">A análise por IA está desativada para este profissional.</p>':""}<div class="commands"><button class="primary" data-quality-analyze ${data.settings.enabled && data.provider_ready!==false?"":"disabled"}>${icon("sparkles")} Analisar atendimento</button><button data-quality-configure>${icon("sliders-horizontal")} Régua e IA</button></div><label class="check qualityConsent"><input type="checkbox" data-quality-consent>Confirmo a análise dos textos pela OpenAI e o consumo da API da clínica</label><div class="qualityConversationHistory">${data.jobs.map(j=>`<button type="button" data-quality-job="${esc(j.id)}"><span>${esc(when(j.created_at))}</span><strong>${labels[j.status] || esc(j.status)}</strong>${icon("chevron-right")}</button>`).join("") || '<p>Nenhuma avaliação registrada nesta conversa.</p>'}</div>`;
      host.querySelector("[data-quality-configure]").onclick=()=>configure(doc,data.settings,data.rubric,()=>openConversation(context));
      const consent=host.querySelector('.qualityConsent');
      consent.insertAdjacentHTML('beforebegin','<label class="check qualityConsent"><input type="checkbox" data-quality-audio>Transcrever os áudios desta análise pela OpenAI (consumo adicional)</label>');
      consent.lastChild.textContent="Confirmo o processamento dos textos e dos áudios selecionados pela OpenAI, com consumo da API da clínica";
      host.querySelectorAll("[data-quality-job]").forEach(b=>b.onclick=()=>openJob(doc,b.dataset.qualityJob,context));
      host.querySelector("[data-quality-analyze]").onclick=async event=>{
        if(!host.querySelector("[data-quality-consent]").checked){error("Confirme o processamento pela IA antes de analisar.");return;}
        const button=event.currentTarget;button.disabled=true;
        try{
          const includeAudio=host.querySelector('[data-quality-audio]').checked;
          const result=await api("quality/analyze",{doctor_id:doc,channel_id:context.channel_id,contact_id:context.contact_id,confirm:true,include_audio:includeAudio,confirm_audio:includeAudio},{doctor:doc});
          if(active() && host.isConnected)openJob(doc,result.id,context);
        }catch(e){if(active() && host.isConnected){error(e.message);button.disabled=false;}}
      };icons();
    }catch(e){if(active())error(e.message);}
  }
  async function openJob(doc,id,context){
    const token=++dialogSeq;clearTimeout(timer);
    const retryNonce=crypto.randomUUID();
    modal("Avaliação do atendimento",'<div id="crmQualityDetail" class="qualityDetail"><p role="status">Carregando avaliação…</p></div>');
    async function poll(){
      if(token!==dialogSeq || !document.getElementById("crmQualityDetail") || context?.active && !context.active())return;
      try{
        const {job}=await api("quality/item",undefined,{doctor:doc,id});
        if(token!==dialogSeq || !document.getElementById("crmQualityDetail") || context?.active && !context.active())return;
        const host=document.getElementById("crmQualityDetail"),messageById=new Map(job.messages.map(m=>[m.id,m]));
        host.innerHTML=`<div class="qualityDetailHeading"><span class="qualityJobState">${labels[job.status] || esc(job.status)}</span><small>${esc(when(job.created_at))}</small></div><p class="qualityScope">Recorte: ${esc(when(job.period.from))} a ${esc(when(job.period.to))}</p>${job.partial?'<p class="inlineWarning">Contexto parcial: há texto limitado, autoria desconhecida ou mídia não analisada. Revise antes de usar a nota.</p>':""}${job.error?`<p class="qualityError" role="alert">${esc(job.error)}</p>`:""}${["queued","running"].includes(job.status)?'<p role="status">A análise está em processamento. Você pode fechar esta janela.</p>':""}${job.assessments.map(a=>`<section class="qualityAssessment" data-assessment="${esc(a.id)}"><header><div><h3>${esc(a.actor_name)}</h3><span>${a.result.items.filter(i=>i.outcome!=="na").length} de ${job.rubric.length} critérios avaliados · Seleção manual</span></div><div class="qualityScoreBlock">${badge(a.score,a.reviewed)}</div></header><p><small class="qualitySource">Resumo da IA</small>${esc(a.result.summary)}</p>${a.reviewed && a.history.length?`<div class="qualityReviewNote"><strong>Revisão do gestor</strong><p>${esc(a.history[a.history.length-1].reason)}</p></div>`:""}<div class="qualityFeedback"><strong>Feedback da IA</strong><p>${esc(a.result.feedback)}</p></div><div class="qualityCriteria">${job.rubric.map(r=>{
          const item=a.result.items.find(i=>i.id===r.id),original=a.original.items.find(i=>i.id===r.id);
          return `<details class="qualityCriterion ${esc(item.outcome)}"><summary><span>${icon(item.outcome==="fail"?"circle-alert":item.outcome==="pass"?"circle-check":"circle-minus")}${esc(r.label)}</span><strong>${item.outcome==="fail"?`−${r.penalty}`:item.outcome==="pass"?"Sem desvio":"Não avaliável"}</strong></summary><p>${esc(item.reason)}</p>${item.evidence.map(ref=>{const m=messageById.get(ref);return m?`<blockquote><span>${esc(when(m.time))} · ${m.kind==="customer"?"Cliente":m.kind==="human"?"Atendente":"Contexto"}</span>${esc(m.text) || "[mídia não analisada]"}</blockquote>`:"";}).join("")}${original.outcome==="fail"?`<label class="check"><input type="checkbox" data-keep-failure="${esc(r.id)}" ${item.outcome==="fail"?"checked":""}>Manter desconto de ${r.penalty} pontos</label>`:""}</details>`;
        }).join("")}</div><form class="qualityReviewForm"><label>Registro da revisão<textarea name="reason" required maxlength="1200" rows="2" placeholder="Motivo da aprovação ou correção dos descontos"></textarea></label><button type="submit" class="primary">${icon("check-check")} Salvar revisão</button></form>${a.history.length?`<details class="qualityReviewHistory"><summary>${a.history.length} revisão(ões) registrada(s)</summary>${a.history.map(h=>`<p><time>${esc(when(h.created_at))}</time> · ${esc(h.reason)}</p>`).join("")}</details>`:""}</section>`).join("")}`;
        if(job.audio_summary?.requested){
          const summary=document.createElement('p');summary.className='qualityScope';summary.textContent=`Áudios transcritos: ${Number(job.audio_summary.transcribed)||0} · reutilizados sem nova transcrição: ${Number(job.audio_summary.reused)||0}`;
          host.querySelector('.qualityScope').after(summary);
        }
        const transcripts=job.messages.filter(m=>m.transcription);
        if(transcripts.length)host.insertAdjacentHTML('beforeend',`<details class="qualityTranscripts"><summary>${transcripts.length} transcrição(ões) de áudio</summary>${transcripts.map(m=>`<blockquote><span>${esc(when(m.time))} · ${m.kind==='customer'?'Cliente':m.kind==='human'?'Atendente':'Contexto'} · Transcrição automática</span><p>${esc(m.text)}</p></blockquote>`).join('')}</details>`);
        if(job.status==="failed"){
          host.insertAdjacentHTML("beforeend",`<div class="qualityRetry"><label class="check"><input type="checkbox" data-quality-retry-consent>Autorizo uma nova tentativa de análise pela OpenAI, com novo consumo da API</label><button type="button" data-quality-retry>${icon("rotate-cw")} Tentar novamente</button></div>`);
          host.querySelector("[data-quality-retry]").onclick=async event=>{
            if(!host.querySelector("[data-quality-retry-consent]").checked){error("Confirme o consumo da API para tentar novamente.");return;}
            const button=event.currentTarget;button.disabled=true;
            try{
              const includeAudio=host.querySelector('[data-quality-retry-audio]').checked;
              const result=await api("quality/analyze",{doctor_id:doc,channel_id:job.channel_id,contact_id:job.contact_id,confirm:true,retry_from:id,request_id:retryNonce,include_audio:includeAudio,confirm_audio:includeAudio},{doctor:doc});
              if(token===dialogSeq && host.isConnected)openJob(doc,result.id,context);
            }catch(e){if(token===dialogSeq && host.isConnected){error(e.message);button.disabled=false;}}
          };
        }
        host.querySelector('.qualityRetry')?.insertAdjacentHTML('afterbegin',`<label class="check"><input type="checkbox" data-quality-retry-audio ${job.include_audio?'checked':''}>Incluir áudios nesta tentativa; transcrições que falharam podem consumir a API novamente</label>`);
        host.querySelectorAll("[data-assessment]").forEach(section=>{
          const a=job.assessments.find(a=>a.id===section.dataset.assessment);
          const r=a.response;
          if(r){
            section.querySelector('header').insertAdjacentHTML('afterend',`<div class="qualityResponseHeading">${responseBadge(r)}${r.status==='accepted'?`<small>${esc(when(r.accepted_at))}</small>`:''}</div>`);
            if(r.contest_used)section.querySelector('.qualityResponseHeading').insertAdjacentHTML('afterend',`<div class="qualityAppeal"><strong>Ressalva do atendente · ${esc(when(r.contested_at))}</strong><p>${esc(r.contest_reason)}</p>${r.decision?`<strong>${r.decision==='accept'?'Ressalva acolhida':'Avaliação mantida'} · ${esc(when(r.decided_at))}</strong><p>${esc(r.decision_reason)}</p>`:''}</div>`);
            if(r.status==='contested')section.querySelector('.qualityReviewForm').insertAdjacentHTML('afterbegin',`<label>Decisão sobre a ressalva<select name="contest_decision" required><option value="">Selecione</option><option value="accept">Acolher ressalva · ajustar ou esclarecer</option><option value="reject">Manter avaliação</option></select></label><label>Resposta ao atendente<textarea name="contest_note" rows="3" maxlength="2000" required></textarea></label>`);
          }
          section.querySelector("form").onsubmit=async event=>{
            event.preventDefault();const button=event.currentTarget.querySelector("button");button.disabled=true;
            try{
              await api("quality/review",{doctor_id:doc,id:a.id,revision:a.revision,failures:[...section.querySelectorAll("[data-keep-failure]:checked")].map(c=>c.dataset.keepFailure),reason:section.querySelector('[name="reason"]').value,
                ...(r?.status==='contested'?{contest_decision:section.querySelector('[name="contest_decision"]').value,contest_note:section.querySelector('[name="contest_note"]').value,response_revision:r.revision}:{})},{doctor:doc});
              if(token===dialogSeq && host.isConnected){await poll();if(root)refresh();}
            }catch(e){if(token===dialogSeq && host.isConnected){error(e.message);button.disabled=false;}}
          };
        });icons();
        if(["queued","running"].includes(job.status))timer=setTimeout(poll,3000);
      }catch(e){if(token===dialogSeq)error(e.message);}
    }
    await poll();
  }
  return {mount,unmount,openConversation};
};
