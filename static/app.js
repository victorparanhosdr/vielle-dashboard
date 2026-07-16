const state = {
  report: null,
  allPipelines: [],
  allDoctors: [],
  selectedPipelines: new Set(),
  selectedDoctor: "",
  dateFrom: "",
  dateTo: "",
  rankings: {},
  selectedClinic: "",
};

const clinics = {
  vielle: {
    id: "vielle",
    name: "Vielle Clinic",
    title: "DASHBOARD ESTRATÉGICO",
    status: "Relatório atual conectado ao Kommo e Clínica Experts.",
    connected: true,
  },
  inspire: {
    id: "inspire",
    name: "Clínica Inspire",
    title: "DASHBOARD ESTRATÉGICO",
    status: "Relatório da Clínica Inspire conectado ao Kommo.",
    connected: true,
  },
};

function fmtDate(value) {
  if (!value) return "-";
  return new Intl.DateTimeFormat("pt-BR", {
    dateStyle: "short",
    timeStyle: "short",
  }).format(new Date(value * 1000));
}

function showNotice(message) {
  const el = document.getElementById("status");
  el.textContent = message;
  el.classList.toggle("visible", Boolean(message));
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, char => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;",
  }[char]));
}

function buildQuery() {
  const params = new URLSearchParams();
  if (state.selectedClinic) params.set("clinic", state.selectedClinic);
  if (state.selectedPipelines.size) {
    params.set("pipeline_ids", [...state.selectedPipelines].join(","));
  }
  if (state.selectedDoctor) params.set("doctor", state.selectedDoctor);
  if (state.dateFrom) params.set("date_from", state.dateFrom);
  if (state.dateTo) params.set("date_to", state.dateTo);
  const query = params.toString();
  return query ? `?${query}` : "";
}

function syncFilterState(report) {
  const filters = report.filters || {};
  state.dateFrom = filters.date_from || state.dateFrom || "";
  state.dateTo = filters.date_to || state.dateTo || "";
  state.selectedDoctor = filters.doctor || state.selectedDoctor || "";
  document.getElementById("dateFrom").value = state.dateFrom;
  document.getElementById("dateTo").value = state.dateTo;
  state.allPipelines = report.pipelines || [];
  state.allDoctors = filters.doctors || state.allDoctors || [];
  renderDoctorFilter();
}

function render() {
  const report = state.report || {};
  const totals = report.totals || {};
  syncFilterState(report);

  document.getElementById("totalLeads").textContent = totals.total_leads || 0;
  document.getElementById("interactedLeads").textContent = report.interacted_leads?.total || 0;
  document.getElementById("totalPipelines").textContent = state.selectedPipelines.size || totals.total_pipelines || 0;
  document.getElementById("totalStatuses").textContent = totals.total_statuses || 0;
  document.getElementById("lastSync").textContent = `Ultima sincronizacao: ${fmtDate(totals.last_synced_at)}`;

  renderPipelineChoices();
  renderDailyChart(report.daily_new_leads || [], "dailyChart", {
    totalLabel: "Novos leads",
    breakdownKey: "by_doctor",
  });
  renderDailyChart(report.clinica_experts?.daily_bookings || [], "bookingChart");
  renderDailyChart(report.interacted_leads?.daily || [], "interactionChart");
  renderClinicaExperts(report.clinica_experts || {});
  renderDoctorCross(report.clinica_experts?.doctor_cross || []);
  renderFinancial(report.financial || {});
  renderStatusColumnChart(report.all_current_status || []);

  applyClinicHeader();
  const clinic = clinics[state.selectedClinic] || clinics.vielle;
  const lastSync = report.last_sync;
  if (state.selectedClinic && !clinic.connected) {
    showNotice(`${clinic.name} criada. Agora precisamos configurar as integrações dela para começar a puxar dados.`);
  } else if (!report.connected) {
    showNotice("Conecte sua conta Kommo para iniciar a primeira sincronizacao.");
  } else if (lastSync && !lastSync.ok) {
    showNotice(lastSync.message || "A ultima sincronizacao nao foi concluida.");
  } else {
    showNotice("");
  }
}

function applyClinicHeader() {
  const clinic = clinics[state.selectedClinic] || clinics.vielle;
  document.title = `${clinic.title} | ${clinic.name}`;
  document.getElementById("clinicEyebrow").textContent = clinic.name;
  document.getElementById("dashboardTitle").textContent = clinic.title;
  document.querySelectorAll("#syncBtn, #connectBtn").forEach(button => {
    button.disabled = !clinic.connected;
    button.title = clinic.connected ? "" : "Configure as integrações desta clínica primeiro.";
  });
  const syncClinicaBtn = document.getElementById("syncClinicaBtn");
  if (syncClinicaBtn) {
    syncClinicaBtn.disabled = !clinic.connected;
    syncClinicaBtn.title = clinic.connected
      ? ""
      : "Configure as integrações desta clínica primeiro.";
  }
  const settingsLink = document.getElementById("settingsLink");
  if (settingsLink) {
    settingsLink.href = `/settings.html?clinic=${encodeURIComponent(clinic.id)}`;
  }
}

function showClinicLanding() {
  document.getElementById("clinicLanding").classList.remove("hidden");
  document.getElementById("dashboardShell").classList.add("dashboardHidden");
}

function showDashboard() {
  document.getElementById("clinicLanding").classList.add("hidden");
  document.getElementById("dashboardShell").classList.remove("dashboardHidden");
}

function clinicAccessKey(clinicId) {
  return `clinicAccess:${clinicId}`;
}

function openClinicAccessModal(clinicId) {
  const clinic = clinics[clinicId] || clinics.vielle;
  const modal = document.getElementById("clinicAccessModal");
  modal.dataset.clinicId = clinic.id;
  document.getElementById("clinicAccessTitle").textContent = clinic.name;
  document.getElementById("clinicAccessSubtitle").textContent = `Digite o código de acesso da ${clinic.name} para continuar.`;
  document.getElementById("clinicAccessError").textContent = "";
  document.getElementById("clinicAccessCode").value = "";
  modal.classList.remove("hidden");
  modal.setAttribute("aria-hidden", "false");
  document.getElementById("clinicAccessCode").focus();
}

