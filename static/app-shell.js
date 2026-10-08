(() => {
  "use strict";
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
    if (active && active !== lastActive && nav.clientWidth) {
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
})();
