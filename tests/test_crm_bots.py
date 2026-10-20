import copy
import json
import io
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock
from types import SimpleNamespace

from crm_access import institute_access
from crm_bots import BotStore, definition, emit, message_allowed
from crm_store import CrmStore, Conflict
from crm_worker import tick as send_tick


class BotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.crm = CrmStore(Path(self.tmp.name), lambda n: {"status": "new"})
        self.crm.bootstrap({"Victor Paranhos de Andrade": ["Organico Paranhos", "REGENCODE"]})
        listing = self.crm.listing(); self.doc = listing["doctors"][0]["id"]
        pipes = self.crm.listing(self.doc)["pipelines"]
        self.pipeline = next(p for p in pipes if not p["course_key"])
        self.course = next(p for p in pipes if p["course_key"])
        self.stage, self.other = [s["id"] for s in self.pipeline["stages"][:2]]
        self.channel = self.crm.channel({"doctor_id": self.doc, "phone": "11900000001", "name": "Teste", "instance": "test"})
        with self.crm.db() as c:
            c.execute("UPDATE channels SET state='open'")
        self.bots = BotStore(self.crm)
        self.base = {"trigger": {"kind": "stage", "stage_id": self.stage, "delay_minutes": 0}, "stop_on_reply": True,
            "nodes": [{"id": "1", "kind": "start", "data": {}, "next": {"next": "2"}},
                      {"id": "2", "kind": "note", "data": {"body": "Teste"}, "next": {"next": "3"}},
                      {"id": "3", "kind": "end", "data": {}, "next": {}}]}

    def save(self, info=None, pipeline=None):
        return self.bots.save({"doctor_id": self.doc, "pipeline_id": pipeline or self.pipeline["id"], "name": "Teste", "definition": info or self.base}, 1)

    def activate(self, info=None):
        bot = self.save(info); self.bots.publish(bot["id"], self.doc, bot["revision"], True, 1)
        return self.bots.detail(bot["id"], self.doc)

    def lead(self, **changes):
        return self.crm.lead({"doctor_id": self.doc, "pipeline_id": self.pipeline["id"], "stage_id": self.stage,
            "name": "Pessoa Exemplo", "phone": "5511900000009", "title": "Teste", **changes}, "Equipe")

    def ticks(self, count=5, now=None):
        for _ in range(count): self.bots.tick(now)

    def incoming(self, body="Ola", provider="test-message", stamp=None):
        self.crm.receive("doc4docs-vielle-" + self.channel, {"key": {"remoteJid": "5511900000009@s.whatsapp.net", "fromMe": False, "id": provider},
            "message": {"conversation": body}, "messageTimestamp": stamp or int(time.time())}, "messages.upsert")

    def message_info(self):
        info = copy.deepcopy(self.base)
        info["nodes"][1].update(kind="message", data={"body": "Ola {{primeiro_nome}}", "channel_id": self.channel})
        return info

    def test_new_draft_does_not_run_and_activation_does_not_replay_old_events(self):
        bot = self.save(); self.lead(); self.ticks()
        self.assertEqual(self.bots.history(bot["id"], self.doc), [])
        self.bots.publish(bot["id"], self.doc, bot["revision"], True, 1); self.ticks()
        self.assertEqual(self.bots.history(bot["id"], self.doc), [])

    def test_future_event_runs_once_and_notes_are_transactional(self):
        bot = self.activate(); lead = self.lead(); self.ticks(8)
        runs = self.bots.history(bot["id"], self.doc)
        self.assertEqual(len(runs), 1); self.assertEqual(runs[0]["status"], "done")
        with self.crm.db() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM events WHERE kind='note'").fetchone()[0], 1)
            c.execute("UPDATE bot_events SET processed=0")
        self.ticks(); self.assertEqual(len(self.bots.history(bot["id"], self.doc)), 1)

    def test_delay_survives_restart_and_stage_change_cancels(self):
        info = copy.deepcopy(self.base); info["trigger"]["delay_minutes"] = 60
        bot = self.activate(info); lead = self.lead(); self.ticks()
        run = self.bots.history(bot["id"], self.doc)[0]; self.assertEqual(run["steps"], 0)
        self.bots = BotStore(CrmStore(Path(self.tmp.name)))
        with self.crm.db() as c: c.execute("UPDATE leads SET stage_id=? WHERE id=?", (self.other, lead))
        self.bots.tick(run["due_at"]+1)
        self.assertEqual(self.bots.history(bot["id"], self.doc)[0]["status"], "cancelled")

    def test_reply_immediately_blocks_queued_send_even_before_next_bot_tick(self):
        bot = self.activate(self.message_info()); self.lead(); self.ticks(2)
        with self.crm.db() as c:
            run = dict(c.execute("SELECT * FROM bot_runs").fetchone()); self.assertTrue(message_allowed(c, run["id"]))
        self.incoming(stamp=int(time.time())-100)
        with self.crm.db() as c: self.assertFalse(message_allowed(c, run["id"]))
        gateway = Mock(); send_tick(self.crm, gateway); gateway.send.assert_not_called()
        self.ticks(); self.assertEqual(self.bots.history(bot["id"], self.doc)[0]["status"], "cancelled")

    def test_optout_prevents_enqueue_and_reactivation_does_not_resume(self):
        bot = self.activate(self.message_info()); lead = self.lead(); self.ticks(2)
        self.incoming("SAIR"); self.ticks()
        with self.crm.db() as c:
            self.assertEqual(c.execute("SELECT status FROM messages WHERE direction='out'").fetchone()[0], "skipped")
            c.execute("UPDATE contacts SET opted_out=0")
        self.ticks(); self.assertEqual(self.bots.history(bot["id"], self.doc)[0]["status"], "cancelled")

    def test_bot_message_uses_queue_and_unknown_send_is_never_retried(self):
        bot = self.activate(self.message_info()); self.lead(); self.ticks(2)
        gateway = Mock(); gateway.send.side_effect = RuntimeError('network')
        send_tick(self.crm, gateway); self.ticks(5); send_tick(self.crm, gateway)
        self.assertEqual(gateway.send.call_count, 1)
        self.assertEqual(self.bots.history(bot["id"], self.doc)[0]["status"], "failed")

    def test_successful_send_advances_without_duplicate_after_restart(self):
        bot = self.activate(self.message_info()); self.lead(); self.ticks(2)
        gateway = Mock(); gateway.send.return_value = 'provider-1'; send_tick(self.crm, gateway)
        self.bots = BotStore(CrmStore(Path(self.tmp.name))); self.ticks()
        self.assertEqual(self.bots.history(bot["id"], self.doc)[0]["status"], "done")
        with self.crm.db() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM messages WHERE direction='out'").fetchone()[0], 1)
            self.assertIn("Ola Pessoa", c.execute("SELECT body FROM messages WHERE direction='out'").fetchone()[0])

    def test_manual_start_request_is_idempotent_and_pause_cancels_queue(self):
        bot = self.activate(self.message_info()); lead = self.lead()
        a = self.bots.start(bot["id"], self.doc, lead, 'request-1'); b = self.bots.start(bot["id"], self.doc, lead, 'request-1')
        self.assertEqual(a, b); self.ticks(2)
        self.bots.publish(bot["id"], self.doc, bot["revision"], False, 1)
        gateway = Mock(); send_tick(self.crm, gateway); gateway.send.assert_not_called()

    def test_course_access_and_cross_scope_rejected(self):
        info = copy.deepcopy(self.base); info['trigger']['stage_id'] = self.course['stages'][0]['id']
        bot = self.save(info, self.course['id'])
        with institute_access([]):
            self.assertEqual(self.bots.listing(self.doc)['bots'], [])
            with self.assertRaises(PermissionError): self.bots.detail(bot['id'], self.doc)
        info['nodes'][1] = {'id': '2', 'kind': 'move', 'data': {'stage_id': self.stage}, 'next': {'next': '3'}}
        with self.assertRaises(ValueError): self.save(info, self.course['id'])

    def test_validation_rejects_cycles_unknown_actions_and_incomplete_publish(self):
        info = copy.deepcopy(self.base); info['nodes'][1]['next']['next'] = '2'
        with self.assertRaises(ValueError): definition(info)
        info = copy.deepcopy(self.base); info['nodes'][1]['kind'] = 'code'
        with self.assertRaises(ValueError): definition(info)
        info = copy.deepcopy(self.base); info['nodes'][1]['next'] = {}
        bot = self.save(info)
        with self.assertRaises(ValueError): self.bots.publish(bot['id'], self.doc, bot['revision'], True, 1)

    def test_permission_revocation_cancels_existing_run(self):
        bot = self.activate(self.message_info()); self.lead(); self.ticks(2)
        self.bots.tick(permitted=lambda b: False)
        self.assertEqual(self.bots.history(bot['id'], self.doc)[0]['status'], 'cancelled')

    def test_automated_and_campaign_sends_share_daily_limit(self):
        self.activate(self.message_info()); self.lead(); self.ticks(2)
        with self.crm.db() as c:
            c.execute('UPDATE channels SET daily_limit=1')
            queued = dict(c.execute("SELECT * FROM messages WHERE direction='out'").fetchone())
            c.execute("INSERT INTO messages(id,channel_id,contact_id,body,direction,status,created_at,sent_at,bot_run_id) VALUES('used',?,?,?,'out','sent',?,?,?)", (self.channel, queued['contact_id'], 'Earlier', time.time(), time.time(), queued['bot_run_id']))
        self.assertIsNone(self.crm.claim())

    def test_conditions_move_task_and_tag(self):
        info = copy.deepcopy(self.base)
        info['nodes'] = [info['nodes'][0], {'id':'2','kind':'tag','data':{'label':'Curso'},'next':{'next':'3'}},
            {'id':'3','kind':'condition','data':{'field':'tag','value':'Curso'},'next':{'yes':'4','no':'6'}},
            {'id':'4','kind':'task','data':{'title':'Ligar','minutes':60},'next':{'next':'5'}},
            {'id':'5','kind':'move','data':{'stage_id':self.other},'next':{'next':'6'}},
            {'id':'6','kind':'end','data':{},'next':{}}]
        bot = self.activate(info); lead = self.lead(); self.ticks(10)
        self.assertEqual(self.bots.history(bot['id'],self.doc)[0]['status'],'done')
        self.assertEqual(self.bots.listing(self.doc)['tasks'][0]['title'],'Ligar')
        with self.crm.db() as c: self.assertEqual(c.execute('SELECT stage_id FROM leads WHERE id=?',(lead,)).fetchone()[0],self.other)

    def test_form_link_is_scope_checked_and_works_after_restart(self):
        from crm_forms import FormStore
        forms = FormStore(self.crm)
        form = forms.save({'doctor_id':self.doc,'definition':{'title':'Contato','fields':[{'id':'name','type':'name','label':'Nome'}]}},1)
        forms.publish(form['id'],self.doc,form['revision'],'published')
        info = self.message_info(); info['nodes'][1].update(kind='form',data={'body':'Veja aqui','channel_id':self.channel,'form_id':form['id']})
        bot=self.activate(info); self.lead(); self.bots=BotStore(CrmStore(Path(self.tmp.name))); self.ticks(2)
        with self.crm.db() as c:
            body=c.execute("SELECT body FROM messages WHERE direction='out'").fetchone()[0]
            self.assertIn('/f/'+form['slug'],body)

    def test_concurrent_ticks_do_not_duplicate_actions(self):
        from concurrent.futures import ThreadPoolExecutor
        bot=self.activate(); self.lead()
        with ThreadPoolExecutor(max_workers=4) as workers:
            list(workers.map(lambda n:self.bots.tick(),range(12)))
        self.assertEqual(len(self.bots.history(bot['id'],self.doc)),1)
        with self.crm.db() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM events WHERE kind='note'").fetchone()[0],1)

    def test_staff_can_save_but_cannot_activate_and_cross_origin_is_rejected(self):
        from crm_bots_api import handle
        bot=self.save()
        p={'doctor_id':self.doc,'id':bot['id'],'revision':bot['revision'],'active':True,'confirm':True}
        raw=json.dumps(p).encode()
        handler=SimpleNamespace(command='POST',headers={'Content-Length':str(len(raw))},rfile=io.BytesIO(raw),
            connection=Mock(),same_origin_request=lambda:True,auth_json=Mock(),server=SimpleNamespace(auth_store=Mock()))
        with self.assertRaises(PermissionError): handle(handler,self.crm,'bots/publish',{}, {'id':2,'is_master':False})
        handler.rfile=io.BytesIO(raw);handler.same_origin_request=lambda:False
        handle(handler,self.crm,'bots/publish',{}, {'id':1,'is_master':True})
        self.assertEqual(handler.auth_json.call_args.args[1],403)

    def test_editing_active_draft_does_not_change_running_version(self):
        bot=self.activate()
        changed=copy.deepcopy(self.base);changed['nodes'][1]['data']['body']='Outra nota'
        self.bots.save({'id':bot['id'],'revision':bot['revision'],'doctor_id':self.doc,'pipeline_id':self.pipeline['id'],'name':'Teste','definition':changed},1)
        self.lead();self.ticks()
        with self.crm.db() as c:
            self.assertEqual(c.execute("SELECT body FROM events WHERE kind='note'").fetchone()[0],'Teste')

    def test_old_history_import_does_not_create_bot_events(self):
        self.lead()
        with self.crm.db() as c: c.execute('DELETE FROM bot_events')
        self.crm.receive('doc4docs-vielle-'+self.channel,{'key':{'remoteJid':'5511900000009@s.whatsapp.net','fromMe':True,'id':'old'},'message':{'conversation':'Antiga'},'messageTimestamp':int(time.time())-600},'messages.upsert',history=True)
        with self.crm.db() as c: self.assertEqual(c.execute('SELECT count(*) FROM bot_events').fetchone()[0],0)


if __name__ == '__main__': unittest.main()
