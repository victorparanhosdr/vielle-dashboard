"""Separate accrual titles, actual payments and outstanding due-date forecasts."""
from collections import defaultdict
from datetime import date
from decimal import Decimal

from financial_competence import build_report, financial_titles, title_direction
from financial_receipts import (CANCELLED, SETTLED, SaleOwners, cancelled, cents, day,
                                net_value, parcel_records, record, receipt_date)
from financial_validation import issue


def build_expenses(conn, date_from, date_to, professional_uuids=(), export=False):
    options = dict(date_from=date_from, date_to=date_to, direction="expense", sort="date", order="desc", page=1)
    competence = build_report(conn, options, professional_uuids, export=True)
    categories = defaultdict(lambda: {"total": 0, "amount": 0})
    daily = defaultdict(float)
    details = []
    source_titles = {uuid: raw for uuid, raw, _ in financial_titles(conn)} if export else {}
    for item in competence["items"]:
        category = categories[item["category"]]
        category["total"] += 1
        category["amount"] += item["gross"]
        daily[item["date"]] += item["gross"]
        details.append({"uuid": item["uuid"], "date": item["date"], "date_source": item["date_source"],
            "description": item["description"], "direction": "saida", "detail": item["category"],
            "amount": item["gross"], "net": abs(item["net"]), "status": item["status"],
            "emission_date": item["emission_date"], "source": "clinica_bills"})
        if export:
            details[-1]["raw_json"] = source_titles.get(item["uuid"], {})
    professionals = set(professional_uuids)
    owners = SaleOwners(conn)
    totals = {"paid_gross": Decimal(0), "paid_net": Decimal(0), "planned_gross": Decimal(0), "planned_net": Decimal(0)}
    pending = []
    for uuid, row, bill, parent, raw, status, kind in parcel_records(conn):
        if status in CANCELLED or cancelled(raw) or cancelled(parent):
            continue
        source = {**parent, "type": kind}
        direction = title_direction(source, {status})
        if direction != "expense":
            continue
        owner, _ = owners.resolve(parent, raw, income=False)
        if not owner and record(parent.get("person")).get("uuid") in professionals:
            owner = parent["person"]["uuid"]
        if professionals and owner and owner not in professionals:
            continue
        paid = status in SETTLED
        if paid:
            when, date_source = receipt_date(raw, row.get("paid_at"))
        elif status in {"open", "late", "pending", "unpaid", "partial", "partially_paid"}:
            when, date_source = day(raw.get("due_date") or row.get("due_date")), "due_date"
        else:
            continue
        detail = dict(bill=parent, source="parcel", title_id=bill.get("uuid") or row.get("bill_uuid"),
            date=when, date_source=date_source, status=status,
            gross=raw.get("final_amount"), net=net_value(raw))
        if not when:
            pending.append(issue(uuid, "date", raw, **detail))
            continue
        if not date_from <= when <= date_to or (paid and when > date.today().isoformat()):
            continue
        if professionals and not owner:
            pending.append(issue(uuid, "professional", raw, **detail))
            continue
        gross, net = cents(raw.get("final_amount")), net_value(raw)
        if gross is None or gross < 0 or net is None:
            pending.append(issue(uuid, "amount", raw, **detail))
            continue
        if not paid:
            balance = cents(raw.get("balance"))
            if balance is None and status not in {"open", "late", "pending", "unpaid"}:
                pending.append(issue(uuid, "amount", raw, **detail))
                continue
            if balance is not None:
                if balance < 0 or balance > gross:
                    pending.append(issue(uuid, "amount", raw, **detail))
                    continue
                # A remaining balance is already a net monetary amount; do not deduct fees twice.
                gross = net = balance
        prefix = "paid" if paid else "planned"
        totals[prefix + "_gross"] += gross
        totals[prefix + "_net"] += net
    return {"competence_gross": competence["totals"]["expense_gross"],
        "competence_net": competence["totals"]["expense"],
        **{key: float(value / 100) for key, value in totals.items()},
        "categories": [{"category": key, **value} for key, value in sorted(categories.items(), key=lambda pair: -pair[1]["amount"])],
        "daily": [{"day": key, "total": value} for key, value in sorted(daily.items())],
        "details": details, "pending": pending,
        "basis": {"competence": "Título completo · competência informada ou emissão · valor bruto",
                  "paid": "Parcelas pagas · compensação ou pagamento efetivo · valor líquido",
                  "planned": "Saldo das parcelas em aberto · vencimento · valor a pagar"}}