function closeClinicAccessModal() {
  const modal = document.getElementById("clinicAccessModal");
  modal.classList.add("hidden");
  modal.setAttribute("aria-hidden", "true");
  modal.dataset.clinicId = "";
}

function requestClinicAccess(clinicId, updateUrl = true) {
  const validClinicId = clinics[clinicId] ? clinicId : "vielle";
  if (sessionStorage.getItem(clinicAccessKey(validClinicId)) === "ok") {
    selectClinic(validClinicId, updateUrl);
    return;
  }
  openClinicAccessModal(validClinicId);
}

function selectClinic(clinicId, updateUrl = true) {
  state.selectedClinic = clinics[clinicId] ? clinicId : "vielle";
  state.report = null;
  state.allPipelines = [];
  state.allDoctors = [];
  state.selectedPipelines.clear();
  state.selectedDoctor = "";
  state.dateFrom = "";
  state.dateTo = "";
  localStorage.setItem("selectedClinic", state.selectedClinic);
  showDashboard();
  applyClinicHeader();
  if (updateUrl) {
    const params = new URLSearchParams(window.location.search);
    params.set("clinic", state.selectedClinic);
    history.pushState(null, "", `${window.location.pathname}?${params.toString()}`);
  }
  loadReport();
}

function emptyReportForClinic(clinicId) {
  const today = new Date().toISOString().slice(0, 10);
  const start = new Date();
  start.setDate(start.getDate() - 30);
  const dateFrom = start.toISOString().slice(0, 10);
  return {
    connected: false,
    filters: {
      pipeline_ids: [],
      doctor: "",
      date_from: dateFrom,
      date_to: today,
      doctors: [],
    },
    totals: { total_leads: 0, total_pipelines: 0, total_statuses: 0, last_synced_at: null },
    pipelines: [],
    by_pipeline: [],
    interacted_leads: { total: 0, by_pipeline: [], daily: [], basis: "Ainda sem integração" },
    by_status: [],
    all_current_status: [],
    daily_new_leads: [],
    agendado_migrations: { total: 0 },
    kommo_panel: { active_conversations: 0, lead_sources: [] },
    clinica_experts: {
      connected: false,
      totals: { patients: 0, bookings: 0, sales: 0, sales_total: 0 },
      bookings_by_status: [],
      daily_bookings: [],
      doctor_cross: [],
      last_sync: null,
    },
    financial: {
      basis: `${clinics[clinicId]?.name || "Clínica"}: aguardando integração`,
      expense_source: "categorias",
      totals: {
        income: 0,
        income_received: 0,
        income_pending: 0,
        expenses: 0,
        expenses_paid: 0,
        expenses_pending: 0,
        balance: 0,
        cash_balance: 0,
        average_ticket: 0,
      },
      daily: [],
      daily_details: {},
      income_by_type: [],
      expenses_by_category: [],
      recent: [],
      sales_intelligence: {
        top_patients: [],
        top_procedures: [],
        procedure_categories: [],
        performance_daily: [],
        basis: "Aguardando integração da clínica.",
      },
    },
    last_sync: null,
  };
}

const brl = new Intl.NumberFormat("pt-BR", {
  style: "currency",
  currency: "BRL",
  maximumFractionDigits: 0,
});

const bookingStatusLabels = {
  done: "Concluído",
  canceled: "Cancelado",
  cancelled: "Cancelado",
  rescheduled: "Remarcado",
  scheduled: "Agendado",
  noshow: "Não compareceu",
  pending: "Pendente",
  confirmed: "Confirmado",
};

function renderClinicaExperts(clinica) {
  const totals = clinica.totals || {};
  const log = clinica.last_sync;
  document.getElementById("clinicaPatients").textContent = totals.patients || 0;
  document.getElementById("clinicaBookings").textContent = totals.bookings || 0;
  document.getElementById("clinicaSales").textContent = totals.sales || 0;
  document.getElementById("clinicaSalesTotal").textContent = brl.format(totals.sales_total || 0);
  document.getElementById("clinicaStatus").textContent = log
    ? (log.ok ? `Atualizado: ${fmtDate(log.finished_at)}` : `Erro: ${friendlyError(log.message)}`)
    : (clinica.connected ? "Token configurado" : "Token não configurado");
  const translatedStatus = (clinica.bookings_by_status || []).map(row => ({
    ...row,
    status_label: bookingStatusLabels[String(row.status || "").toLowerCase()] || row.status || "Sem status",
  }));
  renderNumberList("clinicaBookingStatus", translatedStatus, "status_label");
}

function renderDoctorCross(rows) {
  const el = document.getElementById("doctorCross");
  el.innerHTML = rows.length
    ? rows.map(row => `
      <article class="doctorCard">
        <div>
          <h3>${escapeHtml(row.doctor)}</h3>
          <p>${escapeHtml((row.pipelines || []).join(" + "))}</p>
        </div>
        <div class="doctorMetrics">
          <span><b>${row.new_leads || 0}</b> leads</span>
          <span><b>${row.bookings || 0}</b> agend.</span>
          <span><b>${row.bookings_done || 0}</b> feitos</span>
          <span><b>${row.sales || 0}</b> vendas</span>
        </div>
        <div class="conversionLine">
          <i style="width:${Math.min(100, Math.round((row.lead_to_booking_rate || 0) * 100))}%"></i>
        </div>
        <footer>
          <span>Lead → agendamento: ${formatPercent(row.lead_to_booking_rate)}</span>
          <strong>${brl.format(row.sales_total || 0)}</strong>
        </footer>
      </article>
    `).join("")
    : `<div class="empty">Nenhum funil considerado no filtro atual.</div>`;
}

