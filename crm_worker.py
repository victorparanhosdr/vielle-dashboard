"""Run explicitly: python crm_worker.py. One shared persistent volume, no replicas."""
import os
import logging
from pathlib import Path
import sqlite3
import threading
import time

from crm_gateway import EvolutionGateway, GatewayError, campaigns_enabled, sends_enabled
from crm_store import CrmStore, SEND_FAILURES


def tick(store, gateway, now=None, *, allow_campaigns=True):
    from crm_followup_runtime import advance
    advance(store,now)
    item = store.claim(now, allow_campaigns=allow_campaigns)
    if item is None:
        return False
    try:
        with store.db() as c:
            if item.get("followup_cycle_id"):
                from crm_followup_runtime import message_allowed
                if not message_allowed(c,store,item,time.time() if now is None else now):
                    c.execute("UPDATE messages SET status='queued' WHERE id=? AND status='sending'",(item["id"],))
                    return True
            if item.get("bot_run_id"):
                from crm_bots import message_allowed
                if not message_allowed(c, item["bot_run_id"],store,now):
                    deferred=c.execute("SELECT 1 FROM bot_runs r JOIN crm_followup_bot_steps f ON f.run_id=r.id WHERE r.id=? AND r.status='sending'",(item["bot_run_id"],)).fetchone()
                    c.execute("UPDATE messages SET status=?,skip_reason=? WHERE id=? AND status='sending'", ("queued" if deferred else "cancelled","" if deferred else "Automacao interrompida",item["id"]))
                    return True
            current = c.execute("""SELECT coalesce(ca.status,'running') status,ct.opted_out,coalesce(ps.phone,ct.phone) phone,ct.experts_status,ch.state FROM messages m
                LEFT JOIN campaigns ca ON ca.id=m.campaign_id JOIN contacts ct ON ct.id=m.contact_id
                LEFT JOIN contact_phone_settings ps ON ps.contact_id=ct.id
                JOIN channels ch ON ch.id=m.channel_id WHERE m.id=?""", (item["id"],)).fetchone()
            if current["status"] != "running" or current["opted_out"] or not current["phone"] or current["state"] != "open":
                status = "skipped" if current["opted_out"] or not current["phone"] else "cancelled" if current["status"] == "cancelled" else "queued"
                c.execute("UPDATE messages SET status=? WHERE id=?", (status, item["id"]))
                return True
            if (item["campaign_id"] or item.get("bot_run_id")) and (current["experts_status"] == "review" or current["phone"] != item["phone"]):
                c.execute("UPDATE messages SET status='skipped',skip_reason='Cadastro ou telefone alterado; confira o contato' WHERE id=?", (item["id"],))
                return True
        if item.get("media_id"):
            from crm_media import for_send
            item["media"] = for_send(store, item)
        provider_id = gateway.send(item)
    except Exception as error:
        code = error.code if isinstance(error, GatewayError) else "timeout" if isinstance(error, TimeoutError) else "unconfirmed"
        confirmed = store.finish(item, error=True, error_code=code)
        if not confirmed:
            logging.warning("CRM WhatsApp send confirmation unavailable: %s", code if code in SEND_FAILURES else "unconfirmed")
    else:
        # A database/finalization error is not a second provider send failure.
        store.finish(item, provider_id)
    return True


def run(auth, stop):
    from crm_api import store_for
    store = None
    gateway = EvolutionGateway()
    webhook_next = {}
    delay = 2
    while not stop.wait(delay):
        try:
            if not sends_enabled():
                delay = 2
                continue
            if store is None:
                store = store_for(auth)
                from crm_followup_runtime import permitted
                store.followup_permissions=permitted(store,auth)
            store.heartbeat()
            with store.db() as c:
                channels = [dict(r) for r in c.execute("SELECT * FROM channels WHERE state='open'")]
            for channel in channels:
                if webhook_next.get(channel["id"], 0) > time.time():
                    continue
                try:
                    gateway.configure_webhook(channel)
                    webhook_next[channel["id"]] = float("inf")
                except ValueError:
                    webhook_next[channel["id"]] = time.time() + 300
                    logging.warning("CRM WhatsApp webhook configuration pending")
            tick(store, gateway, allow_campaigns=campaigns_enabled() or store.campaigns_allowed())
            delay = 2
        except sqlite3.OperationalError as exc:
            code = getattr(exc, "sqlite_errorcode", None)
            if code is None or (code & 0xff) not in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
                raise
            # Retry the worker cycle, never reset or replay an in-flight message.
            delay = min(delay * 2, 30)
            logging.warning("CRM WhatsApp worker waiting for database; retrying in %s seconds", delay)


if __name__ == "__main__":
    if not sends_enabled():
        raise SystemExit("Worker desativado. Configure o gateway e CRM_SEND_ENABLED=1.")
    from auth_store import AuthStore, auth_database_path
    auth = AuthStore(auth_database_path(Path(__file__).resolve().parent))
    run(auth, threading.Event())
