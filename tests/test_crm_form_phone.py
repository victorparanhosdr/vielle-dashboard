import unittest
from crm_forms import definition,answers_for


class FormPhoneTests(unittest.TestCase):
    def setUp(self):
        self.info=definition({'title':'Contato','fields':[{'id':'phone','type':'phone','label':'WhatsApp','required':True}]})

    def test_brazilian_mobile_landline_and_international_paste(self):
        for raw,expected in [('(11) 99999-0000','5511999990000'),('+55 (11) 99999-0000','5511999990000'),
                             ('5511999990000','5511999990000'),('(11) 3333-4444','551133334444')]:
            self.assertEqual(answers_for(self.info,{'phone':raw})['phone'],expected)

    def test_foreign_e164_keeps_country_code_without_prefixing_brazil(self):
        self.assertEqual(answers_for(self.info,{'phone':'+1 202-555-0123'})['phone'],'12025550123')
        self.assertEqual(answers_for(self.info,{'phone':'+351 912 345 678'})['phone'],'351912345678')

    def test_invalid_ddd_incomplete_extension_and_injection_are_rejected(self):
        for value in ['999990000','(00) 99999-0000','(11) 99999-000','+55 11 99999-0000 ext 1','abc+5511999990000']:
            with self.subTest(value=value),self.assertRaises(ValueError):answers_for(self.info,{'phone':value})


if __name__=='__main__':unittest.main()
