"use strict";
window.createCrmEngagement=function({api,esc,icon,icons,reminders,onTasks,getUser}){
  let user=null,epoch=0,timer=null,busy=false,rerun=false,cursors=null,journey=null,alerts=[],dock=null,dialog=null,toastTimer=null;
  const sounds=window.CrmSounds.create({getUser:()=>user?.id});
  const dueSeen=new Set();
  const localAwards=new Set();
  const request=(query={})=>api("engagement",undefined,{doctor:"",...query});
  const $=selector=>document.querySelector(selector);
  function mountDock(){
    if(dock?.isConnected)return;
    const target=$(".appHeaderActions");if(!target)return;
    dock=document.createElement("div");dock.className="crmAttentionDock";
    dock.innerHTML=`<button type="button" class="icon-button crmReminderBell" aria-label="Abrir tarefas" title="Tarefas e lembretes">${icon("alarm-clock")}<b hidden></b></button><button type="button" class="icon-button crmSoundToggle" aria-label="Configurar sons" title="Configurar sons">${icon("volume-x")}</button>`;
    target.prepend(dock);dock.querySelector(".crmReminderBell").onclick=()=>{onTasks();reminders.showOverdue();};dock.querySelector(".crmSoundToggle").onclick=openSounds;updateDock();
  }
  function updateDock(){
    if(!dock)return;const count=dock.querySelector("b"),button=dock.querySelector(".crmSoundToggle"),settings=sounds.settings();
    count.textContent=alerts.total || "";count.hidden=!alerts.total;
    dock.querySelector(".crmReminderBell").setAttribute("aria-label",alerts.total?`${alerts.total} lembrete(s) no horário de atender`:"Abrir tarefas");
    button.innerHTML=icon(settings.enabled?"volume-2":"volume-x");button.setAttribute("aria-pressed",String(settings.enabled));button.title=settings.enabled?"Sons ativados · configurar":"Sons silenciados · configurar";icons();
  }
  function dueBanner(){
    let banner=$("#crmReminderAlerts");
    if(!banner){banner=document.createElement("section");banner.id="crmReminderAlerts";banner.className="crmReminderAlerts";banner.setAttribute("aria-label","Lembretes no horário de atender");$("#notice").after(banner);}
    banner.hidden=!alerts.total;
    banner.innerHTML=alerts.total?`<div>${icon("alarm-clock")}<strong>${alerts.total} lembrete(s) aguardando você</strong><button type="button" data-see-tasks>Ver tarefas ${icon("arrow-right")}</button></div><ul>${alerts.items.slice(0,2).map(item=>`<li data-alert="${esc(item.id)}"><span><strong>${esc(item.title)}</strong><small>${esc(item.client_name)} · ${reminders.when(item)}</small></span><button type="button" class="icon-button" data-alert-done aria-label="Concluir ${esc(item.title)}" title="Concluir tarefa">${icon("check")}</button><button type="button" class="icon-button" data-alert-dismiss aria-label="Dispensar aviso de ${esc(item.title)}" title="Dispensar aviso, mantendo tarefa pendente">${icon("bell-off")}</button></li>`).join("")}</ul>`:"";
    banner.querySelector("[data-see-tasks]")?.addEventListener("click",()=>{onTasks();reminders.showOverdue();});
    banner.querySelectorAll("[data-alert]").forEach(row=>{const item=alerts.items.find(i=>i.id===row.dataset.alert);row.querySelectorAll("button").forEach(button=>button.onclick=async()=>{button.disabled=true;try{await reminders.request(button.hasAttribute("data-alert-done")?"done":"ack",{id:item.id,revision:item.revision});await poll(true);await reminders.refresh();}catch{await poll(true);}finally{button.disabled=false;}});});icons();
  }
  function toast(message,kind="achievement"){
    let box=$("#crmAchievementToast");if(!box){box=document.createElement("div");box.id="crmAchievementToast";box.className="crmAchievementToast";box.setAttribute("role","status");document.body.append(box);}
    box.innerHTML=`${icon(kind==="win"?"trophy":"sparkles")}<div><strong>${esc(message)}</strong><span>Jornada da equipe</span></div>`;box.hidden=false;icons();clearTimeout(toastTimer);toastTimer=setTimeout(()=>{box.hidden=true;},4500);
  }
  async function poll(force=false){
    if(!user)return;if(busy){if(force)rerun=true;return;}const version=epoch;busy=true;
    try{
      mountDock();const data=await request(cursors?{watch:"1",messages_after:cursors.messages,xp_after:cursors.xp}:{});
      if(version!==epoch)return;const previous=journey;journey=data.journey;
      const due=await reminders.request("alerts");if(version!==epoch)return;
      alerts=due;updateDock();dueBanner();
      const freshDue=due.items.filter(item=>!dueSeen.has(`${item.id}:${item.revision}`));
      due.items.forEach(item=>dueSeen.add(`${item.id}:${item.revision}`));
      if(freshDue.length)await sounds.once(`reminder:${freshDue[0].id}:${freshDue[0].revision}`,"reminder");
      if(cursors){
        const pending=data.celebrations.filter(p=>!localAwards.has(p.id));
        const win=pending.find(p=>p.label==="Fechamento"),earned=win || pending.at(-1);
        if(earned){const kind=previous && journey.level>previous.level?"level":win?"win":"achievement";toast(kind==="level"?`Você chegou ao nível ${journey.level}!`:`${earned.actor} · ${earned.label} · +${earned.points} XP`,win?"win":"achievement");await sounds.once(`xp:${earned.id}`,kind);}
        if(data.fresh_messages)await sounds.once(`message:${data.message_cursor}`,"message");
      }
      cursors={messages:Math.max(cursors?.messages || 0,data.message_cursor),xp:Math.max(cursors?.xp || 0,data.xp_cursor)};
      if(dialog?.open && dialog.dataset.kind==="journey" && JSON.stringify(previous)!==JSON.stringify(journey))renderJourney();
    }catch(error){if(version===epoch && [401,403].includes(error.status))stop();}
    finally{if(version===epoch){busy=false;if(rerun){rerun=false;poll();}}}
  }
  async function notify(feedback){
    if(!user || !feedback || String(feedback.actor_id)!==String(user.id) || !["stage","achievement","win","level"].includes(feedback.kind))return;
    const related=(feedback.xp_ids || []).map(id=>`xp:${id}`);
    (feedback.xp_ids || []).forEach(id=>localAwards.add(id));
    if(localAwards.size>500){const keep=[...localAwards].slice(-250);localAwards.clear();keep.forEach(id=>localAwards.add(id));}
    if(feedback.kind==="level")toast(`Você chegou ao nível ${feedback.level}!`);
    else if(!feedback.stage_changes && feedback.points)toast(`Follow-up registrado · +${feedback.points} XP`);
    const key=feedback.kind==="level"?`level:${user.id}:${feedback.level}`:related[0] || feedback.id;
    await sounds.once(key,feedback.kind,{relatedIds:related});
    poll(true);
  }
  function close(){dialog?.close();dialog?.remove();dialog=null;$("#crmJourneyToggle")?.setAttribute("aria-expanded","false");}
  function show(title,kind){
    close();dialog=document.createElement("dialog");dialog.className="crmEngagementDialog";dialog.dataset.kind=kind;dialog.setAttribute("aria-labelledby","crmEngagementTitle");
    dialog.innerHTML=`<header><h2 id="crmEngagementTitle">${esc(title)}</h2><button class="icon-button" type="button" aria-label="Fechar jornada">${icon("x")}</button></header><div class="crmEngagementBody"></div>`;
    document.body.append(dialog);dialog.querySelector("header button").onclick=close;dialog.addEventListener("close",()=>$("#crmJourneyToggle")?.setAttribute("aria-expanded","false"));dialog.showModal();if(kind==="journey")$("#crmJourneyToggle")?.setAttribute("aria-expanded","true");icons();return dialog.querySelector(".crmEngagementBody");
  }
  function renderJourney(){
    if(!dialog?.open || dialog.dataset.kind!=="journey")return;const body=dialog.querySelector(".crmEngagementBody"),g=journey;
    if(!g){body.innerHTML='<p role="status">Carregando sua jornada…</p>';return;}
    body.innerHTML=`<section class="journeyScore"><span class="journeyTrophy">${icon("trophy")}</span><div><span>${esc(g.name)}</span><h3>Nível ${g.level}</h3><strong>${g.xp} XP</strong></div><div class="journeyNext"><span>${100-g.progress} XP para o próximo nível</span><progress value="${g.progress}" max="100" aria-label="Progresso do seu nível"></progress></div></section><div class="journeyStats"><div>${icon("zap")}<strong>${g.today_xp}</strong><span>XP hoje</span></div><div>${icon("calendar-days")}<strong>${g.week_xp}</strong><span>XP na semana</span></div><div>${icon("flame")}<strong>${g.streak}</strong><span>dia(s) em sequência</span></div></div><section class="journeyMissions"><h3>Suas missões de hoje</h3>${g.goals.map(goal=>`<div class="journeyMission ${goal.count>=goal.target?"complete":""}">${icon(goal.count>=goal.target?"circle-check":goal.icon)}<span>${esc(goal.label)}<progress value="${Math.min(goal.count,goal.target)}" max="${goal.target}" aria-label="${esc(goal.label)}"></progress></span><strong>${Math.min(goal.count,goal.target)} / ${goal.target}</strong></div>`).join("")}</section><section class="journeyMedals"><h3>Suas medalhas</h3><div>${g.badges.map(badge=>`<article class="${badge.unlocked?"unlocked":"locked"}">${icon(badge.unlocked?badge.icon:"lock-keyhole")}<strong>${esc(badge.label)}</strong><small>${esc(badge.detail)}</small><span>${badge.unlocked?"Conquistada":"Em progresso"}</span></article>`).join("")}</div></section><section class="journeyRanking"><h3>Ranking da semana <small>${g.team_week_xp} XP da equipe</small></h3>${g.team.length?`<ol>${g.team.map((member,i)=>`<li class="${member.name===g.name?"you":""}"><b>${String(i+1).padStart(2,"0")}</b><span>${esc(member.name)}${member.name===g.name?' <small>Você</small>':""}</span><strong>${member.xp} XP</strong></li>`).join("")}</ol>`:'<p>As primeiras conquistas da semana começam aqui.</p>'}</section><details class="journeyRules"><summary>Como os pontos funcionam</summary><p>Follow-up registrado: 3 XP, uma vez por cliente ao dia. Nova fase aberta: 5 XP, uma vez por fase. Negócio ganho: 25 XP, uma vez por oportunidade. Mensagens e disparos não geram pontos. Ganhos no CRM não são pagamentos confirmados.</p><p>Semana de segunda a domingo, no horário de Brasília. O ranking inclui apenas os profissionais e funis permitidos ao seu usuário. O progresso individual usa o nome registrado nas atividades.</p></details><button type="button" data-journey-sounds>${icon("volume-2")} Preferências de som</button>`;
    body.querySelector("[data-journey-sounds]").onclick=openSounds;icons();
  }
  async function openJourney(){show("Jornada de conquistas","journey");renderJourney();await poll();renderJourney();}
  function openSounds(){
    const body=show("Sons do CRM","sounds"),settings=sounds.settings();
    body.innerHTML=`<form class="crmSoundsForm"><label class="soundSwitch"><span>${icon("volume-2")} Ativar sons neste dispositivo</span><input name="enabled" type="checkbox" role="switch" ${settings.enabled?"checked":""}></label><fieldset class="soundStyles"><legend>Personalidade do som</legend>${[["vibrant","Vibrante","sparkles"],["soft","Discreto","audio-lines"]].map(([value,label,symbol])=>`<label><input type="radio" name="style" value="${value}" ${settings.style===value?"checked":""}><span>${icon(symbol)}${label}</span></label>`).join("")}</fieldset>${[["messages","Novas mensagens","message-circle"],["reminders","Lembretes de tarefas","alarm-clock"],["achievements","Fases, conquistas e novos níveis","trophy"]].map(([key,label,symbol])=>`<label class="soundSwitch"><span>${icon(symbol)}${label}</span><input name="${key}" type="checkbox" role="switch" ${settings[key]?"checked":""}></label>`).join("")}<label class="soundVolume">Volume <output>${settings.volume}%</output><input name="volume" type="range" min="0" max="100" value="${settings.volume}"></label><div class="soundTests">${[["message","Mensagem"],["reminder","Lembrete"],["stage","Mudança de fase"],["win","Venda ganha"],["level","Novo nível"]].map(([kind,label])=>`<button type="button" data-test-sound="${kind}">${icon("play")}${label}</button>`).join("")}</div><p class="soundState" role="status"></p></form>`;
    const form=body.querySelector("form"),status=body.querySelector("[role=status]");
    form.onsubmit=e=>e.preventDefault();
    form.oninput=async()=>{const values={};for(const key of ["enabled","messages","reminders","achievements"])values[key]=form.elements[key].checked;values.volume=Number(form.elements.volume.value);values.style=form.elements.style.value;sounds.save(values);form.querySelector("output").textContent=values.volume+"%";updateDock();if(values.enabled){const ready=await sounds.unlock();status.textContent=ready?"Sons ativados neste dispositivo.":"O navegador não liberou o áudio. Toque em testar novamente.";}else status.textContent="Sons silenciados.";};
    body.querySelectorAll("[data-test-sound]").forEach(button=>button.onclick=async()=>{if(await sounds.unlock()){const played=await sounds.play(button.dataset.testSound,{test:true});status.textContent=played?`Tocando: ${button.textContent.trim()} · ${sounds.settings().style==="vibrant"?"Vibrante":"Discreto"}`:"Aumente o volume para ouvir a prévia.";}else status.textContent="Áudio indisponível neste navegador.";});icons();
  }
  function stop(){epoch++;clearInterval(timer);timer=null;busy=false;rerun=false;cursors=null;journey=null;alerts=[];dueSeen.clear();localAwards.clear();user=null;dock?.remove();dock=null;close();$("#crmReminderAlerts")?.remove();$("#crmAchievementToast")?.remove();}
  function start(value){
    if(user && String(user.id)===String(value.id))return;
    stop();user=value;sounds.reset();mountDock();poll();timer=setInterval(poll,10000);
  }
  window.addEventListener("crm-reminders-changed",()=>poll(true));
  document.addEventListener("crm-open-journey",openJourney);
  document.addEventListener("visibilitychange",()=>{if(!document.hidden && user)poll();});
  return {start,stop,poll,notify,openJourney,openSounds};
};
