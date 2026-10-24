import concurrent.futures
import contextlib
import functools
import http.client
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sqlite3
import ssl
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
from urllib.parse import urlsplit

from auth_store import AuthStore, verify_password
from auth_http import AUTH_PAGE_ROUTES, LoginLimiter, SessionAuthMixin
from auth_recovery import RECOVERY_TTL, normalize_email
from auth_mail import MailSettings, RecoveryService, SmtpMailer
from master_api import MasterApiMixin


PASSWORD = "Synthetic-initial-password!"
NEXT_PASSWORD = "Synthetic-changed-password!"
EMAIL = "person@example.test"


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = AuthStore(Path(self.temp.name) / "auth.sqlite3")
        self.store.initialize()
        self.user = self.store.create_user("Pessoa ficticia", "legacy", PASSWORD, email=EMAIL, clinic_keys=["vielle"])

    def token(self, **kwargs):
        return self.store.create_password_recovery(EMAIL, **kwargs)[1]


class RecoveryStoreTests(Fixture):
    def test_email_normalization_and_invalid_input(self):
        self.assertEqual(normalize_email(" Person@EXAMPLE.test "), EMAIL)
        self.assertEqual(normalize_email("p@exämple.test"), "p@xn--exmple-cua.test")
        for value in (None, "", "not-email", "a@host", ".a@example.test", "a..b@example.test", "a@-host.test", "a@example.test\r\nBcc: theft@example.test", "a@host..test"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_email(value)

    def test_email_and_legacy_login_keep_same_identity_and_permissions(self):
        session = self.store.login("PERSON@EXAMPLE.TEST", PASSWORD)
        self.assertEqual(self.store.session_user(session)["id"], self.user)
        self.assertIsNotNone(self.store.login("legacy", PASSWORD))
        self.assertEqual(self.store.list_users(search=EMAIL)["total"], 1)
        self.assertEqual(self.store.get_public_user(self.user)["clinic_keys"], ["vielle"])

    def test_email_cannot_collide_with_another_login_or_email(self):
        for login, email in ((EMAIL, None), ("other", EMAIL), ("other", "legacy@example.test")):
            if email == "legacy@example.test":
                self.store.create_user("Outra pessoa", email, PASSWORD)
            with self.assertRaises(sqlite3.IntegrityError):
                self.store.create_user("Nao criar", login, PASSWORD, email=email)

    def test_unicode_domain_alias_cannot_steal_legacy_email_login(self):
        self.store.create_user("Legado internacional", "p@exämple.test", PASSWORD)
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.create_user("Outra pessoa", "p@xn--exmple-cua.test", PASSWORD, email="p@xn--exmple-cua.test")

    def test_migration_preserves_legacy_user_session_and_does_not_guess_email(self):
        legacy = self.store.create_user("Legado", "without-email", PASSWORD)
        session = self.store.login("without-email", PASSWORD)
        with self.store.connection() as conn:
            conn.execute("DROP INDEX users_email_unique")
            conn.execute("ALTER TABLE users DROP COLUMN email")
            conn.execute("PRAGMA user_version=4")
        self.store.initialize()
        self.store.initialize()
        self.assertEqual(self.store.session_user(session)["id"], legacy)
        self.assertIsNone(self.store.get_public_user(legacy)["email"])

    def test_only_token_hash_is_persisted_and_audited(self):
        token = self.token()
        with self.store.connection() as conn:
            row = dict(conn.execute("SELECT * FROM password_recovery").fetchone())
            audit = [dict(r) for r in conn.execute("SELECT * FROM audit_log")]
        self.assertNotIn(token, json.dumps(row))
        self.assertEqual(row["token_hash"], self.store.token_hash(token))
        self.assertNotIn(token, json.dumps(audit))
        self.assertNotIn(EMAIL, json.dumps(audit))

    def test_token_is_single_use_and_revokes_every_session(self):
        sessions = [self.store.login("legacy", PASSWORD) for _ in range(2)]
        token = self.token()
        self.assertEqual(self.store.consume_password_recovery(token, NEXT_PASSWORD), self.user)
        self.assertIsNone(self.store.consume_password_recovery(token, NEXT_PASSWORD))
        self.assertTrue(all(self.store.session_user(t) is None for t in sessions))
        self.assertIsNone(self.store.login(EMAIL, PASSWORD))
        self.assertIsNotNone(self.store.login(EMAIL, NEXT_PASSWORD))

    def test_new_request_invalidates_old_link_and_expiry_is_exact(self):
        first = self.token(now=1000)
        second = self.token(now=1001)
        self.assertIsNone(self.store.consume_password_recovery(first, NEXT_PASSWORD, now=1002))
        self.assertIsNone(self.store.consume_password_recovery(second, NEXT_PASSWORD, now=1001 + RECOVERY_TTL))

    def test_unknown_inactive_or_missing_email_cannot_receive_token(self):
        self.assertIsNone(self.store.create_password_recovery("missing@example.test"))
        self.store.set_user_active(self.user, False)
        self.assertIsNone(self.store.create_password_recovery(EMAIL))
        self.store.create_user("Sem email", "only-legacy", PASSWORD)
        self.assertIsNone(self.store.create_password_recovery("only-legacy@example.test"))

    def test_password_change_invalidates_outstanding_link(self):
        token = self.token()
        self.store.set_password(self.user, NEXT_PASSWORD)
        self.assertIsNone(self.store.consume_password_recovery(token, PASSWORD))

    def test_email_change_revokes_links_and_sessions_and_old_alias(self):
        token = self.token()
        session = self.store.login(EMAIL, PASSWORD)
        self.store.update_user(self.user, "Pessoa ficticia", "legacy", email="new@example.test")
        self.assertIsNone(self.store.consume_password_recovery(token, NEXT_PASSWORD))
        self.assertIsNone(self.store.session_user(session))
        self.assertIsNone(self.store.login(EMAIL, PASSWORD))
        self.assertIsNotNone(self.store.login("new@example.test", PASSWORD))

    def test_disable_reenable_does_not_revive_link(self):
        token = self.token()
        self.store.set_user_active(self.user, False)
        self.store.set_user_active(self.user, True)
        self.assertIsNone(self.store.consume_password_recovery(token, NEXT_PASSWORD))

    def test_login_change_invalidates_link(self):
        token = self.token()
        self.store.update_user(self.user, "Pessoa ficticia", "renamed")
        self.assertIsNone(self.store.consume_password_recovery(token, NEXT_PASSWORD))

    def test_promotion_invalidates_link(self):
        master = self.store.create_first_master("Master", "master", PASSWORD)
        token = self.token()
        self.store.promote_to_master(self.user, actor_id=master, expected_login="legacy")
        self.assertIsNone(self.store.consume_password_recovery(token, NEXT_PASSWORD))

    def test_concurrent_consumption_has_one_winner(self):
        token = self.token()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: self.store.consume_password_recovery(token, NEXT_PASSWORD), range(2)))
        self.assertEqual(results.count(self.user), 1)
        self.assertEqual(results.count(None), 1)

    def test_rate_limit_survives_store_reopen_and_has_expiry(self):
        for _ in range(3):
            self.assertTrue(self.store.recovery_limit(EMAIL, "test-peer", now=100))
        other = AuthStore(self.store.path)
        self.assertFalse(other.recovery_limit(EMAIL, "another-peer", now=101))
        self.assertTrue(other.recovery_limit(EMAIL, "test-peer", now=3601))
        with other.connection() as conn:
            raw = json.dumps([dict(r) for r in conn.execute("SELECT * FROM recovery_limits")])
        self.assertNotIn(EMAIL, raw)
        self.assertNotIn("test-peer", raw)


