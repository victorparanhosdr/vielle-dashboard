"""Read-only accrual report over synchronized Experts financial titles."""
import io
import unicodedata
from collections import defaultdict
from datetime import date
from decimal import Decimal

from financial_receipts import (record, cents, day, SaleOwners, cancelled, SETTLED,
                                parcel_records, receipt_date, net_value, build_receipts, payment_item)
from financial_validation import issue

SORT_KEYS = {"date", "description", "contact", "gross", "net"}
CANCELLED = {"cancelled", "canceled", "deleted", "void", "cancelado", "cancelada", "excluido", "excluida", "refunded"}
INCOME_TYPES = {"venda", "sale", "receita", "income", "receivable", "a receber"}
EXPENSE_TYPES = {"despesa", "expense", "payable", "a pagar"}
OPEN = {"open", "late", "pending", "unpaid", "partial", "partially_paid"}
PAYMENT_LABELS = {"received": "Recebido / pago", "partial": "Parcialmente recebido / pago",
                  "not_received": "Não recebido / não pago", "pending": "Dados pendentes"}


def normalized(value):
    return "".join(char for char in unicodedata.normalize("NFKD", str(value or ""))
                   if not unicodedata.combining(char)).casefold().strip()


def query_options(params):
    def value(key, default=""):
        values = params.get(key, [default])
        if len(values) != 1:
            raise ValueError("Informe apenas um valor por filtro.")
        return values[0]

    today = date.today()
    start = value("date_from", today.replace(day=1).isoformat())
    end = value("date_to", today.isoformat())
    if day(start) != start or day(end) != end or start > end:
        raise ValueError("Informe datas válidas; a data inicial não pode ser posterior à final.")
    direction = value("direction", "all")
    sort = value("sort", "date")
    order = value("order", "desc")
    date_basis = value("date_basis", "competence")
    payment_status = value("payment_status", "all")
    if direction not in {"all", "income", "expense"} or sort not in SORT_KEYS or order not in {"asc", "desc"}:
        raise ValueError("Filtro ou ordenação inválidos.")
    if date_basis not in {"competence", "receipt"} or payment_status not in {"all", *PAYMENT_LABELS}:
        raise ValueError("Base de data ou situação inválida.")
    try:
        page = int(value("page", "1"))
    except ValueError as exc:
        raise ValueError("Página inválida.") from exc
    if page < 1:
        raise ValueError("Página inválida.")
    filters = {key: value(key) for key in ("search", "contact", "category", "title_type", "doctor")}
    if any(len(text) > 500 for text in filters.values()):
        raise ValueError("Filtro muito longo.")
    return {"date_from": start, "date_to": end, "direction": direction, "sort": sort,
            "order": order, "page": page, "date_basis": date_basis,
            "payment_status": payment_status, **filters}


def competence_day(raw):
    # Do not substitute creation, due date or payment for a title's accrual date.
    for key in ("competence_date", "competency_date", "accrual_date", "emission_date"):
        if raw.get(key):
            return day(raw[key]), key
    return None, None


def title_direction(raw, statuses):
    kind = normalized(raw.get("type"))
    if kind in INCOME_TYPES:
        return "income"
    if kind in EXPENSE_TYPES:
        return "expense"
    for key in ("direction", "nature"):
        direction = normalized(raw.get(key))
        if direction in {"income", "in", "credit", "receivable", "receita", "entrada"}:
            return "income"
        if direction in {"expense", "out", "debit", "payable", "despesa", "saida"}:
            return "expense"
    if kind == "conta":
        description = normalized(raw.get("description"))
        if "a receber" in description:
            return "income"
        if "a pagar" in description:
            return "expense"
        # Experts uses Conta for payable titles and also manually received income.
        if "received" in statuses:
            return None if "paid" in statuses else "income"
        if "receita" in normalized(record(raw.get("category")).get("name")):
            return "income"
        return "expense"
    return None


