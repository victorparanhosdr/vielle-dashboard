"""Keep bounded ad referrals; resolve ad metadata without exporting CRM data."""
import json
import logging
import re
import time
from urllib.parse import parse_qs, urlsplit

from crm_attribution import UTM_KEYS, clean_url, value


def schema(c):
    c.executescript("""
        CREATE TABLE IF NOT EXISTS whatsapp_ad_receipts(
            message_id TEXT PRIMARY KEY REFERENCES messages(id),source_id TEXT NOT NULL,
            created_at REAL NOT NULL,data TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS whatsapp_ad_receipts_source ON whatsapp_ad_receipts(source_id);
        CREATE TABLE IF NOT EXISTS meta_ad_lookups(
            account_id TEXT NOT NULL,source_id TEXT NOT NULL,checked_at REAL NOT NULL,
            data TEXT NOT NULL,PRIMARY KEY(account_id,source_id));
        CREATE TABLE IF NOT EXISTS meta_campaign_catalog(
            account_id TEXT NOT NULL,campaign_id TEXT NOT NULL,name TEXT NOT NULL,status TEXT NOT NULL,
            synced_at REAL NOT NULL,PRIMARY KEY(account_id,campaign_id));
    """)


def bounded(raw, maximum=250):
    try:
        return value(raw, maximum)
    except ValueError:
        return ""


def capture(message):
    # Only the current message context, never quoted messages or URLs in its text.
    for _ in range(5):
        if not isinstance(message, dict):
            return {}
        for kind in ("extendedTextMessage", "imageMessage", "videoMessage", "documentMessage", "audioMessage"):
            part = message.get(kind)
            context = part.get("contextInfo") if isinstance(part, dict) else None
            if isinstance(context, dict) and (context.get("isForwarded") is True or context.get("forwardingScore", 0)):
                continue
            ad = context.get("externalAdReply") if isinstance(context, dict) else None
            if not isinstance(ad, dict):
                continue
            click = bounded(ad.get("ctwaClid", ""), 500)
            source_type = bounded(ad.get("sourceType", "")).lower()
            if not click and source_type != "ad" and ad.get("showAdAttribution") is not True:
                continue
            source_id = bounded(ad.get("sourceId", ""), 100)
            raw_url = bounded(ad.get("sourceUrl", ""), 4000)
            url = clean_url(raw_url)
            utm = {}
            if url:
                query = parse_qs(urlsplit(raw_url).query, max_num_fields=100)
                utm = {key: val for key in UTM_KEYS if (val := bounded(query.get(key, [""])[0]))}
            return {"version": 2, "kind": "whatsapp_ad", "utm": utm,
                    "sources": {key: "whatsapp_referral_url" for key in utm},
                    "click_ids": {"ctwa_clid": click} if click else {},
                    "meta": {"source_id": source_id, "source_url": url,
                             "headline": bounded(ad.get("title", "")), "status": "pending"}}
        wrapper = next((message[k] for k in ("ephemeralMessage", "viewOnceMessage", "viewOnceMessageV2", "documentWithCaptionMessage")
                        if isinstance(message.get(k), dict)), {})
        message = wrapper.get("message")
    return {}


def record(c, message, channel, target, raw):
    try:
        data = capture(raw)
    except ValueError:
        # Malformed tracking must not prevent delivery of the customer's message.
        return
    if not data:
        return
    source_id = data["meta"]["source_id"]
    c.execute("INSERT OR IGNORE INTO whatsapp_ad_receipts VALUES(?,?,?,?)",
              (message["id"], source_id, message["created_at"], json.dumps(data, ensure_ascii=False)))
    candidates = c.execute("SELECT id,pipeline_id FROM leads WHERE contact_id=? AND doctor_id=? AND deleted_at=0",
                           (message["contact_id"], channel["doctor_id"])).fetchall()
    matching = [row for row in candidates if target and row["pipeline_id"] == target["pipeline_id"]]
    candidates = matching or candidates
    # Never guess which opportunity an ad belongs to when multiple are possible.
    if len(candidates) == 1:
        c.execute("INSERT OR IGNORE INTO lead_attributions VALUES(?,?,?,?,?,?)",
                  ("wa:" + message["id"], candidates[0]["id"], "", "WhatsApp - " + channel["name"],
                   message["created_at"], json.dumps(data, ensure_ascii=False)))