class MailTests(Fixture):
    def test_configuration_is_disabled_by_default_and_requires_https_and_secret(self):
        self.assertIsNone(MailSettings.from_environ({}))
        env = {"DOC4DOCS_AUTH_EMAIL_ENABLED": "1", "DOC4DOCS_SMTP_USER": "sender@example.test", "DOC4DOCS_SMTP_PASSWORD": "synthetic-only-secret"}
        settings = MailSettings.from_environ(env)
        self.assertNotIn(env["DOC4DOCS_SMTP_PASSWORD"], repr(settings))
        for public in ("http://example.test", "https://example.test/path", "https://u:p@example.test", "https://example.test#x"):
            with self.subTest(public=public):
                self.assertIsNone(MailSettings.from_environ({**env, "DOC4DOCS_AUTH_PUBLIC_URL": public}))

    def test_smtp_uses_verified_implicit_tls_no_mailbox_read_and_fragment_link(self):
        settings = MailSettings("smtp.example.test", "sender@example.test", "synthetic-only-secret", "https://app.example.test")
        smtp = MagicMock()
        smtp.send_message.return_value = {}
        with patch("auth_mail.smtplib.SMTP_SSL") as connect:
            connect.return_value.__enter__.return_value = smtp
            SmtpMailer(settings).send(EMAIL, token="T" * 43)
        self.assertEqual(connect.call_args.args, ("smtp.example.test", 465))
        context = connect.call_args.kwargs["context"]
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)
        message = smtp.send_message.call_args.args[0]
        self.assertIn("https://app.example.test/redefinir-senha#token=" + "T" * 43, message.get_content())
        self.assertNotIn(settings.password, message.as_string())
        self.assertEqual(smtp.send_message.call_args.kwargs["to_addrs"], [EMAIL])

    def test_enqueue_does_not_send_and_unknown_never_sends(self):
        mailer = MagicMock()
        service = RecoveryService(self.store, mailer=mailer)
        service.request(EMAIL, "peer")
        mailer.send.assert_not_called()
        service.process_one(service.jobs.get_nowait())
        self.assertEqual(mailer.send.call_count, 1)
        service.request("unknown@example.test", "peer")
        service.process_one(service.jobs.get_nowait())
        self.assertEqual(mailer.send.call_count, 1)

    def test_test_email_only_targets_configured_sender_without_recovery_link(self):
        settings = MailSettings("smtp.example.test", "sender@example.test", "synthetic-only-secret")
        smtp = MagicMock()
        smtp.send_message.return_value = {}
        with patch("auth_mail.smtplib.SMTP_SSL") as connect:
            connect.return_value.__enter__.return_value = smtp
            SmtpMailer(settings).send_test_to_sender()
        call = smtp.send_message.call_args
        self.assertEqual(call.kwargs["to_addrs"], [settings.username])
        self.assertNotIn("#token=", call.args[0].get_content())
        self.assertNotIn(settings.password, call.args[0].as_string())

    def test_provider_errors_do_not_log_secrets_addresses_or_links(self):
        mailer = MagicMock()
        mailer.send.side_effect = RuntimeError("private-provider-body " + EMAIL)
        service = RecoveryService(self.store, mailer=mailer)
        with self.assertLogs(level="WARNING") as logs:
            service.process_one(("reset", EMAIL))
        self.assertEqual(len(logs.output), 1)
        self.assertNotIn(EMAIL, logs.output[0])
        self.assertNotIn("private-provider-body", logs.output[0])


