(() => {
  "use strict";
  window.createCrmChannelSettings=({api,esc,icon,icons,modal,error,onDone,onTransferred})=>{
    async function transfer(channel,doctor){
      modal("Transferir conexão WhatsApp",'<div id="crmChannelTransfer"><p class="connectionNote" role="status">Conferindo conexão…</p></div>');
      const host=document.getElementById("crmChannelTransfer");
      try{
        const data=await api("channel-transfer",undefined,{doctor,id:channel.id});
        if(!host.isConnected || !document.getElementById("crmDialog").open)return;
        const {impact}=data,blocked=Boolean(impact.sending || impact.unknown);
        const source=data.channel;
        host.innerHTML=`<form id="crmChannelTransferForm"><p class="connectionNote"><strong>${esc(source.name)}</strong> · +${esc(source.phone)}</p><div class="formGrid"><label>Profissional de destino<select name="target_doctor" required><option value="">Selecione o profissional</option>${data.doctors.map(d=>`<option value="${esc(d.id)}">${esc(d.name)}</option>`).join("")}</select></label><label>Funil dos novos leads<select name="pipeline" required disabled></select></label></div><label>Etapa de entrada<select name="stage" required disabled></select></label><dl class="channelTransferImpact"><div><dt>Conversas nesta conexão</dt><dd>${impact.conversations}</dd></div><div><dt>Mensagens pendentes</dt><dd>${impact.queued}</dd></div><div><dt>Campanhas vinculadas</dt><dd>${impact.campaigns}</dd></div><div><dt>Robôs ativos vinculados</dt><dd>${impact.bots}</dd></div></dl><p class="connectionNote">A transferência não desconecta o WhatsApp. O histórico e os anexos desta conexão passam ao profissional de destino, conforme as permissões dos usuários. Os leads e seus dados permanecem nos funis atuais, sem criar cópias.</p><p class="inlineWarning">Campanhas vinculadas serão pausadas, execuções de robôs interrompidas e mensagens pendentes canceladas. Nenhum envio será iniciado pela transferência.</p>${blocked?'<p class="inlineWarning" role="alert">Há mensagem em envio ou sem confirmação. Confira os envios antes de transferir esta conexão.</p>':""}<label class="channelTransferConfirm"><input type="checkbox" name="confirm" required ${blocked?"disabled":""}><span>Confirmo a transferência da conexão e do histórico para o profissional selecionado.</span></label><div class="formActions"><button type="submit" class="primary" disabled>${icon("arrow-right-left")} Transferir conexão</button></div></form>`;
        const form=document.getElementById("crmChannelTransferForm"),target=form.elements.target_doctor,pipeline=form.elements.pipeline,stage=form.elements.stage,submit=form.querySelector('[type="submit"]');
        let saving=false;
        const ready=()=>{submit.disabled=saving || blocked || !target.value || !pipeline.value || !stage.value || !form.elements.confirm.checked;};
        function stages(){
          const items=data.pipelines.find(p=>p.id===pipeline.value)?.stages || [];
          stage.innerHTML=items.map(s=>`<option value="${esc(s.id)}">${esc(s.name)}</option>`).join("") || '<option value="">Sem etapa aberta</option>';
          stage.disabled=!items.length;ready();
        }
        target.onchange=()=>{
          const items=data.pipelines.filter(p=>p.doctor_id===target.value && p.stages.length);
          pipeline.innerHTML=items.map(p=>`<option value="${esc(p.id)}">${esc(p.name)}</option>`).join("") || '<option value="">Nenhum funil disponível</option>';
          pipeline.disabled=!items.length;form.elements.confirm.checked=false;stages();
        };
        pipeline.onchange=()=>{form.elements.confirm.checked=false;stages();};stage.onchange=()=>{form.elements.confirm.checked=false;ready();};form.elements.confirm.onchange=ready;
        pipeline.innerHTML='<option value="">Escolha o profissional primeiro</option>';stage.innerHTML='<option value="">Escolha o funil primeiro</option>';
        form.onsubmit=async e=>{
          e.preventDefault();if(submit.disabled || saving)return;saving=true;ready();
          try{
            const result=await api("channel-transfer",{id:source.id,doctor_id:doctor,config_revision:source.config_revision,target_doctor_id:target.value,inbound_pipeline_id:pipeline.value,inbound_stage_id:stage.value,confirm:form.elements.confirm.checked},{doctor});
            document.getElementById("crmDialog").close();await onTransferred(result);
          }catch(err){error(err.message);saving=false;ready();}
        };
        icons();
      }catch(err){if(host.isConnected)error(err.message);}
    }
    function edit(channel,pipelines,doctor){
      modal("Editar conexão WhatsApp",`<form id="crmChannelSettings"><p class="connectionNote">+${esc(channel.phone)}</p><label>Nome da conexão<input name="name" maxlength="160" required value="${esc(channel.name)}"></label><label>Funil de destino dos novos leads<select name="pipeline"><option value="">Padrão do profissional</option>${pipelines.map(p=>`<option value="${esc(p.id)}" ${p.id===channel.inbound_pipeline_id?"selected":""}>${esc(p.name)}</option>`).join("")}</select></label><label>Etapa de entrada<select name="stage"></select></label><div class="formActions"><button type="submit" class="primary">${icon("check")} Salvar conexão</button></div></form>`);
      const form=document.getElementById("crmChannelSettings"),pipeline=form.elements.pipeline,stage=form.elements.stage;
      function fill(selected=""){
        const stages=pipelines.find(p=>p.id===pipeline.value)?.stages.filter(s=>s.kind==="open") || [];
        stage.innerHTML=stages.map(s=>`<option value="${esc(s.id)}" ${s.id===selected?"selected":""}>${esc(s.name)}</option>`).join("") || '<option value="">Padrão do profissional</option>';
        stage.disabled=!pipeline.value;stage.required=Boolean(pipeline.value);
      }
      fill(channel.inbound_stage_id);pipeline.onchange=()=>fill();
      form.onsubmit=async e=>{
        e.preventDefault();const submit=form.querySelector('[type="submit"]');if(submit.disabled)return;submit.disabled=true;
        try{await api("channel-update",{id:channel.id,doctor_id:doctor,config_revision:channel.config_revision,name:form.elements.name.value,inbound_pipeline_id:pipeline.value,inbound_stage_id:pipeline.value?stage.value:""},{doctor});document.getElementById("crmDialog").close();await onDone();}
        catch(err){error(err.message);}finally{if(submit.isConnected)submit.disabled=false;}
      };
    }
    function mount(host,channels,pipelines,doctor,canManage){
      for(const channel of channels){
        const commands=host.querySelector(`[data-channel-state="${CSS.escape(channel.id)}"]`)?.parentElement;if(!commands)continue;
        const p=pipelines.find(p=>p.id===channel.inbound_pipeline_id),s=p?.stages.find(s=>s.id===channel.inbound_stage_id);
        const route=document.createElement("p");route.className="connectionNote";
        route.textContent=`Novos leads: ${channel.inbound_pipeline_id?p?`${p.name} · ${s?.name || "Etapa indisponível"}`:"Funil não liberado para seu usuário":"padrão do profissional"}`;
        commands.closest("article").querySelector(".recordMetrics").after(route);
        if(!canManage)continue;
        const button=document.createElement("button");button.className="icon-button";button.type="button";
        button.title="Editar nome e destino dos novos leads";button.setAttribute("aria-label",`Editar conexão ${channel.name}`);button.innerHTML=icon("pencil");
        button.onclick=()=>edit(channel,pipelines,doctor);commands.prepend(button);
        const move=document.createElement("button");move.className="icon-button";move.type="button";
        move.title="Transferir conexão para outro profissional";move.setAttribute("aria-label",`Transferir conexão ${channel.name}`);move.innerHTML=icon("arrow-right-left");
        move.onclick=()=>transfer(channel,doctor);commands.prepend(move);
      }icons();
    }
    return {mount};
  };
})();
