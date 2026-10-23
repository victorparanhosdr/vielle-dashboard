from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
import tempfile
import unittest

from crm_access import crm_access
from crm_store import CrmStore, Conflict
import crm_clinic_journey as journey
import crm_followup_settings as followups


class FollowupSettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = CrmStore(Path(self.tmp.name))
        self.store.bootstrap({"Profissional Exemplo": ["Clínica", "Outro funil", "RegenCode"], "Outro profissional": ["Outra clínica"]})
        data = self.store.listing()
        self.doctor = next(d["id"] for d in data["doctors"] if d["name"] == "Profissional Exemplo")
        data = self.store.listing(self.doctor)
        self.pipeline = next(p for p in data["pipelines"] if p["name"] == "Clínica")
        self.other = next(p for p in data["pipelines"] if p["name"] == "Outro funil")
        self.course = next(p for p in data["pipelines"] if p["name"] == "RegenCode")
        self.actor = {"id": "manager-example", "is_master": True}

    def payload(self):
        return {"pipeline_id": self.pipeline["id"], "revision": 0, "config": followups.defaults()}

    def save(self, payload=None, actor=None):
        return followups.save(self.store, self.doctor, payload or self.payload(), actor or self.actor)

    def stamp(self, value):
        return datetime.fromisoformat(value).replace(tzinfo=followups.TZ).timestamp()

    def test_default_four_steps_are_drafts_and_read_never_creates_a_policy(self):
        result = followups.snapshot(self.store, self.doctor, self.pipeline["id"])
        self.assertEqual([s["minutes"] for s in result["config"]["steps"]], [120,1320,2880,5760])
        total=0; offsets=[]
        for step in result["config"]["steps"]:
            total+=step["minutes"];offsets.append(total)
        self.assertEqual(offsets,[120,1440,4320,10080])
        self.assertEqual(result["config"]["spacing_seconds"],180)
        self.assertFalse(result["enabled"])
        self.assertTrue(result["automatic_available"])
        self.assertEqual(result["max_steps"],20)
        self.assertTrue(all("Mensagem automática" not in s["body"] and "PARAR" not in s["body"] for s in result["config"]["steps"]))
        with self.store.db() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM followup_policies").fetchone()[0],0)

    def test_save_is_revisioned_audited_and_isolated_per_funnel(self):
        payload=self.payload();payload["config"]["spacing_seconds"]=300
        payload["config"]["steps"][0]["body"]="Olá, {nome}! Podemos ajudar?"
        result=self.save(payload)
        self.assertEqual(result["revision"],1)
        self.assertEqual(result["config"]["spacing_seconds"],300)
        self.assertFalse(result["enabled"])
        self.assertEqual(followups.snapshot(self.store,self.doctor,self.other["id"])["revision"],0)
        with self.assertRaises(Conflict):self.save(payload)
        payload["revision"]=1
        self.assertEqual(self.save(payload)["revision"],2)
        with self.store.db() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM followup_policies").fetchone()[0],1)
            self.assertEqual(c.execute("SELECT count(*) FROM followup_policy_events").fetchone()[0],2)
            self.assertEqual(c.execute("SELECT activated_at FROM followup_policies").fetchone()[0],0)
            for table in ("messages","leads","campaigns"):
                self.assertEqual(c.execute(f"SELECT count(*) FROM {table}").fetchone()[0],0)

    def test_twenty_steps_persist_and_twenty_first_is_rejected(self):
        payload=self.payload()
        payload["config"]["steps"]=[{"minutes":60,"body":f"Mensagem de exemplo {i}"} for i in range(20)]
        self.assertEqual(len(self.save(payload)["config"]["steps"]),20)
        payload["revision"]=1;payload["config"]["steps"].append({"minutes":60,"body":"Mais uma"})
        with self.assertRaises(ValueError):self.save(payload)

    def test_invalid_steps_and_templates_fail_without_partial_save(self):
        values=[[],None,[None],[{"minutes":14,"body":"Texto"}],[{"minutes":True,"body":"Texto"}],
                [{"minutes":60,"body":""}],[{"minutes":60,"body":"x"*2001}],
                [{"minutes":60,"body":"Texto\u200b"}],[{"minutes":60,"body":"Olá, {segredo}"}]]
        for steps in values:
            with self.subTest(steps=steps):
                p=self.payload();p["config"]["steps"]=steps
                with self.assertRaises(ValueError):self.save(p)
        with self.store.db() as c:self.assertEqual(c.execute("SELECT count(*) FROM followup_policies").fetchone()[0],0)

    def test_spacing_days_and_window_validation(self):
        values=[("spacing_seconds",0),("spacing_seconds",True),("spacing_seconds",61),("spacing_seconds",86401),
                ("weekdays",[]),("weekdays",[True]),("weekdays",[0,0]),("weekdays",[7]),
                ("from","25:00"),("to","08:00"),("to","09:00"),("timezone","UTC")]
        for key,value in values:
            with self.subTest(key=key,value=value):
                p=self.payload();p["config"][key]=value
                with self.assertRaises(ValueError):self.save(p)

    def test_total_age_and_no_steps_are_validated(self):
        p=self.payload();p["config"]["steps"]=[{"minutes":43200,"body":"Exemplo"}]*7
        with self.assertRaises(ValueError):self.save(p)

    def test_closed_foreign_and_archived_stages_are_not_eligible(self):
        for ident in (self.pipeline["stages"][-1]["id"],self.other["stages"][0]["id"]):
            p=self.payload();p["config"]["stage_ids"]=[ident]
            with self.assertRaises(ValueError):self.save(p)
        original=self.pipeline["stages"][0]["id"]
        preview=journey.preview(self.store,self.doctor,self.pipeline["id"],self.actor)
        journey.apply(self.store,self.doctor,{"id":preview["id"],"confirm":True},self.actor)
        p=self.payload();p["config"]["stage_ids"]=[original]
        with self.assertRaises(ValueError):self.save(p)

    def test_master_edit_boundary_and_funnel_scope(self):
        with self.assertRaises(PermissionError):self.save(actor={"id":"staff","is_master":False})
        for operation in (lambda:followups.snapshot(self.store,self.doctor,self.pipeline["id"]),lambda:self.save()):
            with crm_access({self.doctor:[self.other["id"]]},self.doctor):
                with self.assertRaises(PermissionError):operation()
        other_doc=next(d["id"] for d in self.store.listing()["doctors"] if d["id"]!=self.doctor)
        with self.assertRaises(ValueError):followups.snapshot(self.store,other_doc,self.pipeline["id"])

    def test_courses_are_not_modified(self):
        p=self.payload();p["pipeline_id"]=self.course["id"]
        with self.assertRaises(ValueError):self.save(p)
        with self.assertRaises(ValueError):followups.snapshot(self.store,self.doctor,self.course["id"])

    def test_activation_is_not_a_supported_draft_operation(self):
        for key in ("enabled","activate"):
            p=self.payload();p[key]=True
            with self.assertRaises(ValueError):self.save(p)

    def test_concurrent_saves_keep_one_revision_and_one_audit(self):
        def attempt(_):
            try:return self.save()["revision"]
            except Conflict:return "conflict"
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(attempt,range(2)))
        self.assertCountEqual(results,[1,"conflict"])

    def test_morning_backlog_is_spaced_not_burst(self):
        config=followups.defaults();start=self.stamp("2026-10-26T08:00")
        result=followups.slots(config,start)
        self.assertEqual(result,[self.stamp("2026-10-26T09:00"),self.stamp("2026-10-26T09:03"),self.stamp("2026-10-26T09:06")])
        config["spacing_seconds"]=600
        self.assertEqual(followups.slots(config,start)[1]-followups.slots(config,start)[0],600)

    def test_weekends_and_close_boundary_keep_spacing_and_no_catchup_burst(self):
        config=followups.defaults()
        self.assertEqual(followups.slots(config,self.stamp("2026-10-23T17:59")),
            [self.stamp("2026-10-23T17:59"),self.stamp("2026-10-26T09:00"),self.stamp("2026-10-26T09:03")])
        self.assertEqual(followups.slots(config,self.stamp("2026-10-23T18:00"))[0],self.stamp("2026-10-26T09:00"))

    def test_last_send_on_number_delays_first_slot(self):
        config=followups.defaults()
        self.assertEqual(followups.slots(config,self.stamp("2026-10-26T09:02"),last_send_at=self.stamp("2026-10-26T09:01"))[0],self.stamp("2026-10-26T09:04"))

    def test_preview_is_pure_and_rejects_bad_dates(self):
        p=self.payload();p["at_local"]="2026-10-26T08:00"
        result=followups.preview(self.store,self.doctor,p)
        self.assertEqual(result["scheduled"],0)
        self.assertFalse(result["enabled"])
        self.assertEqual(result["slots"][0],self.stamp("2026-10-26T09:00"))
        for value in ("not-a-date",{"date":"bad"},"2026-10-26T09:00+00:00"):
            p["at_local"]=value
            with self.assertRaises(ValueError):followups.preview(self.store,self.doctor,p)
        with self.store.db() as c:
            for table in ("followup_policies","messages","campaigns"):
                self.assertEqual(c.execute(f"SELECT count(*) FROM {table}").fetchone()[0],0)


if __name__ == "__main__":unittest.main()
