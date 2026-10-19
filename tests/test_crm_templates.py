import io
import json
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlparse

from crm_api import handle_request
from crm_store import Conflict, CrmStore
from crm_templates import archive, listing, restore, save
from tests import test_crm as base


class TemplateTests(unittest.TestCase):
    def setUp(self):
        base.CrmTests.setUp(self)

    def create(self, **changes):
        return save(self.store, {"title": "Endereço", "shortcut": "/endereço", "body": "Olá, {nome}! Nosso endereço de teste é Rua Exemplo, 100.", **changes}, "staff-1", "Jane")

    def test_create_shared_list_metadata_and_restart_never_create_messages(self):
        item = self.create()
        self.assertEqual(item["shortcut"], "endereço")
        self.assertEqual(item["shortcut_key"], "endereco")
        self.assertEqual(item["revision"], 1)
        self.assertEqual(item["updated_by_name"], "Jane")
        self.assertEqual(listing(self.store), [item])
        self.assertEqual(listing(CrmStore(self.tmp.name)), [item])
        with self.store.db() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM messages").fetchone()[0], 0)

    def test_shortcuts_are_unique_with_or_without_accents_slash_and_case(self):
        self.create()
        for shortcut in ("ENDERECO", "/EndereÇo", "endereco"):
            with self.subTest(shortcut=shortcut), self.assertRaises(ValueError):
                self.create(shortcut=shortcut)
        self.assertTrue(self.create(shortcut="endereco-sp"))

    def test_update_revision_preserves_others_and_rejects_lost_updates(self):
        first = self.create()
        second = self.create(shortcut="outro")
        updated = save(self.store, {**first, "body": "Novo texto"}, "staff-2", "Maria")
        self.assertEqual(updated["revision"], 2)
        self.assertEqual(updated["updated_by_name"], "Maria")
        with self.assertRaises(Conflict):
            save(self.store, {**first, "body": "Texto antigo"}, "staff-1", "Jane")
        self.assertIn(second, listing(self.store))

    def test_archive_restore_and_shortcut_reuse_are_reversible_and_revision_safe(self):
        item = self.create()
        archive(self.store, item, "staff-1", "Jane")
        self.assertEqual(listing(self.store), [])
        archived = listing(self.store, True)[0]
        with self.assertRaises(Conflict):
            restore(self.store, item, "staff-1", "Jane")
        replacement = self.create(shortcut="endereco")
        with self.assertRaises(ValueError):
            restore(self.store, archived, "staff-1", "Jane")
        archive(self.store, replacement, "staff-1", "Jane")
        restore(self.store, archived, "staff-2", "Maria")
        self.assertEqual(listing(self.store)[0]["revision"], 3)
        self.assertEqual(listing(self.store)[0]["body"], item["body"])

    def test_limits_validation_unknown_ids_and_private_clinic_storage(self):
        for changes in ({"title": ""}, {"body": ""}, {"body": "x"*3001}, {"title": "x"*81},
                        {"shortcut": "tem espaço"}, {"shortcut": "/"}, {"shortcut": "a/b"}, {"shortcut": "a"*41}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.create(**changes)
        with self.assertRaises(LookupError):
            self.create(id="f"*32, revision=1)
        other = CrmStore(self.tmp.name + "/other-clinic")
        self.create()
        self.assertEqual(listing(other), [])

    def handler(self, method="GET", permitted=True, origin=True, payload=None):
        h = Mock();h.command=method;h.current_user={"id": "operator", "nome": "Jane", "is_master": False}
        h.server.auth_store.has_permission.return_value=permitted
        raw=json.dumps(payload or {}).encode();h.headers={"Content-Length": str(len(raw))};h.rfile=io.BytesIO(raw)
        h.same_origin_request.return_value=origin
        return h

    def test_staff_can_manage_templates_with_crm_permission_and_no_send(self):
        h=self.handler("POST",payload={"title":"Retorno","shortcut":"retorno","body":"Podemos agendar seu retorno?"})
        with patch("crm_api.store_for",return_value=self.store), patch("crm_api.EvolutionGateway") as gateway:
            handle_request(h,urlparse("/api/crm/message-template-save?clinic=vielle"))
            self.assertTrue(h.auth_json.call_args.args[0]["ok"])
            item=h.auth_json.call_args.args[0]["template"]
            gateway.assert_not_called()
            self.assertNotIn(item["body"],json.dumps(h.server.auth_store.audit_event.call_args.kwargs))
            for action, payload in (("message-template-save",{**item,"body":"Revisado"}),):
                h=self.handler("POST",payload=payload);handle_request(h,urlparse("/api/crm/"+action+"?clinic=vielle"))
                self.assertTrue(h.auth_json.call_args.args[0]["ok"])
            h=self.handler();handle_request(h,urlparse("/api/crm/message-templates?clinic=vielle"))
            self.assertEqual(h.auth_json.call_args.args[0]["templates"][0]["body"],"Revisado")

    def test_permissions_same_origin_and_clinic_are_checked_before_changes(self):
        for options, clinic in (({"permitted":False},"vielle"), ({"origin":False},"vielle"), ({},"other")):
            h=self.handler("POST",payload={"title":"a","shortcut":"a","body":"a"},**options)
            with patch("crm_api.store_for",return_value=self.store):
                handle_request(h,urlparse("/api/crm/message-template-save?clinic="+clinic))
            self.assertEqual(h.auth_json.call_args.args[1],403)
        self.assertEqual(listing(self.store), [])
