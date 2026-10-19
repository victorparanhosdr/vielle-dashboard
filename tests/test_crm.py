from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from crm_store import CrmStore, Conflict, TZ
from crm_worker import tick
from crm_gateway import EvolutionGateway


class CrmTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = CrmStore(Path(self.tmp.name))
        self.store.bootstrap({"Doutor A": ["Organico", "Indicacao"], "Doutor B": ["Organico"]})
        self.a = next(d["id"] for d in self.store.listing()["doctors"] if d["name"] == "Doutor A")
        self.b = next(d["id"] for d in self.store.listing()["doctors"] if d["name"] == "Doutor B")
        self.p = self.store.listing(self.a)["pipelines"][0]
        self.channel = self.store.channel({"doctor_id": self.a, "name": "Atendimento", "phone": "5500000000001", "daily_limit": 50})
        self.now = datetime(2026, 10, 19, 10, 0, tzinfo=TZ).timestamp()

    def lead(self, number="5500000000002", consent=True, doctor=None, pipeline=None):
        doctor = doctor or self.a
        pipeline = pipeline or self.p
        return self.store.lead({"doctor_id": doctor, "pipeline_id": pipeline["id"], "stage_id": pipeline["stages"][0]["id"],
            "name": "Cliente Exemplo", "phone": number, "title": "Solicitou contato", "value_cents": 120000,
            "consent": consent, "consent_source": "Solicitacao no formulario" if consent else ""}, "Equipe")

    def campaign(self, ids=None, **changes):
        ids = ids or [self.lead()]
        return self.store.campaign({"doctor_id": self.a, "channel_id": self.channel, "name": "Retornos",
            "body": "Ola, {nome}. Aqui e a equipe de {doutor}.", "lead_ids": ids,
            "start_at": "2026-10-19T09:00", **changes}, "Equipe")

    def enable(self, key):
        with self.store.db() as c:
            c.execute("UPDATE channels SET state='open' WHERE id=?", (self.channel,))
        self.store.campaign_status(key, self.a, "start", True)

    def update(self, key, stage):
        lead = self.store.detail(key, self.a)["lead"]
        return self.store.lead({**lead, "stage_id": stage}, "Equipe")

    def test_bootstrap_and_multiple_numbers_are_idempotent(self):
        self.store.bootstrap({"Doutor A": ["Organico", "Indicacao"]})
        self.assertEqual(len(self.store.listing(self.a)["pipelines"]), 2)
        self.store.channel({"doctor_id": self.a, "name": "Recepcao", "phone": "5500000000003"})
        self.assertEqual(len(self.store.listing(self.a)["channels"]), 2)

    def test_wrong_doctor_and_wrong_stage_are_rejected(self):
        b = self.store.listing(self.b)["pipelines"][0]
        with self.assertRaises(ValueError):
            self.store.lead({"doctor_id": self.a, "pipeline_id": b["id"]}, "Equipe")
        lead = self.lead()
        with self.assertRaises(ValueError): self.store.detail(lead, self.b)
        with self.assertRaises(ValueError): self.update(lead, b["stages"][0]["id"])
        self.assertEqual(self.store.listing(self.b)["leads"], [])

    def test_pipeline_reorder_rename_and_used_stage_protection(self):
        self.lead()
        p = self.p
        self.store.pipeline({**p, "stages": list(reversed(p["stages"])), "name": "Novo nome"})
        saved = next(q for q in self.store.listing(self.a)["pipelines"] if q["id"] == p["id"])
        self.assertEqual(saved["stages"][0]["id"], p["stages"][-1]["id"])
        with self.assertRaises(Conflict): self.store.pipeline(p)
        with self.assertRaises(ValueError): self.store.pipeline({**saved, "stages": saved["stages"][:-1]})

    def test_game_does_not_reward_repeating_stages_or_broadcasts(self):
        key = self.lead()
        first, second = [s["id"] for s in self.p["stages"][:2]]
        self.update(key, second);self.update(key, first);self.update(key, second)
        for _ in range(2): self.store.activity({"id": key,"doctor_id":self.a,"kind":"followup","body":"Conversei e registrei o proximo passo"}, "Equipe")
        self.assertEqual(self.store.listing(self.a)["game"]["xp"], 8)
        self.campaign([key])
        self.assertEqual(self.store.listing(self.a)["game"]["xp"], 8)
        self.assertEqual(self.store.listing(self.b)["game"]["xp"], 0)

    def test_lead_optimistic_lock(self):
        key = self.lead();old = self.store.detail(key, self.a)["lead"]
        self.update(key, self.p["stages"][1]["id"])
        with self.assertRaises(Conflict): self.store.lead(old, "Equipe")

    def test_missing_consent_or_optout_block_campaign(self):
        key = self.lead(consent=False)
        with self.assertRaises(ValueError): self.campaign([key])
        self.store.activity({"id":key,"doctor_id":self.a,"kind":"consent","body":"Autorizou no formulario"}, "Equipe")
        self.store.activity({"id":key,"doctor_id":self.a,"kind":"optout","body":"Solicitou descadastro"}, "Equipe")
        with self.assertRaises(ValueError): self.campaign([key])
        with self.assertRaises(ValueError): self.store.activity({"id":key,"doctor_id":self.a,"kind":"consent","body":"Reativar"}, "Equipe")

    def test_wrong_doctor_campaign_and_wrong_channel(self):
        lead = self.lead(doctor=self.b,pipeline=self.store.listing(self.b)["pipelines"][0])
        with self.assertRaises(ValueError): self.campaign([lead])
        channel = self.store.channel({"doctor_id":self.b,"phone":"5500000000004","name":"B"})
        with self.assertRaises(ValueError): self.campaign(channel_id=channel)

    def test_campaign_deduplicates_same_phone_across_pipelines(self):
        first = self.lead()
        second = self.lead(pipeline=self.store.listing(self.a)["pipelines"][1])
        self.campaign([first,first,second])
        c = self.store.listing(self.a)["campaigns"][0]
        self.assertEqual(c["counts"], {"queued":1})
        with self.store.db() as db:
            body = db.execute("SELECT body FROM messages").fetchone()[0]
            self.assertIn("Cliente",body);self.assertIn("SAIR",body);self.assertNotIn("{nome}",body)

    def test_interval_and_hours_validation(self):
        lead=self.lead()
        for changes in ({"interval_seconds":0},{"interval_seconds":29},{"hour_from":7},{"hour_to":22},{"hour_from":18,"hour_to":17}):
            with self.subTest(changes=changes), self.assertRaises(ValueError): self.campaign([lead],**changes)

    def test_disabled_or_disconnected_cannot_start(self):
        key=self.campaign()
        with self.assertRaises(ValueError): self.store.campaign_status(key,self.a,"start",False)
        with self.assertRaises(ValueError): self.store.campaign_status(key,self.a,"start",True)

    def test_cadence_shared_between_campaigns_and_inflight_lock(self):
        one,two=self.lead(),self.lead("5500000000003")
        a,b=self.campaign([one]),self.campaign([two]);self.enable(a);self.enable(b)
        claimed=self.store.claim(self.now)
        self.assertIsNotNone(claimed)
        self.assertIsNone(self.store.claim(self.now+65))
        self.store.finish(claimed,"provider1")
        self.assertIsNone(self.store.claim(self.now+10))
        self.assertIsNotNone(self.store.claim(self.now+65))

    def test_two_workers_do_not_dispatch_twice(self):
        key=self.campaign();self.enable(key)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:self.store.claim(self.now),range(2)))
        self.assertEqual(sum(r is not None for r in results),1)

    def test_hours_schedule_pause_and_cancel(self):
        key=self.campaign();self.enable(key)
        self.assertIsNone(self.store.claim(self.now-8*3600))
        self.store.campaign_status(key,self.a,"pause")
        self.assertIsNone(self.store.claim(self.now))
        self.store.campaign_status(key,self.a,"cancel")
        self.assertEqual(self.store.listing(self.a)["campaigns"][0]["counts"],{"cancelled":1})

    def test_daily_limit_across_campaigns(self):
        ids=[self.lead(),self.lead("5500000000003")]
        key=self.campaign(ids);self.enable(key)
        with self.store.db() as db: db.execute("UPDATE channels SET daily_limit=1")
        item=self.store.claim(self.now);self.store.finish(item,"provider1")
        self.assertIsNone(self.store.claim(self.now+65))

    def test_failures_pause_and_do_not_retry(self):
        key=self.campaign();self.enable(key)
        item=self.store.claim(self.now);self.store.finish(item,error=True)
        self.assertIsNone(self.store.claim(self.now+300))
        with self.assertRaises(ValueError):self.store.campaign_status(key,self.a,"start",True)
        campaign=self.store.listing(self.a)["campaigns"][0]
        self.assertEqual(campaign["status"],"paused");self.assertEqual(campaign["counts"],{"unknown":1})

    def test_stale_inflight_is_not_resent(self):
        key=self.campaign();self.enable(key);self.store.claim(self.now)
        self.assertIsNone(self.store.claim(self.now+121))
        self.assertEqual(self.store.listing(self.a)["campaigns"][0]["counts"],{"unknown":1})

    def test_inbound_idempotent_and_global_optout(self):
        key=self.campaign();self.enable(key)
        channel=self.store.listing(self.a)["channels"][0]
        data={"key":{"id":"in1","fromMe":False,"remoteJid":"5500000000002@s.whatsapp.net"},"message":{"conversation":"SAIR"}}
        for _ in range(2):self.store.receive(channel["instance"],data,"messages.upsert")
        self.assertIsNone(self.store.claim(self.now))
        with self.store.db() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM messages WHERE direction='in'").fetchone()[0],1)
        self.assertEqual(self.store.listing(self.a)["campaigns"][0]["counts"],{"skipped":1})

    def test_inbound_creates_opportunity_without_marketing_consent(self):
        channel=self.store.listing(self.a)["channels"][0]
        self.store.receive(channel["instance"],{"key":{"id":"in1","remoteJid":"5500000000099@s.whatsapp.net"},"message":{"conversation":"Gostaria de informacoes"}},"messages.upsert")
        lead=self.store.listing(self.a)["leads"][0]
        self.assertIsNone(lead["consent_at"])
        self.assertEqual(len(self.store.detail(lead["id"],self.a)["messages"]),1)
        self.assertEqual(self.store.listing(self.b)["leads"],[])

    def test_worker_timeout_does_not_retry(self):
        key=self.campaign();self.enable(key)
        gateway=Mock();gateway.send.side_effect=TimeoutError()
        tick(self.store,gateway,self.now);tick(self.store,gateway,self.now+65)
        gateway.send.assert_called_once()

    def test_connection_loss_pauses_campaigns(self):
        key=self.campaign();self.enable(key)
        channel=self.store.listing(self.a)["channels"][0]
        self.store.receive(channel["instance"],{"state":"close"},"connection.update")
        self.assertEqual(self.store.listing(self.a)["campaigns"][0]["status"],"paused")

    def test_gateway_requires_webhook_before_network_and_hides_error(self):
        with patch.dict(os.environ,{"CRM_WEBHOOK_URL":"","CRM_WEBHOOK_SECRET":""}), patch.object(EvolutionGateway,"request") as request:
            with self.assertRaises(ValueError):EvolutionGateway().connect({})
            request.assert_not_called()

    def test_worker_heartbeat_expires(self):
        self.assertFalse(self.store.worker_online());self.store.heartbeat();self.assertTrue(self.store.worker_online())
        self.store.heartbeat(0);self.assertFalse(self.store.worker_online())

    def test_gateway_adapter_contract_keeps_secret_out_of_results(self):
        channel={"instance":"doc4docs-vielle-test","phone":"5500000000011"}
        env={"CRM_EVOLUTION_URL":"https://gateway.example","CRM_EVOLUTION_KEY":"private-key",
             "CRM_WEBHOOK_URL":"https://doc4docs.example/api/crm-webhook","CRM_WEBHOOK_SECRET":"x"*40}
        with patch.dict(os.environ,env), patch.object(EvolutionGateway,"request",side_effect=[[],{}, {}, {"base64":"data:image/png;base64,AAA"}]) as req:
            result=EvolutionGateway().connect(channel)
            self.assertEqual(result,{"qr":"data:image/png;base64,AAA"})
            self.assertEqual(req.call_args_list[1].args[1],"/instance/create")
            self.assertEqual(req.call_args_list[2].args[2]["webhook"]["headers"],{"X-DOC4DOCS-Webhook":"x"*40})
        with patch.dict(os.environ,env), patch.object(EvolutionGateway,"request",return_value={"key":{"id":"message-1"}}) as req:
            self.assertEqual(EvolutionGateway().send({**channel,"body":"Teste"}),"message-1")
            self.assertEqual(req.call_args.args[2],{"number":"5500000000011","text":"Teste"})


if __name__ == "__main__":
    unittest.main()
