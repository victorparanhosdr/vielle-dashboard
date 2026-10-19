from concurrent.futures import ThreadPoolExecutor
from functools import partial
import time
import unittest
from unittest.mock import Mock

from tests import test_crm as base
from crm_store import Conflict, CrmStore
from crm_worker import tick


class InboxTests(unittest.TestCase):
    def setUp(self):
        base.CrmTests.setUp(self)
        self.store.reply=partial(self.store.reply,sender_name="Equipe")
    lead=base.CrmTests.lead
    campaign=base.CrmTests.campaign

    def incoming(self, channel=None, number="5500000000002", provider="in-1", body="Quero agendar"):
        channel=channel or self.channel
        with self.store.db() as c:
            ch=self.store.row(c,"channels",channel)
        self.store.receive(ch["instance"],{"key":{"id":provider,"remoteJid":number+"@s.whatsapp.net"},"message":{"conversation":body}},"messages.upsert")
        return self.store.inbox(ch["doctor_id"],"user")["conversations"][0]

    def payload(self, conversation, **changes):
        cursor=self.store.conversation(self.a,conversation["channel_id"],conversation["contact_id"])["inbound_cursor"]
        return {"doctor_id":self.a,"channel_id":conversation["channel_id"],"contact_id":conversation["contact_id"],"cursor":cursor,**changes}

    def test_viewing_never_resolves_and_close_is_shared_race_safe_per_number(self):
        item=self.incoming()
        data=self.store.conversation(self.a,self.channel,item["contact_id"])
        for _ in range(3):self.store.conversation(self.a,self.channel,item["contact_id"])
        self.assertEqual(self.store.inbox(self.a,"user")["conversations"][0]["unread"],1)
        self.incoming(provider="in-2")
        self.store.close_conversation(self.payload(item,cursor=data["inbound_cursor"]),"user")
        self.assertEqual(self.store.inbox(self.a,"user")["conversations"][0]["unread"],1)
        self.assertEqual(self.store.inbox(self.a,"other")["conversations"][0]["unread"],1)
        second=self.store.channel({"doctor_id":self.a,"name":"Segundo","phone":"5500000000003"})
        new=self.incoming(second,provider="in-3")
        self.assertEqual(new["unread"],1)
        with self.assertRaises(ValueError):self.store.close_conversation(self.payload(new,cursor=data["inbound_cursor"]),"user")
        self.assertEqual(self.store.inbox(self.a,"user",kind="unread")["counts"]["unread"],2)
        self.store.close_conversation(self.payload(item),"other")
        self.assertEqual(self.store.inbox(self.a,"user",channel=self.channel,kind="waiting")["total"],0)
        self.assertEqual(self.store.inbox(self.a,"user",channel=self.channel,kind="all")["total"],1)
        self.assertEqual(self.store.conversation(self.a,self.channel,item["contact_id"])["resolution"],"closed")
        self.incoming(provider="in-2")  # Webhook retry is not a new message.
        self.assertEqual(self.store.inbox(self.a,"user",channel=self.channel,kind="unread")["total"],0)
        self.incoming(provider="new-after-close")
        self.assertEqual(self.store.inbox(self.a,"other",channel=self.channel)["conversations"][0]["unread"],1)

    def test_legacy_view_markers_are_not_treated_as_answers(self):
        item=self.incoming()
        cursor=self.payload(item)["cursor"]
        with self.store.db() as c:
            c.execute("INSERT INTO conversation_reads VALUES(?,?,?,?)",(self.channel,item["contact_id"],"user",cursor))
            c.execute("DROP TABLE reply_requests")
            c.execute("CREATE TABLE reply_requests(request_id TEXT PRIMARY KEY,message_id TEXT NOT NULL REFERENCES messages(id),user_id TEXT NOT NULL)")
            c.execute("DROP TABLE conversation_resolutions")
        migrated=CrmStore(self.tmp.name)
        self.assertEqual(migrated.inbox(self.a,"user")["conversations"][0]["unread"],1)
        migrated.close_conversation(self.payload(item),"user")
        self.assertEqual(CrmStore(self.tmp.name).inbox(self.a,"other",kind="unread")["total"],0)

    def test_doctor_channel_contact_and_cursor_scope(self):
        item=self.incoming()
        with self.assertRaises(ValueError):self.store.conversation(self.b,self.channel,item["contact_id"])
        with self.assertRaises(ValueError):self.store.inbox(self.b,"user",channel=self.channel)
        for cursor in (999999,0,-1,True):
            with self.assertRaises(ValueError):self.store.close_conversation(self.payload(item,cursor=cursor),"user")
        with self.assertRaises(ValueError):self.store.close_conversation(self.payload(item,doctor_id=self.b),"user")
        self.assertEqual(self.store.inbox(self.b,"user")["conversations"],[])
        other_channel=self.store.channel({"doctor_id":self.b,"name":"B","phone":"5500000000004"})
        with self.assertRaises(ValueError):self.store.conversation(self.b,other_channel,item["contact_id"])

    def test_drafts_not_inbox_search_filters_and_pagination(self):
        self.campaign()
        self.assertEqual(self.store.inbox(self.a,"user")["counts"]["all"],0)
        for i in range(85):self.incoming(provider=f"in-{i}")
        item=self.store.inbox(self.a,"user")["conversations"][0]
        data=self.store.conversation(self.a,self.channel,item["contact_id"])
        self.assertEqual(len(data["messages"]),80);self.assertTrue(data["has_older"])
        older=self.store.conversation(self.a,self.channel,item["contact_id"],data["messages"][0]["seq"])
        self.assertEqual(len(older["messages"]),5)
        self.assertFalse(older["has_older"])
        self.assertEqual(self.store.inbox(self.a,"user",search="agendar")["total"],1)
        self.assertEqual(self.store.inbox(self.a,"user",search="nao existe")["total"],0)
        self.assertEqual(self.store.inbox(self.a,"user",kind="waiting")["total"],1)

    def test_reply_is_available_by_default_but_keeps_connection_and_optout_checks(self):
        lead=self.lead(consent=False)
        ct=self.store.detail(lead,self.a)["lead"]["contact_id"]
        p={"doctor_id":self.a,"channel_id":self.channel,"contact_id":ct,"body":"Ola","request_id":"one"}
        with self.assertRaises(ValueError):self.store.reply(p,"user",True)
        with self.store.db() as c:c.execute("UPDATE channels SET state='open' WHERE id=?",(self.channel,))
        self.assertTrue(self.store.conversation(self.a,self.channel,ct)["reply_allowed"])
        self.assertTrue(self.store.reply(p,"user",True))
        self.incoming()
        with self.assertRaises(ValueError):self.store.reply({**p,"request_id":"disabled"},"user",False)
        self.assertTrue(self.store.reply(p,"user",True))
        with self.store.db() as c:c.execute("UPDATE messages SET created_at=? WHERE direction='in'",(time.time()-90000,))
        self.assertTrue(self.store.reply({**p,"request_id":"two"},"user",True))
        self.assertIsNone(self.store.detail(lead,self.a)["lead"]["consent_at"])
        self.store.activity({"id":lead,"doctor_id":self.a,"kind":"optout","body":"Solicitou"},"Equipe")
        self.assertFalse(self.store.conversation(self.a,self.channel,ct)["reply_allowed"])
        with self.assertRaises(ValueError):self.store.reply({**p,"request_id":"three"},"user",True)

    def test_reply_idempotency_worker_and_waiting_filter(self):
        item=self.incoming()
        with self.store.db() as c:c.execute("UPDATE channels SET state='open' WHERE id=?",(self.channel,))
        p=self.payload(item,body="Vamos agendar",request_id="request-1")
        with ThreadPoolExecutor(max_workers=2) as pool:
            ids=list(pool.map(lambda _:self.store.reply(p,"user",True),range(2)))
        self.assertEqual(ids[0],ids[1])
        with self.assertRaises(Conflict):self.store.reply({**p,"body":"Outro texto"},"user",True)
        with self.assertRaises(Conflict):self.store.reply(p,"other",True)
        self.assertEqual(self.store.inbox(self.a,"user",kind="waiting")["total"],1)
        gateway=Mock();gateway.send.return_value="out-1"
        self.assertTrue(tick(self.store,gateway))
        gateway.send.assert_called_once()
        self.assertEqual(self.store.inbox(self.a,"user",kind="waiting")["total"],0)
        self.assertEqual(self.store.inbox(self.a,"other",kind="unread")["total"],0)
        data=self.store.conversation(self.a,self.channel,item["contact_id"])
        self.assertEqual(data["messages"][-1]["status"],"sent")
        self.assertEqual(data["resolution"],"replied")

    def test_manual_only_worker_does_not_dispatch_even_running_campaigns(self):
        campaign=self.campaign()
        with self.store.db() as c:
            c.execute("UPDATE channels SET state='open' WHERE id=?",(self.channel,))
        self.store.campaign_status(campaign,self.a,"start",True)
        item=self.incoming()
        self.store.reply(self.payload(item,body="Resposta manual",request_id="manual-only"),"user",True)
        gateway=Mock();gateway.send.return_value="manual-accepted"
        self.assertTrue(tick(self.store,gateway,self.now,allow_campaigns=False))
        self.assertFalse(tick(self.store,gateway,self.now+61,allow_campaigns=False))
        gateway.send.assert_called_once()
        self.assertIsNone(gateway.send.call_args.args[0]["campaign_id"])
        with self.store.db() as c:
            self.assertEqual(c.execute("SELECT status FROM messages WHERE campaign_id=?",(campaign,)).fetchone()[0],"queued")
        gateway.send.return_value="campaign-accepted"
        self.assertTrue(tick(self.store,gateway,self.now+61,allow_campaigns=True))

    def test_manual_replies_have_no_daily_quota_or_campaign_interval(self):
        item=self.incoming()
        future=self.now+86400
        with self.store.db() as c:
            c.execute("UPDATE channels SET state='open',daily_limit=1,next_send_at=? WHERE id=?",(future,self.channel))
        for i in range(12):
            self.store.reply(self.payload(item,body=f"Resposta {i}",request_id=f"no-manual-cap-{i}"),"user",True)
            message=self.store.claim(self.now+i*2,allow_campaigns=False)
            self.assertIsNotNone(message)
            self.assertIsNone(message["campaign_id"])
            self.assertIsNone(self.store.claim(self.now+i*2,allow_campaigns=False))
            self.store.finish(message,f"accepted-{i}")
        with self.store.db() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM messages WHERE direction='out' AND status='sent'").fetchone()[0],12)
            self.assertEqual(c.execute("SELECT next_send_at FROM channels WHERE id=?",(self.channel,)).fetchone()[0],future)

    def test_campaign_quota_and_cooldown_do_not_block_manual_replies(self):
        campaign=self.campaign()
        with self.store.db() as c:c.execute("UPDATE channels SET state='open',daily_limit=1 WHERE id=?",(self.channel,))
        self.store.campaign_status(campaign,self.a,"start",True)
        first=self.store.claim(self.now)
        self.store.finish(first,"campaign-accepted")
        second_campaign=self.campaign(name="Outra campanha")
        self.store.campaign_status(second_campaign,self.a,"start",True)
        with self.store.db() as c:c.execute("UPDATE messages SET sent_at=? WHERE id=?",(self.now,first["id"]))
        item=self.incoming()
        self.store.reply(self.payload(item,body="Atendimento",request_id="after-campaign-cap"),"user",True)
        manual=self.store.claim(self.now+1)
        self.assertIsNotNone(manual);self.assertIsNone(manual["campaign_id"])
        self.store.finish(manual,"manual-accepted")
        self.assertIsNone(self.store.claim(self.now+61))

    def test_manual_replies_do_not_consume_campaign_quota(self):
        campaign=self.campaign()
        with self.store.db() as c:c.execute("UPDATE channels SET state='open',daily_limit=1 WHERE id=?",(self.channel,))
        self.store.campaign_status(campaign,self.a,"start",True)
        item=self.incoming()
        self.store.reply(self.payload(item,body="Atendimento",request_id="manual-before-campaign"),"user",True)
        manual=self.store.claim(self.now)
        self.assertIsNone(manual["campaign_id"])
        self.store.finish(manual,"manual-accepted")
        self.assertEqual(self.store.claim(self.now+1)["campaign_id"],campaign)

    def test_successful_reply_only_resolves_observed_boundary(self):
        item=self.incoming()
        with self.store.db() as c:c.execute("UPDATE channels SET state='open' WHERE id=?",(self.channel,))
        payload=self.payload(item,body="Vamos agendar",request_id="boundary")
        self.incoming(provider="arrived-before-queue")
        self.store.reply(payload,"user",True)
        self.assertEqual(self.store.inbox(self.a,"user")["conversations"][0]["unread"],2)
        claimed=self.store.claim()
        self.incoming(provider="arrived-in-flight")
        self.assertEqual(self.store.inbox(self.a,"user")["conversations"][0]["unread"],3)
        self.store.finish(claimed,"accepted")
        self.assertEqual(self.store.inbox(self.a,"other")["conversations"][0]["unread"],2)
        # A retry with a later cursor cannot silently acknowledge later arrivals.
        self.store.reply(self.payload(item,body=payload["body"],request_id="boundary"),"user",True)
        self.store.finish(claimed,"accepted")
        self.assertEqual(self.store.inbox(self.a,"other",kind="waiting")["total"],1)
        self.assertEqual(self.store.inbox(self.a,"other")["conversations"][0]["unread"],2)

    def test_reply_cannot_resolve_outbound_or_another_number_cursor(self):
        item=self.incoming()
        second=self.store.channel({"doctor_id":self.a,"name":"Segundo","phone":"5500000000003"})
        other=self.incoming(second,provider="other-in")
        with self.store.db() as c:c.execute("UPDATE channels SET state='open' WHERE id=?",(self.channel,))
        p=self.payload(item,body="Ola",request_id="scope")
        with self.assertRaises(ValueError):self.store.reply({**p,"cursor":self.payload(other)["cursor"]},"user",True)
        self.store.reply(p,"user",True)
        outbound=self.store.conversation(self.a,self.channel,item["contact_id"])["messages"][-1]["seq"]
        with self.assertRaises(ValueError):self.store.reply({**p,"request_id":"scope-2","cursor":outbound},"user",True)

    def test_direct_unknown_blocks_channel_without_retry(self):
        item=self.incoming()
        with self.store.db() as c:c.execute("UPDATE channels SET state='open' WHERE id=?",(self.channel,))
        p=self.payload(item,body="Vamos agendar",request_id="request-1")
        self.store.reply(p,"user",True)
        gateway=Mock();gateway.send.side_effect=TimeoutError()
        tick(self.store,gateway)
        self.assertEqual(self.store.conversation(self.a,self.channel,item["contact_id"])["messages"][-1]["status"],"unknown")
        self.assertEqual(self.store.inbox(self.a,"user",kind="waiting")["total"],1)
        self.assertEqual(self.store.inbox(self.a,"other",kind="unread")["total"],1)
        self.assertFalse(tick(self.store,gateway, time.time()+300))
        with self.assertRaises(ValueError):self.store.reply({**p,"request_id":"request-2"},"user",True)
        self.store.close_conversation(self.payload(item),"user")
        self.assertEqual(self.store.inbox(self.a,"other",kind="unread")["total"],0)

    def test_stale_direct_send_is_not_redispatched(self):
        item=self.incoming()
        with self.store.db() as c:c.execute("UPDATE channels SET state='open' WHERE id=?",(self.channel,))
        self.store.reply(self.payload(item,body="Ola",request_id="stale"),"user",True)
        claimed=self.store.claim()
        self.assertIsNotNone(claimed)
        self.assertIsNone(self.store.claim(time.time()+200))
        self.assertEqual(self.store.conversation(self.a,self.channel,item["contact_id"])["messages"][-1]["status"],"unknown")
        self.assertEqual(self.store.inbox(self.a,"user",kind="unread")["total"],1)

    def test_reply_uses_actual_inbound_phone_not_patient_primary_alias(self):
        self.store.patient_lookup=lambda number:{"status":"matched","uuid":"patient-one","name":"Paciente teste"}
        self.lead()
        item=self.incoming(number="5500000000099")
        data=self.store.conversation(self.a,self.channel,item["contact_id"])
        self.assertEqual(data["reply_phone"],"5500000000099")
        with self.store.db() as c:c.execute("UPDATE channels SET state='open' WHERE id=?",(self.channel,))
        p=self.payload(item,body="Ola",request_id="alias",phone="5500000000002")
        with self.assertRaises(Conflict):self.store.reply(p,"user",True)
        self.store.reply({**p,"phone":data["reply_phone"]},"user",True)
        self.assertEqual(self.store.claim()["phone"],"5500000000099")

    def test_sender_selection_preserves_recipient_and_is_scoped_to_doctor(self):
        self.store.patient_lookup=lambda number:{"status":"matched","uuid":"one","name":"Paciente teste"}
        self.lead()
        item=self.incoming(number="5500000000099")
        sender=self.store.channel({"doctor_id":self.a,"name":"Recepcao","phone":"5500000000003"})
        p=self.payload(item,channel_id=sender,source_channel_id=self.channel,phone="5500000000099",body="Ola",request_id="handoff")
        with self.assertRaises(ValueError):self.store.reply(p,"user",True)
        with self.store.db() as c:c.execute("UPDATE channels SET state='open' WHERE id=?",(sender,))
        self.store.reply(p,"user",True)
        message=self.store.claim()
        self.assertEqual(message["channel_id"],sender)
        self.assertEqual(message["phone"],"5500000000099")
        self.assertEqual(self.store.conversation(self.a,sender,item["contact_id"])["reply_phone"],"5500000000099")
        self.incoming(sender,number="5500000000099",provider="sender-pending")
        self.store.finish(message,"handoff-accepted")
        self.assertEqual(self.store.inbox(self.a,"other",channel=self.channel,kind="unread")["total"],0)
        self.assertEqual(self.store.inbox(self.a,"other",channel=sender,kind="unread")["total"],1)
        with self.assertRaises(Conflict):self.store.reply({**p,"source_channel_id":sender},"user",True)
        other=self.store.channel({"doctor_id":self.b,"name":"Outro","phone":"5500000000004"})
        for changes in ({"channel_id":other},{"source_channel_id":other}):
            with self.assertRaises(ValueError):self.store.reply({**p,**changes,"request_id":"bad"},"user",True)

    def test_signature_is_bold_persisted_and_uses_server_name(self):
        item=self.incoming()
        with self.store.db() as c:c.execute("UPDATE channels SET state='open' WHERE id=?",(self.channel,))
        p=self.payload(item,body="Podemos agendar?",request_id="signed",sender_name="Falso",sender_user_id="other")
        key=self.store.reply(p,"real-user",True,sender_name="*Ana*\n_Clara_")
        data=self.store.conversation(self.a,self.channel,item["contact_id"])
        message=data["messages"][-1]
        self.assertEqual(message["body"],"*Ana Clara*\n\nPodemos agendar?")
        self.assertEqual(message["sender_name"],"Ana Clara")
        self.assertEqual(message["sender_user_id"],"real-user")
        self.assertEqual(self.store.reply(p,"real-user",True,sender_name="Novo nome"),key)
        gateway=Mock();gateway.send.return_value="signed-out"
        tick(self.store,gateway)
        self.assertEqual(gateway.send.call_args.args[0]["body"],message["body"])

    def test_default_reply_channel_uses_last_inbound_not_alphabetic_or_last_outbound(self):
        key=self.lead()
        self.incoming()
        sender=self.store.channel({"doctor_id":self.a,"name":"A Recepcao","phone":"5500000000003"})
        self.incoming(sender,provider="new-in")
        with self.store.db() as c:
            contact=self.store.detail(key,self.a)["lead"]["contact_id"]
            c.execute("INSERT INTO messages(id,channel_id,contact_id,body,direction,status,created_at) VALUES('late-out',?,?,?,'out','sent',?)",(self.channel,contact,"Outro envio",time.time()))
        self.assertEqual(self.store.detail(key,self.a)["preferred_channel_id"],sender)

    def test_campaign_messages_keep_creator_signature(self):
        self.campaign()
        with self.store.db() as c:
            message=dict(c.execute("SELECT * FROM messages WHERE campaign_id IS NOT NULL").fetchone())
        self.assertTrue(message["body"].startswith("*Equipe*\n\n"))
        self.assertEqual(message["sender_name"],"Equipe")

    def test_campaign_success_does_not_resolve_inbound_messages(self):
        lead=self.lead()
        item=self.incoming()
        campaign=self.campaign([lead])
        with self.store.db() as c:c.execute("UPDATE channels SET state='open' WHERE id=?",(self.channel,))
        self.store.campaign_status(campaign,self.a,"start",True)
        gateway=Mock();gateway.send.return_value="campaign-out"
        self.assertTrue(tick(self.store,gateway,self.now))
        self.assertEqual(self.store.inbox(self.a,"user",kind="waiting")["total"],1)
        self.assertEqual(self.store.conversation(self.a,self.channel,item["contact_id"])["unread"],1)
