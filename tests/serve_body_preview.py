"""Loopback-only demo. Synthetic data, separate SQLite files, no background sync."""
import io
import json
import os
from pathlib import Path
import secrets
import sqlite3
import sys
import tempfile
from http.server import ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
data_dir = Path(tempfile.mkdtemp(prefix="doc4docs-body-preview-"))
os.environ["DATA_DIR"] = str(data_dir)
# Existing clinic databases must never be copied into the demo.
for name in ("kommo_report.sqlite3", "kommo_report_inspire.sqlite3", "kommo_report_carla.sqlite3"):
    sqlite3.connect(data_dir / name).close()

import app
import body_evolution as body
from reportlab.pdfgen.canvas import Canvas

store = app.AuthStore(data_dir / "auth.sqlite3")
store.initialize()
password = secrets.token_urlsafe(12)
actor_id = store.create_user("Demonstração local", "preview", password, is_master=True)
actor = {"id": actor_id, "nome": "Demonstração local"}
with app.clinic_context("inspire"), app.db() as conn:
    for i, name in enumerate(("Ana Exemplo (demonstração)", "Bruno Exemplo (demonstração)")):
        conn.execute("INSERT INTO clinica_patients(uuid,name,phone,raw_json,synced_at) VALUES (?,?,?,?,?)",
                     (f"demo-{i}", name, f"1190000000{i}", "{}", 0))
    body.initialize(conn)
    patient = body.enroll(conn, "demo-0", actor)
    for i, month in enumerate((6, 7, 8, 9)):
        blob = io.BytesIO(); pdf = Canvas(blob); pdf.drawString(50, 760, f"DOC4DOCS - Exame ficticio de demonstracao - mes {month}"); pdf.save()
        fields = {"weight_kg": [80,78.2,76,74.2][i], "fat_pct": [34,32.5,31,29.1][i],
                  "muscle_kg": [26,26.7,27.1,27.8][i], "waist_cm": [96,93,90,88][i],
                  "hip_cm": [108,106,104,103][i], "bmr_kcal":1420, "fat_free_kg":52.9}
        body.save_evaluation(conn, {"patient_id":patient, "exam_at":f"2026-{month:02d}-15T10:00", "source":"inbody",
            "method":"InBody120", "professional":"Dra. Exemplo", "notes":"Dados fictícios para validar a interface. Medidas de circunferência registradas manualmente.",
            "fields":fields,"confirmed":True}, actor, (blob.getvalue(),{"source":"inbody", "fields":fields,"parser_version":"demo"}))

server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
server.auth_store = store
server.login_limiter = app.LoginLimiter()
info = {"url":f"http://127.0.0.1:{server.server_port}/body-evolution.html?clinic=inspire&patient={patient}",
        "login":"preview", "password":password,"data_dir":str(data_dir),"pid":os.getpid()}
Path("/tmp/doc4docs-body-preview-info.json").write_text(json.dumps(info))
print(json.dumps(info), flush=True)
server.serve_forever()
