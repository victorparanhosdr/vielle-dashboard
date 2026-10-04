class FinancialCompetenceReport {
  constructor() {
    this.root = document.getElementById("competenceReport");
    this.elements = Object.fromEntries(["Title", "From", "To", "Doctor", "Search", "Contact", "Category", "Type", "DateBasis", "PaymentStatus", "BasisNote", "BasisCaption", "DateLabel", "Export", "Count", "Income", "Expense", "Balance", "IncomeGross", "ExpenseGross", "BalanceGross", "IncomeLabel", "ExpenseLabel", "BalanceLabel", "IncomeReceived", "ExpensePaid", "IncomeOpen", "ExpenseOpen", "BalanceReceived", "Rows", "Warning", "WarningText", "Validation", "Page", "Previous", "Next", "Clear", "FilterCount", "FiltersToggle", "FilterFields"].map(key => [key, document.getElementById("competence" + key)]));
    this.filters = {direction: "all", sort: "date", order: "desc", page: 1};
    this.money = new Intl.NumberFormat("pt-BR", {style: "currency", currency: "BRL"});
    this.context = null;
    this.ready = false;
    this.busy = false;
    this.elements.FiltersToggle.addEventListener("click", () => {
      const expanded = this.elements.FilterFields.hidden;
      this.elements.FilterFields.hidden = !expanded;
      this.elements.FiltersToggle.setAttribute("aria-expanded", String(expanded));
    });
    ["From", "To", "Doctor", "Contact", "Category", "Type", "DateBasis", "PaymentStatus"].forEach(key => this.elements[key].addEventListener("change", () => this.load(true)));
    this.elements.Search.addEventListener("input", () => {
      clearTimeout(this.searchTimer);
      this.searchTimer = setTimeout(() => this.load(true), 250);
    });
    this.root.querySelectorAll("[data-competence-direction]").forEach(button => button.addEventListener("click", () => {
      this.filters.direction = button.dataset.competenceDirection;
      this.load(true);
    }));
    this.root.querySelectorAll("[data-competence-sort]").forEach(button => button.addEventListener("click", () => {
      const sort = button.dataset.competenceSort;
      this.filters.order = this.filters.sort === sort && this.filters.order === "asc" ? "desc" : "asc";
      this.filters.sort = sort;
      this.load(true);
    }));
    this.elements.Previous.addEventListener("click", () => { this.filters.page--; this.load(); });
    this.elements.Next.addEventListener("click", () => { this.filters.page++; this.load(); });
    this.elements.Clear.addEventListener("click", () => {
      ["Search", "Contact", "Category", "Type"].forEach(key => { this.elements[key].value = ""; });
      this.elements.PaymentStatus.value = "all";
      this.load(true);
    });
    this.elements.Export.addEventListener("click", () => this.export());
  }

  sync(context) {
    const changed = !this.context || ["clinic", "doctor", "dateFrom", "dateTo"].some(key => this.context[key] !== context[key]);
    if (changed) {
      this.elements.From.value = context.dateFrom;
      this.elements.To.value = context.dateTo;
      this.selectOptions("Doctor", (context.doctors || []).map(name => ({id: name, name})), "Todos os profissionais", context.doctor);
      if (this.context?.clinic !== context.clinic) {
        ["Search", "Contact", "Category", "Type"].forEach(key => { this.elements[key].value = ""; });
        this.filters.direction = "all";
        this.elements.DateBasis.value = "competence";
        this.elements.PaymentStatus.value = "all";
      }
      this.filters.page = 1;
    }
    this.context = context;
    this.elements.Export.hidden = !context.canExport;
    this.load();
  }

  suspend() {
    clearTimeout(this.searchTimer);
    this.request?.abort();
    this.ready = false;
    financialValidation.clear("competence");
  }

  query() {
    return new URLSearchParams({clinic: this.context.clinic,
      date_from: this.elements.From.value, date_to: this.elements.To.value,
      doctor: this.elements.Doctor.value, search: this.elements.Search.value,
      contact: this.elements.Contact.value, category: this.elements.Category.value,
      title_type: this.elements.Type.value, date_basis: this.elements.DateBasis.value,
      payment_status: this.elements.PaymentStatus.value, ...this.filters});
  }

  validationContext() {
    return {clinic: this.context.clinicName || this.context.clinic,
      from: this.elements.From.value, to: this.elements.To.value, doctor: this.elements.Doctor.value,
      dateBasis: this.elements.DateBasis.value, paymentStatus: this.elements.PaymentStatus.value};
  }

  async load(resetPage = false) {
    if (!this.context) return;
    clearTimeout(this.searchTimer);
    if (resetPage) this.filters.page = 1;
    this.request?.abort();
    const request = new AbortController();
    this.request = request;
    this.ready = false;
    this.root.setAttribute("aria-busy", "true");
    this.elements.Export.disabled = true;
    this.elements.Previous.disabled = this.elements.Next.disabled = true;
    this.elements.Warning.hidden = true;
    this.elements.Validation.hidden = true;
    financialValidation.prepare("competence", this.validationContext());
    this.elements.Count.textContent = "Carregando...";
    ["Income", "Expense", "Balance", "IncomeGross", "ExpenseGross", "BalanceGross", "IncomeReceived", "ExpensePaid", "IncomeOpen", "ExpenseOpen", "BalanceReceived"].forEach(key => { this.elements[key].textContent = "-"; });
    this.message("Carregando relatório...");
    try {
      const response = await fetch(`/api/financial-competence?${this.query()}`, {signal: request.signal});
      if (response.status === 401) { location.href = `/login?next=${encodeURIComponent(location.pathname + location.search)}`; return; }
      const report = await response.json();
      if (!response.ok) throw new Error(report.error || "Não foi possível carregar o relatório.");
      if (this.request !== request) return;
      this.render(report);
      this.ready = true;
    } catch (error) {
      if (error.name !== "AbortError" && this.request === request) {
        financialValidation.clear("competence");
        this.message(error.message);
        this.elements.Count.textContent = "Relatório indisponível";
      }
    } finally {
      if (this.request === request) {
        this.root.removeAttribute("aria-busy");
        this.elements.Export.disabled = !this.ready || this.busy;
      }
    }
  }

  selectOptions(key, options, label, value = this.elements[key].value) {
    const select = this.elements[key];
    select.replaceChildren(new Option(label, ""), ...options.map(item => new Option(item.name, item.id)));
    // Keep a selected filter even if refreshed data no longer contains it.
    if (value && !options.some(item => item.id === value)) select.add(new Option(value, value));
    select.value = value;
  }

  message(text) {
    const td = document.createElement("td");
    td.colSpan = this.elements.DateBasis.value === "receipt" ? 8 : 10;
    td.className = "competenceEmpty";
    td.textContent = text;
    const tr = document.createElement("tr");
    tr.append(td);
    this.elements.Rows.replaceChildren(tr);
  }

  render(report) {
    const cash = report.date_basis === "receipt";
    this.root.dataset.dateBasis = report.date_basis;
    this.elements.Title.textContent = cash ? "Relatório de recebimentos e pagamentos" : "Relatório de competência";
    this.elements.BasisCaption.textContent = cash ? "Compensação · recebimento / pagamento" : "Competência · emissão do título";
    this.elements.BasisCaption.title = report.basis;
    this.elements.DateLabel.textContent = cash ? "Recebimento / pagamento" : "Competência";
    this.elements.BasisNote.textContent = cash
      ? "Somente pagamentos efetivos no período · valores em aberto e compensações futuras não entram."
      : "Títulos completos por competência / emissão · recebido e pago confirmados até hoje, independentemente do mês do pagamento.";
    this.root.querySelectorAll("[data-competence-accrual]").forEach(element => { element.hidden = cash; });
    const cashLabels = {Income: "Recebido líquido", Expense: "Pago líquido", Balance: "Recebido − pago · líquido"};
    ["Income", "Expense", "Balance"].forEach(key => { this.elements[key + "Label"].textContent = cash ? cashLabels[key] : "Total por competência · líquido"; });
    this.filters.page = report.page;
    this.elements.Count.textContent = `${report.count} ${report.count === 1 ? "registro" : "registros"}`;
    ["Income", "Expense", "Balance"].forEach(key => {
      this.elements[key].textContent = this.money.format(report.totals[key.toLowerCase()]);
      this.elements[key + "Gross"].textContent = this.money.format(report.totals[key.toLowerCase() + "_gross"]);
    });
    ["IncomeReceived", "ExpensePaid", "IncomeOpen", "ExpenseOpen", "BalanceReceived"].forEach(key => {
      const field = key.replace(/[A-Z]/g, (letter, index) => (index ? "_" : "") + letter.toLowerCase());
      this.elements[key].textContent = this.money.format(report.settlement_totals[field]);
    });
    this.selectOptions("Contact", report.options.contacts, "Todos os contatos");
    this.selectOptions("Category", report.options.categories.map(name => ({id: name, name})), "Todas as categorias");
    this.selectOptions("Type", report.options.title_types.map(name => ({id: name, name})), "Todos os tipos");
    const activeFilters = ["Contact", "Category", "Type"].filter(key => this.elements[key].value).length;
    this.elements.FilterCount.textContent = activeFilters;
    this.elements.FilterCount.hidden = !activeFilters;
    this.root.querySelectorAll("[data-competence-direction]").forEach(button => button.setAttribute("aria-pressed", String(button.dataset.competenceDirection === this.filters.direction)));
    this.root.querySelectorAll("[data-competence-sort]").forEach(button => {
      const active = button.dataset.competenceSort === this.filters.sort;
      button.closest("th").setAttribute("aria-sort", active ? (this.filters.order === "asc" ? "ascending" : "descending") : "none");
      button.querySelector("span").textContent = active ? (this.filters.order === "asc" ? "↑" : "↓") : "↕";
    });
    const labels = {date: cash ? "sem data efetiva de recebimento/pagamento" : "sem data de competência/emissão", amount: "sem valores completos", net_amount: "sem líquido confirmado", direction: "sem movimento identificado", professional: "sem vínculo seguro com o profissional"};
    const warnings = Object.entries(report.excluded).filter(([, count]) => count).map(([key, count]) => `${count} ${labels[key]}`);
    const warningsText = warnings.length ? `Resultado parcial. Registros não contabilizados: ${warnings.join("; ")}.` : "";
    const paymentCount = report.payment_pending?.length || 0;
    const paymentText = paymentCount ? ` Liquidação parcial: ${paymentCount} ${paymentCount === 1 ? "título" : "títulos"} com recebimento ou saldo não confirmado.` : "";
    const grossText = report.gross_incomplete ? ` Bruto parcial: ${report.gross_incomplete} pagamentos sem bruto confirmado; o líquido efetivo permanece contabilizado.` : "";
    this.elements.WarningText.textContent = (warningsText + paymentText + grossText).trim();
    this.elements.Warning.hidden = !this.elements.WarningText.textContent;
    const pending = [...(report.pending || []), ...(report.payment_pending || [])];
    this.elements.Validation.hidden = !pending.length;
    this.elements.Validation.querySelector("span").textContent = `Ver pendências (${pending.length})`;
    financialValidation.set("competence", pending, this.validationContext());
    if (!report.items.length) this.message("Nenhum lançamento encontrado para os filtros selecionados.");
    else this.elements.Rows.replaceChildren(...report.items.map(item => {
      const row = document.createElement("tr");
      row.className = item.direction;
      const value = amount => amount == null ? "-" : this.money.format(amount);
      const values = [item.date.split("-").reverse().join("/"), item.description, item.contact, item.category,
        value(item.gross), value(item.net), value(item.fees), value(item.received), value(item.open)];
      values.forEach((value, index) => {
        const cell = document.createElement("td");
        cell.textContent = value;
        cell.className = index >= 4 ? "money" : index === 1 ? "description" : index === 2 ? "contact" : "";
        if (index === 7 || index === 8) cell.hidden = cash;
        if (index === 1) {
          const type = document.createElement("small");
          type.textContent = item.title_type;
          cell.append(type);
        }
        row.append(cell);
      });
      const status = document.createElement("td");
      const badge = document.createElement("span");
      badge.className = "competenceStatus " + item.payment_state;
      const labels = item.direction === "income"
        ? {received: "Recebido", partial: "Parcialmente recebido", not_received: "Não recebido", pending: "Dados pendentes"}
        : {received: "Pago", partial: "Parcialmente pago", not_received: "Não pago", pending: "Dados pendentes"};
      badge.textContent = labels[item.payment_state] || "Dados pendentes";
      status.append(badge);
      row.append(status);
      return row;
    }));
    this.elements.Page.textContent = `Página ${report.page} de ${report.pages}`;
    this.elements.Previous.disabled = report.page <= 1;
    this.elements.Next.disabled = report.page >= report.pages;
  }

  async export() {
    if (!this.ready || this.busy || !this.context?.canExport) return;
    this.busy = true;
    this.elements.Export.disabled = true;
    const query = this.query();
    try {
      const response = await fetch(`/api/financial-competence/export?${query}`);
      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.error || "Não foi possível exportar.");
      }
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = url;
      link.download = `doc4docs-${query.get("clinic")}-${query.get("date_basis") === "receipt" ? "recebimentos" : "competencia"}-${query.get("date_from")}-${query.get("date_to")}.xlsx`;
      document.body.append(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 60000);
    } catch (error) {
      this.elements.WarningText.textContent = error.message;
      this.elements.Validation.hidden = true;
      this.elements.Warning.hidden = false;
    } finally {
      this.busy = false;
      this.elements.Export.disabled = !this.ready;
    }
  }
}