function renderFinancial(financial) {
  const totals = financial.totals || {};
  document.getElementById("financeBasis").textContent = `${financial.basis || "Clínica Experts"} · saídas por ${financial.expense_source || "categorias"}`;
  document.getElementById("financeIncome").textContent = brl.format(totals.income || 0);
  document.getElementById("financeReceived").textContent = brl.format(totals.income_received || 0);
  document.getElementById("financeReceivable").textContent = brl.format(totals.income_pending || 0);
  document.getElementById("financeExpenses").textContent = brl.format(totals.expenses || 0);
  document.getElementById("financePaidExpenses").textContent = brl.format(totals.expenses_paid || 0);
  document.getElementById("financeOpenExpenses").textContent = brl.format(totals.expenses_pending || 0);
  document.getElementById("financeCashBalance").textContent = brl.format(totals.cash_balance || 0);
  document.getElementById("financeBalance").textContent = brl.format(totals.balance || 0);
  document.getElementById("financeAverageTicket").textContent = brl.format(totals.average_ticket || 0);
  renderFinanceDailyChart(financial.daily || [], financial.daily_details || {});
  renderFinanceList("financeIncomeTypes", financial.income_by_type || [], "amount");
  renderFinanceList("financeExpenseTypes", financial.expenses_by_category || [], "amount", "category", {
    showShare: true,
    shareTotal: totals.expenses || 0,
  });
  renderFinanceRecent(financial.recent || []);
  renderSalesIntelligence(financial.sales_intelligence || {});
}

function renderSalesIntelligence(data) {
  state.rankings = {
    patients: data.top_patients || [],
    procedures: data.top_procedures || [],
    categories: data.procedure_categories || [],
  };
  document.getElementById("salesIntelligenceBasis").textContent = data.basis || "Clínica Experts";
  renderSalesPerformanceChart(data.performance_daily || []);
  renderRankList("topPatients", state.rankings.patients, {
    type: "patients",
    modalTitle: "Ranking completo de pacientes",
    titleKey: "patient",
    subtitle: item => `${item.sales || 0} venda${(item.sales || 0) === 1 ? "" : "s"}`,
    amountKey: "amount",
  });
  renderRankList("topProcedures", state.rankings.procedures, {
    type: "procedures",
    modalTitle: "Ranking completo de procedimentos",
    titleKey: "procedure",
    subtitle: item => `${Math.round(item.quantity || 0)} un. · ${item.category || "Sem categoria"}`,
    amountKey: "amount",
  });
  renderRankList("procedureCategories", state.rankings.categories, {
    type: "categories",
    modalTitle: "Categorias de procedimento",
    titleKey: "category",
    subtitle: item => `${Math.round(item.quantity || 0)} un. · ${item.procedures || 0} procedimento${(item.procedures || 0) === 1 ? "" : "s"}`,
    amountKey: "amount",
  });
}

function renderRankList(id, items, config) {
  const el = document.getElementById(id);
  const medals = ["gold", "silver", "bronze"];
  const sortedItems = [...items].sort((a, b) => (b[config.amountKey] || 0) - (a[config.amountKey] || 0));
  el.innerHTML = items.length
    ? `
      ${sortedItems.slice(0, 3).map((item, index) => rankItemMarkup(item, index, config, medals)).join("")}
      <button class="rankMore" type="button" data-rank-open="${escapeHtml(config.type)}">Ver ranking completo</button>
    `
    : `<div class="empty">Sem vendas no período selecionado.</div>`;
  el.querySelectorAll("[data-rank-open], .rankItem").forEach(item => {
    item.addEventListener("click", () => openRankModal(config.type, config));
    item.addEventListener("keydown", event => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        openRankModal(config.type, config);
      }
    });
  });
}

function rankItemMarkup(item, index, config, medals = ["gold", "silver", "bronze"]) {
  return `
    <article class="rankItem ${medals[index] || ""}" role="button" tabindex="0">
      <div class="rankPosition">
        <b>${index + 1}</b>
        ${index < 3 ? "<i>★</i>" : ""}
      </div>
      <div class="rankText">
        <strong>${escapeHtml(item[config.titleKey] || "-")}</strong>
        <span>${escapeHtml(config.subtitle(item))}</span>
      </div>
      <em>${brl.format(item[config.amountKey] || 0)}</em>
    </article>
  `;
}

function openRankModal(type, config = null) {
  const modal = document.getElementById("rankModal");
  const title = document.getElementById("rankModalTitle");
  const body = document.getElementById("rankModalBody");
  const fallbackConfigs = {
    patients: {
      modalTitle: "Ranking completo de pacientes",
      titleKey: "patient",
      subtitle: item => `${item.sales || 0} venda${(item.sales || 0) === 1 ? "" : "s"}`,
      amountKey: "amount",
    },
    procedures: {
      modalTitle: "Ranking completo de procedimentos",
      titleKey: "procedure",
      subtitle: item => `${Math.round(item.quantity || 0)} un. · ${item.category || "Sem categoria"}`,
      amountKey: "amount",
    },
    categories: {
      modalTitle: "Categorias de procedimento",
      titleKey: "category",
      subtitle: item => `${Math.round(item.quantity || 0)} un. · ${item.procedures || 0} procedimento${(item.procedures || 0) === 1 ? "" : "s"}`,
      amountKey: "amount",
    },
  };
  const rankConfig = { ...(fallbackConfigs[type] || {}), ...(config || {}) };
  const items = [...(state.rankings[type] || [])].sort((a, b) => (b[rankConfig.amountKey] || 0) - (a[rankConfig.amountKey] || 0));
  title.textContent = rankConfig.modalTitle || "Ranking completo";
  body.innerHTML = items.length
    ? items.map((item, index) => rankItemMarkup(item, index, rankConfig)).join("")
    : `<div class="empty">Sem dados no período selecionado.</div>`;
  modal.hidden = false;
  document.body.classList.add("modalOpen");
}

function closeRankModal() {
  document.getElementById("rankModal").hidden = true;
  document.body.classList.remove("modalOpen");
}

