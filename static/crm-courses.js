"use strict";
window.createCrmCourses=({api,esc,icon,icons})=>{
  const money=n=>(Number(n || 0)/100).toLocaleString("pt-BR",{style:"currency",currency:"BRL"});
  const label=l=>{const d=l.course_data || {};return d.state==="ambiguous"?"Vínculo a conferir":d.state==="unavailable"?"Curso online":d.paid_count?"Aluno · pagamento aprovado":d.pending_count?"Pagamento pendente":d.refunded_count?"Reembolso / chargeback":"Interessado";};
  const badge=l=>`<span class="courseBadge" title="${esc(label(l))}">${icon("graduation-cap")}${esc(label(l))}</span>`;
  const compact=l=>{const d=l.course_data || {};return `<span class="conversationFacts">${badge(l)}${d.paid_count?`<span class="conversationQuote" title="Pagamentos aprovados · Kiwify">${money(d.paid_cents)}</span>`:""}</span>`;};
  const panel=l=>{
    const d=l.course_data || {};
    const status=d.state==="ambiguous"?"Mais de uma identidade associada ao telefone. Pedidos não vinculados.":d.state==="unavailable"?"Pedidos Kiwify ainda não disponíveis.":!d.order_count?"Nenhum pedido localizado na base Kiwify deste curso.":"";
    return `<section class="courseSummary">${badge(l)}<div class="courseAmount"><span>Pago · Kiwify</span><strong>${d.state==="ready"?money(d.paid_cents):"—"}</strong></div><dl><div><dt>Pedidos aprovados</dt><dd>${Number(d.paid_count || 0)}</dd></div><div><dt>Aguardando pagamento</dt><dd>${Number(d.pending_count || 0)} · ${money(d.pending_cents)}</dd></div><div><dt>Reembolsos / chargebacks</dt><dd>${Number(d.refunded_count || 0)} · ${money(d.refunded_cents)}</dd></div>${d.net_complete && d.paid_count?`<div><dt>Líquido aprovado</dt><dd>${money(d.net_cents)}</dd></div>`:""}</dl>${status?`<p class="connectionNote">${esc(status)}</p>`:""}${d.partial?'<p class="inlineWarning">Valores parciais: há pedidos sem valor ou moeda confirmados.</p>':""}${d.synced_at?`<small>Base Kiwify · ${esc(new Date(d.synced_at*1000).toLocaleString("pt-BR"))}${d.sync_ok?"":" · última atualização não concluída"}</small>`:""}</section>`;
  };
  const orders=l=>{
    const d=l.course_data || {};
    return `<div class="courseOrders"><h3>Pedidos do curso</h3>${(d.orders || []).map(o=>`<article class="commercialItem"><div class="commercialItemHead"><span class="commercialBadge">${esc(o.status_label)}</span><time>${esc(o.date?o.date.split("-").reverse().join("/"):"Data não informada")}</time><strong>${o.gross_cents==null?"Valor a conferir":money(o.gross_cents)}</strong></div><p>Pedido #${esc(o.id)}</p><p>${esc(o.email)}${o.payment_method?` · ${esc(o.payment_method)}`:""}</p>${o.net_cents!=null?`<small>Líquido: ${money(o.net_cents)}</small>`:""}</article>`).join("") || '<p class="connectionNote">Nenhum pedido vinculado a este contato.</p>'}${d.order_count>100?`<p class="connectionNote">100 de ${d.order_count} pedidos · histórico completo em Institutos.</p>`:""}</div>`;
  };
  async function refresh(l,button,status,onUpdate,active=()=>button.isConnected){
    button.disabled=true;status.hidden=false;status.textContent="Atualizando pedidos Kiwify…";
    try{
      await api("course-refresh",{doctor_id:l.doctor_id,id:l.id});
      for(let attempt=0;attempt<90 && active();attempt++){
        const result=await api("course-data",undefined,{doctor:l.doctor_id,id:l.id});
        if(!active())return;
        if(!result.running){status.textContent=result.message || "Base Kiwify consultada.";onUpdate(result.lead);icons();return;}
        await new Promise(resolve=>setTimeout(resolve,1500));
      }
      if(active())status.textContent="Atualização em andamento. Consulte novamente em instantes.";
    }catch(e){if(active())status.textContent=e.message;}
    finally{if(active())button.disabled=false;}
  }
  return {badge,compact,panel,orders,refresh,label};
};
