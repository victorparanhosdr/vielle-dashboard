"""Bounded, per-conversation recovery of outgoing messages stored by Evolution."""
import time

from crm_store import whatsapp_time


def sync_conversation(store, gateway, payload):
    doctor, channel, contact = (payload.get(k) for k in ("doctor_id", "channel_id", "contact_id"))
    now = time.time()
    with store.db() as c:
        c.execute("BEGIN IMMEDIATE")
        ch, ct = store.conversation_contact(c, doctor, channel, contact)
        if ch["state"] != "open":
            raise ValueError("Numero desconectado. Historico local preservado.")
        number = store.conversation_peer(c, channel, contact, ct["phone"])
        key = "message_sync:" + channel + ":" + contact
        previous = c.execute("SELECT value FROM runtime WHERE key=?", (key,)).fetchone()
        if previous and now - previous[0] < 30:
            return {"imported": 0, "cached": True, "limited": False}
        c.execute("INSERT INTO runtime(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, now))
    records, limited = gateway.history(ch, number)
    records = [r for r in records if isinstance(r, dict)]
    records.sort(key=lambda r: whatsapp_time(r.get("messageTimestamp"), now))
    imported = sum(bool(store.receive(ch["instance"], row, "messages.upsert", expected_contact=contact, history=True)) for row in records)
    return {"imported": imported, "cached": False, "limited": limited}
