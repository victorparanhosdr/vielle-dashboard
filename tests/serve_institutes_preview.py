"""Loopback-only demo, isolated from real credentials, leads, and clinic databases."""
import os
from pathlib import Path
import secrets
import sys
import tempfile
import time
from datetime import date, timedelta
from http.server import ThreadingHTTPServer
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
data_dir = Path(tempfile.mkdtemp(prefix="doc4docs-institutes-preview-"))
os.environ["DATA_DIR"] = str(data_dir)
import app
from institute_api import store_for

auth = app.AuthStore(data_dir / "auth.sqlite3")
auth.initialize()
password = secrets.token_urlsafe(24)
auth.create_first_master("Master · demonstração", "demo", password)
token = auth.login("demo", password)
auth.create_user("Comercial · demonstração", "comercial", password)
store = store_for(auth)
I,C = "victor-paranhos", "regen-code"
course = store.course(I,C)
course.pop("institute_key")
course["product_ids"] = ["demo-course"]
store.save_course(I,course)
store.save_campaigns(I,C,[{"id":"123456","name":"REGEN.CODE · Captação","aliases":["co2-captacao"]},
                             {"id":"789012","name":"REGEN.CODE · Remarketing","aliases":["co2-remarketing"]}])
start = date.today().replace(day=1)
end = start + timedelta(days=27)
leads,sales,crm,ads = [],[],[],[]
for n in range(240):
    campaign = "co2-captacao" if n%3 else "co2-remarketing"
    d = start + timedelta(days=n%28)
    leads.append({"id":str(n),"name":f"Aluno demonstração {n+1}","email":f"aluno{n}@example.invalid","phone":"",
                  "created_day":d.isoformat(),"campaign":campaign,"source":"instagram","medium":"paid","content":"aula-co2","score":str(10+n%5)})
    if n%6==0:
        sales.append({"id":f"sale{n}","name":f"Aluno demonstração {n+1}","email":f"aluno{n}@example.invalid","phone":"", "product_id":"demo-course",
           "created_day":d.isoformat(),"approved_day":d.isoformat(),"status":"paid","gross":149900,"net":133250,"currency":"BRL","net_currency":"BRL","campaign":campaign,"payment_method":"pix"})
    if n%13==0:
        sales.append({"id":f"pending{n}","name":f"Aluno demonstração {n+1}","email":f"aluno{n}@example.invalid","phone":"", "product_id":"demo-course",
           "created_day":d.isoformat(),"approved_day":"","status":"waiting_payment","gross":149900,"net":133250,"currency":"BRL","net_currency":"BRL","campaign":campaign,"payment_method":"boleto"})
    stage = ["Entrada","Contato","Interesse","Negociação"][n%4]
    crm.append({"id":str(n),"name":f"Lead demonstração {n+1}","created_day":d.isoformat(),"stage":stage,"stage_sort":n%4,"seller_id":"1","pipeline_id":"123","pipeline_name":"REGENCODE"})
for n in range(28):
    d=start+timedelta(days=n)
    for ident in ("123456","789012"):
        ads.append({"id":ident+":"+d.isoformat(),"campaign_id":ident,"day":d.isoformat(),"spend":4500+n*95,"impressions":1200,"clicks":45})
for source,rows in (("kiwify",sales),("sheets",leads),("kommo",crm),("meta",ads)):
    store.save_records(I,C,source,rows,True)
    store.set_sync_state(I,C,source,{"ok":True,"at":int(time.time()),"from":start.isoformat(),"to":end.isoformat(),"count":len(rows)})

class Preview(app.Handler):
    def do_GET(self):
        if self.path == "/__preview__":
            self.send_response(303)
            self.send_header("Set-Cookie",f"doc4docs_session={token}; Path=/; HttpOnly; SameSite=Lax")
            self.send_header("Location","/institutos")
            self.end_headers()
            return
        path=urlsplit(self.path).path
        if path in {"/api/institutes/products", "/api/institutes/meta-campaigns"}:
            if not self.require_dashboard_auth():return
            data={"products":[{"id":"demo-course","name":"REGEN.CODE · CO₂ Avançado"}]} if path.endswith("products") else {
                "account":{"name":"Conta de demonstração","currency":"BRL"},"campaigns":store.campaigns(I,C)}
            return self.auth_json({"ok":True,**data})
        return super().do_GET()
    def do_POST(self):
        if urlsplit(self.path).path == "/api/institutes/sync":
            if not self.require_dashboard_auth():return
            return self.auth_json({"ok":True,"running":False},202)
        return super().do_POST()
    def log_message(self,*_):pass

server=ThreadingHTTPServer(("127.0.0.1",int(os.getenv("INSTITUTE_PREVIEW_PORT","0"))),Preview)
server.auth_store=auth
server.login_limiter=app.LoginLimiter()
print(f"http://127.0.0.1:{server.server_port}/__preview__",flush=True)
server.serve_forever()
