from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

from crm_store import CrmStore, Conflict
from crm_worker import tick
import crm_followup_settings as settings
import crm_followup_runtime as followups


class Gateway:
    def __init__(self):self.sent=[]
    def send(self,item):
        self.sent.append(item.copy());return "synthetic-"+uuid.uuid4().hex


class FollowupRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=CrmStore(Path(self.tmp.name));self.store.bootstrap({"Profissional Exemplo":["Clínica"]})
        data=self.store.listing();self.doctor=data["doctor_id"];self.pipeline=data["pipelines"][0]
        self.actor={"id":"manager-test","is_master":True};self.now=self.stamp("2026-10-26T09:00")
        self.channel=self.store.channel({"doctor_id":self.doctor,"name":"Comercial","phone":"5500000000100"})
        self.ch=next(c for c in self.store.listing(self.doctor)["channels"] if c["id"]==self.channel)
        self.store.receive(self.ch["instance"],{"state":"open"},"connection.update")
        self.store.followup_permissions=lambda cycle:True
        self.gateway=Gateway();self.count=0
        self.config=settings.defaults();self.config["stage_ids"]=[self.pipeline["stages"][0]["id"]]
        with patch("time.time",return_value=self.now):
            settings.save(self.store,self.doctor,{"pipeline_id":self.pipeline["id"],"revision":0,"config":self.config},self.actor)
            settings.toggle(self.store,self.doctor,{"pipeline_id":self.pipeline["id"],"revision":1,"enabled":True,"confirm":True},self.actor)

    def stamp(self,value):return datetime.fromisoformat(value).replace(tzinfo=settings.TZ).timestamp()

    def lead(self,consent=True):
        self.count+=1
        ident=self.store.lead({"doctor_id":self.doctor,"pipeline_id":self.pipeline["id"],"stage_id":self.pipeline["stages"][0]["id"],
            "name":"Pessoa Exemplo", "phone":"5500000"+str(self.count).zfill(6),"title":"Interesse comercial", "consent":consent,
            "consent_source":"Autorização sintética para acompanhamento" if consent else ""},"Equipe")
        return self.store.detail(ident,self.doctor)["lead"]

    def incoming(self,lead,body="Gostaria de saber como funciona a avaliação"):
        with patch("time.time",return_value=self.now):
            self.store.receive(self.ch["instance"],{"key":{"id":uuid.uuid4().hex,"remoteJid":lead["phone"]+"@s.whatsapp.net"},"message":{"conversation":body}},"messages.upsert")

    def reply(self,lead,requested=True,body="Podemos conversar sobre suas dúvidas?",stamp=None):
        moment=self.now+1 if stamp is None else stamp
        with patch("time.time",return_value=moment):
            cursor=self.store.conversation(self.doctor,self.channel,lead["contact_id"])["inbound_cursor"]
            ident=self.store.reply({"doctor_id":self.doctor,"channel_id":self.channel,"contact_id":lead["contact_id"],"cursor":cursor,
                "body":body,"request_id":uuid.uuid4().hex,"followup_requested":requested,"followup_lead_id":lead["id"]},"staff-test",enabled=True,sender_name="Equipe Exemplo")
            item=self.store.claim(moment);self.assertEqual(item["id"],ident)
            self.store.finish(item,"manual-"+uuid.uuid4().hex)
        return ident

    def cycle(self,lead):
        with self.store.db() as c:return dict(c.execute("SELECT * FROM crm_followup_cycles WHERE lead_id=? ORDER BY created_at DESC LIMIT 1",(lead["id"],)).fetchone())

    def run_at(self,stamp):
        with patch("time.time",return_value=stamp):return tick(self.store,self.gateway,stamp)

    def start(self):
        lead=self.lead();self.incoming(lead);self.reply(lead);return lead

    def test_only_confirmed_native_explicit_requests_start_a_cycle(self):
        lead=self.lead();self.incoming(lead);self.reply(lead,requested=False)
        with self.store.db() as c:self.assertEqual(c.execute("SELECT count(*) FROM crm_followup_cycles").fetchone()[0],0)
        self.incoming(lead);self.reply(lead)
        self.assertEqual(self.cycle(lead)["status"],"waiting")

    def test_first_send_after_two_hours_with_no_footer_and_no_stage_movement(self):
        lead=self.start();cycle=self.cycle(lead)
        self.assertEqual(cycle["due_at"],self.now+1+7200)
        self.assertFalse(self.run_at(cycle["due_at"]-1))
        self.assertTrue(self.run_at(cycle["due_at"]))
        self.assertEqual(len(self.gateway.sent),1)
        self.assertEqual(self.gateway.sent[0]["body"],"Oi, Pessoa! Ficou alguma dúvida sobre o que conversamos? Estamos por aqui para te ajudar.")
        self.assertEqual(self.gateway.sent[0]["origin"],"followup")
        self.assertEqual(self.store.detail(lead["id"],self.doctor)["lead"]["stage_id"],lead["stage_id"])

    def test_customer_response_cancels_even_a_queued_attempt(self):
        lead=self.start();due=self.cycle(lead)["due_at"]
        followups.advance(self.store,due)
        self.incoming(lead,"Pode me explicar melhor?")
        self.assertEqual(self.cycle(lead)["status"],"cancelled")
        self.assertFalse(self.run_at(due));self.assertEqual(self.gateway.sent,[])

    def test_preflight_rechecks_a_response_after_claim(self):
        lead=self.start();due=self.cycle(lead)["due_at"];followups.advance(self.store,due)
        item=self.store.claim(due);self.incoming(lead)
        with self.store.db() as c:self.assertFalse(followups.message_allowed(c,self.store,item,due))
        self.assertEqual(self.gateway.sent,[])

    def test_deactivation_cancels_queue_and_does_not_resume_on_reactivation(self):
        lead=self.start();due=self.cycle(lead)["due_at"];followups.advance(self.store,due)
        settings.toggle(self.store,self.doctor,{"pipeline_id":self.pipeline["id"],"revision":2,"enabled":False},self.actor)
        settings.toggle(self.store,self.doctor,{"pipeline_id":self.pipeline["id"],"revision":3,"enabled":True,"confirm":True},self.actor)
        self.assertFalse(self.run_at(due));self.assertEqual(self.cycle(lead)["status"],"cancelled")

    def test_activation_never_enrolls_old_conversations(self):
        self.lead()
        with self.store.db() as c:self.assertEqual(c.execute("SELECT count(*) FROM crm_followup_cycles").fetchone()[0],0)

    def test_missing_consent_and_clinical_context_pause_without_messages(self):
        lead=self.lead(False);self.incoming(lead);self.reply(lead)
        self.assertEqual(self.cycle(lead)["status"],"paused")
        another=self.lead();self.incoming(another,"Estou com dor depois do procedimento");self.reply(another)
        self.assertEqual(self.cycle(another)["status"],"paused")
        self.assertFalse(self.run_at(self.now+9000));self.assertEqual(self.gateway.sent,[])

    def test_appointment_and_return_tasks_prevent_generic_followup(self):
        lead=self.lead();self.incoming(lead)
        with self.store.db() as c:c.execute("UPDATE leads SET next_action='2026-11-01' WHERE id=?",(lead["id"],))
        self.reply(lead);self.assertEqual(self.cycle(lead)["status"],"paused")

    def test_stage_phone_and_permission_changes_stop_dispatch(self):
        for change in ("stage","phone","permission"):
            lead=self.start();due=self.cycle(lead)["due_at"]
            with self.store.db() as c:
                if change=="stage":c.execute("UPDATE leads SET stage_id=? WHERE id=?",(self.pipeline["stages"][1]["id"],lead["id"]))
                if change=="phone":c.execute("INSERT INTO contact_phone_settings VALUES(?,?,2,?,?)",(lead["contact_id"],"",self.now,"manager-test"))
            if change=="permission":self.store.followup_permissions=lambda cycle:False
            self.assertFalse(self.run_at(due));self.assertNotEqual(self.cycle(lead)["status"],"waiting")
        self.assertEqual(self.gateway.sent,[])

    def test_disconnect_then_reconnect_does_not_resume(self):
        lead=self.start();due=self.cycle(lead)["due_at"]
        self.store.receive(self.ch["instance"],{"state":"close"},"connection.update")
        self.store.receive(self.ch["instance"],{"state":"open"},"connection.update")
        self.assertEqual(self.cycle(lead)["status"],"paused");self.assertFalse(self.run_at(due))

    def test_backlog_uses_shared_three_minute_channel_gap(self):
        first=self.start();second=self.start();due=self.cycle(first)["due_at"]
        self.assertTrue(self.run_at(due));self.assertEqual(len(self.gateway.sent),1)
        self.assertFalse(self.run_at(due+179));self.assertEqual(len(self.gateway.sent),1)
        self.assertTrue(self.run_at(due+180));self.assertEqual(len(self.gateway.sent),2)

    def test_end_of_day_defers_backlog_to_next_window_with_gap(self):
        first=self.start();second=self.start()
        with self.store.db() as c:c.execute("UPDATE crm_followup_cycles SET due_at=?",(self.stamp("2026-10-26T18:00"),))
        self.assertFalse(self.run_at(self.stamp("2026-10-26T18:00")))
        self.assertTrue(self.run_at(self.stamp("2026-10-27T09:00")))
        self.assertFalse(self.run_at(self.stamp("2026-10-27T09:01")))
        self.assertTrue(self.run_at(self.stamp("2026-10-27T09:03")))

    def test_manual_sends_keep_priority_and_do_not_inherit_automation_gap(self):
        lead=self.start();due=self.cycle(lead)["due_at"];self.run_at(due)
        other=self.lead();self.incoming(other);self.reply(other,requested=False,stamp=due+1)
        self.assertEqual(len(self.gateway.sent),1)

    def test_timeout_is_unknown_and_never_retried(self):
        lead=self.start();due=self.cycle(lead)["due_at"]
        with patch.object(self.gateway,"send",side_effect=TimeoutError()):self.run_at(due)
        self.assertFalse(self.run_at(due+10000));self.assertEqual(self.cycle(lead)["status"],"paused")
        with self.store.db() as c:self.assertEqual(c.execute("SELECT status FROM messages WHERE followup_cycle_id<>''").fetchone()[0],"unknown")

    def test_planning_failure_does_not_undo_native_acceptance(self):
        lead=self.lead();self.incoming(lead)
        with patch("crm_followup_runtime.accepted",side_effect=ValueError("synthetic failure")):
            ident=self.reply(lead)
        with self.store.db() as c:self.assertEqual(c.execute("SELECT status FROM messages WHERE id=?",(ident,)).fetchone()[0],"sent")

    def test_uncertain_attempt_does_not_block_other_contacts_on_the_number(self):
        first=self.start();second=self.start();due=self.cycle(first)["due_at"]
        with patch.object(self.gateway,"send",side_effect=TimeoutError()):self.run_at(due)
        with self.store.db() as c:
            uncertain=c.execute("SELECT contact_id FROM messages WHERE status='unknown'").fetchone()[0]
        paused,remaining=(first,second) if uncertain==first["contact_id"] else (second,first)
        self.assertTrue(self.run_at(due+180))
        self.assertEqual(len(self.gateway.sent),1)
        self.assertEqual(self.cycle(paused)["status"],"paused")
        self.assertEqual(self.gateway.sent[0]["contact_id"],remaining["contact_id"])

    def test_default_off_and_explicit_master_confirmation(self):
        with self.assertRaises(ValueError):settings.toggle(self.store,self.doctor,{"pipeline_id":self.pipeline["id"],"revision":2,"enabled":True},self.actor)
        with self.assertRaises(PermissionError):settings.toggle(self.store,self.doctor,{"pipeline_id":self.pipeline["id"],"revision":2,"enabled":False},{"is_master":False})
        with self.assertRaises(Conflict):settings.save(self.store,self.doctor,{"pipeline_id":self.pipeline["id"],"revision":2,"config":self.config},self.actor)

    def test_completed_cycle_does_not_restart_for_the_same_silence(self):
        lead=self.start()
        for _ in range(4):self.run_at(self.cycle(lead)["due_at"])
        self.assertEqual(self.cycle(lead)["status"],"completed")
        self.reply(lead,stamp=self.now+864000)
        with self.store.db() as c:self.assertEqual(c.execute("SELECT count(*) FROM crm_followup_cycles WHERE lead_id=?",(lead["id"],)).fetchone()[0],1)

    def test_two_open_contexts_do_not_create_competing_cadences(self):
        lead=self.lead();other=self.store.pipeline({"doctor_id":self.doctor,"name":"Outro funil","stages":[{"name":"Entrada","kind":"open"},{"name":"Ganho","kind":"won"}]})
        stage=next(p for p in self.store.listing(self.doctor)["pipelines"] if p["id"]==other)["stages"][0]["id"]
        self.store.lead({"doctor_id":self.doctor,"pipeline_id":other,"stage_id":stage,"name":lead["name"],"phone":lead["phone"],"title":"Outro contexto"},"Equipe")
        self.incoming(lead);self.reply(lead)
        self.assertEqual(self.cycle(lead)["status"],"paused")

    def test_permission_lookup_failure_denies_followup(self):
        from types import SimpleNamespace
        def unavailable(_):
            raise ValueError("synthetic unavailable auth")
        check=followups.permitted(self.store,SimpleNamespace(get_public_user=unavailable))
        with self.assertLogs(level="WARNING"):
            self.assertFalse(check({"activator_id":"synthetic-master"}))


if __name__=="__main__":unittest.main()
