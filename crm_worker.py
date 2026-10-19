"""Run explicitly: python crm_worker.py. One shared persistent volume, no replicas."""
import os
from pathlib import Path
import threading
import time

from crm_gateway import EvolutionGateway, campaigns_enabled, sends_enabled
from crm_store import CrmStore


def tick(store, gateway, now=None, *, allow_campaigns=True):
    item = store.claim(now, allow_campaigns=allow_campaigns)
    if item is None:
        return False
    try:
        with store.db() as c:
            current = c.execute("""SELECT coalesce(ca.status,'running') status,ct.opted_out,ch.state FROM messages m
                LEFT JOIN campaigns ca ON ca.id=m.campaign_id JOIN contacts ct ON ct.id=m.contact_id
                JOIN channels ch ON ch.id=m.channel_id WHERE m.id=?""", (item["id"],)).fetchone()
            if current["status"] != "running" or current["opted_out"] or current["state"] != "open":
                status = "skipped" if current["opted_out"] else "cancelled" if current["status"] == "cancelled" else "queued"
                c.execute("UPDATE messages SET status=? WHERE id=?", (status, item["id"]))
                return True
        store.finish(item, gateway.send(item))
    except Exception:
        store.finish(item, error=True)
    return True


def run(auth, stop):
    store = CrmStore(auth.path.parent / "crm" / "vielle")
    gateway = EvolutionGateway()
    while not stop.wait(2):
        if sends_enabled():
            store.heartbeat()
            tick(store, gateway, allow_campaigns=campaigns_enabled())


if __name__ == "__main__":
    if not sends_enabled():
        raise SystemExit("Worker desativado. Configure o gateway e CRM_SEND_ENABLED=1.")
    from auth_store import AuthStore, auth_database_path
    auth = AuthStore(auth_database_path(Path(__file__).resolve().parent))
    run(auth, threading.Event())
