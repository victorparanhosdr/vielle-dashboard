"use strict";
window.createCrmFacebook=({state,api,esc,icon,icons,profile})=>{
  let root=null,doctor='',seq=0,data=null,selected='',search='';
  const status={ACTIVE:'Ativa',PAUSED:'Pausada',ARCHIVED:'Arquivada',DELETED:'Excluída',WITH_ISSUES:'A conferir'};
  const active=request=>root?.isConnected && doctor===state.doctor && request===seq;
  async function mount(target){
    if(root===target && doctor===state.doctor)return;
    unmount();root=target;doctor=state.doctor;await load();
  }
  function unmount(){seq++;if(root){root.onclick=null;root.oninput=null;}root=null;data=null;selected='';search='';}
  async function load(sync=false){
    const request=++seq;
    root.innerHTML='<p class="connectionNote" role="status">Carregando integração Facebook…</p>';
    try{
      const result=await api(sync?'facebook-sync':'facebook',sync?{}:undefined,{doctor,campaign:selected});
      if(!active(request))return;data=result;draw();
    }catch(e){if(active(request)){root.innerHTML=`<p role="alert">${esc(e.message)}</p><button data-facebook-retry>${icon('refresh-cw')} Tentar novamente</button>`;root.onclick=()=>load();icons();}}
  }
  function draw(){
    const items=data.campaigns.filter(row=>row.name.toLocaleLowerCase('pt-BR').includes(search.toLocaleLowerCase('pt-BR')));
    const campaign=data.campaigns.find(row=>row.campaign_id===selected);
    root.innerHTML=`<section class="facebookIntegration"><header class="facebookToolbar"><div><strong>Conta Meta ${esc(data.account_id || 'não configurada')}</strong><p>${data.synced_at?'Sincronizada em '+new Date(data.synced_at*1000).toLocaleString('pt-BR'):'Campanhas ainda não sincronizadas'}</p></div><div class="commands"><a class="icon-button" href="/settings.html?clinic=vielle" title="Configurar integração Meta" aria-label="Configurar integração Meta">${icon('settings-2')}</a><button data-facebook-sync ${data.connected?'':'disabled'}>${icon('refresh-cw')} Sincronizar campanhas</button></div></header><div class="facebookMetrics"><span><b>${data.campaigns.length}</b> campanhas</span><span><b>${data.pending}</b> referências sem campanha identificada</span><span>${icon(data.connected?'circle-check':'circle-alert')}${data.connected?'Integração configurada':'Integração pendente'}</span></div><label class="facebookSearch"><span class="visuallyHidden">Buscar campanha</span>${icon('search')}<input type="search" id="facebookCampaignSearch" placeholder="Buscar campanha" value="${esc(search)}" maxlength="160"></label><div class="facebookTableWrap"><table class="facebookCampaignTable"><thead><tr><th>Campanha</th><th>Situação</th><th>Leads identificados</th></tr></thead><tbody>${items.map(row=>`<tr class="${row.campaign_id===selected?'selected':''}"><td><button data-facebook-campaign="${esc(row.campaign_id)}"><strong>${esc(row.name)}</strong><small>${esc(row.campaign_id)}</small></button></td><td>${esc(status[row.status] || row.status || 'Sem situação sincronizada')}</td><td>${row.identified}</td></tr>`).join('') || '<tr><td colspan="3">Nenhuma campanha encontrada.</td></tr>'}</tbody></table></div>${selected?`<section class="facebookCampaignLeads"><header><h2>${esc(campaign?.name || 'Leads da campanha')}</h2><button class="icon-button" data-facebook-clear title="Fechar leads da campanha" aria-label="Fechar leads da campanha">${icon('x')}</button></header>${data.leads.length?`<p class="connectionNote">${data.leads.length===100?'Até 100 oportunidades':'Oportunidades identificadas'} · ${esc(state.data.doctors.find(row=>row.id===doctor)?.name || '')}</p>${data.leads.map(row=>`<button class="facebookLead" data-facebook-lead="${esc(row.id)}"><span><strong>${esc(row.name)}</strong><small>${esc(row.pipeline)} · ${esc(row.stage)}</small></span>${icon('arrow-up-right')}</button>`).join('')}`:'<p class="connectionNote">Nenhum lead identificado para esta campanha e doutor.</p>'}</section>`:''}</section>`;
    root.onclick=event=>{
      if(event.target.closest('[data-facebook-sync]'))load(true);
      const campaignButton=event.target.closest('[data-facebook-campaign]');
      if(campaignButton){selected=campaignButton.dataset.facebookCampaign;load();}
      if(event.target.closest('[data-facebook-clear]')){selected='';draw();}
      const lead=event.target.closest('[data-facebook-lead]');if(lead)profile(lead.dataset.facebookLead);
    };
    root.oninput=event=>{if(event.target.id==='facebookCampaignSearch'){search=event.target.value;const caret=event.target.selectionStart;draw();const input=root.querySelector('#facebookCampaignSearch');input.focus();try{input.setSelectionRange(caret,caret);}catch{}}};
    icons();
  }
  return {mount,unmount};
};