def financial_titles(conn):
    bills = {row["uuid"]: dict(row) for row in conn.execute(
        "select uuid, raw_json, synced_at from clinica_bills")}
    parcels = defaultdict(dict)
    for row in conn.execute("select uuid, bill_uuid, status, raw_json, synced_at from clinica_parcels"):
        raw = record(row["raw_json"])
        bill = record(raw.get("raw_bill"))
        owner = row["bill_uuid"] or bill.get("uuid") or record(raw.get("bill")).get("uuid")
        if not owner:
            continue
        if owner not in bills and bill.get("uuid"):
            bills[owner] = {"uuid": owner, "raw_json": bill, "synced_at": row["synced_at"]}
        parcels[owner][row["uuid"]] = (row["synced_at"] or 0, row["status"] or raw.get("status"))
    for uuid, row in bills.items():
        raw = record(row["raw_json"])
        statuses = {}
        for method in raw.get("payment_methods") or []:
            for parcel in record(method).get("parcels") or []:
                parcel = record(parcel)
                statuses[parcel.get("uuid")] = parcel.get("status")
        for parcel_id, (synced_at, status) in parcels[uuid].items():
            if synced_at >= (row["synced_at"] or 0) or parcel_id not in statuses:
                statuses[parcel_id] = status
        yield uuid, raw, {normalized(value) for value in statuses.values() if value}
    for uuid in parcels.keys() - bills.keys():
        # A parcel without its parent cannot establish the title's accrual date.
        yield uuid, {}, set()


def expense_payments(records, date_from, date_to, professionals, owners):
    items, pending = [], []
    for uuid, row, bill, parent, raw, status, kind in records:
        if status not in SETTLED or cancelled(raw) or cancelled(parent):
            continue
        if title_direction({**parent, "type": kind}, {status}) != "expense":
            continue
        owner, _ = owners.resolve(parent, raw, income=False)
        if not owner and record(parent.get("person")).get("uuid") in professionals:
            owner = parent["person"]["uuid"]
        if professionals and owner and owner not in professionals:
            continue
        when, source = receipt_date(raw, row.get("paid_at"))
        detail = dict(bill=parent, source="parcel", title_id=row.get("bill_uuid") or bill.get("uuid"),
                      date=when, date_source=source, status=status,
                      gross=raw.get("final_amount"), net=net_value(raw))
        reason = "date" if not when else None
        if when and (not date_from <= when <= date_to or when > date.today().isoformat()):
            continue
        if not reason and professionals and not owner:
            reason = "professional"
        gross, net = cents(raw.get("final_amount")), net_value(raw)
        if not reason and (gross is None or gross < 0 or net is None):
            reason = "amount"
        if reason:
            pending.append(issue(uuid, reason, raw, **detail))
            continue
        items.append(payment_item(uuid, row, bill, parent, raw, status, kind, when, source, net, "expense"))
    return items, pending


