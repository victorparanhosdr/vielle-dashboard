(() => {
  "use strict";
  window.createCrmImporter = ({api, modal, error, icons, esc, load}) => ({
    async open(doctor, pipeline, pipelineName="") {
      let current=null,page=1,poll=null,busy=false;
      const dialog=document.getElementById("crmDialog");
      modal("Migração do Kommo",'<p role="status">Consultando migração…</p>');
      const stop=()=>{clearTimeout(poll);poll=null;};
      dialog.addEventListener("close",stop,{once:true});
      const paint=()=>{
        if(!dialog.open)return;
        const r=current.report || {},done=current.status==="done",ready=current.status==="ready",loading=current.status==="loading";
        const course=Boolean(current.course_key) || pipelineName.toLowerCase()==="regencode";
        const entries=[["total","Oportunidades na origem"],["created",done?"Novas no DOC4DOCS":"Novas a importar"],["reused","Já existentes preservadas"],["duplicates","Repetições agrupadas"],["already","Já migradas anteriormente"],...(course?[["course_contacts","Contatos do curso · sem vínculo clínico obrigatório"]]:[["linked","Pacientes vinculados"],["resolved","Vínculos confirmados por nome e telefone"],["new","Contatos sem ficha localizada"],["review",done?"Contatos importados sem vínculo clínico":"Contatos a importar sem vínculo clínico"]]),["issue_count","Pendências não importadas"]];
        if(r.ignored_no_phone)entries.push(["ignored_no_phone","Ignorados sem telefone · inclui Instagram sem WhatsApp"]);
        const reasons=(r.issue_summary || []).map(item=>`<li><strong>${Number(item.count)}</strong> · ${esc(item.reason)}</li>`).join("");
        document.getElementById("modalBody").innerHTML=`<div class="kommoImport"><p class="connectionNote">${esc(current.pipeline_name || pipelineName)} · Dr. Victor Paranhos de Andrade</p><p role="status">${esc(current.message || "Migração ainda não preparada.")}</p>${Object.hasOwn(r,"total")?`<dl class="importSummary">${entries.map(([key,label])=>`<div><dt>${label}</dt><dd>${Number(r[key] || 0)}</dd></div>`).join("")}</dl>`:""}${reasons?`<h3>Pendências por motivo</h3><ul>${reasons}</ul>`:""}${current.issues?.length?`<h3>Conferência da migração</h3><div class="importIssues"><table><thead><tr><th>Cliente</th><th>Situação</th><th>Conferência necessária</th><th>Kommo</th></tr></thead><tbody>${current.issues.map(item=>`<tr><td>${esc(item.name)}</td><td>${item.imported?(done?"Importado sem ficha":"Importar sem ficha"):"Não importado"}</td><td>${esc(item.reason)}</td><td>#${esc(item.source_id)}</td></tr>`).join("")}</tbody></table></div><div class="formActions"><button id="importPrevious" class="icon-button" title="Página anterior" aria-label="Página anterior" ${page<=1?"disabled":""}><i data-lucide="chevron-left"></i></button><span>${page} de ${current.pages}</span><button id="importNext" class="icon-button" title="Próxima página" aria-label="Próxima página" ${page>=current.pages?"disabled":""}><i data-lucide="chevron-right"></i></button></div>`:""}<div class="formActions"><button id="prepareKommo" ${loading||busy?"disabled":""}><i data-lucide="refresh-cw"></i> ${loading?"Preparando prévia…":"Gerar nova prévia"}</button>${done&&r.issue_count?`<button id="retryKommo" class="primary" ${busy?"disabled":""}><i data-lucide="list-restart"></i> Resolver pendências em lote</button>`:""}${ready?`<button id="commitKommo" class="primary" ${busy||!(r.created+r.reused+r.duplicates)?"disabled":""}><i data-lucide="download"></i> Importar clientes</button>`:""}</div></div>`;
        icons();
        const prepare=async(retry=false)=>{
          busy=true;paint();
          try{const job=await api("kommo-import-preview",{doctor_id:doctor,pipeline_id:pipeline,...(retry?{retry_from:current.id}:{})});current={id:job.id,status:"loading",message:retry?"Atualizando cadastros e reavaliando pendências…":course?"Atualizando Kiwify e consultando o funil REGENCODE…":"Atualizando Clínica Experts e consultando Kommo…",report:{}};page=1;paint();await refresh();}
          catch(e){error(e.message);}finally{busy=false;if(dialog.open)paint();}
        };
        document.getElementById("prepareKommo").onclick=()=>prepare();
        const retry=document.getElementById("retryKommo");
        if(retry)retry.onclick=()=>prepare(true);
        const commit=document.getElementById("commitKommo");
        if(commit)commit.onclick=async()=>{
          busy=true;paint();
          try{current=await api("kommo-import-commit",{doctor_id:doctor,pipeline_id:pipeline,id:current.id});page=1;await load();paint();}
          catch(e){error(e.message);}finally{busy=false;if(dialog.open)paint();}
        };
        const previous=document.getElementById("importPrevious"),next=document.getElementById("importNext");
        if(previous)previous.onclick=()=>{page--;refresh();};
        if(next)next.onclick=()=>{page++;refresh();};
      };
      const refresh=async()=>{
        stop();
        try{current=await api("kommo-import",undefined,{doctor,pipeline,id:current?.id || "",page});if(!dialog.open)return;page=current.page || 1;paint();if(current.status==="loading")poll=setTimeout(refresh,2500);}
        catch(e){if(dialog.open)error(e.message);}
      };
      await refresh();
    }
  });
})();
