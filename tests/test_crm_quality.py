import copy
import io
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlparse

from crm_access import crm_access
from crm_api import handle_request
import crm_quality as quality
from crm_store import CrmStore, Conflict


class QualityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = CrmStore(Path(self.tmp.name))
        self.store.bootstrap({"Profissional Exemplo": ["Clínica"], "Outro Profissional": ["Outro Funil"]})
        listing = self.store.listing()
        self.doc = next(d["id"] for d in listing["doctors"] if d["name"] == "Profissional Exemplo")
        self.other = next(d["id"] for d in listing["doctors"] if d["id"] != self.doc)
        p = self.store.listing(self.doc)["pipelines"][0]
        self.lead = self.store.lead({"doctor_id": self.doc, "pipeline_id": p["id"], "stage_id": p["stages"][0]["id"],
            "name": "Cliente Exemplo", "phone": "5511900000009", "title": "Exemplo"}, "Equipe")
        self.contact = self.store.detail(self.lead, self.doc)["lead"]["contact_id"]
        self.channel = self.store.channel({"doctor_id": self.doc, "name": "Comercial", "phone": "5511900000001"})
        self.now = time.time()
        self.message("in-1", "Olá, preciso de ajuda.", "in", stamp=self.now-40)
        self.message("out-1", "Olá, vou ajudar.", actor="11", name="Atendente Exemplo", stamp=self.now-30)

    def message(self, ident, body, direction="out", actor="", name="", stamp=None, **extra):
        with self.store.db() as c:
            c.execute("INSERT INTO messages(id,channel_id,contact_id,body,direction,status,created_at,sender_user_id,sender_name,media_id,bot_run_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (ident, self.channel, self.contact, body, direction, extra.get("status", "received" if direction == "in" else "sent"), stamp or self.now-20, actor, name, extra.get("media", ""), extra.get("bot", "")))

    def authorize(self, **changes):
        with self.store.db() as c:
            revision = quality.settings(c, self.doc)["revision"]
        return quality.save_settings(self.store, self.doc, {**quality.defaults(), "enabled": True,
            "revision": revision, "confirm": True, **changes}, "master-1")

    def request(self):
        return quality.request(self.store, self.doc, self.channel, self.contact, "master-1")

    def raw(self, messages, failures=None, na=False):
        data = json.loads(messages[1]["content"])
        assessments = []
        for actor in data["actors"]:
            own = next(m["id"] for m in data["messages"] if m["actor"] == actor and m["kind"] == "human")
            assessments.append({"actor": actor, "summary": "Resumo sintético", "feedback": "Orientação de exemplo",
                "items": [{"id": r["id"], "outcome": "na" if na or not r["enabled"] else "fail" if r["id"] in (failures or []) else "pass",
                    "reason": "Evidência sintética para teste", "evidence": [own]} for r in data["rubric"]]})
        return {"assessments": assessments, "score": 100000}

    def run_job(self, ident, failures=None, **kw):
        provider = Mock(side_effect=lambda messages: self.raw(messages, failures))
        self.assertTrue(quality.tick(self.store, provider, lambda actor: actor == "master-1", **kw))
        return quality.detail(self.store, self.doc, ident), provider

    def test_defaults_and_closing_do_not_enable_ai_or_change_messages(self):
        with self.assertRaises(ValueError): self.request()
        thread = self.store.conversation(self.doc, self.channel, self.contact)
        self.store.close_conversation({"doctor_id": self.doc, "channel_id": self.channel, "contact_id": self.contact, "cursor": thread["inbound_cursor"]}, "11")
        provider = Mock()
        self.assertFalse(quality.tick(self.store, provider, lambda _: True))
        provider.assert_not_called()
        with self.store.db() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM messages").fetchone()[0], 2)

    def test_settings_require_authorization_revision_and_bounded_parameters(self):
        for changes in ({"confirm": False}, {"daily_limit": 101}, {"max_interactions": -1}, {"revision": 4}, {"scripts": "x"*4001}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                quality.save_settings(self.store, self.doc, {**quality.defaults(), "enabled": True, "revision": 0, "confirm": True, **changes}, "master-1")
        self.authorize()
        with self.assertRaises(Conflict):
            quality.save_settings(self.store, self.doc, {**quality.defaults(), "enabled": True, "revision": 0, "confirm": True}, "master-1")

    def test_same_context_is_deduplicated_and_score_is_server_computed(self):
        self.authorize()
        ident = self.request()
        self.assertEqual(ident, self.request())
        job, provider = self.run_job(ident, ["clarity", "warmth"])
        self.assertEqual(job["status"], "done")
        self.assertEqual(job["assessments"][0]["score"], 40)
        self.assertEqual(provider.call_count, 1)
        self.assertFalse(quality.tick(self.store, provider, lambda _: True))
        self.assertEqual(provider.call_count, 1)

    def test_each_penalty_and_floor(self):
        for ids, expected in ((["professionalism"], 0), (["courtesy"], 50), (["clarity"], 70), (["clarity", "courtesy", "warmth"], 0)):
            items = [{"id": ident, "outcome": "fail"} for ident in ids]
            self.assertEqual(quality.score(items), expected)
        self.assertIsNone(quality.score([{"id": "clarity", "outcome": "na"}]))

    def test_financial_exclusion_is_system_policy_even_with_old_pricing_references(self):
        policy = self.authorize(scripts="Fluxo antigo exige taxa de agendamento.",
                                services="Tabela antiga com valores de consulta.")
        with self.store.db() as c:
            snap = quality.snapshot(c, self.store, self.doc, self.channel, self.contact, time.time())
        messages = quality.model_messages(snap, policy)
        system = messages[0]["content"]
        payload = json.loads(messages[1]["content"])
        self.assertEqual(payload["assessment_scope"], {"financial_conditions": "excluded"})
        self.assertIn("não existe valor ou cobrança obrigatória", system)
        self.assertIn("nenhum critério", system)
        self.assertIn("exclusão prevalece", system)
        for topic in ("preços", "taxas", "descontos", "créditos", "isenções"):
            self.assertIn(topic, system)
        description = next(row["description"] for row in payload["rubric"] if row["id"] == "complete")
        self.assertIn("não financeiras", description)
        self.assertIn("fora da avaliação", description)
        self.assertEqual(snap["version"], "monitoria-native-v2-no-pricing")
        self.assertTrue(next(row for row in payload["rubric"] if row["id"] == "courtesy")["enabled"])
        self.assertEqual(quality.CATALOG["courtesy"]["penalty"], 50)

    def test_financial_scope_does_not_enable_processing_or_change_evidence(self):
        self.message("waived", "Sua avaliação está isenta nesta campanha.", actor="11")
        with self.store.db() as c:
            policy = quality.settings(c, self.doc)
            snap = quality.snapshot(c, self.store, self.doc, self.channel, self.contact, time.time())
        messages = quality.model_messages(snap, policy)
        payload = json.loads(messages[1]["content"])
        self.assertTrue(any("isenta nesta campanha" in row["text"] for row in payload["messages"]))
        self.assertFalse(policy["enabled"])
        self.assertFalse(policy["auto_closed"])
        self.assertFalse(policy["selection_enabled"])
        provider = Mock()
        self.assertFalse(quality.tick(self.store, provider, lambda _: True))
        provider.assert_not_called()

    def test_pilot_has_no_numeric_resolution_penalty_or_count_based_proxy(self):
        policy = self.authorize()
        self.assertEqual(policy["max_interactions"], 0)
        with self.store.db() as c:
            snap = quality.snapshot(c, self.store, self.doc, self.channel, self.contact, time.time())
        messages = quality.model_messages(snap, policy)
        self.assertIn("não penalize a quantidade de mensagens ou turnos em nenhum critério", messages[0]["content"])
        self.assertIn("conversa longa não comprova ineficiência", messages[0]["content"])
        raw = self.raw(messages)
        next(item for item in raw["assessments"][0]["items"] if item["id"] == "resolution")["outcome"] = "fail"
        result = quality.validate_result(raw, snap, policy)[0]
        self.assertEqual(result["score"], 100)
        self.assertEqual(next(item for item in result["items"] if item["id"] == "resolution")["outcome"], "na")
        self.assertTrue(next(row for row in quality.rubric(policy) if row["id"] == "efficiency")["enabled"])

    def test_explicit_future_limit_keeps_turn_definition(self):
        policy = {**quality.defaults(), "max_interactions": 5}
        with self.store.db() as c:
            snap = quality.snapshot(c, self.store, self.doc, self.channel, self.contact, time.time())
        messages = quality.model_messages(snap, policy)
        self.assertIn("uma interação é um turno de resposta humana", messages[0]["content"])
        self.assertNotIn("Sem limite X configurado", messages[0]["content"])
        self.assertTrue(next(row for row in quality.rubric(policy) if row["id"] == "resolution")["enabled"])

    def test_preserves_repeated_messages_and_uses_native_authorship(self):
        self.message("repeat", "Olá, vou ajudar.", actor="22", name="Outro Atendente")
        self.authorize()
        job, provider = self.run_job(self.request(), ["clarity"])
        self.assertEqual(len(job["assessments"]), 2)
        self.assertEqual({a["actor_id"] for a in job["assessments"]}, {"11", "22"})
        transmitted = json.loads(provider.call_args.args[0][1]["content"])
        self.assertNotIn("Cliente Exemplo", json.dumps(transmitted))
        self.assertNotIn("Atendente Exemplo", json.dumps(transmitted))
        self.assertEqual(sum(m["text"] == "Olá, vou ajudar." for m in transmitted["messages"]), 2)
        self.assertTrue(all("message_id" not in m for m in transmitted["messages"]))

    def test_disabled_criteria_cannot_be_penalized_even_by_model(self):
        policy = self.authorize()
        with self.store.db() as c:
            snap = quality.snapshot(c, self.store, self.doc, self.channel, self.contact, time.time())
        raw = self.raw(quality.model_messages(snap, policy))
        for item in raw["assessments"][0]["items"]:
            if item["id"] in {"promptness", "knowledge", "scripts", "resolution"}:
                item["outcome"] = "fail"
        result = quality.validate_result(raw, snap, policy)[0]
        self.assertEqual(result["score"], 100)
        self.assertTrue(all(i["outcome"] == "na" for i in result["items"] if i["id"] in {"promptness", "knowledge", "scripts", "resolution"}))

    def test_invented_or_wrong_author_evidence_and_incomplete_rubrics_fail_closed(self):
        policy = self.authorize()
        self.message("other-reply", "Outra resposta", actor="22")
        with self.store.db() as c:
            snap = quality.snapshot(c, self.store, self.doc, self.channel, self.contact, time.time())
        valid = self.raw(quality.model_messages(snap, policy), ["clarity"])
        wrong = next(m["id"] for m in snap["messages"] if m["actor"] == "a2")
        variants = []
        bad = copy.deepcopy(valid);bad["assessments"][0]["items"].pop();variants.append(bad)
        bad = copy.deepcopy(valid);bad["assessments"][0]["items"][12]["evidence"] = ["invented"];variants.append(bad)
        bad = copy.deepcopy(valid);bad["assessments"][0]["items"][12]["evidence"] = [wrong];variants.append(bad)
        bad = copy.deepcopy(valid);bad["assessments"].pop();variants.append(bad)
        bad = copy.deepcopy(valid);bad["assessments"][0]["items"][1] = copy.deepcopy(bad["assessments"][0]["items"][0]);variants.append(bad)
        for value in variants:
            with self.assertRaises(ValueError): quality.validate_result(value, snap, policy)

    def test_no_attribution_for_bot_or_unknown_senders_or_unsent_messages(self):
        self.message("automation", "Mensagem automática", bot="example-bot", actor="33")
        self.message("external", "Mensagem externa")
        self.message("unsent", "Mensagem não enviada", actor="44", status="unknown")
        self.authorize()
        job, provider = self.run_job(self.request())
        self.assertEqual(len(job["assessments"]), 1)
        self.assertTrue(job["partial"])
        text = provider.call_args.args[0][1]["content"]
        self.assertNotIn("Mensagem não enviada", text)

    def test_no_text_from_customer_or_identified_operator_is_not_evaluable(self):
        self.authorize()
        with self.store.db() as c: c.execute("UPDATE messages SET sender_user_id='' WHERE direction='out'")
        with self.assertRaises(ValueError): self.request()

    def test_model_failure_does_not_fabricate_score_or_retry(self):
        self.authorize();ident = self.request()
        provider = Mock(side_effect=RuntimeError("sensitive provider body"))
        self.assertTrue(quality.tick(self.store, provider, lambda _: True))
        self.assertFalse(quality.tick(self.store, provider, lambda _: True))
        job = quality.detail(self.store, self.doc, ident)
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["assessments"], [])
        self.assertNotIn("sensitive", job["error"])

    def test_all_na_does_not_become_perfect_score(self):
        self.authorize();ident = self.request()
        quality.tick(self.store, lambda m: self.raw(m, na=True), lambda _: True)
        job = quality.detail(self.store, self.doc, ident)
        self.assertIsNone(job["assessments"][0]["score"])

    def test_failed_analysis_can_only_be_retried_explicitly_and_idempotently(self):
        self.authorize();ident=self.request()
        quality.tick(self.store, Mock(side_effect=RuntimeError()), lambda _: True)
        self.assertEqual(self.request(), ident)
        args={"retry_from":ident,"request_id":"request-example-12345"}
        retried=quality.request(self.store,self.doc,self.channel,self.contact,"master-1",**args)
        self.assertNotEqual(retried,ident)
        self.assertEqual(quality.request(self.store,self.doc,self.channel,self.contact,"master-1",**args),retried)
        self.assertEqual(self.request(),retried)
        job, _=self.run_job(retried)
        self.assertEqual(job["status"],"done")
        self.assertEqual(self.request(),retried)

    def test_resolution_cannot_exceed_interaction_limit_when_only_one_reply(self):
        policy={**self.authorize(), "max_interactions": 3}
        with self.store.db() as c:
            snap=quality.snapshot(c,self.store,self.doc,self.channel,self.contact,time.time())
        raw=self.raw(quality.model_messages(snap,policy),["resolution"])
        result=quality.validate_result(raw,snap,policy)[0]
        self.assertEqual(next(i["outcome"] for i in result["items"] if i["id"]=="resolution"),"na")
        self.assertEqual(result["score"],100)

    def test_pilot_rejects_automatic_settings_and_unapproved_caps(self):
        for changes in ({"auto_closed": True}, {"selection_enabled": True},
                        {"daily_limit": 21}, {"max_interactions": 1}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.authorize(**changes)
        with self.store.db() as c:
            self.assertFalse(quality.settings(c, self.doc)["enabled"])

    def test_review_is_audited_and_only_reviewed_scores_enter_average(self):
        self.authorize();ident = self.request();job, _ = self.run_job(ident, ["clarity", "warmth"])
        self.assertIsNone(quality.listing(self.store, self.doc)["summary"]["average"])
        item = job["assessments"][0]
        payload = {"revision": 1, "failures": ["warmth"], "reason": "Texto foi interpretado fora do contexto."}
        quality.review(self.store, self.doc, item["id"], payload, "master-1")
        revised = quality.detail(self.store, self.doc, ident)["assessments"][0]
        self.assertEqual(revised["score"], 70)
        self.assertEqual(revised["original"]["score"], 40)
        self.assertEqual(len(revised["history"]), 1)
        self.assertEqual(quality.listing(self.store, self.doc)["summary"]["average"], 70)
        with self.assertRaises(Conflict): quality.review(self.store, self.doc, item["id"], payload, "master-1")
        with self.assertRaises(ValueError): quality.review(self.store, self.doc, item["id"], {**payload, "revision": 2, "failures": ["privacy"]}, "master-1")

    def test_daily_limit_only_applies_to_new_context(self):
        self.authorize(daily_limit=1);ident = self.request()
        self.assertEqual(self.request(), ident)
        self.message("new", "Nova dúvida", "in")
        with self.assertRaises(ValueError): self.request()

    def test_approved_manual_pilot_caps_new_requests_at_twenty(self):
        policy = self.authorize(daily_limit=20, auto_closed=False, selection_enabled=False)
        self.assertEqual(policy["daily_limit"], 20)
        self.assertEqual(policy["max_interactions"], 0)
        jobs = []
        for index in range(20):
            self.message(f"pilot-{index}", f"Resposta sintetica de exemplo {index}.",
                         actor="11", stamp=self.now-10+index/100)
            jobs.append(self.request())
        self.assertEqual(len(set(jobs)), 20)
        self.assertEqual(self.request(), jobs[-1])
        thread = self.store.conversation(self.doc, self.channel, self.contact)
        self.store.close_conversation({"doctor_id": self.doc, "channel_id": self.channel,
            "contact_id": self.contact, "cursor": thread["inbound_cursor"]}, "11")
        self.message("pilot-over-limit", "Outra resposta sintetica.", actor="11")
        with self.assertRaisesRegex(ValueError, "Limite diário"):
            self.request()
        with self.store.db() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM quality_jobs").fetchone()[0], 20)
            self.assertEqual({r[0] for r in c.execute("SELECT source FROM quality_jobs")}, {"manual"})
            self.assertEqual({r[0] for r in c.execute("SELECT status FROM quality_jobs")}, {"queued"})
            self.assertEqual(c.execute("SELECT count(*) FROM quality_assessments").fetchone()[0], 0)

    def test_closing_repeatedly_after_manual_authorization_never_creates_jobs(self):
        self.authorize()
        thread = self.store.conversation(self.doc, self.channel, self.contact)
        payload = {"doctor_id": self.doc, "channel_id": self.channel,
                   "contact_id": self.contact, "cursor": thread["inbound_cursor"]}
        self.store.close_conversation(payload, "11")
        self.store.close_conversation(payload, "11")
        provider = Mock()
        self.assertFalse(quality.tick(self.store, provider, lambda _: True))
        provider.assert_not_called()

    def test_revocation_and_owner_changes_cancel_before_provider_and_after_response(self):
        self.authorize();ident = self.request()
        provider = Mock()
        quality.tick(self.store, provider, lambda _: False)
        provider.assert_not_called()
        self.assertEqual(quality.detail(self.store, self.doc, ident)["status"], "cancelled")
        self.message("new", "Outro texto", actor="11")
        ident = self.request()
        def changed(messages):
            with self.store.db() as c: c.execute("UPDATE channels SET doctor_id=? WHERE id=?", (self.other, self.channel))
            return self.raw(messages)
        quality.tick(self.store, changed, lambda _: True)
        with self.store.db() as c:
            self.assertEqual(c.execute("SELECT status FROM quality_jobs WHERE id=?", (ident,)).fetchone()[0], "cancelled")
            self.assertEqual(c.execute("SELECT count(*) FROM quality_assessments WHERE job_id=?", (ident,)).fetchone()[0], 0)

    def test_manual_release_does_not_install_other_evolution_modules(self):
        with self.store.db() as c:
            tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertNotIn("quality_selections", tables)
        self.assertNotIn("followup_cycles", tables)
        self.assertNotIn("lead_closure_events", tables)

    def test_stale_close_does_not_grade_new_unanswered_inbound(self):
        self.authorize()
        old=self.store.conversation(self.doc,self.channel,self.contact)["inbound_cursor"]
        self.message("new-inbound","Ainda preciso de ajuda.","in")
        result=self.store.close_conversation({"doctor_id":self.doc,"channel_id":self.channel,"contact_id":self.contact,"cursor":old},"11")
        self.assertEqual(result["resolution"],"open")
        with self.store.db() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM quality_jobs").fetchone()[0],0)

    def test_aged_running_requests_fail_without_retry_on_restart(self):
        self.authorize();ident = self.request()
        with self.store.db() as c: c.execute("UPDATE quality_jobs SET status='running',claimed_at=? WHERE id=?", (time.time()-1000, ident))
        provider=Mock();quality.tick(self.store, provider, lambda _: True)
        provider.assert_not_called()
        self.assertEqual(quality.detail(self.store, self.doc, ident)["status"], "failed")

    def test_wrong_doctor_and_restricted_scope_cannot_read_reviews(self):
        self.authorize();ident = self.request()
        with self.assertRaises(ValueError): quality.detail(self.store, self.other, ident)
        with crm_access({self.other: None}):
            with self.assertRaises(PermissionError): quality.detail(self.store, self.doc, ident)

    def test_redacts_phone_and_email_in_transmitted_text_not_metadata(self):
        self.message("private", "Contato +55 (11) 90000-0000 e exemplo@example.test", "in")
        self.authorize();job, provider = self.run_job(self.request())
        content = provider.call_args.args[0][1]["content"]
        self.assertNotIn("exemplo@example.test", content)
        self.assertNotIn("90000", content)
        self.assertIn("omitido", content)

    def test_api_denies_staff_cross_clinic_and_cross_origin(self):
        def handler(user, payload=None, method="GET", origin=True):
            raw=json.dumps(payload or {}).encode()
            return SimpleNamespace(command=method,current_user=user,headers={"Content-Length":str(len(raw))},rfile=io.BytesIO(raw),
                server=SimpleNamespace(auth_store=Mock()),connection=Mock(),same_origin_request=lambda:origin,auth_json=lambda d,s=200:(s,d))
        path=urlparse("/api/crm/quality?clinic=vielle&doctor="+self.doc)
        with patch("crm_api.store_for",return_value=self.store):
            staff={"id":11,"is_master":False,"crm_scope":None}
            self.assertEqual(handle_request(handler(staff),path)[0],403)
            master={"id":1,"is_master":True}
            self.assertEqual(handle_request(handler(master),urlparse("/api/crm/quality?clinic=other&doctor="+self.doc))[0],403)
            self.assertEqual(handle_request(handler(master),path)[0],200)
            self.assertEqual(handle_request(handler(master,method="POST",origin=False),urlparse("/api/crm/quality/analyze?clinic=vielle&doctor="+self.doc))[0],403)
            self.assertEqual(handle_request(handler(master,{"doctor_id":self.other,"confirm":True},"POST"),urlparse("/api/crm/quality/analyze?clinic=vielle&doctor="+self.doc))[0],400)
            self.authorize()
            with patch("crm_quality_api.provider_ready", return_value=False):
                response=handle_request(handler(master,{"doctor_id":self.doc,"channel_id":self.channel,
                    "contact_id":self.contact,"confirm":True},"POST"),
                    urlparse("/api/crm/quality/analyze?clinic=vielle&doctor="+self.doc))
                self.assertEqual(response[0],400)
                self.assertIn("Configure a API",response[1]["error"])
            with self.store.db() as c:
                self.assertEqual(c.execute("SELECT count(*) FROM quality_jobs").fetchone()[0],0)


if __name__ == "__main__":
    unittest.main()
