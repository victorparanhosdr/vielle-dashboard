import json
import sqlite3
import time
import unittest

from crm_filters import parse_filters
from tests import test_crm_commercial as base


class FilterTests(unittest.TestCase):
    setUp = base.CommercialTests.setUp
    add = base.CommercialTests.add

    def rows(self, **filters):
        return self.store.listing(self.doctor, filters)["leads"]

    def new_lead(self, number="5500000000002"):
        pipeline=self.store.listing(self.doctor)["pipelines"][0]
        return self.store.lead({"doctor_id":self.doctor,"pipeline_id":pipeline["id"],"stage_id":pipeline["stages"][0]["id"],
            "name":"Novo contato","phone":number,"title":"Primeiro contato"},"Equipe")

    def test_closed_sales_amount_count_and_doctor_scope(self):
        self.add("one",final_amount=310000);self.add("two",final_amount=410000)
        self.add("other",seller={"uuid":"doc-b","name":"Outro"},final_amount=99999999)
        self.add("quote",kind="sale_quote",status="open",final_amount=99999999)
        self.add("deleted",deleted=True,final_amount=99999999)
        self.assertEqual(len(self.rows(sales="yes",min_spent_cents=700000,max_spent_cents=720000,min_sales=2)),1)
        self.assertEqual(self.rows(max_spent_cents=719999),[])
        self.assertEqual(self.rows(sales="no"),[])
        self.assertEqual(self.rows(min_sales=3),[])
        self.assertEqual(self.rows()[0]["customer_history"]["sales_cents"],720000)

    def test_known_zero_sales_is_different_from_unlinked_and_unavailable(self):
        self.new_lead()
        self.assertEqual([r["id"] for r in self.rows(sales="no")],[self.lead])
        self.assertEqual(len(self.rows(identity="new")),1)
        self.store.commercial_source=None
        data=self.store.listing(self.doctor,{"sales":"no"})
        self.assertEqual(data["leads"],[])
        self.assertEqual(data["filter_incomplete"],2)

    def test_quote_filters_distinguish_open_lost_expired_and_converted(self):
        self.add("expired",kind="sale_quote",status="expired")
        self.add("lost",kind="sale_quote",status="lost")
        self.add("won",kind="sale_quote",status="won")
        self.assertEqual(self.rows(quotes="active"),[])
        for kind in ("pending","expired","lost"):
            self.assertEqual(len(self.rows(quotes=kind)),1)
        self.assertEqual(self.rows(quotes="none"),[])
        self.add("open",kind="sale_quote",status="open")
        self.assertEqual(len(self.rows(quotes="active")),1)
        self.add("converted-sale",sale_quote_uuid="open")
        self.assertEqual(self.rows(quotes="active"),[])

    def test_partial_sales_dont_claim_no_purchase_or_amount_but_safe_quotes_still_work(self):
        self.add("bad",final_amount=None)
        with sqlite3.connect(self.path) as c:c.execute("UPDATE clinica_sales SET total=NULL WHERE uuid='bad'")
        self.add("open",kind="sale_quote",status="open")
        for filters in ({"sales":"no"},{"sales":"yes"},{"min_spent_cents":0},{"max_spent_cents":999999}):
            self.assertEqual(self.rows(**filters),[])
        self.assertEqual(len(self.rows(quotes="active")),1)

    def test_search_client_experts_profile_and_own_procedures_without_exposing_search_blob(self):
        with sqlite3.connect(self.path) as c:
            c.execute("UPDATE clinica_patients SET raw_json=? WHERE uuid='patient'",(json.dumps({"email":"ana@example.test","cpf":"123.456.789-01"}),))
        self.add("own",description="Plano estético",procedures=[{"name":"Rejuvenescimento facial"}])
        self.add("other",description="Procedimento secreto",seller={"uuid":"doc-b","name":"Outro"})
        for search in ("PACIENTE","5500000000001","ana@example.test","12345678901","estetico","rejuvenescimento","Avaliação"):
            rows=self.rows(search=search)
            self.assertEqual(len(rows),1,search)
            self.assertNotIn("_experts_search",rows[0])
        self.assertEqual(self.rows(search="secreto"),[])

    def test_filters_run_before_the_1000_result_cap(self):
        self.add("own",final_amount=800000)
        pipeline=self.store.listing(self.doctor)["pipelines"][0]
        now=time.time()+10
        with self.store.db() as c:
            for i in range(1001):
                contact=f"bulk-{i}";number=f"551111{i:07d}"
                c.execute("INSERT INTO contacts(id,name,phone,experts_status) VALUES(?,?,?,'new')",(contact,"Novo contato",number))
                c.execute("INSERT INTO contact_phones VALUES(?,?)",(number,contact))
                c.execute("INSERT INTO leads(id,doctor_id,pipeline_id,stage_id,contact_id,title,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                    (f"lead-{i}",self.doctor,pipeline["id"],pipeline["stages"][0]["id"],contact,"Mais recente",now,now))
        self.assertEqual(len(self.rows()),1000)
        data=self.store.listing(self.doctor,{"sales":"yes","min_spent_cents":800000})
        self.assertEqual([r["id"] for r in data["leads"]],[self.lead])
        self.assertEqual(data["matched_leads"],1)
        self.assertEqual(data["total_leads"],1002)

    def test_inbox_uses_same_financial_filters_without_marking_read(self):
        self.add("sale",final_amount=800000)
        self.add("open",kind="sale_quote",status="open")
        channel=self.store.channel({"doctor_id":self.doctor,"name":"Comercial","phone":"5500000000099"})
        with self.store.db() as c:instance=self.store.row(c,"channels",channel)["instance"]
        self.store.receive(instance,{"key":{"id":"in-1","remoteJid":"5500000000001@s.whatsapp.net"},"message":{"conversation":"Dúvida de atendimento"}},"messages.upsert")
        data=self.store.inbox(self.doctor,"user",filters={"sales":"yes","quotes":"active","min_spent_cents":700000})
        self.assertEqual(data["total"],1)
        self.assertEqual(data["counts"]["unread"],1)
        self.assertEqual(self.store.inbox(self.doctor,"user",filters={"sales":"no"})["total"],0)
        self.assertEqual(self.store.inbox(self.doctor,"user",filters={"search":"duvida"})["total"],1)
        self.assertEqual(self.store.inbox(self.doctor,"other")["counts"]["unread"],1)

    def test_largest_purchases_sort_unknown_after_known(self):
        with sqlite3.connect(self.path) as c:c.execute("INSERT INTO clinica_patients VALUES('second','Segundo','5500000000002','{}',1)")
        self.directory.checked=0
        self.new_lead()
        self.new_lead("5500000000003")
        self.add("own",final_amount=300000)
        self.add("second",patient="second",final_amount=900000)
        rows=self.rows(sort="spent")
        self.assertEqual([r["customer_history"].get("sales_cents") for r in rows],[900000,300000,None])

    def test_invalid_filters_rejected(self):
        for filters in ([],{"unknown":1},{"sales":"maybe"},{"min_sales":True},{"min_spent_cents":-1},
                        {"min_spent_cents":2,"max_spent_cents":1},{"min_sales":1.5},{"search":"x"*161}):
            with self.assertRaises(ValueError):parse_filters(filters)
