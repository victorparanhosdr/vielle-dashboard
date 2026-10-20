(() => {
  "use strict";
  window.createCrmChannelSettings=({api,esc,icon,icons,modal,error,onDone})=>{
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
      }icons();
    }
    return {mount};
  };
})();