function renderSalesPerformanceChart(items) {
  const el = document.getElementById("salesPerformanceChart");
  const activeItems = items.filter(item => (item.revenue || 0) || (item.quoted || 0) || (item.sales || 0));
  if (!activeItems.length) {
    el.innerHTML = `<div class="empty">Sem dados de vendas e orçamentos no período selecionado.</div>`;
    return;
  }
  const width = Math.max(760, activeItems.length * 44);
  const height = 320;
  const pad = { top: 28, right: 58, bottom: 52, left: 78 };
  const chartW = width - pad.left - pad.right;
  const chartH = height - pad.top - pad.bottom;
  const maxMoney = Math.max(...activeItems.map(item => Math.max(item.revenue || 0, item.quoted || 0)), 1);
  const maxSales = Math.max(...activeItems.map(item => item.sales || 0), 1);
  const xFor = index => pad.left + (activeItems.length === 1 ? chartW / 2 : (index / (activeItems.length - 1)) * chartW);
  const yMoney = value => pad.top + chartH - ((value || 0) / maxMoney) * chartH;
  const ySales = value => pad.top + chartH - ((value || 0) / maxSales) * chartH;
  const linePath = (key, yFn) => activeItems.map((item, index) => `${index ? "L" : "M"} ${xFor(index).toFixed(1)} ${yFn(item[key]).toFixed(1)}`).join(" ");
  const areaPath = key => `${linePath(key, yMoney)} L ${xFor(activeItems.length - 1).toFixed(1)} ${pad.top + chartH} L ${xFor(0).toFixed(1)} ${pad.top + chartH} Z`;
  const moneyTicks = [0, .25, .5, .75, 1].map(ratio => {
    const value = maxMoney * ratio;
    const y = yMoney(value);
    return `
      <line class="lineGrid" x1="${pad.left}" y1="${y}" x2="${width - pad.right}" y2="${y}"></line>
      <text class="chartAxis" x="${pad.left - 12}" y="${y + 4}" text-anchor="end">${moneyShort(value)}</text>
    `;
  }).join("");
  const salesTicks = [0, .5, 1].map(ratio => {
    const value = Math.round(maxSales * ratio);
    const y = ySales(value);
    return `<text class="chartAxis" x="${width - pad.right + 12}" y="${y + 4}">${value}</text>`;
  }).join("");
  const labels = activeItems.map((item, index) => {
    if (activeItems.length > 16 && index % Math.ceil(activeItems.length / 12)) return "";
    return `<text class="pointDate tilted" x="${xFor(index)}" y="${height - 18}">${formatShortDay(item.day)}</text>`;
  }).join("");
  const points = activeItems.map((item, index) => {
    const x = xFor(index);
    return `
      <g>
        <circle class="chartPoint revenue" cx="${x}" cy="${yMoney(item.revenue)}" r="4"></circle>
        <circle class="chartPoint sales" cx="${x}" cy="${ySales(item.sales)}" r="4"></circle>
        <circle class="chartPoint quoted" cx="${x}" cy="${yMoney(item.quoted)}" r="4"></circle>
      </g>
    `;
  }).join("");
  const hitZones = activeItems.map((item, index) => {
    const x = xFor(index);
    const previous = index ? xFor(index - 1) : pad.left;
    const next = index < activeItems.length - 1 ? xFor(index + 1) : width - pad.right;
    const hitW = Math.max(24, (next - previous) / 2);
    return `<rect class="chartHitZone" data-index="${index}" x="${x - hitW / 2}" y="${pad.top}" width="${hitW}" height="${chartH}"></rect>`;
  }).join("");
  el.innerHTML = `
    <div class="performanceLegend">
      <span><i class="revenueLine"></i>Faturamento</span>
      <span><i class="salesLine"></i>Vendas</span>
      <span><i class="quotedLine"></i>Orçado</span>
    </div>
    <div class="lineChartScroller">
      <svg class="performanceSvg" viewBox="0 0 ${width} ${height}" role="img" aria-label="Faturamento, vendas e orçado dia a dia">
        <defs>
          <linearGradient id="revenueArea" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stop-color="#8a45ff" stop-opacity=".26"></stop>
            <stop offset="100%" stop-color="#8a45ff" stop-opacity="0"></stop>
          </linearGradient>
          <linearGradient id="quotedArea" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stop-color="#16c784" stop-opacity=".22"></stop>
            <stop offset="100%" stop-color="#16c784" stop-opacity="0"></stop>
          </linearGradient>
        </defs>
        ${moneyTicks}
        ${salesTicks}
        <path class="chartArea revenueArea" d="${areaPath("revenue")}"></path>
        <path class="chartArea quotedArea" d="${areaPath("quoted")}"></path>
        <path class="performanceLine revenueStroke" d="${linePath("revenue", yMoney)}"></path>
        <path class="performanceLine salesStroke" d="${linePath("sales", ySales)}"></path>
        <path class="performanceLine quotedStroke" d="${linePath("quoted", yMoney)}"></path>
        ${points}
        ${hitZones}
        ${labels}
      </svg>
      <div class="chartTooltip" hidden></div>
    </div>
  `;
  const tooltip = el.querySelector(".chartTooltip");
  el.querySelectorAll(".chartHitZone").forEach(zone => {
    zone.addEventListener("mouseenter", event => {
      showSalesTooltip(event, activeItems[Number(zone.dataset.index)], tooltip, el);
    });
    zone.addEventListener("mousemove", event => {
      showSalesTooltip(event, activeItems[Number(zone.dataset.index)], tooltip, el);
    });
    zone.addEventListener("mouseleave", () => {
      tooltip.hidden = true;
    });
  });
}

function showSalesTooltip(event, item, tooltip, container) {
  if (!item) return;
  tooltip.innerHTML = `
    <strong>${formatDay(item.day)}</strong>
    <span><i class="revenueDot"></i>Faturamento: ${brl.format(item.revenue || 0)}</span>
    <span><i class="salesDot"></i>Vendas: ${item.sales || 0}</span>
    <span><i class="quotedDot"></i>Orçado: ${brl.format(item.quoted || 0)}</span>
  `;
  const bounds = container.getBoundingClientRect();
  tooltip.hidden = false;
  const tooltipWidth = tooltip.offsetWidth || 250;
  const left = Math.min(
    Math.max(12, event.clientX - bounds.left - tooltipWidth / 2),
    bounds.width - tooltipWidth - 12
  );
  const top = Math.max(52, event.clientY - bounds.top - tooltip.offsetHeight - 18);
  tooltip.style.left = `${left}px`;
  tooltip.style.top = `${top}px`;
}

