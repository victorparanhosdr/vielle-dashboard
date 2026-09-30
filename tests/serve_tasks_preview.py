"""Loopback preview with synthetic data; never reads clinic integration databases."""
import os
from pathlib import Path
import secrets
import sys
import tempfile
from datetime import date, timedelta
from http.server import ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
data_dir = Path(tempfile.mkdtemp(prefix="doc4docs-tasks-preview-"))
os.environ["DATA_DIR"] = str(data_dir)
import app
from task_api import store_for, members

auth = app.AuthStore(data_dir / "auth.sqlite3")
auth.initialize()
password = secrets.token_urlsafe(24)
uid = auth.create_first_master("Victor · demonstração", "preview", password)
for name, login in (("Mariana Souza", "mariana"), ("Ayrton Silva", "ayrton")):
    auth.create_user(name, login, password, clinic_keys=["vielle", "inspire"], permissions={key: ["tasks.view", "tasks.create", "tasks.edit"] for key in ("vielle", "inspire")})
token = auth.login("preview", password)
actor = auth.session_user(token)
people = members(auth, "vielle")
store = store_for(auth, "vielle")
for title, description, status, days, progress, priority in (
    ("Organizar agenda da próxima semana", "Revisar os horários disponíveis e alinhar com a recepção.", "todo", 2, 0, "normal"),
    ("Revisar estoque de materiais", "Conferência mensal dos insumos e da validade dos produtos.", "todo", -1, 0, "high"),
    ("Acompanhar retornos pendentes", "Confirmar o contato com os pacientes da lista de retorno.", "doing", 1, 45, "normal"),
    ("Preparar treinamento da equipe", "Atualizar o material de atendimento e organizar a reunião.", "doing", 5, 70, "low"),
    ("Conferir pagamentos da semana", "Conferência finalizada com a equipe financeira.", "done", -1, 100, "normal"),
):
    task = store.create({"title": title, "description": description, "status": status, "priority": priority,
        "due_date": (date.today() + timedelta(days=days)).isoformat(), "assignees": [p["id"] for p in people[:2]], "progress": progress}, actor, people)
    if status == "doing": store.comment(task["id"], "Primeira etapa finalizada. Aguardando revisão da equipe.", actor)

class Preview(app.Handler):
    def do_GET(self):
        if self.path == "/__preview__":
            self.send_response(303)
            self.send_header("Set-Cookie", f"doc4docs_session={token}; Path=/; HttpOnly; SameSite=Lax")
            self.send_header("Location", "/tasks.html?clinic=vielle")
            self.end_headers()
            return
        return super().do_GET()
    def log_message(self, *_): pass

server = ThreadingHTTPServer(("127.0.0.1", 0), Preview)
server.auth_store = auth
server.login_limiter = app.LoginLimiter()
print(f"http://127.0.0.1:{server.server_port}/__preview__", flush=True)
server.serve_forever()
