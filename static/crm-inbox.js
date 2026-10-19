/* One conversation per doctor, sender number and contact. No remote read receipts. */
"use strict";
window.createCrmInbox = function ({state,api,esc,icon,icons,patientLabel,money,date,profile,moveLead,appointmentBadge,customerValueBadge,customerValuePanel,pendingQuotesValue,refreshPatient,patientRefreshMessage,patientRefreshes,voice}) {
  let root=null, owner="", selected=null, listSeq=0, threadSeq=0, filter="all", channel="", list=[], messages=[], hasOlder=false;
  let threadData=null, sending=false, replyChannel="", openSeq=0, closing="", selectedRecord=null, drafts=new Map();
  const $=id=>root?.querySelector(`#${id}`);
  const key=s=>`${owner}:${s.channel_id}:${s.contact_id}`;
  const initials=name=>String(name).split(/\s+/).slice(0,2).map(n=>n[0]).join("").toUpperCase();
  const time=value=>new Date(value*1000).toLocaleTimeString("pt-BR",{hour:"2-digit",minute:"2-digit"});
  const labels={received:"Recebida",queued:"Na fila",sending:"Em envio",sent:"Aceita pelo conector",unknown:"Sem confirmação · não reenviar",cancelled:"Cancelada",skipped:"Não enviada"};
  const leadFor=()=>selected && (state.data.leads.find(l=>l.id===selected.lead_id) || state.data.leads.find(l=>l.contact_id===selected.contact_id) || selectedRecord);
  const senderName=()=>String(state.session?.user.nome || "").replace(/[*_~`\u0000-\u001f\u007f]/g," ").trim().replace(/\s+/g," ");
  const messageText=m=>m.sender_name && m.body.startsWith(`*${m.sender_name}*\n\n`)?m.body.slice(m.sender_name.length+4):m.body;
  function chatError(message="") {if($("chatError")){$("chatError").textContent=message;$("chatError").hidden=!message;}}
  function mount(target) {
    const changed=owner!==state.doctor;
    if(changed){owner=state.doctor;selected=null;selectedRecord=null;filter="all";channel="";messages=[];threadData=null;threadSeq++;}
    if(root===target && $("inboxWorkspace") && !changed){refreshList();if(selected){drawSenderOptions();drawInfo();refreshThread();}return;}
    root=target;listSeq++;threadSeq++;
    root.innerHTML=`<div class="inboxWorkspace" id="inboxWorkspace"><aside class="conversationList" aria-label="Conversas"><div class="inboxListHead"><h2>Ponto de atendimento</h2><span id="inboxCount"></span></div><label class="channelFilter">Número de atendimento<select id="inboxChannel"><option value="">Todos os números</option>${state.data.channels.map(c=>`<option value="${esc(c.id)}" ${channel===c.id?"selected":""}>${esc(c.name)} · +${esc(c.phone)}</option>`).join("")}</select></label><div class="inboxSegments" role="group" aria-label="Filtrar conversas">${[["all","Todas"],["unread","Não lidas"],["waiting","Aguardando"]].map(([id,name])=>`<button data-inbox-filter="${id}" aria-pressed="${filter===id}">${name}<span data-count="${id}">0</span></button>`).join("")}</div><div id="conversationRows" class="conversationRows"></div><p id="inboxLimit" class="connectionNote" hidden></p><p id="inboxError" role="status" hidden></p></aside><section id="chatPane" class="chatPane" aria-label="Chat do cliente"><div class="chatPlaceholder">${icon("messages-square")}<h2>Conversas que conectam</h2><p>Selecione um cliente</p></div></section><aside id="contactPanel" class="contactPanel" aria-label="Ficha comercial" hidden></aside></div>`;
    $("inboxChannel").onchange=()=>{channel=$("inboxChannel").value;refreshList();};
    $("inboxWorkspace").onclick=event=>{
      const b=event.target.closest("button");if(!b)return;
      if(b.dataset.inboxFilter){filter=b.dataset.inboxFilter;root.querySelectorAll("[data-inbox-filter]").forEach(n=>n.setAttribute("aria-pressed",String(n.dataset.inboxFilter===filter)));refreshList();}
      if(b.dataset.thread!==undefined){const item=list[Number(b.dataset.thread)];if(item)select(item);}
      if(b.id==="chatBack"){$("inboxWorkspace").classList.remove("chatActive");}
      if(b.id==="toggleContact"){$("inboxWorkspace").classList.toggle("showContact");b.setAttribute("aria-expanded",String($("inboxWorkspace").classList.contains("showContact")));}
      if(b.id==="openProfile"){const l=leadFor();if(l)profile(l.id);}
      if(b.dataset.commercial){const l=leadFor();if(l)profile(l.id,b.dataset.commercial);}
      if(b.id==="olderMessages")refreshThread(true);
      if(b.id==="closeConversation")closeConversation();
      if(b.id==="voiceCall"){const l=leadFor();if(l)voice.open(l);}
    };
    refreshList();if(selected)select(selected);icons();
  }
  async function refreshList() {
    if(!root)return;
    const request=++listSeq,doc=owner;
    try {
      const data=await api("inbox",undefined,{doctor:doc,channel,kind:filter,search:state.search});
      if(!root || request!==listSeq || doc!==owner)return;
      list=data.conversations;
      $("inboxCount").textContent=`${data.counts.all} conversas`;
      root.querySelectorAll("[data-count]").forEach(n=>n.textContent=data.counts[n.dataset.count]);
      $("inboxLimit").hidden=data.total<=list.length;$("inboxLimit").textContent=`Exibindo ${list.length} de ${data.total}. Refine a busca.`;
      $("inboxError").hidden=true;drawList();
    }catch(e){if(root && request===listSeq){$("inboxError").textContent=e.message;$("inboxError").hidden=false;}}
  }
  function drawList() {
    if(!root)return;
    $("conversationRows").innerHTML=list.map((c,i)=>`<button class="conversationRow ${selected && c.channel_id===selected.channel_id && c.contact_id===selected.contact_id?"active":""}" data-thread="${i}"><span class="contactAvatar">${esc(initials(c.name))}</span><span class="conversationPreview"><span class="conversationName"><strong>${esc(c.name)}</strong><small>${date(c.created_at)}</small></span><span class="conversationSnippet">${c.direction==="out"?icon(c.status==="sent"?"check":"clock-3"):""}${esc(messageText(c))}</span><span class="conversationMeta">${esc(c.channel_name)} ${c.unread?"· Aguardando resposta":""}</span></span>${c.unread?`<b class="unreadCount" aria-label="${c.unread} mensagens não lidas">${c.unread}</b>`:""}</button>`).join("") || '<div class="inboxEmpty">Nenhuma conversa neste filtro</div>';
    icons();
  }
  async function openLead(l) {
    const doc=owner, request=++openSeq;
    channel="";if($("inboxChannel"))$("inboxChannel").value="";
    try {
      const data=await api("lead",undefined,{doctor:doc,id:l.id});
      if(!root || doc!==owner || request!==openSeq)return;
      const sender=data.preferred_channel_id || data.messages[0]?.channel_id || state.data.channels.find(c=>c.state==="open")?.id || state.data.channels[0]?.id;
      if(!sender){$("chatPane").innerHTML='<div class="chatPlaceholder"><h2>Nenhum número cadastrado</h2><p>Cadastre um número em Números para abrir o atendimento.</p></div>';$("inboxWorkspace").classList.add("chatActive");return;}
      select({contact_id:l.contact_id,channel_id:sender,lead_id:l.id,_lead:data.lead});
    }catch(e){if(root && request===openSeq){$("inboxError").textContent=e.message;$("inboxError").hidden=false;}}
  }
  function select(item) {
    openSeq++;
    const previous=selected?.contact_id===item.contact_id?leadFor():null;
    selected={contact_id:item.contact_id,channel_id:item.channel_id,lead_id:item.lead_id};
    selectedRecord=item._lead || state.data.leads.find(l=>l.id===item.lead_id) || previous;
    replyChannel=selected.channel_id;threadSeq++;messages=[];threadData=null;sending=false;
    const l=leadFor(), ch=state.data.channels.find(c=>c.id===selected.channel_id), draft=drafts.get(key(selected));
    $("inboxWorkspace").classList.add("chatActive");$("inboxWorkspace").classList.remove("showContact");
    $("chatPane").innerHTML=`<header class="chatHeader"><button id="chatBack" class="icon-button" aria-label="Voltar às conversas" title="Voltar às conversas">${icon("arrow-left")}</button><span class="contactAvatar">${esc(initials(l?.name || "Cliente"))}</span><div class="chatIdentity"><h2 id="chatName">${esc(l?.name || "Cliente")}</h2><span>${esc(ch?.name || "WhatsApp")} · +${esc(ch?.phone || "")}</span></div><button id="toggleContact" class="icon-button" aria-label="Ficha comercial" title="Ficha comercial" aria-expanded="false">${icon("panel-right")}</button></header><div class="messageTimeline" id="messageTimeline" tabindex="0" aria-label="Histórico de mensagens"><p class="inboxEmpty">Carregando conversa…</p></div><div class="chatComposer"><div class="replyOptions"><label>Responder por<select id="replyChannel"></select></label><div class="replySignature"><span>Atendente</span><strong id="replySignature">${esc(senderName())}</strong></div></div><p id="replyState" class="replyState"></p><p id="chatError" role="alert" hidden></p><form id="replyForm"><textarea id="replyBody" name="body" rows="2" maxlength="3000" aria-label="Mensagem para o cliente" placeholder="Escreva uma mensagem…">${esc(draft?.body || "")}</textarea><button id="sendReply" type="submit" class="primary icon-button" aria-label="Enviar mensagem" title="Enviar mensagem" disabled>${icon("send")}</button></form></div>`;
    $("chatPane").querySelector(".chatHeader").insertAdjacentHTML("afterend",`<div class="conversationActions"><span id="conversationStatus" role="status">Carregando…</span><button id="closeConversation" title="Fechar conversa sem responder" disabled>${icon("check-check")} Fechar conversa</button></div>`);
    if(l)$("toggleContact").insertAdjacentHTML("beforebegin",`<button id="voiceCall" class="icon-button" aria-label="Ligar para ${esc(l.name)}" title="Ligar para cliente">${icon("phone")}</button>`);
    drawSenderOptions();
    $("replyChannel").onchange=()=>{replyChannel=$("replyChannel").value;chatError();syncSend();};
    $("replyBody").oninput=()=>{drafts.set(key(selected),{body:$("replyBody").value,requests:{}});syncSend();};
    $("replyForm").onsubmit=send;
    drawInfo();drawList();refreshThread();icons();
    if(!selectedRecord && selected.lead_id){
      const signature=key(selected),doc=owner;
      api("lead",undefined,{doctor:doc,id:selected.lead_id}).then(data=>{if(root && selected && signature===key(selected)){selectedRecord=data.lead;drawInfo();}}).catch(e=>{if(root && selected && signature===key(selected))chatError(e.message);});
    }
    if(matchMedia("(max-width:760px)").matches)$("chatPane").scrollIntoView({block:"start",behavior:matchMedia("(prefers-reduced-motion:reduce)").matches?"instant":"smooth"});
  }
  function drawSenderOptions() {
    if(!$("replyChannel"))return;
    if(!state.data.channels.some(c=>c.id===replyChannel))replyChannel=selected.channel_id;
    $("replyChannel").innerHTML=state.data.channels.map(c=>`<option value="${esc(c.id)}" ${c.id===replyChannel?"selected":""}>${esc(c.name)} · +${esc(c.phone)}${c.id===selected.channel_id?" · conversa atual":""}${c.state!=="open"?" · desconectado":""}</option>`).join("");
  }
  function drawInfo() {
    if(!root || !selected)return;
    const l=leadFor(), p=state.data.pipelines.find(p=>p.id===l?.pipeline_id), panel=$("contactPanel");
    $("chatPane").querySelector(".chatCustomerTier")?.remove();
    panel.hidden=false;
    panel.innerHTML=l?`<div class="contactSummary"><span class="eyebrow">FICHA COMERCIAL</span><h3>${esc(l.name)}</h3><p>+${esc(l.phone)}</p><span class="identityTag ${l.experts_status=== "matched"?"matched":""}">${icon(l.experts_status==="matched"?"user-check":"user-round")}${esc(patientLabel(l.experts_status))}</span></div><div class="contactOpportunity"><span class="eyebrow">${esc(p?.name)}</span><h3>${esc(l.title)}</h3><strong class="opportunityValue">${money(l.value_cents)}</strong><form id="quickStage"><label>Fase da jornada<select name="stage_id">${(p?.stages || []).map(s=>`<option value="${s.id}" ${s.id===l.stage_id?"selected":""}>${esc(s.name)}</option>`).join("")}</select></label><button type="submit">${icon("route")} Mover fase</button></form><p class="nextContact">${icon("calendar-days")} Próximo contato · ${date(l.next_action)}</p></div><button id="openProfile">${icon("notebook-pen")} Ficha e histórico</button>`:'<p class="connectionNote">Conversa sem oportunidade no funil.</p>';
    if(l)panel.querySelector(".opportunityValue").outerHTML=pendingQuotesValue(l);
    if(l){const calls=document.createElement("button");calls.dataset.commercial="calls";calls.innerHTML=`${icon("phone-call")} Ligações e gravações`;panel.append(calls);}
    if($("quickStage"))$("quickStage").onsubmit=async e=>{e.preventDefault();const id=new FormData(e.currentTarget).get("stage_id"),b=e.currentTarget.querySelector("button");b.disabled=true;try{await moveLead(l,id);}catch(err){chatError(err.message);}finally{b.disabled=false;}};
    if(l){panel.querySelector(".contactSummary").insertAdjacentHTML("beforeend",customerValuePanel(l.customer_value));$("chatName").parentElement.insertAdjacentHTML("beforeend",`<span class="chatCustomerTier">${customerValueBadge(l.customer_value)}</span>`);const booking=document.createElement("div");booking.className="contactAppointment";booking.innerHTML=appointmentBadge(l.appointment);panel.querySelector("#openProfile").before(booking);const shortcuts=document.createElement("div");shortcuts.className="commercialShortcuts";shortcuts.innerHTML=`<button data-commercial="sales">${icon("shopping-bag")} Vendas fechadas</button><button data-commercial="quotes">${icon("file-clock")} Orçamentos pendentes</button><button data-commercial="appointments">${icon("calendar-days")} Agendamentos</button>`;panel.append(shortcuts);}
    if(l){
      const controls=document.createElement("div");controls.className="patientRefreshControls";
      controls.innerHTML=`<button type="button" id="refreshPatient" title="Buscar novamente no Clínica Experts" ${patientRefreshes.has(l.id)?"disabled":""}>${icon("refresh-cw")} Atualizar contato</button><p id="patientRefreshState" role="status" hidden></p>`;
      panel.querySelector(".contactSummary").after(controls);
      $("refreshPatient").onclick=async()=>{
        const doc=owner,button=$("refreshPatient"),status=$("patientRefreshState");
        try {
          const updated=await refreshPatient(l,button,status);
          if(updated && root && owner===doc && leadFor()?.id===l.id){
            selectedRecord=updated;drawInfo();refreshThread();refreshList();
            $("patientRefreshState").hidden=false;$("patientRefreshState").textContent=patientRefreshMessage(updated);
          }
        }catch(e){if(root && owner===doc && leadFor()?.id===l.id){$("patientRefreshState").hidden=false;$("patientRefreshState").textContent=e.message;}}
      };
    }
    icons();
  }
  function syncSend() {
    if(!$("sendReply"))return;
    const ch=state.data.channels.find(c=>c.id===replyChannel);
    const available=state.data.sends_enabled && ch?.state==="open" && threadData?.reply_allowed;
    $("sendReply").disabled=sending || !available || !$("replyBody").value.trim();
    $("replyChannel").disabled=sending;
    $("replyState").textContent=!state.data.sends_enabled?"Respostas manuais indisponíveis no servidor":ch?.state!=="open"?"Número desconectado":!threadData?.reply_allowed?"Envio bloqueado · confira a autorização de contato":"Atendimento por texto";
    syncResolution();
  }
  function syncResolution() {
    if(!$("closeConversation"))return;
    const pending=threadData?.unread || 0;
    $("closeConversation").disabled=sending || closing===key(selected) || !pending || !threadData?.inbound_cursor;
    $("conversationStatus").textContent=!threadData?"Carregando…":pending?`${pending} ${pending===1?"mensagem não lida":"mensagens não lidas"}`:threadData.resolution==="closed"?"Conversa fechada":threadData.resolution==="replied"?"Respondida":"Sem mensagens pendentes";
    $("conversationStatus").classList.toggle("pending",pending>0);
  }
  async function closeConversation() {
    if(!selected || $("closeConversation").disabled)return;
    const doc=owner,target={...selected},signature=key(target),cursor=threadData.inbound_cursor;
    closing=signature;syncResolution();chatError();
    try {
      await api("conversation-close",{doctor_id:doc,channel_id:target.channel_id,contact_id:target.contact_id,cursor});
      if(root && selected && signature===key(selected)){await refreshThread();refreshList();}
    }catch(e){if(root && selected && signature===key(selected))chatError(e.message);}
    finally{if(closing===signature)closing="";if(root && selected && signature===key(selected))syncResolution();}
  }
  async function refreshThread(older=false) {
    if(!root || !selected)return;
    const request=++threadSeq, doc=owner, target={...selected}, signature=key(target);
    try {
      const data=await api("conversation",undefined,{doctor:doc,channel:target.channel_id,contact:target.contact_id,before:older?messages[0]?.seq || 0:0});
      if(!root || request!==threadSeq || doc!==owner || !selected || signature!==key(selected))return;
      threadData={...data,inbound_cursor:Math.max(threadData?.inbound_cursor || 0,data.inbound_cursor)};hasOlder=older?data.has_older:(messages.length>80?hasOlder:data.has_older);
      const combined=new Map(messages.map(m=>[m.id,m]));data.messages.forEach(m=>combined.set(m.id,m));
      const next=[...combined.values()].sort((a,b)=>a.seq-b.seq);
      const timeline=$("messageTimeline"), nearBottom=timeline.scrollHeight-timeline.scrollTop-timeline.clientHeight<90, height=timeline.scrollHeight, scroll=timeline.scrollTop;
      if(JSON.stringify(messages)!==JSON.stringify(next) || !timeline.querySelector(".chatMessages")){
        messages=next;
        let day="";
        timeline.innerHTML=`${hasOlder?'<button id="olderMessages" class="olderMessages">Mensagens anteriores</button>':""}<div class="chatMessages">${messages.map(m=>{
          const d=new Date(m.created_at*1000).toLocaleDateString("pt-BR"), divider=day!==d?`<div class="chatDay">${d}</div>`:"";day=d;
          return `${divider}<article class="chatBubble ${m.direction} ${m.status}">${m.sender_name?`<strong class="messageAuthor">${esc(m.sender_name)}</strong>`:""}<p>${esc(messageText(m))}</p><small>${time(m.created_at)} ${m.direction==="out"?`<span title="${esc(labels[m.status] || m.status)}">${icon(m.status==="sent"?"check":m.status==="unknown"?"circle-alert":"clock-3")}${esc(labels[m.status] || m.status)}</span>`:""}</small></article>`;
        }).join("") || '<p class="inboxEmpty">Nenhuma mensagem neste número</p>'}</div>`;
        if(older)timeline.scrollTop=scroll+timeline.scrollHeight-height;else if(nearBottom || !height)timeline.scrollTop=timeline.scrollHeight;
        icons();
      }
      $("chatName").textContent=data.contact.name;
      $("chatName").nextElementSibling.textContent=`${data.channel.name} · Cliente +${data.reply_phone}`;
      syncSend();
    }catch(e){if(root && request===threadSeq)chatError(e.message);}
  }
  async function send(event) {
    event.preventDefault();if(sending || $("sendReply").disabled || !selected)return;
    const doc=owner,target={...selected},signature=key(target),body=$("replyBody").value.trim(),sender=replyChannel,cursor=threadData?.inbound_cursor || 0,phone=threadData?.reply_phone;
    let draft=drafts.get(signature);if(!draft || draft.body.trim()!==body)draft={body,requests:{}};
    draft.requests ||= {};draft.requests[sender] ||= crypto.randomUUID();
    drafts.set(signature,draft);sending=true;syncSend();chatError();
    try {
      await api("reply",{doctor_id:doc,channel_id:sender,source_channel_id:target.channel_id,contact_id:target.contact_id,cursor,phone,body,request_id:draft.requests[sender]});
      drafts.delete(signature);
      if(root && selected && signature===key(selected)){
        if(sender!==target.channel_id)select({...target,channel_id:sender});
        else{$("replyBody").value="";await refreshThread();}
        refreshList();
      }
    }catch(e){if(root && selected && signature===key(selected))chatError(e.message);}
    finally{if(root && selected && signature===key(selected)){sending=false;syncSend();}}
  }
  function unmount(){root=null;listSeq++;threadSeq++;openSeq++;}
  setInterval(()=>{if(root && !document.hidden && !document.querySelector("dialog[open]")){refreshList();if(selected)refreshThread();}},5000);
  return {mount,unmount,openLead};
};