function moneyShort(value) {
  if (!value) return "0";
  if (value >= 1000) return `R$ ${(value / 1000).toFixed(0)}k`;
  return brl.format(value);
}

function formatShortDay(value) {
  if (!value) return "-";
  const [year, month, day] = value.split("-");
  return `${Number(day)}/${Number(month)}`;
}

function renderFinanceDailyChart(items, detailsByDay = {}) {
  const el = document.getElementById("financeDailyChart");
  const activeItems = items.filter(item => (item.income || 0) || (item.expenses || 0));
  if (!activeItems.length) {
    el.innerHTML = `<div class="empty">Sem dados financeiros no período selecionado.</div>`;
    return;
  }
  const max = Math.max(...activeItems.map(item => Math.max(item.income || 0, item.expenses || 0)), 1);
  el.innerHTML = `
    <div class="financeLegend">
      <span><i class="incomeDot"></i>Entradas</span>
      <span><i class="expenseDot"></i>Saídas</span>
    </div>
    <div class="financeDailyList">
      ${activeItems.map(item => {
        const incomeWidth = Math.max(2, ((item.income || 0) / max) * 100);
        const expenseWidth = Math.max(2, ((item.expenses || 0) / max) * 100);
        return `
          <details class="financeDailyRow" title="Entradas: ${escapeHtml(brl.format(item.income || 0))} | Saídas: ${escapeHtml(brl.format(item.expenses || 0))} | Saldo: ${escapeHtml(brl.format(item.balance || 0))}">
            <summary>
              <strong>${formatDay(item.day)}</strong>
              <div class="financeDailyBars">
                <span><i class="incomeBarLine" style="width:${incomeWidth}%"></i></span>
                <span><i class="expenseBarLine" style="width:${expenseWidth}%"></i></span>
              </div>
              <div class="financeDailyValues">
                <b class="in">${brl.format(item.income || 0)}</b>
                <b class="out">${brl.format(item.expenses || 0)}</b>
                <b>${brl.format(item.balance || 0)}</b>
              </div>
            </summary>
            <div class="financeLaunchList">
              ${renderFinanceLaunches(detailsByDay[item.day] || [])}
            </div>
          </details>
        `;
      }).join("")}
    </div>
  `;
}

function renderFinanceLaunches(items) {
  if (!items.length) return `<div class="empty">Sem detalhes de lançamento nesse dia.</div>`;
  return items.map(item => `
    <article class="${item.direction === "saida" ? "out" : "in"}">
      <div>
        <strong>${escapeHtml(item.description || "-")}</strong>
        <span>${escapeHtml(item.detail || "Clínica Experts")} · ${financeSettlementText(item)}</span>
      </div>
      <b>${item.direction === "saida" ? "-" : "+"}${brl.format(item.amount || 0)}</b>
    </article>
  `).join("");
}

function renderFinanceList(id, items, amountKey, labelKey = "type", options = {}) {
  const el = document.getElementById(id);
  const sortedItems = [...items].sort((a, b) => (b[amountKey] || 0) - (a[amountKey] || 0));
  const max = Math.max(...sortedItems.map(item => item[amountKey] || 0), 1);
  const shareTotal = options.shareTotal || sortedItems.reduce((sum, item) => sum + (item[amountKey] || 0), 0);
  el.innerHTML = sortedItems.length
    ? sortedItems.map(item => {
      const amount = item[amountKey] || 0;
      const share = shareTotal ? amount / shareTotal : null;
      return `
      <div class="financeItem">
        <div>
          <strong>${escapeHtml(labelKey === "category" ? (item.category || "Sem categoria") : financeTypeLabel(item.type))}</strong>
          <span>${financeListSubtitle(item)}</span>
          <div class="bar"><i style="width:${Math.max(4, (amount / max) * 100)}%"></i></div>
        </div>
        <div class="financeAmount">
          <b>${brl.format(amount)}</b>
          ${options.showShare ? `<small>${formatPercent(share)} do total</small>` : ""}
        </div>
      </div>
    `;
    }).join("")
    : `<div class="empty">Sem dados financeiros para o filtro selecionado.</div>`;
}

function renderFinanceRecent(items) {
  const el = document.getElementById("financeRecent");
  el.innerHTML = items.length
    ? items.map(item => `
      <article class="${item.direction === "saida" ? "out" : "in"}">
        <div>
          <strong>${escapeHtml(item.description || "-")}</strong>
          <span>${formatDay(item.date)} · ${escapeHtml(item.detail || "Clínica Experts")} · ${financeSettlementText(item)}</span>
        </div>
        <b>${item.direction === "saida" ? "-" : "+"}${brl.format(item.amount || 0)}</b>
      </article>
    `).join("")
    : `<div class="empty">Sem lançamentos no período selecionado.</div>`;
}

function financeListSubtitle(item) {
  const totalText = `${item.total || 0} lançamento${(item.total || 0) === 1 ? "" : "s"}`;
  if (item.settled === undefined && item.open_amount === undefined) return totalText;
  return `${totalText} · pago/recebido ${brl.format(item.settled || 0)} · aberto ${brl.format(item.open_amount || 0)}`;
}

function financeSettlementText(item) {
  const settled = item.settled || 0;
  const open = item.open_amount || 0;
  if (!settled && !open) return "quitado";
  if (open && settled) return `${brl.format(settled)} quitado · ${brl.format(open)} aberto`;
  if (open) return `${brl.format(open)} em aberto`;
  return `${brl.format(settled)} quitado`;
}

function financeTypeLabel(type) {
  const labels = {
    sale: "Venda",
    combo: "Combo",
    credit: "Crédito",
    order: "Pedido",
    bill: "Conta",
    shopping: "Compra",
    commission: "Comissão",
    withdraw: "Saque",
    supply: "Suprimento",
    initial_balance: "Saldo inicial",
  };
  return labels[String(type || "").toLowerCase()] || type || "Sem tipo";
}

function formatPercent(value) {
  if (value === null || value === undefined) return "-";
  return new Intl.NumberFormat("pt-BR", { style: "percent", maximumFractionDigits: 1 }).format(value);
}

