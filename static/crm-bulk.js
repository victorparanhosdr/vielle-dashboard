(() => {
  "use strict";
  window.createCrmBulk=({api,esc,icon,icons,modal,error,getScope,onDone,notice})=>{
    let board=null,toolbar=null,observer=null,selecting=false,key="",request=0,columnVersion=0;
    const chosen=new Set(),dialog=()=>document.getElementById("crmDialog");
    const columns=new Map(),busyColumns=new Set();
    dialog().addEventListener("close",()=>{request++;});
    const button=(command,label,symbol,disabled=false)=>`<button type="button" data-bulk="${command}" ${disabled?"disabled":""}>${icon(symbol)} ${label}</button>`;
    function unmount(){columnVersion++;busyColumns.clear();observer?.disconnect();observer=null;board=null;toolbar?.remove();toolbar=null;}
    function reset(){chosen.clear();columns.clear();selecting=false;key="";unmount();}
    function paint(){
      if(!board || !toolbar)return;
      board.classList.toggle("crmBulkSelecting",selecting);
      board.querySelectorAll("[data-lead]").forEach(card=>{
        const id=card.dataset.lead,wrap=card.closest(".leadSelectable");
        if(wrap){wrap.classList.toggle("selected",chosen.has(id));wrap.querySelector("input").checked=chosen.has(id);wrap.querySelector("input").disabled=busyColumns.size>0;wrap.querySelector("label").hidden=!selecting;}
        card.draggable=!selecting;
      });
      const busy=busyColumns.size>0;
      toolbar.innerHTML=selecting?`<strong role="status">${busy?"Selecionando coluna…":`${chosen.size} selecionados`}</strong>${button("loaded","Selecionar carregados","list-checks",busy)}${button("move","Mover etapa","arrow-right",!chosen.size || busy)}${button("transfer","Transferir","arrow-right-left",!chosen.size || busy)}${button("delete","Excluir","trash-2",!chosen.size || busy)}<button type="button" data-bulk="clear" class="icon-button" aria-label="Encerrar seleção" title="Encerrar seleção">${icon("x")}</button>`:button("select","Selecionar leads","list-checks");
      board.querySelectorAll(".crmColumn").forEach(column=>{
        const stage=column.dataset.stage,control=column.querySelector(".crmColumnSelect"),input=control?.querySelector("input");if(!input)return;
        const snapshot=columns.get(stage),known=snapshot || [...column.querySelectorAll("[data-lead]")].map(card=>card.dataset.lead),selected=known.filter(id=>chosen.has(id)).length;
        control.hidden=!selecting;input.disabled=!selecting || busyColumns.has(stage);
        input.checked=busyColumns.has(stage) || Boolean(snapshot?.length && selected===snapshot.length);input.indeterminate=selected>0 && !input.checked;
        control.querySelector("span").textContent=busyColumns.has(stage)?"Selecionando…":input.checked?`Coluna selecionada (${selected})`:"Selecionar coluna";
      });
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
      });
      board.querySelectorAll(".crmColumn").forEach(column=>{
        if(column.querySelector(".crmColumnSelect"))return;
        const label=document.createElement("label");label.className="crmColumnSelect";
        const title=column.querySelector(".columnHead strong")?.textContent || "etapa";
        label.innerHTML=`<input type="checkbox" data-bulk-column="${esc(column.dataset.stage)}" aria-label="Selecionar coluna ${esc(title)}"><span>Selecionar coluna</span>`;
        column.querySelector(".columnHead").append(label);
      });paint();
    }
    async function selectColumn(stage,checked){
      if(!selecting || busyColumns.has(stage))return;
      if(!checked && columns.has(stage)){columns.get(stage).forEach(id=>chosen.delete(id));paint();return;}
      const root=board,version=columnVersion,scope=getScope(),signature=key;
      busyColumns.add(stage);paint();
      try{
        const result=await api("lead-column-selection",{doctor_id:scope.doctor,pipeline_id:scope.pipeline,stage_id:stage,filters:scope.filters},{doctor:scope.doctor});
        if(root!==board || version!==columnVersion || signature!==key || !selecting)return;
        const all=new Set([...chosen,...result.lead_ids]);
        if(all.size>5000)throw new Error("Selecione até 5.000 leads por operação. Refine os filtros ou desmarque outra coluna.");
        columns.set(stage,result.lead_ids);result.lead_ids.forEach(id=>chosen.add(id));
        if(!result.count)notice("Esta coluna não tem leads nos filtros atuais.");
      }catch(err){if(root===board && version===columnVersion)notice(err.message);}
      finally{if(root===board && version===columnVersion){busyColumns.delete(stage);paint();}}
    }
    function toggle(id){if(!selecting)return false;if(busyColumns.size)return true;chosen.has(id)?chosen.delete(id):chosen.add(id);paint();return true;}
    function mountBoard(target){
      unmount();const scope=getScope(),next=JSON.stringify([scope.doctor,scope.pipeline,scope.filters]);
      if(next!==key){chosen.clear();columns.clear();selecting=false;key=next;}
      board=target;toolbar=document.createElement("div");toolbar.className="crmBulkToolbar";
      toolbar.setAttribute("aria-label","Ações em massa");target.closest(".crmWorkspace").before(toolbar);
      target.addEventListener("change",e=>{if(e.target.dataset.bulkLead)toggle(e.target.dataset.bulkLead);if(e.target.dataset.bulkColumn)selectColumn(e.target.dataset.bulkColumn,e.target.checked);});
      toolbar.onclick=e=>{const command=e.target.closest("[data-bulk]")?.dataset.bulk;if(!command)return;
        if(command==="select")selecting=true;
        if(command==="clear"){columnVersion++;busyColumns.clear();selecting=false;chosen.clear();columns.clear();}
        if(command==="loaded")board.querySelectorAll("[data-lead]").forEach(card=>chosen.add(card.dataset.lead));
        if(["move","transfer","delete"].includes(command))return open(command,{lead_ids:[...chosen]});paint();
      };
      observer=new MutationObserver(decorate);target.querySelectorAll(".columnCards").forEach(lane=>observer.observe(lane,{childList:true}));decorate();
    }
    async function open(action,selection,override={}){
      const scope={...getScope(),...override},owner=scope.doctor,pipeline=scope.pipelines.find(p=>p.id===scope.pipeline);
      const current=()=>scope.active?scope.active():getScope().doctor===owner;
      if(!pipeline){notice("Selecione um funil antes de continuar.");return;}
      const seq=++request;
      async function prepare(stage="",destination={}){
        modal("Conferir seleção",'<p class="connectionNote" role="status">Conferindo os leads selecionados…</p>');
        try{
          const preview=await api("lead-bulk-preview",{doctor_id:owner,pipeline_id:pipeline.id,action,selection,stage_id:stage,...destination},{doctor:owner});
          if(seq!==request || !dialog().open || !current())return;
          const label={move:"Mover",transfer:"Transferir",delete:"Excluir",restore:"Restaurar"}[action];
          modal(`${label} ${preview.count} ${preview.count===1?"lead":"leads"}`,`<form id="crmBulkConfirm"><p class="connectionNote">${esc(preview.pipeline)}${preview.stage?` · ${esc(preview.stage)}`:""}</p>${action==="delete"?'<p>Os leads irão para a lixeira. Contatos, conversas e fichas externas serão preservados. Envios já em andamento não podem ser recolhidos.</p>':action==="restore"?'<p>Os leads voltarão à etapa anterior. Robôs e campanhas cancelados não serão retomados.</p>':""}<ul class="crmBulkSample">${preview.sample.map(l=>`<li>${esc(l.name)}<small>${esc(l.title)}</small></li>`).join("")}${preview.count>preview.sample.length?`<li>e mais ${preview.count-preview.sample.length} leads</li>`:""}</ul>${preview.automations.length?`<p class="inlineWarning">Esta etapa pode ativar: ${preview.automations.map(b=>esc(b.name)).join(", ")}. Mensagens automáticas seguem os limites do número.</p>`:""}<label class="check"><input type="checkbox" name="confirm" required>Confirmo ${action==="delete"?"a exclusão":action==="restore"?"a restauração":"a movimentação"} de ${preview.count} ${preview.count===1?"lead":"leads"}.</label><div class="formActions"><button type="button" data-bulk-cancel>Cancelar</button><button type="submit" class="primary ${action==="delete"?"crmDestructive":""}">${icon(action==="delete"?"trash-2":action==="restore"?"undo-2":"arrow-right")} ${label}</button></div></form>`);
          document.querySelector("[data-bulk-cancel]").onclick=()=>{request++;dialog().close();};
          if(action==="transfer"){
            const form=document.getElementById("crmBulkConfirm"),route=document.createElement("div");route.className="leadTransferSummary";
            form.querySelector(".connectionNote").textContent=`Origem · ${preview.pipeline}`;
            route.innerHTML=`<span>Destino</span><strong>${esc(preview.target_doctor)}</strong><p>${esc(preview.target_pipeline)} · ${esc(preview.stage)}</p>`;form.querySelector(".connectionNote").after(route);
            const warning=document.createElement("p");warning.className="inlineWarning";
            warning.textContent="Notas, atividades e rastreabilidade acompanham a oportunidade, sem duplicar o contato. A conexão WhatsApp e suas conversas continuam na origem. Automações antigas e seus envios pendentes serão interrompidos; robôs do destino não serão iniciados.";
            form.querySelector(".crmBulkSample").after(warning);
          }
          document.getElementById("crmBulkConfirm").onsubmit=async e=>{
            e.preventDefault();if(seq!==request || !current())return;
            const form=e.currentTarget,submit=form.querySelector('[type="submit"]');if(submit.disabled)return;submit.disabled=true;
            try{
              const result=await api("lead-bulk-apply",{id:preview.id,doctor_id:owner,confirm:form.elements.confirm.checked},{doctor:owner});
              if(seq!==request || !current())return;
              chosen.clear();selecting=false;dialog().close();await onDone(result);notice(`${result.count} ${result.count===1?"lead":"leads"} ${action==="delete"?"enviados para a lixeira":action==="restore"?"restaurados":action==="transfer"?"transferidos":"movimentados"}.`);
            }catch(err){error(err.message);}finally{if(submit.isConnected)submit.disabled=false;}
          };
        }catch(err){if(seq===request && dialog().open)error(err.message);}
      }
      if(action==="transfer"){
        modal("Transferir oportunidade",'<p class="connectionNote" role="status">Carregando destinos…</p>');
        try{
          const data=await api("lead-transfer-options",undefined,{doctor:owner});
          if(seq!==request || !dialog().open || !current())return;
          modal("Transferir para outro doutor ou funil",`<form id="crmLeadTransferTarget"><p class="connectionNote">Origem · ${esc(pipeline.name)}</p><div class="formGrid"><label>Doutor de destino<select name="doctor" required><option value="">Selecionar doutor</option>${data.doctors.map(d=>`<option value="${esc(d.id)}" ${d.id===owner?"selected":""}>${esc(d.name)}</option>`).join("")}</select></label><label>Funil de destino<select name="pipeline" required></select></label></div><label>Etapa de destino<select name="stage" required></select></label><div class="formActions"><button type="submit" class="primary" disabled>${icon("arrow-right-left")} Conferir transferência</button></div></form>`);
          const form=document.getElementById("crmLeadTransferTarget"),doc=form.elements.doctor,target=form.elements.pipeline,stage=form.elements.stage,submit=form.querySelector('[type="submit"]');
          const stages=()=>{
            const items=data.pipelines.find(p=>p.id===target.value)?.stages || [];
            stage.innerHTML=items.map(s=>`<option value="${esc(s.id)}">${esc(s.name)}</option>`).join("") || '<option value="">Sem etapas disponíveis</option>';
            stage.disabled=!items.length;submit.disabled=!doc.value || !target.value || !stage.value;
          };
          const pipelines=()=>{
            const items=data.pipelines.filter(p=>p.doctor_id===doc.value && p.id!==pipeline.id);
            target.innerHTML=items.map(p=>`<option value="${esc(p.id)}">${esc(p.name)}</option>`).join("") || '<option value="">Nenhum outro funil disponível</option>';
            target.disabled=!items.length;stages();
          };
          doc.onchange=pipelines;target.onchange=stages;pipelines();
          form.onsubmit=e=>{e.preventDefault();if(submit.disabled || seq!==request)return;prepare(stage.value,{target_doctor_id:doc.value,target_pipeline_id:target.value});};
        }catch(err){if(seq===request && dialog().open)error(err.message);}
        return;
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
