class FinancialValidation {
  constructor() {
    this.dialog = document.getElementById("financialValidationDialog");
    this.elements = Object.fromEntries(["Title", "Context", "Search", "Reason", "Count", "Rows", "Page", "Previous", "Next"].map(key => [key, document.getElementById("financialValidation" + key)]));
    this.datasets = new Map();
    this.money = new Intl.NumberFormat("pt-BR", {style: "currency", currency: "BRL"});
    this.reasons = {
      date: {label: "Data não identificada", action: "Conferir a data registrada no Clínica Experts. Sem uma data válida, o período deste registro não pode ser confirmado."},
      professional: {label: "Profissional não confirmado", action: "Conferir o profissional responsável e o vínculo entre a venda e o título no Clínica Experts."},
      amount: {label: "Valores incompletos", action: "Conferir o valor bruto, o valor líquido e as taxas do título no Clínica Experts."},
      net_amount: {label: "Valor líquido não confirmado", action: "Conferir o valor líquido recebido ou o valor bruto e as taxas da parcela no Clínica Experts."},
      direction: {label: "Movimento não identificado", action: "Conferir se o título representa uma receita ou uma despesa e revisar seu tipo no Clínica Experts."},
    };
    document.addEventListener("click", event => {
      const trigger = event.target.closest("[data-financial-validation]");
      if (trigger) this.open(trigger.dataset.financialValidation);
    });
    this.dialog.querySelector("[data-validation-close]").addEventListener("click", () => this.dialog.close());
    this.dialog.addEventListener("close", () => { this.source = null; });
    this.dialog.addEventListener("click", event => {
      if (event.target !== this.dialog) return;
      const rect = this.dialog.getBoundingClientRect();
      if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) this.dialog.close();
    });
    this.elements.Search.addEventListener("input", () => { this.page = 1; this.render(); });
    this.elements.Reason.addEventListener("change", () => { this.page = 1; this.render(); });
    this.elements.Previous.addEventListener("click", () => { this.page--; this.render(); });
    this.elements.Next.addEventListener("click", () => { this.page++; this.render(); });
    this.dialog.addEventListener("click", async event => {
      const button = event.target.closest("[data-validation-copy]");
      if (!button) return;
      try {
        await navigator.clipboard.writeText(button.dataset.validationCopy);
        button.title = "Identificação copiada";
        button.setAttribute("aria-label", "Identificação copiada");
      } catch {
        button.title = "Não foi possível copiar a identificação";
      }
    });
  }

  set(source, pending, context) {
    const previous = this.datasets.get(source);
    this.datasets.set(source, {pending: pending || [], context});
    if (this.source !== source) return;
    if (JSON.stringify(previous?.context) !== JSON.stringify(context)) this.dialog.close();
    else this.render();
  }

  clear(source) {
    this.datasets.delete(source);
    if (this.source === source) this.dialog.close();
  }

  prepare(source, context) {
    if (JSON.stringify(this.datasets.get(source)?.context) !== JSON.stringify(context)) this.clear(source);
  }

  open(source) {
    const data = this.datasets.get(source);
    if (!data) return;
    this.source = source;
    this.page = 1;
    this.elements.Search.value = "";
    this.elements.Reason.replaceChildren(new Option("Todos os motivos", ""), ...Object.keys(this.reasons)
      .filter(reason => data.pending.some(item => item.reason === reason))
      .map(reason => new Option(this.reasons[reason].label, reason)));
    this.elements.Title.textContent = source === "receipts" ? "Validação de recebimentos" : source === "expenses" ? "Validação de despesas" : "Validação por competência";
    this.elements.Context.textContent = [data.context.clinic, `${this.day(data.context.from)} a ${this.day(data.context.to)}`, data.context.doctor || "Todos os profissionais"].join(" · ");
    this.render();
    if (!this.dialog.open) this.dialog.showModal();
  }

  day(value) {
    return /^\d{4}-\d{2}-\d{2}$/.test(value || "") ? value.split("-").reverse().join("/") : "Não identificada";
  }

  normalized(value) {
    return String(value || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLocaleLowerCase("pt-BR");
  }

  render() {
    const data = this.datasets.get(this.source);
    if (!data) return;
    const search = this.normalized(this.elements.Search.value);
    const reason = this.elements.Reason.value;
    const items = data.pending.filter(item => (!reason || item.reason === reason)
      && (!search || this.normalized([item.description, item.contact, item.id, item.title_id].join(" ")).includes(search)));
    const pages = Math.max(1, Math.ceil(items.length / 25));
    this.page = Math.min(this.page, pages);
    this.elements.Count.textContent = `${items.length} ${items.length === 1 ? "registro não contabilizado" : "registros não contabilizados"}`;
    const visible = items.slice((this.page - 1) * 25, this.page * 25);
    this.elements.Rows.replaceChildren(...visible.map(item => this.row(item)));
    if (!visible.length) {
      const empty = document.createElement("p");
      empty.className = "validationEmpty";
      empty.textContent = "Nenhuma pendência encontrada para estes filtros.";
      this.elements.Rows.append(empty);
    }
    this.elements.Page.textContent = `Página ${this.page} de ${pages}`;
    this.elements.Previous.disabled = this.page <= 1;
    this.elements.Next.disabled = this.page >= pages;
    this.dialog.querySelectorAll("[data-lucide]").forEach(element => {
      const name = element.dataset.lucide.split("-").map(part => part[0].toUpperCase() + part.slice(1)).join("");
      if (window.lucide?.icons[name]) element.replaceWith(lucide.createElement(lucide.icons[name], {"aria-hidden": "true"}));
    });
  }

  row(item) {
    const row = document.createElement("article");
    row.className = "validationRecord";
    const header = document.createElement("header");
    const title = document.createElement("h3");
    title.textContent = item.description || "Registro financeiro sem descrição";
    const badge = document.createElement("span");
    badge.className = "validationBadge";
    badge.textContent = "Fora do total";
    header.append(title, badge);
    const contact = document.createElement("p");
    contact.className = "validationContact";
    contact.textContent = item.contact || "Contato não informado";
    const fields = document.createElement("dl");
    const dateLabel = item.date_source === "due_date" ? "Vencimento" :
      item.source === "parcel" ? "Pagamento / recebimento" : "Competência / emissão";
    const values = [[dateLabel, this.day(item.date)],
      ["Bruto disponível", item.gross == null ? "Não informado" : this.money.format(item.gross)],
      ["Líquido disponível", item.net == null ? "Não informado" : this.money.format(item.net)],
      ["Status na origem", item.status || "Não informado"]];
    values.forEach(([label, value]) => {
      const field = document.createElement("div");
      const term = document.createElement("dt");
      const description = document.createElement("dd");
      term.textContent = label;
      description.textContent = value;
      field.append(term, description);
      fields.append(field);
    });
    const problem = document.createElement("div");
    problem.className = "validationProblem";
    const label = document.createElement("strong");
    const action = document.createElement("p");
    label.textContent = this.reasons[item.reason]?.label || "Informação pendente";
    action.textContent = this.reasons[item.reason]?.action || "Conferir os dados deste registro no Clínica Experts.";
    problem.append(label, action);
    const footer = document.createElement("footer");
    const ids = document.createElement("div");
    const id = document.createElement("code");
    id.textContent = `${item.source === "parcel" ? "Parcela" : "Título"}: ${item.id}`;
    ids.append(id);
    if (item.source === "parcel" && item.title_id && item.title_id !== item.id) {
      const parent = document.createElement("code");
      parent.textContent = `Título: ${item.title_id}`;
      ids.append(parent);
    }
    const copy = document.createElement("button");
    copy.type = "button";
    copy.className = "validationIconButton";
    copy.title = "Copiar identificação";
    copy.setAttribute("aria-label", "Copiar identificação");
    copy.dataset.validationCopy = item.id;
    const icon = document.createElement("i");
    icon.dataset.lucide = "copy";
    copy.append(icon);
    footer.append(ids, copy);
    row.append(header, contact, fields, problem, footer);
    return row;
  }
}

const financialValidation = new FinancialValidation();