function renderStatusColumnChart(items) {
  const el = document.getElementById("statusColumnChart");
  const grouped = new Map();
  items.forEach(item => {
    const name = item.status_name || "-";
    const current = grouped.get(name) || { status_name: name, total: 0, pipelines: new Set() };
    current.total += item.total || 0;
    if (item.pipeline_name) current.pipelines.add(item.pipeline_name);
    grouped.set(name, current);
  });
  const sortedItems = [...grouped.values()]
    .map(item => ({ ...item, pipelines: [...item.pipelines] }))
    .sort((a, b) => (b.total || 0) - (a.total || 0));
  const max = Math.max(...sortedItems.map(item => item.total), 1);
  el.innerHTML = sortedItems.length
    ? `
      <div class="statusRankSummary">
        <strong>${sortedItems.reduce((sum, item) => sum + (item.total || 0), 0)}</strong>
        <span>leads distribuídos em ${sortedItems.length} fases</span>
      </div>
      <div class="statusRankList">
          ${sortedItems.map(item => {
            const width = Math.max(2, ((item.total || 0) / max) * 100);
            const pipelineText = item.pipelines.length === 1
              ? item.pipelines[0]
              : `${item.pipelines.length} funis`;
            const title = `${item.status_name || "-"}: ${item.total || 0} leads`;
            return `
              <article class="statusRankItem" title="${escapeHtml(title)}">
                <div class="statusRankText">
                  <strong>${escapeHtml(item.status_name || "-")}</strong>
                  <span>${escapeHtml(pipelineText)}</span>
                </div>
                <div class="statusRankBar">
                  <i style="width:${width}%"></i>
                </div>
                <b>${item.total || 0}</b>
              </article>
            `;
          }).join("")}
      </div>
    `
    : `<div class="empty">Sem fases para o filtro selecionado.</div>`;
}

function renderPipelineChoices() {
  const wrap = document.getElementById("pipelineChoices");
  const summary = document.getElementById("pipelineSummary");
  const selectedCount = state.selectedPipelines.size;
  summary.textContent = selectedCount
    ? `${selectedCount} funil${selectedCount > 1 ? "is" : ""} selecionado${selectedCount > 1 ? "s" : ""}`
    : "Todos considerados";
  const sortedPipelines = [...state.allPipelines].sort((a, b) => (b.total || 0) - (a.total || 0));
  wrap.innerHTML = sortedPipelines.length
    ? sortedPipelines.map(pipeline => {
      const checked = state.selectedPipelines.has(String(pipeline.id)) ? "checked" : "";
      return `
        <label class="pipelineChip">
          <input type="checkbox" value="${pipeline.id}" ${checked}>
          <span>${escapeHtml(pipeline.name)}</span>
          <strong>${pipeline.total || 0}</strong>
        </label>
      `;
    }).join("")
    : `<div class="empty">Nenhum funil encontrado.</div>`;

  wrap.querySelectorAll("input[type='checkbox']").forEach(input => {
    input.addEventListener("change", () => {
      if (input.checked) state.selectedPipelines.add(input.value);
      else state.selectedPipelines.delete(input.value);
      loadReport();
    });
  });
}

function renderDoctorFilter() {
  const select = document.getElementById("doctorFilter");
  const currentOptions = [...select.options].map(option => option.value).join("|");
  const nextOptions = ["", ...state.allDoctors].join("|");
  if (currentOptions !== nextOptions) {
    select.innerHTML = `
      <option value="">Todos considerados</option>
      ${state.allDoctors.map(doctor => `<option value="${escapeHtml(doctor)}">${escapeHtml(doctor)}</option>`).join("")}
    `;
  }
  select.value = state.selectedDoctor;
}

function renderDailyChart(items, targetId = "dailyChart", options = {}) {
  const el = document.getElementById(targetId);
  if (!items.length) {
    el.innerHTML = `<div class="empty">Sem dados no periodo selecionado.</div>`;
    return;
  }

  const width = Math.max(720, items.length * 54);
  const height = 250;
  const pad = { top: 32, right: 24, bottom: 38, left: 34 };
  const chartWidth = width - pad.left - pad.right;
  const chartHeight = height - pad.top - pad.bottom;
  const max = Math.max(...items.map(item => item.total), 1);
  const step = items.length > 1 ? chartWidth / (items.length - 1) : chartWidth;
  const points = items.map((item, index) => {
    const x = pad.left + (index * step);
    const y = pad.top + ((max - item.total) / max) * chartHeight;
    return { ...item, x, y };
  });
  const line = points.map(point => `${point.x},${point.y}`).join(" ");
  const area = [
    `${pad.left},${pad.top + chartHeight}`,
    ...points.map(point => `${point.x},${point.y}`),
    `${pad.left + chartWidth},${pad.top + chartHeight}`,
  ].join(" ");
  const grid = [0, .25, .5, .75, 1].map(ratio => {
    const y = pad.top + chartHeight * ratio;
    return `<line class="lineGrid" x1="${pad.left}" y1="${y}" x2="${pad.left + chartWidth}" y2="${y}"></line>`;
  }).join("");
  const hitZones = points.map((point, index) => {
    const prevX = index === 0 ? pad.left : (points[index - 1].x + point.x) / 2;
    const nextX = index === points.length - 1 ? pad.left + chartWidth : (point.x + points[index + 1].x) / 2;
    return `
      <rect class="chartHitZone dailyHitZone" data-index="${index}" x="${prevX}" y="${pad.top - 16}" width="${Math.max(28, nextX - prevX)}" height="${chartHeight + 44}"></rect>
    `;
  }).join("");

  el.innerHTML = `
    <div class="lineChartScroller">
      <svg class="lineChart" viewBox="0 0 ${width} ${height}" role="img" aria-label="Grafico dia a dia">
        <defs>
          <linearGradient id="${targetId}Stroke" x1="0" x2="1" y1="0" y2="0">
            <stop offset="0%" stop-color="#13a8c8"></stop>
            <stop offset="55%" stop-color="#39c6e2"></stop>
            <stop offset="100%" stop-color="#7167e8"></stop>
          </linearGradient>
          <linearGradient id="${targetId}Area" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stop-color="#13a8c8" stop-opacity=".22"></stop>
            <stop offset="100%" stop-color="#7167e8" stop-opacity=".03"></stop>
          </linearGradient>
        </defs>
        ${grid}
        <polygon class="lineArea" points="${area}" fill="url(#${targetId}Area)"></polygon>
        <polyline class="lineStroke" points="${line}" stroke="url(#${targetId}Stroke)"></polyline>
        ${points.map(point => `
          <g class="linePoint">
            <circle cx="${point.x}" cy="${point.y}" r="5"></circle>
            <text class="pointValue" x="${point.x}" y="${Math.max(16, point.y - 12)}">${point.total}</text>
            <text class="pointDate" x="${point.x}" y="${height - 12}">${formatDay(point.day)}</text>
          </g>
        `).join("")}
        ${hitZones}
      </svg>
    </div>
    <div class="chartTooltip dailyTooltip" hidden></div>
  `;
  const tooltip = el.querySelector(".chartTooltip");
  el.querySelectorAll(".dailyHitZone").forEach(zone => {
    zone.addEventListener("mouseenter", event => {
      showDailyTooltip(event, points[Number(zone.dataset.index)], tooltip, el, options);
    });
    zone.addEventListener("mousemove", event => {
      showDailyTooltip(event, points[Number(zone.dataset.index)], tooltip, el, options);
    });
    zone.addEventListener("mouseleave", () => {
      tooltip.hidden = true;
    });
  });
}

