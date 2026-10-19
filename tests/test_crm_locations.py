from pathlib import Path
import unittest

from crm_campaigns import audience, candidates, create
from crm_filters import parse_filters
from crm_store import Conflict, CrmStore, location_from_phone
from tests import test_crm as base


class ServiceLocationTests(unittest.TestCase):
    setUp = base.CrmTests.setUp
    lead = base.CrmTests.lead
    campaign = base.CrmTests.campaign

    def save(self, lead, location="", **changes):
        record = self.store.detail(lead, self.a)["lead"]
        self.store.save_service_location({"id": lead, "doctor_id": self.a,
            "service_location": location, "service_location_revision": record["service_location_revision"], **changes}, "Equipe")

    def test_ddd_rules_only_apply_to_complete_brazilian_numbers(self):
        for number, location in (("5533999990001", "capelinha"), ("553832220001", "capelinha"),
            ("+55 (31) 99999-0001", "belo-horizonte"), ("5511999990001", "sao-paulo"),
            ("5521999990001", ""), ("5541999990001", ""), ("13399990001", ""),
            ("+33 1 99 99 00 01", ""), ("5511999", "")):
            with self.subTest(number=number):
                self.assertEqual(location_from_phone(number), location)

    def test_existing_contacts_are_backfilled_once_and_survive_restart(self):
        key = self.lead("5533999990001")
        pending = self.lead("5521999990001")
        with self.store.db() as c:
            c.execute("UPDATE contacts SET service_location='',service_location_source='pending'")
            c.execute("DELETE FROM runtime WHERE key='service_locations_v1'")
        upgraded = CrmStore(Path(self.tmp.name))
        self.assertEqual(upgraded.detail(key, self.a)["lead"]["service_location"], "capelinha")
        self.assertEqual(upgraded.detail(pending, self.a)["lead"]["service_location"], "")
        self.save(key, "sao-paulo")
        reopened = CrmStore(Path(self.tmp.name))
        self.assertEqual(reopened.detail(key, self.a)["lead"]["service_location"], "sao-paulo")

    def test_manual_location_shared_across_doctors_and_funnels_without_overwriting(self):
        first = self.lead("5531999990001")
        other_pipeline = self.store.listing(self.a)["pipelines"][1]
        second = self.lead("5531999990001", pipeline=other_pipeline)
        third = self.lead("5531999990001", doctor=self.b, pipeline=self.store.listing(self.b)["pipelines"][0])
        self.save(first, "capelinha")
        self.store.reconcile_patients()
        for key, doc in ((first, self.a), (second, self.a), (third, self.b)):
            detail = self.store.detail(key, doc)
            self.assertEqual(detail["lead"]["service_location"], "capelinha")
            self.assertEqual(detail["lead"]["service_location_source"], "manual")
            self.assertTrue(any(e["kind"] == "service_location" for e in detail["events"]))
        self.save(first, use_ddd=True)
        self.assertEqual(self.store.detail(second, self.a)["lead"]["service_location"], "belo-horizonte")

    def test_clearing_location_stays_pending_until_manually_resolved(self):
        key = self.lead("5511999990001")
        self.save(key)
        self.store.reconcile_patients()
        self.assertEqual(self.store.detail(key, self.a)["lead"]["service_location"], "")
        self.assertEqual([l["id"] for l in self.store.listing(self.a, {"service_location": "pending"})["leads"]], [key])
        self.save(key, "belo-horizonte")
        self.assertEqual(self.store.listing(self.a, {"service_location": "pending"})["leads"], [])

    def test_filters_counts_and_campaigns_use_saved_patient_location(self):
        capelinha = self.lead("5538999990001")
        other = self.lead("5521999990001")
        self.lead("5533999990001", doctor=self.b, pipeline=self.store.listing(self.b)["pipelines"][0])
        self.assertEqual([l["id"] for l in self.store.listing(self.a, {"service_location": "capelinha"})["leads"]], [capelinha])
        self.assertEqual(self.store.listing(self.a)["service_location_pending"][self.p["id"]], 1)
        rows = candidates(self.store, {"doctor_id": self.a, "filters": {"service_location": "pending"}})
        self.assertEqual([l["id"] for l in rows["items"]], [other])
        self.assertEqual(rows["recipients"], 1)
        self.save(other, "capelinha")
        self.assertEqual(candidates(self.store, {"doctor_id": self.a, "filters": {"service_location": "capelinha"}})["selected"], 2)
        self.assertEqual(self.store.listing(self.a)["service_location_pending"][self.p["id"]], 0)
        for value in ("unknown", None, True, [], "Capelinha"):
            with self.assertRaises(ValueError):
                parse_filters({"service_location": value})

    def test_location_change_requires_reviewing_campaign_audience_again(self):
        key = self.lead("5511999990001")
        actor = {"id": "1", "nome": "Equipe"}
        reviewed = audience(self.store, {"doctor_id": self.a, "filters": {"service_location": "sao-paulo"}}, actor)
        self.save(key, "capelinha")
        with self.assertRaises(Conflict):
            create(self.store, {"doctor_id": self.a, "channel_id": self.channel, "preview_id": reviewed["preview_id"],
                "name": "Retornos", "body": "Ola, {nome}."}, actor)
        self.assertEqual(self.store.listing(self.a)["campaigns"], [])

    def test_location_updates_validate_ownership_revision_and_do_not_touch_optouts(self):
        key = self.lead("5533999990001")
        before = self.store.detail(key, self.a)["lead"]
        self.store.activity({"id": key, "doctor_id": self.a, "kind": "optout", "body": "SAIR"}, "Equipe")
        with self.assertRaises(ValueError):
            self.save(key, "capelinha", doctor_id=self.b)
        for value in ("elsewhere", None, [], 5):
            with self.assertRaises(ValueError):
                self.save(key, value)
        self.save(key, "sao-paulo")
        with self.assertRaises(Conflict):
            self.save(key, "belo-horizonte", service_location_revision=before["service_location_revision"])
        lead = self.store.detail(key, self.a)["lead"]
        self.assertEqual(lead["service_location"], "sao-paulo")
        self.assertTrue(lead["opted_out"])
        self.assertEqual(lead["consent_at"], before["consent_at"])

    def test_inbound_contacts_and_inbox_filters_have_the_same_location_rules(self):
        channel = self.store.listing(self.a)["channels"][0]
        for number in ("5533999990001", "5521999990001"):
            self.store.receive(channel["instance"], {"key": {"id": number, "remoteJid": number + "@s.whatsapp.net"},
                "message": {"conversation": "Gostaria de agendar"}}, "messages.upsert")
        capelinha = self.store.inbox(self.a, "user", filters={"service_location": "capelinha"})
        pending = self.store.inbox(self.a, "user", filters={"service_location": "pending"})
        self.assertEqual(capelinha["total"], 1)
        self.assertEqual(capelinha["conversations"][0]["phone"], "5533999990001")
        self.assertEqual(pending["total"], 1)
        self.assertEqual(pending["counts"]["unread"], 1)

    def test_secondary_patient_phone_does_not_silently_change_saved_location(self):
        self.store.patient_lookup = lambda _: {"status": "matched", "uuid": "patient", "name": "Paciente"}
        key = self.lead("5533999990001")
        self.lead("5511999990001", pipeline=self.store.listing(self.a)["pipelines"][1])
        self.assertEqual(self.store.detail(key, self.a)["lead"]["service_location"], "capelinha")
        with self.store.db() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM contacts").fetchone()[0], 1)
