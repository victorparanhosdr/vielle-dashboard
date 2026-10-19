from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import json
import time
import unittest
from unittest.mock import Mock, patch

from tests import test_crm as base
from crm_campaigns import audience, candidates, create, dashboard, detail
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
            "name":"Retornos","body":"Olá, {nome}. Equipe {doutor}.","weekdays":[0,1,2,3,4],"minute_from":30,"hour_from":9,"hour_to":18,"tag":"Retorno enviado","start_at":"2026-10-19T09:00"}

    def test_preview_allows_default_contacts_but_excludes_optout_and_ambiguous(self):
        self.lead()
        self.lead(number="5500000000003",consent=False)
        self.lead(number="5500000000004")
        self.lead(number="5500000000005",consent=False)
        self.store.patient_lookup=lambda number:{"status":"review" if number.endswith("5") else "new"}
        with self.store.db() as c:c.execute("UPDATE contacts SET opted_out=1 WHERE phone='5500000000004'")
        data=self.preview()
        self.assertEqual((data["selected"],data["recipients"],data["excluded"]),(4,2,2))
        self.assertEqual(data["reasons"],{"Descadastrado":1,"Cadastro a conferir":1})
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

    def test_candidates_show_blocked_people_and_real_funnel_counts(self):
        self.lead()
        self.lead(number="5500000000003",consent=False)
        with self.store.db() as c:c.execute("UPDATE contacts SET opted_out=1 WHERE phone='5500000000003'")
        other=next(p for p in self.store.listing(self.a)["pipelines"] if p["id"]!=self.p["id"])
        self.lead(number="5500000000004",pipeline=other)
        data=candidates(self.store,{"doctor_id":self.a,"pipeline_id":self.p["id"]})
        self.assertEqual((data["selected"],data["recipients"]),(2,1))
        self.assertEqual(len(data["items"]),2)
        self.assertIn("Descadastrado",[l["campaign_reason"] for l in data["items"]])
        self.assertEqual(sum(p["count"] for p in data["pipelines"]),3)
        self.assertEqual(next(p for p in data["pipelines"] if p["id"]==self.p["id"])["stages"][0]["count"],2)
        self.assertNotIn("_experts_search",data["items"][0])

    def test_empty_explicit_stages_do_not_silently_select_everybody(self):
        self.lead()
        data=self.preview(stage_ids=[],stages_explicit=True)
        self.assertEqual(data["recipients"],0)
        self.assertEqual(self.preview()["recipients"],1)

    def test_manual_selection_and_all_filter_exclusions_are_exact(self):
        a=self.lead();b=self.lead(number="5500000000003")
        self.assertEqual(self.preview(lead_ids=[])["recipients"],0)
        data=self.preview(lead_ids=[a])
        self.assertEqual(data["sample"][0]["id"],a)
        self.assertEqual(data["recipients"],1)
        data=self.preview(exclude_lead_ids=[a])
        self.assertEqual(data["sample"][0]["id"],b)
        self.assertEqual(data["recipients"],1)
        self.assertEqual(self.preview(filters={"search":"(000) 000-0003"})["recipients"],1)

    def test_foreign_selection_never_crosses_doctor_boundary(self):
        foreign=self.lead(doctor=self.b,pipeline=self.store.listing(self.b)["pipelines"][0])
        with self.assertRaises(ValueError):self.preview(lead_ids=[foreign])
        with self.assertRaises(ValueError):self.preview(pipeline_ids=[self.store.listing(self.b)["pipelines"][0]["id"]])
        with self.assertRaises(ValueError):self.preview(lead_ids="all")

    def test_pagination_is_only_visual_select_all_still_covers_all_pages(self):
        for i in range(55):self.lead(number=f"5500000{i:06d}")
        payload={"doctor_id":self.a,"pipeline_id":self.p["id"]}
        first=candidates(self.store,payload)
        second=candidates(self.store,{**payload,"page":2})
        self.assertEqual((len(first["items"]),len(second["items"])),(50,5))
        self.assertEqual(first["selected"],55)
        self.assertTrue(first["has_more"])
        self.assertFalse(second["has_more"])
        excluded=[first["items"][0]["id"],second["items"][0]["id"]]
        data=self.preview(exclude_lead_ids=excluded)
        key=create(self.store,self.payload(data),self.actor())
        self.assertEqual(detail(self.store,self.a,key,{})["total"],53)

    def test_empty_funnel_is_different_from_a_blocked_audience(self):
        self.lead(consent=False)
        with self.store.db() as c:c.execute("UPDATE contacts SET opted_out=1")
        other=next(p for p in self.store.listing(self.a)["pipelines"] if p["id"]!=self.p["id"])
        empty=candidates(self.store,{"doctor_id":self.a,"pipeline_id":other["id"]})
        blocked=candidates(self.store,{"doctor_id":self.a,"pipeline_id":self.p["id"]})
        self.assertEqual((empty["selected"],empty["recipients"]),(0,0))
        self.assertEqual((blocked["selected"],blocked["recipients"]),(1,0))
        self.assertEqual(blocked["reasons"],{"Descadastrado":1})

    def test_amount_sort_is_global_exact_and_keeps_unknown_amounts_last(self):
        ids=[self.lead(number=f"5500002{i:06d}") for i in range(55)]
        def values(c,leads,doctor,include_search=False):
            for l in leads:
                i=ids.index(l["id"])
                l["customer_history"]={"sales_state":"partial" if i==54 else "ready","quotes_state":"partial" if i==54 else "ready",
                    "sales_cents":900001 if i==0 else i*100,"pending_quotes_cents":900002 if i==0 else i*100}
        with patch.object(self.store,"attach_customer_values",side_effect=values):
            payload={"doctor_id":self.a,"order_by":"quotes"}
            desc=candidates(self.store,payload)
            self.assertEqual(desc["items"][0]["id"],ids[0])
            self.assertEqual(desc["items"][0]["customer_history"]["pending_quotes_cents"],900002)
            tail=candidates(self.store,{**payload,"page":2})
            self.assertEqual(tail["items"][-1]["id"],ids[54])
            asc=candidates(self.store,{**payload,"order_dir":"asc"})
            self.assertEqual(asc["items"][0]["id"],ids[1])
            self.assertEqual(candidates(self.store,{**payload,"order_dir":"asc","page":2})["items"][-1]["id"],ids[54])
            spent=candidates(self.store,{**payload,"order_by":"spent"})
            self.assertEqual(spent["items"][0]["id"],ids[0])
            self.assertEqual(self.preview(order_by="quotes",order_dir="desc")["recipients"],55)

    def test_sorting_name_phase_and_send_readiness(self):
        a=self.lead();b=self.lead(number="5500000000003",consent=False)
        with self.store.db() as c:
            c.execute("UPDATE contacts SET name='Ágata' WHERE id=(SELECT contact_id FROM leads WHERE id=?)",(a,))
            c.execute("UPDATE contacts SET name='Beatriz' WHERE id=(SELECT contact_id FROM leads WHERE id=?)",(b,))
            c.execute("UPDATE contacts SET opted_out=1 WHERE id=(SELECT contact_id FROM leads WHERE id=?)",(b,))
            c.execute("UPDATE leads SET stage_id=? WHERE id=?",(self.p["stages"][1]["id"],a))
        base={"doctor_id":self.a,"pipeline_id":self.p["id"]}
        first=lambda **args:candidates(self.store,{**base,**args})["items"][0]["id"]
        self.assertEqual(first(order_by="name"),a)
        self.assertEqual(first(order_by="name",order_dir="desc"),b)
        self.assertEqual(first(order_by="pipeline"),b)
        self.assertEqual(first(order_by="pipeline",order_dir="desc"),a)
        self.assertEqual(first(order_by="eligibility"),a)
        self.assertEqual(first(order_by="eligibility",order_dir="desc"),b)
        for args in ({"order_by":"unknown"},{"order_by":[]},{"order_dir":"invalid"},{"order_dir":[]}):
            with self.assertRaises(ValueError):candidates(self.store,{**base,**args})

    def test_existing_and_new_contacts_can_dispatch_without_fabricated_consent(self):
        key=self.lead(consent=False)
        data=self.preview()
        self.assertEqual((data["recipients"],data["reasons"]),(1,{}))
        campaign=create(self.store,self.payload(data),self.actor())
        self.enable(campaign)
        self.assertIsNotNone(self.store.claim(self.now))
        with self.store.db() as c:
            row=c.execute("SELECT consent_at,consent_source FROM leads WHERE id=?",(key,)).fetchone()
            self.assertEqual(tuple(row),(None,""))
            self.assertEqual(c.execute("SELECT count(*) FROM events WHERE kind='consent'").fetchone()[0],0)

    def test_optout_stays_global_even_for_new_opportunities_and_incoming_messages(self):
        key=self.lead(consent=False)
        campaign=create(self.store,self.payload(self.preview()),self.actor());self.enable(campaign)
        self.store.activity({"id":key,"doctor_id":self.a,"kind":"optout","body":"Pediu para parar"},"Equipe")
        other=self.store.listing(self.b)["pipelines"][0]
        foreign=self.lead(doctor=self.b,pipeline=other,consent=False)
        with self.store.db() as c:channel=self.store.row(c,"channels",self.channel)
        self.store.receive(channel["instance"],{"key":{"id":"after-optout","remoteJid":"5500000000002@s.whatsapp.net"},"message":{"conversation":"Olá"}},"messages.upsert")
        self.assertTrue(self.store.detail(foreign,self.b)["lead"]["opted_out"])
        self.assertIsNone(self.store.claim(self.now))
        self.assertEqual(self.preview()["recipients"],0)
