import json
from unittest.mock import patch
import uuid

import test_crm_followup_runtime as base
import crm_followup_assets as assets
import crm_followup_runtime as runtime
import crm_followup_settings as settings
from crm_bots import BotStore
from crm_access import crm_access


class FollowupActionsTests(base.FollowupRuntimeTests):
    def replace_steps(self,steps):
        with self.store.db() as c:c.execute("UPDATE followup_policies SET config=?",(json.dumps(self.config | {"steps":steps}),))

    def asset(self,kind="document"):
        name,data={"document":("exemplo.pdf",b"%PDF-1.4\nsynthetic"),"image":("exemplo.png",b"\x89PNG\r\n\x1a\nsynthetic"),
            "video":("exemplo.mp4",b"\0\0\0\x18ftypisom"+b"\0"*32),"audio":("exemplo.mp3",b"ID3"+b"\0"*32)}[kind]
        with self.store.db() as c:c.execute("UPDATE followup_policies SET enabled=0")
        item=assets.upload(self.store,self.doctor,self.pipeline["id"],self.actor,uuid.uuid4().hex,name,kind,data)
        with self.store.db() as c:c.execute("UPDATE followup_policies SET enabled=1")
        return item

    def bot(self,message=False):
        bots=BotStore(self.store)
        middle={"id":"action","kind":"message" if message else "note","data":{"body":"Mensagem sintética","channel_id":self.channel} if message else {"body":"Nota sintética"},"next":{"next":"end"}}
        info={"trigger":{"kind":"manual"},"stop_on_reply":True,"nodes":[{"id":"start","kind":"start","next":{"next":"action"}},middle,{"id":"end","kind":"end"}]}
        ident=bots.save({"doctor_id":self.doctor,"pipeline_id":self.pipeline["id"],"name":"Robô sintético","definition":info},self.actor["id"])["id"]
        bots.publish(ident,self.doctor,1,True,self.actor["id"])
        with self.store.db() as c:revision=c.execute("SELECT revision FROM crm_bots WHERE id=?",(ident,)).fetchone()[0]
        return bots,ident,revision

    def test_all_four_media_formats_are_private_reusable_and_sent_via_gateway(self):
        for kind in ("image","document","video","audio"):
            with self.subTest(kind=kind):
                self.now+=3600
                asset=self.asset(kind);self.replace_steps([{"minutes":120,"body":"" if kind=="audio" else "Olá, {nome}","media_id":asset["id"]}])
                lead=self.start();due=self.cycle(lead)["due_at"];self.assertTrue(self.run_at(due))
                sent=self.gateway.sent[-1];self.assertEqual(sent["media"]["kind"],kind)
                self.assertEqual(self.cycle(lead)["status"],"completed")
                with self.store.db() as c:
                    media=dict(c.execute("SELECT * FROM message_media WHERE id=?",(sent["media_id"],)).fetchone())
                    self.assertEqual(media["contact_id"],lead["contact_id"]);self.assertEqual(media["followup_asset_id"],asset["id"])

    def test_save_validates_asset_scope_audio_caption_and_patient_attachment(self):
        asset=self.asset("audio")
        with self.store.db() as c:
            for step in ({"minutes":60,"body":"Legenda não suportada","media_id":asset["id"]},
                         {"minutes":60,"body":"Texto","media_id":"patient-private-attachment"}):
                with self.assertRaises(ValueError):settings.clean(c,self.pipeline["id"],self.config | {"steps":[step]})
            with self.assertRaises(ValueError):assets.owned(c,asset["id"],"another-funnel")
        with self.assertRaises(PermissionError):assets.upload(self.store,self.doctor,self.pipeline["id"],{"id":"staff"},"upload","arquivo.pdf","document",b"%PDF-1.4")
        with crm_access({self.doctor:[]},self.doctor):
            with self.assertRaises(PermissionError):settings.snapshot(self.store,self.doctor,self.pipeline["id"])

    def test_missing_or_tampered_asset_pauses_without_provider_call(self):
        asset=self.asset();self.replace_steps([{"minutes":120,"body":"","media_id":asset["id"]}]);lead=self.start()
        with patch("crm_followup_assets.content",side_effect=ValueError("synthetic tampering")):
            self.assertFalse(self.run_at(self.cycle(lead)["due_at"]))
        self.assertEqual(self.gateway.sent,[]);self.assertEqual(self.cycle(lead)["status"],"paused")

    def test_media_unknown_never_retries_and_customer_reply_cancels(self):
        asset=self.asset();self.replace_steps([{"minutes":120,"body":"","media_id":asset["id"]}]);lead=self.start();due=self.cycle(lead)["due_at"]
        with patch.object(self.gateway,"send",side_effect=TimeoutError()):self.run_at(due)
        self.assertFalse(self.run_at(due+3600));self.assertEqual(self.gateway.sent,[])

    def test_robot_is_idempotent_and_next_step_waits_for_completion(self):
        bots,ident,revision=self.bot()
        self.replace_steps([{"minutes":120,"action":"bot","body":"","bot_id":ident,"bot_revision":revision},{"minutes":60,"body":"Mensagem seguinte"}])
        lead=self.start();due=self.cycle(lead)["due_at"]
        runtime.advance(self.store,due);runtime.advance(self.store,due+1)
        with self.store.db() as c:self.assertEqual(c.execute("SELECT count(*) FROM bot_runs").fetchone()[0],1)
        self.assertEqual(self.gateway.sent,[])
        for i in range(3):bots.tick(due+i)
        runtime.advance(self.store,due+4)
        self.assertEqual(self.cycle(lead)["attempt"],1);self.assertEqual(self.cycle(lead)["bot_run_id"],"")
        self.assertTrue(self.run_at(self.cycle(lead)["due_at"]));self.assertEqual(self.gateway.sent[-1]["body"],"Mensagem seguinte")

    def test_robot_messages_obey_schedule_and_reply_cancellation(self):
        bots,ident,revision=self.bot(True);self.replace_steps([{"minutes":120,"action":"bot","body":"","bot_id":ident,"bot_revision":revision}])
        lead=self.start();due=self.cycle(lead)["due_at"];runtime.advance(self.store,due)
        bots.tick(due);bots.tick(due+1)
        self.assertIsNone(self.store.claim(self.stamp("2026-10-26T18:00")))
        with self.store.db() as c:self.assertEqual(c.execute("SELECT status FROM messages WHERE bot_run_id<>''").fetchone()[0],"queued")
        self.incoming(lead);bots.tick(due+2)
        self.assertFalse(self.run_at(due+180));self.assertEqual(self.gateway.sent,[])

    def test_robot_permission_revocation_stops_all_actions(self):
        bots,ident,revision=self.bot();self.replace_steps([{"minutes":120,"action":"bot","body":"","bot_id":ident,"bot_revision":revision}])
        lead=self.start();due=self.cycle(lead)["due_at"];runtime.advance(self.store,due)
        self.store.followup_permissions=lambda _:False;bots.tick(due)
        with self.store.db() as c:self.assertEqual(c.execute("SELECT status FROM bot_runs").fetchone()[0],"cancelled")
        self.assertEqual(self.cycle(lead)["status"],"paused")

    def test_robot_messages_send_without_footer_and_complete_once(self):
        bots,ident,revision=self.bot(True);self.replace_steps([{"minutes":120,"action":"bot","body":"","bot_id":ident,"bot_revision":revision}])
        lead=self.start();due=self.cycle(lead)["due_at"];runtime.advance(self.store,due)
        bots.tick(due);bots.tick(due+1)
        self.assertTrue(self.run_at(due+2));self.assertEqual(self.gateway.sent[0]["body"],"Mensagem sintética")
        bots.tick(due+3);bots.tick(due+4);runtime.advance(self.store,due+5)
        self.assertEqual(self.cycle(lead)["status"],"completed")
        item=self.store.detail(lead["id"],self.doctor)["lead"]["followup_progress"]
        self.assertEqual((item["completed_steps"],item["accepted_steps"]),(1,0))
        self.assertFalse(self.run_at(due+3600));self.assertEqual(len(self.gateway.sent),1)

    def test_individual_pause_cancels_robot_and_cannot_resume_by_unpausing(self):
        from crm_followup_contact import save
        bots,ident,revision=self.bot(True);self.replace_steps([{"minutes":120,"action":"bot","body":"","bot_id":ident,"bot_revision":revision}])
        lead=self.start();due=self.cycle(lead)["due_at"];runtime.advance(self.store,due);bots.tick(due);bots.tick(due+1)
        save(self.store,{"doctor_id":self.doctor,"id":lead["id"],"revision":1,"paused":True,"confirm":True},self.actor | {"nome":"Gestor Exemplo"})
        bots.tick(due+2);self.assertFalse(self.run_at(due+3))
        with self.store.db() as c:self.assertEqual(c.execute("SELECT status FROM bot_runs").fetchone()[0],"cancelled")
        save(self.store,{"doctor_id":self.doctor,"id":lead["id"],"revision":2,"paused":False,"confirm":True},self.actor | {"nome":"Gestor Exemplo"})
        bots.tick(due+4);self.assertFalse(self.run_at(due+5))
        self.assertEqual(self.gateway.sent,[])

    def test_robot_revision_change_and_other_connection_fail_closed(self):
        bots,ident,revision=self.bot(True);self.replace_steps([{"minutes":120,"action":"bot","body":"","bot_id":ident,"bot_revision":revision}])
        lead=self.start();due=self.cycle(lead)["due_at"]
        with self.store.db() as c:c.execute("UPDATE crm_bots SET revision=revision+1 WHERE id=?",(ident,))
        runtime.advance(self.store,due);self.assertEqual(self.cycle(lead)["status"],"paused")
        with self.store.db() as c:
            c.execute("UPDATE crm_bots SET revision=? WHERE id=?",(revision,ident))
            raw=json.loads(c.execute("SELECT published FROM crm_bots WHERE id=?",(ident,)).fetchone()[0]);raw["nodes"][1]["data"]["channel_id"]="different-channel"
            c.execute("UPDATE crm_bots SET published=? WHERE id=?",(json.dumps(raw),ident))
        other=self.start();runtime.advance(self.store,self.cycle(other)["due_at"])
        self.assertEqual(self.cycle(other)["status"],"paused");self.assertEqual(self.gateway.sent,[])

    def test_robot_cannot_change_patient_phase_or_ignore_customer_reply(self):
        bots,ident,revision=self.bot()
        for mutation in ("move","ignore-reply"):
            with self.store.db() as c:
                raw=json.loads(c.execute("SELECT published FROM crm_bots WHERE id=?",(ident,)).fetchone()[0])
                if mutation=="move":raw["nodes"][1]["kind"]="move"
                else:raw["nodes"][1]["kind"]="note";raw["stop_on_reply"]=False
                c.execute("UPDATE crm_bots SET published=? WHERE id=?",(json.dumps(raw),ident))
                with self.assertRaises(ValueError):settings.clean(c,self.pipeline["id"],self.config | {"steps":[{"minutes":60,"action":"bot","bot_id":ident,"bot_revision":revision}]})

    def test_progress_is_read_only_counts_completed_not_queued_and_filters(self):
        from crm_filters import parse_filters,matches
        lead=self.start();due=self.cycle(lead)["due_at"]
        p=self.store.detail(lead["id"],self.doctor)["lead"]["followup_progress"]
        self.assertEqual((p["status"],p["completed_steps"],p["next_step"]),("waiting",0,1))
        runtime.advance(self.store,due)
        p=self.store.detail(lead["id"],self.doctor)["lead"]["followup_progress"]
        self.assertEqual((p["completed_steps"],p["pending_status"]),(0,"queued"))
        self.run_at(due)
        item=self.store.detail(lead["id"],self.doctor)["lead"];p=item["followup_progress"]
        self.assertEqual((p["completed_steps"],p["accepted_steps"],p["next_step"]),(1,1,2))
        self.assertTrue(matches(item,parse_filters({"followup_step":"1","followup_status":"waiting"})))
        self.assertFalse(matches(item,parse_filters({"followup_step":"0"})))
        self.incoming(lead)
        self.assertEqual(self.store.detail(lead["id"],self.doctor)["lead"]["followup_progress"]["status"],"responded")

    def test_progress_hides_old_cycle_after_transfer_and_courses(self):
        from crm_followup_progress import attach
        lead=self.start()
        with self.store.db() as c:
            c.execute("UPDATE crm_followup_cycles SET doctor_id='old-owner' WHERE lead_id=?",(lead["id"],))
            item=dict(lead);attach(c,[item]);self.assertFalse(item["followup_progress"]["has_cycle"])
            c.execute("UPDATE pipelines SET course_key='course' WHERE id=?",(self.pipeline["id"],))
            attach(c,[item]);self.assertNotIn("followup_progress",item)