class PaymentLedger:
    def __init__(self, conn, professionals, owners):
        self.records = list(parcel_records(conn))
        self.parcels = defaultdict(list)
        for entry in self.records:
            uuid, row, bill, parent, *_ = entry
            title_id = row.get("bill_uuid") or bill.get("uuid") or parent.get("uuid") or uuid
            self.parcels[title_id].append(entry)
        received = build_receipts(conn, "0001-01-01", date.today().isoformat(), professionals, include_items=True)
        paid, _ = expense_payments(self.records, "0001-01-01", date.today().isoformat(), professionals, owners)
        self.cash = {item["uuid"]: item for item in received["items"] + paid}

    def progress(self, uuid, raw, direction):
        gross = cents(raw.get("final_amount"))
        received, received_gross, outstanding, parcel_total = Decimal(0), Decimal(0), Decimal(0), Decimal(0)
        uncertain = False
        active = [entry for entry in self.parcels[uuid] if not cancelled(entry[4]) and entry[5] not in CANCELLED]
        if not active:
            if gross is not None and normalized(raw.get("status")) in {"open", "late", "pending", "unpaid"}:
                outstanding = gross
            else:
                uncertain = True
        for parcel_id, row, _, _, parcel, status, _ in active:
            amount = cents(parcel.get("final_amount"))
            if amount is None:
                net, fees = net_value(parcel), cents(parcel.get("fees_amount"))
                if net is not None and fees is not None:
                    amount = net + fees
            if amount is None or amount < 0:
                uncertain = True
                continue
            parcel_total += amount
            cash = self.cash.get(parcel_id)
            if cash and cash["direction"] == direction:
                received += Decimal(str(cash["net"])) * 100
                received_gross += amount
                continue
            when, _ = receipt_date(parcel, row.get("paid_at"))
            if status in OPEN or (status in SETTLED and when and when > date.today().isoformat()):
                balance = cents(parcel.get("balance"))
                if balance is None and status in {"partial", "partially_paid"}:
                    uncertain = True
                elif balance is not None and not 0 <= balance <= amount:
                    uncertain = True
                else:
                    outstanding += balance if balance is not None else amount
                    if balance is not None and balance != amount:
                        # A balance does not establish when/how the other part was received.
                        uncertain = True
            else:
                uncertain = True
        if gross is None or (active and parcel_total != gross):
            uncertain = True
        state = "pending" if uncertain else "partial" if received_gross and outstanding else "received" if received_gross or gross == 0 else "not_received"
        return {"received": received, "received_gross": received_gross,
                "open": outstanding if not uncertain else None,
                "payment_state": state, "payment_complete": not uncertain}


