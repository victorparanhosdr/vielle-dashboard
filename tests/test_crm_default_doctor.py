from pathlib import Path
import tempfile
import unittest

from crm_store import CrmStore


class DefaultDoctorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = CrmStore(Path(self.tmp.name))

    def test_opening_crm_prefers_victor_without_reordering_doctors(self):
        self.store.bootstrap({"Marcela": ["Clinica"], "Victor Di Lena": ["Clinica"],
                              "Victor Paranhos de Andrade": ["Organico Paranhos"]})
        result = self.store.listing(kanban_pipeline="")
        victor = next(d for d in result["doctors"] if d["name"] == "Victor Paranhos de Andrade")
        self.assertEqual(result["doctor_id"], victor["id"])
        self.assertEqual(result["doctors"][0]["name"], "Marcela")
        self.assertTrue(result["pipelines"])
        self.assertTrue(all(p["doctor_id"] == victor["id"] for p in result["pipelines"]))
        self.assertEqual(result["kanban"]["pipeline_id"], result["pipelines"][0]["id"])

    def test_explicit_professional_selection_is_preserved(self):
        self.store.bootstrap({"Marcela": ["Clinica"], "Victor Paranhos de Andrade": ["Organico"]})
        marcela = next(d for d in self.store.listing()["doctors"] if d["name"] == "Marcela")
        result = self.store.listing(marcela["id"])
        self.assertEqual(result["doctor_id"], marcela["id"])
        self.assertTrue(all(p["doctor_id"] == marcela["id"] for p in result["pipelines"]))

    def test_default_matching_ignores_case_and_extra_spaces(self):
        self.store.bootstrap({"Marcela": ["Clinica"], "VICTOR  PARANHOS DE ANDRADE": ["Organico"]})
        result = self.store.listing()
        selected = next(d for d in result["doctors"] if d["id"] == result["doctor_id"])
        self.assertEqual(selected["name"], "VICTOR  PARANHOS DE ANDRADE")

    def test_missing_victor_keeps_existing_alphabetical_fallback(self):
        self.store.bootstrap({"Marcela": ["Clinica"], "Ana": ["Clinica"]})
        result = self.store.listing()
        self.assertEqual(result["doctor_id"], result["doctors"][0]["id"])
        self.assertEqual(result["doctors"][0]["name"], "Ana")

    def test_empty_store_still_opens_setup(self):
        result = self.store.listing()
        self.assertEqual(result["doctor_id"], "")
        self.assertEqual(result["doctors"], [])
        self.assertEqual(result["pipelines"], [])
