import unittest

from crm_access import crm_access
from crm_followup_contact import save, preference
from crm_store import Conflict
from tests import test_crm_followup_runtime as fixture


class ContactFollowupTests(unittest.TestCase):
    setUp=fixture.FollowupRuntimeTests.setUp
    stamp=fixture.FollowupRuntimeTests.stamp
    lead=fixture.FollowupRuntimeTests.lead
    incoming=fixture.FollowupRuntimeTests.incoming
    reply=fixture.FollowupRuntimeTests.reply
    cycle=fixture.FollowupRuntimeTests.cycle
    run_at=fixture.FollowupRuntimeTests.run_at
    start=fixture.FollowupRuntimeTests.start

    def change(self,lead,paused,revision=None):
        with self.store.db() as c:current=preference(c,lead["contact_id"])
        return save(self.store,{"doctor_id":self.doctor,"id":lead["id"],"paused":paused,
            "revision":current["revision"] if revision is None else revision,"confirm":True},
            {"id":"operator-test","nome":"Equipe Exemplo"})

    def test_default_has_no_pause_and_does_not_create_a_cycle(self):
        lead=self.lead()
        self.assertEqual(lead["followup_contact"],{"paused":False,"revision":1,"can_manage":True})
        with self.store.db() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM crm_followup_cycles").fetchone()[0],0)
            self.assertEqual(c.execute("SELECT count(*) FROM crm_followup_contact_preferences").fetchone()[0],0)

    def test_inbox_with_enabled_followup_contains_eligible_stages(self):
        from crm_inbox import combined
        data=combined(self.store,[self.doctor],"operator-test")
        pipeline=next(p for p in data["pipelines"] if p["id"]==self.pipeline["id"])
        self.assertEqual(pipeline["followup_stage_ids"],self.config["stage_ids"])

    def test_pause_cancels_waiting_attempt_without_deleting_history(self):
        import crm_followup_runtime as runtime
        lead=self.start();due=self.cycle(lead)["due_at"];runtime.advance(self.store,due)
        result=self.change(lead,True)
        self.assertTrue(result["paused"]);self.assertEqual(result["revision"],2)
        self.assertEqual(self.cycle(lead)["status"],"paused")
        self.assertFalse(self.run_at(due));self.assertEqual(self.gateway.sent,[])
        data=self.store.detail(lead["id"],self.doctor)
        self.assertTrue(data["lead"]["followup_contact"]["paused"])
        self.assertTrue(any(e["kind"]=="followup-control" for e in data["events"]))
        self.assertGreater(len(data["messages"]),0)

    def test_pause_survives_new_human_messages_even_if_client_sends_true(self):
        lead=self.lead();self.change(lead,True);self.incoming(lead);ident=self.reply(lead)
        with self.store.db() as c:
            self.assertEqual(c.execute("SELECT status FROM messages WHERE id=?",(ident,)).fetchone()[0],"sent")
            self.assertEqual(c.execute("SELECT count(*) FROM crm_followup_cycles").fetchone()[0],0)
        self.assertTrue(self.store.conversation(self.doctor,self.channel,lead["contact_id"])["contact"]["followup_contact"]["paused"])

    def test_unpause_does_not_resume_previous_cycle(self):
        lead=self.start();due=self.cycle(lead)["due_at"]
        self.change(lead,True);self.change(lead,False)
        self.assertFalse(self.run_at(due));self.assertEqual(self.cycle(lead)["status"],"paused")
        self.now+=60
        self.incoming(lead);self.reply(lead)
        self.assertEqual(self.cycle(lead)["status"],"waiting")

    def test_course_does_not_receive_contact_pause_controls(self):
        pipeline=self.store.pipeline({"doctor_id":self.doctor,"name":"RegenCode","stages":[{"name":"Entrada","kind":"open"},{"name":"Aluno","kind":"won"}]})
        stage=next(p for p in self.store.listing(self.doctor)["pipelines"] if p["id"]==pipeline)["stages"][0]["id"]
        key=self.store.lead({"doctor_id":self.doctor,"pipeline_id":pipeline,"stage_id":stage,"name":"Contato Curso","phone":"5500000999999","title":"Curso"},"Equipe")
        with self.assertRaises(ValueError):self.change({"id":key,"contact_id":"unused"},True,revision=1)

    def test_change_rejects_stale_revision_and_does_not_touch_other_contacts(self):
        lead=self.lead();other=self.lead();self.change(lead,True)
        with self.assertRaises(Conflict):self.change(lead,False,revision=1)
        self.assertFalse(self.store.detail(other["id"],self.doctor)["lead"]["followup_contact"]["paused"])
        with self.store.db() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM crm_followup_contact_events").fetchone()[0],1)

    def test_pause_persists_after_store_restart(self):
        from crm_store import CrmStore
        lead=self.lead();self.change(lead,True)
        reopened=CrmStore(self.tmp.name)
        self.assertTrue(reopened.detail(lead["id"],self.doctor)["lead"]["followup_contact"]["paused"])

    def test_shared_contact_changes_require_access_to_all_affected_funnels(self):
        lead=self.lead();other=self.store.pipeline({"doctor_id":self.doctor,"name":"Outro clinico","stages":[{"name":"Entrada","kind":"open"},{"name":"Paciente","kind":"won"}]})
        stage=next(p for p in self.store.listing(self.doctor)["pipelines"] if p["id"]==other)["stages"][0]["id"]
        self.store.lead({"doctor_id":self.doctor,"pipeline_id":other,"stage_id":stage,"name":lead["name"],"phone":lead["phone"],"title":"Outro contexto"},"Equipe")
        with crm_access({self.doctor:[self.pipeline["id"]]}),self.assertRaises(PermissionError):self.change(lead,True)
        self.assertFalse(self.store.detail(lead["id"],self.doctor)["lead"]["followup_contact"]["paused"])

    def test_pre_pause_queued_human_reply_cannot_enroll_after_unpause(self):
        import uuid
        from unittest.mock import patch
        lead=self.lead();self.incoming(lead)
        with patch("time.time",return_value=self.now+1):
            cursor=self.store.conversation(self.doctor,self.channel,lead["contact_id"])["inbound_cursor"]
            message=self.store.reply({"doctor_id":self.doctor,"channel_id":self.channel,"contact_id":lead["contact_id"],
                "cursor":cursor,"body":"Resposta sintética","request_id":uuid.uuid4().hex,
                "followup_requested":True,"followup_lead_id":lead["id"]},"staff-test",enabled=True,sender_name="Equipe Exemplo")
        self.change(lead,True);self.change(lead,False)
        self.store.finish(self.store.claim(self.now+2),"accepted-synthetic")
        with self.store.db() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM crm_followup_cycles").fetchone()[0],0)
            self.assertEqual(c.execute("SELECT status FROM messages WHERE id=?",(message,)).fetchone()[0],"sent")

    def test_pause_after_claim_is_rechecked_before_provider(self):
        import crm_followup_runtime as runtime
        lead=self.start();due=self.cycle(lead)["due_at"];runtime.advance(self.store,due)
        item=self.store.claim(due);self.change(lead,True)
        with self.store.db() as c:self.assertFalse(runtime.message_allowed(c,self.store,item,due))
        self.assertEqual(self.gateway.sent,[])


if __name__=="__main__":unittest.main()
