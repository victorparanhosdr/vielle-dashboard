"""Bounded, self-reported campaign attribution without storing arbitrary URL queries."""
import json
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

UTM_KEYS = ("utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term", "utm_id")
CLICK_KEYS = ("gclid", "fbclid", "gbraid", "wbraid", "msclkid", "ttclid")
LABELS = {"utm_source": "Origem", "utm_medium": "Midia", "utm_campaign": "Campanha", "utm_content": "Conteudo", "utm_term": "Termo", "utm_id": "ID da campanha"}


def value(raw, maximum=250):
    if not isinstance(raw, str) or len(raw) > maximum or any(unicodedata.category(c).startswith("C") for c in raw):
        raise ValueError("Parametro de rastreamento invalido.")
    return raw.strip()


def config(raw=None):
    raw = {} if raw is None else raw
    if not isinstance(raw, dict) or not isinstance(raw.get("defaults", {}), dict):
        raise ValueError("Configuracao de rastreamento invalida.")
    return {"enabled": raw.get("enabled", True) is not False,
            "capture_click_ids": raw.get("capture_click_ids") is True,
            "forward_on_redirect": raw.get("forward_on_redirect") is True,
            "defaults": {k: value(raw.get("defaults", {}).get(k, "")) for k in UTM_KEYS}}


def clean_url(raw):
    if not raw:
        return ""
    if not isinstance(raw, str) or len(raw) > 4000:
        raise ValueError("URL de rastreamento invalida.")
    try:
        parsed = urlsplit(raw)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            return ""
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path[:1000], "", ""))
    except ValueError:
        return ""


def capture(raw, settings, slug):
    settings = config(settings)
    if not settings["enabled"]:
        return {}
    if raw is None:
        raw = {}
    if not isinstance(raw, dict) or not isinstance(raw.get("utm", {}), dict) or not isinstance(raw.get("click_ids", {}), dict):
        raise ValueError("Rastreamento invalido.")
    utm, sources = {}, {}
    for key in UTM_KEYS:
        supplied = value(raw.get("utm", {}).get(key, ""))
        fallback = settings["defaults"][key]
        if supplied or fallback:
            utm[key] = supplied or fallback
            sources[key] = "url" if supplied else "default"
    clicks = {key: value(raw.get("click_ids", {}).get(key, ""), 500) for key in CLICK_KEYS} if settings["capture_click_ids"] else {}
    landing = clean_url(raw.get("landing_url", ""))
    if landing and urlsplit(landing).path != "/f/" + slug:
        landing = ""
    return {"version": 1, "utm": utm, "sources": sources, "click_ids": {k:v for k,v in clicks.items() if v},
            "landing_url": landing, "referrer": clean_url(raw.get("referrer", ""))}


def finish_url(finish, settings, attribution):
    result = dict(finish)
    if result.get("mode") != "redirect" or not config(settings)["forward_on_redirect"]:
        return result
    parsed = urlsplit(result["url"])
    query = parse_qsl(parsed.query, keep_blank_values=True)
    existing = {k for k,v in query if v}
    query += [(key, val) for key, val in attribution.get("utm", {}).items() if key in UTM_KEYS and val and key not in existing]
    result["url"] = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))
    return result


def schema(c):
    c.executescript("""CREATE TABLE IF NOT EXISTS lead_attributions(
        id TEXT PRIMARY KEY,lead_id TEXT NOT NULL REFERENCES leads(id),form_id TEXT NOT NULL,
        form_name TEXT NOT NULL,created_at REAL NOT NULL,data TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS lead_attributions_history ON lead_attributions(lead_id,created_at,id);
    """)
    from crm_whatsapp_attribution import schema as whatsapp_schema
    whatsapp_schema(c)


def lead_history(c, lead):
    total = c.execute("SELECT count(*) FROM lead_attributions WHERE lead_id=?", (lead,)).fetchone()[0]
    def convert(row):
        return dict(row) | {"data":json.loads(row["data"])} if row else None
    first = c.execute("SELECT * FROM lead_attributions WHERE lead_id=? ORDER BY created_at,id LIMIT 1", (lead,)).fetchone()
    rows = c.execute("SELECT * FROM lead_attributions WHERE lead_id=? ORDER BY created_at DESC,id DESC LIMIT 30", (lead,)).fetchall()
    return {"total": total, "first": convert(first), "last": convert(rows[0]) if rows else None, "items": [convert(row) for row in rows]}


def export_cells(attribution):
    data = json.loads(attribution) if isinstance(attribution, str) else attribution or {}
    return [*[data.get("utm", {}).get(k, "") for k in UTM_KEYS], data.get("landing_url", ""), data.get("referrer", ""),
            json.dumps(data.get("click_ids", {}), ensure_ascii=False) if data.get("click_ids") else ""]


EXPORT_HEADERS = [*UTM_KEYS, "Pagina do formulario", "Pagina de origem", "IDs de clique"]
