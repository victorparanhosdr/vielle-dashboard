import io
import json
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlparse

from crm_api import handle_request
from crm_campaigns import fingerprint
from crm_store import Conflict
from tests import test_crm as base


class ReactivationTests(unittest.TestCase):
    setUp = base.CrmTests.setUp
    lead = base.CrmTests.lead
    campaign = base.CrmTests.campaign
    enable = base.CrmTests.enable

    def optout(self, key):
        self.store.activity({"id": key, "doctor_id": self.a, "kind": "optout", "body": "SAIR"}, "Equipe")

    def payload(self, key):
        lead = self.store.detail(key, self.a)["lead"]
        return {"id": key, "doctor_id": self.a, "kind": "reactivate", "revision": lead["revision"],
                "body": "Cliente confirmou que enviou SAIR por engano e pediu para voltar.", "confirm_reactivation": True}

    def test_reactivation_is_individual_global_audited_and_preserves_evidence(self):
        key = self.lead(consent=False)
        other_pipeline = self.store.listing(self.a)["pipelines"][1]
        sibling = self.lead(pipeline=other_pipeline)
        other_doctor = self.lead(doctor=self.b, pipeline=self.store.listing(self.b)["pipelines"][0])
        unrelated = self.lead(number="5500000000003")
        self.optout(key)
        self.optout(unrelated)
        before = {k: self.store.detail(k, d)["lead"] for k, d in ((key, self.a), (sibling, self.a), (other_doctor, self.b))}
        self.store.activity(self.payload(key), "Maria Operadora")
        for k, d in ((key, self.a), (sibling, self.a), (other_doctor, self.b)):
            detail = self.store.detail(k, d)
            lead = detail["lead"]
            self.assertFalse(lead["opted_out"])
            self.assertEqual(lead["revision"], before[k]["revision"] + 1)
            self.assertEqual((lead["consent_at"], lead["consent_source"]), (before[k]["consent_at"], before[k]["consent_source"]))
            event = next(e for e in detail["events"] if e["kind"] == "reactivate")
            self.assertEqual(event["actor"], "Maria Operadora")
            self.assertGreater(event["created_at"], 0)
            self.assertIn("SAIR por engano", event["body"])
        self.assertTrue(any(e["kind"] == "optout" for e in self.store.detail(key, self.a)["events"]))
        self.assertTrue(self.store.detail(unrelated, self.a)["lead"]["opted_out"])

    def test_requires_strict_confirmation_reason_and_current_revision(self):
        key = self.lead()
        self.optout(key)
        payload = self.payload(key)
        invalid = [{"confirm_reactivation": v} for v in (None, False, 1, "true")]
        invalid += [{"body": v} for v in (None, "", "   ", "x" * 2001)]
        invalid += [{"revision": v} for v in (None, True, 0, payload["revision"] - 1)]
        for change in invalid:
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.store.activity({**payload, **change}, "Equipe")
        self.assertTrue(self.store.detail(key, self.a)["lead"]["opted_out"])
        self.assertFalse(any(e["kind"] == "reactivate" for e in self.store.detail(key, self.a)["events"]))

    def test_wrong_doctor_and_repeated_or_stale_requests_cannot_reactivate(self):
        key = self.lead()
        self.optout(key)
        payload = self.payload(key)
        with self.assertRaises(ValueError):
            self.store.activity({**payload, "doctor_id": self.b}, "Equipe")
        self.store.activity(payload, "Equipe")
        with self.assertRaises(Conflict):
            self.store.activity(payload, "Equipe")
        self.optout(key)
        with self.assertRaises(Conflict):
            self.store.activity(payload, "Equipe")
        self.assertTrue(self.store.detail(key, self.a)["lead"]["opted_out"])
        self.assertEqual(sum(e["kind"] == "reactivate" for e in self.store.detail(key, self.a)["events"]), 1)

    def test_no_old_messages_requeued_or_campaigns_restarted(self):
        key = self.lead()
        campaign = self.campaign([key])
        self.enable(campaign)
        self.optout(key)
        with self.store.db() as c:
            c.execute("UPDATE campaigns SET status='paused' WHERE id=?", (campaign,))
            old = [tuple(r) for r in c.execute("SELECT id,status,body FROM messages")]
        self.store.activity(self.payload(key), "Equipe")
        with self.store.db() as c:
            self.assertEqual([tuple(r) for r in c.execute("SELECT id,status,body FROM messages")], old)
            self.assertEqual(self.store.row(c, "campaigns", campaign)["status"], "paused")
        self.assertIsNone(self.store.claim(self.now))
        self.assertTrue(self.campaign([key]))

    def test_unprocessed_old_queue_is_skipped_before_reactivation(self):
        key = self.lead()
        self.enable(self.campaign([key]))
        with self.store.db() as c:
            c.execute("UPDATE contacts SET opted_out=1")
            self.assertEqual(c.execute("SELECT status FROM messages").fetchone()[0], "queued")
        self.store.activity(self.payload(key), "Equipe")
        self.assertIsNone(self.store.claim(self.now))
        with self.store.db() as c:
            message = c.execute("SELECT status,skip_reason FROM messages").fetchone()
            self.assertEqual(message[0], "skipped")
            self.assertIn("reativacao", message[1])

    def test_reactivation_invalidates_old_campaign_reviews_for_all_doctors(self):
        key = self.lead()
        sibling = self.lead(doctor=self.b, pipeline=self.store.listing(self.b)["pipelines"][0])
        with self.store.db() as c:
            before = fingerprint(c, self.b, [sibling])
        self.optout(key)
        self.store.activity(self.payload(key), "Equipe")
        with self.store.db() as c:
            self.assertNotEqual(fingerprint(c, self.b, [sibling]), before)

    def test_inbound_and_new_leads_do_not_reactivate_and_new_stop_is_respected(self):
        key = self.lead()
        self.optout(key)
        with self.store.db() as c:
            instance = self.store.row(c, "channels", self.channel)["instance"]
        def incoming(body, message_id):
            self.store.receive(instance, {"key": {"id": message_id, "remoteJid": "5500000000002@s.whatsapp.net"},
                                         "message": {"conversation": body}}, "messages.upsert")
        incoming("Ola", "hello")
        self.lead(pipeline=self.store.listing(self.a)["pipelines"][1])
        self.assertTrue(self.store.detail(key, self.a)["lead"]["opted_out"])
        self.store.activity(self.payload(key), "Equipe")
        incoming("SAIR", "stop-again")
        self.assertTrue(self.store.detail(key, self.a)["lead"]["opted_out"])
        with self.assertRaises(ValueError):
            self.campaign([key])

    def test_api_keeps_master_clinic_origin_and_audit_guards(self):
        key = self.lead()
        self.optout(key)
        payload = self.payload(key)
        def handler(master=True, origin=True, body=payload):
            h = Mock()
            h.current_user = {"id": "operator", "nome": "Maria Operadora", "is_master": master}
            h.command = "POST"
            raw = json.dumps(body).encode()
            h.headers = {"Content-Length": str(len(raw))}
            h.rfile = io.BytesIO(raw)
            h.same_origin_request.return_value = origin
            return h
        path = "/api/crm/activity?clinic=vielle"
        with patch("crm_api.store_for", return_value=self.store):
            for h, url, code in ((handler(master=False), path, 403), (handler(origin=False), path, 403),
                                 (handler(), "/api/crm/activity?clinic=other", 403),
                                 (handler(body={**payload, "doctor_id": self.b}), path, 400)):
                handle_request(h, urlparse(url))
                self.assertEqual(h.auth_json.call_args.args[1], code)
                h.server.auth_store.audit_event.assert_not_called()
            self.assertTrue(self.store.detail(key, self.a)["lead"]["opted_out"])
            h = handler()
            handle_request(h, urlparse(path))
            self.assertTrue(h.auth_json.call_args.args[0]["ok"])
            h.server.auth_store.audit_event.assert_called_once()
            self.assertEqual(h.server.auth_store.audit_event.call_args.args[:2], ("crm_activity", "operator"))


if __name__ == "__main__":
    unittest.main()
