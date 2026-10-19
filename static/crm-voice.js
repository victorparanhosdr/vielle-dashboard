/* Calls stay independent of the selected chat; identity comes from the server. */
"use strict";
window.createCrmVoice = function ({api,esc,icon,icons}) {
  const labels={prepared:"Preparada",initiated:"Iniciando",ringing:"Chamando", "in-progress":"Em ligação",completed:"Finalizada",busy:"Ocupado","no-answer":"Não atendeu",failed:"Falha",canceled:"Cancelada",declined:"Não autorizada",unknown:"Resultado a conferir"};
  const recordings={waiting:"Aguardando atendimento",starting:"Iniciando gravação",recording:"Gravando",queued:"Salvando gravação",downloading:"Salvando gravação",stored:"Gravação disponível",absent:"Sem gravação",error:"Gravação a conferir"};
  const outcomes={"":"Selecionar resultado",return:"Retorno combinado",scheduled:"Agendamento realizado",proposal:"Proposta conversada",resolved:"Atendimento resolvido",followup:"Novo contato necessário"};
  const terminal=new Set(["completed","busy","no-answer","failed","canceled","declined","unknown"]);
  let dialog=null,dock=null,device=null,connection=null,current=null,lead=null,config=null,busy=false,recordBusy=false,requestId="",poll=null,seq=0,muted=false;
  const $=id=>dialog?.querySelector(`#${id}`);
  const duration=s=>`${Math.floor((s||0)/60)}:${String((s||0)%60).padStart(2,"0")}`;
  const stamp=s=>new Date(s*1000).toLocaleString("pt-BR",{timeZone:"America/Sao_Paulo",day:"2-digit",month:"2-digit",year:"numeric",hour:"2-digit",minute:"2-digit"});
  const endpoint=(action,payload,query={})=>api(`voice/${action}`,payload,{doctor:lead?.doctor_id,...query});
  const running=()=>busy || Boolean(current && !terminal.has(current.status));
  function callRestriction(item) {
    if(item?.opted_out)return "Contato descadastrado. As ligações para este contato estão bloqueadas.";
    if(item?.experts_status==="review")return "Confira o vínculo do paciente com o Clínica Experts na ficha antes de ligar.";
    if(!/^55[1-9]\d{9,10}$/.test(item?.phone || ""))return "Confira o telefone brasileiro com DDD na ficha antes de ligar.";
    return "";
  }
  function recordingLabel(call) {
    if(call.notice_mode==="staff" && call.recording_status==="waiting")return terminal.has(call.status)?"Sem gravação":"Gravação não iniciada";
    return recordings[call.recording_status] || "A conferir";
  }
  function message(value="") {if($("voiceError")){$("voiceError").textContent=value;$("voiceError").hidden=!value;}}
  function init() {
    if(dialog)return;
    dialog=document.createElement("dialog");dialog.id="voiceDialog";dialog.setAttribute("aria-labelledby","voiceTitle");document.body.append(dialog);
    dock=document.createElement("button");dock.className="voiceDock";dock.hidden=true;dock.onclick=()=>dialog.showModal();document.body.append(dock);
    dialog.addEventListener("close",()=>{dock.hidden=!running();});
    window.addEventListener("beforeunload",event=>{if(running()){event.preventDefault();event.returnValue="";}});
  }
  function render() {
    const active=running(), restriction=callRestriction(lead), valid=config?.enabled && !restriction;
    dialog.innerHTML=`<header class="voiceHead"><span class="voiceSymbol">${icon("phone")}</span><div><p class="eyebrow">TELEFONIA · VIELLE CLINIC</p><h2 id="voiceTitle">${esc(lead.name)}</h2></div><button id="voiceClose" class="icon-button" aria-label="${active?"Minimizar ligação":"Fechar ligação"}" title="${active?"Minimizar ligação":"Fechar"}">${icon(active?"minus":"x")}</button></header>
      <section class="voiceIdentity"><span>${esc(lead.experts_status==="matched"?"Paciente vinculado ao Clínica Experts":"Contato do CRM")}</span><strong>${esc(current?.destination || "+"+lead.phone)}</strong><p id="voiceState" role="status">${esc(current?labels[current.status]:config?.enabled?(restriction?"Ligação indisponível":"Pronto para ligar"):"Aguardando ativação da Twilio")}</p><time id="voiceTimer">${duration(current?.duration)}</time></section>
      ${!config?.enabled?`<div class="voiceChecks">${[["credentials","Credenciais da Twilio"],["public_url","Endereço de conexão"],["number","Número do doutor"],["activation","Ativação das ligações"]].map(([k,label])=>`<div>${icon(config?.checks?.[k]?"circle-check":"circle-dashed")}<span>${label}</span><small>${config?.checks?.[k]?"Pronto":"Pendente"}</small></div>`).join("")}</div>`:""}
      <label class="voiceLine">Ligar pelo número<select id="voiceLine" ${active?"disabled":""}>${(config?.lines || []).map(line=>`<option value="${esc(line.id)}">${esc(line.name)} · ${esc(line.number)}</option>`).join("") || '<option value="">Número não conectado</option>'}</select></label>
      <div class="voicePrivacy">${icon("shield-check")}<span>${(current?.notice_mode || config?.notice_mode)==="staff"?"O atendente avisa e pede a concordância do paciente. A gravação só começa após essa confirmação.":"O paciente confirma a gravação antes da conversa. Áudio e histórico ficam na ficha deste cliente."}</span></div>
      ${active && current?`<p class="voiceRecordingState" role="status">${icon(current.recording_status==="recording"?"audio-lines":"mic-off")} ${esc(recordingLabel(current))}</p>`:""}
      ${active && current?.notice_mode==="staff" && current.status==="in-progress" && !current.consent_at?`<div class="voiceRecordingConsent"><label><input type="checkbox" id="voiceRecordingConsent">Avisei o paciente e ele concordou com a gravação.</label><button id="voiceRecord" type="button" disabled>${icon("circle-dot")} Iniciar gravação</button></div>`:""}
      ${restriction?`<p class="inlineWarning">${esc(restriction)}</p>`:""}
      <p id="voiceError" role="alert" hidden></p><div class="voiceControls">${active?`<button id="voiceMute" class="icon-button" aria-pressed="${muted}" aria-label="${muted?"Ativar microfone":"Silenciar microfone"}" title="${muted?"Ativar microfone":"Silenciar microfone"}" ${connection?"":"disabled"}>${icon(muted?"mic-off":"mic")}</button><button id="voiceEnd" class="voiceEnd">${icon("phone-off")} Encerrar</button>`:`<button id="voiceStart" class="primary" ${valid?"":"disabled"}>${icon("phone")} ${current?"Nova ligação":"Ligar para cliente"}</button>`}</div>
      ${active?'<label class="voiceDigits">Teclado<input id="voiceDigits" inputmode="tel" maxlength="20" pattern="[0-9*#]+" placeholder="0–9, * ou #" aria-label="Dígitos durante a ligação"><button id="voiceSendDigits" type="button">Enviar</button></label>':""}
      ${current && !active?`<form id="voiceNote"><h3>Resultado da ligação</h3><label>Resultado<select name="outcome">${Object.entries(outcomes).map(([id,label])=>`<option value="${id}" ${current.outcome===id?"selected":""}>${label}</option>`).join("")}</select></label><label>Nota interna<textarea name="note" maxlength="2000" rows="3">${esc(current.note)}</textarea></label><button type="submit">${icon("check")} Salvar na ficha</button><p id="voiceNoteStatus" role="status"></p></form>`:""}`;
    $("voiceClose").onclick=()=>dialog.close();
    if($("voiceStart"))$("voiceStart").onclick=start;
    if($("voiceRecordingConsent"))$("voiceRecordingConsent").onchange=()=>{$("voiceRecord").disabled=!$("voiceRecordingConsent").checked || recordBusy;};
    if($("voiceRecord"))$("voiceRecord").onclick=startRecording;
    if($("voiceEnd"))$("voiceEnd").onclick=end;
    if($("voiceMute"))$("voiceMute").onclick=()=>{muted=!muted;connection?.mute(muted);$("voiceMute").innerHTML=icon(muted?"mic-off":"mic");$("voiceMute").setAttribute("aria-pressed",String(muted));$("voiceMute").title=muted?"Ativar microfone":"Silenciar microfone";icons();};
    if($("voiceSendDigits"))$("voiceSendDigits").onclick=()=>{const digits=$("voiceDigits").value;if(/^[\d*#]{1,20}$/.test(digits)){connection?.sendDigits(digits);$("voiceDigits").value="";}};
    if($("voiceNote"))$("voiceNote").onsubmit=async event=>{
      event.preventDefault();const form=event.currentTarget,button=form.querySelector("button"),data=new FormData(form);button.disabled=true;
      try {await endpoint("note",{id:current.id,doctor_id:lead.doctor_id,outcome:data.get("outcome"),note:data.get("note")});current.note=data.get("note");current.outcome=data.get("outcome");$("voiceNoteStatus").textContent="Salvo na ficha do cliente.";}
      catch(error){message(error.message);}finally{button.disabled=false;}
    };
    icons();
  }
  async function open(item) {
    init();if(running()){dialog.showModal();return;}
    const request=++seq;lead={...item};current=null;requestId="";config=null;
    document.getElementById("crmDialog")?.close();
    dialog.innerHTML=`<h2 id="voiceTitle">Ligação</h2><p role="status">Carregando telefonia…</p>`;dialog.showModal();
    try {const data=await endpoint("config");if(request!==seq)return;config=data;render();}
    catch(error){if(request===seq){render();message(error.message);}}
  }
  async function sdk() {
    if(window.Twilio?.Device)return;
    await new Promise((resolve,reject)=>{const script=document.createElement("script");script.src="/twilio-voice-2.18.5.min.js";script.onload=resolve;script.onerror=()=>{script.remove();reject(new Error("Não foi possível carregar a telefonia."));};document.head.append(script);});
  }
  async function start() {
    if(running() || !config?.enabled || callRestriction(lead))return;
    if(current && terminal.has(current.status)){current=null;requestId="";}
    busy=true;requestId ||= crypto.randomUUID();message();$("voiceStart").disabled=true;
    try {
      await sdk();
      // Browser permission is requested only on this deliberate call gesture.
      const permission=await navigator.mediaDevices.getUserMedia({audio:true});permission.getTracks().forEach(track=>track.stop());
      const data=await endpoint("prepare",{lead_id:lead.id,doctor_id:lead.doctor_id,line_id:$("voiceLine").value,request_id:requestId});
      current=data.call;device?.destroy();device=new window.Twilio.Device(data.token,{logLevel:0,closeProtection:true});
      device.on("error",()=>{message("Falha na telefonia. Confira a ligação no histórico antes de tentar novamente.");});
      connection=await device.connect({params:{CallId:current.id}});muted=false;busy=false;
      connection.on("disconnect",finish);connection.on("cancel",finish);connection.on("reject",finish);connection.on("error",()=>{message("Conexão interrompida. Confira o histórico da ligação.");finish();});
      render();dock.hidden=false;dock.innerHTML=`${icon("phone-call")}<span>${esc(lead.name)}</span><b>Em ligação</b>`;icons();poll=setInterval(refresh,2500);refresh();
    } catch(error) {
      busy=false;
      if(current){try{await endpoint("cancel",{id:current.id,doctor_id:lead.doctor_id});}catch(_){}current.status="unknown";}
      device?.destroy();device=null;connection=null;render();message(error.name==="NotAllowedError"?"Permita o microfone no navegador para fazer a ligação.":"Não foi possível iniciar. Confira o histórico antes de tentar novamente.");
    }
  }
  async function refresh() {
    if(!current)return;
    const key=current.id,doc=lead.doctor_id;
    try {
      const data=await endpoint("history",undefined,{doctor:doc,lead:lead.id});
      if(current?.id!==key)return;
      const row=data.calls.find(c=>c.id===key);if(!row)return;
      const changed=row.status!==current.status || row.recording_status!==current.recording_status || Boolean(row.consent_at)!==Boolean(current.consent_at);
      current=row;
      if(changed && !terminal.has(row.status))render();
      if($("voiceState"))$("voiceState").textContent=labels[row.status] || row.status;
      if($("voiceTimer"))$("voiceTimer").textContent=duration(row.ended_at?row.duration:row.answered_at?Math.floor(Date.now()/1000-row.answered_at):0);
      if(terminal.has(row.status)){clearInterval(poll);poll=null;connection?.disconnect();connection=null;device?.destroy();device=null;dock.hidden=true;render();}
    } catch(_){message("Status temporariamente indisponível. Não inicie outra ligação.");}
  }
  function finish() {connection=null;busy=false;dock.hidden=true;refresh();}
  async function startRecording() {
    if(recordBusy || current?.status!=="in-progress" || !$("voiceRecordingConsent")?.checked)return;
    const key=current.id;recordBusy=true;$("voiceRecord").disabled=true;message();
    try {
      const data=await endpoint("start-recording",{id:key,doctor_id:lead.doctor_id,verbal_authorization:true});
      if(current?.id!==key)return;
      current=data.call;render();
      if(current.recording_status==="error")message("Não foi possível confirmar o início da gravação. A conversa continua; não repita a solicitação.");
    } catch(error){message(error.message || "Não foi possível confirmar a gravação. Confira o histórico; a conversa continua.");}
    finally {recordBusy=false;}
  }
  async function end() {
    $("voiceEnd").disabled=true;connection?.disconnect();
    try {await endpoint("cancel",{id:current.id,doctor_id:lead.doctor_id});await refresh();}
    catch(_){message("Confira o encerramento no histórico. Não inicie outra ligação ainda.");if($("voiceEnd"))$("voiceEnd").disabled=false;}
  }
  function history(root,item) {
    let page=1,version=0;
    const draw=async()=>{
      const request=++version;
      root.innerHTML='<p class="connectionNote" role="status">Carregando ligações…</p>';
      try {
        const data=await api("voice/history",undefined,{doctor:item.doctor_id,lead:item.id,page});
        if(request!==version || !root.isConnected)return;
        root.innerHTML=`<header class="voiceHistoryHead"><div><h3>Ligações e gravações</h3><p>${data.total} registro(s) · ${esc(item.name)}</p></div><div class="commands"><button data-voice-refresh class="icon-button" title="Atualizar ligações" aria-label="Atualizar ligações">${icon("refresh-cw")}</button><button data-voice-call class="primary">${icon("phone")} Ligar</button></div></header>${!data.config.enabled?'<p class="connectionNote voicePending">Telefonia aguardando configuração da Twilio.</p>':""}<div class="voiceHistory">${data.calls.map(call=>`<article class="voiceRecord"><header><span class="voiceRecordIcon">${icon(call.status==="completed"?"phone-call":"phone-outgoing")}</span><div><strong>${esc(labels[call.status] || call.status)}</strong><time>${stamp(call.created_at)}</time></div><b>${duration(call.duration)}</b></header><div class="voiceRecordMeta"><span>${esc(call.actor_name)}</span><span>${esc(call.line_name)} · ${esc(call.caller_number)}</span><span>Para ${esc(call.destination)}</span></div><div class="voiceAudio ${esc(call.recording_status)}">${icon(call.recording_status==="stored"?"audio-lines":"shield-check")}<span>${esc(recordings[call.recording_status] || "A conferir")}</span>${call.recording_status==="stored"?`<audio controls preload="none" aria-label="Gravação de ${esc(stamp(call.created_at))}" src="/api/crm/voice/audio?${new URLSearchParams({clinic:"vielle",doctor:item.doctor_id,id:call.id})}"></audio>`:""}</div>${call.consent_at?`<small class="voiceConsent">Gravação autorizada pelo paciente · ${stamp(call.consent_at)}</small>`:""}<details class="voiceNoteDetails"><summary>${esc(call.outcome?outcomes[call.outcome]:"Resultado e nota interna")}</summary><form data-call-note="${esc(call.id)}"><label>Resultado<select name="outcome">${Object.entries(outcomes).map(([id,label])=>`<option value="${id}" ${call.outcome===id?"selected":""}>${label}</option>`).join("")}</select></label><label>Nota interna<textarea name="note" rows="3" maxlength="2000">${esc(call.note)}</textarea></label><button type="submit">${icon("check")} Salvar</button><p role="status"></p></form></details></article>`).join("") || `<div class="voiceEmpty">${icon("phone")}<h3>Nenhuma ligação registrada</h3><p>${esc(item.name)}</p></div>`}</div><footer class="voicePagination"><button data-voice-prev ${page===1?"disabled":""}>${icon("chevron-left")}</button><span>Página ${page}</span><button data-voice-next ${data.has_more?"":"disabled"}>${icon("chevron-right")}</button></footer>`;
        root.querySelector("[data-voice-refresh]").onclick=draw;root.querySelector("[data-voice-call]").onclick=()=>open(item);
        root.querySelector("[data-voice-prev]").onclick=()=>{page--;draw();};root.querySelector("[data-voice-next]").onclick=()=>{page++;draw();};
        for(const [selector,label] of [["[data-voice-prev]","Página anterior"],["[data-voice-next]","Próxima página"]]){root.querySelector(selector).setAttribute("aria-label",label);root.querySelector(selector).title=label;}
        data.calls.forEach((call,index)=>{
          const record=root.querySelectorAll(".voiceRecord")[index];
          record.querySelector(".voiceAudio>span").textContent=recordingLabel(call);
          if(call.consent_source==="staff_verbal" && call.consent_at)record.querySelector(".voiceConsent").textContent=`Autorização verbal registrada por ${call.actor_name} · ${stamp(call.consent_at)}`;
          if(call.can_retry_recording){const button=document.createElement("button");button.type="button";button.innerHTML=`${icon("refresh-cw")} Buscar gravação`;record.querySelector(".voiceAudio").append(button);button.onclick=async()=>{button.disabled=true;try{await api("voice/retry-recording",{id:call.id,doctor_id:item.doctor_id},{doctor:item.doctor_id});draw();}catch(error){button.textContent=error.message;button.disabled=false;}};}
        });
        root.querySelectorAll("[data-call-note]").forEach(form=>form.onsubmit=async event=>{
          event.preventDefault();const button=form.querySelector("button"),status=form.querySelector('[role="status"]'),fields=new FormData(form);button.disabled=true;
          try{await api("voice/note",{doctor_id:item.doctor_id,id:form.dataset.callNote,outcome:fields.get("outcome"),note:fields.get("note")},{doctor:item.doctor_id});status.textContent="Salvo na ficha.";}
          catch(error){status.textContent=error.message;}finally{button.disabled=false;}
        });icons();
      }catch(error){if(request===version && root.isConnected)root.textContent=error.message;}
    };
    return {show(){page=1;draw();},cancel(){version++;root.querySelectorAll("audio").forEach(audio=>audio.pause());}};
  }
  async function settings(root,doctor) {
    try {
      const data=await api("voice/config",undefined,{doctor});if(!root.isConnected)return;
      root.innerHTML=`<div class="recordMain"><span class="recordIcon">${icon("phone-call")}</span><div><h3>Telefonia VoIP <span class="statusPill ${data.enabled?"open":"disconnected"}">${data.enabled?"Ativada":"Aguardando Twilio"}</span></h3>${data.lines.map(line=>`<p>${esc(line.name)} · ${esc(line.number)}</p>`).join("") || '<p>Número de voz ainda não conectado a este doutor.</p>'}<p>Histórico e gravações na ficha do cliente.</p></div></div>`;icons();
    }catch(error){if(root.isConnected)root.textContent=error.message;}
  }
  return {open,history,settings};
};
