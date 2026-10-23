import io
import os
import sqlite3
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from crm_gateway import EvolutionGateway, GatewayError
from crm_worker import tick
from tests import test_crm as fixture


class SendConfirmationTests(unittest.TestCase):
    setUp = fixture.CrmTests.setUp
    lead = fixture.CrmTests.lead
    campaign = fixture.CrmTests.campaign
    enable = fixture.CrmTests.enable

    def pending(self):
        campaign = self.campaign()
        self.enable(campaign)
        return campaign, self.store.claim(self.now)

    def echo(self, item, provider_id="synthetic-echo", **changes):
        data = {"key": {"id": provider_id, "fromMe": True,
                        "remoteJid": item["phone"] + "@s.whatsapp.net"},
                "message": {"conversation": item["body"]}, "messageTimestamp": self.now}
        data.update(changes)
        return self.store.receive(item["instance"], data, "send.message")

    def row(self, table, key):
        with self.store.db() as c:
            return dict(self.store.row(c, table, key))

    def test_echo_before_timeout_preserves_acceptance_without_duplicate_hooks(self):
        campaign, item = self.pending()
        with patch("crm_followup_runtime.accepted") as accepted:
            self.assertFalse(self.echo(item))
            self.store.finish(item, error=True, error_code="timeout")
            self.store.finish(item, "synthetic-echo")
            self.assertFalse(self.echo(item))
        accepted.assert_called_once()
        message = self.row("messages", item["id"])
        self.assertEqual((message["status"], message["provider_id"]), ("sent", "synthetic-echo"))
        self.assertEqual(self.row("campaigns", campaign)["status"], "running")
        with self.store.db() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM messages WHERE direction='out'").fetchone()[0], 1)

    def test_worker_does_not_turn_echo_confirmation_into_unknown_on_timeout(self):
        campaign = self.campaign()
        self.enable(campaign)
        gateway = Mock()
        def send(item):
            self.echo(item)
            raise TimeoutError("synthetic private content")
        gateway.send.side_effect = send
        tick(self.store, gateway, self.now)
        gateway.send.assert_called_once()
        self.assertEqual(self.row("campaigns", campaign)["status"], "running")
        self.assertEqual(self.store.listing(self.a)["campaigns"][0]["counts"], {"sent": 1})

    def test_late_confirmation_settles_only_message_and_does_not_resume_campaign(self):
        campaign, item = self.pending()
        self.store.finish(item, error=True, error_code="network_error")
        self.assertEqual(self.row("campaigns", campaign)["status"], "paused")
        with patch("crm_followup_runtime.accepted") as accepted, patch("crm_followup_runtime.stop_contact") as stop:
            self.echo(item)
        accepted.assert_not_called()
        stop.assert_called_once()
        self.assertEqual(self.row("messages", item["id"])["status"], "sent")
        state = self.row("campaigns", campaign)
        self.assertEqual(state["status"], "paused")
        self.assertIn("mantida pausada", state["last_error"])
        self.assertIsNone(self.store.claim(self.now + 180))

    def test_previous_echo_id_can_be_reconciled_by_a_new_signed_echo(self):
        campaign, item = self.pending()
        self.store.finish(item, error=True)
        with self.store.db() as c:
            c.execute("UPDATE messages SET provider_id='synthetic-echo' WHERE id=?", (item["id"],))
        self.echo(item)
        self.assertEqual(self.row("messages", item["id"])["status"], "sent")
        self.assertEqual(self.row("campaigns", campaign)["status"], "paused")

    def test_mismatched_or_missing_timestamp_echo_never_confirms_pending_message(self):
        campaign, item = self.pending()
        for index, changes in enumerate(({"message": {"conversation": "Different synthetic text"}},
                                         {"messageTimestamp": 0}, {"messageTimestamp": self.now + 121})):
            with self.subTest(changes=changes):
                self.echo(item, provider_id=f"unmatched-{index}", **changes)
                row = self.row("messages", item["id"])
                self.assertEqual((row["status"], row["provider_id"]), ("sending", None))
        self.store.finish(item, error=True)
        self.assertEqual(self.row("campaigns", campaign)["status"], "paused")

    def test_ambiguous_identical_echo_is_not_assigned_to_either_message(self):
        _, item = self.pending()
        with self.store.db() as c:
            c.execute("""INSERT INTO messages(id,channel_id,contact_id,campaign_id,body,direction,status,
                created_at,recipient_phone,origin,claimed_at) VALUES(?,?,?,?,?,'out','unknown',?,?,'crm',?)""",
                ("ambiguous", item["channel_id"], item["contact_id"], None, item["body"],
                 self.now, item["phone"], self.now))
        self.echo(item)
        for key in (item["id"], "ambiguous"):
            self.assertIsNone(self.row("messages", key)["provider_id"])

    def test_imported_history_is_not_a_new_confirmation_event(self):
        _, item = self.pending()
        self.store.receive(item["instance"], {
            "key": {"id": "synthetic-history", "fromMe": True, "remoteJid": item["phone"] + "@s.whatsapp.net"},
            "message": {"conversation": item["body"]}, "messageTimestamp": self.now,
        }, "messages.upsert", history=True)
        row = self.row("messages", item["id"])
        self.assertEqual((row["status"], row["provider_id"]), ("sending", None))

    def test_error_details_are_static_and_uncertain_message_is_never_retried(self):
        campaign = self.campaign()
        self.enable(campaign)
        gateway = Mock()
        gateway.send.side_effect = GatewayError("http_limit")
        tick(self.store, gateway, self.now)
        tick(self.store, gateway, self.now + 180)
        gateway.send.assert_called_once()
        self.assertIn("limite de requisicoes", self.row("campaigns", campaign)["last_error"])
        with self.store.db() as c:
            reason = c.execute("SELECT skip_reason FROM messages WHERE campaign_id=?", (campaign,)).fetchone()[0]
            self.assertIn("limite de requisicoes", reason)

    def test_finalization_error_is_not_reclassified_as_provider_failure(self):
        campaign = self.campaign()
        self.enable(campaign)
        gateway = Mock()
        gateway.send.return_value = "synthetic-accepted"
        with patch.object(self.store, "finish", side_effect=sqlite3.OperationalError("synthetic database error")) as finish:
            with self.assertRaises(sqlite3.OperationalError):
                tick(self.store, gateway, self.now)
        gateway.send.assert_called_once()
        finish.assert_called_once()
        self.assertEqual(finish.call_args.args[1], "synthetic-accepted")

    def test_reused_provider_id_is_uncertain_without_crashing_or_cross_linking(self):
        campaign = self.campaign([self.lead(), self.lead("5500000000003")])
        self.enable(campaign)
        gateway = Mock()
        gateway.send.return_value = "synthetic-duplicate-id"
        self.assertTrue(tick(self.store, gateway, self.now))
        self.assertTrue(tick(self.store, gateway, self.now + 180))
        self.assertFalse(tick(self.store, gateway, self.now + 360))
        self.assertEqual(gateway.send.call_count, 2)
        with self.store.db() as c:
            rows = c.execute("SELECT status,provider_id,skip_reason FROM messages WHERE campaign_id=? ORDER BY status", (campaign,)).fetchall()
            self.assertEqual([row["status"] for row in rows], ["sent", "unknown"])
            self.assertEqual(rows[0]["provider_id"], "synthetic-duplicate-id")
            self.assertIsNone(rows[1]["provider_id"])
            self.assertIn("reutilizou identificador", rows[1]["skip_reason"])
        self.assertEqual(self.row("campaigns", campaign)["status"], "paused")