def resolved(raw, source_id, account_id, now):
    if not isinstance(raw, dict) or raw.get("id") != source_id or raw.get("account_id") != account_id:
        return {}
    campaign, adset = raw.get("campaign"), raw.get("adset")
    if not isinstance(campaign, dict) or not isinstance(adset, dict):
        return {}
    if not all(re.fullmatch(r"[0-9]{1,30}", str(row.get("id", ""))) for row in (campaign, adset)):
        return {}
    return {"status": "resolved", "account_id": account_id, "ad_id": source_id,
            "ad_name": bounded(raw.get("name", "")), "campaign_id": str(campaign["id"]),
            "campaign_name": bounded(campaign.get("name", "")), "adset_id": str(adset["id"]),
            "adset_name": bounded(adset.get("name", "")), "resolved_at": now}


def enrich(c, source_id, result):
    if not result:
        return 0
    rows = c.execute("""SELECT a.id,a.data FROM lead_attributions a JOIN whatsapp_ad_receipts r
        ON a.id='wa:'||r.message_id WHERE r.source_id=? AND json_extract(a.data,'$.meta.status')='pending'""",
                     (source_id,)).fetchall()
    for row in rows:
        data = json.loads(row["data"])
        data["meta"].update(result)
        c.execute("UPDATE lead_attributions SET data=? WHERE id=?", (json.dumps(data, ensure_ascii=False), row["id"]))
    return len(rows)


def tick(store, account_id, request, now=None):
    now = time.time() if now is None else now
    account_id = str(account_id).removeprefix("act_").strip()
    if not re.fullmatch(r"[0-9]{1,30}", account_id):
        return 0
    with store.db() as c:
        cached = c.execute("SELECT source_id,data FROM meta_ad_lookups WHERE account_id=? AND checked_at>=?",
                           (account_id, now - 3600)).fetchall()
        count = sum(enrich(c, row["source_id"], json.loads(row["data"])) for row in cached)
        sources = [row[0] for row in c.execute("""SELECT DISTINCT r.source_id FROM whatsapp_ad_receipts r
            JOIN lead_attributions a ON a.id='wa:'||r.message_id
            LEFT JOIN meta_ad_lookups m ON m.source_id=r.source_id AND m.account_id=?
            WHERE json_extract(a.data,'$.meta.status')='pending' AND (m.checked_at IS NULL OR m.checked_at<?)
            ORDER BY r.source_id LIMIT 5""", (account_id, now - 3600))]
    for source_id in sources:
        if not re.fullmatch(r"[0-9]{1,30}", source_id):
            result = {}
        else:
            try:
                result = resolved(request(source_id, {"fields": "id,name,account_id,campaign{id,name},adset{id,name}"}), source_id, account_id, now)
            except Exception:
                logging.warning("CRM ad attribution lookup unavailable")
                result = {}
        with store.db() as c:
            c.execute("INSERT OR REPLACE INTO meta_ad_lookups VALUES(?,?,?,?)",
                      (account_id, source_id, now, json.dumps(result, ensure_ascii=False)))
            count += enrich(c, source_id, result)
    return count


def run(auth, stop):
    from app import clinic_context, config_value, configured, meta_request
    from crm_api import store_for
    store = store_for(auth)
    while not stop.wait(60):
        try:
            with clinic_context("vielle"):
                if configured(config_value("META_ACCESS_TOKEN", "")):
                    tick(store, config_value("META_AD_ACCOUNT_ID", ""), meta_request)
        except Exception:
            logging.warning("CRM ad attribution worker unavailable")


