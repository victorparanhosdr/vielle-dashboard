"""Isolated UI QA with temporary users. No customer database or integration is used."""
from http.server import ThreadingHTTPServer
import os
from pathlib import Path
import sys
import tempfile
import threading
import uuid
from urllib.parse import urlsplit

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def main():
    with tempfile.TemporaryDirectory(prefix="doc4docs-team-preview-") as directory:
        os.environ["DATA_DIR"]=directory
        from app import Handler
        from auth_http import LoginLimiter
        from auth_store import AuthStore
        from team_presence import TeamPresence
        auth=AuthStore(Path(directory)/"auth.sqlite3");auth.initialize()
        auth.create_user("Gestor de teste","gestor","TesteLocalDOC42026!",clinic_keys=["vielle"],permissions={"vielle":["team.view","crm.view"]})
        auth.create_user("Ana de teste","ana","TesteLocalDOC42026!",clinic_keys=["vielle"],permissions={"vielle":["crm.view"]})
        uid=auth.create_user("Bruno de teste","bruno","TesteLocalDOC42026!",clinic_keys=["vielle"],permissions={"vielle":["crm.view"]})
        auth.create_user("Camila de teste","camila","TesteLocalDOC42026!",clinic_keys=["vielle"],permissions={"vielle":["crm.view"]})
        token=auth.login("bruno","TesteLocalDOC42026!");store=TeamPresence(auth);client=uuid.uuid4().hex
        stop=threading.Event()
        def colleague():
            n=0
            while not stop.is_set():
                n+=1;store.heartbeat("vielle",uid,token,{"client_id":client,"sequence":n,"activity_age":0});stop.wait(20)
        threading.Thread(target=colleague,daemon=True).start()
        class PreviewHandler(Handler):
            def do_GET(self):
                path=urlsplit(self.path).path
                if path.startswith("/api/") and path!="/api/auth/me" and not path.startswith("/api/team/"):
                    if self.require_dashboard_auth():self.auth_json({"ok":False,"error":"Integrações desligadas na prévia de teste."},403)
                    return
                super().do_GET()
            def do_POST(self):
                path=urlsplit(self.path).path
                if path not in {"/api/auth/login","/api/auth/logout"} and not path.startswith("/api/team/"):
                    if self.require_dashboard_auth():self.auth_json({"ok":False,"error":"Integrações desligadas na prévia de teste."},403)
                    return
                super().do_POST()
        server=ThreadingHTTPServer(("127.0.0.1",8769),PreviewHandler);server.auth_store=auth;server.login_limiter=LoginLimiter()
        print("Preview: http://127.0.0.1:8769/login?next=%2Fteam.html%3Fclinic%3Dvielle",flush=True)
        try:server.serve_forever()
        except KeyboardInterrupt:pass
        finally:stop.set();server.server_close()


if __name__=="__main__":main()
