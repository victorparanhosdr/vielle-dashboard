"""Explicit, clinic-scoped links in the Experts patient annotation (not prontuario)."""

import hashlib
import http.client
import ipaddress
import json
import os
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from uuid import UUID, uuid4

from body_evolution import now


API_ROOT = "https://api.clinicaexperts.com.br/api/v1/patients/"
_locks = [threading.Lock() for _ in range(32)]


class LinkError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ExpertsPatients:
    def __init__(self, token):
        if not token:
            raise LinkError("A API do Clínica Experts não está configurada nesta clínica.", 503)
        self.token = token

    def request(self, uuid, annotation=None, *, name=None):
        try:
            uuid = str(UUID(uuid))
        except (ValueError, TypeError, AttributeError):
            raise LinkError("O cadastro não possui um UUID válido do Clínica Experts.") from None
        body = None
        if annotation is not None:
            if not isinstance(name, str) or not name.strip():
                raise LinkError("Não foi possível conferir o nome atual do paciente para preservar o cadastro.")
            # The live API requires name even when only the annotation changes.
            body = json.dumps({"name": name, "annotation": annotation}).encode()
        request = urllib.request.Request(
            API_ROOT + uuid, data=body, method="GET" if body is None else "PUT",
            headers={"Authorization": "Bearer " + self.token, "Accept": "application/json",
                     "Content-Type": "application/json", "User-Agent": "DOC4DOCS/1.0"})
        try:
            # Never replay writes after a timeout or forward credentials through a redirect.
            with urllib.request.build_opener(NoRedirect()).open(request, timeout=20) as response:
                content = response.read(1024 * 1024 + 1)
                if len(content) > 1024 * 1024:
                    raise ValueError("Response too large")
                return json.loads(content) if content else {}
        except urllib.error.HTTPError as exc:
            messages = {401: "A chave do Clínica Experts foi recusada.",
                        403: "A chave não tem permissão para esta operação no Clínica Experts.",
                        404: "Paciente não encontrado no Clínica Experts desta clínica.",
                        422: "O Clínica Experts não aceitou a anotação.",
                        429: "O Clínica Experts atingiu o limite de requisições. Tente mais tarde."}
            raise LinkError(messages.get(exc.code, "O Clínica Experts não confirmou a operação."), 502) from None
        except (OSError, ValueError, http.client.HTTPException):
            raise LinkError("Não foi possível confirmar a resposta do Clínica Experts. Confira o vínculo antes de tentar novamente.", 502) from None


