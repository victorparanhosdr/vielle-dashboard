"""Synthetic local preview; never reads patient databases or external APIs."""
import os
import json
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
            for key, extra in (("date", {"emission_date": None}), ("amount", {"net_amount": None}),
                               ("professional", {"seller": None}), ("direction", {"type": "Transferência"})):
                app.save_clinica_bill(conn, {"uuid": "pending-" + key, "type": "Venda", "emission_date": "2026-09-15",
                    "description": "Registro fictício para validação - " + key, "person": {"uuid": "test", "name": "Contato de teste"},
                    "seller": {"uuid": "a"}, "final_amount": 123400, "net_amount": 120000, **extra}, 100)
            for key, extra in (("date", {"compensation_date": None}), ("net", {"net_amount": None})):
                raw = {"uuid": "received-pending-" + key, "status": "received", "compensation_date": "2026-09-15",
                    "net_amount": 120000, "description": "Recebimento fictício para validação - " + key,
                    "raw_bill": {"uuid": "pending-parent-" + key, "seller": {"uuid": "a"}, "person": {"name": "Contato de teste"}}, **extra}
                conn.execute("insert into clinica_parcels(uuid,bill_uuid,type,status,raw_json,synced_at) values(?,?,'Conta','received',?,100)",
                             (raw["uuid"], raw["raw_bill"]["uuid"], json.dumps(raw)))
            for key, status, value in (("paga", "paid", 20000), ("prevista", "open", 15000)):
                app.save_clinica_bill(conn, {"uuid": "expense-" + key, "type": "Conta", "emission_date": "2026-08-15",
                    "seller": {"uuid": "a"}, "description": "Despesa de teste " + key,
                    "final_amount": value, "net_amount": value,
                    "payment_methods": [{"parcels": [{"uuid": "expense-parcel-" + key, "status": status,
                        "final_amount": value, "net_amount": value, "due_date": "2026-09-20",
                        "compensation_date": "2026-09-15" if status == "paid" else None}]}]}, 100)

        original_report = app.report_data
        def preview_report(**args):
            report = original_report(**args)
            report["connected"] = True
            return report

        app.report_data = preview_report
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