def sync_campaigns(store, account_id, request, now=None):
    account_id = str(account_id).removeprefix("act_").strip()
    if not re.fullmatch(r"[0-9]{1,30}", account_id):
        raise ValueError("Configure a conta Meta em Configuracoes.")
    now = time.time() if now is None else now
    params = {"fields": "id,name,effective_status", "limit": "200"}
    campaigns = {}
    for _ in range(10):
        payload = request(f"act_{account_id}/campaigns", params)
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise ValueError("Resposta de campanhas da Meta invalida.")
        for row in payload["data"]:
            if not isinstance(row, dict) or not re.fullmatch(r"[0-9]{1,30}", str(row.get("id", ""))):
                raise ValueError("Campanha da Meta invalida.")
            campaigns[row["id"]] = (account_id, row["id"], bounded(row.get("name", "")),
                                     bounded(row.get("effective_status", ""), 100), now)
        paging = payload.get("paging") or {}
        if not paging.get("next"):
            break
        after = bounded((paging.get("cursors") or {}).get("after", ""), 2000)
        if not after:
            raise ValueError("Paginacao de campanhas da Meta invalida.")
        params = {**params, "after": after}
    else:
        raise ValueError("A conta excedeu o limite de campanhas desta sincronizacao.")
    with store.db() as c:
        c.execute("DELETE FROM meta_campaign_catalog WHERE account_id=?", (account_id,))
        c.executemany("INSERT INTO meta_campaign_catalog VALUES(?,?,?,?,?)", campaigns.values())
    return len(campaigns)


def listing(store, doctor, account_id, connected, campaign=""):
    with store.db() as c:
        store.row(c, "doctors", doctor)
        campaigns = {row["campaign_id"]: dict(row) | {"identified": 0} for row in c.execute(
            "SELECT * FROM meta_campaign_catalog WHERE account_id=? ORDER BY name", (account_id,))}
        counts = c.execute("""SELECT json_extract(a.data,'$.meta.campaign_id') campaign_id,
            max(json_extract(a.data,'$.meta.campaign_name')) campaign_name,count(DISTINCT l.id) identified
            FROM lead_attributions a JOIN leads l ON l.id=a.lead_id
            WHERE l.doctor_id=? AND l.deleted_at=0 AND json_extract(a.data,'$.meta.account_id')=?
            AND json_extract(a.data,'$.meta.status')='resolved' GROUP BY campaign_id""",
                           (doctor, account_id)).fetchall()
        for row in counts:
            item = campaigns.setdefault(row["campaign_id"], {"campaign_id": row["campaign_id"], "name": row["campaign_name"],
                                                            "status": "", "synced_at": 0, "identified": 0})
            item["identified"] += row["identified"]
        pending = c.execute("""SELECT count(DISTINCT a.id) FROM lead_attributions a JOIN leads l ON l.id=a.lead_id
            WHERE l.doctor_id=? AND l.deleted_at=0 AND json_extract(a.data,'$.kind')='whatsapp_ad'
            AND json_extract(a.data,'$.meta.status')='pending'""", (doctor,)).fetchone()[0]
        leads = []
        if campaign:
            leads = [dict(row) for row in c.execute("""SELECT DISTINCT l.id,ct.name,p.name pipeline,s.name stage
                FROM lead_attributions a JOIN leads l ON l.id=a.lead_id JOIN contacts ct ON ct.id=l.contact_id
                JOIN pipelines p ON p.id=l.pipeline_id JOIN stages s ON s.id=l.stage_id
                WHERE l.doctor_id=? AND l.deleted_at=0 AND json_extract(a.data,'$.meta.account_id')=?
                AND json_extract(a.data,'$.meta.campaign_id')=? ORDER BY ct.name,l.id LIMIT 100""", (doctor, account_id, campaign))]
        return {"account_id": account_id, "connected": bool(connected), "campaigns": list(campaigns.values()),
                "pending": pending, "leads": leads, "synced_at": max((row["synced_at"] for row in campaigns.values()), default=0)}


def handle(handler, store, action, query):
    from app import clinic_context, config_value, configured, meta_request
    doctor = query.get("doctor", [""])[0]
    with clinic_context("vielle"):
        account = config_value("META_AD_ACCOUNT_ID", "").removeprefix("act_").strip()
        connected = configured(config_value("META_ACCESS_TOKEN", "")) and bool(account)
        if action == "facebook-sync" and handler.command == "POST":
            if not connected:
                raise ValueError("Configure a integracao Meta em Configuracoes.")
            with store.db() as c:
                store.row(c, "doctors", doctor)
            sync_campaigns(store, account, meta_request)
        elif action != "facebook" or handler.command != "GET":
            return handler.auth_json({"ok": False}, 405)
        return handler.auth_json({"ok": True, **listing(store, doctor, account, connected, query.get("campaign", [""])[0])})
