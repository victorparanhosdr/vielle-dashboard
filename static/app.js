const state = {
  report: null,
  allPipelines: [],
  allDoctors: [],
  allSellers: [],
  allGeneralDoctors: [],
  allBookingRegistryUsers: [],
  selectedPipelines: new Set(),
  selectedDoctor: "",
  selectedGeneralDoctor: "",
  selectedSeller: "",
  selectedBookingRegistryUser: "",
  selectedFollowupCategory: "",
  selectedFollowupStatus: "",
  selectedFollowupLost: "active",
  selectedFollowupContact: "",
  selectedFollowupLastFrom: "",
  selectedFollowupLastTo: "",
  patientFollowupItems: [],
  followupVisibleCount: 24,
  selectedQuoteFollowupWallet: "active",
  selectedQuoteFollowupContact: "",
  selectedQuoteFollowupStatus: "",
  quoteFollowupItems: [],
  quoteFollowupVisibleCount: 24,
  activeView: "generalView",
  selectedMonth: "",
  dateFrom: "",
  dateTo: "",
  rankings: {},
  selectedClinic: "",
  followupOnlyMode: false,
};

let pendingAutoPrint = false;
const FOLLOWUP_CALLERS = ["Emerson", "Mariana", "Ayrton", "Victor"];

const clinics = {
  vielle: {
    id: "vielle",
    name: "Vielle Clinic",
    kommoSubdomain: "vielleclinic",
    title: "DOC4DOCS",
    status: "Relatório atual conectado ao Kommo e Clínica Experts.",
    connected: true,
    patientFollowup: true,
  },
  inspire: {
    id: "inspire",
    name: "Clínica Inspire",
    kommoSubdomain: "clinicamedicainspire",
    title: "DOC4DOCS",
    status: "Relatório da Clínica Inspire conectado ao Kommo.",
    connected: true,
  },
  carla: {
    id: "carla",
    name: "Dr. Carla Ferreira",
    kommoSubdomain: "",
    title: "DOC4DOCS",
    status: "Relatório da Dr. Carla Ferreira pronto para conectar Kommo e Clínica Experts.",
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

function kommoFollowupUrl(item) {
  if (item?.kommo_lead_url) return item.kommo_lead_url;
  if (item?.kommo_search_url) return item.kommo_search_url;
  const clinic = clinics[state.selectedClinic] || clinics.vielle;
  const subdomain = clinic.kommoSubdomain;
  if (!subdomain) return "";
  const rawPhone = String(item?.patient_phone || "").replace(/\D+/g, "");
  const phoneTerm = rawPhone.length >= 10
    ? (rawPhone.startsWith("55") && rawPhone.length > 11 ? rawPhone.slice(2) : rawPhone).slice(-11)
    : "";
  const term = phoneTerm || item?.patient_email || item?.patient_name || "";
  if (!term || term === "-") return "";
  return `https://${subdomain}.kommo.com/leads/list/?term=${encodeURIComponent(term)}`;
}

function clinicaPatientUrl(item) {
  if (item?.clinica_patient_url) return item.clinica_patient_url;
  if (item?.clinica_patient_web_id) {
    const webId = String(item.clinica_patient_web_id || "").replace(/\D+/g, "");
    if (webId) return `https://app.clinicaexperts.com.br/clinica/contatos/listagem/paciente/${webId}/informacoes`;
  }
  const rawPhone = String(item?.patient_phone || "").replace(/\D+/g, "");
  const phoneTerm = rawPhone.length >= 10
    ? (rawPhone.startsWith("55") && rawPhone.length > 11 ? rawPhone.slice(2) : rawPhone).slice(-11)
    : "";
  const term = phoneTerm || item?.patient_email || item?.patient_name || "";
  if (!term || term === "-") return "";
  return `https://app.clinicaexperts.com.br/clinica/contatos/listagem-contatos?search=${encodeURIComponent(term)}`;
}

function buildQuery() {
  const params = new URLSearchParams();
  if (state.selectedClinic) params.set("clinic", state.selectedClinic);
  if (state.followupOnlyMode) {
    params.set("view", "patientFollowupView");
    params.set("modo", "equipe");
  }
  if (state.activeView === "generalView") {
    if (state.selectedGeneralDoctor) params.set("doctor", state.selectedGeneralDoctor);
    const range = monthRange(state.selectedMonth || currentMonthValue());
    params.set("date_from", range.from);
    params.set("date_to", range.to);
  } else {
    if (state.selectedPipelines.size) {
      params.set("pipeline_ids", [...state.selectedPipelines].join(","));
    }
    if (state.selectedDoctor) params.set("doctor", state.selectedDoctor);
    if (state.selectedSeller) params.set("seller", state.selectedSeller);
    if (state.dateFrom) params.set("date_from", state.dateFrom);
    if (state.dateTo) params.set("date_to", state.dateTo);
  }
  if (state.activeView === "patientFollowupView" && clinicSupportsFollowup()) params.set("include_followup", "1");
  if (state.activeView === "quoteFollowupView") params.set("include_quote_followup", "1");
  const query = params.toString();
  return query ? `?${query}` : "";
}

function clinicSupportsFollowup(clinicId = state.selectedClinic) {
  const clinic = clinics[clinicId] || clinics.vielle;
  return Boolean(clinic.patientFollowup);
}

function monthRange(monthValue) {
  const month = monthValue || currentMonthValue();
  const [year, monthNumber] = month.split("-").map(Number);
  const lastDay = new Date(year, monthNumber, 0).getDate();
  return {
    from: `${year}-${String(monthNumber).padStart(2, "0")}-01`,
    to: `${year}-${String(monthNumber).padStart(2, "0")}-${String(lastDay).padStart(2, "0")}`,
  };
}

function currentMonthValue() {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
}

function monthFromDate(dateValue) {
  return dateValue ? dateValue.slice(0, 7) : currentMonthValue();
}

function monthLabel(monthValue) {
  if (!monthValue) return "-";
  const [year, month] = monthValue.split("-").map(Number);
  return new Intl.DateTimeFormat("pt-BR", { month: "long", year: "numeric" }).format(new Date(year, month - 1, 1));
}

function normalizeGeneralMonth() {
  if (!state.selectedMonth) state.selectedMonth = monthFromDate(state.dateFrom);
  const range = monthRange(state.selectedMonth);
  state.dateFrom = range.from;
  state.dateTo = range.to;
  document.getElementById("dateFrom").value = range.from;
  document.getElementById("dateTo").value = range.to;
  const monthInput = document.getElementById("generalMonth");
  if (monthInput) monthInput.value = state.selectedMonth;
}

function syncFilterState(report) {
  const filters = report.filters || {};
  state.dateFrom = filters.date_from || state.dateFrom || "";
  state.dateTo = filters.date_to || state.dateTo || "";
  if (state.activeView === "generalView") {
    state.selectedGeneralDoctor = filters.doctor || state.selectedGeneralDoctor || "";
  } else {
    state.selectedDoctor = filters.doctor || state.selectedDoctor || "";
  }
  state.selectedSeller = filters.seller || state.selectedSeller || "";
  document.getElementById("dateFrom").value = state.dateFrom;
  document.getElementById("dateTo").value = state.dateTo;
  state.allPipelines = report.pipelines || [];
  state.allDoctors = filters.doctors || state.allDoctors || [];
  state.allSellers = filters.sellers || state.allSellers || [];
  state.allBookingRegistryUsers = report.clinica_experts?.booking_registry_users || [];
  if (state.selectedBookingRegistryUser && !state.allBookingRegistryUsers.includes(state.selectedBookingRegistryUser)) {
    state.selectedBookingRegistryUser = "";
  }
  renderDoctorFilter();
  renderGeneralDoctorFilter();
  renderSellerFilter();
  renderBookingRegistryUserFilter();
}

function render() {
  const report = state.report || {};
  const totals = report.totals || {};
  syncFilterState(report);
  if (state.activeView === "generalView") normalizeGeneralMonth();

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
  renderDailyChart(filteredBookingDailyItems(report.clinica_experts?.daily_bookings || []), "bookingChart", {
    totalLabel: "Agendamentos",
    breakdownKey: "by_doctor",
  });
  renderDailyChart(report.interacted_leads?.daily || [], "interactionChart");
  renderClinicaExperts(report.clinica_experts || {});
  renderDoctorCross(report.clinica_experts?.doctor_cross || []);
  renderFinancial(report.financial || {});
  renderPaidTraffic(report.paid_traffic || {});
  renderGeneralDoctorFilter();
  renderGeneralPanel(report.general_panel || {});
  if (state.activeView === "patientFollowupView" && clinicSupportsFollowup()) {
    renderPatientFollowup(report.patient_followup || {});
  }
  if (state.activeView === "quoteFollowupView") {
    renderQuoteFollowup(report.quote_followup || {});
  }
  renderStatusColumnChart(report.all_current_status || []);

  applyClinicHeader();
  const clinic = clinics[state.selectedClinic] || clinics.vielle;
  const lastSync = report.last_sync;
  const clinicaBackgroundSync = report.clinica_experts?.background_sync || {};
  if (state.selectedClinic && !clinic.connected) {
    showNotice(`${clinic.name} criada. Agora precisamos configurar as integrações dela para começar a puxar dados.`);
  } else if (!report.connected) {
    showNotice(clinic.commercialSource === "midas"
      ? "Configure a API Midas para iniciar a primeira sincronizacao comercial."
      : "Conecte sua conta Kommo para iniciar a primeira sincronizacao.");
  } else if (clinicaBackgroundSync.running) {
    showNotice("Clínica Experts: sincronização histórica em andamento desde 01/01/2025. Os dados aparecem aos poucos.");
  } else if (clinicaBackgroundSync.ok === false) {
    showNotice(clinicaBackgroundSync.message || "A sincronização histórica do Clínica Experts não foi concluída.");
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
  const isMidas = clinic.commercialSource === "midas";
  const connectBtn = document.getElementById("connectBtn");
  const syncBtn = document.getElementById("syncBtn");
  if (connectBtn) connectBtn.textContent = isMidas ? "Configurar Midas" : "Conectar Kommo";
  if (syncBtn && !syncBtn.disabled) syncBtn.textContent = isMidas ? "Atualizar Midas" : "Atualizar";
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
  applyActiveViewState();
}

function updateMobileTabsToggle() {
  const toggle = document.getElementById("mobileTabsToggle");
  if (!toggle) return;
  const activeTab = document.querySelector(`.tabBtn[data-view="${state.activeView}"]`);
  const label = activeTab ? activeTab.textContent.trim() : "Painel geral";
  const labelEl = toggle.querySelector("span:first-child");
  if (labelEl) labelEl.textContent = label;
}

function applyActiveViewState() {
  const supportsFollowup = clinicSupportsFollowup();
  if (!supportsFollowup && state.followupOnlyMode) {
    state.followupOnlyMode = false;
    state.activeView = "generalView";
  }
  if (state.followupOnlyMode) state.activeView = "patientFollowupView";
  if (!supportsFollowup && state.activeView === "patientFollowupView") {
    state.activeView = "generalView";
  }
  document.querySelectorAll(".tabBtn").forEach(tab => {
    const isFollowupTab = tab.dataset.view === "patientFollowupView";
    tab.hidden = isFollowupTab && !supportsFollowup;
    tab.classList.toggle("active", tab.dataset.view === state.activeView);
  });
  document.querySelectorAll(".viewPanel").forEach(panel => {
    panel.classList.toggle("active", panel.id === state.activeView);
  });
  const monthMode = state.activeView === "generalView";
  document.body.classList.toggle("generalMode", monthMode);
  document.body.classList.toggle("followupOnlyMode", state.followupOnlyMode);
  const dateFrom = document.getElementById("dateFrom");
  const dateTo = document.getElementById("dateTo");
  if (dateFrom) dateFrom.disabled = monthMode;
  if (dateTo) dateTo.disabled = monthMode;
  if (monthMode) normalizeGeneralMonth();
  updateMobileTabsToggle();
}

function accessModeKey() {
  return state.followupOnlyMode ? "team" : "dashboard";
}

function clinicAccessKey(clinicId, mode = accessModeKey()) {
  return `clinicAccess:${clinicId}:${mode}`;
}

function openClinicAccessModal(clinicId, mode = accessModeKey()) {
  const clinic = clinics[clinicId] || clinics.vielle;
  const modal = document.getElementById("clinicAccessModal");
  modal.dataset.clinicId = clinic.id;
  modal.dataset.accessMode = mode;
  document.getElementById("clinicAccessTitle").textContent = mode === "team" ? `${clinic.name} · Modo equipe` : clinic.name;
  document.getElementById("clinicAccessSubtitle").textContent = mode === "team"
    ? "Digite o código da equipe para abrir apenas o acompanhamento de pacientes."
    : `Digite o código de acesso da ${clinic.name} para continuar.`;
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
  modal.dataset.accessMode = "";
}

function requestClinicAccess(clinicId, updateUrl = true, mode = accessModeKey()) {
  const validClinicId = clinics[clinicId] ? clinicId : "vielle";
  state.followupOnlyMode = mode === "team";
  if (state.followupOnlyMode) state.activeView = "patientFollowupView";
  const hasCurrentModeAccess = sessionStorage.getItem(clinicAccessKey(validClinicId, mode)) === "ok";
  const hasDashboardAccess = mode === "team" && sessionStorage.getItem(clinicAccessKey(validClinicId, "dashboard")) === "ok";
  if (hasCurrentModeAccess || hasDashboardAccess) {
    selectClinic(validClinicId, updateUrl);
    return;
  }
  state.selectedClinic = validClinicId;
  state.report = emptyReportForClinic(validClinicId);
  state.allPipelines = [];
  state.allDoctors = [];
  state.allGeneralDoctors = [];
  state.allSellers = [];
  state.allBookingRegistryUsers = [];
  state.selectedPipelines.clear();
  state.selectedDoctor = "";
  state.selectedGeneralDoctor = "";
  state.selectedSeller = "";
  state.selectedBookingRegistryUser = "";
  state.dateFrom = "";
  state.dateTo = "";
  localStorage.setItem("selectedClinic", state.selectedClinic);
  showDashboard();
  render();
  openClinicAccessModal(validClinicId, mode);
}

function selectClinic(clinicId, updateUrl = true) {
  state.selectedClinic = clinics[clinicId] ? clinicId : "vielle";
  state.report = null;
  state.allPipelines = [];
  state.allDoctors = [];
  state.allGeneralDoctors = [];
  state.allSellers = [];
  state.allBookingRegistryUsers = [];
  state.selectedPipelines.clear();
  state.selectedDoctor = "";
  state.selectedGeneralDoctor = "";
  state.selectedSeller = "";
  state.selectedBookingRegistryUser = "";
  state.dateFrom = "";
  state.dateTo = "";
  localStorage.setItem("selectedClinic", state.selectedClinic);
  showDashboard();
  applyClinicHeader();
  if (updateUrl) {
    const params = new URLSearchParams(window.location.search);
    params.set("clinic", state.selectedClinic);
    if (state.followupOnlyMode) {
      params.set("view", "patientFollowupView");
      params.set("modo", "equipe");
    }
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
      sellers: [],
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
      booking_registry_users: [],
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
    paid_traffic: {
      connected: false,
      basis: "Meta Ads",
      account_id: "",
      totals: { spend: 0, impressions: 0, reach: 0, clicks: 0, leads: 0, ctr: null, cpc: null, cpl: null },
      daily: [],
      campaigns: [],
      last_sync: null,
    },
    general_panel: {
      month: monthFromDate(dateFrom),
      goal: 0,
      revenue: 0,
      goal_rate: null,
      projected_revenue: 0,
      elapsed_days: 0,
      month_days: 0,
      average_ticket: 0,
      sales_count: 0,
      active_revenue_days: 0,
      distinct_patients: 0,
      financial_daily: [],
      payment_methods: [],
      top_patients: [],
      value_ranges: [],
      sales_ticket_daily: [],
      daily_leads: [],
      daily_bookings: [],
    },
    last_sync: null,
  };
}

const brl = new Intl.NumberFormat("pt-BR", {
  style: "currency",
  currency: "BRL",
  maximumFractionDigits: 0,
});

const brlCents = new Intl.NumberFormat("pt-BR", {
  style: "currency",
  currency: "BRL",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
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

function renderGeneralPanel(panel) {
  const month = panel.month || state.selectedMonth || monthFromDate(state.dateFrom);
  state.selectedMonth = month || currentMonthValue();
  const goal = Number(panel.goal || 0);
  const revenue = Number(panel.revenue || 0);
  const goalRate = panel.goal_rate;
  const projected = Number(panel.projected_revenue || 0);
  const elapsed = panel.elapsed_days || 0;
  const monthDays = panel.month_days || 0;
  const salesCount = Number(panel.sales_count || 0);
  const activeDays = Number(panel.active_revenue_days || 0);
  const dailyFinancial = panel.financial_daily || [];
  const totalLeads = (panel.daily_leads || []).reduce((sum, item) => sum + Number(item.total || 0), 0);
  const totalBookings = (panel.daily_bookings || []).reduce((sum, item) => sum + Number(item.total || 0), 0);
  const bookingConversion = totalLeads ? totalBookings / totalLeads : null;
  const monthInput = document.getElementById("generalMonth");
  const clinic = clinics[state.selectedClinic] || clinics.vielle;
  if (monthInput) monthInput.value = state.selectedMonth;
  const goalsMonthInput = document.getElementById("goalsModalMonthInput");
  if (goalsMonthInput) goalsMonthInput.value = state.selectedMonth;
  document.getElementById("generalBoardTitle").textContent = "Resumo mensal";
  document.getElementById("generalGoal").textContent = brl.format(goal);
  document.getElementById("generalGoalMonth").textContent = `${clinic.name} · ${monthLabel(state.selectedMonth)}`;
  document.getElementById("generalRevenue").textContent = brl.format(revenue);
  document.getElementById("generalFeaturedExpenses").textContent = brl.format(panel.expenses_total || 0);
  document.getElementById("generalFeaturedBalance").textContent = brl.format(panel.balance || 0);
  document.getElementById("generalGoalRate").textContent = goal ? formatPercent(goalRate) : "-";
  document.getElementById("generalGoalRateHint").textContent = goal
    ? `${brl.format(Math.max(0, goal - revenue))} faltando para a meta`
    : "Cadastre uma meta mensal";
  document.getElementById("generalProjection").textContent = brl.format(projected);
  document.getElementById("generalProjectionHint").textContent = `${elapsed} de ${monthDays} dias calculados`;
  document.getElementById("generalAverageTicket").textContent = brl.format(panel.average_ticket || 0);
  document.getElementById("generalMarginOne").textContent = formatPercent(panel.margin_1_rate);
  document.getElementById("generalMarginOneProfit").textContent = `Lucro: ${brl.format(revenue - Number(panel.margin_1_expenses || 0))}`;
  document.getElementById("generalMarginOne").title = `Saídas consideradas: ${brl.format(panel.margin_1_expenses || 0)}`;
  document.getElementById("generalMarginTwo").textContent = formatPercent(panel.margin_2_rate);
  document.getElementById("generalMarginTwoProfit").textContent = `Lucro: ${brl.format(revenue - Number(panel.margin_2_expenses || 0))}`;
  document.getElementById("generalMarginTwo").title = `Todas as saídas: ${brl.format(panel.margin_2_expenses || 0)}`;
  const activeRevenueAverage = activeDays ? revenue / activeDays : 0;
  const dailyAverage = monthDays ? revenue / monthDays : 0;
  const revenueDays = dailyFinancial.filter(item => Number(item.income || 0) > 0);
  const strongestDay = revenueDays.reduce((best, item) => Number(item.income || 0) > Number(best?.income || 0) ? item : best, null);
  const weakestDay = revenueDays.reduce((best, item) => Number(item.income || 0) < Number(best?.income || Infinity) ? item : best, null);
  document.getElementById("generalAverageActiveDay").textContent = brl.format(activeRevenueAverage);
  document.getElementById("generalDailyAverage").textContent = brl.format(dailyAverage);
  document.getElementById("generalStrongDay").textContent = strongestDay ? `${formatDay(strongestDay.day)} · ${brl.format(strongestDay.income || 0)}` : "-";
  document.getElementById("generalWeakDay").textContent = weakestDay ? `${formatDay(weakestDay.day)} · ${brl.format(weakestDay.income || 0)}` : "-";
  document.getElementById("generalTotalLeads").textContent = totalLeads;
  document.getElementById("generalTotalBookings").textContent = totalBookings;
  document.getElementById("generalBookingConversion").textContent = bookingConversion === null
    ? "Conversão: -"
    : `Conversão: ${formatPercent(bookingConversion)}`;
  document.getElementById("generalDistinctPatients").textContent = Number(panel.distinct_patients || 0);
  document.getElementById("generalSalesCount").textContent = salesCount;
  document.getElementById("generalActiveDays").textContent = activeDays;
  document.getElementById("generalActiveDaysHint").textContent = `${activeDays} de ${monthDays} dias`;
  document.getElementById("generalExpensesTotal").textContent = brl.format(panel.expenses_total || 0);
  document.getElementById("generalExpensesPaid").textContent = brl.format(panel.expenses_paid || 0);
  document.getElementById("generalExpensesPending").textContent = brl.format(panel.expenses_pending || 0);
  document.getElementById("generalBalance").textContent = brl.format(panel.balance || 0);
  renderMonthlyGoalRows(panel.goal_entries || []);
  renderGeneralRevenueBarChart(dailyFinancial);
  renderGeneralAccumulatedChart(dailyFinancial);
  renderGeneralTopPatients(panel.top_patients || []);
  renderGeneralValueRanges(panel.value_ranges || []);
  renderGeneralSalesTicketChart(panel.sales_ticket_daily || []);
  renderGeneralLeadBookingChart(panel.daily_leads || [], panel.daily_bookings || []);
  renderGeneralExpenseCategories(panel.expenses_by_category || [], panel.expenses_total || 0);
  renderGeneralIncomeTypes(panel.income_by_type || []);
  renderGeneralExpenseDailyChart(panel.expenses_daily || []);
}

function renderMonthlyGoalRows(entries) {
  const el = document.getElementById("monthlyGoalsList");
  if (!el) return;
  const monthLabelEl = document.getElementById("goalsModalMonth");
  if (monthLabelEl) monthLabelEl.textContent = monthLabel(state.selectedMonth);
  const knownGoals = new Map(entries.map(entry => [entry.doctor, Number(entry.goal || 0)]));
  const doctors = [...new Set([
    ...state.allGeneralDoctors,
    ...entries.map(entry => entry.doctor).filter(Boolean),
    ...state.allDoctors,
  ])].sort((a, b) => a.localeCompare(b, "pt-BR"));
  state.allGeneralDoctors = doctors;
  el.innerHTML = doctors.length
    ? doctors.map(doctor => `
      <label class="monthlyGoalRow">
        <span>${escapeHtml(doctor)}</span>
        <input type="number" min="0" step="100" data-goal-doctor="${escapeHtml(doctor)}" value="${knownGoals.get(doctor) ? Math.round(knownGoals.get(doctor)) : ""}" placeholder="R$ 0">
      </label>
    `).join("")
    : `<div class="empty">Sem profissionais para cadastrar meta.</div>`;
}

function openGoalsModal() {
  const modal = document.getElementById("goalsModal");
  const monthInput = document.getElementById("goalsModalMonthInput");
  if (monthInput) monthInput.value = state.selectedMonth || currentMonthValue();
  renderMonthlyGoalRows(state.report?.general_panel?.goal_entries || []);
  modal.hidden = false;
  document.body.classList.add("modalOpen");
}

function closeGoalsModal() {
  document.getElementById("goalsModal").hidden = true;
  document.body.classList.remove("modalOpen");
}

function renderGeneralRevenueBarChart(items) {
  const el = document.getElementById("generalRevenueBarChart");
  const days = items.filter(item => item.day);
  const hasSignal = days.some(item => Number(item.income || 0) > 0);
  if (!hasSignal) {
    el.innerHTML = `<div class="empty">Sem faturamento no mês selecionado.</div>`;
    return;
  }
  const max = Math.max(...days.map(item => Number(item.income || 0)), 1);
  el.innerHTML = `
    <div class="generalBarPlot" style="--bar-count:${Math.max(days.length, 1)}">
      ${days.map(item => {
        const value = Number(item.income || 0);
        const height = Math.max(value ? 7 : 1, (value / max) * 100);
        return `
          <span class="generalBarDay" data-tip="${escapeHtml(formatDay(item.day))} · ${escapeHtml(brl.format(value))}">
            <i style="height:${height}%"></i>
            <b>${String(Number(item.day.slice(-2))).padStart(2, "0")}</b>
          </span>
        `;
      }).join("")}
    </div>
  `;
}

function renderGeneralPaymentDonut(items, total) {
  const el = document.getElementById("generalPaymentDonut");
  const sortedItems = [...items].filter(item => Number(item.amount || 0) > 0).sort((a, b) => Number(b.amount || 0) - Number(a.amount || 0));
  if (!sortedItems.length || !total) {
    el.innerHTML = `<div class="empty">Sem entradas por forma de pagamento.</div>`;
    return;
  }
  const colors = ["#102B25", "#969A78", "#B4B79B", "#B58A45", "#55788F"];
  let offset = 0;
  const circles = sortedItems.slice(0, 5).map((item, index) => {
    const share = Math.max(0, Number(item.amount || 0) / total);
    const dash = `${(share * 100).toFixed(2)} ${Math.max(0, 100 - share * 100).toFixed(2)}`;
    const circle = `<circle r="15.9155" cx="20" cy="20" fill="transparent" stroke="${colors[index]}" stroke-width="7" stroke-dasharray="${dash}" stroke-dashoffset="${(-offset).toFixed(2)}"></circle>`;
    offset += share * 100;
    return circle;
  }).join("");
  el.innerHTML = `
    <div class="generalDonut">
      <svg viewBox="0 0 40 40" role="img" aria-label="Formas de pagamento">
        <circle r="15.9155" cx="20" cy="20" fill="transparent" stroke="#E8E3D9" stroke-width="7"></circle>
        ${circles}
      </svg>
      <strong>${brl.format(total)}</strong>
      <span>Total</span>
    </div>
    <div class="generalDonutLegend">
      ${sortedItems.slice(0, 5).map((item, index) => `
        <span>
          <i style="background:${colors[index]}"></i>
          <b>${escapeHtml(financeTypeLabel(item.type))}</b>
          <small>${brl.format(item.amount || 0)} · ${formatPercent((item.amount || 0) / total)}</small>
        </span>
      `).join("")}
    </div>
  `;
}

function renderGeneralAccumulatedChart(items) {
  const el = document.getElementById("generalAccumulatedChart");
  const days = items.filter(item => item.day);
  let accumulated = 0;
  const prepared = days.map(item => {
    accumulated += Number(item.income || 0);
    return { day: item.day, total: accumulated };
  });
  if (!prepared.some(item => item.total > 0)) {
    el.innerHTML = `<div class="empty">Sem faturamento acumulado.</div>`;
    return;
  }
  const width = Math.max(360, prepared.length * 18);
  const height = 190;
  const pad = { top: 18, right: 18, bottom: 30, left: 46 };
  const chartW = width - pad.left - pad.right;
  const chartH = height - pad.top - pad.bottom;
  const max = Math.max(...prepared.map(item => item.total), 1);
  const xFor = index => pad.left + (prepared.length === 1 ? chartW / 2 : (index / (prepared.length - 1)) * chartW);
  const yFor = value => pad.top + chartH - (value / max) * chartH;
  const path = prepared.map((item, index) => `${index ? "L" : "M"} ${xFor(index).toFixed(1)} ${yFor(item.total).toFixed(1)}`).join(" ");
  const area = `${path} L ${xFor(prepared.length - 1).toFixed(1)} ${pad.top + chartH} L ${xFor(0).toFixed(1)} ${pad.top + chartH} Z`;
  el.innerHTML = `
    <svg class="generalAccumulatedSvg" viewBox="0 0 ${width} ${height}" role="img" aria-label="Faturamento acumulado">
      <defs>
        <linearGradient id="generalAccumulatedArea" x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stop-color="#102B25" stop-opacity=".22"></stop>
          <stop offset="100%" stop-color="#102B25" stop-opacity="0"></stop>
        </linearGradient>
      </defs>
      <line class="lineGrid" x1="${pad.left}" y1="${pad.top + chartH}" x2="${width - pad.right}" y2="${pad.top + chartH}"></line>
      <path class="generalAccumulatedArea" d="${area}"></path>
      <path class="generalAccumulatedLine" d="${path}"></path>
      ${prepared.map((item, index) => `
        <g class="generalAccumulatedPoint">
          <circle cx="${xFor(index).toFixed(1)}" cy="${yFor(item.total).toFixed(1)}" r="5"></circle>
          <rect x="${(xFor(index) - 10).toFixed(1)}" y="${pad.top}" width="20" height="${chartH}" rx="8"></rect>
          <title>${formatDay(item.day)} · acumulado ${brl.format(item.total || 0)}</title>
        </g>
      `).join("")}
      ${prepared.map((item, index) => index % Math.ceil(prepared.length / 5) ? "" : `<text class="pointDate" x="${xFor(index)}" y="${height - 8}">${formatShortDay(item.day)}</text>`).join("")}
    </svg>
  `;
}

function renderGeneralTopPatients(items) {
  const el = document.getElementById("generalTopPatients");
  const sortedItems = [...items].sort((a, b) => Number(b.amount || 0) - Number(a.amount || 0)).slice(0, 8);
  const max = Math.max(...sortedItems.map(item => Number(item.amount || 0)), 1);
  el.innerHTML = sortedItems.length
    ? sortedItems.map((item, index) => `
      <div class="generalTopItem">
        <b>${String(index + 1).padStart(2, "0")}</b>
        <span>${escapeHtml(item.patient || "Paciente sem nome")}</span>
        <i><em style="width:${Math.max(6, (Number(item.amount || 0) / max) * 100)}%"></em></i>
        <strong>${brl.format(item.amount || 0)}</strong>
      </div>
    `).join("")
    : `<div class="empty">Sem pacientes com venda no mês.</div>`;
}

function renderGeneralValueRanges(items) {
  const el = document.getElementById("generalValueRanges");
  const totalSales = items.reduce((sum, item) => sum + Number(item.sales || 0), 0);
  const maxSales = Math.max(...items.map(item => Number(item.sales || 0)), 1);
  el.innerHTML = items.length
    ? items.map(item => {
      const sales = Number(item.sales || 0);
      return `
        <div class="generalRangeItem">
          <div>
            <b>${escapeHtml(item.range || "Faixa")}</b>
            <small>${sales} venda${sales === 1 ? "" : "s"} · ${formatPercent(totalSales ? sales / totalSales : 0)}</small>
          </div>
          <i><em style="width:${Math.max(sales ? 8 : 0, (sales / maxSales) * 100)}%"></em></i>
          <strong>${brl.format(item.amount || 0)}</strong>
        </div>
      `;
    }).join("")
    : `<div class="empty">Sem vendas para distribuir por faixa.</div>`;
}

function renderGeneralSalesTicketChart(items) {
  const prepared = items.map(item => ({
    day: item.day,
    sales: Number(item.sales || 0),
    average_ticket: Number(item.average_ticket || 0),
    revenue: Number(item.revenue || 0),
  }));
  renderHorizontalComparisonChart("generalSalesTicketChart", prepared, {
    empty: "Sem vendas no mês selecionado.",
    firstKey: "revenue",
    secondKey: "average_ticket",
    firstLabel: "Faturamento",
    secondLabel: "Ticket médio",
    firstColor: "#102B25",
    secondColor: "#969A78",
    independentScale: true,
    firstFormatter: value => brl.format(value || 0),
    secondFormatter: value => brl.format(value || 0),
    detail: item => `${item.sales || 0} venda${item.sales === 1 ? "" : "s"}`,
  });
}

function renderGeneralLeadBookingChart(leads, bookings) {
  const bookingLookup = Object.fromEntries(bookings.map(item => [item.day, Number(item.total || 0)]));
  const days = [...new Set([...leads.map(item => item.day), ...bookings.map(item => item.day)])].filter(Boolean).sort();
  const prepared = days.map(day => ({
    day,
    leads: Number((leads.find(item => item.day === day) || {}).total || 0),
    bookings: bookingLookup[day] || 0,
  }));
  renderHorizontalComparisonChart("generalLeadBookingChart", prepared, {
    empty: "Sem leads ou agendamentos no mês selecionado.",
    firstKey: "leads",
    secondKey: "bookings",
    firstLabel: "Leads",
    secondLabel: "Agendamentos",
    firstColor: "#102B25",
    secondColor: "#969A78",
    firstFormatter: value => `${Math.round(value || 0)}`,
    secondFormatter: value => `${Math.round(value || 0)}`,
  });
}

function renderGeneralExpenseCategories(items, total) {
  const el = document.getElementById("generalExpenseCategories");
  if (!el) return;
  const sortedItems = [...items]
    .filter(item => Number(item.amount || 0) > 0)
    .sort((a, b) => Number(b.amount || 0) - Number(a.amount || 0));
  if (!sortedItems.length || !total) {
    el.innerHTML = `<div class="empty">Sem saídas por categoria no mês selecionado.</div>`;
    return;
  }
  const colors = ["#102B25", "#969A78", "#B4B79B", "#B58A45", "#55788F", "#387A5A", "#B95656"];
  let offset = 0;
  const slices = sortedItems.slice(0, 7).map((item, index) => {
    const share = Number(item.amount || 0) / total;
    const dash = `${(share * 100).toFixed(2)} ${Math.max(0, 100 - share * 100).toFixed(2)}`;
    const slice = `<circle r="15.9155" cx="20" cy="20" fill="transparent" stroke="${colors[index % colors.length]}" stroke-width="8" stroke-dasharray="${dash}" stroke-dashoffset="${(-offset).toFixed(2)}"></circle>`;
    offset += share * 100;
    return slice;
  }).join("");
  const max = Math.max(...sortedItems.map(item => Number(item.amount || 0)), 1);
  el.innerHTML = `
    <div class="expenseDonutBlock">
      <div class="expenseDonut">
        <svg viewBox="0 0 40 40" role="img" aria-label="Distribuição de saídas por categoria">
          <circle r="15.9155" cx="20" cy="20" fill="transparent" stroke="#F5F5F3" stroke-width="8"></circle>
          ${slices}
        </svg>
        <strong>${brl.format(total)}</strong>
        <span>Total</span>
      </div>
    </div>
    <div class="expenseCategoryRows">
      ${sortedItems.slice(0, 8).map((item, index) => {
        const amount = Number(item.amount || 0);
        const share = total ? amount / total : 0;
        return `
          <div class="expenseCategoryRow">
            <i style="background:${colors[index % colors.length]}"></i>
            <div>
              <strong>${escapeHtml(item.category || "Sem categoria")}</strong>
              <small>${financeListSubtitle(item)} · ${formatPercent(share)}</small>
              <span><em style="width:${Math.max(5, (amount / max) * 100)}%"></em></span>
            </div>
            <b>${brl.format(amount)}</b>
          </div>
        `;
      }).join("")}
    </div>
  `;
}

function renderGeneralIncomeTypes(items) {
  const el = document.getElementById("generalIncomeTypes");
  if (!el) return;
  const sortedItems = [...items]
    .filter(item => Number(item.amount || 0) > 0)
    .sort((a, b) => Number(b.amount || 0) - Number(a.amount || 0));
  const total = sortedItems.reduce((sum, item) => sum + Number(item.amount || 0), 0);
  if (!sortedItems.length) {
    el.innerHTML = `<div class="empty">Sem entradas por tipo no mês selecionado.</div>`;
    return;
  }
  el.innerHTML = sortedItems.slice(0, 6).map(item => {
    const amount = Number(item.amount || 0);
    return `
      <div class="incomeTypeCard">
        <span>${escapeHtml(financeTypeLabel(item.type))}</span>
        <strong>${brl.format(amount)}</strong>
        <small>${formatPercent(total ? amount / total : 0)} das entradas</small>
      </div>
    `;
  }).join("");
}

function renderGeneralExpenseDailyChart(items) {
  const prepared = items
    .filter(item => item.day)
    .map(item => ({
      day: item.day,
      expenses: Number(item.expenses || 0),
      income: Number(item.income || 0),
    }));
  const active = prepared.filter(item => item.expenses > 0);
  const el = document.getElementById("generalExpenseDailyChart");
  if (!el) return;
  if (!active.length) {
    el.innerHTML = `<div class="empty">Sem saídas no mês selecionado.</div>`;
    return;
  }
  const max = Math.max(...active.map(item => item.expenses), 1);
  const total = active.reduce((sum, item) => sum + item.expenses, 0);
  const average = total / active.length;
  const strongest = active.reduce((best, item) => item.expenses > (best?.expenses || 0) ? item : best, null);
  el.innerHTML = `
    <div class="expenseDailyStats">
      <span><b>${active.length}</b> dias com saída</span>
      <span><b>${brl.format(average)}</b> média diária</span>
      <span><b>${strongest ? formatDay(strongest.day) : "-"}</b> maior saída</span>
    </div>
    <div class="expenseDayBars" style="--expense-days:${Math.max(active.length, 1)}">
      ${active.map(item => `
        <span class="expenseDayBar" data-tip="${escapeHtml(`${formatDay(item.day)} · Saídas: ${brl.format(item.expenses)} · Entradas: ${brl.format(item.income)}`)}">
          <i style="height:${Math.max(6, (item.expenses / max) * 100)}%"></i>
          <b>${String(Number(item.day.slice(-2))).padStart(2, "0")}</b>
        </span>
      `).join("")}
    </div>
  `;
}

function renderHorizontalComparisonChart(id, items, config) {
  const el = document.getElementById(id);
  const active = items.filter(item => item.day);
  const hasSignal = active.some(item => Number(item[config.firstKey] || 0) || Number(item[config.secondKey] || 0));
  if (!active.length || !hasSignal) {
    el.innerHTML = `<div class="empty">${config.empty}</div>`;
    return;
  }
  const maxFirst = Math.max(...active.map(item => Number(item[config.firstKey] || 0)), 1);
  const maxSecond = Math.max(...active.map(item => Number(item[config.secondKey] || 0)), 1);
  const sharedMax = Math.max(maxFirst, maxSecond, 1);
  el.innerHTML = `
    <div class="generalHorizontalLegend">
      <span><i style="background:${config.firstColor}"></i>${escapeHtml(config.firstLabel)}</span>
      <span><i style="background:${config.secondColor}"></i>${escapeHtml(config.secondLabel)}</span>
    </div>
    <div class="generalHorizontalRows">
      ${active.map(item => {
        const first = Number(item[config.firstKey] || 0);
        const second = Number(item[config.secondKey] || 0);
        const tooltip = `${formatDay(item.day)} · ${config.firstLabel}: ${config.firstFormatter(first)} · ${config.secondLabel}: ${config.secondFormatter(second)}${config.detail ? ` · ${config.detail(item)}` : ""}`;
        return `
          <div class="generalHorizontalRow" data-tip="${escapeHtml(tooltip)}">
            <b>${formatShortDay(item.day)}</b>
            <div>
              <span><i style="width:${Math.max(first ? 5 : 0, (first / (config.independentScale ? maxFirst : sharedMax)) * 100)}%;background:${config.firstColor}"></i><em>${config.firstFormatter(first)}</em></span>
              <span><i style="width:${Math.max(second ? 5 : 0, (second / (config.independentScale ? maxSecond : sharedMax)) * 100)}%;background:${config.secondColor}"></i><em>${config.secondFormatter(second)}</em></span>
            </div>
          </div>
        `;
      }).join("")}
    </div>
  `;
}

function renderDualAxisChart(id, items, config) {
  const el = document.getElementById(id);
  const hasSignal = items.some(item => (item[config.leftKey] || 0) || (item[config.rightKey] || 0));
  const active = items.filter(item => item.day);
  if (!hasSignal || !active.length) {
    el.innerHTML = `<div class="empty">${config.empty}</div>`;
    return;
  }
  const width = Math.max(760, active.length * 42);
  const height = 310;
  const pad = { top: 28, right: 86, bottom: 54, left: 72 };
  const chartW = width - pad.left - pad.right;
  const chartH = height - pad.top - pad.bottom;
  const maxLeft = Math.max(...active.map(item => item[config.leftKey] || 0), 1);
  const maxRight = Math.max(...active.map(item => item[config.rightKey] || 0), 1);
  const xFor = index => pad.left + (active.length === 1 ? chartW / 2 : (index / (active.length - 1)) * chartW);
  const yLeft = value => pad.top + chartH - ((value || 0) / maxLeft) * chartH;
  const yRight = value => pad.top + chartH - ((value || 0) / maxRight) * chartH;
  const linePath = (key, yFn) => active.map((item, index) => `${index ? "L" : "M"} ${xFor(index).toFixed(1)} ${yFn(item[key]).toFixed(1)}`).join(" ");
  const ticks = [0, .25, .5, .75, 1].map(ratio => {
    const leftValue = maxLeft * ratio;
    const rightValue = maxRight * ratio;
    const y = yLeft(leftValue);
    return `
      <line class="lineGrid" x1="${pad.left}" y1="${y}" x2="${width - pad.right}" y2="${y}"></line>
      <text class="chartAxis" x="${pad.left - 12}" y="${y + 4}" text-anchor="end">${escapeHtml(config.leftFormatter(leftValue))}</text>
      <text class="chartAxis" x="${width - pad.right + 12}" y="${y + 4}">${escapeHtml(config.rightFormatter(rightValue))}</text>
    `;
  }).join("");
  const labels = active.map((item, index) => {
    if (active.length > 16 && index % Math.ceil(active.length / 12)) return "";
    return `<text class="pointDate tilted" x="${xFor(index)}" y="${height - 18}">${formatShortDay(item.day)}</text>`;
  }).join("");
  const points = active.map((item, index) => {
    const x = xFor(index);
    return `
      <circle class="chartPoint" style="stroke:${config.leftColor};fill:#FFFFFF" cx="${x}" cy="${yLeft(item[config.leftKey])}" r="4.5"></circle>
      <circle class="chartPoint" style="stroke:${config.rightColor};fill:#FFFFFF" cx="${x}" cy="${yRight(item[config.rightKey])}" r="4.5"></circle>
    `;
  }).join("");
  const zones = active.map((item, index) => {
    const x = xFor(index);
    const previous = index ? xFor(index - 1) : pad.left;
    const next = index < active.length - 1 ? xFor(index + 1) : width - pad.right;
    const hitW = Math.max(24, (next - previous) / 2);
    return `<rect class="chartHitZone" data-index="${index}" x="${x - hitW / 2}" y="${pad.top}" width="${hitW}" height="${chartH}"></rect>`;
  }).join("");
  el.innerHTML = `
    <div class="generalChartLegend">
      <span><i style="background:${config.leftColor}"></i>${escapeHtml(config.leftLabel)}</span>
      <span><i style="background:${config.rightColor}"></i>${escapeHtml(config.rightLabel)}</span>
    </div>
    <div class="lineChartScroller">
      <svg class="performanceSvg" viewBox="0 0 ${width} ${height}" role="img">
        ${ticks}
        <path class="performanceLine" style="stroke:${config.leftColor}" d="${linePath(config.leftKey, yLeft)}"></path>
        <path class="performanceLine" style="stroke:${config.rightColor}" d="${linePath(config.rightKey, yRight)}"></path>
        ${points}
        ${zones}
        ${labels}
      </svg>
      <div class="chartTooltip" hidden></div>
    </div>
  `;
  const tooltip = el.querySelector(".chartTooltip");
  el.querySelectorAll(".chartHitZone").forEach(zone => {
    zone.addEventListener("mouseenter", event => showGeneralTooltip(event, active[Number(zone.dataset.index)], tooltip, el, config));
    zone.addEventListener("mousemove", event => showGeneralTooltip(event, active[Number(zone.dataset.index)], tooltip, el, config));
    zone.addEventListener("mouseleave", () => {
      tooltip.hidden = true;
    });
  });
}

function showGeneralTooltip(event, item, tooltip, container, config) {
  tooltip.innerHTML = config.tooltip(item);
  const bounds = container.getBoundingClientRect();
  tooltip.hidden = false;
  const tooltipWidth = tooltip.offsetWidth || 250;
  const left = Math.min(Math.max(12, event.clientX - bounds.left - tooltipWidth / 2), bounds.width - tooltipWidth - 12);
  const top = Math.max(52, event.clientY - bounds.top - tooltip.offsetHeight - 18);
  tooltip.style.left = `${left}px`;
  tooltip.style.top = `${top}px`;
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
            <stop offset="0%" stop-color="#102B25" stop-opacity=".24"></stop>
            <stop offset="100%" stop-color="#102B25" stop-opacity="0"></stop>
          </linearGradient>
          <linearGradient id="quotedArea" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stop-color="#969A78" stop-opacity=".22"></stop>
            <stop offset="100%" stop-color="#969A78" stop-opacity="0"></stop>
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

function integerFormat(value) {
  return new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 0 }).format(Number(value || 0));
}

function formatPercent(value) {
  if (value === null || value === undefined) return "-";
  return new Intl.NumberFormat("pt-BR", { style: "percent", maximumFractionDigits: 1 }).format(value);
}

function formatNullableMoney(value) {
  const number = Number(value || 0);
  return number ? brlCents.format(number) : "-";
}

function statusLabel(status) {
  return {
    red: "RED flag",
    due: "Contato agora",
    warn: "Reforço",
    monitor: "Monitorar",
  }[status] || "Monitorar";
}

function callerSelect(name, placeholder) {
  return `
    <select name="${escapeHtml(name)}" required>
      <option value="">${escapeHtml(placeholder)}</option>
      ${FOLLOWUP_CALLERS.map(person => `<option value="${escapeHtml(person)}">${escapeHtml(person)}</option>`).join("")}
    </select>
  `;
}

function renderPatientFollowup(followup) {
  if (!followup.items) {
    const container = document.getElementById("patientFollowupList");
    if (container) container.innerHTML = `<div class="empty">Carregando acompanhamento de pacientes...</div>`;
    return;
  }
  const totals = followup.totals || {};
  state.patientFollowupItems = followup.items || [];
  const followupStart = followup.start_date ? formatFullDay(followup.start_date) : "01/01/2025";
  document.getElementById("followupReferenceDate").textContent = `Base: ${followupStart} a ${formatFullDay(followup.reference_date)}`;
  document.getElementById("followupTotal").textContent = integerFormat(totals.actionable ?? ((totals.red || 0) + (totals.due || 0) + (totals.warn || 0)));
  document.getElementById("followupRed").textContent = integerFormat(totals.red || 0);
  document.getElementById("followupDue").textContent = integerFormat((totals.due || 0) + (totals.warn || 0));
  document.getElementById("followupContacted").textContent = integerFormat(totals.contacted || 0);
  const lostSelect = document.getElementById("followupLostFilter");
  if (lostSelect) lostSelect.value = state.selectedFollowupLost;
  const contactSelect = document.getElementById("followupContactFilter");
  if (contactSelect) contactSelect.value = state.selectedFollowupContact;

  const categorySelect = document.getElementById("followupCategoryFilter");
  const categories = followup.categories || [];
  categorySelect.innerHTML = `<option value="">Todas</option>` + categories.map(category => (
    `<option value="${escapeHtml(category)}" ${state.selectedFollowupCategory === category ? "selected" : ""}>${escapeHtml(category)}</option>`
  )).join("");
  if (state.selectedFollowupCategory && !categories.includes(state.selectedFollowupCategory)) {
    state.selectedFollowupCategory = "";
    categorySelect.value = "";
  }
  document.getElementById("followupStatusFilter").value = state.selectedFollowupStatus;
  const lastFromInput = document.getElementById("followupLastFrom");
  const lastToInput = document.getElementById("followupLastTo");
  if (lastFromInput) lastFromInput.value = state.selectedFollowupLastFrom;
  if (lastToInput) lastToInput.value = state.selectedFollowupLastTo;
  renderPatientFollowupList();
}

function filteredPatientFollowupItems() {
  return (state.patientFollowupItems || []).filter(item => {
    const walletStatus = item.wallet_status || (item.lost ? "lost" : "active");
    if (state.selectedFollowupLost === "active" && walletStatus !== "active") return false;
    if (state.selectedFollowupLost === "lost" && !item.lost) return false;
    if (state.selectedFollowupLost === "won" && !item.won) return false;
    if (state.selectedFollowupContact === "contacted" && !item.contact_count) return false;
    if (state.selectedFollowupContact === "not_contacted" && item.contact_count) return false;
    if (state.selectedFollowupCategory && item.category !== state.selectedFollowupCategory) return false;
    if (state.selectedFollowupStatus && item.status !== state.selectedFollowupStatus) return false;
    if (state.selectedFollowupLastFrom && item.sale_date < state.selectedFollowupLastFrom) return false;
    if (state.selectedFollowupLastTo && item.sale_date > state.selectedFollowupLastTo) return false;
    if (!state.selectedFollowupStatus && item.status === "monitor") return false;
    return true;
  });
}

function followupItemKey(item) {
  return [item?.patient_key || "", item?.category || ""].map(value => encodeURIComponent(value)).join("::");
}

function followupItemByKey(key) {
  return filteredPatientFollowupItems().find(item => followupItemKey(item) === key);
}

function renderPatientFollowupList() {
  const container = document.getElementById("patientFollowupList");
  const items = filteredPatientFollowupItems();
  const countEl = document.getElementById("followupFilteredCount");
  if (countEl) {
    countEl.textContent = `${integerFormat(items.length)} ${items.length === 1 ? "paciente" : "pacientes"} no filtro`;
  }
  if (!items.length) {
    container.innerHTML = `<div class="empty">Nenhum paciente encontrado para este filtro.</div>`;
    return;
  }
  const visibleItems = items.slice(0, state.followupVisibleCount);
  const cardsHtml = visibleItems.map((item) => {
    const itemKey = followupItemKey(item);
    const lastContact = item.last_contact;
    const contactText = lastContact
      ? `${formatDay(lastContact.contact_date)} · ${escapeHtml(lastContact.contacted_by || "Sem nome")}`
      : "Ainda sem contato registrado";
    const phone = item.patient_phone ? `<a href="tel:${escapeHtml(item.patient_phone)}">${escapeHtml(item.patient_phone)}</a>` : "<span>-</span>";
    const email = item.patient_email ? `<a href="mailto:${escapeHtml(item.patient_email)}">${escapeHtml(item.patient_email)}</a>` : "<span>-</span>";
    const kommoHref = kommoFollowupUrl(item);
    const kommoLabel = item.kommo_lead_url ? "Abrir lead no Kommo" : "Buscar telefone no Kommo";
    const kommoLink = kommoHref
      ? `<a class="followupActionIcon followupKommoLink" href="${escapeHtml(kommoHref)}" target="_blank" rel="noopener" title="${escapeHtml(kommoLabel)}" aria-label="${escapeHtml(kommoLabel)}"><img src="kommo-icon.png" alt=""></a>`
      : "";
    const statusInfo = item.status_info || item.lost_info || {};
    const walletStatus = item.wallet_status || (item.lost ? "lost" : "active");
    const walletText = item.won ? "Ganho" : (item.lost ? "Perdido" : statusLabel(item.status));
    const statusDate = statusInfo.status_date || statusInfo.lost_date;
    const statusNote = walletStatus !== "active"
      ? `<div class="followupStatusNote ${escapeHtml(walletStatus)}">
          <b>${escapeHtml(item.won ? "Ganho" : "Perdido")}</b>
          <span>${formatDay(statusDate)} · ${escapeHtml(statusInfo.marked_by || "Sem responsável")}</span>
          <em>${escapeHtml(statusInfo.note || statusInfo.reason || "Sem observação")}</em>
          <button class="followupRestoreBtn" type="button" data-followup-key="${escapeHtml(itemKey)}">Reativar paciente</button>
        </div>`
      : "";
    const history = (item.contacts || []).map(contact => `
      <li>
        <b>${formatDay(contact.contact_date)}</b>
        <span>${escapeHtml(contact.contacted_by || "Sem nome")}</span>
        <em>${escapeHtml(contact.description || "Sem descrição")}</em>
      </li>
    `).join("");
    return `
      <article class="followupCard ${escapeHtml(item.status)} ${item.lost ? "lost" : ""} ${item.won ? "won" : ""}">
        <div class="followupCardTop">
          <div>
            <span class="followupBadge">${escapeHtml(walletText)}</span>
            <h3>${escapeHtml(item.patient_name)}</h3>
            <p>${escapeHtml(item.category)} · ${escapeHtml(item.procedure_name)}</p>
          </div>
          <strong>${integerFormat(item.months_since || 0)} meses</strong>
        </div>
        <div class="followupFacts">
          <span><b>Último procedimento</b>${formatFullDay(item.sale_date)}</span>
          <span><b>Doutor(a)</b>${escapeHtml(item.professional_name || "-")}</span>
          <span><b>Contatos</b>${integerFormat(item.contact_count || 0)}</span>
        </div>
        <div class="followupContactLine">
          <span>${phone}</span>
          <span>${email}</span>
          <span>${contactText}</span>
          ${kommoLink}
        </div>
        ${statusNote}
        <details class="followupDetails">
          <summary>Registrar novo contato</summary>
          <form class="followupForm" data-followup-key="${escapeHtml(itemKey)}">
            <input type="date" name="contact_date" value="${new Date().toISOString().slice(0, 10)}" required>
            ${callerSelect("contacted_by", "Quem chamou")}
            <textarea name="description" placeholder="Descrição do contato, retorno ou combinado"></textarea>
            <button type="submit">Salvar contato</button>
          </form>
          <ul class="followupHistory">${history || "<li><em>Sem histórico ainda.</em></li>"}</ul>
        </details>
        ${walletStatus === "active" ? `
          <details class="followupDetails followupWonDetails">
            <summary>Dar como ganho</summary>
            <form class="followupStatusForm" data-followup-key="${escapeHtml(itemKey)}" data-followup-status="won">
              <input type="date" name="status_date" value="${new Date().toISOString().slice(0, 10)}" required>
              ${callerSelect("marked_by", "Quem marcou")}
              <textarea name="note" placeholder="Observação: fechou retorno, comprou novo plano..."></textarea>
              <button type="submit">Marcar ganho</button>
            </form>
          </details>
          <details class="followupDetails followupLostDetails">
            <summary>Dar como perdido</summary>
            <form class="followupStatusForm followupLostForm" data-followup-key="${escapeHtml(itemKey)}" data-followup-status="lost">
              <input type="date" name="status_date" value="${new Date().toISOString().slice(0, 10)}" required>
              ${callerSelect("marked_by", "Quem marcou")}
              <textarea name="note" placeholder="Motivo: não respondeu, sem interesse, fechou fora..."></textarea>
              <button type="submit">Marcar perdido</button>
            </form>
          </details>
        ` : ""}
      </article>
    `;
  }).join("");
  const remaining = items.length - visibleItems.length;
  const moreHtml = remaining > 0
    ? `<div class="followupMore"><button type="button" id="followupShowMore">Mostrar mais ${integerFormat(Math.min(24, remaining))} de ${integerFormat(remaining)} pacientes</button></div>`
    : "";
  container.innerHTML = `${cardsHtml}${moreHtml}`;
}

async function savePatientFollowupContact(event) {
  event.preventDefault();
  const form = event.target;
  const item = followupItemByKey(form.dataset.followupKey);
  if (!item) return;
  const button = form.querySelector("button");
  const original = button.textContent;
  button.disabled = true;
  button.textContent = "Salvando...";
  try {
    const params = new URLSearchParams();
    if (state.selectedClinic) params.set("clinic", state.selectedClinic);
    if (state.followupOnlyMode) params.set("modo", "equipe");
    const data = new FormData(form);
    const res = await fetch(`/api/patient-followup-contact?${params.toString()}`, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        patient_key: item.patient_key,
        patient_name: item.patient_name,
        category: item.category,
        procedure_name: item.procedure_name,
        sale_date: item.sale_date,
        contact_date: data.get("contact_date"),
        contacted_by: data.get("contacted_by"),
        description: data.get("description"),
      }),
    });
    const payload = await res.json();
    if (!res.ok || !payload.ok) throw new Error(payload.error || "Não foi possível salvar o contato.");
    showNotice("Contato registrado.");
    await loadReport();
  } catch (error) {
    showNotice(error.message || "Não foi possível salvar o contato.");
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

async function setPatientFollowupStatus(item, status, formData = null) {
  if (!item) return;
  const params = new URLSearchParams();
  if (state.selectedClinic) params.set("clinic", state.selectedClinic);
  if (state.followupOnlyMode) params.set("modo", "equipe");
  const body = {
    patient_key: item.patient_key,
    patient_name: item.patient_name,
    category: item.category,
    procedure_name: item.procedure_name,
    sale_date: item.sale_date,
    status,
  };
  if (formData) {
    body.status_date = formData.get("status_date");
    body.marked_by = formData.get("marked_by");
    body.note = formData.get("note");
  }
  const res = await fetch(`/api/patient-followup-status?${params.toString()}`, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(body),
  });
  const payload = await res.json();
  if (!res.ok || !payload.ok) throw new Error(payload.error || "Não foi possível atualizar o paciente.");
}

async function savePatientFollowupStatus(event) {
  event.preventDefault();
  const form = event.target;
  const item = followupItemByKey(form.dataset.followupKey);
  if (!item) return;
  const button = form.querySelector("button");
  const original = button.textContent;
  button.disabled = true;
  button.textContent = "Salvando...";
  try {
    const status = form.dataset.followupStatus || "lost";
    await setPatientFollowupStatus(item, status, new FormData(form));
    state.selectedFollowupLost = status;
    showNotice(status === "won" ? "Paciente marcado como ganho." : "Paciente marcado como perdido.");
    await loadReport();
  } catch (error) {
    showNotice(error.message || "Não foi possível atualizar o paciente.");
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

async function restorePatientFollowup(event) {
  const button = event.target.closest(".followupRestoreBtn");
  if (!button) return;
  const item = followupItemByKey(button.dataset.followupKey);
  if (!item) return;
  const original = button.textContent;
  button.disabled = true;
  button.textContent = "Reativando...";
  try {
    await setPatientFollowupStatus(item, "active");
    showNotice("Paciente reativado na carteira.");
    await loadReport();
  } catch (error) {
    showNotice(error.message || "Não foi possível reativar o paciente.");
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

function renderQuoteFollowup(followup) {
  if (!followup.items) {
    const container = document.getElementById("quoteFollowupList");
    if (container) container.innerHTML = `<div class="empty">Carregando acompanhamento de orçamentos...</div>`;
    return;
  }
  const totals = followup.totals || {};
  state.quoteFollowupItems = followup.items || [];
  document.getElementById("quoteFollowupReferenceDate").textContent = `Base: ${formatFullDay(followup.reference_date)}`;
  document.getElementById("quoteFollowupTotal").textContent = integerFormat(totals.total || 0);
  document.getElementById("quoteFollowupAmount").textContent = `${brl.format(totals.amount || 0)} em carteira`;
  document.getElementById("quoteFollowupRed").textContent = integerFormat(totals.red || 0);
  document.getElementById("quoteFollowupDue").textContent = integerFormat(totals.due || 0);
  document.getElementById("quoteFollowupContacted").textContent = integerFormat(totals.contacted || 0);
  const walletSelect = document.getElementById("quoteFollowupStatusWallet");
  if (walletSelect) walletSelect.value = state.selectedQuoteFollowupWallet;
  const contactSelect = document.getElementById("quoteFollowupContactFilter");
  if (contactSelect) contactSelect.value = state.selectedQuoteFollowupContact;
  const statusSelect = document.getElementById("quoteFollowupStatusFilter");
  if (statusSelect) statusSelect.value = state.selectedQuoteFollowupStatus;
  renderQuoteFollowupList();
}

function filteredQuoteFollowupItems() {
  return (state.quoteFollowupItems || []).filter(item => {
    const walletStatus = item.wallet_status || (item.lost ? "lost" : item.won ? "won" : "active");
    if (state.selectedQuoteFollowupWallet === "active" && walletStatus !== "active") return false;
    if (state.selectedQuoteFollowupWallet === "lost" && walletStatus !== "lost") return false;
    if (state.selectedQuoteFollowupWallet === "won" && walletStatus !== "won") return false;
    if (state.selectedQuoteFollowupContact === "contacted" && !item.contact_count) return false;
    if (state.selectedQuoteFollowupContact === "not_contacted" && item.contact_count) return false;
    if (state.selectedQuoteFollowupStatus && item.status !== state.selectedQuoteFollowupStatus) return false;
    return true;
  });
}

function quoteFollowupItemKey(item) {
  return encodeURIComponent(item?.quote_key || "");
}

function quoteFollowupItemByKey(key) {
  return filteredQuoteFollowupItems().find(item => quoteFollowupItemKey(item) === key);
}

function renderQuoteFollowupList() {
  const container = document.getElementById("quoteFollowupList");
  if (!container) return;
  const items = filteredQuoteFollowupItems();
  const countEl = document.getElementById("quoteFollowupFilteredCount");
  if (countEl) {
    countEl.textContent = `${integerFormat(items.length)} ${items.length === 1 ? "orçamento" : "orçamentos"} no filtro`;
  }
  if (!items.length) {
    container.innerHTML = `<div class="empty">Nenhum orçamento encontrado para este filtro.</div>`;
    return;
  }
  const visibleItems = items.slice(0, state.quoteFollowupVisibleCount);
  const cardsHtml = visibleItems.map(item => {
    const itemKey = quoteFollowupItemKey(item);
    const walletStatus = item.wallet_status || "active";
    const lastContact = item.last_contact;
    const contactText = lastContact
      ? `${formatDay(lastContact.contact_date)} · ${escapeHtml(lastContact.contacted_by || "Sem nome")}`
      : "Ainda sem contato registrado";
    const phone = item.patient_phone ? `<a href="tel:${escapeHtml(item.patient_phone)}">${escapeHtml(item.patient_phone)}</a>` : "<span>-</span>";
    const email = item.patient_email ? `<a href="mailto:${escapeHtml(item.patient_email)}">${escapeHtml(item.patient_email)}</a>` : "<span>-</span>";
    const kommoHref = kommoFollowupUrl(item);
    const kommoLabel = item.kommo_lead_url ? "Abrir lead no Kommo" : "Buscar telefone no Kommo";
    const kommoLink = kommoHref
      ? `<a class="followupActionIcon followupKommoLink" href="${escapeHtml(kommoHref)}" target="_blank" rel="noopener" title="${escapeHtml(kommoLabel)}" aria-label="${escapeHtml(kommoLabel)}"><img src="kommo-icon.png" alt=""></a>`
      : "";
    const statusInfo = item.status_info || {};
    const walletText = item.won ? "Ganho" : (item.lost ? "Perdido" : item.status_label || statusLabel(item.status));
    const statusNote = walletStatus !== "active"
      ? `<div class="followupStatusNote ${escapeHtml(walletStatus)}">
          <b>${escapeHtml(item.won ? "Ganho" : "Perdido")}</b>
          <span>${formatDay(statusInfo.status_date)} · ${escapeHtml(statusInfo.marked_by || "Sem responsável")}</span>
          <em>${escapeHtml(statusInfo.note || "Sem observação")}</em>
          <button class="quoteFollowupRestoreBtn" type="button" data-quote-key="${escapeHtml(itemKey)}">Reativar orçamento</button>
        </div>`
      : "";
    const history = (item.contacts || []).map(contact => `
      <li>
        <b>${formatDay(contact.contact_date)}</b>
        <span>${escapeHtml(contact.contacted_by || "Sem nome")}</span>
        <em>${escapeHtml(contact.description || "Sem descrição")}</em>
      </li>
    `).join("");
    return `
      <article class="followupCard ${escapeHtml(item.status || "monitor")} ${item.lost ? "lost" : ""} ${item.won ? "won" : ""}">
        <div class="followupCardTop">
          <div>
            <span class="followupBadge">${escapeHtml(walletText)}</span>
            <h3>${escapeHtml(item.patient_name)}</h3>
            <p>Orçamento · ${brl.format(item.quote_total || 0)}</p>
          </div>
          <strong>${integerFormat(item.days_open || 0)} dias</strong>
        </div>
        <div class="followupFacts">
          <span><b>Data do orçamento</b>${formatFullDay(item.quote_date)}</span>
          <span><b>Doutor(a)</b>${escapeHtml(item.professional_name || "-")}</span>
          <span><b>Valor</b>${brl.format(item.quote_total || 0)}</span>
          <span><b>Contatos</b>${integerFormat(item.contact_count || 0)}</span>
        </div>
        <div class="followupContactLine">
          <span>${phone}</span>
          <span>${email}</span>
          <span>${contactText}</span>
          ${kommoLink}
        </div>
        ${statusNote}
        <details class="followupDetails">
          <summary>Registrar novo contato</summary>
          <form class="quoteFollowupForm followupForm" data-quote-key="${escapeHtml(itemKey)}">
            <input type="date" name="contact_date" value="${new Date().toISOString().slice(0, 10)}" required>
            ${callerSelect("contacted_by", "Quem chamou")}
            <textarea name="description" placeholder="Descrição do contato, objeção, retorno ou combinado"></textarea>
            <button type="submit">Salvar contato</button>
          </form>
          <ul class="followupHistory">${history || "<li><em>Sem histórico ainda.</em></li>"}</ul>
        </details>
        ${walletStatus === "active" ? `
          <details class="followupDetails followupWonDetails">
            <summary>Dar como ganho</summary>
            <form class="quoteFollowupStatusForm followupStatusForm" data-quote-key="${escapeHtml(itemKey)}" data-quote-status="won">
              <input type="date" name="status_date" value="${new Date().toISOString().slice(0, 10)}" required>
              ${callerSelect("marked_by", "Quem marcou")}
              <textarea name="note" placeholder="Observação: fechou no Clínica Experts, fechou depois, aguardando lançamento..."></textarea>
              <button type="submit">Marcar ganho</button>
            </form>
          </details>
          <details class="followupDetails followupLostDetails">
            <summary>Dar como perdido</summary>
            <form class="quoteFollowupStatusForm followupStatusForm followupLostForm" data-quote-key="${escapeHtml(itemKey)}" data-quote-status="lost">
              <input type="date" name="status_date" value="${new Date().toISOString().slice(0, 10)}" required>
              ${callerSelect("marked_by", "Quem marcou")}
              <textarea name="note" placeholder="Motivo: preço, sem retorno, decidiu não fazer..."></textarea>
              <button type="submit">Marcar perdido</button>
            </form>
          </details>
        ` : ""}
      </article>
    `;
  }).join("");
  const remaining = items.length - visibleItems.length;
  const moreHtml = remaining > 0
    ? `<div class="followupMore"><button type="button" id="quoteFollowupShowMore">Mostrar mais ${integerFormat(Math.min(24, remaining))} de ${integerFormat(remaining)} orçamentos</button></div>`
    : "";
  container.innerHTML = `${cardsHtml}${moreHtml}`;
}

async function saveQuoteFollowupContact(event) {
  event.preventDefault();
  const form = event.target;
  const item = quoteFollowupItemByKey(form.dataset.quoteKey);
  if (!item) return;
  const button = form.querySelector("button");
  const original = button.textContent;
  button.disabled = true;
  button.textContent = "Salvando...";
  try {
    const params = new URLSearchParams();
    if (state.selectedClinic) params.set("clinic", state.selectedClinic);
    const data = new FormData(form);
    const res = await fetch(`/api/quote-followup-contact?${params.toString()}`, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        quote_key: item.quote_key,
        patient_key: item.patient_key,
        patient_name: item.patient_name,
        quote_date: item.quote_date,
        quote_total: item.quote_total,
        contact_date: data.get("contact_date"),
        contacted_by: data.get("contacted_by"),
        description: data.get("description"),
      }),
    });
    const payload = await res.json();
    if (!res.ok || !payload.ok) throw new Error(payload.error || "Não foi possível salvar o contato.");
    showNotice("Contato do orçamento registrado.");
    await loadReport();
  } catch (error) {
    showNotice(error.message || "Não foi possível salvar o contato.");
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

async function setQuoteFollowupStatus(item, status, formData = null) {
  if (!item) return;
  const params = new URLSearchParams();
  if (state.selectedClinic) params.set("clinic", state.selectedClinic);
  const body = {
    quote_key: item.quote_key,
    patient_key: item.patient_key,
    patient_name: item.patient_name,
    quote_date: item.quote_date,
    quote_total: item.quote_total,
    status,
  };
  if (formData) {
    body.status_date = formData.get("status_date");
    body.marked_by = formData.get("marked_by");
    body.note = formData.get("note");
  }
  const res = await fetch(`/api/quote-followup-status?${params.toString()}`, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(body),
  });
  const payload = await res.json();
  if (!res.ok || !payload.ok) throw new Error(payload.error || "Não foi possível atualizar o orçamento.");
}

async function saveQuoteFollowupStatus(event) {
  event.preventDefault();
  const form = event.target;
  const item = quoteFollowupItemByKey(form.dataset.quoteKey);
  if (!item) return;
  const button = form.querySelector("button");
  const original = button.textContent;
  button.disabled = true;
  button.textContent = "Salvando...";
  try {
    const status = form.dataset.quoteStatus || "lost";
    await setQuoteFollowupStatus(item, status, new FormData(form));
    state.selectedQuoteFollowupWallet = status;
    showNotice(status === "won" ? "Orçamento marcado como ganho." : "Orçamento marcado como perdido.");
    await loadReport();
  } catch (error) {
    showNotice(error.message || "Não foi possível atualizar o orçamento.");
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

async function restoreQuoteFollowup(event) {
  const button = event.target.closest(".quoteFollowupRestoreBtn");
  if (!button) return;
  const item = quoteFollowupItemByKey(button.dataset.quoteKey);
  if (!item) return;
  const original = button.textContent;
  button.disabled = true;
  button.textContent = "Reativando...";
  try {
    await setQuoteFollowupStatus(item, "active");
    showNotice("Orçamento reativado na carteira.");
    await loadReport();
  } catch (error) {
    showNotice(error.message || "Não foi possível reativar o orçamento.");
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

function renderPaidTraffic(traffic) {
  const totals = traffic.totals || {};
  document.getElementById("trafficBasis").textContent = traffic.basis || "Meta Ads";
  document.getElementById("trafficAccount").textContent = traffic.account_id
    ? `Conta ${traffic.account_id}`
    : "Conta de anúncios";
  document.getElementById("trafficLastSync").textContent = traffic.last_sync
    ? `Atualizado: ${fmtDate(traffic.last_sync)}`
    : (traffic.connected ? "Token configurado. Clique para atualizar." : "Configure o token da Meta em Configurações.");
  document.getElementById("trafficSpend").textContent = brl.format(totals.spend || 0);
  document.getElementById("trafficLeads").textContent = integerFormat(totals.leads || 0);
  document.getElementById("trafficImpressions").textContent = integerFormat(totals.impressions || 0);
  document.getElementById("trafficReach").textContent = integerFormat(totals.reach || 0);
  document.getElementById("trafficClicks").textContent = integerFormat(totals.clicks || 0);
  document.getElementById("trafficCtr").textContent = formatPercent(totals.ctr);
  document.getElementById("trafficCpc").textContent = formatNullableMoney(totals.cpc);
  document.getElementById("trafficCpl").textContent = formatNullableMoney(totals.cpl);
  renderTrafficDailyChart(traffic.daily || []);
  renderTrafficCampaigns(traffic.campaigns || []);
}

function renderTrafficDailyChart(items) {
  const prepared = (items || []).map(item => ({
    ...item,
    spend: Number(item.spend || 0),
    leads: Number(item.leads || 0),
    clicks: Number(item.clicks || 0),
    impressions: Number(item.impressions || 0),
  }));
  const el = document.getElementById("trafficDailyChart");
  const active = prepared.filter(item => item.day);
  const hasSignal = active.some(item => item.spend || item.leads || item.clicks || item.impressions);
  if (!active.length || !hasSignal) {
    el.innerHTML = `<div class="empty">Ainda não há dados de tráfego pago para o período.</div>`;
    return;
  }

  const totalSpend = active.reduce((sum, item) => sum + item.spend, 0);
  const totalLeads = active.reduce((sum, item) => sum + item.leads, 0);
  const bestSpendDay = [...active].sort((a, b) => b.spend - a.spend)[0];
  const bestLeadDay = [...active].sort((a, b) => b.leads - a.leads)[0];
  const width = Math.max(860, active.length * 44);
  const height = 330;
  const pad = { top: 34, right: 78, bottom: 58, left: 72 };
  const chartW = width - pad.left - pad.right;
  const chartH = height - pad.top - pad.bottom;
  const maxSpend = Math.max(...active.map(item => item.spend), 1);
  const maxLeads = Math.max(...active.map(item => item.leads), 1);
  const step = active.length > 1 ? chartW / (active.length - 1) : chartW;
  const barW = Math.min(22, Math.max(10, step * .46));
  const xFor = index => pad.left + (active.length === 1 ? chartW / 2 : index * step);
  const ySpend = value => pad.top + chartH - ((value || 0) / maxSpend) * chartH;
  const yLeads = value => pad.top + chartH - ((value || 0) / maxLeads) * chartH;
  const leadPath = active.map((item, index) => `${index ? "L" : "M"} ${xFor(index).toFixed(1)} ${yLeads(item.leads).toFixed(1)}`).join(" ");
  const spendArea = active.map((item, index) => `${index ? "L" : "M"} ${xFor(index).toFixed(1)} ${ySpend(item.spend).toFixed(1)}`).join(" ");
  const ticks = [0, .25, .5, .75, 1].map(ratio => {
    const y = pad.top + chartH - chartH * ratio;
    return `
      <line class="lineGrid" x1="${pad.left}" y1="${y}" x2="${width - pad.right}" y2="${y}"></line>
      <text class="chartAxis" x="${pad.left - 12}" y="${y + 4}" text-anchor="end">${escapeHtml(moneyShort(maxSpend * ratio))}</text>
      <text class="chartAxis" x="${width - pad.right + 12}" y="${y + 4}">${integerFormat(Math.round(maxLeads * ratio))}</text>
    `;
  }).join("");
  const bars = active.map((item, index) => {
    const x = xFor(index) - barW / 2;
    const y = ySpend(item.spend);
    const h = pad.top + chartH - y;
    return `<rect class="trafficSpendBar" x="${x}" y="${y}" width="${barW}" height="${Math.max(2, h)}" rx="6"></rect>`;
  }).join("");
  const points = active.map((item, index) => `
    <circle class="trafficLeadPoint" cx="${xFor(index)}" cy="${yLeads(item.leads)}" r="${item.leads ? 5.2 : 3.8}"></circle>
  `).join("");
  const labels = active.map((item, index) => {
    if (active.length > 18 && index % Math.ceil(active.length / 12)) return "";
    return `<text class="pointDate tilted" x="${xFor(index)}" y="${height - 20}">${formatShortDay(item.day)}</text>`;
  }).join("");
  const zones = active.map((item, index) => {
    const x = xFor(index);
    const previous = index ? xFor(index - 1) : pad.left;
    const next = index < active.length - 1 ? xFor(index + 1) : width - pad.right;
    const hitW = Math.max(28, (next - previous) / 2);
    return `<rect class="chartHitZone" data-index="${index}" x="${x - hitW / 2}" y="${pad.top}" width="${hitW}" height="${chartH}"></rect>`;
  }).join("");

  el.innerHTML = `
    <div class="trafficDailySummary">
      <span><b>${brl.format(totalSpend)}</b><small>Investimento no período</small></span>
      <span><b>${integerFormat(totalLeads)}</b><small>Leads Meta</small></span>
      <span><b>${formatShortDay(bestSpendDay.day)}</b><small>Maior investimento: ${brl.format(bestSpendDay.spend)}</small></span>
      <span><b>${formatShortDay(bestLeadDay.day)}</b><small>Mais leads: ${integerFormat(bestLeadDay.leads)}</small></span>
    </div>
    <div class="generalChartLegend trafficLegend">
      <span><i class="trafficSpendSwatch"></i>Investimento</span>
      <span><i class="trafficLeadSwatch"></i>Leads</span>
    </div>
    <div class="trafficChartScroller">
      <svg class="trafficDailySvg" viewBox="0 0 ${width} ${height}" role="img">
        <defs>
          <linearGradient id="trafficSpendGradient" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stop-color="#102B25"></stop>
            <stop offset="100%" stop-color="#969A78"></stop>
          </linearGradient>
          <linearGradient id="trafficAreaGradient" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stop-color="#102B25" stop-opacity=".22"></stop>
            <stop offset="100%" stop-color="#969A78" stop-opacity="0"></stop>
          </linearGradient>
        </defs>
        ${ticks}
        <path class="trafficSpendArea" d="${spendArea} L ${xFor(active.length - 1).toFixed(1)} ${pad.top + chartH} L ${xFor(0).toFixed(1)} ${pad.top + chartH} Z"></path>
        ${bars}
        <path class="trafficLeadLine" d="${leadPath}"></path>
        ${points}
        ${zones}
        ${labels}
      </svg>
      <div class="chartTooltip trafficTooltip" hidden></div>
    </div>
  `;
  const tooltip = el.querySelector(".trafficTooltip");
  el.querySelectorAll(".chartHitZone").forEach(zone => {
    const item = active[Number(zone.dataset.index)];
    const show = event => {
      const tooltipHtml = `
        <strong>${formatDay(item.day)}</strong>
        <span><i style="background:#102B25"></i>Investimento: ${brl.format(item.spend || 0)}</span>
        <span><i style="background:#969A78"></i>Leads: ${integerFormat(item.leads || 0)}</span>
        <span><i style="background:#55788F"></i>Cliques: ${integerFormat(item.clicks || 0)}</span>
        <span><i style="background:#387A5A"></i>CPC: ${formatNullableMoney(item.cpc)} · CPL: ${formatNullableMoney(item.cpl)}</span>
      `;
      showGeneralTooltip(event, item, tooltip, el, { tooltip: () => tooltipHtml });
    };
    zone.addEventListener("mouseenter", show);
    zone.addEventListener("mousemove", show);
    zone.addEventListener("mouseleave", () => {
      tooltip.hidden = true;
    });
  });
}

function renderTrafficCampaigns(campaigns) {
  const el = document.getElementById("trafficCampaigns");
  const rows = [...(campaigns || [])]
    .sort((a, b) => Number(b.spend || 0) - Number(a.spend || 0));
  if (!rows.length) {
    el.innerHTML = `<div class="empty">Nenhuma campanha sincronizada ainda.</div>`;
    return;
  }
  const maxSpend = Math.max(...rows.map(row => Number(row.spend || 0)), 1);
  el.innerHTML = rows.map(row => {
    const spend = Number(row.spend || 0);
    const leads = Number(row.leads || 0);
    const cpl = leads ? spend / leads : 0;
    return `
      <article class="trafficCampaignRow">
        <div class="trafficCampaignName">
          <strong>${escapeHtml(row.campaign_name || "Campanha sem nome")}</strong>
          <span>${integerFormat(row.impressions || 0)} impressões · ${integerFormat(row.clicks || 0)} cliques</span>
          <i><b style="width:${Math.max(spend ? 4 : 0, (spend / maxSpend) * 100)}%"></b></i>
        </div>
        <div><span>Investimento</span><strong>${brl.format(spend)}</strong></div>
        <div><span>Leads</span><strong>${integerFormat(leads)}</strong></div>
        <div><span>CPL</span><strong>${formatNullableMoney(cpl)}</strong></div>
        <div><span>CTR</span><strong>${formatPercent(row.ctr)}</strong></div>
      </article>
    `;
  }).join("");
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

function renderGeneralDoctorFilter() {
  const select = document.getElementById("generalDoctorFilter");
  if (!select) return;
  const rows = state.report?.clinica_experts?.doctor_cross || [];
  const rowDoctors = rows.map(row => row.doctor).filter(Boolean);
  const allDoctors = [...new Set([...state.allGeneralDoctors, ...rowDoctors, ...state.allDoctors])]
    .sort((a, b) => a.localeCompare(b, "pt-BR"));
  state.allGeneralDoctors = allDoctors;
  if (state.selectedGeneralDoctor && !allDoctors.includes(state.selectedGeneralDoctor)) {
    state.selectedGeneralDoctor = "";
  }
  const currentOptions = [...select.options].map(option => option.value).join("|");
  const nextOptions = ["", ...allDoctors].join("|");
  if (currentOptions !== nextOptions) {
    select.innerHTML = `
      <option value="">Todos os profissionais</option>
      ${allDoctors.map(doctor => `<option value="${escapeHtml(doctor)}">${escapeHtml(doctor)}</option>`).join("")}
    `;
  }
  select.value = state.selectedGeneralDoctor;
}

function renderSellerFilter() {
  const select = document.getElementById("sellerFilter");
  if (!select) return;
  const currentOptions = [...select.options].map(option => option.value).join("|");
  const nextOptions = ["", ...state.allSellers].join("|");
  if (currentOptions !== nextOptions) {
    select.innerHTML = `
      <option value="">Todos considerados</option>
      ${state.allSellers.map(seller => `<option value="${escapeHtml(seller)}">${escapeHtml(seller)}</option>`).join("")}
    `;
  }
  select.value = state.selectedSeller;
}

function renderBookingRegistryUserFilter() {
  const select = document.getElementById("bookingRegistryUserFilter");
  if (!select) return;
  const filter = select.closest(".miniFilter");
  if (filter) filter.hidden = false;
  const currentOptions = [...select.options].map(option => `${option.value}:${option.textContent}`).join("|");
  const emptyLabel = state.allBookingRegistryUsers.length ? "Todos" : "Aguardando registros";
  const nextOptions = [`:${emptyLabel}`, ...state.allBookingRegistryUsers.map(user => `${user}:${user}`)].join("|");
  if (currentOptions !== nextOptions) {
    select.innerHTML = `
      <option value="">${emptyLabel}</option>
      ${state.allBookingRegistryUsers.map(user => `<option value="${escapeHtml(user)}">${escapeHtml(user)}</option>`).join("")}
    `;
  }
  select.value = state.selectedBookingRegistryUser;
  select.disabled = !state.allBookingRegistryUsers.length;
  select.title = state.allBookingRegistryUsers.length
    ? "Filtra somente o gráfico de agendamentos pelo usuário que registrou o agendamento"
    : "";
}

function filteredBookingDailyItems(items) {
  if (!state.selectedBookingRegistryUser) return items;
  return items.map(item => {
    const registryUser = (item.by_registry_user || []).find(row => row.user === state.selectedBookingRegistryUser);
    return {
      ...item,
      total: registryUser?.total || 0,
      by_doctor: registryUser?.by_doctor || [],
    };
  });
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
            <stop offset="0%" stop-color="#969A78"></stop>
            <stop offset="55%" stop-color="#B4B79B"></stop>
            <stop offset="100%" stop-color="#B4B79B"></stop>
          </linearGradient>
          <linearGradient id="${targetId}Area" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stop-color="#969A78" stop-opacity=".22"></stop>
            <stop offset="100%" stop-color="#B4B79B" stop-opacity=".03"></stop>
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
  const colors = ["#102B25", "#969A78", "#B4B79B", "#B58A45", "#55788F", "#387A5A", "#B95656", "#E8E3D9"];
  const total = items.reduce((sum, item) => sum + (item.total || 0), 0);
  if (!items.length || !total) {
    donut.style.background = "#F5F5F3";
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

function formatFullDay(day) {
  if (!day) return "-";
  const [year, month, date] = day.split("-");
  return `${date}/${month}/${year}`;
}

async function loadReport() {
  if (!state.selectedClinic) {
    showClinicLanding();
    return;
  }
  try {
    const res = await fetch(`/api/report${buildQuery()}`);
    const payload = await res.json();
    if (res.status === 401) {
      sessionStorage.removeItem(clinicAccessKey(state.selectedClinic));
      showDashboard();
      openClinicAccessModal(state.selectedClinic);
      showNotice(payload.error || "Digite o código de acesso para continuar.");
      return;
    }
    if (!res.ok || payload.ok === false) {
      throw new Error(payload.error || "Não foi possível carregar o relatório.");
    }
    state.report = payload;
    render();
    scheduleAutoPrint();
  } catch (error) {
    const localFile = window.location.protocol === "file:";
    showNotice(
      localFile
        ? "Abra pelo link online do Railway. A versão em arquivo local não consegue buscar os dados da API."
        : friendlyError(error.message || "Não foi possível conectar à API do relatório. Tente atualizar a página.")
    );
  }
}

async function syncNow() {
  const clinic = clinics[state.selectedClinic] || clinics.vielle;
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
    btn.textContent = clinic.commercialSource === "midas" ? "Atualizar Midas" : "Atualizar";
  }
}

async function syncClinicaNow() {
  const btn = document.getElementById("syncClinicaBtn");
  btn.disabled = true;
  btn.textContent = "Buscando historico...";
  try {
    const params = new URLSearchParams();
    if (state.selectedClinic) params.set("clinic", state.selectedClinic);
    params.set("historical", "1");
    const res = await fetch(`/api/sync-clinica?${params.toString()}`);
    const payload = await res.json();
    if (!payload.ok) throw new Error(payload.error || "Nao foi possivel sincronizar Clínica Experts.");
    showNotice(payload.message || "Sincronização histórica iniciada.");
    await loadReport();
    setTimeout(loadReport, 15000);
  } catch (error) {
    showNotice(friendlyError(error.message));
    await loadReport();
  } finally {
    btn.disabled = false;
    btn.textContent = "Atualizar Clínica Experts";
  }
}

async function syncTrafficNow() {
  const btn = document.getElementById("syncTrafficBtn");
  if (!btn) return;
  const original = btn.textContent;
  btn.disabled = true;
  btn.textContent = "Atualizando tráfego...";
  try {
    const res = await fetch(`/api/sync-traffic${buildQuery()}`);
    const payload = await res.json();
    if (!payload.ok) throw new Error(payload.error || "Nao foi possivel sincronizar o tráfego pago.");
    showNotice(`Tráfego pago atualizado: ${payload.rows || 0} linhas.`);
    await loadReport();
  } catch (error) {
    showNotice(friendlyError(error.message));
  } finally {
    btn.disabled = false;
    btn.textContent = original;
  }
}

async function saveMonthlyGoal() {
  const month = state.selectedMonth || currentMonthValue();
  const goals = {};
  document.querySelectorAll("[data-goal-doctor]").forEach(input => {
    goals[input.dataset.goalDoctor] = input.value || "0";
  });
  const btn = document.getElementById("saveMonthlyGoalBtn");
  const original = btn.textContent;
  btn.disabled = true;
  btn.textContent = "Salvando metas...";
  try {
    const params = new URLSearchParams();
    if (state.selectedClinic) params.set("clinic", state.selectedClinic);
    const res = await fetch(`/api/monthly-goal?${params.toString()}`, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({month, goals}),
    });
    const payload = await res.json();
    if (!res.ok || !payload.ok) throw new Error(payload.error || "Não foi possível salvar a meta.");
    showNotice("Metas mensais salvas.");
    await loadReport();
  } catch (error) {
    showNotice(error.message || "Não foi possível salvar a meta.");
  } finally {
    btn.disabled = false;
    btn.textContent = original;
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
  btn.disabled = true;
  btn.textContent = "Abrindo PDF...";
  requestAnimationFrame(() => {
    window.print();
    btn.disabled = false;
    btn.textContent = original;
  });
  setTimeout(() => {
    btn.disabled = false;
    btn.textContent = original;
  }, 2400);
}

function scheduleAutoPrint() {
  if (!pendingAutoPrint) return;
  pendingAutoPrint = false;
  const cleanParams = new URLSearchParams(window.location.search);
  cleanParams.delete("print");
  const cleanQuery = cleanParams.toString();
  history.replaceState(null, "", `${window.location.pathname}${cleanQuery ? `?${cleanQuery}` : ""}`);
  setTimeout(() => window.print(), 700);
}

document.getElementById("connectBtn").addEventListener("click", () => {
  const clinic = clinics[state.selectedClinic] || clinics.vielle;
  if (clinic.commercialSource === "midas") {
    window.location.href = `/settings.html?clinic=${encodeURIComponent(clinic.id)}`;
    return;
  }
  window.location.href = `/auth/start${buildQuery()}`;
});

document.getElementById("syncBtn").addEventListener("click", syncNow);
document.getElementById("syncClinicaBtn").addEventListener("click", syncClinicaNow);
document.getElementById("syncTrafficBtn")?.addEventListener("click", syncTrafficNow);
document.getElementById("exportPdfBtn").addEventListener("click", exportPdf);
document.getElementById("followupLostFilter")?.addEventListener("change", event => {
  state.selectedFollowupLost = event.target.value || "active";
  state.followupVisibleCount = 24;
  renderPatientFollowupList();
});
document.getElementById("followupContactFilter")?.addEventListener("change", event => {
  state.selectedFollowupContact = event.target.value;
  state.followupVisibleCount = 24;
  renderPatientFollowupList();
});
document.getElementById("followupCategoryFilter")?.addEventListener("change", event => {
  state.selectedFollowupCategory = event.target.value;
  state.followupVisibleCount = 24;
  renderPatientFollowupList();
});
document.getElementById("followupStatusFilter")?.addEventListener("change", event => {
  state.selectedFollowupStatus = event.target.value;
  state.followupVisibleCount = 24;
  renderPatientFollowupList();
});
document.getElementById("followupLastFrom")?.addEventListener("change", event => {
  state.selectedFollowupLastFrom = event.target.value;
  state.followupVisibleCount = 24;
  renderPatientFollowupList();
});
document.getElementById("followupLastTo")?.addEventListener("change", event => {
  state.selectedFollowupLastTo = event.target.value;
  state.followupVisibleCount = 24;
  renderPatientFollowupList();
});
document.getElementById("patientFollowupList")?.addEventListener("submit", event => {
  if (event.target.matches(".followupForm")) savePatientFollowupContact(event);
  if (event.target.matches(".followupStatusForm")) savePatientFollowupStatus(event);
});
document.getElementById("patientFollowupList")?.addEventListener("click", event => {
  if (event.target.matches("#followupShowMore")) {
    state.followupVisibleCount += 24;
    renderPatientFollowupList();
    return;
  }
  restorePatientFollowup(event);
});
document.getElementById("quoteFollowupStatusWallet")?.addEventListener("change", event => {
  state.selectedQuoteFollowupWallet = event.target.value;
  state.quoteFollowupVisibleCount = 24;
  renderQuoteFollowupList();
});
document.getElementById("quoteFollowupContactFilter")?.addEventListener("change", event => {
  state.selectedQuoteFollowupContact = event.target.value;
  state.quoteFollowupVisibleCount = 24;
  renderQuoteFollowupList();
});
document.getElementById("quoteFollowupStatusFilter")?.addEventListener("change", event => {
  state.selectedQuoteFollowupStatus = event.target.value;
  state.quoteFollowupVisibleCount = 24;
  renderQuoteFollowupList();
});
document.getElementById("quoteFollowupList")?.addEventListener("submit", event => {
  if (event.target.matches(".quoteFollowupForm")) saveQuoteFollowupContact(event);
  if (event.target.matches(".quoteFollowupStatusForm")) saveQuoteFollowupStatus(event);
});
document.getElementById("quoteFollowupList")?.addEventListener("click", event => {
  if (event.target.matches("#quoteFollowupShowMore")) {
    state.quoteFollowupVisibleCount += 24;
    renderQuoteFollowupList();
    return;
  }
  restoreQuoteFollowup(event);
});
document.querySelectorAll("[data-rank-close]").forEach(button => {
  button.addEventListener("click", closeRankModal);
});
document.querySelectorAll("[data-goals-close]").forEach(button => {
  button.addEventListener("click", closeGoalsModal);
});
document.addEventListener("keydown", event => {
  if (event.key === "Escape" && !document.getElementById("rankModal").hidden) {
    closeRankModal();
  }
  if (event.key === "Escape" && !document.getElementById("goalsModal").hidden) {
    closeGoalsModal();
  }
});
document.querySelectorAll(".tabBtn").forEach(button => {
  button.addEventListener("click", () => {
    state.activeView = button.dataset.view || "commercialView";
    document.querySelector(".viewTabs")?.classList.remove("open");
    document.getElementById("mobileTabsToggle")?.setAttribute("aria-expanded", "false");
    applyActiveViewState();
    if (state.activeView === "generalView" || state.activeView === "patientFollowupView" || state.activeView === "quoteFollowupView") {
      loadReport();
    } else {
      render();
    }
  });
});
document.getElementById("mobileTabsToggle")?.addEventListener("click", event => {
  event.stopPropagation();
  const tabs = document.querySelector(".viewTabs");
  if (!tabs) return;
  const willOpen = !tabs.classList.contains("open");
  tabs.classList.toggle("open", willOpen);
  event.currentTarget.setAttribute("aria-expanded", String(willOpen));
});
document.addEventListener("click", event => {
  const tabs = document.querySelector(".viewTabs");
  const toggle = document.getElementById("mobileTabsToggle");
  if (!tabs || !toggle || !tabs.classList.contains("open")) return;
  if (tabs.contains(event.target) || toggle.contains(event.target)) return;
  tabs.classList.remove("open");
  toggle.setAttribute("aria-expanded", "false");
});
document.getElementById("generalMonth").addEventListener("change", event => {
  state.selectedMonth = event.target.value || currentMonthValue();
  normalizeGeneralMonth();
  loadReport();
});
document.getElementById("goalsModalMonthInput").addEventListener("change", event => {
  state.selectedMonth = event.target.value || currentMonthValue();
  normalizeGeneralMonth();
  loadReport();
});
document.getElementById("generalDoctorFilter").addEventListener("change", event => {
  state.selectedGeneralDoctor = event.target.value;
  loadReport();
});
document.getElementById("openGoalsModalBtn").addEventListener("click", openGoalsModal);
document.getElementById("saveMonthlyGoalBtn").addEventListener("click", saveMonthlyGoal);
document.getElementById("selectAllBtn").addEventListener("click", () => {
  state.selectedPipelines.clear();
  state.selectedDoctor = "";
  state.selectedSeller = "";
  state.selectedBookingRegistryUser = "";
  loadReport();
});
document.getElementById("doctorFilter").addEventListener("change", event => {
  state.selectedDoctor = event.target.value;
  state.selectedPipelines.clear();
  loadReport();
});
document.getElementById("sellerFilter").addEventListener("change", event => {
  state.selectedSeller = event.target.value;
  loadReport();
});
document.getElementById("bookingRegistryUserFilter").addEventListener("change", event => {
  state.selectedBookingRegistryUser = event.target.value;
  render();
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
pendingAutoPrint = params.get("print") === "1";
const initialView = params.get("view");
if (initialView && document.getElementById(initialView)) {
  state.activeView = initialView;
}
if (params.get("error")) showNotice(decodeURIComponent(params.get("error")));
if (params.get("connected")) showNotice("Kommo conectado. A primeira sincronizacao foi iniciada.");

document.querySelectorAll("[data-clinic-select]").forEach(button => {
  button.addEventListener("click", () => {
    requestClinicAccess(button.dataset.clinicSelect, true, button.dataset.accessMode || "dashboard");
  });
});
document.getElementById("clinicAccessForm").addEventListener("submit", async event => {
  event.preventDefault();
  const modal = document.getElementById("clinicAccessModal");
  const clinicId = modal.dataset.clinicId;
  const accessMode = modal.dataset.accessMode || accessModeKey();
  const clinic = clinics[clinicId] || clinics.vielle;
  const value = document.getElementById("clinicAccessCode").value.trim();
  const submitButton = event.currentTarget.querySelector('button[type="submit"]');
  submitButton.disabled = true;
  try {
    if (window.location.protocol === "file:") {
      throw new Error("Abra pelo link online do Railway para validar o código e carregar os dados.");
    }
    const response = await fetch("/api/clinic-access", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({clinic_id: clinic.id, access_code: value, access_mode: accessMode}),
    });
    const result = await response.json();
    if (!response.ok || !result.ok) {
      throw new Error(result.error || "Código incorreto.");
    }
    sessionStorage.setItem(clinicAccessKey(clinic.id, accessMode), "ok");
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
  state.followupOnlyMode = false;
  state.activeView = "generalView";
  localStorage.removeItem("selectedClinic");
  history.pushState(null, "", window.location.pathname);
  showClinicLanding();
});

state.followupOnlyMode = params.get("modo") === "equipe" || params.get("mode") === "team" || params.get("staff") === "1";
if (state.followupOnlyMode) state.activeView = "patientFollowupView";
const initialClinic = params.get("clinic");
if (initialClinic && clinics[initialClinic]) {
  requestClinicAccess(initialClinic, false, accessModeKey());
} else {
  showClinicLanding();
}
setInterval(() => {
  if (state.selectedClinic) loadReport();
}, 60_000);
