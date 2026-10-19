from datetime import datetime, timedelta
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from crm_appointments import PatientAppointments, TZ
from crm_patients import PatientDirectory
from crm_store import CrmStore
from crm_filters import parse_filters


class AppointmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/"experts.sqlite3"
        with sqlite3.connect(self.path) as c:
            c.execute("CREATE TABLE clinica_patients(uuid TEXT PRIMARY KEY,name TEXT,phone TEXT,raw_json TEXT,synced_at INTEGER)")
            c.execute("INSERT INTO clinica_patients VALUES('patient','Paciente','5500000000001','{}',1)")
            c.execute("CREATE TABLE clinica_bookings(uuid TEXT PRIMARY KEY,patient_uuid TEXT,professional_uuid TEXT,procedure_uuid TEXT,status TEXT,starts_at TEXT,ends_at TEXT,raw_json TEXT,synced_at INTEGER)")
        self.current=datetime(2026,10,18,12,tzinfo=TZ)
        self.reader=PatientAppointments(self.path,{"Doutor A":"doc-a"},lambda:self.current)
        directory=PatientDirectory(self.path)
        self.store=CrmStore(Path(self.tmp.name)/"crm",directory.lookup,appointment_source=self.reader)
        self.store.bootstrap({"Doutor A":["Funil","Segundo"],"Doutor B":["Funil"]})
        self.doctor=next(d["id"] for d in self.store.listing()["doctors"] if d["name"]=="Doutor A")
        p=self.store.listing(self.doctor)["pipelines"][0]
        self.lead=self.store.lead({"doctor_id":self.doctor,"pipeline_id":p["id"],"stage_id":p["stages"][0]["id"],"name":"Paciente","phone":"5500000000001","title":"Avaliação"},"Equipe")

    def add(self,uid,start="2026-10-20T14:00:00",status="confirmed",patient="patient",owner="doc-a",end=None,raw_patient=None,**changes):
        raw={"patient":raw_patient or {"uuid":patient},"professional":{"uuid":owner,"name":"Doutor A" if owner=="doc-a" else "Doutor B"},"procedure":{"name":"Avaliação facial"},**changes}
        with sqlite3.connect(self.path) as c:
            c.execute("INSERT INTO clinica_bookings VALUES(?,?,?,?,?,?,?,?,?)",(uid,patient,owner,"procedure",status,start,end,json.dumps(raw),1234))

    def summary(self):
        return self.store.listing(self.doctor)["leads"][0]["appointment"]

    def filtered(self, **filters):
        return self.store.listing(self.doctor, filters)

    def test_period_filter_uses_all_history_not_only_next_or_last(self):
        self.add("september",start="2026-09-12T14:00:00",status="completed")
        self.add("recent",start="2026-10-15T10:00:00",status="completed")
        self.add("next")
        before=self.path.read_bytes()
        data=self.filtered(appointment_status="completed",appointment_from="2026-09-01",appointment_to="2026-09-30")
        self.assertEqual(len(data["leads"]),1)
        self.assertEqual(data["leads"][0]["appointment_filter"]["item"]["uuid"],"september")
        self.assertEqual(data["leads"][0]["appointment"]["item"]["uuid"],"next")
        self.assertEqual(self.path.read_bytes(),before)

    def test_completed_is_not_inferred_from_past_confirmed_or_no_show(self):
        self.add("missed",start="2026-09-12T14:00:00",status="no_show")
        self.add("unresolved",start="2026-09-13T10:00:00",status="confirmed")
        filters={"appointment_from":"2026-09-01","appointment_to":"2026-09-30"}
        self.assertEqual(self.filtered(appointment_status="completed",**filters)["leads"],[])
        self.assertEqual(self.filtered(appointment_status="missed",**filters)["leads"][0]["appointment_filter"]["count"],1)
        self.assertEqual(self.filtered(appointment_status="any",**filters)["leads"][0]["appointment_filter"]["count"],2)
        self.add("attended",start="2026-09-14T10:00:00",status="attended")
        self.assertEqual(self.filtered(appointment_status="completed",**filters)["leads"][0]["appointment_filter"]["item"]["uuid"],"attended")

    def test_return_filter_checks_future_agenda_outside_the_selected_period(self):
        self.add("done",start="2026-09-12T14:00:00",status="completed")
        self.add("future",start="2026-11-20T10:00:00")
        filters={"appointment_status":"completed","appointment_from":"2026-09-01","appointment_to":"2026-09-30","appointment_no_upcoming":True}
        self.assertEqual(self.filtered(**filters)["leads"],[])
        with sqlite3.connect(self.path) as c:c.execute("UPDATE clinica_bookings SET status='cancelled' WHERE uuid='future'")
        self.add("other-doctor",owner="doc-b")
        self.assertEqual(len(self.filtered(**filters)["leads"]),1)

    def test_inclusive_days_sp_timezone_and_completed_today_without_time(self):
        self.add("first",start="2026-09-01T03:00:00Z",status="completed")
        self.add("last",start="2026-10-01T02:59:59Z",status="completed")
        self.add("outside",start="2026-10-01T03:00:00Z",status="completed")
        self.add("date-only",start="2026-09-15",status="completed")
        data=self.filtered(appointment_status="completed",appointment_from="2026-09-01",appointment_to="2026-09-30")["leads"][0]["appointment_filter"]
        self.assertEqual(data["count"],3)
        self.assertEqual(data["item"]["uuid"],"last")
        self.assertEqual(data["item"]["date"],"2026-09-30")
        self.add("today",start="2026-10-18",status="completed")
        value=self.filtered(appointment_status="completed",appointment_from="2026-10-18",appointment_to="2026-10-18")["leads"][0]["appointment_filter"]
        self.assertEqual(value["item"]["uuid"],"today")
        self.assertIsNone(value["item"]["time"])
        self.assertEqual(self.filtered(appointment_status="scheduled")["leads"],[])

    def test_scheduled_none_and_unknown_are_not_confused(self):
        self.assertEqual(self.filtered(appointment_status="none")["leads"],[])
        self.assertEqual(self.filtered(appointment_status="none")["filter_incomplete"],1)
        self.add("cancelled",status="cancelled")
        self.assertEqual(len(self.filtered(appointment_status="none")["leads"]),1)
        self.add("confirmed")
        self.assertEqual(self.filtered(appointment_status="none")["leads"],[])
        value=self.filtered(appointment_status="scheduled",appointment_from="2026-10-20",appointment_to="2026-10-20")["leads"][0]["appointment_filter"]
        self.assertEqual(value["count"],1)
        self.assertEqual(value["item"]["uuid"],"confirmed")

    def test_date_validation_and_conflicting_attendance_are_safe(self):
        for filters in ({"appointment_from":"2026-02-30"},{"appointment_to":"2026-1-1"},
                        {"appointment_from":"2026-10-20","appointment_to":"2026-10-19"},
                        {"appointment_no_upcoming":"true"},{"appointment_status":"unknown"}):
            with self.assertRaises(ValueError):parse_filters(filters)
        self.add("conflict",start="2026-09-15T10:00:00",status="completed")
        with sqlite3.connect(self.path) as c:
            raw=json.loads(c.execute("SELECT raw_json FROM clinica_bookings WHERE uuid='conflict'").fetchone()[0]);raw["status"]="no_show"
            c.execute("UPDATE clinica_bookings SET raw_json=? WHERE uuid='conflict'",(json.dumps(raw),))
        self.assertEqual(self.filtered(appointment_status="completed")["leads"],[])
        self.assertEqual(self.filtered(appointment_status="none")["leads"],[])

    def test_segment_batch_deduplicates_and_inbox_keeps_unread(self):
        self.add("done",start="2026-09-15T10:00:00",status="completed")
        pipeline=self.store.listing(self.doctor)["pipelines"][1]
        self.store.lead({"doctor_id":self.doctor,"pipeline_id":pipeline["id"],"stage_id":pipeline["stages"][0]["id"],"name":"Paciente","phone":"5500000000001","title":"Retorno"},"Equipe")
        filters={"appointment_status":"completed","appointment_from":"2026-09-01","appointment_to":"2026-09-30"}
        with patch.object(self.reader,"load",wraps=self.reader.load) as load:
            self.assertEqual(len(self.filtered(**filters)["leads"]),2)
        load.assert_called_once_with(["patient"],"Doutor A")
        channel=self.store.channel({"doctor_id":self.doctor,"name":"Comercial","phone":"5500000000099"})
        with self.store.db() as c:instance=self.store.row(c,"channels",channel)["instance"]
        self.store.receive(instance,{"key":{"id":"agenda","remoteJid":"5500000000001@s.whatsapp.net"},"message":{"conversation":"Quero retornar"}},"messages.upsert")
        data=self.store.inbox(self.doctor,"user",filters=filters)
        self.assertEqual(data["total"],1);self.assertEqual(data["counts"]["unread"],1)
        self.assertEqual(self.store.inbox(self.doctor,"other")["counts"]["unread"],1)

    def test_card_next_nearest_then_last_after_time_passes(self):
        self.add("later",start="2026-10-25T10:00:00");self.add("next");self.add("past",start="2026-10-15T10:00:00",status="completed")
        before=self.path.read_bytes();value=self.summary()
        self.assertEqual(value["kind"],"next");self.assertEqual(value["item"]["uuid"],"next")
        self.assertEqual(value["item"]["time"],"14:00")
        self.current=datetime(2026,11,1,12,tzinfo=TZ)
        value=self.summary();self.assertEqual(value["kind"],"last");self.assertEqual(value["item"]["uuid"],"later")
        self.assertEqual(value["item"]["status_label"],"Confirmado")
        self.assertEqual(before,self.path.read_bytes())

    def test_cancelled_not_suggested_deleted_not_shown_history_retains_cancelled(self):
        self.add("cancelled",status="cancelled");self.add("deleted",deleted_at="2026-10-18")
        self.add("past",start="2026-10-15T10:00:00",status="no_show")
        value=self.summary();self.assertEqual(value["kind"],"last");self.assertEqual(value["item"]["status_label"],"Não compareceu")
        history=self.store.appointments(self.lead,self.doctor)
        self.assertEqual({i["uuid"] for i in history["items"]},{"cancelled","past"})
        self.assertEqual(self.store.appointments(self.lead,self.doctor,period="upcoming")["total"],0)

    def test_ongoing_with_end_and_timezone_conversion(self):
        self.add("current",start="2026-10-18T14:30:00Z",end="2026-10-18T15:30:00Z")
        value=self.summary();self.assertEqual(value["kind"],"ongoing");self.assertEqual(value["item"]["time"],"11:30")
        self.add("later",start="2026-10-21T10:00:00")
        self.assertEqual(self.store.appointments(self.lead,self.doctor,period="upcoming")["items"][0]["uuid"],"current")
        self.current=datetime(2026,10,18,12,31,tzinfo=TZ)
        self.assertEqual(self.summary()["item"]["uuid"],"later")
        self.current=datetime(2026,10,22,12,tzinfo=TZ)
        self.assertEqual(self.summary()["kind"],"last")

    def test_date_only_does_not_invent_midnight_appointment_hour(self):
        self.add("today",start="2026-10-18")
        value=self.summary();self.assertEqual(value["kind"],"next");self.assertIsNone(value["item"]["time"])
        self.current+=timedelta(days=1)
        self.assertEqual(self.summary()["kind"],"last")

    def test_exact_patient_uuid_and_professional_scope(self):
        self.add("other-patient",patient="someone-else");self.add("other-doctor",owner="doc-b")
        self.add("own",start="2026-10-21T11:00:00")
        self.assertEqual(self.summary()["item"]["uuid"],"own")
        self.assertEqual(self.store.appointments(self.lead,self.doctor,scope="clinic")["total"],2)
        other=next(d["id"] for d in self.store.listing()["doctors"] if d["name"]=="Doutor B")
        with self.assertRaises(ValueError):self.store.appointments(self.lead,other)

    def test_conflicting_identity_owner_and_invalid_date_not_guessed(self):
        self.add("bad-patient",raw_patient={"uuid":"different"})
        self.add("bad-owner",professional={"uuid":"doc-b","name":"Doutor A"})
        self.add("bad-time",start="invalid")
        self.add("finished-future",status="completed")
        value=self.summary();self.assertIsNone(value["item"])
        self.assertEqual(value["warnings"],{"identity":1,"professional":1,"date":2})

    def test_missing_unsynced_and_unlinked_are_not_empty_confirmed_agenda(self):
        self.assertEqual(self.summary()["state"],"unavailable")
        missing=PatientAppointments(Path(self.tmp.name)/"absent.sqlite3")
        self.assertEqual(missing.summaries(["patient"],"Doutor A")["patient"]["state"],"unavailable")
        self.assertFalse(missing.path.exists())
        self.add("one")
        with sqlite3.connect(self.path) as c:c.execute("UPDATE clinica_patients SET raw_json=? WHERE uuid='patient'",(json.dumps({"deleted":True}),))
        self.assertEqual(self.reader.summaries(["patient"],"Doutor A")["patient"]["state"],"identity_pending")

    def test_batch_shared_patient_no_duplicate_lookups_and_pagination(self):
        for i in range(26):self.add(f"a-{i}",start=(self.current+timedelta(days=i+1)).isoformat())
        p=self.store.listing(self.doctor)["pipelines"][1]
        self.store.lead({"doctor_id":self.doctor,"pipeline_id":p["id"],"stage_id":p["stages"][0]["id"],"name":"Mesmo paciente","phone":"5500000000001","title":"Outro interesse"},"Equipe")
        with patch.object(self.reader,"summaries",wraps=self.reader.summaries) as lookup:
            self.store.listing(self.doctor)
        lookup.assert_called_once_with(["patient"],"Doutor A")
        first=self.store.appointments(self.lead,self.doctor);second=self.store.appointments(self.lead,self.doctor,page=2)
        self.assertEqual(first["total"],26);self.assertEqual(len(first["items"]),25);self.assertEqual(len(second["items"]),1)
        for changes in ({"scope":"other"},{"period":"other"},{"page":0}):
            with self.assertRaises(ValueError):self.store.appointments(self.lead,self.doctor,**changes)
