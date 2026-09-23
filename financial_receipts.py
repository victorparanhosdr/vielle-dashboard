"""Read-only net receipts from Experts parcels, separate from sale-date totals."""
import json
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation


SETTLED = {"received", "paid", "settled", "done", "pago", "paga", "quitado",
           "quitada", "liquidado", "liquidada"}
INCOME = {"venda", "sale", "receita"}


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


def receipt_day(raw, paid_at=None):
    # Compensation is cash entry; execution may be the earlier card transaction.
    for key in ("compensation_date", "liquidation_date", "settlement_date", "settled_at",
                "received_at", "paid_at", "payment_date", "paid_date", "execution_date"):
        if raw.get(key):
            return day(raw[key])
    if raw.get("calc_compensation_date"):
        return None
    return day(paid_at)


def net_value(raw):
    value = cents(raw.get("net_amount"))
    if value is None:
        gross, fees = cents(raw.get("final_amount")), cents(raw.get("fees_amount"))
        if gross is not None and fees is not None:
            value = gross - fees
    return value if value is not None and value >= 0 else None


def build_receipts(conn, date_from, date_to, professional_uuids=()):
    """Never infer receipt from zero balance, due date, or total bill amount."""
    date_to = min(date_to, date.today().isoformat())
    professionals = set(professional_uuids)
    sales = defaultdict(set)
    for row in conn.execute("select patient_uuid, sale_date, total, raw_json from clinica_sales where type = 'sale'"):
        raw = record(row["raw_json"])
        buyer = record(raw.get("buyer")).get("uuid") or row["patient_uuid"]
        seller = record(raw.get("seller")).get("uuid")
        amount = cents(raw.get("final_amount"))
        sale_day = day(row["sale_date"])
        if buyer and amount is not None and sale_day:
            # Keep unknown sellers in the candidate set: they make a match ambiguous.
            sales[(buyer, sale_day, amount)].add(seller)

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

    flat = {row["uuid"]: dict(row) for row in conn.execute(
        "select uuid, bill_uuid, type, status, paid_at, raw_json, synced_at from clinica_parcels")}
    excluded = {"professional": 0, "date": 0, "net_amount": 0}
    total, fees_total, count = Decimal(0), Decimal(0), 0
    for uuid in embedded.keys() | flat.keys():
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
        kind = str(bill.get("type") or row.get("type") or merged.get("type") or "").lower()
        if kind not in INCOME or str(status or "").lower() not in SETTLED:
            continue

        bill_raw = bill.get("raw") or record(merged.get("raw_bill"))
        seller = record(bill_raw.get("seller")).get("uuid")
        if not seller:
            key = (record(bill_raw.get("person")).get("uuid"),
                   day(bill.get("emission_date") or bill_raw.get("emission_date")),
                   cents(bill_raw.get("final_amount")))
            candidates = sales.get(key, set())
            seller = next(iter(candidates)) if len(candidates) == 1 else None

        # An explicit different seller is outside scope, not a missing-data warning.
        if professionals and seller and seller not in professionals:
            continue
        paid_day = receipt_day(merged, row.get("paid_at"))
        if not paid_day:
            excluded["date"] += 1
            continue
        if not date_from <= paid_day <= date_to:
            continue
        if professionals and not seller:
            excluded["professional"] += 1
            continue
        net = net_value(merged)
        if net is None:
            excluded["net_amount"] += 1
            continue
        total += net
        fees_total += cents(merged.get("fees_amount")) or Decimal(0)
        count += 1

    return {"net_total": float(total / 100), "fees_total": float(fees_total / 100),
            "count": count, "excluded": excluded,
            "status": "partial" if any(excluded.values()) else "complete",
            "basis": "Parcelas de receitas recebidas, pelo valor líquido e pela data de compensação; "
                     "na ausência dessa data, utiliza a data de recebimento registrada. "
                     "Vencimento e compensação prevista não comprovam recebimento. "
                     "Compensações futuras não entram no total recebido."}
