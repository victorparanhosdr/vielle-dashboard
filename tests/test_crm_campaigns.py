from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import json
import time
import unittest
from unittest.mock import Mock

from tests import test_crm as base
from crm_campaigns import audience, create, dashboard, detail
from crm_store import Conflict, TZ
from crm_worker import tick


class CampaignTests(unittest.TestCase):
    setUp=base.CrmTests.setUp
    lead=base.CrmTests.lead
    campaign=base.CrmTests.campaign
    enable=base.CrmTests.enable

    def actor(self):return {"id":"operator","nome":"Equipe"}

    def preview(self, **values):
        return audience(self.store,{"doctor_id":self.a,"pipeline_id":self.p["id"],**values},self.actor())

    def payload(self, preview):
        return {"doctor_id":self.a,"preview_id":preview["preview_id"],"channel_id":self.channel,
            "name":"Retornos","body":"Olá, {nome}. Equipe {doutor}.","weekdays":[0,1,2,3,4],"minute_from":30,"hour_from":9,"hour_to":18,"tag":"Retorno enviado"}

    def test_preview_excludes_no_consent_optout_and_ambiguous(self):
        self.lead()
        self.lead(number="5500000000003",consent=False)
        self.lead(number="5500000000004")
        with self.store.db() as c:c.execute("UPDATE contacts SET opted_out=1 WHERE phone='5500000000004'")
        data=self.preview()
        self.assertEqual((data["selected"],data["recipients"],data["excluded"]),(3,1,2))
        self.assertEqual(data["reasons"]["Sem autorizacao"],1)
        with self.assertRaises(ValueError):self.preview(pipeline_id=self.store.listing(self.b)["pipelines"][0]["id"])

    def test_all_matching_rows_not_just_first_thousand_and_dedup(self):
        now=time.time()
        with self.store.db() as c:
            for i in range(1101):
                cid=f"contact-{i}";lid=f"lead-{i}"
                c.execute("INSERT INTO contacts(id,name,phone) VALUES(?,?,?)",(cid,f"Pessoa {i}",f"551190{i:07d}"))
                c.execute("INSERT INTO contact_phones VALUES(?,?)",(f"551190{i:07d}",cid))
                c.execute("INSERT INTO leads(id,doctor_id,pipeline_id,stage_id,contact_id,title,consent_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",(lid,self.a,self.p["id"],self.p["stages"][0]["id"],cid,"Retorno",now,now,now))
        data=self.preview()
        self.assertEqual(data["recipients"],1101)
        self.assertEqual(len(data["sample"]),100)
        key=create(self.store,self.payload(data),self.actor())
        self.assertEqual(detail(self.store,self.a,key,{})["total"],1101)

    def test_snapshot_changed_is_not_silently_sent_and_request_is_idempotent(self):
        key=self.lead();preview=self.preview()
        with self.store.db() as c:c.execute("UPDATE contacts SET name='Nome corrigido'")
        with self.assertRaises(Conflict):create(self.store,self.payload(preview),self.actor())
        preview=self.preview()
        with ThreadPoolExecutor(max_workers=2) as pool:
            ids=list(pool.map(lambda _:create(self.store,self.payload(preview),self.actor()),range(2)))
        self.assertEqual(ids[0],ids[1])
        with self.assertRaises(LookupError):create(self.store,self.payload(preview),{"id":"other","nome":"Outro"})
        with self.store.db() as c:self.assertEqual(c.execute("SELECT count(*) FROM campaigns").fetchone()[0],1)

    def test_same_contact_in_multiple_pipelines_gets_one_message(self):
        self.lead()
        other=next(p for p in self.store.listing(self.a)["pipelines"] if p["id"]!=self.p["id"])
        self.lead(pipeline=other)
        preview=self.preview(pipeline_id="")
        self.assertEqual((preview["selected"],preview["recipients"],preview["duplicates"]),(2,1,1))
        key=create(self.store,self.payload(preview),self.actor())
        self.assertEqual(detail(self.store,self.a,key,{})["total"],1)

    def test_weekdays_and_minute_window_are_observed(self):
        self.lead();key=create(self.store,self.payload(self.preview()),self.actor());self.enable(key)
        monday=datetime(2026,10,19,9,20,tzinfo=TZ).timestamp()
        self.assertIsNone(self.store.claim(monday))
        saturday=datetime(2026,10,24,10,0,tzinfo=TZ).timestamp()
        self.assertIsNone(self.store.claim(saturday))
        self.assertIsNotNone(self.store.claim(monday+20*60))

    def test_pending_contact_phone_change_never_redirects_campaign(self):
        self.lead();key=create(self.store,self.payload(self.preview()),self.actor());self.enable(key)
        with self.store.db() as c:c.execute("UPDATE contacts SET phone='5511999991234'")
        self.assertIsNone(self.store.claim(self.now))
        row=detail(self.store,self.a,key,{})["items"][0]
        self.assertEqual(row["status"],"skipped")
        self.assertIn("Telefone alterado",row["skip_reason"])

    def test_tag_only_after_provider_acceptance_and_unknown_never_retries(self):
        self.lead();key=create(self.store,self.payload(self.preview()),self.actor());self.enable(key)
        gateway=Mock();gateway.send.return_value="provider-1"
        self.assertTrue(tick(self.store,gateway,self.now))
        with self.store.db() as c:self.assertEqual(c.execute("SELECT label FROM contact_tags").fetchone()[0],"Retorno enviado")
        self.assertFalse(tick(self.store,gateway,self.now+70))
        self.assertEqual(gateway.send.call_count,1)

    def test_reports_are_scoped_and_delivery_is_not_invented(self):
        self.lead();key=create(self.store,self.payload(self.preview()),self.actor());self.enable(key)
        item=self.store.claim(self.now);self.store.finish(item,"provider-1")
        data=dashboard(self.store,self.a,{})
        self.assertEqual(data["metrics"]["sent"],1)
        self.assertNotIn("delivered",data["metrics"])
        self.assertEqual(dashboard(self.store,self.b,{})["metrics"]["sent"],0)
        with self.assertRaises(ValueError):detail(self.store,self.b,key,{})

    def test_enabling_never_starts_old_campaigns(self):
        self.lead();key=create(self.store,self.payload(self.preview()),self.actor());self.enable(key)
        self.assertFalse(self.store.campaigns_allowed())
        self.store.enable_campaigns()
        self.assertTrue(self.store.campaigns_allowed())
        self.assertEqual(detail(self.store,self.a,key,{})["campaign"]["status"],"paused")
