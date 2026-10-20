from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from crm_channels import update
from crm_store import CrmStore, Conflict


class ChannelRoutingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = CrmStore(Path(self.tmp.name))
        self.store.bootstrap({"Victor Paranhos de Andrade": ["A", "B", "RegenCode"], "Outra doutora": ["Outro"]})
        self.doc = self.store.listing()["doctor_id"]
        self.a, self.b, self.course = self.store.listing(self.doc)["pipelines"]
        self.channel = self.store.channel({"doctor_id": self.doc, "phone": "5500000000001", "name": "Nome inicial"})

    def channel_row(self):
        with self.store.db() as c:
            return self.store.row(c, "channels", self.channel)

    def configure(self, **extra):
        row = self.channel_row()
        return update(self.store, {"id": row["id"], "doctor_id": self.doc, "config_revision": row["config_revision"],
            "name": "Comercial WhatsApp", "inbound_pipeline_id": self.b["id"], "inbound_stage_id": self.b["stages"][1]["id"], **extra})

    def incoming(self, number="5500000000002", ident="message"):
        return self.store.receive(self.channel_row()["instance"], {"key": {"remoteJid": number + "@s.whatsapp.net", "id": ident},
            "pushName": "Contato sintetico", "message": {"conversation": "Ola"}}, "messages.upsert")

    def test_rename_and_routing_preserve_connection_identity_and_state(self):
        before = self.channel_row()
        with self.store.db() as c:
            c.execute("UPDATE channels SET state='open',next_send_at=123 WHERE id=?", (self.channel,))
        self.configure()
        after = self.channel_row()
        self.assertEqual(after["name"], "Comercial WhatsApp")
        self.assertEqual(after["config_revision"], 2)
        for field in ("phone", "instance", "daily_limit", "doctor_id"):
            self.assertEqual(after[field], before[field])
        self.assertEqual((after["state"], after["next_send_at"]), ("open", 123))

    def test_new_lead_uses_configured_funnel_and_stage(self):
        self.configure()
        self.incoming()
        lead = self.store.listing(self.doc)["leads"][0]
        self.assertEqual((lead["pipeline_id"], lead["stage_id"]), (self.b["id"], self.b["stages"][1]["id"]))

    def test_existing_lead_is_not_moved_or_duplicated(self):
        old = self.store.lead({"doctor_id": self.doc, "pipeline_id": self.a["id"], "stage_id": self.a["stages"][2]["id"],
            "phone": "5500000000002", "name": "Ja cadastrado", "title": "Existente"}, "Equipe")
        self.configure()
        self.incoming()
        rows = self.store.listing(self.doc)["leads"]
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["id"], rows[0]["stage_id"]), (old, self.a["stages"][2]["id"]))

    def test_course_routing_does_not_search_or_require_clinical_record(self):
        self.configure(inbound_pipeline_id=self.course["id"], inbound_stage_id=self.course["stages"][0]["id"])
        lookup = Mock(side_effect=AssertionError("No clinical lookup for course leads"))
        self.store.patient_lookup = lookup
        self.incoming()
        lookup.assert_not_called()
        with self.store.db() as c:
            lead = c.execute("SELECT * FROM leads").fetchone()
            self.assertEqual(lead["pipeline_id"], self.course["id"])
            self.assertEqual(lead["source_name"], "Contato sintetico")

    def test_invalid_destinations_and_stale_updates_do_not_change_channel(self):
        other = next(d["id"] for d in self.store.listing()["doctors"] if d["id"] != self.doc)
        foreign = self.store.listing(other)["pipelines"][0]
        for changes in ({"inbound_pipeline_id": foreign["id"], "inbound_stage_id": foreign["stages"][0]["id"]},
                        {"inbound_stage_id": self.a["stages"][0]["id"]},
                        {"inbound_stage_id": self.b["stages"][-1]["id"]}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.configure(**changes)
            self.assertEqual(self.channel_row()["name"], "Nome inicial")
        self.configure()
        with self.assertRaises(Conflict):
            self.configure(config_revision=1)

    def test_default_route_remains_compatible_and_can_be_restored(self):
        self.configure()
        self.configure(inbound_pipeline_id="", inbound_stage_id="")
        self.incoming()
        lead = self.store.listing(self.doc)["leads"][0]
        self.assertEqual(lead["pipeline_id"], self.a["id"])

    def test_destination_stage_cannot_be_removed_or_closed_while_configured(self):
        self.configure()
        route = self.b["stages"][1]["id"]
        with self.assertRaises(ValueError):
            self.store.pipeline({**self.b, "stages": [s for s in self.b["stages"] if s["id"] != route]})
        stages = [dict(s) for s in self.b["stages"]]
        stages[1]["kind"] = "won"
        with self.assertRaises(ValueError):
            self.store.pipeline({**self.b, "stages": stages})