def public_link(clinic, patient_id):
    origin = os.environ.get("BODY_EVOLUTION_PUBLIC_ORIGIN") or os.environ.get("APP_BASE_URL", "")
    try:
        parsed = urllib.parse.urlsplit(origin.strip())
        host = parsed.hostname or ""
        port = parsed.port
    except ValueError:
        raise LinkError("O domínio público configurado para o DOC4DOCS é inválido.", 503) from None
    try:
        local_ip = not ipaddress.ip_address(host).is_global
    except ValueError:
        local_ip = False
    if (parsed.scheme != "https" or not host or "." not in host or local_ip
            or host.endswith((".localhost", ".local", ".internal"))
            or parsed.username or parsed.password or port not in (None, 443)
            or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
        raise LinkError("Configure BODY_EVOLUTION_PUBLIC_ORIGIN com o domínio HTTPS publicado do DOC4DOCS antes de vincular pacientes.", 503)
    return f"https://{host}/body-evolution.html?" + urllib.parse.urlencode({"clinic": clinic, "patient": patient_id})


def initialize(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS body_experts_link_writes (
        id TEXT PRIMARY KEY, patient_id TEXT NOT NULL REFERENCES body_patients(id),
        experts_uuid TEXT NOT NULL, actor_id INTEGER NOT NULL, url TEXT NOT NULL,
        before_annotation TEXT NOT NULL, after_annotation TEXT NOT NULL,
        status TEXT NOT NULL, created_at TEXT NOT NULL, verified_at TEXT
    )""")


def read_patient(client, uuid):
    patient = client.request(uuid)
    if isinstance(patient, dict) and isinstance(patient.get("data"), dict):
        patient = patient["data"]
    if (not isinstance(patient, dict) or patient.get("uuid") != uuid
            or not isinstance(patient.get("name"), str) or not patient["name"].strip()
            or "annotation" not in patient
            or patient["annotation"] is not None and not isinstance(patient["annotation"], str)):
        raise LinkError("Não foi possível conferir o paciente e sua anotação atual. Nenhum novo texto foi enviado.", 502)
    return patient


def revision(patient, link):
    return hashlib.sha256(json.dumps([patient["uuid"], patient["name"], patient["annotation"],
                                     patient.get("updated_at"), link], ensure_ascii=False).encode()).hexdigest()


def has_link(annotation, link):
    return bool(re.search(re.escape(link) + r"(?=$|[\s<>\"'])", annotation.replace("&amp;", "&")))


def handle_link(handler, clinic, payload, connect, token):
    if not handler.require_permission(clinic, "body_evolution.edit"):
        return
    try:
        mode = payload.get("mode")
        patient_id = payload.get("patient_id")
        if mode not in ("preview", "save") or not isinstance(patient_id, str):
            raise LinkError("Solicitação de vínculo inválida.")
        if mode == "save" and payload.get("confirmed") is not True:
            raise LinkError("Confira o paciente e confirme a gravação do link.")
        with connect() as conn:
            row = conn.execute("""SELECT p.experts_uuid, COALESCE(c.name,p.name) AS name
                FROM body_patients p JOIN clinica_patients c ON c.uuid=p.experts_uuid WHERE p.id=?""", (patient_id,)).fetchone()
        if not row:
            raise LinkError("Paciente não encontrado na base sincronizada desta clínica.", 404)
        uuid = row["experts_uuid"]
        link = public_link(clinic, patient_id)
        addition = "DOC4DOCS - Evolução corporal\n" + link
        client = ExpertsPatients(token())
        lock = _locks[int(hashlib.sha256((clinic + uuid).encode()).hexdigest(), 16) % len(_locks)]
        if not lock.acquire(blocking=False):
            raise LinkError("Já existe uma conferência deste vínculo em andamento. Aguarde e tente novamente.", 409)
        try:
            patient = read_patient(client, uuid)
            before = patient["annotation"] or ""
            linked = has_link(before, link)
            if mode == "preview":
                return handler.auth_json({"ok": True, "patient_id": patient_id, "local_name": row["name"],
                    "experts_name": patient["name"], "annotation": before, "addition": addition,
                    "url": link, "revision": revision(patient, link), "linked": linked})
            if linked:
                return handler.auth_json({"ok": True, "linked": True, "already_linked": True})
            if payload.get("revision") != revision(patient, link):
                raise LinkError("O cadastro foi alterado no Clínica Experts. Feche e abra o vínculo novamente para conferir o texto atualizado.", 409)
            after = before + ("\n\n" if before else "") + addition
            if len(after) > 100000:
                raise LinkError("A anotação é muito extensa para uma atualização automática segura.")
            attempt = uuid4().hex
            # Commit the backup before external I/O; do not hold a SQLite write lock over HTTP.
            with connect() as conn:
                initialize(conn)
                conn.execute("INSERT INTO body_experts_link_writes VALUES (?,?,?,?,?,?,?,?,?,NULL)",
                    (attempt, patient_id, uuid, handler.current_user["id"], link, before, after, "pending", now()))
            try:
                client.request(uuid, after, name=patient["name"])
                verified = read_patient(client, uuid)
                if verified["annotation"] != after or verified["name"] != patient["name"]:
                    raise LinkError("O texto retornado difere do esperado. Confira a anotação no Clínica Experts; não houve nova tentativa automática.", 502)
            except LinkError:
                with connect() as conn:
                    conn.execute("UPDATE body_experts_link_writes SET status='unconfirmed' WHERE id=?", (attempt,))
                raise
            with connect() as conn:
                conn.execute("UPDATE body_experts_link_writes SET status='verified', verified_at=? WHERE id=?", (now(), attempt))
            handler.server.auth_store.audit_event("body_experts_link", actor_id=handler.current_user["id"],
                                                  details={"clinic": clinic, "patient_id": patient_id, "attempt_id": attempt})
            return handler.auth_json({"ok": True, "linked": True, "already_linked": False})
        finally:
            lock.release()
    except LinkError as exc:
        return handler.auth_json({"ok": False, "error": str(exc)}, exc.status)