class RecoveryHandler(SessionAuthMixin, MasterApiMixin, SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        parsed = urlsplit(self.path)
        if parsed.path.startswith("/api/master/"):
            self.handle_master_api(parsed)
        elif self.require_dashboard_auth():
            self.path = AUTH_PAGE_ROUTES.get(parsed.path, parsed.path)
            if self.path.startswith("/static/"):
                self.path = self.path.removeprefix("/static")
            super().do_GET()

    def do_POST(self):
        path = urlsplit(self.path)
        if not self.handle_session_post(path.path):
            if not self.handle_master_api(path):
                self.auth_json({"ok": False}, 404)


class RecoveryHttpTests(Fixture):
    def setUp(self):
        super().setUp()
        self.mailer = MagicMock()
        self.service = RecoveryService(self.store, mailer=self.mailer)
        static = Path(__file__).resolve().parents[1] / "static"
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(RecoveryHandler, directory=str(static)))
        self.server.auth_store = self.store
        self.server.login_limiter = LoginLimiter()
        self.server.account_recovery = self.service
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)
        patcher = patch.dict(sys.modules, {
            "offsite_backup_api": SimpleNamespace(handle=lambda *args: False),
            "marketing_leads": SimpleNamespace(master_request=lambda *args: False),
        })
        patcher.start()
        self.addCleanup(patcher.stop)
        catalog = patch("master_api.crm_catalog", return_value={"doctors": [], "pipelines": []})
        catalog.start()
        self.addCleanup(catalog.stop)

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(self, path, data=None, *, headers=None, method="POST"):
        h = {"X-DOC4DOCS-Request": "1", **(headers or {})}
        if data is not None:
            data = json.dumps(data)
            h["Content-Type"] = "application/json"
        conn = http.client.HTTPConnection(*self.server.server_address, timeout=8)
        conn.request(method, path, body=data, headers=h)
        res = conn.getresponse()
        result = res.status, dict(res.getheaders()), res.read()
        conn.close()
        return result

    def test_forgot_responses_are_identical_for_known_unknown_inactive_and_limited(self):
        bodies = []
        for email in (EMAIL, "unknown@example.test", EMAIL, EMAIL, EMAIL):
            status, _, body = self.request("/api/auth/password/forgot", {"email": email})
            self.assertEqual(status, 200)
            bodies.append(body)
        self.store.set_user_active(self.user, False)
        bodies.append(self.request("/api/auth/password/forgot", {"email": EMAIL})[2])
        self.assertEqual(len(set(bodies)), 1)
        self.assertNotIn(EMAIL.encode(), bodies[0])
        self.assertEqual(set(json.loads(bodies[0])), {"ok", "message"})
        self.mailer.send.assert_not_called()

    def test_csrf_and_unexpected_fields_fail_without_enqueue(self):
        for headers in ({"Origin": "https://evil.example"}, {"X-DOC4DOCS-Request": ""}, {"Sec-Fetch-Site": "cross-site"}):
            self.assertEqual(self.request("/api/auth/password/forgot", {"email": EMAIL}, headers=headers)[0], 403)
        self.assertEqual(self.request("/api/auth/password/forgot", {"email": EMAIL, "debug": True})[0], 400)
        self.assertEqual(self.service.jobs.qsize(), 0)

    def test_public_pages_headers_no_raw_token_and_get_does_not_consume(self):
        token = self.token()
        for path in ("/recuperar-senha", "/redefinir-senha?token=" + token, "/reset-password.html", "/static/reset-password.html"):
            status, headers, body = self.request(path, method="GET")
            self.assertEqual(status, 200)
            self.assertNotIn(token.encode(), body)
            self.assertEqual(headers["Referrer-Policy"], "no-referrer")
            self.assertEqual(headers["Cache-Control"], "no-store")
            self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertEqual(self.store.consume_password_recovery(token, NEXT_PASSWORD), self.user)

    def test_reset_requires_matching_password_and_valid_single_use_link(self):
        token = self.token()
        body = {"token": token, "password": NEXT_PASSWORD, "password_confirmation": "different"}
        self.assertEqual(self.request("/api/auth/password/reset", body)[0], 400)
        body["password_confirmation"] = NEXT_PASSWORD
        status, headers, raw = self.request("/api/auth/password/reset", body)
        self.assertEqual(status, 200, raw)
        self.assertEqual(json.loads(raw), {"ok": True})
        self.assertIn("Max-Age=0", headers["Set-Cookie"])
        self.assertEqual(self.request("/api/auth/password/reset", body)[0], 400)
        self.assertEqual(self.service.jobs.qsize(), 1)

    def test_complete_request_mail_link_reset_login_and_notice(self):
        session = self.store.login(EMAIL, PASSWORD)
        status, _, raw = self.request("/api/auth/password/forgot", {"email": EMAIL})
        self.assertEqual(status, 200)
        self.service.process_one(self.service.jobs.get_nowait())
        token = self.mailer.send.call_args.kwargs["token"]
        self.assertNotIn(token.encode(), raw)
        status, _, raw = self.request("/api/auth/password/reset", {
            "token": token, "password": NEXT_PASSWORD, "password_confirmation": NEXT_PASSWORD})
        self.assertEqual(status, 200, raw)
        self.assertIsNone(self.store.session_user(session))
        self.assertEqual(self.request("/api/auth/login", {"login": EMAIL, "password": NEXT_PASSWORD})[0], 200)
        self.service.process_one(self.service.jobs.get_nowait())
        self.assertEqual(self.mailer.send.call_args.args, (EMAIL,))
        self.assertEqual(self.mailer.send.call_args.kwargs, {})

    def test_disabled_email_does_not_prevent_existing_login(self):
        self.server.account_recovery = RecoveryService(self.store)
        self.assertEqual(self.request("/api/auth/password/forgot", {"email": EMAIL})[0], 503)
        self.assertEqual(self.request("/api/auth/login", {"login": EMAIL, "password": PASSWORD})[0], 200)

    def test_master_only_creation_uses_confirmed_email(self):
        payload = {"nome": "Nova pessoa", "login": "new@example.test", "email": "new@example.test", "confirm_email": True, "password": PASSWORD}
        self.assertEqual(self.request("/api/master/users", payload)[0], 403)
        master = self.store.create_first_master("Master", "master", PASSWORD)
        headers = {"Cookie": "doc4docs_session=" + self.store.login("master", PASSWORD)}
        self.assertEqual(self.request("/api/master/users", {**payload, "confirm_email": False}, headers=headers)[0], 400)
        self.assertEqual(self.request("/api/master/users", {**payload, "login": "legacy-name"}, headers=headers)[0], 400)
        status, _, raw = self.request("/api/master/users", payload, headers=headers)
        self.assertEqual(status, 201, raw)
        user = json.loads(raw)["user"]
        self.assertEqual(user["email"], payload["email"])
        self.assertFalse(user["is_master"])

    def test_email_edit_requires_confirmation_and_revokes_old_token(self):
        self.store.create_first_master("Master", "master", PASSWORD)
        headers = {"Cookie": "doc4docs_session=" + self.store.login("master", PASSWORD)}
        token = self.token()
        payload = {"nome": "Pessoa ficticia", "login": "legacy", "email": "other@example.test"}
        route = f"/api/master/users/{self.user}/edit"
        self.assertEqual(self.request(route, payload, headers=headers)[0], 400)
        self.assertEqual(self.request(route, {**payload, "confirm_email": True}, headers=headers)[0], 200)
        self.assertIsNone(self.store.consume_password_recovery(token, NEXT_PASSWORD))


if __name__ == "__main__":
    unittest.main()
