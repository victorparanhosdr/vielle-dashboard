/* One conversation per doctor, sender number and contact. No remote read receipts. */
"use strict";
window.createCrmInbox = function ({state,api,esc,icon,icons,patientLabel,money,date,profile,moveLead,appointmentBadge,customerValueBadge,customerValuePanel,pendingQuotesValue,serviceLocationBadge,refreshPatient,patientRefreshMessage,patientRefreshes,voice,avatars}) {
  let root=null, owner="", selected=null, listSeq=0, threadSeq=0, filter="all", channel="", list=[], messages=[], hasOlder=false;
  let threadData=null, sending=false, replyChannel="", openSeq=0, closing="", selectedRecord=null, drafts=new Map(),renderedSearch="",focusFrame=0;
  const syncing=new Set(),syncedAt=new Map();
  let clockOffset=0;
  const $=id=>root?.querySelector(`#${id}`);
  const key=s=>`${owner}:${s.channel_id}:${s.contact_id}`;
  const media=window.createCrmMedia({esc,icon,icons,onError:chatError,onBusy:()=>syncSend(),onChange:value=>{
    if(!selected)return;const signature=key(selected),draft=drafts.get(signature) || {body:$("replyBody")?.value || "",requests:{}};
    draft.media=value;draft.requests={};drafts.set(signature,draft);syncSend();
  }});
  const chatSearch=window.createCrmChatSearch({api,esc,icon,icons,onResult:seq=>{messages=[];hasOlder=false;refreshThread(false,seq);},onClose:()=>{messages=[];hasOlder=false;refreshThread();}});
  const templates=window.createCrmMessageTemplates({api,esc,icon,icons,onError:chatError,getContext:()=>({name:threadData?.contact.name || leadFor()?.name || "",doctor:state.data.doctors.find(d=>d.id===owner)?.name || ""})});
  function conversationFacts(c) {
    const h=c.customer_history || {},ready=h.quotes_state==="ready" && Number.isSafeInteger(h.pending_quotes_cents);
    const detail=`${h.pending_quotes_count || 0} orçamento(s) pendente(s) · abertos e vencidos · Clínica Experts · ${money(h.pending_quotes_cents)} · ${c.customer_value?.doctor_name || "Doutor selecionado"}`;
    const quote=ready && h.pending_quotes_count?`<span class="conversationQuote" title="${esc(detail)}" aria-label="${esc(detail)}">${icon("file-clock")}<b>${money(h.pending_quotes_cents)}</b></span>`:!ready && h.quotes_state!=="unlinked"?`<span class="conversationQuote pending" title="Orçamentos do Clínica Experts a conferir" aria-label="Orçamentos a conferir">${icon("circle-help")}</span>`:"";
    return `<span class="conversationFacts">${customerValueBadge(c.customer_value)}${serviceLocationBadge(c)}${quote}</span>`;
  }
  const time=value=>new Date(value*1000).toLocaleTimeString("pt-BR",{hour:"2-digit",minute:"2-digit"});
  const labels={received:"Recebida",queued:"Na fila",sending:"Em envio",sent:"Aceita pelo conector",unknown:"Sem confirmação · não reenviar",cancelled:"Cancelada",skipped:"Não enviada"};
  const leadFor=()=>selected && (state.data.leads.find(l=>l.id===selected.lead_id) || state.data.leads.find(l=>l.contact_id===selected.contact_id) || selectedRecord);
  const senderName=()=>String(state.session?.user.nome || "").replace(/[*_~`\u0000-\u001f\u007f]/g," ").trim().replace(/\s+/g," ");
  const messageText=m=>m.sender_name && m.body===`*${m.sender_name}*`?"":m.sender_name && m.body.startsWith(`*${m.sender_name}*\n\n`)?m.body.slice(m.sender_name.length+4):m.body;
  const messagePreview=m=>messageText(m) || ({audio:"Áudio",document:"Arquivo",image:"Imagem",video:"Vídeo"}[m.media_kind] || "");
  function chatError(message="") {if($("chatError")){$("chatError").textContent=message;$("chatError").hidden=!message;}}
  function waitingTime(seconds){
    seconds=Math.max(0,Math.floor(seconds));const pad=n=>String(n).padStart(2,"0"),h=Math.floor(seconds/3600),m=Math.floor(seconds/60)%60,s=seconds%60;
    return h>=24?`${Math.floor(h/24)}d ${pad(h%24)}h`:h?`${pad(h)}:${pad(m)}:${pad(s)}`:`${pad(m)}:${pad(s)}`;
  }
  function waitingTimer(c){
    const since=Number(c.waiting_since);if(!c.unread || !Number.isFinite(since) || since<=0)return date(c.created_at);
    return `<time class="crmWaitTimer" data-wait-since="${since}" title="Sem resposta desde ${esc(new Date(since*1000).toLocaleString("pt-BR"))}">${icon("clock-3")}<span></span></time>`;
  }
  function updateTimers(){
    if(!root || document.hidden)return;const now=Date.now()/1000+clockOffset;
    root.querySelectorAll("[data-wait-since]").forEach(node=>{const seconds=now-Number(node.dataset.waitSince);node.querySelector("span").textContent=waitingTime(seconds);node.classList.toggle("overdue",seconds>=3600);node.setAttribute("aria-label",`Sem resposta há ${waitingTime(seconds)}`);});
  }
  function focus() {
    cancelAnimationFrame(focusFrame);
    const workspace=$("inboxWorkspace");if(!workspace)return;
    focusFrame=requestAnimationFrame(()=>{focusFrame=0;if(root && workspace===$("inboxWorkspace"))workspace.scrollIntoView({block:"start",behavior:"instant"});});
  }
  function mount(target) {
    const changed=owner!==state.doctor;
    if(changed){media.destroy();chatSearch.destroy();templates.destroy();owner=state.doctor;selected=null;selectedRecord=null;filter="all";channel="";messages=[];threadData=null;threadSeq++;}
    if(root===target && $("inboxWorkspace") && !changed){refreshList();if(selected){drawSenderOptions();drawInfo();refreshThread();}return;}
    media.destroy();chatSearch.destroy();templates.destroy();root=target;listSeq++;threadSeq++;
    root.innerHTML=`<div class="inboxWorkspace" id="inboxWorkspace"><aside class="conversationList" aria-label="Conversas"><div class="inboxListHead"><div><h2>Atendimento</h2><span id="inboxCount"></span></div><button id="inboxNewContact" class="icon-button" aria-label="Nova oportunidade" title="Nova oportunidade">${icon("square-pen")}</button></div><label class="channelFilter">Número de atendimento<select id="inboxChannel"><option value="">Todos os números</option>${state.data.channels.map(c=>`<option value="${esc(c.id)}" ${channel===c.id?"selected":""}>${esc(c.name)} · +${esc(c.phone)}</option>`).join("")}</select></label><div class="inboxSegments" role="group" aria-label="Filtrar conversas">${[["all","Todas"],["unread","Não lidas"],["waiting","Sem resposta"]].map(([id,name])=>`<button data-inbox-filter="${id}" aria-pressed="${filter===id}">${name}<span data-count="${id}">0</span></button>`).join("")}</div><div id="conversationRows" class="conversationRows"></div><p id="inboxLimit" class="connectionNote" hidden></p><p id="inboxError" role="status" hidden></p></aside><section id="chatPane" class="chatPane" aria-label="Chat do cliente"><div class="chatPlaceholder">${icon("messages-square")}<h2>Conversas que conectam</h2><p>Selecione um cliente</p></div></section><aside id="contactPanel" class="contactPanel" aria-label="Ficha comercial" hidden></aside></div>`;
    $("inboxNewContact").onclick=()=>document.getElementById("newLead")?.click();
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
      if(b.id==="syncMessages")syncExternalMessages(true);
      if(b.id==="voiceCall"){const l=leadFor();if(l)voice.open(l);}
      if(b.dataset.loadMedia)loadMedia(b);
    };
    refreshList();if(selected)select(selected);icons();focus();
  }
  async function refreshList() {
    if(!root)return;
    const request=++listSeq,doc=owner;
    try {
      const data=await api("inbox",undefined,{doctor:doc,channel,kind:filter,search:state.search});
      if(!root || request!==listSeq || doc!==owner)return;
      list=data.conversations;
      if(Number.isFinite(data.server_time))clockOffset=data.server_time-Date.now()/1000;
      $("inboxCount").textContent=`${data.counts.all} conversas`;
      root.querySelectorAll("[data-count]").forEach(n=>n.textContent=data.counts[n.dataset.count]);
      $("inboxLimit").hidden=data.total<=list.length;$("inboxLimit").textContent=`Exibindo ${list.length} de ${data.total}. Refine a busca.`;
      $("inboxError").hidden=true;drawList();
    }catch(e){if(root && request===listSeq){$("inboxError").textContent=e.message;$("inboxError").hidden=false;}}
  }
  function drawList() {
    if(!root)return;
    $("conversationRows").innerHTML=list.map((c,i)=>`<button class="conversationRow ${selected && c.channel_id===selected.channel_id && c.contact_id===selected.contact_id?"active":""}" data-tier="${esc(c.customer_value?.tier || "unclassified")}" data-thread="${i}" title="${esc(c.channel_name)}${c.unread?" · Aguardando resposta":""}">${avatars.markup(c.name,c.contact_id,owner,c.channel_id)}<span class="conversationPreview"><span class="conversationName"><strong>${esc(c.name)}</strong><small>${waitingTimer(c)}</small></span><span class="conversationSnippet">${c.direction==="out"?icon(c.status==="sent"?"check":"clock-3"):""}${esc(messagePreview(c))}</span>${conversationFacts(c)}</span>${c.unread?`<b class="unreadCount" aria-label="${c.unread} mensagens não lidas">${c.unread}</b>`:""}</button>`).join("") || '<div class="inboxEmpty">Nenhuma conversa neste filtro</div>';
    icons();updateTimers();
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
    media.destroy();chatSearch.destroy();templates.destroy();renderedSearch="";
    openSeq++;
    const previous=selected?.contact_id===item.contact_id?leadFor():null;
    selected={contact_id:item.contact_id,channel_id:item.channel_id,lead_id:item.lead_id};
    selectedRecord=item._lead || state.data.leads.find(l=>l.id===item.lead_id) || previous;
    replyChannel=selected.channel_id;threadSeq++;messages=[];threadData=null;sending=false;
    const l=leadFor(), ch=state.data.channels.find(c=>c.id===selected.channel_id), draft=drafts.get(key(selected));
    $("inboxWorkspace").classList.add("chatActive");$("inboxWorkspace").classList.remove("showContact");
    $("chatPane").innerHTML=`<header class="chatHeader"><button id="chatBack" class="icon-button" aria-label="Voltar às conversas" title="Voltar às conversas">${icon("arrow-left")}</button>${avatars.markup(l?.name || "Cliente",selected.contact_id,owner,selected.channel_id)}<div class="chatIdentity"><h2 id="chatName">${esc(l?.name || "Cliente")}</h2><span>${esc(ch?.name || "WhatsApp")} · +${esc(ch?.phone || "")}</span></div><button id="toggleChatSearch" class="icon-button" aria-label="Buscar nesta conversa" title="Buscar nesta conversa" aria-expanded="false" aria-controls="chatSearch">${icon("search")}</button><button id="toggleContact" class="icon-button" aria-label="Ficha comercial" title="Ficha comercial" aria-expanded="false">${icon("panel-right")}</button></header><div id="chatSearch" class="chatSearch" role="search" aria-label="Busca no histórico desta conversa" hidden></div><div class="messageTimeline" id="messageTimeline" tabindex="0" aria-label="Histórico de mensagens"><p class="inboxEmpty">Carregando conversa…</p></div><div class="chatComposer"><div class="replyOptions"><label>Responder por<select id="replyChannel"></select></label><div class="replySignature"><span>Atendente</span><strong id="replySignature">${esc(senderName())}</strong></div></div><p id="replyState" class="replyState"></p><p id="chatError" role="alert" hidden></p><div id="mediaPreview" class="mediaPreview" hidden></div><form id="replyForm"><div id="mediaTools"></div><textarea id="replyBody" name="body" rows="2" maxlength="3000" aria-label="Mensagem para o cliente" placeholder="Escreva uma mensagem…">${esc(draft?.body || "")}</textarea><button id="sendReply" type="submit" class="primary icon-button" aria-label="Enviar mensagem" title="Enviar mensagem" disabled>${icon("send")}</button></form></div>`;
    $("chatPane").querySelector(".chatHeader").insertAdjacentHTML("afterend",`<div class="conversationActions"><span id="conversationStatus" role="status">Carregando…</span><button id="syncMessages" class="icon-button" title="Sincronizar mensagens do WhatsApp" aria-label="Sincronizar mensagens do WhatsApp" ${!state.data.gateway_configured || ch?.state!=="open"?"disabled":""}>${icon("refresh-cw")}</button><button id="closeConversation" title="Fechar conversa sem responder" disabled>${icon("check-check")} Fechar conversa</button></div>`);
    if(l)$("toggleContact").insertAdjacentHTML("beforebegin",`<button id="voiceCall" class="icon-button" aria-label="Ligar para ${esc(l.name)}" title="Ligar para cliente">${icon("phone")}</button>`);
    chatSearch.mount($("chatSearch"),$("toggleChatSearch"),{doctor:owner,channel:selected.channel_id,contact:selected.contact_id});
    $("chatPane").querySelector(".replyOptions").insertAdjacentHTML("beforeend",'<div id="messageTemplateTools" class="messageTemplateTools"></div>');
    drawSenderOptions();
    $("replyChannel").onchange=()=>{replyChannel=$("replyChannel").value;chatError();syncSend();};
    $("replyBody").oninput=()=>{drafts.set(key(selected),{...drafts.get(key(selected)),body:$("replyBody").value,requests:{}});syncSend();};
    templates.mount($("messageTemplateTools"),$("replyBody"));
    media.mount($("mediaTools"),$("mediaPreview"),{doctor:owner,channel:selected.channel_id,contact:selected.contact_id},draft?.media || null);
    $("replyForm").onsubmit=send;
    drawInfo();drawList();refreshThread().then(()=>syncExternalMessages());icons();
    if(!selectedRecord && selected.lead_id){
      const signature=key(selected),doc=owner;
      api("lead",undefined,{doctor:doc,id:selected.lead_id}).then(data=>{if(root && selected && signature===key(selected)){selectedRecord=data.lead;drawInfo();}}).catch(e=>{if(root && selected && signature===key(selected))chatError(e.message);});
    }
    if(matchMedia("(max-width:760px)").matches)focus();
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
    if(l){panel.querySelector(".opportunityValue").outerHTML=pendingQuotesValue(l);panel.querySelector(".contactSummary").insertAdjacentHTML("beforeend",serviceLocationBadge(l));}
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
    $("sendReply").disabled=sending || media.busy() || !available || (!$("replyBody").value.trim() && !media.value()) || (media.hasDraft() && !media.value());
    media.availability(available && !sending);
    templates.availability(!sending);
    $("replyBody").maxLength=media.value() && media.value().kind!=="audio"?Math.min(1000,1024-senderName().length-4):3000;
    $("replyBody").placeholder=media.hasDraft()?"Legenda opcional…":"Escreva uma mensagem…";
    $("replyChannel").disabled=sending;
    $("replyState").textContent=!state.data.sends_enabled?"Respostas manuais indisponíveis no servidor":ch?.state!=="open"?"Número desconectado":!threadData?"Carregando conversa…":!threadData.reply_allowed?"Envio bloqueado · contato descadastrado":"";
    syncResolution();
  }
  function syncResolution() {
    if(!$("closeConversation"))return;
    const pending=threadData?.unread || 0;
    $("closeConversation").disabled=sending || closing===key(selected) || !pending || !threadData?.inbound_cursor;
    $("conversationStatus").textContent=!threadData?"Carregando…":pending?`${pending} ${pending===1?"mensagem não lida":"mensagens não lidas"}`:threadData.resolution==="closed"?"Conversa fechada":threadData.resolution==="replied"?"Respondida":"Sem mensagens pendentes";
    $("conversationStatus").classList.toggle("pending",pending>0);
  }
  async function syncExternalMessages(force=false) {
    if(!root || !selected || !state.data.gateway_configured)return;
    const doc=owner,target={...selected},signature=key(target),ch=state.data.channels.find(c=>c.id===target.channel_id);
    if(ch?.state!=="open" || syncing.has(signature) || !force && Date.now()-(syncedAt.get(signature)||0)<300000)return;
    syncing.add(signature);syncedAt.set(signature,Date.now());
    if($("syncMessages"))$("syncMessages").disabled=true;
    try {
      const data=await api("conversation-sync",{doctor_id:doc,channel_id:target.channel_id,contact_id:target.contact_id});
      if(root && selected && signature===key(selected)){
        $("syncMessages").title=data.cached?"Consulta recente · aguarde para consultar novamente":data.limited?"Sincronizados os 200 registros mais recentes disponíveis":"Histórico disponível sincronizado";
        await refreshThread();refreshList();
      }
    }catch(e){if(root && selected && signature===key(selected)){$("syncMessages").title="Sincronização indisponível";if(force)chatError(e.message);}}
    finally{syncing.delete(signature);if(root && selected && signature===key(selected) && $("syncMessages"))$("syncMessages").disabled=false;}
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
  async function refreshThread(older=false,around=0) {
    if(!root || !selected)return;
    const request=++threadSeq, doc=owner, target={...selected}, signature=key(target);
    try {
      const focus=around || chatSearch.target();
      const data=await api("conversation",undefined,{doctor:doc,channel:target.channel_id,contact:target.contact_id,before:!focus && older?messages[0]?.seq || 0:0,around:focus});
      if(!root || request!==threadSeq || doc!==owner || !selected || signature!==key(selected))return;
      threadData={...data,inbound_cursor:Math.max(threadData?.inbound_cursor || 0,data.inbound_cursor)};hasOlder=focus?false:older?data.has_older:(messages.length>80?hasOlder:data.has_older);
      const combined=new Map((focus?[]:messages).map(m=>[m.id,m]));data.messages.forEach(m=>combined.set(m.id,m));
      const next=[...combined.values()].sort((a,b)=>a.created_at-b.created_at || a.seq-b.seq);
      const timeline=$("messageTimeline"), nearBottom=timeline.scrollHeight-timeline.scrollTop-timeline.clientHeight<90, height=timeline.scrollHeight, scroll=timeline.scrollTop;
      const playing=[...timeline.querySelectorAll("audio,video")].some(player=>!player.paused && !player.ended);
      if((!playing || around) && (JSON.stringify(messages)!==JSON.stringify(next) || renderedSearch!==chatSearch.term() || !timeline.querySelector(".chatMessages"))){
        messages=next;renderedSearch=chatSearch.term();
        let day="";
        timeline.innerHTML=`${hasOlder?'<button id="olderMessages" class="olderMessages">Mensagens anteriores</button>':""}<div class="chatMessages">${messages.map(m=>{
          const d=new Date(m.created_at*1000).toLocaleDateString("pt-BR"), divider=day!==d?`<div class="chatDay">${d}</div>`:"";day=d;
          const status=m.origin==="external"?"Enviada externamente":labels[m.status] || m.status;
          return `${divider}<article data-message-seq="${m.seq}" class="chatBubble ${m.direction} ${m.status} ${m.seq===focus?"searchResult":""}">${m.sender_name?`<strong class="messageAuthor">${esc(m.sender_name)}</strong>`:""}${window.crmMediaMarkup(m.media,esc,icon)}${m.media?.kind!=="audio" && messageText(m)?`<p>${chatSearch.highlight(messageText(m))}</p>`:""}<small>${time(m.created_at)} ${m.direction==="out"?`<span title="${esc(status)}">${icon(m.status==="sent"?"check":m.status==="unknown"?"circle-alert":"clock-3")}${esc(status)}</span>`:""}</small></article>`;
        }).join("") || '<p class="inboxEmpty">Nenhuma mensagem neste número</p>'}</div>`;
        if(around)timeline.querySelector(`[data-message-seq="${around}"]`)?.scrollIntoView({block:"center",behavior:"instant"});else if(older)timeline.scrollTop=scroll+timeline.scrollHeight-height;else if(!focus && (nearBottom || !height))timeline.scrollTop=timeline.scrollHeight;
        icons();
      }
      $("chatName").textContent=data.contact.name;
      $("chatName").nextElementSibling.textContent=`${data.channel.name} · Cliente +${data.reply_phone}`;
      syncSend();
    }catch(e){if(root && request===threadSeq)chatError(e.message);}
  }
  async function loadMedia(button){
    if(button.disabled || !selected)return;
    const signature=key(selected),doc=owner,error=button.parentElement.querySelector("[data-media-error]");
    button.disabled=true;error.hidden=true;
    try{await api("media-fetch",{doctor_id:doc,id:button.dataset.loadMedia},{doctor:doc});if(root && selected && signature===key(selected))await refreshThread();}
    catch(e){if(button.isConnected){error.textContent=e.message;error.hidden=false;}}
    finally{if(button.isConnected)button.disabled=false;}
  }
  async function send(event) {
    event.preventDefault();if(sending || $("sendReply").disabled || !selected)return;
    const doc=owner,target={...selected},signature=key(target),body=$("replyBody").value.trim(),attachment=media.value(),sender=replyChannel,cursor=threadData?.inbound_cursor || 0,phone=threadData?.reply_phone;
    let draft=drafts.get(signature);if(!draft || draft.body.trim()!==body)draft={body,requests:{}};
    draft.requests ||= {};draft.requests[sender] ||= crypto.randomUUID();
    drafts.set(signature,draft);sending=true;syncSend();chatError();
    try {
      await api("reply",{doctor_id:doc,channel_id:sender,source_channel_id:target.channel_id,contact_id:target.contact_id,cursor,phone,body,media_id:attachment?.id || "",request_id:draft.requests[sender]});
      drafts.delete(signature);
      if(root && selected && signature===key(selected)){
        if(sender!==target.channel_id)select({...target,channel_id:sender});
        else{chatSearch.close(false);$("replyBody").value="";media.clear();drafts.delete(signature);messages=[];await refreshThread();}
        refreshList();
      }
    }catch(e){if(root && selected && signature===key(selected))chatError(e.message);}
    finally{if(root && selected && signature===key(selected)){sending=false;syncSend();}}
  }
  function unmount(){media.destroy();chatSearch.destroy();templates.destroy();cancelAnimationFrame(focusFrame);focusFrame=0;root=null;listSeq++;threadSeq++;openSeq++;}
  setInterval(()=>{if(root && !document.hidden && !document.querySelector("dialog[open]")){refreshList();if(selected)refreshThread();}},5000);
  setInterval(updateTimers,1000);
  function setFilter(kind){if(!["all","unread","waiting"].includes(kind))return;filter=kind;if(root){root.querySelectorAll("[data-inbox-filter]").forEach(b=>b.setAttribute("aria-pressed",String(b.dataset.inboxFilter===kind)));refreshList();}}
  return {mount,unmount,openLead,focus,setFilter};
};
