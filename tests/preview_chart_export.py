"""Disposable Excel-export preview. No existing databases or integrations used."""
from datetime import date
from http.server import ThreadingHTTPServer
import os
from pathlib import Path
import sys
import tempfile
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    with tempfile.TemporaryDirectory(prefix="doc4docs-excel-preview-") as directory:
        os.environ["DATA_DIR"] = directory
        import app
        from auth_store import AuthStore
        from auth_http import LoginLimiter
        # Never copy any legacy clinic database into this preview.
        app.clinic_db_path = lambda clinic_id=None: Path(directory) / "sample.sqlite3"
        app.init_db()
        today = date.today().isoformat()
        with app.db() as conn:
            conn.execute("insert into clinica_bills(uuid,type,due_date,description,amount,raw_json,synced_at) values('demo','sale',?,'Venda demonstrativa',1234.56,'{}',0)", (today,))
            conn.execute("insert into clinica_parcels(uuid,type,status,due_date,category_name,amount,raw_json,synced_at) values('expense','expense','paid',?,'Material',123.45,'{}',0)", (today,))

        class Handler(app.Handler):
            def do_GET(self):
                path = urlsplit(self.path).path
                if path.startswith("/api/") and path not in {"/api/auth/me", "/api/report", "/api/export-chart"}:
                    return self.auth_json({"error": "Indisponível na prévia"}, 403)
                if path.startswith(("/auth/", "/webhooks/")):
                    return self.auth_json({"error": "Indisponível na prévia"}, 403)
                return super().do_GET()

            def do_POST(self):
                if urlsplit(self.path).path not in {"/api/auth/login", "/api/auth/logout"}:
                    return self.auth_json({"error": "Indisponível na prévia"}, 403)
                return super().do_POST()

        store = AuthStore(Path(directory) / "auth.sqlite3")
        store.initialize()
        store.create_user("Teste Excel", "excel", "ExcelLocal2026!", clinic_keys=["vielle"])
        server = ThreadingHTTPServer(("127.0.0.1", 8778), Handler)
        server.auth_store = store
        server.login_limiter = LoginLimiter()
        print("http://127.0.0.1:8778 | excel | ExcelLocal2026! (dados fictícios)", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()


if __name__ == "__main__":
    main()
