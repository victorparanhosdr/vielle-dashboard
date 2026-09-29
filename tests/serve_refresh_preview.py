"""Loopback-only, read-only user demo with simulated refresh and no provider access."""
import contextlib
import os
from pathlib import Path
import secrets
import sys
import tempfile
import time
from http.server import ThreadingHTTPServer
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
data_dir = Path(tempfile.mkdtemp(prefix="doc4docs-refresh-preview-"))
os.environ["DATA_DIR"] = str(data_dir)

import app

store = app.AuthStore(data_dir / "auth.sqlite3")
store.initialize()
password = secrets.token_urlsafe(24)
user_id = store.create_user("Usuário comum de demonstração", "preview", password,
    clinic_keys=["vielle", "inspire"], permissions={"vielle": ["dashboard.view"], "inspire": ["commercial.view"]})
token = store.login("preview", password)


def simulated_refresh(clinic, date_from, date_to, progress):
    services = []
    for name in ("Kommo", "Clínica Experts", "Meta Ads"):
        progress(name)
        time.sleep(2)
        services.append({"name": name, "status": "done"})
    return services


class PreviewHandler(app.Handler):
    def do_GET(self):
        if self.path == "/__preview__":
            self.send_response(303)
            self.send_header("Set-Cookie", f"doc4docs_session={token}; Path=/; HttpOnly; SameSite=Lax")
            self.send_header("Location", "/?clinic=vielle")
            self.end_headers()
            return
        return super().do_GET()

    def do_POST(self):
        if not self.path.startswith("/api/refresh?"):
            return self.auth_json({"ok": False, "error": "Prévia somente leitura."}, 403)
        return super().do_POST()

    def log_message(self, *_):
        pass


with patch.object(app, "clinic_context", side_effect=lambda _: contextlib.nullcontext()), \
     patch.object(app, "config_value", side_effect=lambda key, default=None: default), \
     patch.object(app, "query_report_args", return_value={}), \
     patch.object(app, "report_data", return_value={"connected": True, "filters": {"date_from": "2026-09-01", "date_to": "2026-09-30"}}), \
     patch.object(app, "refresh_clinic_integrations", side_effect=simulated_refresh), \
     patch("urllib.request.urlopen", side_effect=AssertionError("No provider access in preview")):
    server = ThreadingHTTPServer(("127.0.0.1", 0), PreviewHandler)
    server.auth_store = store
    server.login_limiter = app.LoginLimiter()
    print(f"http://127.0.0.1:{server.server_port}/__preview__", flush=True)
    server.serve_forever()
