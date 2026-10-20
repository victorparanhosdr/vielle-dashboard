"""Explicit, read-only Kommo extraction and idempotent CRM migration."""
from collections import Counter
from decimal import Decimal, InvalidOperation
import json
import sqlite3
import threading
import time
import uuid
from urllib.parse import urlencode

from crm_patients import experts_phone, match_patient_name, search_key
from crm_store import Conflict, phone
from institute_report import email

FUNNELS = {"organico paranhos", "regencode", "trafego paranhos"}
DOCTOR = "victor paranhos de andrade"
REASONS = {
    "contact": "Contato principal ausente ou ambiguo no Kommo",
    "phone": "Sem telefone completo valido",
    "shared": "Telefone compartilhado entre fichas do Clinica Experts",
    "identity": "Telefones apontam para pacientes diferentes",
    "conflict": "Contatos ou vinculos existentes precisam de conferencia",
    "unavailable": "Cadastro no Clinica Experts nao confirmado",
    "stage": "Fase de origem nao confirmada",
    "deleted": "Registro excluido na origem",
}


def schema(store):
    with store.db() as c:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS kommo_import_jobs(
                id TEXT PRIMARY KEY,doctor_id TEXT NOT NULL REFERENCES doctors(id),
                pipeline_id TEXT NOT NULL REFERENCES pipelines(id),status TEXT NOT NULL,
                message TEXT NOT NULL,created_at REAL NOT NULL,payload TEXT NOT NULL DEFAULT '{}',
                report TEXT NOT NULL DEFAULT '{}');
            CREATE TABLE IF NOT EXISTS kommo_import_refs(
                source_pipeline INTEGER NOT NULL,source_lead INTEGER NOT NULL,
                lead_id TEXT NOT NULL REFERENCES leads(id),job_id TEXT NOT NULL,
                PRIMARY KEY(source_pipeline,source_lead));
        """)


def target(store, doctor, pipeline):
    with store.db() as c:
        row = store.owned(c, "pipelines", pipeline, doctor)
        name = store.row(c, "doctors", doctor)["name"]
    if search_key(row["name"].strip()) not in FUNNELS or search_key(name.strip()) != DOCTOR:
        raise ValueError("Esta migracao esta limitada aos funis Organico Paranhos, Trafego Paranhos e REGENCODE do Dr. Victor Paranhos de Andrade.")
    return row


def collection(payload, key):
    if payload == {}:  # Kommo's 204 response.
        return []
    items = payload.get("_embedded", {}).get(key) if isinstance(payload, dict) else None
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise ValueError("Resposta incompleta do Kommo; nada foi importado.")
    return items


def positive(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def main_contact(lead):
    contacts = lead.get("_embedded", {}).get("contacts", [])
    if not isinstance(contacts, list):
        return None
    contacts = [c for c in contacts if isinstance(c, dict) and positive(c.get("id"))]
    main = [c for c in contacts if c.get("is_main") is True]
    return main[0]["id"] if len(main) == 1 else contacts[0]["id"] if len(contacts) == 1 else None


def extract(request, progress=lambda message: None, *, funnel="Organico Paranhos"):
    if search_key(funnel.strip()) not in FUNNELS:
        raise ValueError("Funil nao habilitado para migracao.")
    pipelines = [p for p in collection(request("/api/v4/leads/pipelines"), "pipelines")
                 if search_key(str(p.get("name", "")).strip()) == search_key(funnel.strip()) and not p.get("is_archive")]
    if len(pipelines) != 1 or not positive(pipelines[0].get("id")):
        raise ValueError("Nao foi possivel identificar um unico funil correspondente ativo no Kommo.")
    pipeline = pipelines[0]
    source = pipeline["id"]
    stages = {s["id"]: {"name": str(s.get("name") or "Fase sem nome")[:60],
                             "kind": "won" if s["id"] == 142 else "lost" if s["id"] == 143 else "open"}
              for s in pipeline.get("_embedded", {}).get("statuses", []) if isinstance(s, dict) and positive(s.get("id"))}
    if not stages:
        raise ValueError("As fases do funil nao foram confirmadas.")
    leads = {}
    for page in range(1, 201):
        progress(f"Buscando oportunidades do Kommo · pagina {page}")
        items = collection(request("/api/v4/leads?" + urlencode({
            "limit": 250, "page": page, "with": "contacts", "filter[pipeline_id][]": source,
            "order[id]": "asc"})), "leads")
        if any(not positive(item.get("id")) or item.get("pipeline_id") != source for item in items):
            raise ValueError("O Kommo retornou oportunidades fora do funil solicitado.")
        if items and all(item["id"] in leads for item in items):
            raise ValueError("Paginacao repetida no Kommo; nada foi importado.")
        leads.update((item["id"], item) for item in items)
        if len(items) < 250:
            break
    else:
        raise ValueError("Busca do funil incompleta; nada foi importado.")
    ids = sorted({cid for lead in leads.values() if (cid := main_contact(lead))})
    contacts = {}
    for offset in range(0, len(ids), 100):
        progress(f"Conferindo contatos · {min(offset + 100, len(ids))} de {len(ids)}")
        batch = ids[offset:offset + 100]
        query = [("filter[id][]", cid) for cid in batch] + [("limit", 250)]
        items = collection(request("/api/v4/contacts?" + urlencode(query)), "contacts")
        if any(item.get("id") not in batch for item in items):
            raise ValueError("O Kommo retornou contatos fora da selecao.")
        contacts.update((item["id"], item) for item in items)
    records = []
    for lead in sorted(leads.values(), key=lambda item: (item.get("updated_at") or 0, item["id"]), reverse=True):
        cid = main_contact(lead)
        contact = contacts.get(cid, {})
        numbers, emails = [], []
        for field in contact.get("custom_fields_values") or []:
            if not isinstance(field, dict):
                continue
            for item in field.get("values") or []:
                if field.get("field_code") == "EMAIL":
                    address = email(item.get("value")) if isinstance(item, dict) else ""
                    if address and address not in emails:
                        emails.append(address)
                if field.get("field_code") != "PHONE":
                    continue
                try:
                    number = phone(experts_phone(item.get("value")))
                except (ValueError, AttributeError):
                    continue
                if number not in numbers:
                    numbers.append(number)
        reason = "deleted" if lead.get("is_deleted") or contact.get("is_deleted") else "contact" if not contact else "phone" if not numbers else "stage" if lead.get("status_id") not in stages else ""
        try:
            value = int(Decimal(str(lead.get("price") or 0)) * 100)
            if not 0 <= value <= 100_000_000:
                value = 0
        except (InvalidOperation, ValueError, OverflowError):
            value = 0
        records.append({"source_id": lead["id"], "source_contact": cid,
                        "name": str(contact.get("name") or lead.get("name") or "Contato Kommo").strip()[:160],
                        "title": str(lead.get("name") or contact.get("name") or "Oportunidade Kommo").strip()[:160],
                        "phones": numbers, "email": next(iter(emails), email(lead.get("name"))), "stage": stages.get(lead.get("status_id")),
                        "value_cents": value, "created_at": lead.get("created_at"),
                        "updated_at": lead.get("updated_at"), "reason": reason})
    return {"source_pipeline": source, "source_name": pipeline["name"], "records": records}


def process(store, c, pack, doctor, pipeline, actor="", job="", apply=False):
    destination = store.owned(c, "pipelines", pipeline, doctor)
    course = bool(destination["course_key"])
    contacts = {r["id"]: dict(r) for r in c.execute("SELECT * FROM contacts")}
    phones = {r["phone"]: r["contact_id"] for r in c.execute("SELECT * FROM contact_phones")}
    patients = {r["experts_uuid"]: r["id"] for r in contacts.values() if r["experts_uuid"]}
    existing = {r["contact_id"]: r["id"] for r in c.execute("SELECT id,contact_id FROM leads WHERE pipeline_id=?", (pipeline,))}
    refs = {r[0] for r in c.execute("SELECT source_lead FROM kommo_import_refs WHERE source_pipeline=?", (pack["source_pipeline"],))}
    stages = {(search_key(r["name"]), r["kind"]): r["id"] for r in c.execute("SELECT * FROM stages WHERE pipeline_id=?", (pipeline,))}
    report = {"total": len(pack["records"]), "created": 0, "reused": 0, "duplicates": 0,
              "already": 0, "linked": 0, "new": 0, "resolved": 0, "review": 0, "issues": [], "reviews": [], "course_contacts": 0}
    whatsapp_only = search_key(pack["source_name"].strip()) == "trafego paranhos"
    report["ignored_no_phone"] = 0
    touched, seen = set(), set()
    now = time.time()
    for record in pack["records"]:
        if whatsapp_only and not record["phones"]:
            report["ignored_no_phone"] += 1
            continue
        if record["source_id"] in refs:
            report["already"] += 1
            continue
        reason, matches = record["reason"], []
        if not reason:
            matches = ([{"status": "new"} for number in record["phones"]] if course else
                       [match_patient_name(store.patient_lookup(number), record["name"]) for number in record["phones"]])
            if any(m["status"] == "unchecked" for m in matches):
                reason = "unavailable"
            elif any(m["status"] == "review" for m in matches):
                reason = "shared"
            elif len({m["uuid"] for m in matches if m["status"] == "matched"}) > 1:
                reason = "identity"
        matched = next((m for m in matches if m["status"] == "matched"), None)
        patient = matched["uuid"] if matched else ""
        found = {phones[n] for n in record["phones"] if n in phones}
        if patient in patients:
            found.add(patients[patient])
        if not reason and len(found) > 1:
            reason = "conflict"
        cid = next(iter(found), None)
        if not course and not reason and cid and contacts[cid]["experts_uuid"] and contacts[cid]["experts_uuid"] != patient:
            reason = "conflict"
        unlinked = bool(reason == "shared" and pack.get("recover_pending") is True and not matched and len(found) <= 1
                        and (not cid or (not contacts[cid]["experts_uuid"] and
                             search_key(contacts[cid]["name"]).strip() == search_key(record["name"]).strip())))
        if unlinked:
            reason = ""
        if reason:
            report["issues"].append({"source_id": record["source_id"], "name": record["name"],
                                     "reason": REASONS[reason]})
            continue
        number = next((n for n, m in zip(record["phones"], matches) if m["status"] == "matched"),
                      next((n for n in record["phones"] if phones.get(n) == cid), record["phones"][0]))
        if unlinked and not cid:
            number = next(n for n, m in zip(record["phones"], matches) if m["status"] == "review")
        if unlinked and next(m for n, m in zip(record["phones"], matches) if n == number)["status"] != "review":
            report["issues"].append({"source_id": record["source_id"], "name": record["name"], "reason": REASONS["conflict"]})
            continue
        if apply:
            evidence = {"identity_name": record["name"]} if any(m.get("match_basis") == "phone_name" for m in matches) else {}
            if course:
                evidence["clinical"] = False
            contact = store.contact(c, number, record["name"], **evidence)
            if not course and (contact["experts_uuid"] != patient or contact["experts_status"] not in ({"review"} if unlinked else {"matched", "new"})):
                raise Conflict(f"A identidade do paciente mudou na oportunidade Kommo #{record['source_id']}. Gere outra previa.")
            if cid and cid != contact["id"]:
                raise Conflict("O cadastro mudou durante a importacao. Gere outra previa.")
            cid = contact["id"]
            for n in record["phones"]:
                c.execute("INSERT OR IGNORE INTO contact_phones VALUES(?,?)", (n, cid))
        cid = cid or uuid.uuid4().hex
        contacts.setdefault(cid, {"id": cid, "experts_uuid": patient, "name": record["name"]})
        if patient:
            contacts[cid].update(experts_uuid=patient, name=matched["name"])
        for n in record["phones"]:
            phones[n] = cid
        if patient:
            patients[patient] = cid
        if cid not in touched:
            report["course_contacts" if course else "review" if unlinked else "linked" if patient else "new"] += 1
            if patient and any(m.get("match_basis") == "phone_name" for m in matches):
                report["resolved"] += 1
            touched.add(cid)
        if unlinked:
            report["reviews"].append({"source_id": record["source_id"], "name": record["name"],
                                      "reason": "Telefone compartilhado; contato importado sem vincular ficha clinica.", "imported": True})
        if cid in seen:
            report["duplicates"] += 1
        elif cid in existing:
            report["reused"] += 1
        else:
            sidkey = (search_key(record["stage"]["name"]), record["stage"]["kind"])
            if sidkey not in stages:
                stages[sidkey] = uuid.uuid4().hex
                if apply:
                    position = c.execute("SELECT COALESCE(MAX(position),-1)+1 FROM stages WHERE pipeline_id=?", (pipeline,)).fetchone()[0]
                    c.execute("INSERT INTO stages VALUES(?,?,?,?,?)", (stages[sidkey], pipeline, record["stage"]["name"], position, record["stage"]["kind"]))
                    c.execute("UPDATE pipelines SET revision=revision+1 WHERE id=?", (pipeline,))
            lid = uuid.uuid4().hex
            existing[cid] = lid
            if apply:
                stamp = lambda value: float(value) if isinstance(value, (int, float)) and 1262304000 <= value <= now else now
                c.execute("""INSERT INTO leads(id,doctor_id,pipeline_id,stage_id,contact_id,title,value_cents,created_at,updated_at,email,source_name)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (lid, doctor, pipeline, stages[sidkey], cid, record["title"], record["value_cents"],
                                                  stamp(record["created_at"]), stamp(record["updated_at"]), record.get("email", ""), record["name"] if course else ""))
                store.event(c, lid, actor, "kommo_import", f"Importado do Kommo · {pack['source_name']} · oportunidade {record['source_id']}", now)
                if unlinked:
                    store.event(c, lid, actor, "kommo_identity", "Telefone compartilhado. Nenhuma ficha clinica foi vinculada automaticamente.", now)
            report["created"] += 1
        seen.add(cid)
        if apply:
            c.execute("INSERT INTO kommo_import_refs VALUES(?,?,?,?)", (pack["source_pipeline"], record["source_id"], existing[cid], job))
    return report


