import http.client
import importlib
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from auth_store import AuthStore
from task_store import TaskStore, Conflict, today
from task_api import members, store_for


class TaskStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = TaskStore(self.root / "vielle")
        self.actor = {"id": 1, "nome": "Alice"}
        self.people = [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bruno"}]

    def create(self, **extra):
        return self.store.create({"title": "Conferir estoque", **extra}, self.actor, self.people)

    def test_workflow_history_and_restart_persistence(self):
        task = self.create(assignees=[1, 2, 1], start_date="2026-10-01", due_date="2026-10-03")
        self.assertEqual(len(task["assignees"]), 2)
        changed = self.store.update(task["id"], {"revision": 1, "status": "doing", "progress": 40}, self.actor, self.people)
        self.store.comment(task["id"], "Separação iniciada", {"id": 2, "nome": "Bruno"})
        attached = self.store.attach(task["id"], "estoque.txt", b"arquivo de teste", self.actor)
        completed = self.store.update(task["id"], {"revision": changed["revision"], "status": "done"}, self.actor, self.people)
        self.assertEqual(completed["progress"], 100)
        self.assertIsNotNone(completed["completed_at"])
        archived = self.store.archive(task["id"], True, completed["revision"], self.actor)
        self.assertEqual(self.store.listing({})["total"], 0)
        self.assertEqual(self.store.listing({"archived": "1"})["total"], 1)
        reopened = TaskStore(self.root / "vielle")
        persisted = reopened.detail(task["id"])
        self.assertEqual({e["action"] for e in persisted["events"]}, {"created", "updated", "comment", "attachment", "archived"})
        self.assertEqual(persisted["events"][-1]["actor_name"], "Alice")
        meta, path = reopened.file(attached["attachments"][0]["id"])
        self.assertEqual(path.read_bytes(), b"arquivo de teste")
        restored = reopened.archive(task["id"], False, archived["revision"], self.actor)
        self.assertIsNone(restored["archived_at"])

    def test_concurrent_edits_reject_stale_revision(self):
        t = self.create()
        self.store.update(t["id"], {"revision": 1, "title": "Nome atualizado"}, self.actor, self.people)
        comment = self.store.comment(t["id"], "Comentário não deve perder a revisão", self.actor)
        self.assertEqual(comment["revision"], 2)
        with self.assertRaises(Conflict):
            self.store.update(t["id"], {"revision": 1, "title": "Nome antigo"}, self.actor, self.people)
        self.assertEqual(self.store.detail(t["id"])["title"], "Nome atualizado")

    def test_validation_checklist_and_removed_assignee_preservation(self):
        for payload in ({"due_date": "31/12/2026"}, {"start_date": "2026-10-03", "due_date": "2026-10-01"}, {"assignees": [999]}, {"progress": -1}, {"status": "invalid"}):
            with self.assertRaises(ValueError): self.create(**payload)
        task = self.create(assignees=[2], checklist=[{"text": "A", "done": True}, {"text": "B"}])
        self.assertEqual(task["progress"], 50)
        edited = self.store.update(task["id"], {"revision": 1, "assignees": [2], "description": "Mantém histórico"}, self.actor, [self.people[0]])
        self.assertEqual(edited["assignees"], [self.people[1]])
        with self.assertRaises(ValueError): self.store.create({"title": "Nova", "assignees": [2]}, self.actor, [self.people[0]])

    def test_filters_and_clinic_isolation(self):
        t = self.create(assignees=[2], due_date=today(), description="Verificar materiais")
        self.create(title="Atrasada", due_date="2020-01-01")
        self.create(title="Concluída", due_date="2020-01-01", status="done")
        self.assertEqual(self.store.listing({"assignee": "2", "q": "materiais", "due": "today"})["tasks"][0]["id"], t["id"])
        self.assertEqual(self.store.listing({"due": "overdue"})["total"], 1)
        other = TaskStore(self.root / "inspire")
        self.assertEqual(other.listing({})["total"], 0)
        with self.assertRaises(LookupError): other.detail(t["id"])

    def test_history_pagination_and_archived_mutations(self):
        t = self.create()
        for i in range(101): self.store.comment(t["id"], str(i), self.actor)
        first = self.store.events(t["id"])
        second = self.store.events(t["id"], first["before"])
        self.assertEqual(len(first["events"]) + len(second["events"]), 102)
        self.assertFalse(set(e["id"] for e in first["events"]) & set(e["id"] for e in second["events"]))
        self.store.archive(t["id"], True, 1, self.actor)
        with self.assertRaises(ValueError): self.store.comment(t["id"], "Bloqueado", self.actor)
        with self.assertRaises(ValueError): self.store.attach(t["id"], "arquivo.txt", b"x", self.actor)
        self.assertEqual(list(self.store.files.iterdir()), [])


class TaskHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        with patch.dict(os.environ, {"DATA_DIR": str(cls.root)}): cls.app = importlib.import_module("app")
        cls.auth = AuthStore(cls.root / "auth.sqlite3")
        cls.auth.initialize()
        cls.master = cls.auth.create_first_master("Master", "master", "Task-test-password!")
        cls.writer = cls.auth.create_user("Alice", "alice", "Task-test-password!", clinic_keys=["vielle"], permissions={"vielle": ["tasks.view", "tasks.create", "tasks.edit", "tasks.delete"]})
        cls.reader = cls.auth.create_user("Bruno", "bruno", "Task-test-password!", clinic_keys=["vielle"], permissions={"vielle": ["tasks.view"]})
        cls.none = cls.auth.create_user("Sem acesso", "none", "Task-test-password!", clinic_keys=["vielle"], permissions={"vielle": ["dashboard.view"]})
        cls.tokens = {name: cls.auth.login(name, "Task-test-password!") for name in ("master", "alice", "bruno", "none")}
        class QuietHandler(cls.app.Handler):
            def log_message(self, *_): pass
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
        cls.server.auth_store = cls.auth
        cls.server.login_limiter = cls.app.LoginLimiter()
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join(); cls.temp.cleanup()

    def req(self, path, user="alice", payload=None, csrf=True, raw=False, method=None):
        headers = {"Cookie": "doc4docs_session=" + self.tokens[user]} if user else {}
        if csrf: headers["X-DOC4DOCS-Request"] = "1"
        body = payload if raw else json.dumps(payload).encode() if payload is not None else None
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        conn.request(method or ("POST" if body is not None else "GET"), path, body=body, headers=headers)
        response = conn.getresponse(); content = response.read(); status = response.status; info = dict(response.getheaders()); conn.close()
        return status, json.loads(content) if content and info.get("Content-Type", "").startswith("application/json") else content, info

    def test_auth_and_all_scope_guards(self):
        self.assertEqual(self.req("/api/tasks?clinic=vielle", user=None)[0], 401)
        self.assertEqual(self.req("/tasks.html?clinic=vielle", user=None)[0], 303)
        for path in ("/tasks.html?clinic=vielle", "/static/tasks.html?clinic=vielle", "/%74asks.html?clinic=vielle", "/api/tasks?clinic=vielle"):
            self.assertEqual(self.req(path, user="none")[0], 403, path)
        self.assertEqual(self.req("/api/tasks?clinic=inspire")[0], 403)
        self.assertEqual(self.req("/api/tasks?clinic=vielle&clinic=inspire")[0], 400)
        self.assertEqual(self.req("/api/tasks")[0], 400)
        self.assertEqual(self.req("/api/tasks?clinic=vielle", payload={"title": "Bad"}, csrf=False)[0], 403)
        self.assertEqual(self.req("/api/tasks?clinic=vielle", user="bruno", payload={"title": "Bad"})[0], 403)
        self.assertEqual(self.req("/api/tasks?clinic=vielle", method="HEAD")[0], 405)

    def test_task_actions_files_and_isolated_database(self):
        with patch.object(self.app, "clinic_context", side_effect=AssertionError("Task routes must not open integration databases")):
            status, data, _ = self.req("/api/tasks?clinic=vielle", payload={"title": "HTTP task", "assignees": [self.writer, self.reader]})
            self.assertEqual(status, 201); key = data["task"]["id"]
            url = f"/api/tasks/{key}?clinic=vielle"
            self.assertEqual(self.req(url, payload={"revision": 1, "status": "doing"})[0], 200)
            self.assertEqual(self.req(url, payload={"revision": 1, "status": "done"})[0], 409)
            self.assertEqual(self.req(url, user="bruno", payload={"revision": 2, "status": "done"})[0], 403)
            self.assertEqual(self.req(url.replace("vielle", "inspire"), user="master")[0], 404)
            self.assertEqual(self.req(f"/api/tasks/{key}/attachments?clinic=vielle&name=../test.txt", payload=b"test", raw=True)[0], 400)
            self.assertEqual(self.req(f"/api/tasks/{key}/attachments?clinic=vielle&name=test.html", payload=b"test", raw=True)[0], 400)
            status, upload, _ = self.req(f"/api/tasks/{key}/attachments?clinic=vielle&name=test.txt", payload=b"safe test", raw=True)
            self.assertEqual(status, 200)
            file_id = upload["task"]["attachments"][0]["id"]
            file_url = f"/api/tasks/{file_id}/file?clinic=vielle"
            self.assertEqual(self.req(file_url, user="bruno")[1], b"safe test")
            self.assertEqual(self.req(file_url, user="none")[0], 403)
            self.assertEqual(self.req(file_url.replace("vielle", "inspire"), user="master")[0], 404)
            self.assertEqual(self.req(f"/api/tasks/{key}/archive?clinic=vielle", payload={"archived": True, "revision": 2})[0], 200)
            detail = self.req(url)[1]["task"]
            self.assertTrue(detail["archived_at"])
            self.assertTrue(detail["attachments"])
        self.assertEqual(store_for(self.auth, "vielle").path, self.root / "tasks" / "vielle" / "tasks.sqlite3")

    def test_only_eligible_users_are_assignable(self):
        eligible = members(self.auth, "vielle")
        self.assertEqual({p["id"] for p in eligible}, {self.master, self.writer, self.reader})
        self.assertEqual(set(eligible[0]), {"id", "name"})
        self.assertEqual(self.req("/api/tasks?clinic=vielle", payload={"title": "Wrong assignee", "assignees": [self.none]})[0], 400)


if __name__ == "__main__": unittest.main()