function showDailyTooltip(event, item, tooltip, container, options = {}) {
  if (!item) return;
  const breakdown = options.breakdownKey && Array.isArray(item[options.breakdownKey]) ? item[options.breakdownKey] : [];
  const breakdownHtml = options.breakdownKey
    ? (breakdown.length
      ? breakdown.map(row => `
        <span class="tooltipSplit">
          <span><i class="dailyLeadDot"></i>${escapeHtml(row.doctor || "Sem profissional")}</span>
          <b>${row.total || 0}</b>
        </span>
      `).join("")
      : `<span><i class="dailyLeadDot"></i>Sem profissional definido</span>`)
    : "";
  tooltip.innerHTML = `
    <strong>${formatDay(item.day)}</strong>
    <span><i class="dailyTotalDot"></i>${escapeHtml(options.totalLabel || "Total")}: ${item.total || 0}</span>
    ${options.breakdownKey ? `<div class="tooltipDivider"></div>` : ""}
    ${breakdownHtml}
  `;
  const bounds = container.getBoundingClientRect();
  tooltip.hidden = false;
  const tooltipWidth = tooltip.offsetWidth || 280;
  const left = Math.min(
    Math.max(12, event.clientX - bounds.left - tooltipWidth / 2),
    bounds.width - tooltipWidth - 12
  );
  const top = Math.max(42, event.clientY - bounds.top - tooltip.offsetHeight - 18);
  tooltip.style.left = `${left}px`;
  tooltip.style.top = `${top}px`;
}

function renderKommoPanel(panel) {
  document.getElementById("activeConversations").textContent = panel.active_conversations ?? 0;
  document.getElementById("unansweredConversations").textContent = panel.unanswered_conversations ?? "-";
  document.getElementById("responseTime").textContent = formatMinutes(panel.response_time_minutes);
  document.getElementById("longestWait").textContent = formatMinutes(panel.longest_wait_minutes);
  renderSources(panel.lead_sources || []);
}

function formatMinutes(value) {
  if (value === null || value === undefined) return "-";
  if (value < 60) return `${Math.round(value)}m`;
  const hours = Math.floor(value / 60);
  const minutes = Math.round(value % 60);
  return `${hours}h ${minutes}m`;
}

function renderSources(items) {
  const donut = document.getElementById("sourceDonut");
  const legend = document.getElementById("sourceLegend");
  const colors = ["#f4c83f", "#0f766e", "#7c6df2", "#e06f5f", "#4a90b8", "#9b7f4a", "#4f9b62", "#c45aa0"];
  const total = items.reduce((sum, item) => sum + (item.total || 0), 0);
  if (!items.length || !total) {
    donut.style.background = "#edf1f6";
    legend.innerHTML = `<div class="empty">Sem fontes no período.</div>`;
    return;
  }
  let cursor = 0;
  const stops = items.map((item, index) => {
    const start = cursor;
    const pct = ((item.total || 0) / total) * 100;
    cursor += pct;
    const color = colors[index % colors.length];
    return `${color} ${start}% ${cursor}%`;
  });
  donut.style.background = `conic-gradient(${stops.join(", ")})`;
  legend.innerHTML = items.map((item, index) => `
    <div class="sourceItem">
      <i style="background:${colors[index % colors.length]}"></i>
      <span>${escapeHtml(item.name)}</span>
      <strong>${item.total}</strong>
    </div>
  `).join("");
}

function renderNumberList(id, items, titleKey, subtitleKey) {
  const el = document.getElementById(id);
  const sortedItems = [...items].sort((a, b) => (b.total || 0) - (a.total || 0));
  const max = Math.max(...sortedItems.map(item => item.total), 1);
  el.innerHTML = sortedItems.length
    ? sortedItems.map(item => `
      <div class="numberItem">
        <div>
          <strong>${escapeHtml(item[titleKey] || "-")}</strong>
          ${subtitleKey ? `<span>${escapeHtml(item[subtitleKey] || "")}</span>` : ""}
          <div class="bar"><i style="width:${Math.max(4, (item.total / max) * 100)}%"></i></div>
        </div>
        <b>${item.total}</b>
      </div>
    `).join("")
    : `<div class="empty">Sem dados para o filtro selecionado.</div>`;
}

function formatDay(day) {
  if (!day) return "-";
  const [year, month, date] = day.split("-");
  return `${date}/${month}`;
}

