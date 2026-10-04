"""Read-only net receipts from Experts parcels, separate from sale-date totals."""
import json
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
import unicodedata

from financial_validation import issue


SETTLED = {"received", "paid", "settled", "done", "pago", "paga", "quitado",
           "quitada", "liquidado", "liquidada"}
INCOME = {"venda", "sale", "receita"}
CANCELLED = {"cancelled", "canceled", "deleted", "void", "cancelado", "cancelada", "excluido", "excluida", "refunded"}


def normalized(value):
    return "".join(c for c in unicodedata.normalize("NFKD", str(value or ""))
                   if not unicodedata.combining(c)).casefold().strip()


def cancelled(raw):
    return normalized(raw.get("status")) in CANCELLED or raw.get("deleted") is True or bool(raw.get("deleted_at"))


def record(value):
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except (ValueError, TypeError):
        return {}


def cents(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
        return number if number.is_finite() else None
    except InvalidOperation:
        return None


def day(value):
    try:
        return date.fromisoformat(str(value or "")[:10]).isoformat()
    except ValueError:
        return None


def receipt_date(raw, paid_at=None):
    # Compensation is cash entry; execution may be the earlier card transaction.
    for key in ("compensation_date", "liquidation_date", "settlement_date", "settled_at",
                "received_at", "paid_at", "payment_date", "payment_at", "paid_on", "paid_date", "execution_date"):
        if raw.get(key):
            actual = day(raw[key])
            if actual:
                return actual, key
    if raw.get("calc_compensation_date"):
        return None, None
    actual = day(paid_at)
    return actual, "paid_at" if actual else None


def receipt_day(raw, paid_at=None):
    return receipt_date(raw, paid_at)[0]


def net_value(raw):
    value = cents(raw.get("net_amount"))
    if value is None:
        gross, fees = cents(raw.get("final_amount")), cents(raw.get("fees_amount"))
        if gross is not None and fees is not None:
            value = gross - fees
    return value if value is not None and value >= 0 else None


def parcel_records(conn):
    """Reconcile the same parcel UUID across flat and embedded API snapshots."""
    bills = {}
    embedded = {}
    for row in conn.execute("select uuid, type, emission_date, raw_json, synced_at from clinica_bills"):
        bill = dict(row)
        raw = record(row["raw_json"])
        bill["raw"] = raw
        bills[row["uuid"]] = bill
        for method in raw.get("payment_methods") or []:
            for parcel in record(method).get("parcels") or []:
                parcel = record(parcel)
                if parcel.get("uuid"):
                    embedded[parcel["uuid"]] = (bill, parcel)

    flat = {row["uuid"]: dict(row) for row in conn.execute("select * from clinica_parcels")}
    for uuid in sorted(embedded.keys() | flat.keys()):
        row = flat.get(uuid, {})
        bill, nested = embedded.get(uuid, (bills.get(row.get("bill_uuid")), {}))
        bill = bill or {}
        raw = record(row.get("raw_json"))
        if bill.get("synced_at", 0) > row.get("synced_at", 0):
            merged = {**raw, **nested}
            status = nested.get("status") or row.get("status")
        else:
            merged = {**nested, **raw}
            status = row.get("status") or nested.get("status")
        owner_raw = record(merged.get("raw_bill"))
        bill_raw = bill.get("raw") or owner_raw
        if owner_raw and (row.get("synced_at") or 0) > (bill.get("synced_at") or 0):
            bill_raw = {**bill_raw, **owner_raw}
        kind = normalized(bill_raw.get("type") or bill.get("type") or row.get("type") or merged.get("type"))
        yield uuid, row, bill, bill_raw, merged, normalized(status), kind


class SaleOwners:
    def __init__(self, conn):
        self.exact = defaultdict(set)
        self.by_sale = defaultdict(set)
        self.by_bill = defaultdict(set)
        for row in conn.execute("select * from clinica_sales where type = 'sale'"):
            row = dict(row)
            raw = record(row.get("raw_json"))
            if cancelled(raw) or normalized(raw.get("status")) == "inactive":
                continue
            seller = record(raw.get("seller")).get("uuid")
            buyer = record(raw.get("buyer")).get("uuid") or row.get("patient_uuid")
            key = (buyer, day(row.get("sale_date")), cents(raw.get("final_amount")))
            if all(value is not None for value in key):
                self.exact[key].add(seller)
            sale_id = raw.get("uuid") or row.get("uuid")
            if sale_id:
                self.by_sale[str(sale_id)].add(seller)
            bill_id = raw.get("bill_uuid") or record(raw.get("bill")).get("uuid")
            if bill_id:
                self.by_bill[str(bill_id)].add(seller)

    def resolve(self, bill, parcel=None, *, income=True, bill_id=None, emission=None):
        parcel = parcel or {}
        owners = {value for raw in (bill, parcel) for value in (
            record(raw.get("seller")).get("uuid"), record(raw.get("professional")).get("uuid"),
            raw.get("professional_uuid")) if value}
        if owners:
            return (next(iter(owners)), "explicit_uuid") if len(owners) == 1 else (None, "conflict")
        if not income:
            return None, None
        sale_id = bill.get("sale_uuid") or record(bill.get("sale")).get("uuid")
        candidates = self.by_sale.get(str(sale_id), set()) if sale_id else self.by_bill.get(str(bill_id), set())
        if sale_id or candidates:
            return (next(iter(candidates)), "sale_uuid") if len(candidates) == 1 and None not in candidates else (None, "ambiguous")
        key = (record(bill.get("person")).get("uuid"), day(emission or bill.get("emission_date")), cents(bill.get("final_amount")))
        candidates = self.exact.get(key, set())
        return (next(iter(candidates)), "patient_date_amount") if len(candidates) == 1 and None not in candidates else (None, "ambiguous")


def payment_item(uuid, row, bill, parent, raw, status, kind, when, date_source, net, direction):
    """Public payment details without exposing source JSON or integration credentials."""
    gross, fees = cents(raw.get("final_amount")), cents(raw.get("fees_amount"))
    if gross is None and fees is not None:
        gross = net + fees
    if fees is None and gross is not None and gross >= net:
        fees = gross - net
    person = record(parent.get("person"))
    contact = person.get("name") or person.get("full_name") or "Sem contato"
    title_id = row.get("bill_uuid") or bill.get("uuid") or parent.get("uuid") or uuid
    return {"uuid": uuid, "title_id": title_id, "date": when, "date_source": date_source,
            "description": parent.get("description") or raw.get("description") or "Título financeiro",
            "contact": contact, "contact_id": str(person.get("uuid") or normalized(contact)),
            "category": record(parent.get("category") or parent.get("financial_category")).get("name") or "Sem categoria",
            "title_type": str(parent.get("type") or bill.get("type") or row.get("type") or kind),
            "direction": direction, "gross": float(gross / 100) if gross is not None else None,
            "net": float(net / 100), "fees": float(fees / 100) if fees is not None else None,
            "emission_date": day(parent.get("emission_date") or bill.get("emission_date")), "status": status}


def build_receipts(conn, date_from, date_to, professional_uuids=(), *, include_items=False):
    """Never infer receipt from zero balance, due date, or total bill amount."""
    date_to = min(date_to, date.today().isoformat())
    professionals = set(professional_uuids)
    owners = SaleOwners(conn)
    excluded = {"professional": 0, "date": 0, "net_amount": 0}
    pending = []
    resolved = {"professional": 0, "date": 0}
    total, fees_total, count = Decimal(0), Decimal(0), 0
    items = []
    for uuid, row, bill, bill_raw, merged, status, kind in parcel_records(conn):
        # Experts uses "Conta" for both directions: "received" proves an inflow.
        if status not in SETTLED or (kind not in INCOME and status != "received"):
            continue

        if cancelled(bill_raw) or cancelled(merged):
            continue
        seller, owner_source = owners.resolve(bill_raw, merged, income=kind in INCOME,
            bill_id=bill.get("uuid") or row.get("bill_uuid"), emission=bill_raw.get("emission_date"))

        # An explicit different seller is outside scope, not a missing-data warning.
        if professionals and seller and seller not in professionals:
            continue
        paid_day, date_source = receipt_date(merged, row.get("paid_at"))
        detail = dict(bill=bill_raw, source="parcel", title_id=row.get("bill_uuid") or bill.get("uuid"),
                      date=paid_day, date_source=date_source, status=status,
                      gross=merged.get("final_amount"), net=net_value(merged))
        if not paid_day:
            excluded["date"] += 1
            pending.append(issue(uuid, "date", merged, **detail))
            continue
        if not date_from <= paid_day <= date_to:
            continue
        if professionals and not seller:
            excluded["professional"] += 1
            pending.append(issue(uuid, "professional", merged, **detail))
            continue
        net = net_value(merged)
        if net is None:
            excluded["net_amount"] += 1
            pending.append(issue(uuid, "net_amount", merged, **detail))
            continue
        total += net
        fees_total += cents(merged.get("fees_amount")) or Decimal(0)
        count += 1
        if include_items:
            items.append(payment_item(uuid, row, bill, bill_raw, merged, status, kind,
                                      paid_day, date_source, net, "income"))
        if professionals and owner_source != "explicit_uuid":
            resolved["professional"] += 1
        if paid_day and not receipt_day(record(row.get("raw_json"))):
            resolved["date"] += 1

    result = {"net_total": float(total / 100), "fees_total": float(fees_total / 100),
            "count": count, "excluded": excluded, "pending": pending, "resolved": resolved,
            "status": "partial" if any(excluded.values()) else "complete",
            "basis": "Parcelas de receitas recebidas, pelo valor líquido e pela data de compensação; "
                     "na ausência dessa data, utiliza a data de recebimento registrada. "
                     "Vencimento e compensação prevista não comprovam recebimento. "
                     "Compensações futuras não entram no total recebido."}
    if include_items:
        result["items"] = items
    return result
