import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from institute_store import InstituteStore, INSTITUTES
from institute_sources import Kiwify, Meta, SourceError, day, sheet_rows, windows, kommo
from institute_report import build_report, match_lead, workbook
from institute_api import sync_sources

I, C = "victor-paranhos", "regen-code"


class InstitutesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = InstituteStore(Path(self.tmp.name) / "institutes.sqlite3")
        self.store.initialize()
        c = self.store.course(I, C)
        c.pop("institute_key")
        c["product_ids"] = ["regen"]
        self.store.save_course(I, c)

    def lead(self, ident="a", **extra):
        return {"id": ident, "name": "Aluno teste", "email": "aluno@example.invalid", "phone": "+5511999990000",
                "created_day": "2026-09-01", "campaign": "co2", **extra}

    def sale(self, ident="s", **extra):
        return {"id": ident, "name": "Aluno teste", "email": "aluno@example.invalid", "phone": "11999990000", "product_id": "regen",
            "created_day": "2026-09-05", "approved_day": "2026-09-06", "status": "paid", "gross": 100000, "net": 90000,
            "currency": "BRL", "net_currency": "BRL", "campaign": "", "payment_method": "pix", **extra}

    def report(self, **options):
        return build_report(self.store, I, C, {"from": "2026-09-01", "to": "2026-09-30", **options})

    def test_members_not_clinic_memberships(self):
        self.assertEqual(self.store.allowed({"id": 4, "is_master": False}), [])
        self.store.save_members(I, [4])
        self.assertEqual(self.store.allowed({"id": 4, "is_master": False}), [I])
        self.assertEqual(self.store.allowed({"id": 9, "is_master": True}), list(INSTITUTES))

    def test_secrets_not_returned_and_blank_preserves(self):
        self.store.save_settings(I, {"kiwify_client_secret": "very-private", "meta_access_token": "private-meta"})
        self.store.save_settings(I, {"kiwify_client_secret": ""})
        public = self.store.public_settings(I)
        self.assertNotIn("very-private", json.dumps(public))
        self.assertNotIn("private-meta", json.dumps(public))
        self.assertTrue(public["kiwify_client_secret_configured"])
        self.assertEqual(self.store.settings(I)["kiwify_client_secret"], "very-private")

    def test_pending_refunds_and_other_products_never_revenue(self):
        sales = [self.sale(), self.sale("pending", status="waiting_payment"), self.sale("refund", status="refunded"),
                 self.sale("other", product_id="clinic"), self.sale("chargeback", status="chargedback")]
        self.store.save_records(I, C, "kiwify", sales)
        r = self.report()
        self.assertEqual(r["summary"]["gross"], 100000)
        self.assertEqual(r["summary"]["sales"], 1)
        self.assertEqual(r["orders"]["total"], 4)

    def test_first_lead_dedup_and_period_conversion(self):
        self.store.save_records(I, C, "sheets", [self.lead(), self.lead("again", created_day="2026-09-03")])
        self.store.save_records(I, C, "kiwify", [self.sale(), self.sale("repeat")])
        r = self.report()
        self.assertEqual(r["summary"]["leads"], 1)
        self.assertEqual(r["summary"]["conversion"], 100)
        self.assertEqual(r["summary"]["buyers"], 1)

    def test_no_name_only_or_conflicting_phone_match(self):
        lead = self.lead(email="other@example.invalid")
        self.assertEqual(match_lead(self.sale(), [lead])[1], "unmatched")
        self.assertEqual(match_lead(self.sale(email="", phone=""), [lead])[1], "unmatched")

    def test_ambiguous_campaign_not_guessed_and_sale_utm_wins(self):
        leads = [self.lead(), self.lead("b", campaign="different")]
        self.store.save_records(I, C, "sheets", leads)
        self.store.save_records(I, C, "kiwify", [self.sale()])
        self.assertEqual(self.report()["warnings"]["sale_attribution"], 1)
        self.store.save_records(I, C, "kiwify", [self.sale(campaign="direct-utm")])
        r = self.report()
        self.assertEqual(next(c for c in r["campaigns"] if c["sales"])["name"], "direct-utm")
        self.assertNotIn("sale_attribution", r["warnings"])

    def test_meta_mapping_money_and_no_organic_roas(self):
        self.store.save_campaigns(I, C, [{"id":"123", "name":"Curso Meta", "aliases":["co2"]}])
        self.store.save_records(I, C, "sheets", [self.lead()])
        self.store.save_records(I, C, "kiwify", [self.sale(), self.sale("organic", campaign="organic", email="other@example.invalid")])
        self.store.save_records(I, C, "meta", [{"id":"123:2026-09-01", "campaign_id":"123", "day":"2026-09-01", "spend":10000}])
        self.assertIsNone(self.report()["summary"]["spend"])
        self.store.set_sync_state(I, C, "meta", {"ok":True, "from":"2026-09-01", "to":"2026-09-30"})
        r=self.report()
        self.assertEqual(r["summary"]["spend"],10000)
        self.assertEqual(r["summary"]["roas"],10)
        paid=next(c for c in r["campaigns"] if c["meta"])
        self.assertEqual((paid["cpl"],paid["cac"]),(10000,10000))

    def test_invalid_values_dates_flagged_not_invented(self):
        self.store.save_records(I,C,"kiwify",[self.sale(approved_day=""),self.sale("foreign",currency="USD")])
        r=self.report()
        self.assertEqual(r["summary"]["sales"],0)
        self.assertTrue(r["totals_partial"])
        self.assertEqual(len(r["pending"]),2)

    def test_mapping_duplicates_and_cross_course_rejected(self):
        with self.assertRaises(ValueError):
            self.store.save_campaigns(I,C,[{"id":"123","name":"A","aliases":["co2"]},{"id":"456","name":"B","aliases":["co2"]}])
        self.store.save_campaigns(I,C,[{"id":"123","name":"A","aliases":[]}])
        self.store.save_course(I,{"key":"other-course","name":"Outro","product_ids":[]})
        with self.assertRaises(ValueError):
            self.store.save_campaigns(I,"other-course",[{"id":"123","name":"A","aliases":[]}])

    def test_mapping_whitespace_cannot_hide_duplicate_aliases(self):
        with self.assertRaises(ValueError):
            self.store.save_campaigns(I,C,[{"id":"123","name":"A","aliases":["co2"]},
                                          {"id":"456","name":"B","aliases":[" co2 "]}])

    def test_account_change_invalidates_only_its_source(self):
        self.store.save_settings(I,{"kiwify_client_id":"original", "kiwify_account_id":"account"})
        course=self.store.course(I,C);course.pop("institute_key");course["product_ids"]=["regen"]
        self.store.save_course(I,course)
        self.store.save_records(I,C,"kiwify",[self.sale()])
        self.store.save_records(I,C,"sheets",[self.lead()])
        self.store.set_sync_state(I,C,"kiwify",{"ok":True,"at":1})
        self.store.save_settings(I,{"kiwify_client_secret":"rotated-secret"})
        self.assertEqual(len(self.store.records(I,C,"kiwify")),1)
        self.store.save_settings(I,{"kiwify_account_id":"another-account"})
        self.assertEqual(self.store.records(I,C,"kiwify"),[])
        self.assertNotIn("kiwify",self.store.sync_state(I,C))
        self.assertEqual(self.store.course(I,C)["product_ids"],[])
        self.assertEqual(len(self.store.records(I,C,"sheets")),1)

    def test_meta_account_change_removes_old_campaign_mapping(self):
        self.store.save_settings(I,{"meta_account_id":"123456"})
        self.store.save_campaigns(I,C,[{"id":"123","name":"A","aliases":["co2"]}])
        self.store.save_records(I,C,"meta",[{"id":"123:2026-09-01","spend":1}])
        self.store.set_sync_state(I,C,"meta",{"ok":True,"at":1})
        self.store.save_settings(I,{"meta_account_id":"999999"})
        self.assertEqual(self.store.campaigns(I,C),[])
        self.assertEqual(self.store.records(I,C,"meta"),[])
        self.assertNotIn("meta",self.store.sync_state(I,C))

    def test_course_source_change_resets_freshness(self):
        self.store.set_sync_state(I,C,"sheets",{"ok":True,"at":1})
        self.store.set_sync_state(I,C,"kommo",{"ok":True,"at":1})
        course=self.store.course(I,C);course.pop("institute_key")
        course.update(sheet_gid="1",pipeline_name="NOVO FUNIL")
        self.store.save_course(I,course)
        self.assertNotIn("sheets",self.store.sync_state(I,C))
        self.assertNotIn("kommo",self.store.sync_state(I,C))

    def test_success_replaces_only_imported_period(self):
        self.store.save_records(I,C,"kiwify",[self.sale(),self.sale("old",created_day="2025-01-01")])
        self.store.save_records(I,C,"kiwify",[],window=("created_day","2026-09-01","2026-09-30"))
        self.assertEqual([r["id"] for r in self.store.records(I,C,"kiwify")],["old"])

    def test_export_all_pages_and_no_formula_injection(self):
        from openpyxl import load_workbook
        self.store.save_records(I,C,"kiwify",[self.sale(str(n),name="=HYPERLINK(1)") for n in range(65)])
        data=workbook(self.store,I,C,{"from":"2026-09-01","to":"2026-09-30"})
        book=load_workbook(io.BytesIO(data))
        self.assertEqual(book["Pedidos"].max_row,66)
        self.assertEqual(book["Pedidos"]["C2"].data_type,"s")

    def test_brazil_approval_date_and_historical_windows(self):
        self.assertEqual(day("2026-10-01T01:00:00Z"),"2026-09-30")
        self.assertEqual(day("30/09/2026 12:35"),"2026-09-30")
        self.assertGreater(len(list(windows("2025-01-01","2026-10-04"))),20)
        self.assertTrue(all((b-a).days<90 for a,b in windows("2025-01-01","2026-10-04")))

    def test_sheet_schema_and_dates(self):
        rows=sheet_rows('Seu nome,Seu Whatsapp,Seu melhor e-mail,Pontuação,Data,ID,utm_campaign\nTeste,11999990000,test@example.invalid,10,30/09/2026,abc,co2\n')
        self.assertEqual(rows[0]["created_day"],"2026-09-30")
        self.assertEqual(rows[0]["score"],"10")

    def test_failed_sources_preserve_previous_data(self):
        self.store.save_records(I,C,"kiwify",[self.sale()])
        with patch("institute_api.Kiwify") as k,patch("institute_api.sheets",side_effect=SourceError("Google indisponível")),patch("institute_api.kommo",side_effect=SourceError("Kommo indisponível")):
            k.return_value.sales.side_effect=SourceError("Kiwify indisponível")
            result=sync_sources(self.store,I,C,"2026-09-01","2026-09-30",lambda path:{},lambda source:None)
        self.assertFalse(result["kiwify"]["ok"])
        self.assertEqual(len(self.store.records(I,C,"kiwify")),1)

    def test_kommo_excludes_clinic_funnels(self):
        def call(path):
            return {"_embedded":{"pipelines":[{"id":1,"name":"CLINICA"},{"id":2,"name":"REGENCODE","_embedded":{"statuses":[]}}]}} if "pipelines" in path else {"_embedded":{"leads":[{"id":1,"pipeline_id":1}]}}
        with self.assertRaises(SourceError):
            kommo(self.store.course(I,C),call)

    def test_kiwify_dedupe_month_overlap_and_full_details(self):
        calls=[]
        def http(url,headers=None,data=None,**kwargs):
            calls.append(url)
            if "oauth" in url:return {"access_token":"private-token"}
            return {"data":[{"id":"abc","product":{"id":"regen"},"status":"paid","approved_date":"2026-09-02T12:00:00Z","created_at":"2026-09-01T12:00:00Z","currency":"BRL","payment":{"charge_amount":12345,"net_amount":11000,"charge_currency":"BRL","settlement_currency":"BRL"}}]}
        source=Kiwify({"kiwify_client_id":"id","kiwify_client_secret":"secret","kiwify_account_id":"account"},http=http,pause=lambda _:None)
        rows=source.sales(self.store.course(I,C),"2026-08-01","2026-09-30")
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]["gross"],12345)
        self.assertTrue(all("view_full_sale_details=true" in url for url in calls if "sales" in url))

    def test_meta_filters_and_currency_guard(self):
        calls=[]
        def http(url,headers=None,**kwargs):
            calls.append((url,headers))
            return {"data":[{"campaign_id":"999","account_currency":"BRL","date_start":"2026-09-01","spend":"10.15"}]}
        meta=Meta({"meta_account_id":"123456","meta_access_token":"secret"},http)
        with self.assertRaises(SourceError):
            meta.insights([{"id":"123"}],"2026-09-01","2026-09-30")
        self.assertIn("filtering",calls[0][0])
        self.assertNotIn("secret",calls[0][0])
        self.assertEqual(calls[0][1]["Authorization"],"Bearer secret")


if __name__ == "__main__":
    unittest.main()
