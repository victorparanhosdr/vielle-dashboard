"""Temporary, authenticated periodic-sync demo with no external API access."""
import os
from pathlib import Path
import secrets
import sys
import tempfile
import threading
import time
from datetime import datetime
from http.server import ThreadingHTTPServer
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    with tempfile.TemporaryDirectory(prefix="doc4docs-periodic-preview-") as directory:
        os.environ["DATA_DIR"] = directory
        import app
        from periodic_sync import BRAZIL, PeriodicClinicSync

        app.clinic_db_path = lambda clinic_id=None: Path(directory) / f"{clinic_id or app.current_clinic_id()}.sqlite3"
        app.LEGACY_DB_PATH = Path(directory) / "missing.sqlite3"
        app.CLINIC_CONFIG_DEFAULTS = {}
        app.CONFIG_DEFAULTS = {key: "" for key in app.EDITABLE_CONFIG_KEYS}
        app.CONFIG_DEFAULTS.update(AUTO_SYNC_ENABLED="true", AUTO_SYNC_INTERVAL_MINUTES="5")
        app.init_db()
        store = app.AuthStore(Path(directory) / "auth.sqlite3")
        store.initialize()
        password = secrets.token_urlsafe(24)
        store.create_user("Master de demonstração", "preview", password, is_master=True)
        token = store.login("preview", password)

        def simulated(clinic, mode, progress):
            services = []
            for name in ("Kommo", "Clínica Experts", "Meta Ads"):
                progress(name)
                time.sleep(0.2)
                services.append({"name": name, "status": "done"})
            return services

        initial = int(datetime.now(BRAZIL).timestamp()) - 290
        app.PERIODIC_SYNC = PeriodicClinicSync(app.SUPPORTED_CLINICS, app.DASHBOARD_REFRESH_JOBS, simulated,
            lambda clinic: app.periodic_sync_state(clinic) or {"recent_finished_at": initial},
            app.periodic_sync_state, settings_getter=app.periodic_sync_settings)
        stop = threading.Event()

        class PreviewHandler(app.Handler):
            def do_GET(self):
                if self.path == "/__preview__":
                    self.send_response(303)
                    self.send_header("Set-Cookie", f"doc4docs_session={token}; Path=/; HttpOnly; SameSite=Lax")
                    self.send_header("Location", "/settings.html?clinic=inspire")
                    self.end_headers()
                    return
                return super().do_GET()

            def do_POST(self):
                if self.path.split("?", 1)[0] not in {"/api/refresh", "/api/settings"}:
                    return self.auth_json({"error": "Ação não disponível na demonstração."}, 403)
                return super().do_POST()

            def log_message(self, *_):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), PreviewHandler)
        server.auth_store = store
        server.login_limiter = app.LoginLimiter()
        with patch.object(app, "report_data", return_value={"connected": True, "filters": {}}), \
             patch.object(app, "refresh_clinic_integrations", side_effect=lambda clinic, first, last, progress: simulated(clinic, "manual", progress)), \
             patch("urllib.request.urlopen", side_effect=AssertionError("No external API access in preview")):
            worker = threading.Thread(target=app.PERIODIC_SYNC.serve, args=(stop,), daemon=True)
            worker.start()
            print(f"http://localhost:{server.server_port}/__preview__", flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                stop.set()
                worker.join(10)
                server.server_close()


if __name__ == "__main__":
    main()
