"""Evolution API v2 adapter. Credentials stay in server environment variables."""
import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import build_opener, HTTPRedirectHandler, Request


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class EvolutionGateway:
    def __init__(self):
        self.url = os.environ.get("CRM_EVOLUTION_URL", "").rstrip("/")
        self.key = os.environ.get("CRM_EVOLUTION_KEY", "").strip()

    @property
    def configured(self):
        p = urlsplit(self.url)
        return bool(p.scheme in {"http", "https"} and p.netloc and not p.username and not p.password and not p.query and not p.fragment
                    and self.key and self.key.isascii() and not any(ord(c) < 33 for c in self.key))

    def request(self, method, path, payload=None):
        if not self.configured:
            raise ValueError("Servidor WhatsApp nao configurado. Defina CRM_EVOLUTION_URL e CRM_EVOLUTION_KEY no servidor.")
        data = json.dumps(payload).encode() if payload is not None else None
        req = Request(self.url + path, data=data, method=method,
                      headers={"apikey": self.key, "Content-Type": "application/json"})
        try:
            with build_opener(NoRedirect()).open(req, timeout=30) as response:
                raw = response.read(2 * 1024 * 1024 + 1)
                if len(raw) > 2 * 1024 * 1024:
                    raise ValueError("Resposta WhatsApp muito grande.")
                return json.loads(raw)
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError):
            # Provider error bodies may contain credentials or patient content.
            raise ValueError("Nao foi possivel confirmar a operacao no servidor WhatsApp.") from None

    def connect(self, channel):
        callback = os.getenv("CRM_WEBHOOK_URL", "")
        secret = os.getenv("CRM_WEBHOOK_SECRET", "")
        if not callback.startswith("https://") or len(secret) < 32:
            raise ValueError("Configure um webhook HTTPS com segredo de pelo menos 32 caracteres antes de conectar.")
        name = quote(channel["instance"], safe="")
        instances = self.request("GET", "/instance/fetchInstances?instanceName=" + name)
        if not isinstance(instances, list):
            raise ValueError("O servidor nao retornou instancias no formato Evolution v2.")
        if not instances:
            self.request("POST", "/instance/create", {"instanceName": channel["instance"], "integration": "WHATSAPP-BAILEYS",
                "qrcode": False, "number": channel["phone"], "groupsIgnore": True, "syncFullHistory": False})
        self.request("POST", "/webhook/set/" + name, {"webhook": {"enabled": True, "url": callback,
            "byEvents": False, "base64": False, "headers": {"X-DOC4DOCS-Webhook": secret},
            "events": ["MESSAGES_UPSERT", "CONNECTION_UPDATE"]}})
        data = self.request("GET", "/instance/connect/" + name)
        base64 = data.get("base64") or (data.get("qrcode") or {}).get("base64") or ""
        return {"qr": base64 if isinstance(base64, str) and base64.startswith("data:image/png;base64,") else ""}

    def state(self, channel):
        data = self.request("GET", "/instance/connectionState/" + quote(channel["instance"], safe=""))
        return (data.get("instance") or {}).get("state", "close")

    def send(self, item):
        data = self.request("POST", "/message/sendText/" + quote(item["instance"], safe=""),
                            {"number": item["phone"], "text": item["body"]})
        key = data.get("key") or {}
        return key.get("id")


def sends_enabled():
    return os.getenv("CRM_SEND_ENABLED", "0") == "1" and EvolutionGateway().configured
