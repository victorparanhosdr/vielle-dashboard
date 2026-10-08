(() => {
  "use strict";
  class NotificationHistory {
    constructor(storage, now = () => Date.now()) {
      this.storage = storage;
      this.now = now;
      this.items = [];
      this.key = null;
    }
    setUser(id) {
      const key = id ? `doc4docs:notifications:${id}` : null;
      if (key === this.key) return;
      const pending = this.key ? [] : this.items;
      this.key = key;
      this.items = [];
      if (!key) return;
      try {
        const items = JSON.parse(this.storage?.getItem(key) || "[]");
        if (Array.isArray(items)) this.items = items.filter(item =>
          typeof item.id === "string" && typeof item.message === "string" &&
          typeof item.scope === "string" && typeof item.label === "string" &&
          ["info", "success", "warning", "error"].includes(item.level) &&
          typeof item.read === "boolean" && item.message.length <= 1200 &&
          Number.isFinite(item.time) && item.time <= this.now() && item.time > this.now() - 86400000
        ).slice(0, 40);
      } catch (_) { /* Storage is optional, including private browsing. */ }
      pending.forEach(item => { if (!this.items.some(saved => saved.id === item.id)) this.items.unshift(item); });
      this.items = this.items.slice(0, 40);
      this.save();
    }
    save() {
      if (!this.key) return;
      try { this.storage?.setItem(this.key, JSON.stringify(this.items)); } catch (_) {}
    }
    add(source, message, {scope = "Sistema", label = "Sistema", level = "info"} = {}) {
      message = String(message || "").trim().replace(/\s+/g, " ").slice(0, 1200);
      if (!message || message === "-") return false;
      scope = String(scope).slice(0, 160);
      label = String(label).slice(0, 160);
      if (!["info", "success", "warning", "error"].includes(level)) level = "info";
      const id = JSON.stringify([scope, source, message, level]);
      if (this.items.some(item => item.id === id)) return false;
      this.items.unshift({id, scope, label, level, message, time:this.now(), read:false});
      this.items = this.items.slice(0, 40);
      this.save();
      return true;
    }
    read(id) {
      this.items.forEach(item => { if (!id || item.id === id) item.read = true; });
      this.save();
    }
    clearRead() { this.items = this.items.filter(item => !item.read); this.save(); }
    get unread() { return this.items.filter(item => !item.read).length; }
  }
  if (typeof module !== "undefined" && module.exports) {
    module.exports = NotificationHistory;
    return;
  }
  const page = document.body.dataset.appPage;
  if (!page || page === "login") return;
  const $ = selector => document.querySelector(selector);
  const el = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text) node.textContent = text;
    return node;
  };
  const icon = name => {
    const node = el("i");
    node.dataset.lucide = name;
    node.setAttribute("aria-hidden", "true");
    return node;
  };
  const link = (label, href, name) => {
    const node = el("a", "appNavItem");
    node.href = href;
    if (name) node.append(icon(name));
    node.append(el("span", "", label));
    return node;
  };
  const publicPage = page === "pricing";
  const institutePage = page === "institutes";
  const adminPage = page === "master" || page === "brain";
  const originalHeader = $(".topbar, .institute-header, .masterHeader, .brainHeader, .brandbar");
  const originalToolbar = $(".sessionToolbar");
  if (page === "settings" && originalHeader) {
    const heading = el("div", "pageHeading");
    heading.append(el("h1", "", "Configurações"));
    originalHeader.before(heading);
  }
  const chrome = el("div", "appChrome");
  const topbar = el("header", "appTopbar");
  const brand = el("a", "appBrand");
  brand.href = publicPage ? "/precificacao" : "/";
  brand.setAttribute("aria-label", "DOC4DOCS");
  const logo = el("img");
  logo.src = "/doc4docs-logo-white.png";
  logo.alt = "DOC4DOCS";
  logo.width = 126;
  logo.height = 52;
  brand.append(logo);
  topbar.append(brand);
  const context = el("div", "appContext");
  const change = $("#changeClinicBtn, #switchInstitute");
  if (change) {
    change.classList.add("appContextButton");
    change.title = institutePage ? "Trocar instituto" : "Trocar clínica";
    context.append(change);
  } else if (!publicPage) {
    const home = link(adminPage ? "Administração" : "Clínicas", adminPage ? "/master" : "/", adminPage ? "shield-check" : "building-2");
    context.append(home);
  }
  topbar.append(context);
  if (!publicPage) {
    const areas = el("nav", "appAreas");
    areas.setAttribute("aria-label", "Área do sistema");
    areas.append(link("Clínicas", "/?area=clinics"), link("Institutos", "/?area=institutes"));
    topbar.append(areas);
  }
  const actions = el("div", "appHeaderActions");
  const refresh = $("#syncBtn, #sync");
  if (refresh) {
    refresh.classList.add("appRefresh");
    refresh.title = institutePage ? "Atualizar dados do instituto" : "Atualizar tudo";
    refresh.setAttribute("aria-label", refresh.title);
    actions.append(refresh);
  }
  const settings = $("#settingsLink");
  if (settings) {
    settings.classList.add("appIconButton");
    settings.title = "Configurações";
    settings.setAttribute("aria-label", "Configurações");
    settings.replaceChildren(icon("settings-2"));
    actions.append(settings);
  }
  const utilityNodes = originalHeader ? [...originalHeader.querySelectorAll(".actions > button, .actions > a, .header-actions > button")] : [];
  if (publicPage) utilityNodes.forEach(node => actions.append(node));
  const account = el("details", "appAccount");
  let notificationHistory;
  let renderNotifications = () => {};
  let captureNotifications = () => {};
  if (!publicPage) {
    let storage;
    try { storage = window.sessionStorage; } catch (_) {}
    notificationHistory = new NotificationHistory(storage);
    const notifications = el("button", "appIconButton appNotificationToggle");
    notifications.type = "button";
    notifications.title = "Notificações";
    notifications.setAttribute("aria-label", "Notificações");
    notifications.setAttribute("aria-expanded", "false");
    notifications.setAttribute("aria-haspopup", "dialog");
    notifications.setAttribute("aria-controls", "appNotificationPanel");
    const badge = el("span", "appNotificationBadge");
    badge.setAttribute("aria-hidden", "true");
    badge.hidden = true;
    notifications.append(icon("bell"), badge);
    actions.append(notifications);
    const panel = el("section", "appNotificationPanel");
    panel.id = "appNotificationPanel";
    panel.hidden = true;
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-labelledby", "appNotificationTitle");
    const heading = el("div", "appNotificationHeading");
    const title = el("h2", "", "Notificações");
    title.id = "appNotificationTitle";
    const close = el("button", "appIconButton");
    close.type = "button";
    close.title = "Fechar notificações";
    close.setAttribute("aria-label", close.title);
    close.append(icon("x"));
    heading.append(title, close);
    const tools = el("div", "appNotificationTools");
    const count = el("span", "");
    count.setAttribute("role", "status");
    const readAll = el("button", "appIconButton");
    readAll.type = "button";
    readAll.title = "Marcar todas como lidas";
    readAll.setAttribute("aria-label", readAll.title);
    readAll.append(icon("check-check"));
    const clear = el("button", "appIconButton");
    clear.type = "button";
    clear.title = "Limpar notificações lidas";
    clear.setAttribute("aria-label", clear.title);
    clear.append(icon("archive"));
    tools.append(count, readAll, clear);
    const list = el("div", "appNotificationList");
    panel.append(heading, tools, list);
    chrome.append(panel);
    const setOpen = (open, restoreFocus = false) => {
      panel.hidden = !open;
      notifications.setAttribute("aria-expanded", String(open));
      if (open) {
        account.open = false;
        chrome.classList.remove("appMenuOpen");
        chrome.querySelector(".appMobileNavToggle")?.setAttribute("aria-expanded", "false");
        close.focus();
      }
      else if (restoreFocus) notifications.focus();
    };
    notifications.addEventListener("click", () => setOpen(panel.hidden));
    close.addEventListener("click", () => setOpen(false, true));
    document.addEventListener("click", event => {
      if (!panel.hidden && !panel.contains(event.target) && !notifications.contains(event.target)) setOpen(false);
    });
    document.addEventListener("keydown", event => {
      if (event.key === "Escape" && !panel.hidden) setOpen(false, true);
    });
    const timeFormat = new Intl.DateTimeFormat("pt-BR", {day:"2-digit", month:"2-digit", hour:"2-digit", minute:"2-digit"});
    renderNotifications = () => {
      const unread = notificationHistory.unread;
      badge.hidden = !unread;
      badge.textContent = unread > 99 ? "99+" : String(unread);
      notifications.setAttribute("aria-label", unread ? `Notificações, ${unread} não lidas` : "Notificações");
      count.textContent = unread ? `${unread} não ${unread === 1 ? "lida" : "lidas"}` : "Tudo lido";
      readAll.disabled = !unread;
      clear.disabled = !notificationHistory.items.some(item => item.read);
      list.replaceChildren();
      if (!notificationHistory.items.length) list.append(el("p", "appNotificationEmpty", "Nenhuma notificação"));
      notificationHistory.items.forEach(item => {
        const row = el("article", "appNotificationItem");
        row.dataset.level = item.level;
        row.dataset.read = String(Boolean(item.read));
        row.append(icon({error:"circle-alert", warning:"triangle-alert", success:"circle-check", info:"info"}[item.level]));
        const content = el("div", "appNotificationText");
        content.append(el("strong", "", item.label), el("p", "", item.message));
        const time = el("time", "", `${item.scope} · ${timeFormat.format(new Date(item.time))}`);
        time.dateTime = new Date(item.time).toISOString();
        content.append(time);
        row.append(content);
        if (!item.read) {
          const read = el("button", "appIconButton");
          read.type = "button";
          read.title = "Marcar como lida";
          read.setAttribute("aria-label", read.title);
          read.append(icon("check"));
          read.addEventListener("click", event => { event.stopPropagation(); notificationHistory.read(item.id); renderNotifications(); close.focus(); });
          row.append(read);
        }
        list.append(row);
      });
      window.lucide?.createIcons({root:panel});
    };
    readAll.addEventListener("click", event => { event.stopPropagation(); notificationHistory.read(); renderNotifications(); close.focus(); });
    clear.addEventListener("click", event => { event.stopPropagation(); notificationHistory.clearRead(); renderNotifications(); close.focus(); });
    const sourceLabels = {
      refreshStatus:"Atualização das integrações", sessionStatus:"Conta",
      settingsStatus:"Configurações", quoteFollowupSyncWarning:"Orçamentos",
      historyStatus:"Histórico de pacientes", error:"Cérebro do sistema"
    };
    const sources = [...document.querySelectorAll("#refreshStatus, #sessionStatus, #status, #notice, #settingsStatus, #quoteFollowupSyncWarning, #historyStatus, #error")];
    const lastCaptured = new Map();
    sources.forEach(source => source.classList.add("appNotificationSource"));
    captureNotifications = () => {
      const params = new URLSearchParams(location.search);
      const scope = document.querySelector("#clinicEyebrow, #instituteName, [data-clinic-label], .brandbar .clinic")?.textContent.trim()
        || params.get("clinic") || params.get("institute") || (adminPage ? "Administração" : "Sistema");
      let changed = false;
      sources.forEach(source => {
        const message = source.textContent.trim();
        const key = `${scope}:${source.id}`;
        const signature = `${source.dataset.phase || ""}:${source.classList.contains("error")}:${message}`;
        if (lastCaptured.get(key) === signature) return;
        lastCaptured.set(key, signature);
        if (!message || source.dataset.phase === "running" || /^(Iniciando|Carregando|Atualizando|Salvando)\b/i.test(message)) return;
        const error = source.classList.contains("error") || source.dataset.phase === "error" || /falh|erro|não foi possível|não tem acesso|recusad|negad/i.test(message);
        const warning = /pendên|parcial|conecte|não conectad|não configurad|verificar|atenção/i.test(message);
        const level = error ? "error" : warning ? "warning" : /atualizad|salv[oa]|criad[oa]|concluíd/i.test(message) ? "success" : "info";
        changed = notificationHistory.add(source.id, message, {scope, label:sourceLabels[source.id] || "Sistema", level}) || changed;
      });
      if (changed) renderNotifications();
    };
    const observer = new MutationObserver(captureNotifications);
    sources.forEach(source => observer.observe(source, {childList:true, characterData:true, subtree:true, attributes:true, attributeFilter:["class", "data-phase"]}));
    renderNotifications();
  }
  if (!publicPage) {
    const summary = el("summary", "appAccountToggle");
    summary.setAttribute("aria-label", "Menu da conta");
    const avatar = el("span", "appAvatar", "D");
    avatar.setAttribute("aria-hidden", "true");
    const user = $("[data-session-user]") || el("span");
    user.dataset.sessionUser = "";
    user.classList.add("appUserName");
    summary.append(avatar, user, icon("chevron-down"));
    account.append(summary);
    const menu = el("div", "appAccountMenu");
    const accountSources = [originalToolbar, originalHeader].filter(Boolean);
    accountSources.forEach(source => {
      source.querySelectorAll("[data-master-only], [data-logout], [data-institute-entry], .accountActions > a, .brainHeader nav > a").forEach(node => {
        if (!chrome.contains(node) && node !== settings) menu.append(node);
      });
    });
    utilityNodes.forEach(node => { if (!chrome.contains(node) && node !== change && node !== refresh && node !== settings) menu.append(node); });
    menu.prepend(link("Escolher conta", "/", "building-2"));
    account.append(menu);
    actions.append(account);
    const updateAvatar = () => {
      const text = user.textContent.trim();
      avatar.textContent = text ? text.split(/\s+/).slice(0, 2).map(word => word[0]).join("").toUpperCase() : "D";
    };
    new MutationObserver(updateAvatar).observe(user, {childList:true, characterData:true, subtree:true});
    updateAvatar();
    document.addEventListener("click", event => { if (!account.contains(event.target)) account.open = false; });
    document.addEventListener("keydown", event => { if (event.key === "Escape") account.open = false; });
  }
  topbar.append(actions);
  chrome.append(topbar);
  document.body.prepend(chrome);
  const existingNav = $("#viewTabs") || (institutePage ? $("#instituteApp > .tabs") : null);
  const nav = existingNav || (!publicPage ? el("nav", "appNav") : null);
  if (nav) {
    nav.classList.add("appNav");
    nav.setAttribute("aria-label", institutePage ? "Módulos do instituto" : adminPage ? "Administração" : "Módulos da clínica");
    chrome.append(nav);
  }
  const mobileNav = nav ? el("button", "appMobileNavToggle") : null;
  const mobileNavLabel = el("span", "", "Menu");
  if (mobileNav) {
    mobileNav.type = "button";
    mobileNav.title = "Abrir menu";
    mobileNav.setAttribute("aria-label", "Abrir menu de módulos");
    mobileNav.setAttribute("aria-expanded", "false");
    if (!nav.id) nav.id = "appModuleNav";
    mobileNav.setAttribute("aria-controls", nav.id);
    mobileNav.append(icon("menu"), mobileNavLabel, icon("chevron-down"));
    nav.before(mobileNav);
    const setMenuOpen = (open, restoreFocus = false) => {
      chrome.classList.toggle("appMenuOpen", open);
      mobileNav.setAttribute("aria-expanded", String(open));
      if (open) {
        account.open = false;
        const notifications = chrome.querySelector(".appNotificationPanel");
        if (notifications) notifications.hidden = true;
        chrome.querySelector(".appNotificationToggle")?.setAttribute("aria-expanded", "false");
        nav.querySelector("button:not([hidden]), a:not([hidden])")?.focus();
      } else if (restoreFocus) mobileNav.focus();
    };
    mobileNav.addEventListener("click", () => setMenuOpen(!chrome.classList.contains("appMenuOpen")));
    nav.addEventListener("click", event => {
      if (event.target.closest("button,a") && window.matchMedia("(max-width: 760px)").matches) setMenuOpen(false, true);
    });
    document.addEventListener("click", event => {
      if (!nav.contains(event.target) && !mobileNav.contains(event.target)) setMenuOpen(false);
    });
    document.addEventListener("keydown", event => {
      if (event.key === "Escape" && chrome.classList.contains("appMenuOpen")) setMenuOpen(false, true);
    });
    window.matchMedia("(max-width: 760px)").addEventListener("change", () => setMenuOpen(false));
  }
  const navModels = {
    generalView:["Painel geral", "layout-dashboard"], commercialView:["Comercial", "chart-no-axes-combined"],
    financialView:["Financeiro", "wallet"], patientFollowupView:["Pacientes", "users"],
    quoteFollowupView:["Orçamentos", "file-text"], whatsappAuditView:["WhatsApp", "message-circle"],
    trafficView:["Tráfego pago", "chart-column-increasing"], bodyEvolutionView:["Evolução corporal", "activity"],
    tasksView:["Tarefas", "square-check"], birthdaysView:["Aniversários", "gift"]
  };
  let navObserver;
  let lastActive;
  function decorateNav() {
    navObserver?.disconnect();
    nav?.querySelectorAll("button").forEach(button => {
      const target = button.dataset.target ? new URL(button.dataset.target, location.href) : null;
      const pathViews = {"/tasks.html":"tasksView", "/birthdays.html":"birthdaysView", "/body-evolution.html":"bodyEvolutionView"};
      const view = button.dataset.view || target?.searchParams.get("view") || pathViews[target?.pathname];
      const model = navModels[view];
      if (!model || button.dataset.appLabel === model[0]) return;
      button.title = button.textContent.trim();
      button.dataset.appLabel = model[0];
      button.replaceChildren(icon(model[1]), el("span", "", model[0]));
    });
    window.lucide?.createIcons({root:chrome});
    const active = nav?.querySelector(".active, [aria-current=page], [aria-selected=true]");
    if (active) mobileNavLabel.textContent = active.textContent.trim();
    if (active && active !== lastActive && nav.clientWidth && !window.matchMedia("(max-width: 760px)").matches) {
      const item = active.getBoundingClientRect(), bounds = nav.getBoundingClientRect();
      nav.scrollLeft += item.left - bounds.left - (nav.clientWidth - item.width) / 2;
      lastActive = active;
    }
    navObserver?.observe(nav, {childList:true, subtree:true, attributes:true, attributeFilter:["class", "aria-current", "aria-selected"]});
  }
  if (existingNav) {
    navObserver = new MutationObserver(decorateNav);
    decorateNav();
  } else if (adminPage && nav) {
    nav.append(link("Usuários e acessos", "/master", "users"), link("Cérebro do sistema", "/master/brain.html", "network"), link("Voltar ao sistema", "/", "layout-dashboard"));
    nav.children[page === "master" ? 0 : 1].setAttribute("aria-current", "page");
  }
  function applySession(data) {
    if (notificationHistory) {
      notificationHistory.setUser(data.user?.id);
      renderNotifications();
      captureNotifications();
    }
    if (!nav || existingNav || adminPage || publicPage) return;
    const clinic = new URLSearchParams(location.search).get("clinic") || (page === "body" ? "inspire" : "");
    nav.replaceChildren();
    if (!data.clinics?.some(item => item.key === clinic)) return;
    const name = data.clinics.find(item => item.key === clinic).name;
    const label = context.querySelector("a span");
    if (label) label.textContent = name;
    const views = Object.keys(navModels);
    Object.entries(data.modules || {}).sort(([, a], [, b]) => views.indexOf(a.view) - views.indexOf(b.view)).forEach(([key, module]) => {
      if (!data.permissions?.[clinic]?.includes(`${key}.view`)) return;
      const model = navModels[module.view];
      if (!model) return;
      const path = {tasks:"/tasks.html", birthdays:"/birthdays.html", body_evolution:"/body-evolution.html"}[key];
      const href = path ? `${path}?clinic=${encodeURIComponent(clinic)}` : `/?clinic=${encodeURIComponent(clinic)}&view=${encodeURIComponent(module.view)}`;
      const item = link(model[0], href, model[1]);
      if (page === "body" && key === "body_evolution") item.setAttribute("aria-current", "page");
      nav.append(item);
    });
    decorateNav();
  }
  window.addEventListener("doc4docs-session", event => applySession(event.detail));
  if (window.doc4docsSession) applySession(window.doc4docsSession);
  const sourceName = $("#clinicEyebrow, #instituteName, [data-clinic-label], .brandbar .clinic");
  const dashboard = $("#dashboardShell");
  const instituteApp = $("#instituteApp");
  function updateContext() {
    const choosing = page === "dashboard" ? dashboard?.classList.contains("dashboardHidden") : institutePage ? instituteApp?.hidden : false;
    chrome.classList.toggle("appChoosing", Boolean(choosing));
    if (nav) nav.hidden = Boolean(choosing);
    if (mobileNav) mobileNav.hidden = Boolean(choosing);
    context.hidden = Boolean(choosing);
    actions.classList.toggle("appChoosingActions", Boolean(choosing));
    if (change) {
      const name = sourceName?.textContent.trim() || (institutePage ? "Institutos" : "Clínicas");
      if (change.dataset.appName !== name) {
        change.dataset.appName = name;
        change.replaceChildren(icon(institutePage ? "graduation-cap" : "building-2"), el("span", "", name), icon("chevron-down"));
        window.lucide?.createIcons({root:change});
      }
    }
    const mode = institutePage || (page === "dashboard" && choosing && new URLSearchParams(location.search).get("area") === "institutes") ? "Institutos" : "Clínicas";
    chrome.querySelectorAll(".appAreas a").forEach(node => {
      if (node.textContent === mode) node.setAttribute("aria-current", "page");
      else node.removeAttribute("aria-current");
    });
  }
  const contextObserver = new MutationObserver(updateContext);
  if (sourceName) contextObserver.observe(sourceName, {childList:true, characterData:true, subtree:true});
  if (dashboard) contextObserver.observe(dashboard, {attributes:true, attributeFilter:["class"]});
  if (instituteApp) contextObserver.observe(instituteApp, {attributes:true, attributeFilter:["hidden"]});
  if (page === "dashboard") contextObserver.observe(document.body, {attributes:true, attributeFilter:["class"]});
  document.querySelectorAll("[data-landing-area]").forEach(node => contextObserver.observe(node, {attributes:true, attributeFilter:["aria-selected"]}));
  const sessionStatus = $("#sessionStatus");
  if (sessionStatus && originalToolbar?.contains(sessionStatus)) {
    sessionStatus.classList.add("appSessionStatus");
    chrome.append(sessionStatus);
  }
  if (originalToolbar) originalToolbar.classList.add("appLegacyToolbar");
  if (originalHeader) originalHeader.classList.add("appLegacyHeader");
  document.querySelectorAll('button > img[src="/excel-icon.png"]').forEach(img => {
    const frame = el("span", "appExcelIcon");
    frame.setAttribute("aria-hidden", "true");
    img.before(frame);
    frame.append(img);
  });
  document.body.classList.add("appShellReady");
  updateContext();
  decorateNav();
  window.lucide?.createIcons({root:chrome});
  document.addEventListener("DOMContentLoaded", () => window.lucide?.createIcons({root:chrome}), {once:true});
})();
