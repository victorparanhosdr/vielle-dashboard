(function (root) {
  "use strict";
  const labels = {active:"Em atividade", idle:"Ocioso", offline:"Offline"};
  function duration(seconds, short = false) {
    const value = Math.max(0, Math.floor(Number(seconds) || 0));
    if (value < 60) return short ? "0 min" : "menos de 1 min";
    const minutes = Math.floor(value / 60);
    return minutes < 60 ? `${minutes} min` : `${Math.floor(minutes / 60)}h${String(minutes % 60).padStart(2, "0")}`;
  }
  function elapsed(since, now) { return since == null ? "Ainda não entrou" : duration(now - since); }
  function initials(name) { return String(name || "").trim().split(/\s+/).slice(0, 2).map(s => s[0] || "").join("").toUpperCase(); }
  function escape(value) { return String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c])); }
  class ActivityClock {
    constructor(now, visible = true) { this.last = now - (visible ? 0 : 300000); }
    touch(now, trusted, visible) { if (trusted && visible) this.last = now; }
    age(now) { return Math.min(86400, Math.max(0, (now - this.last) / 1000)); }
  }
  const helpers = {duration, elapsed, initials, escape, ActivityClock, labels};
  if (typeof module !== "undefined" && module.exports) { module.exports = helpers; return; }
  if (root.Doc4docsTeam) return;
  const icon = name => `<i data-lucide="${name}" aria-hidden="true"></i>`;
  const icons = element => root.lucide?.createIcons({root:element});
  async function request(clinic, action, payload, query = {}, options = {}) {
    const params = new URLSearchParams({clinic, ...query});
    const response = await fetch(`/api/team/${action}?${params}`, {
      ...options, ...(payload === undefined ? {} : {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(payload)})
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || "Não foi possível acessar a equipe.");
    return data;
  }
  root.Doc4docsTeam = {...helpers, request};
  let session = root.doc4docsSession, clinic = "", version = 0, inFlight = false, polling = false;
  let snapshot = null, error = "", selected = null, draft = "", sending = false, sent = false, opened = false;
  let serverOffset = 0, lastPoll = 0, helpRequestId = "";
  const known = new Set(), clientId = crypto.randomUUID();
  let sequence = 0;
  const activity = new ActivityClock(performance.now(), !document.hidden);
  const widget = document.createElement("aside");
  widget.id = "doc4docsTeamWidget"; widget.className = "teamWidget"; widget.hidden = true;
  widget.setAttribute("aria-label", "Presença da equipe");
  document.body.append(widget);
  const now = () => performance.now() / 1000 + serverOffset;
  const payload = () => ({client_id:clientId, sequence:++sequence, activity_age:activity.age(performance.now())});
  function currentClinic() {
    if (!session?.user?.id) return "";
    const page = document.body.dataset.appPage;
    if (["login", "pricing", "institutes", "master", "brain", "master-sales"].includes(page)) return "";
    if (page === "dashboard" && document.getElementById("dashboardShell")?.classList.contains("dashboardHidden")) return "";
    const key = new URLSearchParams(location.search).get("clinic") || (page === "body" ? "inspire" : "");
    return session.clinics?.some(c => c.key === key) ? key : "";
  }
  function canManage() { return Boolean(session?.permissions?.[clinic]?.includes("team.view")); }
  function memberRow(person) {
    const self = person.id === session.user.id;
    return `<div class="teamMember" data-team-status="${escape(person.status)}"><span class="teamAvatar">${escape(initials(person.name))}<i class="teamDot"></i></span><div class="teamMemberCopy"><strong>${escape(person.name)}${self?'<small>Você</small>':''}</strong><span class="teamStatus"><i class="teamDot"></i>${labels[person.status] || "Offline"}<span aria-hidden="true">·</span><time data-team-elapsed="${person.status_since ?? ''}">${elapsed(person.status_since, now())}</time></span></div>${self?'':`<button type="button" class="teamIcon teamHelpButton" data-team-help="${person.id}" aria-label="Pedir ajuda a ${escape(person.name)}" title="${person.status==='offline'?'Colega offline':'Pedir ajuda'}" ${person.status==='offline'?'disabled':''}>${icon("message-circle-question")}</button>`}</div>`;
  }
  function requestsMarkup() {
    return (snapshot?.help_requests || []).filter(item => ["pending", "accepted"].includes(item.status)).map(item => {
      const incoming = item.recipient_id === session.user.id;
      return `<article class="teamHelpRequest"><div class="teamRequestIdentity">${icon(incoming?"hand-helping":"send")}<strong>${escape(incoming?item.sender_name:item.recipient_name)}</strong><span>${incoming?"Pediu ajuda":"Seu pedido"}</span></div><p>${escape(item.message)}</p><footer><span>${item.status==='accepted'?'Ajuda aceita':'Aguardando resposta'}</span>${incoming&&item.status==='pending'?`<button class="teamIcon" data-team-action="accept" data-id="${escape(item.id)}" aria-label="Aceitar pedido de ${escape(item.sender_name)}" title="Aceitar pedido">${icon("check")}</button><button class="teamIcon" data-team-action="dismiss" data-id="${escape(item.id)}" aria-label="Dispensar pedido" title="Dispensar pedido">${icon("x")}</button>`:`<button class="teamIcon" data-team-action="complete" data-id="${escape(item.id)}" aria-label="Encerrar pedido de ajuda" title="Encerrar pedido">${icon("check-check")}</button>`}</footer></article>`;
    }).join("");
  }
  function render() {
    widget.hidden = !clinic;
    if (!clinic) return;
    const people = snapshot?.members || [], online = people.filter(p => p.status !== "offline");
    const pending = (snapshot?.help_requests || []).filter(p => p.recipient_id === session.user.id && p.status === "pending").length;
    const person = people.find(p => p.id === selected);
    const focused = widget.contains(document.activeElement) ? document.activeElement?.dataset.teamFocus : "";
    const caret = focused === "message" ? [document.activeElement.selectionStart, document.activeElement.selectionEnd] : null;
    const stale = lastPoll && performance.now() - lastPoll > 45000;
    let content;
    if (person) {
      content = `<button type="button" class="teamBack" data-team-back>${icon("arrow-left")}Toda a equipe</button><div class="teamHelpIdentity">${memberRow(person).replace(/<button[\s\S]*<\/button>/, "")}</div>${sent?`<p class="teamSent" role="status">${icon("circle-check")}Pedido enviado para ${escape(person.name.split(" ")[0])}.</p>`:`<form class="teamHelpForm"><label for="teamHelpMessage">Como ${escape(person.name.split(" ")[0])} pode ajudar?</label><textarea id="teamHelpMessage" data-team-focus="message" maxlength="500" rows="4" required>${escape(draft)}</textarea><button class="teamPrimary" type="submit" ${sending||person.status==='offline'?'disabled':''}>${icon("send")}${sending?'Enviando…':'Enviar pedido'}</button></form>`}`;
    } else if (!snapshot) {
      content = '<p class="teamEmpty" role="status">Carregando equipe…</p>';
    } else {
      const sort = list => list.sort((a,b) => a.name.localeCompare(b.name,"pt-BR"));
      content = `${requestsMarkup()}<div class="teamGroupLabel"><span>Online</span><span>${online.length}</span></div>${sort([...online]).map(memberRow).join("") || '<p class="teamEmpty">Nenhum colega online agora.</p>'}<div class="teamGroupLabel"><span>Offline</span><span>${people.length-online.length}</span></div>${sort(people.filter(p=>p.status==='offline')).map(memberRow).join("")}`;
    }
    widget.innerHTML = `<button type="button" class="teamDock ${pending?'hasRequests':''}" data-team-toggle aria-label="Equipe, ${online.length} online${pending?`, ${pending} pedidos de ajuda`:''}" aria-expanded="${opened}" aria-controls="teamPresencePanel" title="Equipe online">${icon("users-round")}<span>${pending||online.length}</span></button><section id="teamPresencePanel" class="teamPanel" role="dialog" aria-modal="false" aria-labelledby="teamPanelTitle" ${opened?'':'hidden'}><header><div><h2 id="teamPanelTitle">${icon("users-round")}Equipe</h2><p>${escape(session.clinics.find(c=>c.key===clinic)?.name || clinic)} · ${online.length} online</p></div><button type="button" class="teamIcon" data-team-close aria-label="Recolher equipe" title="Recolher equipe">${icon("x")}</button></header><div class="teamPanelBody">${error||stale?`<p class="teamError" role="status">${escape(error || 'Reconectando. Os status podem estar desatualizados.')}<button type="button" class="teamIcon" data-team-retry aria-label="Atualizar equipe" title="Atualizar equipe">${icon("refresh-cw")}</button></p>`:''}${content}</div><footer class="teamPanelFooter"><span>${icon("clock-3")}Tempo no status atual</span>${canManage()?`<a href="/team.html?clinic=${encodeURIComponent(clinic)}">${icon("chart-no-axes-gantt")}Relatórios da equipe</a>`:''}<small>Presença e interações registradas nesta clínica.</small></footer></section>`;
    icons(widget);
    if (focused && widget.querySelector(`[data-team-focus="${focused}"]`)) {
      const field = widget.querySelector(`[data-team-focus="${focused}"]`); field.focus();
      if (caret) field.setSelectionRange(...caret);
    }
    const manageLink = document.querySelector("[data-team-manage-link]");
    if (manageLink) { manageLink.hidden = !canManage(); manageLink.href = `/team.html?clinic=${encodeURIComponent(clinic)}`; }
  }
  async function poll(force = false) {
    if (!clinic || polling || (document.hidden && !force)) return;
    polling = true; const key = clinic, epoch = version;
    try {
      const data = await request(key, "presence");
      if (epoch !== version) return;
      serverOffset = data.server_time - performance.now()/1000;
      for (const item of data.help_requests || []) {
        if (item.recipient_id === session.user.id && item.status === "pending" && !known.has(item.id)) {
          root.dispatchEvent(new CustomEvent("doc4docs-team-notification", {detail:{id:item.id, message:`${item.sender_name} pediu ajuda: ${item.message}`, clinic:key}}));
          known.add(item.id);
        }
      }
      snapshot = data; error = ""; lastPoll = performance.now(); render();
      root.dispatchEvent(new CustomEvent("doc4docs-team-presence", {detail:{clinic:key}}));
    } catch (e) { if (epoch === version) { error = e.message; render(); } }
    finally { polling = false; }
  }
  async function sync() {
    const next = currentClinic();
    if (next !== clinic) {
      const old = clinic; if (old) request(old,"leave",payload(),{}, {keepalive:true}).catch(()=>{});
      clinic = next; version++; selected = null; snapshot = null; opened = false; draft = ""; error = ""; sending=false;sent=false;helpRequestId="";known.clear();render();
    }
    if (!clinic || inFlight) return;
    inFlight = true; const key = clinic, epoch = version;
    try { await request(key, "heartbeat", payload()); if (epoch === version) await poll(); }
    catch (e) { if (epoch === version) { error = e.message; render(); } }
    finally { inFlight = false; if(epoch!==version)sync(); }
  }
  widget.addEventListener("input", event => { if (event.target.matches("textarea")) draft = event.target.value; });
  widget.addEventListener("click", async event => {
    const b = event.target.closest("button"); if (!b || b.disabled) return;
    if (b.hasAttribute("data-team-toggle")) { opened = !opened; selected = null; render(); if (opened) { widget.querySelector("[data-team-close]").focus(); await poll(); } }
    else if (b.hasAttribute("data-team-close")) { opened = false; render(); widget.querySelector("[data-team-toggle]").focus(); }
    else if (b.hasAttribute("data-team-back")) { selected = null; render(); }
    else if (b.dataset.teamHelp) { selected = Number(b.dataset.teamHelp); draft = "Pode me ajudar com este atendimento?"; helpRequestId=crypto.randomUUID(); sent = false; render(); widget.querySelector("textarea")?.focus(); }
    else if (b.hasAttribute("data-team-retry")) { error = ""; await sync(); }
    else if (b.dataset.teamAction) {
      b.disabled = true; const key = clinic, epoch = version;
      try { await request(key,"help/action",{id:b.dataset.id,action:b.dataset.teamAction}); if (epoch === version) await poll(true); }
      catch(e) { if (epoch===version) {error=e.message;render();} }
      finally { if(b.isConnected)b.disabled=false; }
    }
  });
  widget.addEventListener("submit", async event => {
    if (!event.target.matches(".teamHelpForm")) return;
    event.preventDefault(); if (sending || !draft.trim()) return;
    const key = clinic, epoch = version, recipient = selected;
    const requestId = helpRequestId || (helpRequestId=crypto.randomUUID());
    sending = true; error = ""; render();
    try { await request(key,"help",{recipient_id:recipient,message:draft,request_id:requestId}); if (epoch===version) {sent=true;await poll(true);} }
    catch(e) { if (epoch===version) error=e.message; }
    finally { if(epoch===version){sending=false;render();} }
  });
  document.addEventListener("keydown", event => { if (event.key === "Escape" && opened) { opened=false;render();widget.querySelector("[data-team-toggle]")?.focus(); } });
  function touch(event) { activity.touch(performance.now(), event.isTrusted, !document.hidden); }
  ["pointerdown","keydown","scroll","touchstart"].forEach(name => document.addEventListener(name,touch,{passive:true,capture:true}));
  root.addEventListener("doc4docs-session", event => {
    if (session?.user?.id !== event.detail?.user?.id) { version++;clinic="";snapshot=null;selected=null;opened=false;draft="";sending=false;sent=false;helpRequestId="";known.clear();widget.hidden=true; }
    session=event.detail; sync();
  });
  root.addEventListener("pageshow", sync);
  root.addEventListener("pagehide", () => { if (clinic) request(clinic,"leave",payload(),{}, {keepalive:true}).catch(()=>{}); });
  document.addEventListener("visibilitychange", () => { if (!document.hidden) sync(); });
  root.addEventListener("online", sync);
  root.addEventListener("popstate", sync);
  const dashboard = document.getElementById("dashboardShell");
  if (dashboard) new MutationObserver(sync).observe(dashboard,{attributes:true,attributeFilter:["class"]});
  setInterval(sync,30000);
  setInterval(()=>{if(opened&&!document.hidden)poll();},15000);
  setInterval(()=>{if(opened)widget.querySelectorAll("[data-team-elapsed]").forEach(node=>{node.textContent=elapsed(node.dataset.teamElapsed===''?null:Number(node.dataset.teamElapsed),now());});},1000);
  sync();
})(typeof window !== "undefined" ? window : globalThis);
