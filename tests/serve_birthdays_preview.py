"""Loopback birthday preview; optional cached data is copied, never modified."""
import argparse
from datetime import date
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import sqlite3
import sys
import tempfile
from urllib.parse import urlsplit
from unittest.mock import patch


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--clinic", default="vielle")
    parser.add_argument("--source-dir", type=Path)
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    with tempfile.TemporaryDirectory(prefix="doc4docs-birthdays-preview-") as directory:
        os.environ["DATA_DIR"] = directory
        import app
        import patient_birthdays as birthdays
        if args.clinic not in app.SUPPORTED_CLINICS:
            parser.error("Clínica inválida")
        auth = app.AuthStore(Path(directory) / "auth.sqlite3")
        auth.initialize()
        password = secrets.token_urlsafe(24)
        auth.create_first_master("Administrador Master", "preview", password)
        session = auth.login("preview", password)
        actor = auth.session_user(session)
        reference = birthdays.today()
        with app.clinic_context(args.clinic), app.db() as conn:
            birthdays.initialize(conn)
            if args.source_dir:
                filename = "kommo_report.sqlite3" if args.clinic == "vielle" else f"kommo_report_{args.clinic}.sqlite3"
                source = (args.source_dir / filename).resolve(strict=True)
                with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as cached:
                    for table, columns in (
                        ("clinica_patients", "uuid,name,phone,email,origin,active,raw_json,synced_at"),
                        ("clinica_sales", "uuid,patient_uuid,type,sale_date,total,raw_json,synced_at"),
                    ):
                        values = cached.execute(f"SELECT {columns} FROM {table}").fetchall()
                        marks = ",".join("?" for _ in columns.split(","))
                        conn.executemany(f"INSERT INTO {table}({columns}) VALUES({marks})", values)
            else:
                names = ["Ana Costa", "Bruno Almeida", "Camila Souza", "Daniel Lima", "Elisa Martins", "Felipe Rocha", "Gabriela Santos", "Helena Oliveira"]
                for n in range(40):
                    uuid = f"preview-patient-{n}"
                    day = 1 + n % 27
                    birthday = date(1975 + n % 25, reference.month, day)
                    raw = {"uuid": uuid, "date_birth": birthday.isoformat()}
                    conn.execute("INSERT INTO clinica_patients(uuid,name,phone,email,raw_json,synced_at) VALUES(?,?,?,?,?,?)",
                                 (uuid, names[n] if n < len(names) else f"Paciente {n + 1:02}", "", f"paciente{n}@example.invalid", json.dumps(raw), int(app.time.time())))
                    for number in range(n % 7):
                        sale_date = date(reference.year, 1, 2 + number).isoformat()
                        total = 900 + n * 120 + number * 350
                        app.save_clinica_sale(conn, {"uuid": f"preview-sale-{n}-{number}", "type": "sale", "status": "active", "sale_date": sale_date,
                            "final_amount": total * 100, "buyer": {"uuid": uuid}}, int(app.time.time()))
                    if n < 10:
                        birthdays.save_gift(conn, {"patient_uuid": uuid, "year": reference.year, "revision": 0, "sent_at": reference.isoformat(),
                            "description": "Kit de cuidados", "note": "Entrega registrada pela recepção."}, actor, reference)

        class Preview(app.Handler):
            def local_host(self):
                if self.headers.get("Host") not in {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}:
                    self.auth_json({"ok": False, "error": "Prévia disponível somente neste computador."}, 403)
                    return False
                return True

            def end_headers(self):
                self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
                self.send_header("Cache-Control", "no-store")
                super().end_headers()

            def do_GET(self):
                if not self.local_host():
                    return
                path = urlsplit(self.path).path
                if path == "/__preview__":
                    self.send_response(303)
                    self.send_header("Set-Cookie", f"doc4docs_session={session}; Path=/; HttpOnly; SameSite=Lax")
                    self.send_header("Location", f"/birthdays.html?clinic={args.clinic}")
                    self.end_headers()
                    return
                if path == "/api/refresh":
                    if not self.require_dashboard_auth():
                        return
                    return self.auth_json({"ok": True, "clinic": args.clinic, "running": False, "phase": "idle", "message": ""})
                if path.startswith("/api/") and path != "/api/auth/me" and not path.startswith("/api/birthdays"):
                    return self.auth_json({"ok": False, "error": "Recurso indisponível nesta prévia local."}, 403)
                return super().do_GET()

            def do_POST(self):
                if not self.local_host():
                    return
                if urlsplit(self.path).path not in {"/api/birthdays/gift", "/api/birthdays/gift/undo"}:
                    return self.auth_json({"ok": False, "error": "Integrações externas desativadas nesta prévia."}, 403)
                return super().do_POST()

            def log_message(self, *_):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", args.port), Preview)
        server.auth_store, server.login_limiter = auth, app.LoginLimiter()
        print(f"http://127.0.0.1:{server.server_port}/__preview__", flush=True)
        with patch("urllib.request.urlopen", side_effect=AssertionError("External calls disabled in birthday preview")):
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                server.server_close()


if __name__ == "__main__":
    main()