async function loadReport() {
  if (!state.selectedClinic) {
    showClinicLanding();
    return;
  }
  const res = await fetch(`/api/report${buildQuery()}`);
  const payload = await res.json();
  if (res.status === 401) {
    sessionStorage.removeItem(clinicAccessKey(state.selectedClinic));
    showDashboard();
    openClinicAccessModal(state.selectedClinic);
    showNotice(payload.error || "Digite o código de acesso para continuar.");
    return;
  }
  state.report = payload;
  render();
}

async function syncNow() {
  const btn = document.getElementById("syncBtn");
  btn.disabled = true;
  btn.textContent = "Atualizando...";
  try {
    const res = await fetch(`/api/sync${buildQuery()}`);
    const payload = await res.json();
    if (!payload.ok) throw new Error(payload.error || "Nao foi possivel sincronizar.");
    state.allPipelines = [];
    await loadReport();
  } catch (error) {
    showNotice(error.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "Atualizar";
  }
}

async function syncClinicaNow() {
  const btn = document.getElementById("syncClinicaBtn");
  btn.disabled = true;
  btn.textContent = "Buscando historico...";
  try {
    const separator = buildQuery() ? "&" : "?";
    const res = await fetch(`/api/sync-clinica${buildQuery()}${separator}historical=1`);
    const payload = await res.json();
    if (!payload.ok) throw new Error(payload.error || "Nao foi possivel sincronizar Clínica Experts.");
    await loadReport();
  } catch (error) {
    showNotice(friendlyError(error.message));
    await loadReport();
  } finally {
    btn.disabled = false;
    btn.textContent = "Atualizar Clínica Experts";
  }
}

function friendlyError(message) {
  const text = String(message || "");
  if (text.includes("502") || text.includes("503") || text.includes("504") || text.includes("Bad gateway")) {
    return "Clínica Experts está temporariamente indisponível. Tente atualizar novamente em alguns minutos.";
  }
  if (text.includes("Too Many Attempts") || text.includes("429")) {
    return "Clínica Experts limitou muitas tentativas. Aguarde alguns minutos e tente atualizar de novo.";
  }
  if (text.length > 220) return `${text.slice(0, 220)}...`;
  return text;
}

function exportPdf() {
  const btn = document.getElementById("exportPdfBtn");
  const original = btn.textContent;
  const params = new URLSearchParams(buildQuery().replace(/^\?/, ""));
  params.set("view", document.querySelector(".tabBtn.active")?.dataset.view || "commercialView");
  btn.disabled = true;
  btn.textContent = "Gerando PDF...";
  window.location.href = `/api/export-pdf?${params.toString()}`;
  setTimeout(() => {
    btn.disabled = false;
    btn.textContent = original;
  }, 1600);
}

document.getElementById("connectBtn").addEventListener("click", () => {
  window.location.href = `/auth/start${buildQuery()}`;
});

document.getElementById("syncBtn").addEventListener("click", syncNow);
document.getElementById("syncClinicaBtn").addEventListener("click", syncClinicaNow);
document.getElementById("exportPdfBtn").addEventListener("click", exportPdf);
document.querySelectorAll("[data-rank-close]").forEach(button => {
  button.addEventListener("click", closeRankModal);
});
document.addEventListener("keydown", event => {
  if (event.key === "Escape" && !document.getElementById("rankModal").hidden) {
    closeRankModal();
  }
});
document.querySelectorAll(".tabBtn").forEach(button => {
  button.addEventListener("click", () => {
    document.querySelectorAll(".tabBtn").forEach(tab => tab.classList.toggle("active", tab === button));
    document.querySelectorAll(".viewPanel").forEach(panel => {
      panel.classList.toggle("active", panel.id === button.dataset.view);
    });
  });
});
document.getElementById("selectAllBtn").addEventListener("click", () => {
  state.selectedPipelines.clear();
  state.selectedDoctor = "";
  loadReport();
});
document.getElementById("doctorFilter").addEventListener("change", event => {
  state.selectedDoctor = event.target.value;
  state.selectedPipelines.clear();
  loadReport();
});
document.getElementById("dateFrom").addEventListener("change", event => {
  state.dateFrom = event.target.value;
  loadReport();
});
document.getElementById("dateTo").addEventListener("change", event => {
  state.dateTo = event.target.value;
  loadReport();
});

const params = new URLSearchParams(window.location.search);
if (params.get("error")) showNotice(decodeURIComponent(params.get("error")));
if (params.get("connected")) showNotice("Kommo conectado. A primeira sincronizacao foi iniciada.");

document.querySelectorAll("[data-clinic-select]").forEach(button => {
  button.addEventListener("click", () => {
    requestClinicAccess(button.dataset.clinicSelect);
  });
});
document.getElementById("clinicAccessForm").addEventListener("submit", async event => {
  event.preventDefault();
  const modal = document.getElementById("clinicAccessModal");
  const clinicId = modal.dataset.clinicId;
  const clinic = clinics[clinicId] || clinics.vielle;
  const value = document.getElementById("clinicAccessCode").value.trim();
  const submitButton = event.currentTarget.querySelector('button[type="submit"]');
  submitButton.disabled = true;
  try {
    const response = await fetch("/api/clinic-access", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({clinic_id: clinic.id, access_code: value}),
    });
    const result = await response.json();
    if (!response.ok || !result.ok) {
      throw new Error(result.error || "Código incorreto.");
    }
    sessionStorage.setItem(clinicAccessKey(clinic.id), "ok");
    closeClinicAccessModal();
    selectClinic(clinic.id);
  } catch (error) {
    document.getElementById("clinicAccessError").textContent = error.message || "Código incorreto. Confira e tente novamente.";
  } finally {
    submitButton.disabled = false;
  }
});
document.getElementById("clinicAccessClose").addEventListener("click", closeClinicAccessModal);
document.getElementById("changeClinicBtn").addEventListener("click", () => {
  state.selectedClinic = "";
  state.report = null;
  localStorage.removeItem("selectedClinic");
  history.pushState(null, "", window.location.pathname);
  showClinicLanding();
});

const initialClinic = params.get("clinic");
if (initialClinic && clinics[initialClinic]) {
  requestClinicAccess(initialClinic, false);
} else {
  showClinicLanding();
}
setInterval(loadReport, 60_000);