class CampaignRecipientFailureTests(unittest.TestCase):
    setUp = fixture.CrmTests.setUp
    lead = fixture.CrmTests.lead
    campaign = fixture.CrmTests.campaign
    enable = fixture.CrmTests.enable
    row = SendConfirmationTests.row
    echo = SendConfirmationTests.echo

    def audience(self, count=4):
        return [self.lead(f"55000000000{i + 2:02d}") for i in range(count)]

    def test_isolated_failures_continue_other_recipients_without_retry_or_fake_acceptance(self):
        for index, code in enumerate(("timeout", "http_400", "invalid_response", "missing_confirmation")):
            with self.subTest(code=code):
                ids = [self.lead(f"5500000000{index}{i + 2:02d}") for i in range(2)]
                campaign = self.campaign(ids, interval_seconds=180)
                self.enable(campaign)
                now = self.now + index * 600
                first = self.store.claim(now)
                self.assertEqual(first["campaign_id"], campaign)
                self.assertFalse(self.store.finish(first, error=True, error_code=code))
                self.assertEqual(self.row("campaigns", campaign)["status"], "running")
                unknown = self.row("messages", first["id"])
                self.assertEqual((unknown["status"], unknown["provider_id"], unknown["automation_excluded_at"]), ("unknown", None, 0))
                self.assertIsNone(self.store.claim(now + 179))
                second = self.store.claim(now + 180)
                self.assertNotEqual(second["contact_id"], first["contact_id"])
                self.store.finish(second, f"accepted-{index}")
                self.assertIsNone(self.store.claim(now + 360))
                self.assertEqual(self.row("campaigns", campaign)["status"], "done")
                self.assertEqual(self.row("messages", first["id"])["status"], "unknown")
                with self.store.db() as c:
                    counts = dict(c.execute("SELECT status,count(*) FROM messages WHERE campaign_id=? GROUP BY status", (campaign,)))
                    self.assertEqual(counts, {"sent": 1, "unknown": 1})

    def test_worker_timeout_does_not_stop_next_recipient(self):
        campaign = self.campaign(self.audience(2), interval_seconds=180)
        self.enable(campaign)
        gateway = Mock()
        gateway.send.side_effect = [TimeoutError(), "accepted-second"]
        self.assertTrue(tick(self.store, gateway, self.now))
        self.assertFalse(tick(self.store, gateway, self.now + 179))
        self.assertTrue(tick(self.store, gateway, self.now + 180))
        self.assertFalse(tick(self.store, gateway, self.now + 360))
        self.assertEqual(gateway.send.call_count, 2)
        self.assertEqual(self.row("campaigns", campaign)["status"], "done")

    def test_repeated_uncertainty_pauses_on_third_attempt_and_keeps_queue(self):
        campaign = self.campaign(self.audience(), interval_seconds=180)
        self.enable(campaign)
        for index in range(3):
            item = self.store.claim(self.now + index * 180)
            self.assertIsNotNone(item)
            self.store.finish(item, error=True, error_code="timeout")
            expected = "paused" if index == 2 else "running"
            self.assertEqual(self.row("campaigns", campaign)["status"], expected)
        self.assertIsNone(self.store.claim(self.now + 540))
        self.assertIn("Tres envios consecutivos", self.row("campaigns", campaign)["last_error"])
        with self.store.db() as c:
            self.assertEqual(dict(c.execute("SELECT status,count(*) FROM messages WHERE campaign_id=? GROUP BY status", (campaign,))), {"queued": 1, "unknown": 3})

    def test_success_breaks_failure_streak(self):
        campaign = self.campaign(self.audience(5), interval_seconds=180)
        self.enable(campaign)
        for index in range(5):
            item = self.store.claim(self.now + index * 180)
            if index == 2:
                self.store.finish(item, "accepted-between-failures")
            else:
                self.store.finish(item, error=True, error_code="timeout")
            self.assertEqual(self.row("campaigns", campaign)["status"], "running")
        self.assertIsNone(self.store.claim(self.now + 900))
        self.assertEqual(self.row("campaigns", campaign)["status"], "done")

    def test_sparse_failures_do_not_trigger_short_window_circuit(self):
        campaign = self.campaign(self.audience(3), interval_seconds=600)
        self.enable(campaign)
        for index in range(3):
            item = self.store.claim(self.now + index * 600)
            self.store.finish(item, error=True, error_code="timeout")
            self.assertEqual(self.row("campaigns", campaign)["status"], "running")

    def test_global_errors_pause_immediately(self):
        for index, code in enumerate(("http_auth", "http_limit", "network_error", "http_404", "http_server", "http_error", "unconfirmed")):
            with self.subTest(code=code):
                ids = [self.lead(f"5500000001{index}{i + 2:02d}") for i in range(2)]
                campaign = self.campaign(ids)
                self.enable(campaign)
                item = self.store.claim(self.now + index * 180)
                self.store.finish(item, error=True, error_code=code)
                self.assertEqual(self.row("campaigns", campaign)["status"], "paused")
                self.assertIsNone(self.store.claim(self.now + index * 180 + 179))

    def test_legacy_queue_is_not_silently_opted_in(self):
        campaign = self.campaign(self.audience(2))
        self.enable(campaign)
        with self.store.db() as c:
            c.execute("UPDATE campaigns SET recipient_uncertainty_guard=0 WHERE id=?", (campaign,))
        item = self.store.claim(self.now)
        self.store.finish(item, error=True, error_code="timeout")
        self.assertEqual(self.row("campaigns", campaign)["status"], "paused")
        self.assertIsNone(self.store.claim(self.now + 180))

    def test_individual_failure_still_consumes_daily_quota(self):
        campaign = self.campaign(self.audience(3), interval_seconds=180)
        self.enable(campaign)
        with self.store.db() as c:
            c.execute("UPDATE channels SET daily_limit=2 WHERE id=?", (self.channel,))
        first = self.store.claim(self.now)
        self.store.finish(first, error=True, error_code="timeout")
        second = self.store.claim(self.now + 180)
        self.store.finish(second, "accepted-within-quota")
        self.assertIsNone(self.store.claim(self.now + 360))
        self.assertEqual(self.row("campaigns", campaign)["status"], "running")
        with self.store.db() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM messages WHERE campaign_id=? AND status='queued'", (campaign,)).fetchone()[0], 1)

    def test_disconnection_after_individual_failure_still_pauses_campaign(self):
        campaign = self.campaign(self.audience(2))
        self.enable(campaign)
        item = self.store.claim(self.now)
        self.store.finish(item, error=True, error_code="timeout")
        self.store.receive(item["instance"], {"state": "close"}, "connection.update")
        self.assertEqual(self.row("campaigns", campaign)["status"], "paused")
        self.assertIsNone(self.store.claim(self.now + 180))

    def test_cancelled_campaign_is_not_changed_to_paused_by_late_failure_or_stale_claim(self):
        campaign = self.campaign(self.audience(2))
        self.enable(campaign)
        item = self.store.claim(self.now)
        self.store.campaign_status(campaign, self.a, "cancel", True)
        self.assertIsNone(self.store.claim(self.now + 121))
        self.store.finish(item, error=True, error_code="http_auth")
        self.assertEqual(self.row("campaigns", campaign)["status"], "cancelled")
        self.assertEqual(self.row("messages", item["id"])["status"], "unknown")

    def test_paused_campaign_is_not_resumed_by_individual_failure(self):
        campaign = self.campaign(self.audience(2))
        self.enable(campaign)
        item = self.store.claim(self.now)
        self.store.campaign_status(campaign, self.a, "pause", True)
        self.store.finish(item, error=True, error_code="timeout")
        self.assertEqual(self.row("campaigns", campaign)["status"], "paused")
        self.assertIsNone(self.store.claim(self.now + 180))

    def test_uncertain_contact_is_skipped_in_other_campaign_while_others_continue(self):
        ids = self.audience(2)
        first = self.campaign(ids[:1])
        self.enable(first)
        second = self.campaign(ids)
        self.enable(second)
        item = self.store.claim(self.now)
        self.store.finish(item, error=True, error_code="timeout")
        next_item = self.store.claim(self.now + 180)
        self.assertEqual(next_item["campaign_id"], second)
        self.assertNotEqual(next_item["contact_id"], item["contact_id"])
        with self.store.db() as c:
            blocked = c.execute("SELECT * FROM messages WHERE campaign_id=? AND contact_id=?", (second, item["contact_id"])).fetchone()
            self.assertEqual(blocked["status"], "skipped")
            self.assertIn("nao repetido", blocked["skip_reason"])

    def test_late_echo_can_resolve_finished_pending_without_new_send_or_followup(self):
        campaign = self.campaign(self.audience(2))
        self.enable(campaign)
        first = self.store.claim(self.now)
        self.store.finish(first, error=True, error_code="timeout")
        second = self.store.claim(self.now + 180)
        self.store.finish(second, "accepted-other")
        self.assertIsNone(self.store.claim(self.now + 360))
        with patch("crm_followup_runtime.accepted") as accepted:
            self.echo(first)
        accepted.assert_not_called()
        self.assertEqual(self.row("messages", first["id"])["status"], "sent")
        self.assertEqual(self.row("campaigns", campaign)["status"], "done")


