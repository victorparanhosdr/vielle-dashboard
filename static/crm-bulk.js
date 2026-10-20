(() => {
  "use strict";
  window.createCrmBulk=({api,esc,icon,icons,modal,error,getScope,onDone,notice})=>{
    let board=null,toolbar=null,observer=null,selecting=false,key="",request=0;
    const chosen=new Set(),dialog=()=>document.getElementById("crmDialog");
    dialog().addEventListener("close",()=>{request++;});
    const button=(command,label,symbol,disabled=false)=>`<button type="button" data-bulk="${command}" ${disabled?"disabled":""}>${icon(symbol)} ${label}</button>`;
    function unmount(){observer?.disconnect();observer=null;board=null;toolbar=null;}
    function reset(){chosen.clear();selecting=false;key="";unmount();}
    function paint(){
      if(!board || !toolbar)return;
      board.classList.toggle("crmBulkSelecting",selecting);
      board.querySelectorAll("[data-lead]").forEach(card=>{
        const id=card.dataset.lead,wrap=card.closest(".leadSelectable");
        if(wrap){wrap.classList.toggle("selected",chosen.has(id));wrap.querySelector("input").checked=chosen.has(id);wrap.querySelector("label").hidden=!selecting;}
        card.draggable=!selecting;
      });
      toolbar.innerHTML=selecting?`<strong role="status">${chosen.size} selecionados</strong>${button("loaded","Selecionar carregados","list-checks")}${button("move","Mover etapa","arrow-right",!chosen.size)}${button("delete","Excluir","trash-2",!chosen.size)}<button type="button" data-bulk="clear" class="icon-button" aria-label="Encerrar seleção" title="Encerrar seleção">${icon("x")}</button>`:button("select","Selecionar leads","list-checks");
      icons();
    }
    function decorate(){
      if(!board)return;
      board.querySelectorAll("[data-lead]").forEach(card=>{
        if(card.closest(".leadSelectable"))return;
        const wrap=document.createElement("div"),label=document.createElement("label"),input=document.createElement("input");
        wrap.className="leadSelectable";label.className="crmLeadSelect";input.type="checkbox";input.dataset.bulkLead=card.dataset.lead;
        input.setAttribute("aria-label",`Selecionar ${card.querySelector("strong")?.textContent || "lead"}`);
        card.replaceWith(wrap);label.append(input);wrap.append(label,card);
      });paint();
    }
    function toggle(id){if(!selecting)return false;chosen.has(id)?chosen.delete(id):chosen.add(id);paint();return true;}
    function mountBoard(target){
      unmount();const scope=getScope(),next=JSON.stringify([scope.doctor,scope.pipeline,scope.filters]);
      if(next!==key){chosen.clear();selecting=false;key=next;}
      board=target;toolbar=document.createElement("div");toolbar.className="crmBulkToolbar";
      toolbar.setAttribute("aria-label","Ações em massa");target.closest(".crmWorkspace").before(toolbar);
      target.addEventListener("change",e=>{if(e.target.dataset.bulkLead)toggle(e.target.dataset.bulkLead);});
      toolbar.onclick=e=>{const command=e.target.closest("[data-bulk]")?.dataset.bulk;if(!command)return;
        if(command==="select")selecting=true;
        if(command==="clear"){selecting=false;chosen.clear();}
        if(command==="loaded")board.querySelectorAll("[data-lead]").forEach(card=>chosen.add(card.dataset.lead));
        if(command==="move" || command==="delete")return open(command,{lead_ids:[...chosen]});paint();
      };
      observer=new MutationObserver(decorate);target.querySelectorAll(".columnCards").forEach(lane=>observer.observe(lane,{childList:true}));decorate();
    }
    async function open(action,selection,override={}){
      const scope={...getScope(),...override},owner=scope.doctor,pipeline=scope.pipelines.find(p=>p.id===scope.pipeline);
      if(!pipeline){notice("Selecione um funil antes de continuar.");return;}
      const seq=++request;
      async function prepare(stage=""){
        modal("Conferir seleção",'<p class="connectionNote" role="status">Conferindo os leads selecionados…</p>');
        try{
          const preview=await api("lead-bulk-preview",{doctor_id:owner,pipeline_id:pipeline.id,action,selection,stage_id:stage},{doctor:owner});
          if(seq!==request || !dialog().open || getScope().doctor!==owner)return;
          const label={move:"Mover",delete:"Excluir",restore:"Restaurar"}[action];
          modal(`${label} ${preview.count} ${preview.count===1?"lead":"leads"}`,`<form id="crmBulkConfirm"><p class="connectionNote">${esc(preview.pipeline)}${preview.stage?` · ${esc(preview.stage)}`:""}</p>${action==="delete"?'<p>Os leads irão para a lixeira. Contatos, conversas e fichas externas serão preservados. Envios já em andamento não podem ser recolhidos.</p>':action==="restore"?'<p>Os leads voltarão à etapa anterior. Robôs e campanhas cancelados não serão retomados.</p>':""}<ul class="crmBulkSample">${preview.sample.map(l=>`<li>${esc(l.name)}<small>${esc(l.title)}</small></li>`).join("")}${preview.count>preview.sample.length?`<li>e mais ${preview.count-preview.sample.length} leads</li>`:""}</ul>${preview.automations.length?`<p class="inlineWarning">Esta etapa pode ativar: ${preview.automations.map(b=>esc(b.name)).join(", ")}. Mensagens automáticas seguem os limites do número.</p>`:""}<label class="check"><input type="checkbox" name="confirm" required>Confirmo ${action==="delete"?"a exclusão":action==="restore"?"a restauração":"a movimentação"} de ${preview.count} ${preview.count===1?"lead":"leads"}.</label><div class="formActions"><button type="button" data-bulk-cancel>Cancelar</button><button type="submit" class="primary ${action==="delete"?"crmDestructive":""}">${icon(action==="delete"?"trash-2":action==="restore"?"undo-2":"arrow-right")} ${label}</button></div></form>`);
          document.querySelector("[data-bulk-cancel]").onclick=()=>{request++;dialog().close();};
          document.getElementById("crmBulkConfirm").onsubmit=async e=>{
            e.preventDefault();if(seq!==request || getScope().doctor!==owner)return;
            const form=e.currentTarget,submit=form.querySelector('[type="submit"]');if(submit.disabled)return;submit.disabled=true;
            try{
              const result=await api("lead-bulk-apply",{id:preview.id,doctor_id:owner,confirm:form.elements.confirm.checked},{doctor:owner});
              if(seq!==request || getScope().doctor!==owner)return;
              chosen.clear();selecting=false;dialog().close();await onDone();notice(`${result.count} ${result.count===1?"lead":"leads"} ${action==="delete"?"enviados para a lixeira":action==="restore"?"restaurados":"movimentados"}.`);
            }catch(err){error(err.message);}finally{if(submit.isConnected)submit.disabled=false;}
          };
        }catch(err){if(seq===request && dialog().open)error(err.message);}
      }
      if(action!=="move")return prepare();
      modal("Mover leads de etapa",`<form id="crmBulkStage"><p class="connectionNote">${esc(pipeline.name)}</p><label>Etapa de destino<select name="stage" required><option value="">Selecionar etapa</option>${pipeline.stages.map(s=>`<option value="${esc(s.id)}">${esc(s.name)}</option>`).join("")}</select></label><div class="formActions"><button type="submit" class="primary">${icon("arrow-right")} Conferir movimentação</button></div></form>`);
      document.getElementById("crmBulkStage").onsubmit=e=>{e.preventDefault();prepare(e.currentTarget.elements.stage.value);};
    }
    async function trash(page=1){
      const scope=getScope(),seq=++request;let selected=new Set();
      modal("Lixeira do funil",'<p class="connectionNote">Carregando lixeira…</p>');
      try{
        const data=await api("lead-trash",undefined,{doctor:scope.doctor,pipeline:scope.pipeline,page});
        if(seq!==request || !dialog().open || getScope().doctor!==scope.doctor)return;
        modal("Lixeira do funil",`<p class="connectionNote">${data.total} ${data.total===1?"lead excluído":"leads excluídos"}</p><div id="crmTrashRows" class="crmTrashRows">${data.items.map(l=>`<label class="crmTrashRow"><input type="checkbox" value="${esc(l.id)}" aria-label="Restaurar ${esc(l.name)}"><span><strong>${esc(l.name)}</strong><small>${esc(l.title)} · ${esc(l.stage)}</small><small>${new Date(l.deleted_at*1000).toLocaleString("pt-BR")} · ${esc(l.deleted_by)}</small></span></label>`).join("") || '<p class="emptyState">A lixeira está vazia.</p>'}</div><div class="crmBulkTrashActions">${button("page","Selecionar página","list-checks",!data.items.length)}${button("restore","Restaurar selecionados","undo-2",true)}</div><div class="campaignPagination"><button type="button" id="crmTrashPrev" class="icon-button" aria-label="Página anterior da lixeira" ${page===1?"disabled":""}>${icon("chevron-left")}</button><span>Página ${page}</span><button type="button" id="crmTrashNext" class="icon-button" aria-label="Próxima página da lixeira" ${!data.has_more?"disabled":""}>${icon("chevron-right")}</button></div>`);
        const rows=document.getElementById("crmTrashRows"),actions=document.querySelector(".crmBulkTrashActions"),restore=actions.querySelector('[data-bulk="restore"]');
        function sync(){restore.disabled=!selected.size;rows.querySelectorAll("input").forEach(input=>input.checked=selected.has(input.value));}
        rows.onchange=e=>{e.target.checked?selected.add(e.target.value):selected.delete(e.target.value);sync();};
        actions.onclick=e=>{const command=e.target.closest("[data-bulk]")?.dataset.bulk;if(command==="page"){selected=new Set(data.items.map(l=>l.id));sync();}if(command==="restore")open("restore",{lead_ids:[...selected]},scope);};
        document.getElementById("crmTrashPrev").onclick=()=>trash(page-1);document.getElementById("crmTrashNext").onclick=()=>trash(page+1);
      }catch(err){if(seq===request && dialog().open)error(err.message);}
    }
    return {mountBoard,unmount,reset,toggle,open,trash,isSelecting:()=>selecting};
  };
})();