def status(store, doctor, pipeline, job_id="", page=1):
    schema(store)
    destination = target(store, doctor, pipeline)
    with store.db() as c:
        row = c.execute("SELECT * FROM kommo_import_jobs WHERE doctor_id=? AND pipeline_id=? " +
                        ("AND id=? " if job_id else "") + "ORDER BY created_at DESC LIMIT 1",
                        (doctor, pipeline, job_id) if job_id else (doctor, pipeline)).fetchone()
    if row is None:
        return {"status": "idle", "message": "", "report": {}, "pipeline_name": destination["name"], "course_key": destination["course_key"]}
    report = json.loads(row["report"])
    issues = report.pop("issues", [])
    reviews = report.pop("reviews", [])
    entries = issues + reviews
    pages = max(1, (len(entries) + 24) // 25)
    page = min(max(1, int(page)), pages)
    return {"id": row["id"], "status": row["status"], "message": row["message"], "pipeline_name": destination["name"], "course_key": destination["course_key"],
            "created_at": row["created_at"], "report": {**report, "issue_count": len(issues),
                "issue_summary": [{"reason": reason, "count": count} for reason, count in Counter(item["reason"] for item in issues).most_common()]},
            "issues": entries[(page - 1) * 25:page * 25], "page": page, "pages": pages}


def prepare(store, doctor, pipeline, *, background=True, source=None, retry_from=""):
    schema(store)
    destination = target(store, doctor, pipeline)
    if not destination["course_key"] and store.patient_directory is None:
        raise ValueError("Base do Clinica Experts indisponivel.")
    with store.db() as c:
        c.execute("BEGIN IMMEDIATE")
        if retry_from:
            previous = c.execute("SELECT id FROM kommo_import_jobs WHERE id=? AND doctor_id=? AND pipeline_id=? AND status='done'",
                                 (retry_from, doctor, pipeline)).fetchone()
            if previous is None:
                raise ValueError("Migracao concluida nao encontrada neste funil.")
        active = c.execute("SELECT id,created_at FROM kommo_import_jobs WHERE status='loading' ORDER BY created_at DESC LIMIT 1").fetchone()
        if active and time.time() - active["created_at"] < 1800:
            raise Conflict("Uma previa do Kommo ja esta sendo preparada. Aguarde a conclusao.")
        if active:
            c.execute("UPDATE kommo_import_jobs SET status='error',message='Previa interrompida. Gere outra previa.' WHERE status='loading'")
        key = uuid.uuid4().hex
        c.execute("INSERT INTO kommo_import_jobs(id,doctor_id,pipeline_id,status,message,created_at) VALUES(?,?,?,'loading',?,?)",
                  (key, doctor, pipeline, "Atualizando cadastros e buscando o funil...", time.time()))

    def progress(message):
        with store.db() as c:
            c.execute("UPDATE kommo_import_jobs SET message=? WHERE id=?", (message, key))

    def run():
        try:
            if source:
                pack = source(progress)
            else:
                import app
                if not destination["course_key"]:
                    from crm_patient_refresh import sync_patients
                    sync_patients(store, progress)
                elif store.course_source:
                    from crm_courses import refresh
                    progress("Atualizando pedidos Kiwify do curso...")
                    try:
                        refresh(store.course_source, destination["institute_key"], destination["course_key"])
                    except Exception:
                        pass  # A provider outage does not block migration of course contacts.
                with app.clinic_context("vielle"):
                    access = app.get_access_context()

                    def request(path):
                        time.sleep(.3)
                        return app.kommo_request("GET", path, token=access["access_token"], domain=access["account_domain"])

                    pack = extract(request, progress, funnel=destination["name"])
            if search_key(pack["source_name"].strip()) != search_key(destination["name"].strip()):
                raise ValueError("O funil de origem nao corresponde ao destino.")
            if retry_from:
                pack["recover_pending"] = True
            if search_key(destination["name"].strip()) == "trafego paranhos":
                pack["recover_pending"] = True
            if destination["course_key"] and store.course_source:
                for record in pack["records"]:
                    if record["reason"] == "phone" and record.get("email"):
                        numbers = store.course_source.recover_phones(destination["institute_key"], destination["course_key"], [record["email"]])
                        if numbers:
                            record.update(phones=numbers, reason="")
            if store.patient_directory and not destination["course_key"]:
                store.patient_directory.invalidate()
            with store.db() as c:
                report = process(store, c, pack, doctor, pipeline)
                c.execute("UPDATE kommo_import_jobs SET status='ready',message='Previa pronta. Nenhum cliente foi importado ainda.',payload=?,report=? WHERE id=?",
                          (json.dumps(pack), json.dumps(report), key))
        except Exception:
            with store.db() as c:
                c.execute("UPDATE kommo_import_jobs SET status='error',message=? WHERE id=?",
                          ("Nao foi possivel confirmar a leitura completa do Kommo e do Clinica Experts. Nada foi importado; confira as integracoes e gere outra previa.", key))

    if background:
        threading.Thread(target=run, daemon=True, name="crm-kommo-preview").start()
    else:
        run()
    return {"id": key}


def commit(store, doctor, pipeline, job_id, actor):
    schema(store)
    target(store, doctor, pipeline)
    with store.db() as c:
        row = c.execute("SELECT * FROM kommo_import_jobs WHERE id=? AND doctor_id=? AND pipeline_id=?", (job_id, doctor, pipeline)).fetchone()
        if row is None:
            raise LookupError()
        if row["status"] == "done":
            return status(store, doctor, pipeline, job_id)
        if row["status"] != "ready" or time.time() - row["created_at"] > 1800:
            raise Conflict("Gere uma previa atualizada antes de importar.")
    backup_dir = store.path.parent / "backups"
    backup_dir.mkdir(exist_ok=True, mode=0o700)
    with sqlite3.connect(store.path) as original, sqlite3.connect(backup_dir / f"before-kommo-{row['id']}.sqlite3") as backup:
        original.backup(backup)
    if store.patient_directory:
        store.patient_directory.invalidate()
    with store.db() as c:
        c.execute("BEGIN IMMEDIATE")
        target_row = store.owned(c, "pipelines", pipeline, doctor)
        pack = json.loads(row["payload"])
        if search_key(target_row["name"].strip()) not in FUNNELS or search_key(target_row["name"].strip()) != search_key(pack["source_name"].strip()):
            raise Conflict("O funil mudou. Gere outra previa.")
        current = c.execute("SELECT status FROM kommo_import_jobs WHERE id=?", (job_id,)).fetchone()[0]
        if current != "done":
            report = process(store, c, pack, doctor, pipeline, actor, job_id, apply=True)
            message = "Migracao concluida. Cadastros existentes preservados."
            if report["review"]:
                message += f" {report['review']} contato(s) importado(s) sem vinculo clinico; fichas ambiguas nao foram associadas."
            if report["issues"]:
                message += f" {len(report['issues'])} registro(s) sem dados suficientes para importacao segura."
            c.execute("UPDATE kommo_import_jobs SET status='done',message=?,report=? WHERE id=?",
                      (message, json.dumps(report), job_id))
    return status(store, doctor, pipeline, job_id)
