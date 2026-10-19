(() => {
  "use strict";
  window.createCrmImporter = ({api, modal, error, icons, esc, load}) => ({
    async open(doctor, pipeline) {
      let current=null,page=1,poll=null,busy=false;
      const dialog=document.getElementById("crmDialog");
      modal("Migração do Kommo",'<p role="status">Consultando migração…</p>');
      const stop=()=>{clearTimeout(poll);poll=null;};
      dialog.addEventListener("close",stop,{once:true});
      const paint=()=>{
        if(!dialog.open)return;
        const r=current.report || {},done=current.status==="done",ready=current.status==="ready",loading=current.status==="loading";
        const entries=[["total","Oportunidades na origem"],["created",done?"Novas no DOC4DOCS":"Novas a importar"],["reused","Já existentes preservadas"],["duplicates","Repetições agrupadas"],["already","Já migradas anteriormente"],["linked","Pacientes vinculados"],["new","Contatos sem ficha localizada"],["issue_count","Pendências não importadas"]];
        document.getElementById("modalBody").innerHTML=`<div class="kommoImport"><p class="connectionNote">Orgânico Paranhos · Dr. Victor Paranhos</p><p role="status">${esc(current.message || "Migração ainda não preparada.")}</p>${Object.hasOwn(r,"total")?`<dl class="importSummary">${entries.map(([key,label])=>`<div><dt>${label}</dt><dd>${Number(r[key] || 0)}</dd></div>`).join("")}</dl>`:""}${r.issue_count?`<h3>Pendências de cadastro</h3><div class="importIssues"><table><thead><tr><th>Cliente</th><th>Conferência necessária</th><th>Kommo</th></tr></thead><tbody>${current.issues.map(item=>`<tr><td>${esc(item.name)}</td><td>${esc(item.reason)}</td><td>#${esc(item.source_id)}</td></tr>`).join("")}</tbody></table></div><div class="formActions"><button id="importPrevious" class="icon-button" title="Página anterior" aria-label="Página anterior" ${page<=1?"disabled":""}><i data-lucide="chevron-left"></i></button><span>${page} de ${current.pages}</span><button id="importNext" class="icon-button" title="Próxima página" aria-label="Próxima página" ${page>=current.pages?"disabled":""}><i data-lucide="chevron-right"></i></button></div>`:""}<div class="formActions"><button id="prepareKommo" ${loading||busy?"disabled":""}><i data-lucide="refresh-cw"></i> ${loading?"Preparando prévia…":"Gerar nova prévia"}</button>${ready?`<button id="commitKommo" class="primary" ${busy||!(r.created+r.reused+r.duplicates)?"disabled":""}><i data-lucide="download"></i> Importar clientes</button>`:""}</div></div>`;
        icons();
        document.getElementById("prepareKommo").onclick=async()=>{
          busy=true;paint();
          try{const job=await api("kommo-import-preview",{doctor_id:doctor,pipeline_id:pipeline});current={id:job.id,status:"loading",message:"Atualizando Clínica Experts e consultando Kommo…",report:{}};page=1;paint();await refresh();}
          catch(e){error(e.message);}finally{busy=false;if(dialog.open)paint();}
        };
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
