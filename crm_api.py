"""Master-only Vielle pilot; webhooks use an independent secret, never cookies."""
import hmac
import json
import logging
import os
import sqlite3
import threading
from urllib.parse import parse_qs

from crm_gateway import EvolutionGateway, campaigns_enabled, sends_enabled
from crm_store import Conflict, CrmStore
from task_api import read_body

STORES, LOCK = {}, threading.Lock()


def store_for(auth):
    root = auth.path.parent / "crm" / "vielle"
    with LOCK:
        if root not in STORES:
            from app import DB_PATH, DOCTOR_PROFESSIONALS
            from crm_patients import PatientDirectory
            from crm_commercial import PatientCommercial
            from crm_appointments import PatientAppointments
            directory = PatientDirectory(DB_PATH)
            history = PatientCommercial(DB_PATH, DOCTOR_PROFESSIONALS)
            appointments = PatientAppointments(DB_PATH, DOCTOR_PROFESSIONALS)
            STORES[root] = CrmStore(root, directory.lookup, history.lookup, appointments, history, patient_directory=directory)
        return STORES[root]


def webhook(handler):
    expected = os.getenv("CRM_WEBHOOK_SECRET", "")
    received = handler.headers.get("X-DOC4DOCS-Webhook", "")
    if len(expected) < 32 or not hmac.compare_digest(expected.encode(), received.encode()):
        return handler.auth_json({"ok": False, "error": "Webhook nao autorizado."}, 401)
    try:
        p = json.loads(read_body(handler, 256 * 1024))
        if not isinstance(p, dict) or not isinstance(p.get("data"), dict) or not isinstance(p.get("instance"), str):
            raise ValueError()
        store_for(handler.server.auth_store).receive(p["instance"], p["data"], p.get("event", ""))
        return handler.auth_json({"ok": True})
    except LookupError:
        return handler.auth_json({"ok": False}, 404)
    except (ValueError, TypeError, UnicodeError):
        return handler.auth_json({"ok": False}, 400)


