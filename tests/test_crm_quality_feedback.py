"""Only synthetic quality records; providers and external calls are never used."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import io
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlparse

import crm_quality as quality
import crm_quality_feedback as feedback
from crm_access import crm_access
from crm_api import handle_request
from crm_appointments import TZ
from crm_store import Conflict
import test_crm_quality as base


class QualityFeedbackTests(unittest.TestCase):
    setUp = base.QualityTests.setUp
    message = base.QualityTests.message
    authorize = base.QualityTests.authorize
    request = base.QualityTests.request
    raw = base.QualityTests.raw
    run_job = base.QualityTests.run_job

    def prepare(self, reviewed=True):
        self.authorize()
        self.job = self.request()
        job, _ = self.run_job(self.job, ["clarity", "warmth"])
        self.a = job["assessments"][0]["id"]
        if reviewed:
            quality.review(self.store, self.doc, self.a, {"revision": 1, "failures": ["clarity"], "reason": "Revisão de exemplo"}, "master-1")
        return self.current()

    def current(self):
        return quality.detail(self.store, self.doc, self.job)["assessments"][0]

    def respond(self, action="contest", **extra):
        a = self.current()
        return feedback.respond(self.store, self.doc, self.a, {"revision": a["revision"],
            "response_revision": a["response"]["revision"], "action": action, "reason": "Contexto de exemplo para reanálise", **extra}, "11")

    def resolve(self, **extra):
        a = self.current()
        quality.review(self.store, self.doc, self.a, {"revision": a["revision"], "failures": [],
            "reason": "Registro sintético de revisão", "contest_decision": "accept", "contest_note": "Ressalva acolhida no exemplo",
            "response_revision": a["response"]["revision"], **extra}, "master-1")

    def test_provisional_and_other_users_are_not_exposed(self):
        self.prepare(False)
        self.assertEqual(feedback.listing(self.store, self.doc, "11")["total"], 0)
        with self.assertRaises(LookupError): feedback.detail(self.store, self.doc, self.a, "11")
        with self.assertRaises(LookupError): self.respond()
        self.resolve(contest_decision="", contest_note="", failures=["clarity"])
        self.assertEqual(feedback.listing(self.store, self.doc, "11")["total"], 1)
        self.assertEqual(feedback.listing(self.store, self.doc, "another-user")["total"], 0)
        with self.assertRaises(LookupError): feedback.detail(self.store, self.doc, self.a, "another-user")

    def test_accept_is_idempotent_and_disallows_contesting_same_grade(self):
        self.prepare()
        self.assertEqual(self.respond("accept")["status"], "accepted")
        self.assertEqual(self.respond("accept", response_revision=0)["status"], "accepted")
        with self.assertRaises(Conflict): self.respond()
        with self.store.db() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM quality_response_events").fetchone()[0], 1)

    def test_one_contest_then_leader_revision_then_final_ack_no_second_chance(self):
        self.prepare()
        original = self.current()["original"]
        r = self.respond()
        self.assertEqual(r["status"], "contested")
        for action in ("contest", "accept"):
            with self.assertRaises(Conflict): self.respond(action)
        self.assertEqual(self.current()["score"], 70)
        self.resolve()
        a = self.current()
        self.assertEqual(a["score"], 100)
        self.assertEqual(a["response"]["status"], "resolved")
        self.assertEqual(a["original"], original)
        self.assertEqual(len(a["history"]), 2)
        with self.assertRaises(Conflict): self.respond()
        self.respond("accept")
        self.assertEqual(self.current()["response"]["status"], "accepted")
        self.resolve(contest_decision="", contest_note="", failures=["warmth"])
        self.assertEqual(self.current()["response"]["status"], "resolved")
        with self.assertRaises(Conflict): self.respond()
        data = feedback.detail(self.store, self.doc, self.a, "11")
        self.assertEqual([r["kind"] for r in data["events"]], ["contested", "resolved", "accepted"])
        self.assertEqual(data["events"][1]["previous_score"], 70)

    def test_rejection_keeps_grade_and_requires_reasoned_decision(self):
        self.prepare(); self.respond()
        before = self.current()
        for values in ({"contest_decision": ""}, {"contest_note": ""}, {"response_revision": 0},
                       {"contest_decision": "reject", "failures": []}):
            with self.assertRaises((ValueError, Conflict)):
                self.resolve(**values)
            self.assertEqual(self.current()["revision"], before["revision"])
            self.assertEqual(self.current()["response"]["status"], "contested")
        self.resolve(contest_decision="reject", failures=["clarity"])
        self.assertEqual(self.current()["score"], 70)
        self.assertEqual(self.current()["response"]["decision"], "reject")

    def test_stale_grade_and_response_revision_cannot_overwrite(self):
        self.prepare()
        with self.assertRaises(Conflict): self.respond(revision=1)
        with self.assertRaises(Conflict): self.respond(response_revision=5)
        self.respond()
        with self.assertRaises(Conflict): self.resolve(response_revision=0)
        self.assertEqual(self.current()["response"]["status"], "contested")

    def test_reason_validation_rolls_back_without_consuming_chance(self):
        self.prepare()
        for reason in ("", " ", None, "x"*2001):
            with self.assertRaises(ValueError): self.respond(reason=reason)
        self.assertFalse(self.current()["response"]["contest_used"])
        self.assertEqual(self.current()["response"]["revision"], 0)
        self.respond()

    def test_concurrent_contests_spend_only_one_chance(self):
        self.prepare()
        payload={"revision":2,"response_revision":0,"action":"contest","reason":"Exemplo concorrente"}
        def attempt(_):
            try:
                feedback.respond(self.store,self.doc,self.a,payload,"11")
                return "ok"
            except Conflict:
                return "conflict"
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertCountEqual(list(pool.map(attempt,range(2))),["ok","conflict"])
        with self.store.db() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM quality_response_events WHERE kind='contested'").fetchone()[0],1)

    def test_regrading_accepted_review_requires_new_ack_but_does_not_reset_used_contest(self):
        self.prepare(); self.respond("accept")
        received = self.current()["published_at"]
        self.resolve(contest_decision="", contest_note="")
        self.assertEqual(self.current()["response"]["status"], "awaiting_ack")
        self.assertEqual(self.current()["published_at"], received)
        self.respond()
        self.assertEqual(self.current()["response"]["status"], "contested")

    def test_scope_rechecked_for_list_detail_and_writes(self):
        self.prepare()
        with crm_access({self.other: None}, self.other):
            with self.assertRaises(PermissionError): feedback.listing(self.store, self.doc, "11")
            with self.assertRaises(PermissionError): feedback.detail(self.store, self.doc, self.a, "11")
        with crm_access({self.doc: []}, self.doc):
            self.assertEqual(feedback.listing(self.store, self.doc, "11")["total"], 0)
            with self.assertRaises(PermissionError): feedback.detail(self.store, self.doc, self.a, "11")
            with self.assertRaises(PermissionError): self.respond()
        with self.assertRaises(LookupError): feedback.detail(self.store, self.other, self.a, "11")

    def test_channel_transfer_revokes_old_scope_without_erasing_history(self):
        self.prepare()
        with self.store.db() as c: c.execute("UPDATE channels SET doctor_id=? WHERE id=?", (self.other, self.channel))
        self.assertEqual(feedback.listing(self.store, self.doc, "11")["total"], 0)
        with self.assertRaises(ValueError): feedback.detail(self.store, self.doc, self.a, "11")
        with self.store.db() as c: self.assertEqual(c.execute("SELECT count(*) FROM quality_reviews").fetchone()[0], 1)

    def test_month_uses_first_publication_sao_paulo_not_job_date_or_last_review(self):
        self.prepare()
        month, start, end = feedback.month_bounds("2026-10")
        self.assertEqual(datetime.fromtimestamp(start, TZ).hour, 0)
        with self.store.db() as c:
            c.execute("UPDATE quality_jobs SET created_at=? WHERE id=?", (start-40*86400, self.job))
            c.execute("UPDATE quality_assessments SET published_at=? WHERE id=?", (start, self.a))
        self.assertEqual(feedback.listing(self.store, self.doc, "11", month=month)["total"], 1)
        self.resolve(contest_decision="", contest_note="")
        self.assertEqual(feedback.listing(self.store, self.doc, "11", month=month)["total"], 1)
        with self.store.db() as c: c.execute("UPDATE quality_assessments SET published_at=? WHERE id=?", (end, self.a))
        self.assertEqual(feedback.listing(self.store, self.doc, "11", month=month)["total"], 0)

    def test_old_review_uses_audit_date_and_migration_is_idempotent(self):
        self.prepare()
        _, start, _ = feedback.month_bounds("2026-09")
        with self.store.db() as c:
            c.execute("UPDATE quality_assessments SET published_at=0 WHERE id=?", (self.a,))
            c.execute("UPDATE quality_reviews SET created_at=? WHERE assessment_id=?", (start+600, self.a))
        self.assertEqual(feedback.listing(self.store, self.doc, "11", month="2026-09")["total"], 1)
        self.resolve(contest_decision="", contest_note="")
        with self.store.db() as c:
            feedback.schema(c); feedback.schema(c)
        self.assertEqual(feedback.detail(self.store, self.doc, self.a, "11")["received_at"], start+600)

    def test_average_applies_full_filtered_set_not_page_and_null_is_not_perfect(self):
        self.prepare()
        with self.store.db() as c:
            row = dict(c.execute("SELECT * FROM quality_assessments WHERE id=?", (self.a,)).fetchone())
            for index in range(30):
                cloned = {**row, "id": f"copy-{index}", "actor_id": "11", "job_id": self.job}
                # Separate synthetic jobs preserve the production actor/job uniqueness.
                job = dict(c.execute("SELECT * FROM quality_jobs WHERE id=?", (self.job,)).fetchone())
                job.update(id=f"job-{index}", fingerprint=f"fingerprint-{index}")
                c.execute(f"INSERT INTO quality_jobs({','.join(job)}) VALUES({','.join('?' for _ in job)})", tuple(job.values()))
                cloned.update(job_id=job["id"], score=None if index==0 else 100)
                c.execute(f"INSERT INTO quality_assessments({','.join(cloned)}) VALUES({','.join('?' for _ in cloned)})", tuple(cloned.values()))
        first = feedback.listing(self.store, self.doc, "11")
        second = feedback.listing(self.store, self.doc, "11", page=2)
        self.assertEqual(first["total"], 31)
        self.assertEqual(first["summary"]["average"], 99)
        self.assertEqual(first["summary"], second["summary"])
        self.respond()
        filtered = feedback.listing(self.store, self.doc, "11", status="contested")
        self.assertEqual(filtered["summary"]["average"], 70)
        self.assertEqual(filtered["total"], 1)

    def test_detail_contains_only_own_published_feedback_not_raw_context(self):
        self.prepare()
        data = feedback.detail(self.store, self.doc, self.a, "11")
        self.assertEqual(data["contact_name"], "Cliente Exemplo")
        for key in ("snapshot", "settings", "messages", "original", "actor_id", "actor_name", "job_id"):
            self.assertNotIn(key, data)
        self.assertNotIn("evidence", data["result"]["items"][0])

    def test_manager_pending_filter_includes_old_appeals_and_resolved_status(self):
        self.prepare(); self.respond()
        with self.store.db() as c: c.execute("UPDATE quality_jobs SET created_at=1 WHERE id=?", (self.job,))
        self.assertEqual(quality.listing(self.store, self.doc)["total"], 0)
        manager = quality.listing(self.store, self.doc, view="contested")
        self.assertEqual(manager["total"], 1)
        self.assertEqual(manager["jobs"][0]["assessments"][0]["response"]["status"], "contested")
        self.resolve()
        self.assertEqual(quality.listing(self.store, self.doc, view="contested")["total"], 0)

    def test_api_identity_is_session_not_request_and_manager_routes_remain_private(self):
        self.prepare()
        def call(action, user="11", payload=None, origin=True, scope=None, clinic="vielle"):
            raw = json.dumps(payload or {}).encode()
            auth = Mock(); auth.has_permission.return_value = True
            handler = SimpleNamespace(command="POST" if payload is not None else "GET",current_user={"id":user,"is_master":False,"crm_scope":scope},
                headers={"Content-Length":str(len(raw))},rfile=io.BytesIO(raw),server=SimpleNamespace(auth_store=auth),connection=Mock(),
                same_origin_request=lambda:origin,auth_json=lambda d,s=200:(s,d))
            with patch("crm_api.store_for",return_value=self.store):
                return handle_request(handler,urlparse(f"/api/crm/{action}?clinic={clinic}&doctor={self.doc}&id={self.a}&actor_id=11"))
        self.assertEqual(call("quality/self")[1]["total"], 1)
        self.assertEqual(call("quality/self", user="other")[1]["total"], 0)
        self.assertEqual(call("quality/self/item", user="other")[0], 404)
        for route in ("quality", "quality/item", "quality/review", "quality/settings"):
            self.assertEqual(call(route)[0], 403)
        payload={"doctor_id":self.doc,"id":self.a,"action":"contest","reason":"Exemplo","revision":2,"response_revision":0,"actor_id":"11"}
        self.assertEqual(call("quality/self/respond", payload=payload,origin=False)[0],403)
        self.assertEqual(call("quality/self/respond", user="other",payload=payload)[0],404)
        self.assertEqual(call("quality/self", clinic="other")[0],403)
        self.assertEqual(call("quality/self", scope={self.other:None})[0],403)
        self.assertEqual(call("quality/self/respond",payload=payload)[0],200)

    def test_bad_filters_and_empty_dataset_never_invent_grade(self):
        self.assertIsNone(feedback.listing(self.store,self.doc,"11")["summary"]["average"])
        for month in ("2026-00", "2026-13", "bad", "2026-1"):
            with self.assertRaises(ValueError): feedback.listing(self.store,self.doc,"11",month=month)
        with self.assertRaises(ValueError): feedback.listing(self.store,self.doc,"11",status="other")


if __name__ == "__main__":
    unittest.main()
