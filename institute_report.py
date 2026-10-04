"""Conservative campaign attribution and integer-cent course reporting."""
from collections import Counter, defaultdict
from datetime import timedelta
import io
import re

from institute_sources import period

PAID = {"paid", "approved"}
STATUS_LABELS = {"paid": "Aprovado", "approved": "Aprovado", "waiting_payment": "Aguardando pagamento",
    "pending": "Pendente", "processing": "Processando", "authorized": "Autorizado",
    "refunded": "Reembolsado", "chargedback": "Chargeback", "refused": "Recusado",
    "refund_requested": "Reembolso solicitado", "pending_refund": "Reembolso pendente"}


def email(value):
    value = str(value or "").strip().lower()
    return value if re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value) else ""


def phone(value):
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) in (12, 13) and digits.startswith("55"):
        digits = digits[2:]
    return digits if len(digits) in (10, 11) else ""


def identity(row):
    return "email:" + email(row.get("email")) if email(row.get("email")) else "phone:" + phone(row.get("phone")) if phone(row.get("phone")) else "id:" + row["id"]


def match_lead(sale, leads, indexes=None):
    e, p = email(sale.get("email")), phone(sale.get("phone"))
    cutoff = sale.get("created_day") or sale.get("approved_day") or ""
    pool = leads if indexes is None else {r["id"]: r for r in [*indexes[0].get(e, []), *indexes[1].get(p, [])]}.values()
    candidates = [r for r in pool if r.get("created_day") and r["created_day"] <= cutoff
        and ((e and e == email(r.get("email"))) or (p and p == phone(r.get("phone"))
            and (not e or not email(r.get("email")) or e == email(r.get("email")))))]
    if not candidates:
        return None, "unmatched"
    exact = [r for r in candidates if e and email(r.get("email")) == e]
    candidates = exact or candidates
    candidates.sort(key=lambda r: (r["created_day"], r["id"]), reverse=True)
    latest = [r for r in candidates if r["created_day"] == candidates[0]["created_day"]]
    if len({r.get("campaign", "") for r in latest}) > 1:
        return None, "ambiguous"
    return candidates[0], "matched"


