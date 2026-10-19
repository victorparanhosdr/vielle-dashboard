"""Read-only patient sales/quotes from the Vielle Experts synchronized cache."""
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
import sqlite3

from financial_receipts import cancelled, cents, day, normalized, record


class PatientCommercial:
    def __init__(self, path, professionals=None):
        self.path = Path(path)
        self.professionals = {normalized(name): str(uid) for name, uid in (professionals or {}).items()}

    def summaries(self, patient_uuids, doctor, include_search=False):
        patients = list(dict.fromkeys(patient_uuids))
        result = {uid: {"state": "unavailable"} for uid in patients}
        if not patients or not self.path.exists():
            return result
        try:
            c = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=3)
            c.row_factory = sqlite3.Row
            try:
                c.execute("BEGIN")
                for offset in range(0, len(patients), 400):
                    chunk = patients[offset:offset+400]
                    placeholders = ",".join("?" for _ in chunk)
                    known = {r["uuid"]: dict(r) for r in c.execute(
                        f"SELECT uuid,name,phone,raw_json FROM clinica_patients WHERE uuid IN ({placeholders})", chunk)}
                    grouped = {uid: [] for uid in chunk}
                    for row in c.execute(f"SELECT uuid,patient_uuid,type,sale_date,total,raw_json,synced_at FROM clinica_sales WHERE patient_uuid IN ({placeholders})", chunk):
                        grouped[row["patient_uuid"]].append(row)
                    for uid in chunk:
                        patient = known.get(uid)
                        raw = record(patient["raw_json"]) if patient else None
                        if raw is None or cancelled(raw) or normalized(raw.get("status")) in {"removed", "excluida"}:
                            result[uid] = {"state": "identity_pending"}
                            continue
                        data = self.build(grouped[uid], uid, doctor, "doctor", "sales", 1, include_search)
                        result[uid] = {key: data[key] for key in ("state", "summary", "sales_warnings", "quote_warnings", "synced_at")}
                        if include_search:
                            profile = [patient["name"], patient["phone"], *(raw.get(k) for k in ("name", "full_name", "email", "cpf", "document", "phone", "cellphone", "mobile", "telephone", "whatsapp"))]
                            result[uid]["search_text"] = " ".join(str(v) for v in profile if isinstance(v, (str, int))) + " " + data["search_text"]
                return result
            finally:
                c.close()
        except (sqlite3.Error, OSError):
            return {uid: {"state": "unavailable"} for uid in patients}

    def lookup(self, patient_uuid, doctor, scope, kind, page):
        result = {"state": "unavailable", "scope": scope, "kind": kind, "page": page,
                  "items": [], "summary": {}, "synced_at": 0, "doctor_name": doctor}
        if not self.path.exists():
            return result
        try:
            c = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=3)
            c.row_factory = sqlite3.Row
            try:
                patient = c.execute("SELECT raw_json FROM clinica_patients WHERE uuid=?", (patient_uuid,)).fetchone()
                if not patient or cancelled(record(patient["raw_json"])) or normalized(record(patient["raw_json"]).get("status")) in {"removed", "excluida"}:
                    return {**result, "state": "identity_pending"}
                rows = c.execute("SELECT uuid,patient_uuid,type,sale_date,total,raw_json,synced_at FROM clinica_sales WHERE patient_uuid=?", (patient_uuid,)).fetchall()
                return self.build(rows, patient_uuid, doctor, scope, kind, page)
            finally:
                c.close()
        except (sqlite3.Error, OSError):
            return result

    def build(self, rows, patient_uuid, doctor, scope, kind, page, include_search=False):
        from app import extract_sale_items, first_value, quote_professional_identity, quote_source_status, sale_counts_for_patient_followup, sale_item_name_and_category
        sales, quotes, converted = [], [], set()
        warnings = {"identity": 0, "professional": 0, "malformed": 0, "amount": 0}
        sales_warnings = dict(warnings)
        expected = self.professionals.get(normalized(doctor))
        synced = 0
        for row in rows:
            raw = record(row["raw_json"])
            synced = max(synced, row["synced_at"] or 0)
            if not raw:
                warnings["malformed"] += 1
                if normalized(row["type"]) not in {"sale_quote", "quote", "budget", "proposal"}:
                    sales_warnings["malformed"] += 1
                continue
            if cancelled(raw) or raw.get("active") is False or normalized(raw.get("status")) in {"removed", "excluida"}:
                continue
            status = quote_source_status(raw, row["type"])
            is_sale = status is None and sale_counts_for_patient_followup(raw, row["type"]) and normalized(raw.get("status")) != "inactive"
            identities = {str(entity.get("uuid") or entity.get("id")) for key in ("patient", "buyer", "buyer_person", "person", "client", "customer")
                          if isinstance((entity := raw.get(key)), dict) and (entity.get("uuid") or entity.get("id"))}
            if identities and identities != {patient_uuid}:
                warnings["identity"] += 1
                sales_warnings["identity"] += int(is_sale)
                continue
            if is_sale:
                reference = first_value(raw, ["sale_quote_uuid", "quote_uuid"])
                linked = first_value(raw, ["sale_quote", "quote"])
                if not reference and isinstance(linked, dict):
                    reference = first_value(linked, ["uuid", "id"])
                if reference:
                    converted.add(str(reference))
            elif status is None or status == "won" or raw.get("_source_event") == "sale_quote.converted":
                continue
            professional_id, professional_name = quote_professional_identity(raw)
            if scope == "doctor":
                if professional_id and expected:
                    if professional_id != expected:
                        continue
                elif normalized(professional_name) != normalized(doctor):
                    if not professional_id and not professional_name:
                        warnings["professional"] += 1
                        sales_warnings["professional"] += int(is_sale)
                    continue
            raw_amount = first_value(raw, ["final_amount", "budget_amount", "quoted_amount", "total", "amount", "nominal_amount"])
            if raw_amount is None and isinstance(raw.get("title"), dict):
                raw_amount = first_value(raw["title"], ["final_amount", "total", "amount", "nominal_amount"])
            amount = cents(raw_amount) if raw_amount is not None else cents(row["total"])
            if amount is not None and raw_amount is None:
                amount *= 100
            amount = int(amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP)) if amount is not None and amount >= 0 else None
            if amount is None:
                warnings["amount"] += 1
                sales_warnings["amount"] += int(is_sale)
            procedures = []
            for item in extract_sale_items(raw):
                name, _ = sale_item_name_and_category(item, {})
                if name:
                    procedures.append(str(name)[:200])
            label = raw.get("description") or raw.get("title")
            item = {"uuid": row["uuid"], "date": day((raw.get("sale_date") if is_sale else raw.get("quote_date")) or row["sale_date"]),
                    "title": str(label)[:200] if isinstance(label, str) and label.strip() else ("Venda Clínica Experts" if is_sale else "Orçamento Clínica Experts"),
                    "status": "closed" if is_sale else status, "amount_cents": amount, "procedures": procedures[:50],
                    "professional": professional_name or "Profissional não informado", "synced_at": row["synced_at"]}
            (sales if is_sale else quotes).append(item)
        quotes = [q for q in quotes if q["uuid"] not in converted]
        for values in (sales, quotes):
            values.sort(key=lambda r: (r["date"] or "", r["uuid"]), reverse=True)
        values = sales if kind == "sales" else quotes
        summary = {"sales_count": len(sales), "sales_cents": sum(r["amount_cents"] or 0 for r in sales),
                   "quotes_count": len(quotes), "quotes_cents": sum(r["amount_cents"] or 0 for r in quotes),
                   "open_quotes_count": sum(r["status"] == "active" for r in quotes),
                   "expired_quotes_count": sum(r["status"] == "expired" for r in quotes),
                   "lost_quotes_count": sum(r["status"] == "lost" for r in quotes)}
        pages = max(1, (len(values) + 24) // 25)
        page = min(page, pages)
        return {"state": "ready", "scope": scope, "kind": kind, "page": page, "pages": pages, "total": len(values),
                "summary": summary, "items": values[(page-1)*25:page*25], "warnings": warnings, "sales_warnings": sales_warnings,
                "quote_warnings": {k: warnings[k]-sales_warnings[k] for k in warnings},
                **({"search_text": " ".join(sorted({v for item in sales+quotes for v in [item["title"], *item["procedures"]]}))} if include_search else {}),
                "synced_at": synced, "doctor_name": doctor}
