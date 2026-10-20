"""CRM-scoped automation editing. Activation is explicit and Master-only."""
import json

from crm_bots import BotStore, clean
from task_api import read_body


def handle(handler, crm, action, query, user):
    bots = BotStore(crm)
    doctor = query.get("doctor", [""])[0]
    ident = query.get("id", [""])[0]
    if handler.command == "GET":
        if action == "bots":
            return handler.auth_json({"ok": True, **bots.listing(doctor)})
        if action == "bots/item":
            return handler.auth_json({"ok": True, "bot": bots.detail(ident, doctor)})
        if action == "bots/history":
            return handler.auth_json({"ok": True, "runs": bots.history(ident, doctor)})
        raise LookupError()
    if handler.command != "POST":
        return handler.auth_json({"ok": False}, 405)
    if not handler.same_origin_request():
        return handler.auth_json({"ok": False}, 403)
    p = json.loads(read_body(handler, 256*1024))
    if not isinstance(p, dict):
        raise ValueError("Solicitacao invalida.")
    doctor, ident = p.get("doctor_id"), p.get("id")
    if action == "bots/save":
        result = {"bot": bots.save(p, user["id"])}
    elif action == "bots/publish":
        if not user.get("is_master"):
            raise PermissionError("Somente o Master pode ativar ou pausar um robo.")
        if p.get("active") is True and p.get("confirm") is not True:
            raise ValueError("Confirme a ativacao. Somente novos eventos serao processados.")
        bots.publish(ident, doctor, p.get("revision"), p.get("active") is True, user["id"])
        result = {"bot": bots.detail(ident, doctor)}
    elif action == "bots/start":
        result = {"run_id": bots.start(ident, doctor, p.get("lead_id"), p.get("request_id"))}
    elif action == "bots/cancel":
        with crm.db() as c:
            c.execute("BEGIN IMMEDIATE")
            bots.owned(c, ident, doctor)
            run = c.execute("SELECT id FROM bot_runs WHERE id=? AND bot_id=?", (p.get("run_id"), ident)).fetchone()
            if not run:
                raise LookupError()
            import time
            bots.cancel(c, "id=?", (run[0],), "Interrompido pela equipe", time.time())
        result = {}
    elif action == "bots/task-done":
        with crm.db() as c:
            row = c.execute("SELECT * FROM bot_tasks WHERE id=?", (ident,)).fetchone()
            if not row:
                raise LookupError()
            crm.owned(c, "leads", row["lead_id"], doctor)
            c.execute("UPDATE bot_tasks SET status='done' WHERE id=?", (ident,))
        result = {}
    else:
        raise LookupError()
    handler.server.auth_store.audit_event("crm_" + action.replace("/", "_"), user["id"], details={"doctor_id": doctor, "id": ident or result.get("bot", {}).get("id", "")})
    return handler.auth_json({"ok": True, **result})