class GatewayDiagnosticsTests(unittest.TestCase):
    def test_http_errors_keep_only_safe_categories_and_never_read_private_body(self):
        env = {"CRM_EVOLUTION_URL": "https://gateway.example", "CRM_EVOLUTION_KEY": "synthetic-key"}
        for status, code in ((400, "http_400"), (401, "http_auth"), (404, "http_404"),
                             (429, "http_limit"), (500, "http_server")):
            with self.subTest(status=status), patch.dict(os.environ, env), patch("crm_gateway.build_opener") as opener:
                body = Mock(wraps=io.BytesIO(b"synthetic private provider body"))
                opener.return_value.open.side_effect = HTTPError("https://gateway.example", status, "private", {}, body)
                with self.assertRaises(GatewayError) as caught:
                    EvolutionGateway().request("POST", "/message/sendText/synthetic")
                self.assertEqual(caught.exception.code, code)
                self.assertNotIn("private", str(caught.exception))
                body.read.assert_not_called()

    def test_network_and_timeout_errors_never_expose_exception_text(self):
        env = {"CRM_EVOLUTION_URL": "https://gateway.example", "CRM_EVOLUTION_KEY": "synthetic-key"}
        for error, code in ((TimeoutError("private"), "timeout"),
                            (URLError(TimeoutError("private")), "timeout"), (URLError("private"), "network_error")):
            with self.subTest(code=code), patch.dict(os.environ, env), patch("crm_gateway.build_opener") as opener:
                opener.return_value.open.side_effect = error
                with self.assertRaises(GatewayError) as caught:
                    EvolutionGateway().request("POST", "/message/sendText/synthetic")
                self.assertEqual(caught.exception.code, code)
                self.assertNotIn("private", str(caught.exception))

    def test_send_requires_a_real_confirmation_id(self):
        item = {"phone": "5500000000002", "instance": "synthetic", "body": "Synthetic text"}
        for response in ({}, [], {"key": None}, {"key": {"id": 123}}, {"key": {"id": ""}}):
            with self.subTest(response=response), patch.object(EvolutionGateway, "request", return_value=response):
                with self.assertRaises(GatewayError) as caught:
                    EvolutionGateway().send(item)
                self.assertEqual(caught.exception.code, "missing_confirmation")
