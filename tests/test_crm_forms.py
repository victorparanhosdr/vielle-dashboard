import copy
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlsplit

from crm_access import institute_access
from crm_forms import FormStore, definition, answers_for, document_number
from crm_forms_api import public_request, handle
from crm_form_integrations import authorization, callback, deliver_once, cipher
from crm_store import CrmStore, Conflict


class FormTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.crm=CrmStore(Path(self.tmp.name)/"crm",lambda number:{"status":"new"})
        self.crm.bootstrap({"Victor Paranhos de Andrade":["Organico Paranhos","REGENCODE"]})
        self.doc=self.crm.listing()["doctors"][0]["id"]
        self.pipes=self.crm.listing(self.doc)["pipelines"]
        self.pipeline=next(p for p in self.pipes if not p["course_key"])
        self.forms=FormStore(self.crm)
        self.info={"title":"Inscricao", "fields":[{"id":"name","type":"name","label":"Nome","required":True},
            {"id":"phone","type":"phone","label":"WhatsApp","required":True},{"id":"email","type":"email","label":"E-mail"}],
            "crm":{"enabled":True,"pipeline_id":self.pipeline["id"],"stage_id":self.pipeline["stages"][0]["id"],"name_field":"name","phone_field":"phone","email_field":"email"}}
        self.form=self.forms.save({"doctor_id":self.doc,"definition":self.info},1)

    def publish(self, info=None):
        if info:
            self.form=self.forms.save({"id":self.form["id"],"revision":self.form["revision"],"doctor_id":self.doc,"definition":info},1)
        self.forms.publish(self.form["id"],self.doc,self.form["revision"],"published")
        row,info=self.forms.public(self.form["slug"])
        return row,info,self.forms.token(row,info)

    def submit(self, token, **answers):
        return self.forms.submit(self.form["slug"],{"token":token,"answers":{"name":"Pessoa Exemplo","phone":"11999990000","email":"person@example.test",**answers}},"127.0.0.1")

    def test_draft_is_not_public_and_published_snapshot_does_not_change_on_save(self):
        with self.assertRaises(LookupError):self.forms.public(self.form["slug"])
        row,info,token=self.publish()
        changed=copy.deepcopy(self.info);changed["title"]="Outro titulo"
        self.forms.save({"id":row["id"],"revision":row["revision"],"doctor_id":self.doc,"definition":changed},1)
        self.assertEqual(self.forms.public(row["slug"])[1]["title"],"Inscricao")
        self.submit(token)
        self.assertEqual(self.forms.responses(row["id"],self.doc)["total"],1)

    def test_idempotent_submission_and_contact_reuse_do_not_move_existing_lead(self):
        _,_,token=self.publish();first=self.submit(token);again=self.submit(token)
        self.assertEqual(first["id"],again["id"])
        _,_,another=self.publish();self.submit(another,name="Outro nome informado")
        with self.crm.db() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM contacts").fetchone()[0],1)
            self.assertEqual(c.execute("SELECT count(*) FROM leads").fetchone()[0],1)
            self.assertEqual(c.execute("SELECT count(*) FROM form_responses").fetchone()[0],2)
        self.assertEqual([r["lead_state"] for r in self.forms.responses(self.form["id"],self.doc)["items"]],["reused","created"])

    def test_course_form_never_reads_clinical_records_and_uses_institute_permissions(self):
        pipeline=next(p for p in self.pipes if p["course_key"])
        info=copy.deepcopy(self.info);info["crm"].update(pipeline_id=pipeline["id"],stage_id=pipeline["stages"][0]["id"])
        _,_,token=self.publish(info)
        self.crm.patient_lookup=Mock(side_effect=AssertionError("No Experts for courses"))
        self.submit(token)
        self.crm.patient_lookup.assert_not_called()
        with institute_access([]):
            self.assertEqual(self.forms.listing(self.doc),[])
            with self.assertRaises(PermissionError):self.forms.detail(self.form["id"],self.doc)

    def test_tokens_are_form_bound_version_bound_signed_and_expire(self):
        row,info,token=self.publish()
        with self.assertRaises(ValueError):self.forms.verify_token(row,info,token+'0')
        with self.assertRaises(ValueError):self.forms.verify_token({**row,"id":"another"},info,token)
        with self.assertRaises(ValueError):self.forms.verify_token(row,{**info,"revision":99},token)
        with patch('crm_forms.time.time',return_value=999999999999):
            with self.assertRaises(ValueError):self.forms.verify_token(row,info,token)

    def test_missing_required_invalid_email_scheme_and_choice_rejected(self):
        info=definition(self.info)
        with self.assertRaises(ValueError):answers_for(info,{"name":"Person"})
        with self.assertRaises(ValueError):answers_for(info,{"name":"Person","phone":"11999990000","email":"not-email"})
        with self.assertRaises(ValueError):definition({**self.info,"finish":{"mode":"redirect","url":"javascript:alert(1)"}})
        with self.assertRaises(ValueError):definition({**self.info,"theme":{"background":"red;url(x)"}})
        with self.assertRaises(ValueError):definition({**self.info,"emails":["good@example.test\r\nBcc:bad@example.test"]})

    def test_documents_and_structured_field_types(self):
        self.assertEqual(document_number("529.982.247-25","cpf"),"52998224725")
        with self.assertRaises(ValueError):document_number("11111111111","cpf")
        fields=[{"id":kind,"type":kind,"label":kind,"required":True,"options":[{"label":"A"},{"label":"B"}],"multiple":kind=="choice","scale":10} for kind in ("choice","images","dropdown","scale","terms","address","date","number","money","cpf","cnpj","url","identity","long")]
        values={"choice":["A","B"],"images":"A","dropdown":"B","scale":0,"terms":True,"address":{"street":"Rua","city":"Cidade","state":"SP"},"date":"2026-10-20","number":"0","money":"10,50","cpf":"52998224725","cnpj":"11222333000181","url":"https://example.test","identity":"RG 123","long":"Texto"}
        result=answers_for(definition({"title":"Tipos","fields":fields}),values)
        self.assertEqual(result["money"],"10.50");self.assertEqual(result["scale"],0)

    def test_answer_attachments_are_bound_to_the_form_field_and_submission(self):
        info=copy.deepcopy(self.info);info["fields"].append({"id":"file","type":"file","label":"Anexo","required":True})
        row,definition,token=self.publish(info);nonce=self.forms.verify_token(row,definition,token)
        file=self.forms.upload(row["id"],"file","example.pdf",b"%PDF-1.4\n%%EOF",nonce=nonce,remote="127.0.0.1")
        with self.assertRaises(ValueError):self.submit(self.forms.token(row,definition),file=[file["id"]])
        self.submit(token,file=[file["id"]])
        self.assertEqual(self.forms.responses(row["id"],self.doc)["items"][0]["files"][0]["id"],file["id"])
        with self.assertRaises(ValueError):self.forms.upload(row["id"],"file","fake.pdf",b"<script>",nonce=nonce)

    def test_optout_is_preserved_and_never_queues_whatsapp(self):
        _,_,token=self.publish();self.submit(token)
        with self.crm.db() as c:c.execute("UPDATE contacts SET opted_out=1")
        _,_,token=self.publish();self.submit(token)
        with self.crm.db() as c:
            self.assertEqual(c.execute("SELECT opted_out FROM contacts").fetchone()[0],1)
            self.assertEqual(c.execute("SELECT count(*) FROM messages").fetchone()[0],0)

    def test_public_schema_has_no_notification_emails_integration_config_or_responses(self):
        self.publish()
        handler=SimpleNamespace(path='/api/public-forms/'+self.form["slug"],command='GET',server=SimpleNamespace(auth_store=object()),auth_json=Mock())
        with patch('crm_api.store_for',return_value=self.crm),patch('crm_forms_api.store_for',return_value=self.forms):
            self.assertTrue(public_request(handler))
        data=handler.auth_json.call_args.args[0]
        self.assertNotIn('crm',data['form']);self.assertNotIn('emails',data['form']);self.assertNotIn('google_token',json.dumps(data))

    def test_revision_conflict_and_notification_configuration_are_explicit(self):
        with self.assertRaises(Conflict):self.forms.save({"id":self.form["id"],"revision":999,"doctor_id":self.doc,"definition":self.info},1)
        info={**self.info,"emails":["team@example.test"]}
        with patch('crm_form_integrations.mail_ready',return_value=False):
            with self.assertRaises(ValueError):self.publish(info)

    def test_queued_notification_contains_no_answers_and_is_not_retried_after_uncertain_send(self):
        with patch('crm_form_integrations.mail_ready',return_value=True):row,_,token=self.publish({**self.info,"emails":["team@example.test","manager@example.test"]})
        self.submit(token)
        with patch('crm_form_integrations.mail_ready',return_value=True),patch('crm_form_integrations.email_delivery',side_effect=TimeoutError):self.assertTrue(deliver_once(self.forms))
        with self.crm.db() as c:
            jobs=[dict(r) for r in c.execute("SELECT * FROM form_deliveries")]
            self.assertEqual(jobs[0]['status'],'unknown');self.assertEqual(jobs[1]['status'],'queued')
            c.execute("UPDATE form_deliveries SET status='unknown'")
        self.assertFalse(deliver_once(self.forms))

    def test_google_oauth_state_is_user_bound_single_use_and_tokens_encrypted(self):
        settings={"CRM_GOOGLE_CLIENT_ID":"test-client","CRM_GOOGLE_CLIENT_SECRET":"test-secret"}
        from urllib.parse import parse_qs
        with patch.dict('os.environ',settings):
            url=authorization(self.forms,self.form["id"],self.doc,1);state=parse_qs(urlsplit(url).query)['state'][0]
            with self.assertRaises(ValueError):callback(self.forms,2,state,'code')
            token={"access_token":"private-access","refresh_token":"private-refresh","expires_in":3600,"scope":"https://www.googleapis.com/auth/drive.file"}
            with patch('crm_form_integrations.http_json',return_value=token):callback(self.forms,1,state,'code')
            with self.crm.db() as c:
                row=self.forms.owned(c,self.form["id"])
                self.assertNotIn('private-refresh',row['google_token'])
                self.assertIn('private-refresh',cipher(self.forms).decrypt(row['google_token'].encode()).decode())
            with self.assertRaises(ValueError):callback(self.forms,1,state,'code')
