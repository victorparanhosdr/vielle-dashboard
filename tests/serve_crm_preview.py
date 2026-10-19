"""Synthetic loopback-only CRM preview. No provider credentials or message sending."""
from datetime import date, datetime, timedelta
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import sqlite3
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if "--background" in sys.argv:
    log = Path(tempfile.gettempdir()) / "doc4docs-crm-preview-running.log"
    with log.open("w") as output:
        child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), *(a for a in sys.argv[1:] if a != "--background")], cwd=ROOT,
                                 stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                                 start_new_session=True)
    for _ in range(100):
        result = log.read_text()
        if result:
            print(result, flush=True)
            break
        if child.poll() is not None:
            raise SystemExit("Preview could not start.")
        time.sleep(.1)
    raise SystemExit(0)
data_dir = Path(tempfile.mkdtemp(prefix="doc4docs-crm-preview-"))
os.environ["DATA_DIR"] = str(data_dir)
os.environ["CRM_SEND_ENABLED"] = "0"
for key in ("CRM_EVOLUTION_URL", "CRM_EVOLUTION_KEY", "CRM_WEBHOOK_SECRET", "CRM_WEBHOOK_URL"):
    os.environ.pop(key, None)
import app
from crm_api import store_for
from crm_appointments import TZ

auth = app.AuthStore(data_dir / "auth.sqlite3")
auth.initialize()
password = secrets.token_urlsafe(24)
auth.create_first_master("Equipe Vielle", "preview", password)
token = auth.login("preview", password)
with sqlite3.connect(app.DB_PATH) as c:
    c.execute("CREATE TABLE clinica_patients(uuid TEXT PRIMARY KEY,name TEXT,phone TEXT,raw_json TEXT,synced_at INTEGER)")
    c.execute("CREATE TABLE clinica_sales(uuid TEXT PRIMARY KEY,patient_uuid TEXT,type TEXT,sale_date TEXT,total REAL,raw_json TEXT,synced_at INTEGER)")
    c.execute("CREATE TABLE clinica_bookings(uuid TEXT PRIMARY KEY,patient_uuid TEXT,professional_uuid TEXT,procedure_uuid TEXT,status TEXT,starts_at TEXT,ends_at TEXT,raw_json TEXT,synced_at INTEGER)")
    for n in range(5):
        for i,name in enumerate(("Ana Exemplo","Marina Exemplo","Renata Exemplo")):
            profile={"email":f"paciente-{n}-{i}@example.test","cpf":f"000.000.{n:03d}-{i:02d}"}
            c.execute("INSERT INTO clinica_patients VALUES(?,?,?,?,?)", (f"patient-{n}-{i}",name,f"55000000{n:02d}{i:03d}",json.dumps(profile),1))
store = store_for(auth)
if "--patient-refresh-fixture" in sys.argv:
    import crm_patient_refresh

    def synthetic_patient_refresh(target, progress):
        progress("cadastros sint\u00e9ticos")
        with sqlite3.connect(app.DB_PATH) as c:
            for n in range(5):
                c.execute("INSERT OR REPLACE INTO clinica_patients VALUES(?,?,?,?,?)",
                          (f"patient-{n}-3", "Camila Cadastro Atualizado", f"55000000{n:02d}003", "{}", int(time.time())))
        target.patient_directory.invalidate()
        return [{"name": "Pacientes sint\u00e9ticos", "status": "done"}]

    crm_patient_refresh.sync_patients = synthetic_patient_refresh
structure = {}
for pipeline, doc in app.PIPELINE_DOCTOR_MAP.items():
    structure.setdefault(doc, []).append(pipeline)