def handle_request(handler, parsed):
    # Repeated here so direct dispatch cannot bypass the pilot's access boundary.
    query = parse_qs(parsed.query, keep_blank_values=True)
    if query.get("clinic") != ["vielle"] or not handler.current_user.get("is_master"):
        return handler.auth_json({"ok": False, "error": "Piloto disponivel apenas para o Master da Vielle."}, 403)
    store = store_for(handler.server.auth_store)
    action = parsed.path.removeprefix("/api/crm").strip("/")
    try:
        if action.startswith("voice/"):
            from crm_voice_api import handle_request as handle_voice
            return handle_voice(handler, store, action, query)
        doctor = query.get("doctor", [""])[0]
        if handler.command == "GET":
            if action == "avatar":
                from crm_avatars import serve
                return serve(handler, store, doctor, query.get("contact", [""])[0], query.get("channel", [""])[0])
            if action == "kommo-import":
                from crm_kommo_import import status
                return handler.auth_json({"ok": True, **status(store, doctor, query.get("pipeline", [""])[0],
                    query.get("id", [""])[0], query.get("page", [1])[0])})
            filters = json.loads(query["filters"][0]) if action in {"", "inbox", "kanban-page"} and "filters" in query else {}
            if not action:
                return handler.auth_json({"ok": True, **store.listing(doctor, filters, kanban_pipeline=query.get("pipeline", [""])[0] if query.get("view") == ["kanban"] else None), "gateway_configured": EvolutionGateway().configured,
                                          "sends_enabled": sends_enabled() and store.worker_online(),
                                          "campaigns_enabled": sends_enabled() and (campaigns_enabled() or store.campaigns_allowed()) and store.worker_online(),
                                          "can_enable_campaigns": sends_enabled() and store.worker_online()})
            if action == "kanban-page":
                return handler.auth_json({"ok": True, **store.kanban_page(doctor, query.get("pipeline", [""])[0],
                    query.get("stage", [""])[0], filters, query.get("cursor", [""])[0])})
            if action == "lead":
                return handler.auth_json({"ok": True, **store.detail(query.get("id", [""])[0], doctor)})
            if action == "campaign-dashboard":
                from crm_campaigns import dashboard
                return handler.auth_json({"ok": True, **dashboard(store, doctor, query)})
            if action == "campaign-detail":
                from crm_campaigns import detail
                return handler.auth_json({"ok": True, **detail(store, doctor, query.get("id", [""])[0], query)})
            if action == "patient-refresh":
                from crm_patient_refresh import status
                job = status(store)
                detail = store.detail(query.get("id", [""])[0], doctor)
                return handler.auth_json({**job, "lead": detail["lead"]})
            if action in {"patients", "patient-directory-refresh"}:
                with store.db() as c:
                    store.row(c, "doctors", doctor)
                if action == "patient-directory-refresh":
                    from crm_patient_refresh import status
                    return handler.auth_json(status(store))
                if store.patient_directory is None:
                    raise ValueError("Base de pacientes indisponivel neste ambiente.")
                return handler.auth_json({"ok": True, **store.patient_directory.search(query.get("search", [""])[0], query.get("page", [1])[0])})
            if action == "commercial":
                return handler.auth_json({"ok": True, **store.commercial(query.get("id", [""])[0], doctor,
                    query.get("scope", ["doctor"])[0], query.get("kind", ["sales"])[0], query.get("page", [1])[0])})
            if action == "appointments":
                return handler.auth_json({"ok": True, **store.appointments(query.get("id", [""])[0], doctor,
                    query.get("scope", ["doctor"])[0], query.get("period", ["all"])[0], query.get("page", [1])[0])})
            if action == "inbox":
                return handler.auth_json({"ok": True, **store.inbox(doctor, handler.current_user["id"],
                    query.get("channel", [""])[0], query.get("kind", ["all"])[0], query.get("search", [""])[0], filters)})
            if action == "conversation":
                return handler.auth_json({"ok": True, **store.conversation(doctor, query.get("channel", [""])[0],
                    query.get("contact", [""])[0], query.get("before", [0])[0])})
            raise LookupError()
        if handler.command != "POST":
            return handler.auth_json({"ok": False}, 405)
        if not handler.same_origin_request():
            return handler.auth_json({"ok": False}, 403)
        limit=512*1024 if action in {"campaign-candidates","campaign-preview","campaign-create"} else 128*1024
        p = json.loads(read_body(handler, limit))
        if not isinstance(p, dict):
            raise ValueError("Solicitacao invalida.")
        actor = handler.current_user["nome"]
        result = {}
        if action == "bootstrap":
            from app import PIPELINE_DOCTOR_MAP
            structure = {}
            for name, doc in PIPELINE_DOCTOR_MAP.items():
                structure.setdefault(doc, []).append(name)
            store.bootstrap(structure)
        elif action == "avatar-refresh":
            from crm_avatars import refresh
            result.update(refresh(store, EvolutionGateway(), p.get("doctor_id"), p.get("contact_id"), p.get("channel_id", "")))
        elif action in {"kommo-import-preview", "kommo-import-commit"}:
            from crm_kommo_import import prepare, commit
            if action.endswith("preview"):
                result.update(prepare(store, p.get("doctor_id"), p.get("pipeline_id")))
            else:
                result.update(commit(store, p.get("doctor_id"), p.get("pipeline_id"), p.get("id"), actor))
        elif action == "doctor":
            result["id"] = store.doctor(p)
        elif action == "pipeline":
            result["id"] = store.pipeline(p)
        elif action == "channel":
            result["id"] = store.channel(p)
        elif action in {"connect", "channel-state"}:
            with store.db() as c:
                ch = store.owned(c, "channels", p.get("id"), p.get("doctor_id"))
            gateway = EvolutionGateway()
            if action == "connect":
                result.update(gateway.connect(ch))
            else:
                state = gateway.state(ch)
                if state not in {"open", "close", "connecting"}:
                    raise ValueError("Estado de conexao desconhecido.")
                store.receive(ch["instance"], {"state": state}, "connection.update")
                result["state"] = state
        elif action == "lead":
            result["id"] = store.lead(p, actor)
        elif action == "patient-refresh":
            from crm_patient_refresh import start_refresh
            with store.db() as c:
                store.owned(c, "leads", p.get("id"), p.get("doctor_id"))
            result.update(start_refresh(store))
        elif action == "patient-directory-refresh":
            from crm_patient_refresh import start_refresh
            with store.db() as c:
                store.row(c, "doctors", p.get("doctor_id"))
            result.update(start_refresh(store))
        elif action == "activity":
            store.activity(p, actor)
        elif action == "service-location":
            store.save_service_location(p, actor)
        elif action == "conversation-close":
            result.update(store.close_conversation(p, handler.current_user["id"]))
        elif action == "conversation-sync":
            from crm_message_sync import sync_conversation
            result.update(sync_conversation(store, EvolutionGateway(), p))
        elif action == "customer-tiers":
            store.save_customer_tier_rules(p)
        elif action == "reply":
            result["id"] = store.reply(p, handler.current_user["id"], sends_enabled() and store.worker_online(), sender_name=actor)
        elif action == "campaign":
            result["id"] = store.campaign(p, actor, handler.current_user["id"])
        elif action == "campaign-preview":
            from crm_campaigns import audience
            result.update(audience(store, p, handler.current_user))
        elif action == "campaign-candidates":
            from crm_campaigns import candidates
            result.update(candidates(store, p))
        elif action == "campaign-create":
            from crm_campaigns import create
            result["id"] = create(store, p, handler.current_user)
        elif action == "campaign-enable":
            if p.get("confirm") is not True or not sends_enabled() or not store.worker_online():
                raise ValueError("Confirme a ativacao com o WhatsApp conectado e o servidor de envios disponivel.")
            store.enable_campaigns()
        elif action == "campaign-status":
            store.campaign_status(p.get("id"), p.get("doctor_id"), p.get("action"), sends_enabled() and (campaigns_enabled() or store.campaigns_allowed()) and store.worker_online())
        else:
            raise LookupError()
        handler.server.auth_store.audit_event("crm_" + action, handler.current_user["id"], details={"clinic": "vielle", "doctor_id": p.get("doctor_id", ""), "id": result.get("id", p.get("id", "")),
            **({"channel_id": p.get("channel_id", ""), "contact_id": p.get("contact_id", "")} if action in {"conversation-close", "conversation-sync"} else {})})
        return handler.auth_json({"ok": True, **result})
    except Conflict as e:
        return handler.auth_json({"ok": False, "error": str(e)}, 409)
    except sqlite3.IntegrityError:
        return handler.auth_json({"ok": False, "error": "Ja existe um registro com estes dados ou ha um vinculo invalido."}, 400)
    except (ValueError, TypeError, KeyError, OverflowError, UnicodeError) as e:
        return handler.auth_json({"ok": False, "error": str(e) if isinstance(e, ValueError) else "Dados invalidos."}, 400)
    except LookupError:
        return handler.auth_json({"ok": False, "error": "Registro nao encontrado."}, 404)
    except Exception:
        if action.startswith("voice/"):
            return handler.auth_json({"ok": False, "error": "Telefonia temporariamente indisponivel. Confira o historico antes de tentar novamente."}, 503)
        logging.exception("CRM operation failed")
        return handler.auth_json({"ok": False, "error": "Nao foi possivel concluir a operacao."}, 503)
