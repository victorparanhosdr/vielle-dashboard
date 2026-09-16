from contextlib import contextmanager
import io
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import urllib.error

import body_evolution as body
import clinica_patient_link as links


UUID = "912dcaf1-9b4f-43a4-a2b5-df44773030d5"


class LinkTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name) / "inspire.db"

        @contextmanager
        def connect():
            conn = sqlite3.connect(path)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            try:
                with conn:
                    yield conn
            finally:
                conn.close()
        self.connect = connect
        with connect() as conn:
            conn.execute("CREATE TABLE clinica_patients(uuid TEXT PRIMARY KEY, name TEXT, phone TEXT)")
            conn.execute("INSERT INTO clinica_patients VALUES (?, 'Paciente Fictício', '')", (UUID,))
            body.initialize(conn)
            self.patient = body.enroll(conn, UUID, {"id": 1})
        self.handler = SimpleNamespace(current_user={"id": 1}, require_permission=Mock(return_value=True),
            auth_json=lambda data, status=200: (status, data),
            server=SimpleNamespace(auth_store=SimpleNamespace(audit_event=Mock())))
        self.remote = {"uuid": UUID, "name": "Paciente Fictício", "annotation": "Preservar esta anotação.\n",
                       "updated_at": "2026-09-01", "phone": "original", "active": True}
        self.calls = []
        self.client = Mock()
        self.client.request.side_effect = self.request
        for mocker in [patch.object(links, "ExpertsPatients", return_value=self.client),
                       patch.dict("os.environ", {"BODY_EVOLUTION_PUBLIC_ORIGIN": "https://doc4docs.com.br"})]:
            mocker.start()
            self.addCleanup(mocker.stop)

    def request(self, uuid, annotation=None):
        self.calls.append((uuid, annotation))
        if annotation is not None:
            self.remote["annotation"] = annotation
        return dict(self.remote)

    def call(self, mode="preview", **extra):
        return links.handle_link(self.handler, "inspire", {"mode": mode, "patient_id": self.patient, **extra}, self.connect, lambda: "token-test")

    def preview(self):
        status, data = self.call()
        self.assertEqual(status, 200, data)
        return data

    def test_preview_is_read_only_and_save_preserves_text_and_other_fields(self):
        old = dict(self.remote)
        preview = self.preview()
        self.assertEqual(len(self.calls), 1)
        self.assertNotIn("phone", preview)
        status, result = self.call("save", revision=preview["revision"], confirmed=True)
        self.assertEqual(status, 200, result)
        self.assertTrue(self.remote["annotation"].startswith(old["annotation"] + "\n\n"))
        self.assertEqual({k:v for k,v in self.remote.items() if k != "annotation"}, {k:v for k,v in old.items() if k != "annotation"})
        self.assertEqual(sum(note is not None for _, note in self.calls), 1)
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM body_experts_link_writes").fetchone()
            self.assertEqual(row["before_annotation"], old["annotation"])
            self.assertEqual(row["status"], "verified")
            self.assertTrue(row["verified_at"])
        audit = self.handler.server.auth_store.audit_event.call_args.kwargs
        self.assertNotIn("annotation", json.dumps(audit))

    def test_repeated_save_does_not_duplicate_link(self):
        preview = self.preview()
        for _ in range(2):
            self.assertEqual(self.call("save", revision=preview["revision"], confirmed=True)[0], 200)
        self.assertEqual(sum(note is not None for _, note in self.calls), 1)
        self.assertTrue(self.preview()["linked"])

    def test_refuses_changed_annotation_or_patient_name_since_confirmation(self):
        for field in ("annotation", "name", "updated_at"):
            with self.subTest(field=field):
                preview = self.preview()
                self.remote[field] += " changed"
                self.assertEqual(self.call("save", revision=preview["revision"], confirmed=True)[0], 409)
        self.assertFalse(any(note is not None for _, note in self.calls))

    def test_no_confirmation_permission_unknown_patient_or_missing_uuid(self):
        self.assertEqual(self.call("save")[0], 400)
        self.assertEqual(self.call(patient_id="another-clinic")[0], 404)
        self.assertEqual(self.calls, [])
        self.handler.require_permission.return_value = False
        self.assertIsNone(self.call())
        self.assertEqual(self.calls, [])

    def test_mismatched_uuid_and_missing_annotation_fail_closed(self):
        for mutate in (lambda: self.remote.update(uuid="other"), lambda: self.remote.pop("annotation")):
            mutate()
            self.assertEqual(self.call()[0], 502)
            self.remote["uuid"] = UUID
        self.assertFalse(any(note is not None for _, note in self.calls))

    def test_null_annotation_and_wrapped_api_response(self):
        self.remote["annotation"] = None
        self.client.request.side_effect = lambda uuid: {"data": dict(self.remote)}
        self.assertEqual(self.preview()["annotation"], "")

    def test_uncertain_write_keeps_backup_and_never_retries(self):
        preview = self.preview()
        def uncertain(uuid, annotation=None):
            result = self.request(uuid, annotation)
            if annotation is not None:
                raise links.LinkError("Timeout", 502)
            return result
        self.client.request.side_effect = uncertain
        self.assertEqual(self.call("save", revision=preview["revision"], confirmed=True)[0], 502)
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM body_experts_link_writes").fetchone()
            self.assertEqual(row["status"], "unconfirmed")
            self.assertIn("Preservar", row["before_annotation"])
        self.assertTrue(self.preview()["linked"])
        self.assertEqual(sum(note is not None for _, note in self.calls), 1)

    def test_confirmation_read_must_match_exact_annotation(self):
        preview = self.preview()
        def lost_write(uuid, annotation=None):
            return dict(self.remote)
        self.client.request.side_effect = lost_write
        self.assertEqual(self.call("save", revision=preview["revision"], confirmed=True)[0], 502)
        self.handler.server.auth_store.audit_event.assert_not_called()


