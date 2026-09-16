"""Clinic-scoped body assessments and original exam documents, isolated from sync data."""

import base64
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo


MAX_PDF = 8 * 1024 * 1024
FIELDS = {
    "weight_kg": ("Peso", "kg", 1, 500),
    "height_cm": ("Altura", "cm", 30, 250),
    "muscle_kg": ("Massa muscular esquelética", "kg", 0, 250),
    "fat_free_kg": ("Massa livre de gordura", "kg", 0, 350),
    "fat_kg": ("Massa de gordura", "kg", 0, 350),
    "fat_pct": ("Gordura corporal", "%", 0, 100),
    "bmi": ("IMC", "kg/m²", 1, 150),
    "waist_cm": ("Circunferência abdominal", "cm", 10, 300),
    "hip_cm": ("Circunferência do quadril", "cm", 10, 300),
    "muscle_mm": ("Espessura muscular", "mm", 0, 2000),
    "fat_mm": ("Espessura de gordura", "mm", 0, 2000),
    "deep_fat_mm": ("Gordura abdominal profunda", "mm", 0, 1000),
    "water_l": ("Água corporal", "L", 0, 200),
    "visceral_level": ("Nível de gordura visceral", "nível", 0, 100),
    "waist_hip_ratio": ("Relação cintura/quadril", "", 0, 5),
    "bmr_kcal": ("TMB informada no exame", "kcal/dia", 100, 10000),
    "predicted_bmr_kcal": ("TMB prevista", "kcal/dia", 100, 10000),
    "tdee_kcal": ("Gasto energético total", "kcal/dia", 100, 20000),
    "diet_kcal": ("Calorias do plano alimentar", "kcal/dia", 100, 15000),
    "rq": ("Quociente respiratório", "", 0, 3),
    "fat_fuel_pct": ("Utilização de gorduras", "%", 0, 100),
    "carb_fuel_pct": ("Utilização de carboidratos", "%", 0, 100),
    "vo2": ("VO₂", "ml/kg/min", 0, 150),
    "ventilation": ("Ventilação", "L/min", 0, 300),
}


