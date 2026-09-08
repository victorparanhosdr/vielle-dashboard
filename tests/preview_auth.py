"""Local login preview with temporary data; external integrations are unavailable."""

import argparse
from http.server import ThreadingHTTPServer
import os
from pathlib import Path
import sys
import tempfile
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--auth-db", type=Path, help="Use an explicitly provisioned local auth database (no users created).")
    parser.add_argument("--sample-reports", action="store_true", help="Synthetic empty reports for UI QA only; never calls integrations.")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="doc4docs-login-preview-") as directory:
        os.environ["DATA_DIR"] = directory
        from app import Handler
        from auth_http import LoginLimiter
        from auth_store import AuthStore

        class PreviewHandler(Handler):
            def do_GET(self):
                path = urlsplit(self.path).path
                if args.sample_reports and path == "/api/report":
                    if self.require_dashboard_auth():
                        from access_policy import project_report
                        self.auth_json(project_report({"connected": True, "filters": {}, "pipelines": [], "general_panel": {}, "financial": {}, "clinica_experts": {}, "patient_followup": {}, "quote_followup": {}, "paid_traffic": {}, "whatsapp_audit": {}}, self.report_module))
                    return
                if (path.startswith("/api/") and path not in ("/api/auth/me", "/api/export-authorize") and not path.startswith("/api/master/")) or path in ("/auth/start", "/auth/callback", "/webhooks/revoked", "/kommo-widget"):
                    if self.require_dashboard_auth():
                        self.auth_json({"ok": False, "error": "Integrações indisponíveis na prévia local de login."}, 403)
                    return
                super().do_GET()

            def do_POST(self):
                path = urlsplit(self.path).path
                if path not in ("/api/auth/login", "/api/auth/logout") and not path.startswith("/api/master/"):
                    if self.require_dashboard_auth():
                        self.auth_json({"ok": False, "error": "Integrações indisponíveis na prévia local de login."}, 403)
                    return
                super().do_POST()

        store = AuthStore(args.auth_db if args.auth_db else Path(directory) / "doc4docs_auth.sqlite3")
        store.initialize()
        if not args.auth_db:
            store.create_user("Usuário de teste", "teste", "TesteLocalDOC42026!")
        server = ThreadingHTTPServer(("127.0.0.1", args.port), PreviewHandler)
        server.auth_store = store
        server.login_limiter = LoginLimiter()
        print(f"Preview: http://127.0.0.1:{args.port}/login", flush=True)
        if not args.auth_db:
            print("Login: teste | Senha: TesteLocalDOC42026! (somente banco temporario)", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()


if __name__ == "__main__":
    main()
