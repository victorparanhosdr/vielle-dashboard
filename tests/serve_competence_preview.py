"""Synthetic local preview; never reads patient databases or external APIs."""
import os
from pathlib import Path
import secrets
import sys
import tempfile
from http.server import ThreadingHTTPServer
from urllib.parse import urlsplit
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    with tempfile.TemporaryDirectory(prefix="doc4docs-competence-preview-") as directory:
        os.environ["DATA_DIR"] = directory
        import app
        from auth_store import AuthStore
        from auth_http import LoginLimiter
        app.clinic_db_path = lambda clinic_id=None: Path(directory) / "clinic.sqlite3"
        app.CONFIG_DEFAULTS = {}
        app.clinic_doctor_professionals = lambda *_: {"Profissional A (teste)": "a", "Profissional B (teste)": "b"}
        app.forced_professional_uuids = lambda *_: []
        app.init_db()
        with app.db() as conn:
            for number in range(64):
                expense = number % 3 == 0
                day = "2026-09-" + str(number % 20 + 10).zfill(2)
                app.save_clinica_bill(conn, {
                    "uuid": "title-" + str(number), "type": "Conta" if expense else "Venda", "emission_date": day,
                    "description": "Título a pagar para fornecedor de teste" if expense else "Venda de consulta - paciente fictício",
                    "person": {"uuid": "contact-" + str(number % 5), "name": "Fornecedor de teste" if expense else "Paciente fictício " + str(number % 5)},
                    "seller": {"uuid": "a" if number % 2 else "b"},
                    "category": {"name": "Serviços da clínica" if expense else "Consultas"},
                    "final_amount": 123450 + number, "net_amount": 120000 + number,
                    "payment_methods": [{"parcels": [{"uuid": "parcel-" + str(number), "status": "open",
                        "due_date": "2026-10-20", "final_amount": 123450 + number, "net_amount": 120000 + number}]}],
                }, 100)
        app.report_data = lambda **args: {"connected": True, "filters": {
            "date_from": args.get("date_from") or "2026-09-01", "date_to": args.get("date_to") or "2026-09-30",
            "doctor": args.get("doctor") or "", "doctors": list(app.clinic_doctor_professionals(None)),
        }, "financial": {}}
        store = AuthStore(Path(directory) / "auth.sqlite3")
        store.initialize()
        password = secrets.token_urlsafe(24)
        store.create_user("Prévia local", "preview", password, is_master=True)
        token = store.login("preview", password)

        class PreviewHandler(app.Handler):
            def do_GET(self):
                path = urlsplit(self.path).path
                if path == "/__preview__":
                    self.send_response(303)
                    self.send_header("Set-Cookie", f"doc4docs_session={token}; HttpOnly; SameSite=Lax; Path=/")
                    self.send_header("Location", "/?clinic=vielle&view=financialView&date_from=2026-09-01&date_to=2026-09-30")
                    self.end_headers()
                    return
                if path.startswith("/api/") and path not in {"/api/auth/me", "/api/report", "/api/financial-competence", "/api/financial-competence/export"}:
                    return self.auth_json({"error": "Prévia somente leitura"}, 403)
                if path.startswith(("/auth/", "/webhooks/")):
                    return self.auth_json({"error": "Prévia somente leitura"}, 403)
                return super().do_GET()

            def do_POST(self):
                return self.auth_json({"error": "Prévia somente leitura"}, 403)

        server = ThreadingHTTPServer(("127.0.0.1", 0), PreviewHandler)
        server.auth_store = store
        server.login_limiter = LoginLimiter()
        print(f"http://127.0.0.1:{server.server_port}/__preview__", flush=True)
        with patch("urllib.request.urlopen", side_effect=AssertionError("No external calls in preview")):
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                server.server_close()


if __name__ == "__main__":
    main()