def paginate(rows, query):
    try:
        page = int(query.get("page", "1"))
        if not 1 <= page <= 100000:
            raise ValueError()
    except (TypeError, ValueError):
        raise ValueError("Página inválida.") from None
    search = query.get("search", "").strip().lower()
    if len(search) > 200:
        raise ValueError("Busca muito longa.")
    status = query.get("status", "")
    rows = [r for r in rows if (not search or search in " ".join(str(r.get(k, "")) for k in ("name", "email", "campaign", "id")).lower())
            and (not status or r.get("status") == status)]
    return {"rows": rows[(page-1)*50:page*50], "total": len(rows), "page": page, "pages": max(1, (len(rows)+49)//50)}


def build_report(store, institute, course, query, all_rows=False):
    start, end = query.get("from", ""), query.get("to", "")
    a, b = period(start, end)
    info = store.course(institute, course)
    leads = store.records(institute, course, "sheets")
    indexes = (defaultdict(list), defaultdict(list))
    for lead in leads:
        if email(lead.get("email")):
            indexes[0][email(lead["email"])].append(lead)
        if phone(lead.get("phone")):
            indexes[1][phone(lead["phone"])].append(lead)
    sales = [r for r in store.records(institute, course, "kiwify") if r["product_id"] in info["product_ids"]]
    mappings = store.campaigns(institute, course)
    aliases = {v: r["id"] for r in mappings for v in [r["id"], *r["aliases"]]}
    selected_ids = {r["id"] for r in mappings}
    campaigns = {r["id"]: {"id": r["id"], "name": r["name"], "meta": True, "leads": 0, "sales": 0,
        "gross": 0, "net": 0, "spend": 0, "buyers": set()} for r in mappings}
    daily = {}
    d = a
    while d <= b:
        daily[d.isoformat()] = {"day": d.isoformat(), "sales": 0, "gross": 0, "net": 0, "leads": 0, "spend": 0}
        d += timedelta(days=1)
    warnings = Counter()
    pending = []

    def campaign(token):
        ident = aliases.get(token, "utm:" + token if token else "unattributed")
        if ident not in campaigns:
            campaigns[ident] = {"id": ident, "name": token or "Sem campanha identificada (orgânico)", "meta": False,
                "leads": 0, "sales": 0, "gross": 0, "net": 0, "spend": 0, "buyers": set()}
        return campaigns[ident]

    unique = {}
    for lead in sorted(leads, key=lambda r: (r.get("created_day") or "9999", r["id"])):
        unique.setdefault(identity(lead), lead)
    cohort = {k: r for k, r in unique.items() if start <= r.get("created_day", "") <= end}
    for lead in cohort.values():
        campaign(lead.get("campaign", ""))["leads"] += 1
        daily[lead["created_day"]]["leads"] += 1
    warnings["lead_missing_date"] = sum(not r.get("created_day") for r in leads)
    approved, orders, converted, buyers, statuses = [], [], set(), set(), Counter()
    for sale in sales:
        status = sale.get("status", "unknown")
        event_day = sale.get("approved_day") if status in PAID else sale.get("refunded_day") or sale.get("created_day")
        if status in PAID and not event_day:
            if start <= sale.get("created_day", "") <= end:
                warnings["sale_missing_date"] += 1
                pending.append({"id": sale["id"], "name": sale.get("name"), "reason": "Pagamento aprovado sem data de aprovação"})
            continue
        if not event_day or not start <= event_day <= end:
            continue
        statuses[status] += 1
        lead, association = match_lead(sale, leads, indexes)
        token = sale.get("campaign") or (lead or {}).get("campaign") or ""
        bucket = campaign(token)
        orders.append({**sale, "status_label": STATUS_LABELS.get(status, status), "day": event_day,
                       "campaign": bucket["name"], "attribution": "UTM Kiwify" if sale.get("campaign") else "Formulário" if lead and token else "Sem atribuição"})
        if status not in PAID:
            continue
        if sale.get("currency") != "BRL" or sale.get("net_currency") != "BRL" or sale.get("gross") is None or sale.get("net") is None:
            warnings["sale_invalid_amount"] += 1
            pending.append({"id": sale["id"], "name": sale.get("name"), "reason": "Moeda diferente de BRL ou valores incompletos"})
            continue
        approved.append(sale)
        buyer = identity(sale)
        buyers.add(buyer)
        bucket["sales"] += 1
        bucket["gross"] += sale["gross"]
        bucket["net"] += sale["net"]
        bucket["buyers"].add(buyer)
        daily[event_day]["sales"] += 1
        daily[event_day]["gross"] += sale["gross"]
        daily[event_day]["net"] += sale["net"]
        if lead and identity(lead) in cohort:
            converted.add(identity(lead))
        if not token or association == "ambiguous" and not sale.get("campaign"):
            warnings["sale_attribution"] += 1
            pending.append({"id": sale["id"], "name": sale.get("name"), "reason": "Campanha ausente ou vínculo ambíguo"})
    for row in store.records(institute, course, "meta"):
        if row["campaign_id"] in selected_ids and start <= row["day"] <= end:
            campaigns[row["campaign_id"]]["spend"] += row["spend"]
            daily[row["day"]]["spend"] += row["spend"]
    sync = store.sync_state(institute, course)
    meta_state = sync.get("meta", {})
    covered = bool(selected_ids) and meta_state.get("ok") and meta_state.get("from", "9999") <= start and meta_state.get("to", "") >= end
    for row in campaigns.values():
        spend = row["spend"]
        valid = row["meta"] and covered
        row.update({"spend": spend if valid else None,
            "cpl": round(spend / row["leads"]) if valid and row["leads"] else None,
            "cac": round(spend / len(row["buyers"])) if valid and row["buyers"] else None,
            "roas": row["gross"] / spend if valid and spend else None})
        del row["buyers"]
    paid_campaigns = [r for r in campaigns.values() if r["meta"]]
    spend = sum(r["spend"] or 0 for r in paid_campaigns) if covered else None
    gross, net = sum(r["gross"] for r in approved), sum(r["net"] for r in approved)
    crm = [r for r in store.records(institute, course, "kommo") if r["pipeline_name"].strip().casefold() == info["pipeline_name"].strip().casefold()
           and start <= r.get("created_day", "") <= end]
    stages = {}
    for r in crm:
        item = stages.setdefault(r["stage"], {"name": r["stage"], "sort": r["stage_sort"], "count": 0})
        item["count"] += 1
    order_page = paginate(sorted(orders, key=lambda r: (r["day"], r["id"]), reverse=True), query)
    if all_rows:
        order_page["rows"] = [r for r in sorted(orders, key=lambda r: (r["day"], r["id"]), reverse=True)
            if (not query.get("search") or query["search"].strip().lower() in " ".join(str(r.get(k, "")) for k in ("name", "email", "campaign", "id")).lower())
            and (not query.get("status") or r["status"] == query["status"])]
    return {"ok": True, "course": info, "period": {"from": start, "to": end}, "sync": sync,
        "summary": {"sales": len(approved), "gross": gross, "net": net, "leads": len(cohort), "buyers": len(buyers),
            "conversion": len(converted)/len(cohort)*100 if cohort else None, "converted_leads": len(converted),
            "ticket": round(gross/len(approved)) if approved else None, "spend": spend,
            "roas": sum(r["gross"] for r in paid_campaigns)/spend if spend else None,
            "net_after_ads": net-spend if spend is not None else None},
        "daily": list(daily.values()), "campaigns": sorted(campaigns.values(), key=lambda r: (-r["gross"], r["name"])),
        "stages": sorted(stages.values(), key=lambda r: r["sort"]),
        "statuses": [{"key": k, "name": STATUS_LABELS.get(k, k), "count": v} for k, v in statuses.items()],
        "orders": order_page,
        "leads": paginate(sorted(cohort.values(), key=lambda r: (r["created_day"], r["id"]), reverse=True), query),
        "warnings": {k: v for k, v in warnings.items() if v}, "pending": pending[:200],
        "totals_partial": bool(warnings["sale_missing_date"] or warnings["sale_invalid_amount"]), "meta_coverage": bool(covered)}


def workbook(store, institute, course, query):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    report = build_report(store, institute, course, query, all_rows=True)
    book = Workbook()
    summary = book.active
    summary.title = "Resumo"
    summary.append(["Curso", report["course"]["name"]])
    summary.append(["De", query["from"], "Até", query["to"]])
    for field, label in (("sales", "Vendas aprovadas"), ("leads", "Leads únicos"), ("gross", "Receita bruta"), ("net", "Receita líquida"), ("spend", "Investimento Meta")):
        value = report["summary"][field]
        summary.append([label, value/100 if value is not None and field in {"gross", "net", "spend"} else value])
    summary.append(["Base", "Aprovação Kiwify; líquido não significa saldo disponível para saque."])
    sheets = [("Campanhas", ["Campanha", "Leads", "Vendas", "Bruto", "Líquido", "Investimento", "CPL", "CAC", "ROAS"],
        [[r["name"], r["leads"], r["sales"], *[r[k]/100 if r[k] is not None else None for k in ("gross", "net", "spend", "cpl", "cac")], r["roas"]] for r in report["campaigns"]]),
        ("Dia a dia", ["Data", "Leads", "Vendas", "Bruto", "Líquido", "Investimento"],
        [[r["day"], r["leads"], r["sales"], r["gross"]/100, r["net"]/100, r["spend"]/100 if report["meta_coverage"] else None] for r in report["daily"]])]
    all_orders = report["orders"]["rows"]
    sheets.append(("Pedidos", ["ID", "Data", "Aluno", "E-mail", "Status", "Campanha", "Bruto", "Líquido", "Moeda"],
        [[r["id"], r["day"], r["name"], r["email"], r["status_label"], r["campaign"],
          r["gross"]/100 if r["gross"] is not None else None, r["net"]/100 if r["net"] is not None else None, r["currency"]] for r in all_orders]))
    for title, headers, rows in sheets:
        ws = book.create_sheet(title)
        ws.append(headers)
        for row in rows:
            ws.append(row)
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
    for ws in book:
        for row in ws:
            for cell in row:
                if isinstance(cell.value, str) and cell.value.startswith(("=", "+", "-", "@")):
                    cell.data_type = "s"
        for cell in ws[1]:
            cell.font = Font(color="FFFFFF", bold=True)
            cell.fill = PatternFill("solid", fgColor="123B30")
        for col in ws.columns:
            ws.column_dimensions[col[0].column_letter].width = min(50, max(15, max(len(str(c.value or "")) for c in col)+2))
    output = io.BytesIO()
    book.save(output)
    return output.getvalue()
