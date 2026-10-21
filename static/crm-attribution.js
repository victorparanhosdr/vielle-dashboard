"use strict";
window.CrmAttribution=(()=>{
  const labels={utm_source:'Origem',utm_medium:'Mídia',utm_campaign:'Campanha',utm_content:'Conteúdo',utm_term:'Termo',utm_id:'ID da campanha'};
  const keys=Object.keys(labels),clickKeys=['gclid','fbclid','gbraid','wbraid','msclkid','ttclid'];
  function collect(settings={},locationUrl=location.href,referrer=document.referrer){
    if(settings.enabled===false)return {};
    const url=new URL(locationUrl),query=url.searchParams,utm={},click_ids={};
    for(const key of keys){const value=query.get(key);if(value)utm[key]=value.slice(0,250);}
    if(settings.capture_click_ids)for(const key of clickKeys){const value=query.get(key);if(value)click_ids[key]=value.slice(0,500);}
    const safe=value=>{try{const parsed=new URL(value);return ['http:','https:'].includes(parsed.protocol) && !parsed.username && !parsed.password?parsed.origin+parsed.pathname:'';}catch{return '';}};
    return {utm,click_ids,landing_url:url.origin+url.pathname,referrer:safe(referrer)};
  }
  function link(base,params={}){const url=new URL(base);for(const key of keys){url.searchParams.delete(key);if(params[key]?.trim())url.searchParams.set(key,params[key].trim());}return url.href;}
  function fields(data,esc){
    const rows=Object.entries(data?.utm || {}).filter(([key])=>keys.includes(key)),meta=data?.meta;
    const metaLabels={campaign_name:'Campanha na Meta',campaign_id:'ID da campanha',adset_name:'Conjunto de anúncios',adset_id:'ID do conjunto',ad_name:'Anúncio',ad_id:'ID do anúncio',source_id:'Referência recebida',headline:'Título do anúncio',source_url:'Origem do anúncio'};
    const metaRows=meta?Object.entries(metaLabels).filter(([key])=>meta[key] && !(key==='source_id' && meta.ad_id)):[];
    return `${meta && meta.status!=='resolved'?'<p class="connectionNote">Anúncio recebido pelo WhatsApp · campanha ainda não identificada.</p>':''}<dl class="attributionFields">${metaRows.map(([key,label])=>`<div><dt>${esc(label)}</dt><dd>${esc(meta[key])}</dd></div>`).join('')}${rows.map(([key,value])=>`<div><dt>${esc(labels[key])}<small>${esc(key)}</small></dt><dd>${esc(value)}</dd></div>`).join('')}${data?.landing_url?`<div><dt>Página</dt><dd>${esc(data.landing_url)}</dd></div>`:''}${data?.referrer?`<div><dt>Página de origem</dt><dd>${esc(data.referrer)}</dd></div>`:''}${Object.entries(data?.click_ids || {}).map(([key,value])=>`<div><dt>${esc(key)}</dt><dd>${esc(value)}</dd></div>`).join('')}</dl>`;
  }
  function summary(data){return data?.meta?.campaign_name?`Meta Ads · ${data.meta.campaign_name}`:[data?.utm?.utm_source,data?.utm?.utm_campaign].filter(Boolean).join(' · ') || (data?.kind==='whatsapp_ad'?'Anúncio no WhatsApp':'');}
  function response(data,esc){return data?.version?`<section class="responseAttribution"><h3>Rastreamento</h3>${fields(data,esc)}${!Object.keys(data.utm || {}).length?'<p class="connectionNote">Sem UTMs informadas.</p>':''}</section>`:'';}
  function lead(history,esc){
    if(!history?.total)return '';
    const stamp=item=>new Date(item.created_at*1000).toLocaleString('pt-BR');
    const touch=(item,title)=>`<section><h4>${title}</h4><p class="connectionNote">${esc(item.form_name)} · ${stamp(item)}</p>${fields(item.data,esc)}</section>`;
    return `<details class="leadAttribution"><summary>Origem e rastreamento <span>${esc(summary(history.first.data) || history.first.form_name)}</span></summary>${touch(history.first,'Primeiro registro')}${history.total>1?touch(history.last,'Registro mais recente'):''}${history.total>2?`<details class="attributionHistory"><summary>Histórico · ${history.total} registros</summary>${history.items.filter(item=>item.id!==history.first.id && item.id!==history.last.id).map(item=>touch(item,stamp(item))).join('')}</details>`:''}</details>`;
  }
  return {keys,labels,collect,link,fields,response,lead,summary};
})();
