import base64
from email import message_from_bytes,policy
import json
import time
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs,urlsplit

from tests import test_crm_forms as fixtures
from crm_form_integrations import authorization,callback,cipher,email_delivery,deliver_once,MAIL_SCOPE,DRIVE_SCOPE,access_token
from crm_form_accounts import prepare,attach,listing
from crm_access import institute_access


class GoogleFormTests(unittest.TestCase):
    setUp=fixtures.FormTests.setUp
    publish=fixtures.FormTests.publish
    submit=fixtures.FormTests.submit

    def connected(self,pipeline=''):
        account=prepare(self.forms,self.doc,pipeline,['owner@example.test'])
        token={'access_token':'private-access','refresh_token':'private-refresh','expires_at':time.time()+3600}
        with self.crm.db() as c:
            c.execute('UPDATE form_google_accounts SET google_token=?,google_email=?,google_scopes=? WHERE id=?',
                (cipher(self.forms).encrypt(json.dumps(token).encode()).decode(),'owner@example.test',DRIVE_SCOPE+' '+MAIL_SCOPE,account['id']))
        return account

    def test_central_account_is_private_scoped_and_reusable(self):
        account=self.connected();row=attach(self.forms,self.form['id'],self.doc,account['id'])
        self.assertTrue(row['google_mail_ready']);self.assertEqual(row['google_email'],'owner@example.test')
        self.assertNotIn('private',json.dumps(listing(self.forms,self.doc)))
        self.assertNotIn('google_token',json.dumps(row))
        course=next(p for p in self.pipes if p['course_key'])
        other=self.connected(course['id'])
        with self.assertRaises(ValueError):attach(self.forms,self.form['id'],self.doc,other['id'])
        with institute_access([]):self.assertEqual(len(listing(self.forms,self.doc)),1)
        with self.assertRaises(LookupError):attach(self.forms,self.form['id'],'another-owner',account['id'])

    def test_oauth_requests_send_only_and_stores_verified_account(self):
        account=prepare(self.forms,self.doc,'',['owner@example.test'])
        with patch.dict('os.environ',{'CRM_GOOGLE_CLIENT_ID':'client','CRM_GOOGLE_CLIENT_SECRET':'secret'}):
            url=authorization(self.forms,account['id'],self.doc,1,mail=True,account=True)
            query=parse_qs(urlsplit(url).query);scopes=query['scope'][0].split()
            self.assertEqual(set(scopes),{DRIVE_SCOPE,MAIL_SCOPE,'openid','email'})
            token={'access_token':'private-access','refresh_token':'private-refresh','scope':' '.join(scopes)}
            with patch('crm_form_integrations.http_json',side_effect=[token,{'email':'owner@example.test','email_verified':True}]):
                result=callback(self.forms,1,query['state'][0],'code')
        self.assertTrue(result['return_settings']);self.assertTrue(listing(self.forms,self.doc)[0]['mail_ready'])
        with self.crm.db() as c:
            stored=c.execute('SELECT google_token FROM form_google_accounts').fetchone()[0]
        self.assertNotIn('private-refresh',stored)

    def test_gmail_sender_is_verified_account_and_email_has_no_answers(self):
        account=self.connected();attach(self.forms,self.form['id'],self.doc,account['id'])
        with self.crm.db() as c:row=self.forms.owned(c,self.form['id'],self.doc)
        with patch('crm_form_integrations.mail_ready',return_value=False),patch('crm_form_integrations.http_json',return_value={'id':'gmail-message'}) as call:
            email_delivery(self.forms,{'target':'team@example.test'},row,{'id':'response','answers':'private medical answers'})
        self.assertEqual(call.call_args.args[0],'https://gmail.googleapis.com/gmail/v1/users/me/messages/send')
        message=message_from_bytes(base64.urlsafe_b64decode(call.call_args.args[2]['raw']),policy=policy.default)
        self.assertEqual(message['From'],'owner@example.test');self.assertEqual(message['To'],'team@example.test')
        self.assertNotIn('private medical answers',message.get_content());self.assertIn('/crm.html?',message.get_content())

    def test_unconfigured_queue_blocks_then_resumes_without_resending(self):
        with patch('crm_form_integrations.mail_ready',return_value=True):_,_,token=self.publish({**self.info,'emails':['team@example.test']})
        self.submit(token)
        with patch('crm_form_integrations.mail_ready',return_value=False):self.assertFalse(deliver_once(self.forms))
        with self.crm.db() as c:self.assertEqual(c.execute('SELECT status FROM form_deliveries').fetchone()[0],'blocked')
        account=self.connected();attach(self.forms,self.form['id'],self.doc,account['id'])
        with patch('crm_form_integrations.email_delivery') as send:
            self.assertTrue(deliver_once(self.forms));self.assertFalse(deliver_once(self.forms));send.assert_called_once()

    def test_refresh_updates_central_token_not_form_copy(self):
        account=self.connected();attach(self.forms,self.form['id'],self.doc,account['id'])
        token={'access_token':'old','refresh_token':'refresh','expires_at':0}
        with self.crm.db() as c:
            c.execute('UPDATE form_google_accounts SET google_token=? WHERE id=?',(cipher(self.forms).encrypt(json.dumps(token).encode()).decode(),account['id']))
            row=self.forms.owned(c,self.form['id'],self.doc)
        with patch.dict('os.environ',{'CRM_GOOGLE_CLIENT_ID':'client','CRM_GOOGLE_CLIENT_SECRET':'secret'}),patch('crm_form_integrations.http_json',return_value={'access_token':'new','expires_in':3600}):
            self.assertEqual(access_token(self.forms,row),'new')
        with self.crm.db() as c:
            self.assertEqual(c.execute('SELECT google_token FROM crm_forms').fetchone()[0],'')
            stored=c.execute('SELECT google_token FROM form_google_accounts').fetchone()[0]
        self.assertEqual(json.loads(cipher(self.forms).decrypt(stored.encode()))['access_token'],'new')

    def test_account_cannot_change_when_existing_sheet_depends_on_it(self):
        account=self.connected();attach(self.forms,self.form['id'],self.doc,account['id'])
        with self.crm.db() as c:c.execute("UPDATE crm_forms SET sheet_id='sheet123456789'")
        with patch.dict('os.environ',{'CRM_GOOGLE_CLIENT_ID':'client','CRM_GOOGLE_CLIENT_SECRET':'secret'}):
            state=parse_qs(urlsplit(authorization(self.forms,account['id'],self.doc,1,mail=True,account=True)).query)['state'][0]
            token={'access_token':'private-access','refresh_token':'private-refresh','scope':DRIVE_SCOPE+' email '+MAIL_SCOPE}
            with patch('crm_form_integrations.http_json',side_effect=[token,{'email':'different@example.test','email_verified':True}]):
                with self.assertRaises(ValueError):callback(self.forms,1,state,'code')


if __name__=='__main__':unittest.main()