def initialize(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS body_patients (
            id TEXT PRIMARY KEY, experts_uuid TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL, created_at TEXT NOT NULL, created_by INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS body_documents (
            id TEXT PRIMARY KEY, patient_id TEXT NOT NULL REFERENCES body_patients(id),
            sha256 TEXT NOT NULL UNIQUE, content BLOB NOT NULL, created_at TEXT NOT NULL,
            extracted_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS body_evaluations (
            id TEXT PRIMARY KEY, patient_id TEXT NOT NULL REFERENCES body_patients(id),
            exam_at TEXT NOT NULL, source TEXT NOT NULL, method TEXT NOT NULL,
            fields_json TEXT NOT NULL, notes TEXT NOT NULL, professional TEXT NOT NULL,
            document_id TEXT UNIQUE REFERENCES body_documents(id),
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            created_by INTEGER NOT NULL, updated_by INTEGER NOT NULL, version INTEGER NOT NULL DEFAULT 1
        );
        CREATE INDEX IF NOT EXISTS body_evaluations_date ON body_evaluations(patient_id, exam_at);
        CREATE TABLE IF NOT EXISTS body_revisions (
            id INTEGER PRIMARY KEY, evaluation_id TEXT NOT NULL REFERENCES body_evaluations(id),
            actor_id INTEGER NOT NULL, action TEXT NOT NULL, recorded_at TEXT NOT NULL,
            snapshot_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS body_exclusions (
            evaluation_id TEXT PRIMARY KEY REFERENCES body_evaluations(id),
            deleted_at TEXT NOT NULL, deleted_by INTEGER NOT NULL
        );
    """)


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def bounded(value, length, label, required=False):
    if not isinstance(value, str) or len(value) > length or (required and not value.strip()):
        raise ValueError(f"{label}: valor inválido.")
    return value.strip()


def search_patients(conn, query, registered=False):
    query = bounded(query, 100, "Busca")
    pattern = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    if registered:
        return [dict(row) for row in conn.execute("""
            SELECT p.id, p.experts_uuid, COALESCE(c.name, p.name) AS name,
                   MAX(e.exam_at) AS last_exam, COUNT(e.id) AS evaluations
            FROM body_patients p LEFT JOIN clinica_patients c ON c.uuid=p.experts_uuid
            LEFT JOIN body_evaluations e ON e.patient_id=p.id
                AND NOT EXISTS (SELECT 1 FROM body_exclusions x WHERE x.evaluation_id=e.id)
            WHERE COALESCE(c.name, p.name) LIKE ? ESCAPE '\\'
            GROUP BY p.id ORDER BY name LIMIT 100
        """, (pattern,))]
    if len(query) < 2:
        return []
    return [dict(row) for row in conn.execute("""
        SELECT c.uuid, c.name, c.phone, b.id AS enrolled_id FROM clinica_patients c
        LEFT JOIN body_patients b ON b.experts_uuid=c.uuid
        WHERE c.name LIKE ? ESCAPE '\\' OR c.phone LIKE ? ESCAPE '\\'
        ORDER BY c.name LIMIT 50
    """, (pattern, pattern))]


def enroll(conn, experts_uuid, actor):
    experts_uuid = bounded(experts_uuid, 200, "Paciente", True)
    patient = conn.execute("SELECT uuid, name FROM clinica_patients WHERE uuid=?", (experts_uuid,)).fetchone()
    if not patient:
        raise ValueError("Paciente não encontrado na base desta clínica. Atualize a integração Clínica Experts.")
    conn.execute("INSERT OR IGNORE INTO body_patients VALUES (?, ?, ?, ?, ?)",
                 (uuid4().hex, patient["uuid"], patient["name"] or "Sem nome", now(), actor["id"]))
    return conn.execute("SELECT id FROM body_patients WHERE experts_uuid=?", (experts_uuid,)).fetchone()[0]


def patient_detail(conn, patient_id, include_deleted=False):
    row = conn.execute("""SELECT p.*, COALESCE(c.name,p.name) AS display_name, c.phone
                          FROM body_patients p LEFT JOIN clinica_patients c ON c.uuid=p.experts_uuid
                          WHERE p.id=?""", (patient_id,)).fetchone()
    if not row:
        raise ValueError("Paciente não encontrado nesta clínica.")
    evaluations, deleted = [], []
    for item in conn.execute("""SELECT e.*, x.deleted_at FROM body_evaluations e
                                LEFT JOIN body_exclusions x ON x.evaluation_id=e.id
                                WHERE e.patient_id=? ORDER BY e.exam_at, e.created_at, e.id""", (patient_id,)):
        value = dict(item)
        value["fields"] = json.loads(value.pop("fields_json"))
        if value["deleted_at"]:
            if include_deleted:
                deleted.append(value)
        else:
            evaluations.append(value)
    result = {"patient": dict(row), "evaluations": evaluations}
    if include_deleted:
        result["deleted_evaluations"] = deleted
    return result


def decode_pdf(value):
    if not isinstance(value, str) or not value or len(value) > (MAX_PDF * 4 // 3 + 4):
        raise ValueError("O PDF deve ter no máximo 8 MB.")
    try:
        data = base64.b64decode(value, validate=True)
    except Exception:
        raise ValueError("PDF inválido.") from None
    if len(data) > MAX_PDF or not data.startswith(b"%PDF-"):
        raise ValueError("Selecione um PDF válido de até 8 MB.")
    return data


def extract_pdf(data):
    try:
        result = subprocess.run([sys.executable, str(Path(__file__).with_name("body_exams.py"))],
                                input=data, capture_output=True, timeout=20, check=True)
        payload = json.loads(result.stdout)
    except (subprocess.SubprocessError, ValueError):
        raise ValueError("Não foi possível ler o PDF no tempo permitido. Use o arquivo original ou registre manualmente.") from None
    if not payload.get("ok"):
        raise ValueError(payload.get("error", "PDF não reconhecido."))
    return payload["exam"]


def validate_evaluation(payload):
    source = payload.get("source")
    if source not in ("manual", "inbody", "handymet"):
        raise ValueError("Origem do exame inválida.")
    stamp = bounded(payload.get("exam_at"), 16, "Data do exame", True)
    try:
        parsed = datetime.strptime(stamp, "%Y-%m-%dT%H:%M")
    except ValueError:
        raise ValueError("Informe a data e hora reais do exame.") from None
    if parsed > datetime.now(ZoneInfo("America/Sao_Paulo")).replace(tzinfo=None) or parsed.year < 1900:
        raise ValueError("A data do exame não pode estar no futuro ou antes de 1900.")
    raw = payload.get("fields")
    if not isinstance(raw, dict) or set(raw) - FIELDS.keys():
        raise ValueError("Medidas inválidas.")
    fields = {}
    for key, value in raw.items():
        if value is None or value == "":
            continue
        if isinstance(value, bool):
            raise ValueError("Medida inválida.")
        try:
            value = float(str(value).replace(",", "."))
        except ValueError:
            raise ValueError(f"{FIELDS[key][0]}: informe um número.") from None
        label, _, low, high = FIELDS[key]
        if not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f"{label}: valor fora do intervalo aceito ({low} a {high}).")
        fields[key] = value
    if not fields:
        raise ValueError("Informe pelo menos uma medida.")
    return {"exam_at": stamp, "source": source,
            "method": bounded(payload.get("method", ""), 120, "Método / aparelho", True),
            "professional": bounded(payload.get("professional", ""), 120, "Profissional", True),
            "notes": bounded(payload.get("notes", ""), 4000, "Observações"), "fields": fields}


def save_evaluation(conn, payload, actor, attachment=None):
    value = validate_evaluation(payload)
    patient_id = bounded(payload.get("patient_id"), 64, "Paciente", True)
    if not conn.execute("SELECT 1 FROM body_patients WHERE id=?", (patient_id,)).fetchone():
        raise ValueError("Paciente não encontrado nesta clínica.")
    if payload.get("confirmed") is not True:
        raise ValueError("Confira a identidade do paciente, a data e os valores antes de confirmar.")
    evaluation_id = payload.get("id")
    previous = None
    if evaluation_id:
        previous = conn.execute("SELECT * FROM body_evaluations WHERE id=? AND patient_id=?", (evaluation_id, patient_id)).fetchone()
        if not previous:
            raise ValueError("Avaliação não encontrada.")
        if conn.execute("SELECT 1 FROM body_exclusions WHERE evaluation_id=?", (evaluation_id,)).fetchone():
            raise ValueError("Esta avaliação foi excluída. Restaure antes de editar.")
        if payload.get("version") != previous["version"]:
            raise ValueError("Esta avaliação foi alterada por outra pessoa. Recarregue antes de editar.")
        if attachment or value["source"] != previous["source"]:
            raise ValueError("O documento e a origem de uma avaliação não podem ser trocados.")
    elif value["source"] != "manual" and not attachment:
        raise ValueError("Importe o PDF original para registrar este tipo de exame.")
    doc_id = previous["document_id"] if previous else None
    if attachment:
        data, extracted = attachment
        if value["source"] != extracted["source"]:
            raise ValueError("A origem não corresponde ao PDF.")
        doc_id = uuid4().hex
        try:
            conn.execute("INSERT INTO body_documents VALUES (?, ?, ?, ?, ?, ?)",
                         (doc_id, patient_id, hashlib.sha256(data).hexdigest(), data, now(), json.dumps(extracted, ensure_ascii=False)))
        except sqlite3.IntegrityError:
            raise ValueError("Este PDF já foi importado nesta clínica. Abra a avaliação existente ou restaure-a em Avaliações excluídas.") from None
    stamp = now()
    fields_json = json.dumps(value["fields"], ensure_ascii=False)
    if previous:
        result = conn.execute("""UPDATE body_evaluations SET exam_at=?, method=?, fields_json=?, notes=?, professional=?,
                                 updated_at=?, updated_by=?, version=version+1 WHERE id=? AND version=?""",
                              (value["exam_at"], value["method"], fields_json, value["notes"], value["professional"],
                               stamp, actor["id"], evaluation_id, previous["version"]))
        if result.rowcount != 1:
            raise ValueError("Avaliação alterada simultaneamente. Recarregue a ficha.")
    else:
        evaluation_id = uuid4().hex
        conn.execute("INSERT INTO body_evaluations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
                     (evaluation_id, patient_id, value["exam_at"], value["source"], value["method"], fields_json,
                      value["notes"], value["professional"], doc_id, stamp, stamp, actor["id"], actor["id"]))
    snapshot = {"before": dict(previous) if previous else None, "after": value,
                "actor_name": actor["nome"], "document_id": doc_id}
    conn.execute("INSERT INTO body_revisions(evaluation_id, actor_id, action, recorded_at, snapshot_json) VALUES (?, ?, ?, ?, ?)",
                 (evaluation_id, actor["id"], "edit" if previous else "create", stamp, json.dumps(snapshot, ensure_ascii=False)))
    return evaluation_id


def change_exclusion(conn, payload, actor, restore=False):
    evaluation_id = bounded(payload.get("id"), 64, "Avaliação", True)
    patient_id = bounded(payload.get("patient_id"), 64, "Paciente", True)
    if payload.get("confirmed") is not True:
        raise ValueError("Confirme a operação antes de continuar.")
    previous = conn.execute("""SELECT e.*, x.deleted_at FROM body_evaluations e
                               LEFT JOIN body_exclusions x ON x.evaluation_id=e.id
                               WHERE e.id=? AND e.patient_id=?""", (evaluation_id, patient_id)).fetchone()
    if not previous:
        raise ValueError("Avaliação não encontrada nesta ficha.")
    if payload.get("version") != previous["version"]:
        raise ValueError("Esta avaliação foi alterada. Recarregue a ficha antes de continuar.")
    if bool(previous["deleted_at"]) != restore:
        raise ValueError("Avaliação já excluída." if not restore else "Esta avaliação já está ativa.")
    stamp = now()
    result = conn.execute("""UPDATE body_evaluations SET updated_at=?, updated_by=?, version=version+1
                             WHERE id=? AND version=?""", (stamp, actor["id"], evaluation_id, previous["version"]))
    if result.rowcount != 1:
        raise ValueError("Avaliação alterada simultaneamente. Recarregue a ficha.")
    # Keep the assessment and original PDF intact; only its active state changes.
    if restore:
        conn.execute("DELETE FROM body_exclusions WHERE evaluation_id=?", (evaluation_id,))
    else:
        conn.execute("INSERT INTO body_exclusions VALUES (?, ?, ?)", (evaluation_id, stamp, actor["id"]))
    after = {**dict(previous), "fields": json.loads(previous["fields_json"]),
             "deleted_at": None if restore else stamp, "version": previous["version"] + 1,
             "updated_at": stamp, "updated_by": actor["id"]}
    after.pop("fields_json")
    snapshot = {"before": dict(previous), "after": after, "actor_name": actor["nome"],
                "document_id": previous["document_id"]}
    conn.execute("INSERT INTO body_revisions(evaluation_id, actor_id, action, recorded_at, snapshot_json) VALUES (?, ?, ?, ?, ?)",
                 (evaluation_id, actor["id"], "restore" if restore else "delete", stamp, json.dumps(snapshot, ensure_ascii=False)))
    return evaluation_id


def handle_request(handler, parsed, connect, experts_token=lambda: ""):
    """Called only after the session/clinic/module guards, inside clinic_context."""
    from urllib.parse import parse_qs

    query = parse_qs(parsed.query)
    clinic = query.get("clinic", [""])[0]
    can_delete = handler.server.auth_store.has_permission(handler.current_user["id"], clinic, "body_evolution.delete")
    action = parsed.path.removeprefix("/api/body/")
    try:
        payload = {}
        if handler.command == "POST":
            length = int(handler.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_PDF * 4 // 3 + 30000 or handler.headers.get_content_type() != "application/json":
                raise ValueError("Envie JSON válido com um PDF de até 8 MB.")
            payload = json.loads(handler.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("Solicitação inválida.")
        if action == "experts-link" and handler.command == "POST":
            from clinica_patient_link import handle_link
            return handle_link(handler, clinic, payload, connect, experts_token)
        attachment = None
        if handler.command == "POST" and action in ("import", "evaluation") and payload.get("pdf"):
            data = decode_pdf(payload["pdf"])
            attachment = data, extract_pdf(data)
        if action == "import" and handler.command == "POST":
            if not attachment:
                raise ValueError("Selecione um PDF.")
            return handler.auth_json({"ok": True, "exam": attachment[1]})
        with connect() as conn:
            initialize(conn)
            if handler.command == "GET" and action == "patients":
                data = {"patients": search_patients(conn, query.get("q", [""])[0], query.get("registered", [""])[0] == "1"),
                        "catalog": FIELDS, "synced_patients": conn.execute("SELECT COUNT(*) FROM clinica_patients").fetchone()[0]}
            elif handler.command == "GET" and action == "patient":
                data = patient_detail(conn, query.get("id", [""])[0], include_deleted=can_delete)
            elif handler.command == "POST" and action == "enroll":
                data = {"id": enroll(conn, payload.get("experts_uuid"), handler.current_user)}
            elif handler.command == "POST" and action == "evaluation":
                permission = "body_evolution.edit" if payload.get("id") else "body_evolution.create"
                clinic = query.get("clinic", [""])[0]
                if not handler.require_permission(clinic, permission):
                    return
                data = {"id": save_evaluation(conn, payload, handler.current_user, attachment)}
            elif handler.command == "POST" and action in ("delete", "restore"):
                if not handler.require_permission(clinic, "body_evolution.delete"):
                    return
                data = {"id": change_exclusion(conn, payload, handler.current_user, restore=action == "restore")}
            elif handler.command == "GET" and action == "document":
                row = conn.execute("""SELECT d.content FROM body_documents d
                                      JOIN body_evaluations e ON e.document_id=d.id
                                      WHERE d.id=? AND d.patient_id=?
                                      AND NOT EXISTS (SELECT 1 FROM body_exclusions x WHERE x.evaluation_id=e.id)""",
                                   (query.get("id", [""])[0], query.get("patient", [""])[0])).fetchone()
                if not row:
                    return handler.auth_json({"ok": False, "error": "Documento não encontrado."}, 404)
                handler.send_response(200)
                handler.send_header("Content-Type", "application/pdf")
                handler.send_header("Content-Disposition", 'attachment; filename="exame.pdf"')
                handler.send_header("Content-Security-Policy", "sandbox")
                handler.send_header("Content-Length", str(len(row[0])))
                handler.end_headers()
                handler.wfile.write(row[0])
                return
            elif handler.command == "GET" and action == "revisions":
                rows = conn.execute("""SELECT r.action, r.recorded_at, r.snapshot_json FROM body_revisions r
                                       JOIN body_evaluations e ON e.id=r.evaluation_id WHERE e.patient_id=? AND e.id=?
                                       AND (? OR NOT EXISTS (SELECT 1 FROM body_exclusions x WHERE x.evaluation_id=e.id))
                                       ORDER BY r.id DESC""",
                                    (query.get("patient", [""])[0], query.get("id", [""])[0], can_delete)).fetchall()
                data = {"revisions": [{"action": row[0], "recorded_at": row[1], **json.loads(row[2])} for row in rows]}
            else:
                return handler.auth_json({"ok": False, "error": "Rota não encontrada."}, 404)
        return handler.auth_json({"ok": True, **data})
    except (ValueError, TypeError) as exc:
        return handler.auth_json({"ok": False, "error": str(exc)}, 400)
    except sqlite3.Error:
        return handler.auth_json({"ok": False, "error": "Não foi possível salvar ou consultar a ficha. Tente novamente."}, 503)
