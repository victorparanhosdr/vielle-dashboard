"""Master authorization and CSRF boundaries using a dummy HTTP handler."""
from email.message import Message
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlparse

from offsite_backup import BackupError
import offsite_backup_api as api


class ApiTests(unittest.TestCase):
    def handler(self, *, command="GET", master=True, same_origin=True, body=b'{"confirm":true}'):
        headers = Message()
        headers["Content-Type"] = "application/json"
        headers["Content-Length"] = str(len(body))
        return SimpleNamespace(command=command, headers=headers, rfile=io.BytesIO(body),
            require_master_auth=Mock(return_value=master), same_origin_request=Mock(return_value=same_origin),
            auth_json=Mock(), current_user={"id": 42}, server=SimpleNamespace(auth_store=SimpleNamespace(
                path=Path("/scratch/synthetic/auth.sqlite3"), audit_event=Mock())))

    def call(self, handler, path="/api/master/backups"):
        return api.handle(handler, urlparse(path))

    def test_unrelated_route_is_not_handled(self):
        handler = self.handler()
        self.assertFalse(self.call(handler, "/api/master/users"))
        handler.require_master_auth.assert_not_called()

    def test_nonmaster_cannot_read_status_or_queue_work(self):
        for method in ("GET", "POST"):
            handler = self.handler(command=method, master=False)
            with patch.object(api, "overview") as overview, patch.object(api, "request_retry") as request:
                self.assertTrue(self.call(handler))
                overview.assert_not_called()
                request.assert_not_called()

    def test_master_read_is_readonly(self):
        handler = self.handler()
        with patch.object(api, "overview", return_value={"status": "disabled"}) as overview, patch.object(api, "request_retry") as request:
            self.call(handler)
            overview.assert_called_once_with(Path("/scratch/synthetic"))
            request.assert_not_called()
            handler.auth_json.assert_called_once_with({"ok": True, "status": "disabled"})

    def test_cross_origin_post_cannot_queue(self):
        handler = self.handler(command="POST", same_origin=False)
        with patch.object(api, "request_retry") as request:
            self.call(handler)
            request.assert_not_called()
        self.assertEqual(handler.auth_json.call_args.args[1], 403)

    def test_confirmation_is_exact_boolean_not_integer_or_extra_field(self):
        for value in ({"confirm": 1}, {"confirm": False}, {"confirm": True, "extra": 1}, [], None):
            handler = self.handler(command="POST", body=json.dumps(value).encode())
            with patch.object(api, "request_retry") as request:
                self.call(handler)
                request.assert_not_called()
            self.assertEqual(handler.auth_json.call_args.args[1], 400)

    def test_oversized_body_rejected_before_reading(self):
        handler = self.handler(command="POST", body=b" " * 257)
        self.call(handler)
        self.assertEqual(handler.rfile.tell(), 0)
        self.assertEqual(handler.auth_json.call_args.args[1], 400)

    def test_master_retry_is_audited_and_queued_not_executed_inline(self):
        handler = self.handler(command="POST")
        with patch.object(api, "request_retry", return_value={"status": "requested"}) as request:
            self.call(handler)
            request.assert_called_once_with(Path("/scratch/synthetic"))
        handler.server.auth_store.audit_event.assert_called_once_with("offsite_backup_requested", 42)
        self.assertEqual(handler.auth_json.call_args.args[1], 202)

    def test_errors_do_not_expose_provider_or_configuration(self):
        for error, status in ((BackupError("synthetic_secret"), 409), (RuntimeError("synthetic_secret"), 503)):
            handler = self.handler()
            with patch.object(api, "overview", side_effect=error):
                self.call(handler)
            self.assertEqual(handler.auth_json.call_args.args[1], status)
            self.assertNotIn("synthetic_secret", str(handler.auth_json.call_args))

    def test_other_methods_do_not_execute(self):
        handler = self.handler(command="DELETE")
        with patch.object(api, "request_retry") as request:
            self.call(handler)
            request.assert_not_called()
        self.assertEqual(handler.auth_json.call_args.args[1], 405)


if __name__ == "__main__":
    unittest.main()