class TransportTests(unittest.TestCase):
    def test_only_annotation_is_sent_to_official_host(self):
        response = io.BytesIO(b'{"ok":true}')
        opener = Mock()
        opener.open.return_value = response
        with patch.object(links.urllib.request, "build_opener", return_value=opener):
            links.ExpertsPatients("private-test-token").request(UUID, "Link")
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, links.API_ROOT + UUID)
        self.assertEqual(request.method, "PUT")
        self.assertEqual(json.loads(request.data), {"annotation":"Link"})

    def test_api_failure_does_not_echo_secret_response_or_retry(self):
        opener = Mock()
        opener.open.side_effect = urllib.error.HTTPError(links.API_ROOT + UUID, 403, "private content", {}, io.BytesIO(b'secret'))
        with patch.object(links.urllib.request, "build_opener", return_value=opener):
            with self.assertRaises(links.LinkError) as result:
                links.ExpertsPatients("secret-token").request(UUID, "new note")
        self.assertNotIn("secret", str(result.exception))
        self.assertEqual(opener.open.call_count, 1)

    def test_no_redirects_and_invalid_uuids(self):
        self.assertIsNone(links.NoRedirect().redirect_request(None,None,302,"",{},"https://elsewhere.com"))
        with patch.object(links.urllib.request, "build_opener") as network:
            with self.assertRaises(links.LinkError):
                links.ExpertsPatients("test").request("../../other")
            network.assert_not_called()

    def test_origin_is_configured_not_from_user_payload_or_localhost(self):
        for origin in ("", "http://doc4docs.com.br", "https://localhost", "https://127.0.0.1", "https://10.0.0.1",
                       "https://foo.local", "https://name:pass@doc4docs.com.br", "https://doc4docs.com.br/sub", "https://doc4docs.com.br/?a=1"):
            with self.subTest(origin=origin), patch.dict("os.environ", {"BODY_EVOLUTION_PUBLIC_ORIGIN":origin,"APP_BASE_URL":""}):
                with self.assertRaises(links.LinkError): links.public_link("inspire", "abc")
        with patch.dict("os.environ", {"BODY_EVOLUTION_PUBLIC_ORIGIN":"https://doc4docs.com.br/"}):
            self.assertEqual(links.public_link("inspire", "abc"), "https://doc4docs.com.br/body-evolution.html?clinic=inspire&patient=abc")

    def test_exact_link_detection_does_not_accept_another_patient(self):
        link = "https://doc4docs.com.br/body-evolution.html?clinic=inspire&patient=abc"
        self.assertTrue(links.has_link('<a href="'+link.replace("&","&amp;")+'">Ficha</a>',link))
        self.assertFalse(links.has_link(link+"def",link))


if __name__ == "__main__":
    unittest.main()
