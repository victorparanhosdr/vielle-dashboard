"""Read-only accrual report over synchronized Experts financial titles."""
import io
import unicodedata
from collections import defaultdict
from datetime import date
from decimal import Decimal

from financial_receipts import record, cents, day, SaleOwners, cancelled
from financial_validation import issue

SORT_KEYS = {"date", "description", "contact", "gross", "net"}
CANCELLED = {"cancelled", "canceled", "deleted", "void", "cancelado", "cancelada", "excluido", "excluida", "refunded"}
INCOME_TYPES = {"venda", "sale", "receita", "income", "receivable", "a receber"}
EXPENSE_TYPES = {"despesa", "expense", "payable", "a pagar"}


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
    if direction not in {"all", "income", "expense"} or sort not in SORT_KEYS or order not in {"asc", "desc"}:
        raise ValueError("Filtro ou ordenação inválidos.")
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
            "order": order, "page": page, **filters}


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


def build_report(conn, options, professional_uuids=(), export=False):
    professionals = set(professional_uuids)
    owners = SaleOwners(conn)

    excluded = {"date": 0, "amount": 0, "direction": 0, "professional": 0}
    pending = []
    items = []
    for uuid, raw, statuses in financial_titles(conn):
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
        items.append({"uuid": uuid, "date": competence, "date_source": date_source,
                      "description": raw.get("description") or raw.get("type") or "Título financeiro",
                      "contact": contact, "contact_id": str(person.get("uuid") or normalized(contact)),
                      "category": record(raw.get("category") or raw.get("financial_category")).get("name") or "Sem categoria",
                      "title_type": str(raw.get("type") or "Sem tipo"), "direction": direction,
                      "gross": gross, "net": net if direction == "income" else -net,
                      "emission_date": day(raw.get("emission_date")), "status": raw.get("status") or ", ".join(sorted(statuses))})

    contacts = {item["contact_id"]: item["contact"] for item in items}
    filter_options = {"contacts": [{"id": key, "name": name} for key, name in sorted(contacts.items(), key=lambda pair: normalized(pair[1]))],
                      "categories": sorted({item["category"] for item in items}, key=normalized),
                      "title_types": sorted({item["title_type"] for item in items}, key=normalized)}
    search = normalized(options.get("search"))
    filtered = [item for item in items
                if (not options.get("contact") or item["contact_id"] == options["contact"])
                and (not options.get("category") or item["category"] == options["category"])
                and (not options.get("title_type") or item["title_type"] == options["title_type"])
                and (not search or search in normalized(" ".join(str(item[key]) for key in ("description", "contact", "category", "title_type"))))]
    income = sum((item["net"] for item in filtered if item["direction"] == "income"), Decimal(0))
    expense = -sum((item["net"] for item in filtered if item["direction"] == "expense"), Decimal(0))
    income_gross = sum((item["gross"] for item in filtered if item["direction"] == "income"), Decimal(0))
    expense_gross = sum((item["gross"] for item in filtered if item["direction"] == "expense"), Decimal(0))
    direction = options.get("direction", "all")
    visible = [item for item in filtered if direction == "all" or item["direction"] == direction]
    sort = options.get("sort", "date")
    visible.sort(key=lambda item: (normalized(item[sort]) if isinstance(item[sort], str) else item[sort], item["uuid"]),
                 reverse=options.get("order", "desc") == "desc")
    count = len(visible)
    page_size = 50
    pages = max(1, (count + page_size - 1) // page_size)
    page = min(options.get("page", 1), pages)
    if not export:
        visible = visible[(page - 1) * page_size:page * page_size]
    for item in visible:
        item["gross"] = float(item["gross"] / 100)
        item["net"] = float(item["net"] / 100)
    return {"items": visible, "totals": {"income": float(income / 100), "expense": float(expense / 100),
                                           "balance": float((income - expense) / 100),
                                           "income_gross": float(income_gross / 100),
                                           "expense_gross": float(expense_gross / 100),
                                           "balance_gross": float((income_gross - expense_gross) / 100)},
            "count": count, "page": page, "pages": pages, "page_size": page_size,
            "options": filter_options, "excluded": excluded, "pending": pending,
            "basis": "Competência informada no título; quando ausente, emissão (emission_date). "
                     "Valores brutos e líquidos do título, incluindo os valores em aberto, sem repetir parcelas. "
                     "Não utiliza vencimento, criação ou pagamento como competência."}


def export_workbook(report, options, clinic_name):
    from openpyxl import Workbook
    from chart_export import add_sheet
    wb = Workbook()
    wb.remove(wb.active)
    add_sheet(wb, "Competência", ["Competência", "Descrição", "Contato", "Categoria", "Tipo de título",
                                 "Movimento", "Valor bruto (R$)", "Valor líquido (R$)", "ID título", "ID contato", "Emissão", "Status", "Campo de data"],
              [[item["date"], item["description"], item["contact"], item["category"], item["title_type"],
                "Receita" if item["direction"] == "income" else "Despesa", item["gross"], item["net"],
                item["uuid"], item["contact_id"], item["emission_date"], item["status"], item["date_source"]]
               for item in report["items"]], [7, 8])
    add_sheet(wb, "Resumo", ["Indicador", "Bruto (R$)", "Líquido (R$)"],
              [[label, report["totals"][key + "_gross"], report["totals"][key]]
               for label, key in (("Receitas", "income"), ("Despesas", "expense"), ("Total do período", "balance"))], [2, 3])
    add_sheet(wb, "Filtros e critérios", ["Campo", "Valor"], [
        ["Clínica", clinic_name], *options.items(), ["Critério", report["basis"]],
        *[["Excluídos: " + key, value] for key, value in report["excluded"].items()]])
    output = io.BytesIO()
    wb.save(output)
    return output.getvalue()
