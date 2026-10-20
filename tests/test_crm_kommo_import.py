import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlparse

from crm_api import handle_request
from crm_kommo_import import commit, extract, prepare, schema, status
from crm_patients import PatientDirectory
from crm_store import Conflict, CrmStore


class KommoMigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.patient_path = Path(self.tmp.name) / "patients.sqlite3"
        with sqlite3.connect(self.patient_path) as c:
            c.execute("CREATE TABLE clinica_patients(uuid TEXT,name TEXT,phone TEXT,raw_json TEXT,synced_at INTEGER)")
            c.executemany("INSERT INTO clinica_patients VALUES(?,?,?,?,?)", [
                ("p1", "Nome correto Experts", "5533999991111", json.dumps({"cellphone": "5533999992222"}), 1),
                ("p2", "Outro paciente", "5531999993333", "{}", 1),
                ("p3", "Compartilhado 1", "5511999994444", "{}", 1),
                ("p4", "Compartilhado 2", "5511999994444", "{}", 1)])
        directory = PatientDirectory(self.patient_path)
        self.store = CrmStore(Path(self.tmp.name) / "crm", directory.lookup, patient_directory=directory)
        self.store.bootstrap({"Victor Paranhos de Andrade": ["Organico Paranhos", "Outro funil"], "Outro doutor": ["Organico Paranhos"]})
        data = self.store.listing()
        self.doctor = next(d["id"] for d in data["doctors"] if "Victor" in d["name"])
        self.pipe = next(p for p in self.store.listing(self.doctor)["pipelines"] if p["name"] == "Organico Paranhos")

    def record(self, sid=1, numbers=None, **extra):
        return {"source_id": sid, "source_contact": sid, "name": "Nome Kommo", "title": "Tratamento",
                "phones": numbers or ["5533999991111"], "stage": {"name": "Negociacao Kommo", "kind": "open"},
                "value_cents": 125000, "created_at": 1700000000, "updated_at": 1700000001, "reason": "", **extra}

    def preview(self, records):
        return prepare(self.store, self.doctor, self.pipe["id"], background=False,
                       source=lambda progress: {"source_pipeline": 99, "source_name": "Orgânico Paranhos", "records": records})["id"]

    def retry(self, previous, records):
        return prepare(self.store, self.doctor, self.pipe["id"], background=False, retry_from=previous,
                       source=lambda progress: {"source_pipeline": 99, "source_name": "Orgânico Paranhos", "records": records})["id"]

    def apply(self, key):
        return commit(self.store, self.doctor, self.pipe["id"], key, "Master")

    def test_preview_is_non_mutating_and_import_links_canonical_patient(self):
        key = self.preview([self.record()])
        self.assertEqual(self.store.listing(self.doctor)["leads"], [])
        self.assertEqual(status(self.store, self.doctor, self.pipe["id"], key)["report"]["linked"], 1)
        result = self.apply(key)
        self.assertEqual(result["status"], "done")
        lead = self.store.listing(self.doctor)["leads"][0]
        self.assertEqual((lead["name"], lead["experts_uuid"]), ("Nome correto Experts", "p1"))
        self.assertEqual(lead["value_cents"], 125000)
        self.assertEqual(lead["created_at"], 1700000000)
        self.assertEqual(lead["service_location"], "capelinha")
        with self.store.db() as c:
            for table in ("messages", "xp", "campaigns"):
                self.assertEqual(c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM kommo_import_refs").fetchone()[0], 1)
        self.assertTrue(list((self.store.path.parent / "backups").glob("*.sqlite3")))

    def test_duplicate_phone_patient_other_number_and_rerun_never_duplicate(self):
        key = self.preview([self.record(3, updated_at=1700000003), self.record(2, ["5533999992222"]), self.record(1)])
        result = self.apply(key)["report"]
        self.assertEqual((result["created"], result["duplicates"], result["linked"]), (1, 2, 1))
        self.assertEqual(self.apply(key)["report"], result)
        repeat = self.apply(self.preview([self.record(1), self.record(2), self.record(3)]))["report"]
        self.assertEqual((repeat["created"], repeat["already"]), (0, 3))
        with self.store.db() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM contacts").fetchone()[0], 1)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM leads").fetchone()[0], 1)

    def test_existing_opportunity_and_optout_manual_location_are_preserved(self):
        lid = self.store.lead({"doctor_id": self.doctor, "pipeline_id": self.pipe["id"], "stage_id": self.pipe["stages"][1]["id"],
                               "name": "Paciente", "phone": "5533999991111", "title": "Trabalho da equipe", "value_cents": 56700}, "Equipe")
        with self.store.db() as c:
            c.execute("UPDATE contacts SET opted_out=1,service_location='sao-paulo',service_location_source='manual'")
        self.assertEqual(self.apply(self.preview([self.record()]))["report"]["reused"], 1)
        lead = self.store.detail(lid, self.doctor)["lead"]
        self.assertEqual((lead["title"], lead["stage_id"], lead["value_cents"]), ("Trabalho da equipe", self.pipe["stages"][1]["id"], 56700))
        self.assertEqual((lead["opted_out"], lead["service_location"]), (1, "sao-paulo"))

    def test_shared_conflicting_deleted_missing_phones_are_pending(self):
        records = [self.record(1, ["5511999994444"]), self.record(2, ["5533999991111", "5531999993333"]),
                   self.record(3, reason="deleted"), self.record(4, phones=[], reason="phone")]
        result = self.apply(self.preview(records))
        self.assertEqual(result["report"]["issue_count"], 4)
        self.assertEqual(result["report"]["created"], 0)
        self.assertEqual(self.store.listing(self.doctor)["leads"], [])

    def test_exact_full_name_resolves_shared_phone_and_survives_reconciliation(self):
        with sqlite3.connect(self.patient_path) as c:
            c.execute("UPDATE clinica_patients SET name='Maria Conceicao Silva' WHERE uuid='p3'")
        record = self.record(numbers=["5511999994444"], name="  MARIA  Conceição Silva ")
        result = self.apply(self.preview([record]))["report"]
        self.assertEqual((result["created"], result["linked"], result["resolved"]), (1, 1, 1))
        lead = self.store.listing(self.doctor)["leads"][0]
        self.assertEqual((lead["experts_uuid"], lead["experts_status"]), ("p3", "matched"))
        with self.store.db() as c:
            contact = self.store.contact(c, "5511999994444", "Compartilhado 2")
            self.assertEqual((contact["experts_uuid"], contact["experts_status"]), ("p3", "matched"))
            c.execute("UPDATE contacts SET opted_out=1")
        self.store.patient_directory.invalidate()
        with sqlite3.connect(self.patient_path) as c:
            c.execute("UPDATE clinica_patients SET name='Maria Conceicao Silva' WHERE uuid='p4'")
        lead = self.store.listing(self.doctor)["leads"][0]
        self.assertEqual((lead["experts_uuid"], lead["experts_status"], lead["opted_out"]), ("p3", "review", 1))

    def test_shared_names_never_use_partial_name_nickname_or_choose_between_identical_names(self):
        with sqlite3.connect(self.patient_path) as c:
            c.execute("UPDATE clinica_patients SET name='Maria da Silva'")
        for name in ("Maria", "Maria Silva", "Maria da Silva", "Mãe do Victor"):
            result = self.apply(self.preview([self.record(numbers=["5511999994444"], name=name)]))["report"]
            self.assertEqual((result["created"], result["issue_count"]), (0, 1))

    def test_bulk_retry_imports_ambiguous_phone_without_clinical_link_and_is_idempotent(self):
        records = [self.record(numbers=["5511999994444"]), self.record(2, reason="phone")]
        previous = self.preview(records)
        self.apply(previous)
        fresh = {"source_pipeline": 99, "source_name": "Orgânico Paranhos", "records": records}
        with patch("crm_patient_refresh.sync_patients") as sync, patch("crm_kommo_import.extract", return_value=fresh) as extract_mock, \
                patch("app.get_access_context", return_value={"access_token": "test", "account_domain": "example"}):
            key = prepare(self.store, self.doctor, self.pipe["id"], background=False, retry_from=previous)["id"]
        sync.assert_called_once()
        extract_mock.assert_called_once()
        self.assertEqual(self.store.listing(self.doctor)["leads"], [])
        result = self.apply(key)
        self.assertEqual((result["report"]["created"], result["report"]["review"], result["report"]["issue_count"]), (1, 1, 1))
        self.assertTrue(any(item.get("imported") for item in result["issues"]))
        lead = self.store.listing(self.doctor)["leads"][0]
        self.assertEqual((lead["experts_uuid"], lead["experts_status"]), ("", "review"))
        self.assertEqual(self.apply(key)["report"], result["report"])
        repeat = self.retry(key, records)
        result = self.apply(repeat)["report"]
        self.assertEqual((result["created"], result["already"], result["issue_count"]), (0, 1, 1))
        with self.store.db() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM contacts").fetchone()[0], 1)
            for table in ("messages", "campaigns", "xp"):
                self.assertEqual(c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)

    def test_bulk_retry_does_not_merge_different_people_or_overwrite_existing_patient(self):
        records = [self.record(1, ["5511999994444"], name="Maria Silva"),
                   self.record(2, ["5511999994444"], name="Outra Pessoa")]
        previous = self.preview(records)
        self.apply(previous)
        key = self.retry(previous, records)
        result = self.apply(key)["report"]
        self.assertEqual((result["created"], result["review"], result["issue_count"]), (1, 1, 1))
        with self.store.db() as c:
            c.execute("UPDATE contacts SET experts_uuid='p2',opted_out=1")
        key = self.retry(previous, records)
        result = self.apply(key)["report"]
        self.assertEqual((result["created"], result["issue_count"]), (0, 1))
        with self.store.db() as c:
            self.assertEqual(tuple(c.execute("SELECT experts_uuid,opted_out FROM contacts").fetchone()), ("p2", 1))

    def test_retry_source_requires_a_completed_migration_owned_by_the_target(self):
        previous = self.preview([self.record()])
        with self.assertRaises(ValueError):
            prepare(self.store, self.doctor, self.pipe["id"], retry_from=previous)
        self.apply(previous)
        with self.store.db() as c:
            other = c.execute("SELECT id FROM doctors WHERE id<>?", (self.doctor,)).fetchone()[0]
            c.execute("UPDATE kommo_import_jobs SET doctor_id=? WHERE id=?", (other, previous))
        with self.assertRaises(ValueError):
            prepare(self.store, self.doctor, self.pipe["id"], retry_from=previous)

    def test_unmatched_new_contact_and_secondary_existing_phone(self):
        numbers = ["5538999991111", "5538999992222"]
        lid = self.store.lead({"doctor_id": self.doctor, "pipeline_id": self.pipe["id"], "stage_id": self.pipe["stages"][0]["id"],
                               "name": "Novo", "phone": numbers[1], "title": "Existente"}, "Equipe")
        result = self.apply(self.preview([self.record(numbers=numbers)]))["report"]
        self.assertEqual((result["new"], result["reused"]), (1, 1))
        with self.store.db() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM contacts").fetchone()[0], 1)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM contact_phones").fetchone()[0], 2)
        self.assertEqual(self.store.detail(lid, self.doctor)["lead"]["title"], "Existente")

    def test_same_patient_other_pipeline_reuses_contact_but_not_lead(self):
        other = next(p for p in self.store.listing(self.doctor)["pipelines"] if p["id"] != self.pipe["id"])
        self.store.lead({"doctor_id": self.doctor, "pipeline_id": other["id"], "stage_id": other["stages"][0]["id"],
                         "name": "Teste", "phone": "5533999991111", "title": "Outro"}, "Equipe")
        self.apply(self.preview([self.record(numbers=["5533999992222"])]))
        with self.store.db() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM contacts").fetchone()[0], 1)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM leads").fetchone()[0], 2)

    def test_live_recheck_conflict_and_rollback(self):
        key = self.preview([self.record(1), self.record(2, ["5531999993333"])])
        original = self.store.contact
        def changed(c, number, name):
            result = original(c, number, name)
            if number == "5531999993333":
                return {**result, "experts_status": "review"}
            return result
        with patch.object(self.store, "contact", side_effect=changed):
            with self.assertRaises(Conflict): self.apply(key)
        self.assertEqual(self.store.listing(self.doctor)["leads"], [])

    def test_scope_expiry_failure_and_report_pagination(self):
        with self.assertRaises(ValueError):
            prepare(self.store, self.doctor, next(p["id"] for p in self.store.listing(self.doctor)["pipelines"] if p["id"] != self.pipe["id"]))
        key = self.preview([self.record(i, reason="phone") for i in range(60)])
        self.assertEqual(len(status(self.store, self.doctor, self.pipe["id"], key, 2)["issues"]), 25)
        with self.store.db() as c: c.execute("UPDATE kommo_import_jobs SET created_at=0")
        with self.assertRaises(Conflict): self.apply(key)
        key = prepare(self.store, self.doctor, self.pipe["id"], background=False, source=Mock(side_effect=RuntimeError("private token")))["id"]
        result = status(self.store, self.doctor, self.pipe["id"], key)
        self.assertEqual(result["status"], "error")
        self.assertNotIn("private token", json.dumps(result))

    def test_http_master_clinic_csrf_and_action_dispatch(self):
        h = Mock(command="POST", current_user={"is_master": True, "id": "u", "nome": "Master"})
        h.same_origin_request.return_value = True
        body = json.dumps({"doctor_id": self.doctor, "pipeline_id": self.pipe["id"]}).encode()
        with patch("crm_api.store_for", return_value=self.store), patch("crm_api.read_body", return_value=body), patch("crm_kommo_import.prepare", return_value={"id": "job"}) as prepare_mock:
            h.current_user["is_master"] = False
            handle_request(h, urlparse("/api/crm/kommo-import-preview?clinic=vielle"))
            self.assertEqual(h.auth_json.call_args.args[1], 403)
            h.current_user["is_master"] = True
            h.same_origin_request.return_value = False
            handle_request(h, urlparse("/api/crm/kommo-import-preview?clinic=vielle"))
            self.assertEqual(h.auth_json.call_args.args[1], 403)
            prepare_mock.assert_not_called()
            h.same_origin_request.return_value = True
            handle_request(h, urlparse("/api/crm/kommo-import-preview?clinic=vielle"))
            prepare_mock.assert_called_once_with(self.store, self.doctor, self.pipe["id"])


