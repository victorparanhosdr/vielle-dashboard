import copy
import json
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from tests import test_crm_forms as fixtures
from crm_attribution import capture,config,finish_url,export_cells


class AttributionTests(unittest.TestCase):
    setUp=fixtures.FormTests.setUp
    publish=fixtures.FormTests.publish
    def submit_tracking(self,token,source='instagram',**changes):
        return self.forms.submit(self.form['slug'],{'token':token,'answers':{'name':'Pessoa Exemplo','phone':'11999990000'},
            'attribution':{'utm':{'utm_source':source,'utm_campaign':'regen-code'},'landing_url':'https://doc4docs.com.br/f/'+self.form['slug']+'?token=private&utm_source='+source,'referrer':'https://example.test/ads?email=private#secret',**changes}},'127.0.0.1')

    def test_capture_uses_url_over_defaults_and_strips_unrelated_query(self):
        settings=config({'defaults':{'utm_source':'padrao','utm_medium':'social'}})
        result=capture({'utm':{'utm_source':'instagram'},'landing_url':'https://doc4docs.com.br/f/test?token=secret','referrer':'https://example.test/path?email=secret#id'},settings,'test')
        self.assertEqual(result['utm'],{'utm_source':'instagram','utm_medium':'social'})
        self.assertEqual(result['sources'],{'utm_source':'url','utm_medium':'default'})
        self.assertEqual(result['landing_url'],'https://doc4docs.com.br/f/test')
        self.assertEqual(result['referrer'],'https://example.test/path')
        self.assertNotIn('secret',json.dumps(result))

    def test_click_ids_are_optional_and_disabled_tracking_collects_nothing(self):
        self.assertFalse(capture({'click_ids':{'gclid':'abc'}},{},'test')['click_ids'])
        self.assertEqual(capture({'click_ids':{'gclid':'abc'}},{'capture_click_ids':True},'test')['click_ids'],{'gclid':'abc'})
        self.assertEqual(capture({'utm':{'utm_source':'private'}},{'enabled':False},'test'),{})

    def test_repeated_submission_keeps_original_attribution_and_lead_is_not_duplicated(self):
        _,_,token=self.publish();first=self.submit_tracking(token);self.submit_tracking(token,'changed')
        rows=self.forms.responses(self.form['id'],self.doc)['items']
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['attribution']['utm']['utm_source'],'instagram')
        lead=rows[0]['lead_id'];history=self.crm.detail(lead,self.doc)['attribution']
        self.assertEqual(history['total'],1);self.assertEqual(history['first']['id'],first['id'])

    def test_first_last_and_history_preserved_on_return_via_another_campaign(self):
        _,_,token=self.publish();self.submit_tracking(token)
        _,_,second=self.publish();self.submit_tracking(second,'google')
        rows=self.forms.responses(self.form['id'],self.doc)['items'];history=self.crm.detail(rows[0]['lead_id'],self.doc)['attribution']
        self.assertEqual(history['total'],2)
        self.assertEqual(history['first']['data']['utm']['utm_source'],'instagram')
        self.assertEqual(history['last']['data']['utm']['utm_source'],'google')
        with self.crm.db() as c:self.assertEqual(c.execute('SELECT count(*) FROM leads').fetchone()[0],1)

    def test_invalid_and_unbounded_tracking_are_rejected(self):
        with self.assertRaises(ValueError):config({'defaults':{'utm_source':'x'*251}})
        with self.assertRaises(ValueError):capture({'utm':{'utm_source':'x\nBcc:'}},{},'test')
        with self.assertRaises(ValueError):capture({'utm':[]},{},'test')
        self.assertEqual(capture({'landing_url':'javascript:alert(1)','referrer':'https://user:secret@example.test'},{},'test')['referrer'],'')

    def test_redirect_forwards_only_utms_when_enabled_without_replacing_checkout_settings(self):
        finish={'mode':'redirect','url':'https://checkout.example.test/?offer=1&utm_source=checkout#pay'}
        data={'utm':{'utm_source':'instagram','utm_campaign':'Curso'},'click_ids':{'fbclid':'private'},'referrer':'private'}
        self.assertEqual(finish_url(finish,{},data),finish)
        result=finish_url(finish,{'forward_on_redirect':True},data)
        query=parse_qs(urlsplit(result['url']).query)
        self.assertEqual(query,{'offer':['1'],'utm_source':['checkout'],'utm_campaign':['Curso']})
        self.assertNotIn('private',result['url']);self.assertEqual(urlsplit(result['url']).fragment,'pay')

    def test_google_rows_include_utm_prefix_and_use_raw_cells(self):
        from crm_form_integrations import sheet_delivery
        _,_,token=self.publish();self.submit_tracking(token)
        with self.crm.db() as c:
            row=self.forms.owned(c,self.form['id'],self.doc)
            row['sheet_id']='spreadsheet123456789'
            response=dict(c.execute('SELECT * FROM form_responses').fetchone())
        with patch('crm_form_integrations.access_token',return_value='private'),patch('crm_form_integrations.http_json',return_value={}) as call:
            sheet_delivery(self.forms,{'target':row['sheet_id']},row,response)
        headers=call.call_args_list[0].args[2]['values'][0];values=call.call_args_list[1].args[2]['values'][0]
        self.assertEqual(headers[2:5],['utm_source','utm_medium','utm_campaign'])
        self.assertEqual(values[2],'instagram');self.assertEqual(values[4],'regen-code')
        self.assertIn('valueInputOption=RAW',call.call_args_list[1].args[0])


if __name__=='__main__':unittest.main()
