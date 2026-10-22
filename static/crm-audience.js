"use strict";
window.createCrmAudience=function({api,esc,icon,icons}) {
  const reasonLabels={"":"Disponível","Cadastro a conferir":"Cadastro a conferir","Descadastrado":"Descadastrado","Contato repetido":"Contato repetido"};
  const filterLabels={identity:"Cadastro",tier:"Classificação",service_location:"Local de Atendimento",sales:"Vendas",quotes:"Orçamentos",min_spent_cents:"Comprado a partir de",max_spent_cents:"Comprado até",min_sales:"Mínimo de vendas",appointment_status:"Agenda",appointment_from:"Agenda de",appointment_to:"Agenda até",appointment_no_upcoming:"Sem próximo agendamento"};
  const filterValues={capelinha:"Capelinha","belo-horizonte":"Belo Horizonte","sao-paulo":"São Paulo",matched:"Vinculado",new:"Novo",review:"A conferir",gold:"Ouro",silver:"Prata",bronze:"Bronze",unclassified:"Sem classificação",yes:"Com venda",no:"Sem venda",active:"Em aberto",pending:"Pendentes",expired:"Vencidos",lost:"Perdidos",none:"Nenhum",any:"Com agendamento",completed:"Realizado",scheduled:"Agendado",missed:"Não compareceu"};
  const money=n=>(Number(n||0)/100).toLocaleString("pt-BR",{style:"currency",currency:"BRL"});
  const select=(name,label,choices,value)=>`<label>${label}<select name="${name}">${choices.map(([key,text])=>`<option value="${key}" ${String(value??"")===key?"selected":""}>${text}</option>`).join("")}</select></label>`;
  return function mount(target,{doctor,pipelines,initial={},manual=false,editable=true,fixedAudience=null,onCampaign,onBulk,onOpen,onChange=()=>{}}) {
    const original=Array.isArray(fixedAudience)?[...new Set(fixedAudience)]:null;
    let scope={doctor_id:doctor,pipeline_ids:initial.pipeline_ids || (initial.pipeline_id?[initial.pipeline_id]:[]),stage_ids:initial.stage_ids || [],stages_explicit:initial.stages_explicit===true,filters:{...(initial.filters||{})},order_by:initial.order_by || "",order_dir:initial.order_dir || "asc"};
    let all=initial.lead_ids===undefined?!manual:false,chosen=new Set(initial.lead_ids||[]),removed=new Set(initial.exclude_lead_ids||[]),page=1,data=null,request=0,timer;
    const find=id=>target.querySelector(`[data-audience="${id}"]`);
    const options=(key,choices)=>select(key,{identity:"Cadastro",tier:"Classificação",service_location:"Local de Atendimento",sales:"Vendas fechadas",quotes:"Orçamentos",appointment_status:"Agendamentos"}[key],choices,scope.filters[key]);
    const controls=()=>`<div class="audienceSearch"><label>${icon("search")}<input name="search" type="search" maxlength="160" value="${esc(scope.filters.search||"")}" placeholder="Nome, telefone, CPF ou procedimento" aria-label="Buscar destinatários"></label>${editable&&!original?'<button type="button" data-audience="advanced" aria-expanded="false">'+icon("sliders-horizontal")+' Filtros</button>':""}<button type="button" data-audience="refresh" class="icon-button" aria-label="Atualizar destinatários" title="Atualizar destinatários">${icon("refresh-cw")}</button></div>
      ${editable?`<div class="audienceAdvanced" data-audience="filterPanel" hidden><div class="formGrid">
      ${options("service_location",[["","Todos os locais"],["capelinha","Capelinha"],["belo-horizonte","Belo Horizonte"],["sao-paulo","São Paulo"],["pending","Pendente de definição"]])}
      ${options("identity",[["","Todos os cadastros"],["matched","Clínica Experts"],["new","Novos leads"],["review","A conferir"]])}${options("tier",[["","Todas"],["gold","Ouro"],["silver","Prata"],["bronze","Bronze"],["unclassified","Sem classificação"]])}${options("sales",[["","Todas as situações"],["yes","Com venda fechada"],["no","Sem venda fechada"]])}${options("quotes",[["","Todas as situações"],["active","Em aberto"],["pending","Pendentes"],["expired","Vencidos"],["none","Sem pendentes"]])}<label>Total comprado mínimo (R$)<input name="min_spent_cents" type="number" min="0" step="0.01" value="${scope.filters.min_spent_cents==null?"":scope.filters.min_spent_cents/100}"></label><label>Total comprado máximo (R$)<input name="max_spent_cents" type="number" min="0" step="0.01" value="${scope.filters.max_spent_cents==null?"":scope.filters.max_spent_cents/100}"></label>${options("appointment_status",[["","Sem filtro de agenda"],["any","Com agendamento"],["completed","Realizado"],["scheduled","Agendado"],["missed","Não compareceu"],["none","Sem agendamento"]])}<label>Agenda de<input name="appointment_from" type="date" value="${esc(scope.filters.appointment_from||"")}"></label><label>Agenda até<input name="appointment_to" type="date" value="${esc(scope.filters.appointment_to||"")}"></label></div><label class="check"><input name="appointment_no_upcoming" type="checkbox" ${scope.filters.appointment_no_upcoming?"checked":""}>Sem próximo agendamento</label><button type="button" data-audience="clearFilters">${icon("rotate-ccw")} Limpar filtros</button></div>`:""}
      <div class="audienceFunnelTools">${editable&&!original?`<label>Funis<select data-audience="pipeline"><option value="">Todos os funis do doutor</option>${pipelines.map(p=>`<option value="${esc(p.id)}" ${scope.pipeline_ids.length===1&&scope.pipeline_ids[0]===p.id?"selected":""}>${esc(p.name)}</option>`).join("")}</select></label>`:""}<div data-audience="stages" class="audienceStages"></div></div>`;
    target.innerHTML=`<section class="crmAudience">${controls()}<div data-audience="activeFilters" class="activeCrmFilters"></div><div data-audience="results" aria-live="polite"></div></section>`;
    target.querySelector('.audienceAdvanced .formGrid')?.insertAdjacentHTML("afterbegin",select("course_status","Situação Kiwify",[["","Todas"],["paid","Pagamento aprovado"],["pending","Aguardando pagamento"],["refunded","Reembolso / chargeback"],["none","Sem pedido localizado"]],scope.filters.course_status));
    if(!editable)target.querySelector('.audienceSearch').hidden=true;
    const payload=()=>original?{doctor_id:doctor,lead_ids:original.filter(id=>all?!removed.has(id):chosen.has(id))}:({...scope,filters:{...scope.filters},...(all?{exclude_lead_ids:[...removed]}:{lead_ids:[...chosen]})});
    const selectedCount=()=>original?payload().lead_ids.length:all?Math.max(0,(data?.selected||0)-removed.size):chosen.size;
    const courseOnly=()=>{const visible=(data?.pipelines || pipelines).filter(p=>!scope.pipeline_ids.length || scope.pipeline_ids.includes(p.id));return visible.length && visible.every(p=>p.course_key);};
    const financialCells=l=>{
      if(l.course_key){const d=l.course_data || {};return `<td>${d.state==="ready"?money(d.paid_cents):"—"}<small>${Number(d.paid_count || 0)} aprovados · Kiwify</small></td><td>${money(l.value_cents)}<small>${d.pending_count?`${d.pending_count} pagamento(s) pendente(s)`:d.refunded_count?`${d.refunded_count} reembolso(s)`:"Oportunidade · não é pagamento"}</small></td>`;}
      const h=l.customer_history || {};return `<td>${h.sales_state==="ready"?money(h.sales_cents):"—"}<small>${h.sales_state==="ready"?`${h.sales_count} vendas`:l.experts_status==="new"?"Sem vínculo no Experts":"Histórico a conferir"}</small></td><td>${h.quotes_state==="ready"?money(h.pending_quotes_cents):"—"}</td>`;
    };
    const sortHeader=(key,label)=>{
      const active=scope.order_by===key,next=active?(scope.order_dir==="asc"?"desc":"asc"):(["spent","quotes"].includes(key)?"desc":"asc");
      const action=["spent","quotes"].includes(key)?next==="desc"?"maiores primeiro":"menores primeiro":next==="asc"?"crescente":"decrescente";
      return `<th aria-sort="${active?scope.order_dir==='asc'?'ascending':'descending':'none'}"><button type="button" class="audienceSort" data-audience-sort="${key}" title="Ordenar ${label}: ${action}" aria-label="Ordenar ${label}: ${action}"><span>${label}</span>${icon(active?scope.order_dir==='asc'?'arrow-up':'arrow-down':'arrow-up-down')}</button></th>`;
    };
    function stages() {
      if(original){find("stages").innerHTML="";return;}
      const open=find("stages").querySelector('details')?.open;
      const catalog=data?.pipelines || pipelines;
      const visible=catalog.filter(p=>!scope.pipeline_ids.length || scope.pipeline_ids.includes(p.id));
      find("stages").innerHTML=`<details ${open?'open':''}><summary>Fases <span>${scope.stages_explicit?scope.stage_ids.length:"Todas"}</span>${icon("chevron-down")}</summary><div class="audienceStageMenu"><div class="audienceStageCommands"><button type="button" data-audience="allStages">Todas</button><button type="button" data-audience="noStages">Nenhuma</button><button type="button" data-audience="openStages">Em negociação</button></div>${visible.map(p=>`<fieldset><legend>${esc(p.name)} <b>${p.count??""}</b></legend>${p.stages.map(s=>`<label class="check"><input type="checkbox" data-stage-id="${esc(s.id)}" ${!scope.stages_explicit || scope.stage_ids.includes(s.id)?"checked":""}><span>${esc(s.name)}</span><b>${s.count??""}</b></label>`).join("")}</fieldset>`).join("")}</div></details>`;
      if(find("pipeline"))for(const opt of find("pipeline").options){const p=catalog.find(p=>p.id===opt.value);if(p)opt.textContent=`${p.name} (${p.count??0})`;}
      icons();
    }
    function result() {
      const count=selectedCount(),total=data.selected;
      target.querySelectorAll('.audienceAdvanced [name]').forEach(input=>{if(input.name!=="search")input.closest("label").hidden=input.name==="course_status"?!courseOnly():Boolean(courseOnly());});
      find("results").innerHTML=`<div class="audienceTotals"><div><strong>${total}</strong><span>Oportunidades encontradas</span></div><div><strong>${data.recipients}</strong><span>Contatos aptos no filtro</span></div><div><strong>${data.excluded}</strong><span>Com impedimento</span></div></div>
        ${data.incomplete?`<p class="inlineWarning">${data.incomplete} cadastro(s) com histórico incompleto para os filtros do Clínica Experts.</p>`:""}
        ${Object.keys(data.reasons).length?`<div class="audienceReasons">${Object.entries(data.reasons).map(([reason,n])=>`<span>${esc(reasonLabels[reason]||reason)} <b>${n}</b></span>`).join("")}</div>`:""}
        <div class="audienceSelection"><label class="check"><input type="checkbox" data-audience="selectPage" aria-label="Selecionar página" ${data.items.length&&data.items.every(l=>all?!removed.has(l.id):chosen.has(l.id))?"checked":""}>Página atual</label><strong data-audience="selectionCount">${count} selecionadas</strong><button type="button" data-audience="selectAll" ${!(original?original.length:total)?"disabled":""}>${original?`Selecionar todos da campanha (${original.length})`:`Selecionar todos do filtro (${total})`}</button><button type="button" data-audience="clearSelection" class="icon-button" title="Limpar seleção" aria-label="Limpar seleção">${icon("x")}</button>${onCampaign?`<button type="button" class="primary" data-audience="campaign" ${!count?"disabled":""}>${icon("send")} Criar disparo</button>`:""}</div>
        <div class="audienceTableScroll"><table class="audienceTable"><thead><tr><th><span class="visuallyHidden">Selecionar</span></th>${sortHeader("name","Cliente")}${sortHeader("pipeline","Funil / fase")}${sortHeader(courseOnly()?"course_paid":"spent",courseOnly()?"Pago · Kiwify":"Total comprado")}${sortHeader(courseOnly()?"course_value":"quotes",courseOnly()?"Valor da oportunidade":"Orçamentos pendentes")}${sortHeader("eligibility","Envio")}</tr></thead><tbody>${data.items.map(l=>{const p=data.pipelines.find(p=>p.id===l.pipeline_id),stage=p?.stages.find(s=>s.id===l.stage_id);return `<tr><td><input type="checkbox" data-lead-id="${esc(l.id)}" aria-label="Selecionar ${esc(l.name)}" ${all?!removed.has(l.id)?"checked":"":chosen.has(l.id)?"checked":""}></td><td>${onOpen?`<button type="button" data-open-lead="${esc(l.id)}">${esc(l.name)}</button>`:`<strong>${esc(l.name)}</strong>`}<small>+${esc(l.phone)}</small></td><td><span>${esc(p?.name)}</span><small>${esc(stage?.name)}</small></td>${financialCells(l)}<td><span class="audienceEligibility ${l.campaign_reason?"blocked":"ready"}">${icon(l.campaign_reason?"circle-alert":"circle-check")}${esc(reasonLabels[l.campaign_reason]||l.campaign_reason||"Apto")}</span></td></tr>`;}).join("") || `<tr><td colspan="6" class="emptyState">Nenhuma oportunidade no CRM para este filtro.</td></tr>`}</tbody></table></div>
        <footer class="campaignPagination"><button type="button" data-audience="prev" class="icon-button" aria-label="Página anterior de clientes" ${page===1?"disabled":""}>${icon("chevron-left")}</button><span>Página ${page} de ${Math.max(1,Math.ceil(total/50))} · ${data.duplicates} repetições</span><button type="button" data-audience="next" class="icon-button" aria-label="Próxima página de clientes" ${data.has_more?"":"disabled"}>${icon("chevron-right")}</button></footer>`;
      if(onBulk)find("results").querySelector(".audienceSelection").insertAdjacentHTML("beforeend",`<button type="button" data-audience="bulkMove" ${!count?"disabled":""}>${icon("arrow-right")} Mover etapa</button><button type="button" data-audience="bulkTransfer" ${!count?"disabled":""}>${icon("arrow-right-left")} Transferir</button><button type="button" data-audience="bulkDelete" ${!count?"disabled":""}>${icon("trash-2")} Excluir</button>`);
      icons();onChange(payload(),{count,data});
    }
    async function refresh(reset=false) {
      if(reset){page=1;if(!original){all=!manual;chosen.clear();removed.clear();}}
      const current=++request;find("results").setAttribute("aria-busy","true");
      find("activeFilters").innerHTML=editable?Object.entries(scope.filters).filter(([key,value])=>filterLabels[key] && value!=="" && value!=null && value!==false).map(([key,value])=>`<button type="button" class="filterChip" data-remove-audience-filter="${key}">${esc(filterLabels[key])}${value===true?"":`: ${esc(key.endsWith('_cents')?money(value):filterValues[value]||value)}`}${icon("x")}</button>`).join(""):"";
      find("activeFilters").hidden=!find("activeFilters").innerHTML;
      onChange(null,{loading:true});
      find("results").querySelectorAll('input,button').forEach(input=>input.disabled=true);
      try {
        const next=await api("campaign-candidates",{...scope,...(original?{lead_ids:original}:{}),page},{doctor});
        if(current!==request || !target.isConnected)return;
        data=next;stages();result();
      } catch(err) {if(current===request && target.isConnected){data=null;onChange(null,{error:true});find("results").innerHTML=`<p role="alert" class="inlineWarning">${esc(err.message)}</p><button type="button" data-audience="refresh">${icon("refresh-cw")} Tentar novamente</button>`;}}
      finally{if(current===request && target.isConnected)find("results").setAttribute("aria-busy","false");}
    }
    target.onclick=e=>{
      const b=e.target.closest("button");if(!b)return;
      if(b.dataset.audienceSort){const key=b.dataset.audienceSort;scope.order_dir=scope.order_by===key?(scope.order_dir==="asc"?"desc":"asc"):["spent","quotes"].includes(key)?"desc":"asc";scope.order_by=key;page=1;refresh();return;}
      if(b.dataset.openLead)return onOpen?.(b.dataset.openLead);
      if(b.dataset.removeAudienceFilter){const key=b.dataset.removeAudienceFilter;delete scope.filters[key];const input=target.querySelector(`[name="${key}"]`);if(input){if(input.type==="checkbox")input.checked=false;else input.value="";}refresh(true);return;}
      const action=b.dataset.audience;
      if(action==="advanced"){const open=find("filterPanel").hidden;find("filterPanel").hidden=!open;b.setAttribute("aria-expanded",String(open));}
      if(action==="refresh")refresh();
      if(action==="prev" || action==="next"){page+=action==="prev"?-1:1;refresh();}
      if(action==="selectAll"){all=true;chosen.clear();removed.clear();result();}
      if(action==="clearSelection"){all=false;chosen.clear();removed.clear();result();}
      if(action==="campaign")onCampaign?.(payload());
      if(action==="bulkMove" || action==="bulkDelete")onBulk?.(action==="bulkMove"?"move":"delete",payload());
      if(action==="bulkTransfer")onBulk?.("transfer",payload());
      if(["allStages","noStages","openStages"].includes(action)){
        scope.stages_explicit=action!=="allStages";
        scope.stage_ids=action==="openStages"?(data?.pipelines||pipelines).filter(p=>!scope.pipeline_ids.length||scope.pipeline_ids.includes(p.id)).flatMap(p=>p.stages.filter(s=>s.kind==="open").map(s=>s.id)):[];
        refresh(true);
      }
      if(action==="clearFilters"){scope.filters={};target.querySelectorAll('[name]').forEach(input=>{if(input.type==="checkbox")input.checked=false;else input.value="";});refresh(true);}
    };
    target.onchange=e=>{
      const input=e.target;
      if(input.dataset.leadId){if(all)input.checked?removed.delete(input.dataset.leadId):removed.add(input.dataset.leadId);else input.checked?chosen.add(input.dataset.leadId):chosen.delete(input.dataset.leadId);result();}
      else if(input.dataset.audience==="selectPage"){for(const l of data.items){if(all)input.checked?removed.delete(l.id):removed.add(l.id);else input.checked?chosen.add(l.id):chosen.delete(l.id);}result();}
      else if(input.dataset.audience==="pipeline"){scope.pipeline_ids=input.value?[input.value]:[];scope.stage_ids=[];scope.stages_explicit=false;refresh(true);}
      else if(input.dataset.stageId){scope.stages_explicit=true;scope.stage_ids=[...target.querySelectorAll('[data-stage-id]:checked')].map(i=>i.dataset.stageId);refresh(true);}
      else if(input.name && input.name!=="search"){
        if(input.type==="checkbox")scope.filters[input.name]=input.checked;
        else if(input.value!=="")scope.filters[input.name]=input.name.endsWith("_cents")?Math.round(Number(input.value)*100):input.value;
        else delete scope.filters[input.name];
        refresh(true);
      }
    };
    target.oninput=e=>{if(e.target.name==="search"){scope.filters.search=e.target.value.trim();clearTimeout(timer);timer=setTimeout(()=>refresh(true),300);}};
    target.onkeydown=e=>{if(e.target.name==="search" && e.key==="Enter"){e.preventDefault();clearTimeout(timer);refresh(true);}};
    stages();refresh();
    return {payload,refresh,count:selectedCount,destroy:()=>{request++;clearTimeout(timer);target.onclick=target.onchange=target.oninput=target.onkeydown=null;}};
  };
};