store.bootstrap(structure)
for n, doctor in enumerate(store.listing()["doctors"]):
    doc = doctor["id"]
    with sqlite3.connect(app.DB_PATH) as c:
        for i in range(3):
            uid=f"patient-{n}-{i}"
            clock=datetime.now(TZ)
            previous_month=(clock.replace(day=1)-timedelta(days=1)).replace(day=12,hour=14,minute=30,second=0,microsecond=0)
            when=(clock+timedelta(days=1)).replace(hour=14,minute=30,second=0,microsecond=0) if i!=1 else previous_month
            booking={"patient":{"uuid":uid},"professional":{"uuid":app.DOCTOR_PROFESSIONALS[doctor["name"]],"name":doctor["name"]},"procedure":{"name":"Avaliação facial"}}
            c.execute("INSERT INTO clinica_bookings VALUES(?,?,?,?,?,?,?,?,?)",(f"booking-{n}-{i}",uid,booking["professional"]["uuid"],"procedure","confirmed" if i!=1 else "completed",when.isoformat() if i!=2 else when.date().isoformat(),None,json.dumps(booking),int(time.time())))
            if i==0:
                c.execute("INSERT INTO clinica_bookings VALUES(?,?,?,?,?,?,?,?,?)",(f"booking-{n}-{i}-past",uid,booking["professional"]["uuid"],"procedure","completed",previous_month.isoformat(),None,json.dumps(booking),int(time.time())))
            if i==1:
                for suffix,status,owner in (("cancelled","cancelled",booking["professional"]),("other","confirmed",{"uuid":"other-doctor","name":"Outro profissional"})):
                    raw={**booking,"professional":owner}
                    c.execute("INSERT INTO clinica_bookings VALUES(?,?,?,?,?,?,?,?,?)",(f"booking-{n}-{i}-{suffix}",uid,owner["uuid"],"procedure",status,(clock+timedelta(days=3)).replace(hour=10,minute=0,second=0,microsecond=0).isoformat(),None,json.dumps(raw),int(time.time())))
            for code,kind,status,when,amount in (("sale","sale","active","2025-09-08",310050),
                ("quote","sale_quote","open","2026-10-10",220000),
                ("expired","sale_quote","expired","2024-06-10",150000),
                ("lost","sale_quote","lost","2025-10-10",80000),
                ("other","sale","active","2026-09-01",110000)):
                if kind=="sale_quote" and (i==0 or (i==2 and code=="quote")):
                    continue
                raw={"uuid":f"{code}-{n}-{i}","type":kind,"status":status,"final_amount":amount,
                    "buyer":{"uuid":uid},"seller":{"uuid":app.DOCTOR_PROFESSIONALS[doctor["name"]] if code!="other" else "other-doctor",
                    "name":doctor["name"] if code!="other" else "Outro profissional"},
                    "description":"Avaliação e plano de tratamento" if kind=="sale" else "Plano de cuidado facial",
                    "procedures":[{"name":"Avaliação facial"},{"name":"Plano de tratamento individualizado"}]}
                c.execute("INSERT INTO clinica_sales VALUES(?,?,?,?,?,?,?)",(raw["uuid"],uid,kind,when,amount/100,json.dumps(raw),int(time.time())))
            if i in (0,2):
                amount=1500000 if i==0 else 490000
                raw={"type":"sale","status":"active","final_amount":amount,"buyer":{"uuid":uid},
                    "seller":{"uuid":app.DOCTOR_PROFESSIONALS[doctor["name"]],"name":doctor["name"]},"description":"Histórico sintético de relacionamento"}
                c.execute("INSERT INTO clinica_sales VALUES(?,?,?,?,?,?,?)",(f"history-{n}-{i}",uid,"sale","2024-04-12",amount/100,json.dumps(raw),int(time.time())))
    p = store.listing(doc)["pipelines"][0]
    for i, (name, title, value, stage, due) in enumerate((
        ("Ana Exemplo", "Primeira conversa / avaliação", 0, 0, 1),
        ("Marina Exemplo", "Interesse em avaliação facial", 280000, 1, -1),
        ("Renata Exemplo", "Aguardando disponibilidade", 350000, 1, 2),
        ("Camila Exemplo", "Avaliação confirmada", 420000, 2, 0),
        ("Juliana Exemplo", "Retomar proposta na sexta", 580000, 3, 3),
        ("Clara Exemplo", "Proposta aceita", 390000, 4, 0),
    )):
        key = store.lead({"doctor_id":doc,"pipeline_id":p["id"],"stage_id":p["stages"][0]["id"],
            "name":name,"phone":f"55000000{n:02d}{i:03d}","title":title,"value_cents":value,
            "consent":True,"consent_source":"Dados sintéticos de teste; não enviar.",
            "next_action":(date.today()+timedelta(days=due)).isoformat()}, "Equipe Vielle")
        store.activity({"id":key,"doctor_id":doc,"kind":"followup","body":"Contato de exemplo. Próximo passo alinhado."}, "Equipe Vielle")
        lead = store.detail(key,doc)["lead"]
        if stage:
            store.lead({**lead,"stage_id":p["stages"][stage]["id"]}, "Equipe Vielle")
    commercial=store.channel({"doctor_id":doc,"name":"Comercial","phone":f"550000{n:07d}"})
    reception=store.channel({"doctor_id":doc,"name":"Recepção","phone":f"550001{n:07d}"})
    channels={c["id"]:c for c in store.listing(doc)["channels"]}
    for i,(cid,body) in enumerate(((commercial,"Olá! Gostaria de saber mais sobre a avaliação facial."),
                                  (commercial,"Podemos agendar para quinta-feira à tarde?"),
                                  (reception,"Obrigada pelo atendimento! Vou confirmar meu horário."),
                                  (commercial,"Recebi a proposta. Podemos conversar sobre as opções?"))):
        contact=store.listing(doc)["leads"]
        lead=next(l for l in contact if l["phone"]==f"55000000{n:02d}{i:03d}")
        store.receive(channels[cid]["instance"],{"key":{"id":f"preview-{n}-{i}","remoteJid":lead["phone"]+"@s.whatsapp.net"},
            "pushName":lead["name"],"message":{"conversation":body}},"messages.upsert")
        if i==0:
            with store.db() as c:
                c.execute("INSERT INTO messages(id,channel_id,contact_id,body,direction,status,created_at,sent_at,provider_id,sender_name,sender_user_id) VALUES(?,?,?,?,'out','sent',?,?,?,?,?)",
                    (secrets.token_hex(16),cid,lead["contact_id"],"*Equipe Vielle*\n\nOlá, Ana! Claro. Vou te ajudar a encontrar o melhor horário.",time.time(),time.time(),f"preview-out-{n}","Equipe Vielle","1"))
            thread=store.conversation(doc,cid,lead["contact_id"])
            store.close_conversation({"doctor_id":doc,"channel_id":cid,"contact_id":lead["contact_id"],"cursor":thread["inbound_cursor"]},1)
        if i==1:
            store.receive(channels[cid]["instance"],{"key":{"id":f"preview-follow-{n}","remoteJid":lead["phone"]+"@s.whatsapp.net"},
                "pushName":lead["name"],"message":{"conversation":"Tenho disponibilidade a partir das 15h."}},"messages.upsert")


class Preview(app.Handler):
    def preview_session(self):
        # Loopback synthetic fixture only: other preview tabs share 127.0.0.1 cookies.
        if "Cookie" in self.headers:
            del self.headers["Cookie"]
        self.headers["Cookie"] = f"doc4docs_session={token}"

    def do_GET(self):
        self.preview_session()
        if self.path == "/__preview__":
            victor = next(d["id"] for d in store.listing()["doctors"] if d["name"].startswith("Victor Paranhos"))
            self.send_response(303)
            self.send_header("Set-Cookie", f"doc4docs_session={token}; Path=/; HttpOnly; SameSite=Lax")
            self.send_header("Location", f"/crm.html?clinic=vielle&doctor={victor}")
            self.end_headers()
            return
        return super().do_GET()

    def do_POST(self):
        self.preview_session()
        return super().do_POST()

    def log_message(self, *_):
        pass


server = ThreadingHTTPServer(("127.0.0.1", 0), Preview)
server.auth_store = auth
server.login_limiter = app.LoginLimiter()
print(f"http://127.0.0.1:{server.server_port}/__preview__", flush=True)
server.serve_forever()