class SourceTests(unittest.TestCase):
    def test_extract_only_selected_funnel_with_main_contacts_and_real_stages(self):
        calls = []
        def request(path):
            calls.append(path)
            if path == "/api/v4/leads/pipelines":
                return {"_embedded": {"pipelines": [{"id": 99, "name": "Orgânico Paranhos", "_embedded": {"statuses": [{"id": 142, "name": "Venda ganha"}]}}]}}
            if path.startswith("/api/v4/leads?"):
                self.assertEqual(parse_qs(urlparse(path).query)["filter[pipeline_id][]"], ["99"])
                return {"_embedded": {"leads": [{"id": 4, "pipeline_id": 99, "status_id": 142, "price": 1250.25,
                                                "_embedded": {"contacts": [{"id": 6, "is_main": True}, {"id": 7}]}}]}}
            self.assertEqual(parse_qs(urlparse(path).query)["filter[id][]"], ["6"])
            return {"_embedded": {"contacts": [{"id": 6, "name": "Nome", "custom_fields_values": [{"field_code": "PHONE", "values": [{"value": "(33) 99999-1111"}]}]}]}}
        result = extract(request)
        self.assertEqual(result["records"][0]["phones"], ["5533999991111"])
        self.assertEqual(result["records"][0]["stage"]["kind"], "won")
        self.assertEqual(result["records"][0]["value_cents"], 125025)
        self.assertTrue(all("/api/v4/" in path for path in calls))

    def test_malformed_or_wrong_scope_fails_closed(self):
        with self.assertRaises(ValueError): extract(lambda path: {"unexpected": []})
        pipeline = {"id": 99, "name": "Organico Paranhos", "_embedded": {"statuses": [{"id": 5, "name": "Entrada"}]}}
        with self.assertRaises(ValueError):
            extract(lambda path: {"_embedded": {"pipelines": [pipeline]}} if path.endswith("pipelines") else {"_embedded": {"leads": [{"id": 1, "pipeline_id": 98}]}})


if __name__ == "__main__":
    unittest.main()