def build_report(conn, options, professional_uuids=(), export=False, *, include_settlement=True):
    professionals = set(professional_uuids)
    owners = SaleOwners(conn)
    cash_basis = options.get("date_basis", "competence") == "receipt"
    ledger = PaymentLedger(conn, professionals, owners) if include_settlement or cash_basis else None

    excluded = {"date": 0, "amount": 0, "direction": 0, "professional": 0}
    pending = []
    payment_pending = []
    items = []
    title_records = list(financial_titles(conn))
    for uuid, raw, statuses in (() if cash_basis else title_records):
        if cancelled(raw) or (statuses and statuses <= CANCELLED):
            continue
        if normalized(raw.get("type")) in {"saldo inicial", "initial balance"}:
            continue
        explicit_owner = (record(raw.get("seller")).get("uuid")
                          or record(raw.get("professional")).get("uuid")
                          or raw.get("professional_uuid"))
        if professionals and explicit_owner and explicit_owner not in professionals:
            continue
        competence, date_source = competence_day(raw)
        detail = dict(date=competence, date_source=date_source,
                      status=raw.get("status") or ", ".join(sorted(statuses)),
                      gross=raw.get("final_amount"), net=raw.get("net_amount"))
        if not competence:
            excluded["date"] += 1
            pending.append(issue(uuid, "date", raw, **detail))
            continue
        if not options["date_from"] <= competence <= options["date_to"]:
            continue
        direction = title_direction(raw, statuses)
        if not direction:
            excluded["direction"] += 1
            pending.append(issue(uuid, "direction", raw, **detail))
            continue
        person = record(raw.get("person"))
        if professionals:
            owner, _ = owners.resolve(raw, income=direction == "income", bill_id=uuid)
            if not owner and direction == "expense" and person.get("uuid") in professionals:
                owner = person["uuid"]
            if not owner:
                excluded["professional"] += 1
                pending.append(issue(uuid, "professional", raw, **detail))
                continue
            if owner not in professionals:
                continue
        gross = cents(raw.get("final_amount"))
        net = cents(raw.get("net_amount"))
        fees = cents(raw.get("fees_amount"))
        if net is None and gross is not None and fees is not None:
            net = gross - fees
        if gross is None or net is None or gross < 0 or net < 0:
            excluded["amount"] += 1
            pending.append(issue(uuid, "amount", raw, **detail))
            continue
        contact = person.get("name") or person.get("full_name") or "Sem contato"
        progress = ledger.progress(uuid, raw, direction) if ledger else {}
        if ledger and not progress["payment_complete"]:
            problem = issue(uuid, "payment", raw, **detail)
            problem["scope"] = "settlement"
            payment_pending.append(problem)
        items.append({"uuid": uuid, "date": competence, "date_source": date_source,
                      "description": raw.get("description") or raw.get("type") or "Título financeiro",
                      "contact": contact, "contact_id": str(person.get("uuid") or normalized(contact)),
                      "category": record(raw.get("category") or raw.get("financial_category")).get("name") or "Sem categoria",
                      "title_type": str(raw.get("type") or "Sem tipo"), "direction": direction,
                      "gross": gross, "net": net if direction == "income" else -net,
                      "fees": gross - net if gross >= net else None, **progress,
                      "emission_date": day(raw.get("emission_date")), "status": raw.get("status") or ", ".join(sorted(statuses))})

    if cash_basis:
        received = build_receipts(conn, options["date_from"], options["date_to"], professionals, include_items=True)
        paid, expense_pending = expense_payments(ledger.records, options["date_from"], options["date_to"], professionals, owners)
        pending = received["pending"] + expense_pending
        excluded = {key: sum(item["reason"] == key for item in pending)
                    for key in ("date", "amount", "net_amount", "professional")}
        parents = {uuid: raw for uuid, raw, _ in title_records}
        for payment in received["items"] + paid:
            raw = parents.get(payment["title_id"], {})
            progress = ledger.progress(payment["title_id"], raw, payment["direction"])
            item = {**payment, "payment_state": progress["payment_state"], "payment_complete": True,
                    "received": Decimal(str(payment["net"])) * 100,
                    "received_gross": Decimal(str(payment["gross"])) * 100 if payment["gross"] is not None else None,
                    "open": None}
            for key in ("gross", "net", "fees"):
                item[key] = Decimal(str(payment[key])) * 100 if payment[key] is not None else None
            if item["direction"] == "expense":
                item["net"] = -item["net"]
            items.append(item)

    contacts = {item["contact_id"]: item["contact"] for item in items}
    filter_options = {"contacts": [{"id": key, "name": name} for key, name in sorted(contacts.items(), key=lambda pair: normalized(pair[1]))],
                      "categories": sorted({item["category"] for item in items}, key=normalized),
                      "title_types": sorted({item["title_type"] for item in items}, key=normalized)}
    search = normalized(options.get("search"))
    filtered = [item for item in items
                if (not options.get("contact") or item["contact_id"] == options["contact"])
                and (not options.get("category") or item["category"] == options["category"])
                and (not options.get("title_type") or item["title_type"] == options["title_type"])
                and (options.get("payment_status", "all") == "all" or item.get("payment_state") == options["payment_status"])
                and (not search or search in normalized(" ".join(str(item[key]) for key in ("description", "contact", "category", "title_type"))))]
    income = sum((item["net"] for item in filtered if item["direction"] == "income"), Decimal(0))
    expense = -sum((item["net"] for item in filtered if item["direction"] == "expense"), Decimal(0))
    income_gross = sum((item["gross"] or Decimal(0) for item in filtered if item["direction"] == "income"), Decimal(0))
    expense_gross = sum((item["gross"] or Decimal(0) for item in filtered if item["direction"] == "expense"), Decimal(0))
    settlement = {key: Decimal(0) for key in ("income_received", "expense_paid", "income_open", "expense_open", "fees")}
    for item in filtered:
        prefix = "income" if item["direction"] == "income" else "expense"
        settlement[prefix + ("_received" if prefix == "income" else "_paid")] += item.get("received") or Decimal(0)
        settlement[prefix + "_open"] += item.get("open") or Decimal(0)
        settlement["fees"] += item.get("fees") or Decimal(0)
    settlement["balance_received"] = settlement["income_received"] - settlement["expense_paid"]
    visible_ids = {item["uuid"] for item in filtered}
    payment_pending = [item for item in payment_pending if item["id"] in visible_ids]
    direction = options.get("direction", "all")
    visible = [item for item in filtered if direction == "all" or item["direction"] == direction]
    sort = options.get("sort", "date")
    visible.sort(key=lambda item: (item[sort] is not None, normalized(item[sort]) if isinstance(item[sort], str) else item[sort] or 0, item["uuid"]),
                 reverse=options.get("order", "desc") == "desc")
    count = len(visible)
    page_size = 50
    pages = max(1, (count + page_size - 1) // page_size)
    page = min(options.get("page", 1), pages)
    if not export:
        visible = visible[(page - 1) * page_size:page * page_size]
    for item in visible:
        for key in ("gross", "net", "fees", "received", "received_gross", "open"):
            item[key] = float(item[key] / 100) if item.get(key) is not None else None
    return {"items": visible, "totals": {"income": float(income / 100), "expense": float(expense / 100),
                                           "balance": float((income - expense) / 100),
                                           "income_gross": float(income_gross / 100),
                                           "expense_gross": float(expense_gross / 100),
                                           "balance_gross": float((income_gross - expense_gross) / 100)},
            "count": count, "page": page, "pages": pages, "page_size": page_size,
            "options": filter_options, "excluded": excluded, "pending": pending,
            "payment_pending": payment_pending, "date_basis": "receipt" if cash_basis else "competence",
            "settlement_totals": {key: float(value / 100) for key, value in settlement.items()},
            "gross_incomplete": sum(item["gross"] is None for item in filtered),
            "basis": ("Pagamentos efetivos no período, pela compensação ou recebimento registrado; "
                      "não inclui valores em aberto nem compensações futuras. Receitas usam o mesmo cálculo do Total recebido."
                      if cash_basis else "Competência informada no título; quando ausente, emissão (emission_date). "
                     "Valores brutos e líquidos do título, incluindo os valores em aberto, sem repetir parcelas. "
                     "Liquidação confirmada até hoje, independentemente do mês do pagamento. "
                     "Não utiliza vencimento, criação ou pagamento como competência.")}


def export_workbook(report, options, clinic_name):
    from openpyxl import Workbook
    from chart_export import add_sheet
    wb = Workbook()
    wb.remove(wb.active)
    cash_basis = report.get("date_basis") == "receipt"
    add_sheet(wb, "Recebimentos" if cash_basis else "Competência", ["Recebimento / pagamento" if cash_basis else "Competência", "Descrição", "Contato", "Categoria", "Tipo de título",
                                 "Movimento", "Valor bruto (R$)", "Valor líquido (R$)", "ID registro", "ID contato", "Emissão", "Status na origem", "Campo de data",
                                 "Taxas (R$)", "Recebido / pago líquido (R$)", "Em aberto bruto (R$)", "Situação do título", "ID título"],
              [[item["date"], item["description"], item["contact"], item["category"], item["title_type"],
                "Receita" if item["direction"] == "income" else "Despesa", item["gross"], item["net"],
                item["uuid"], item["contact_id"], item["emission_date"], item["status"], item["date_source"],
                item.get("fees"), item.get("received"), item.get("open"), PAYMENT_LABELS.get(item.get("payment_state")), item.get("title_id", item["uuid"])]
               for item in report["items"]], [7, 8, 14, 15, 16])
    add_sheet(wb, "Resumo", ["Indicador", "Bruto (R$)", "Líquido (R$)"],
              [[label, report["totals"][key + "_gross"], report["totals"][key]]
               for label, key in (("Receitas", "income"), ("Despesas", "expense"), ("Total do período", "balance"))]
              + [["Recebido", None, report["settlement_totals"]["income_received"]],
                 ["Pago", None, report["settlement_totals"]["expense_paid"]],
                 ["Receitas em aberto", report["settlement_totals"]["income_open"], None],
                 ["Despesas em aberto", report["settlement_totals"]["expense_open"], None]], [2, 3])
    add_sheet(wb, "Filtros e critérios", ["Campo", "Valor"], [
        ["Clínica", clinic_name], *options.items(), ["Critério", report["basis"]],
        *[["Excluídos: " + key, value] for key, value in report["excluded"].items()]])
    output = io.BytesIO()
    wb.save(output)
    return output.getvalue()
