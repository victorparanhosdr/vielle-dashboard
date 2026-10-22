import sqlite3
import unittest
from unittest.mock import Mock, call, patch

from crm_worker import run, tick
from tests import test_crm as fixture


def locked(code=sqlite3.SQLITE_BUSY):
    error = sqlite3.OperationalError("database is locked")
    error.sqlite_errorcode = code
    return error


class WorkerRecoveryTests(unittest.TestCase):
    def drive(self, store, iterations=2, *, tick_effect=None, store_effect=None):
        stop = Mock()
        stop.wait.side_effect = [False] * iterations + [True]
        with patch("crm_api.store_for", side_effect=store_effect, return_value=store) as factory, \
                patch("crm_worker.EvolutionGateway"), \
                patch("crm_worker.sends_enabled", return_value=True), \
                patch("crm_worker.campaigns_enabled", return_value=True), \
                patch("crm_worker.tick", side_effect=tick_effect) as dispatch:
            with self.assertLogs(level="WARNING") as logs:
                run(object(), stop)
        return stop, dispatch, factory, logs.output

    def store(self):
        store = Mock()
        store.db.return_value.__enter__ = Mock(return_value=Mock())
        store.db.return_value.__exit__ = Mock(return_value=False)
        store.db.return_value.__enter__.return_value.execute.return_value = []
        return store

    def test_heartbeat_lock_recovers_and_restores_normal_polling(self):
        store = self.store()
        store.heartbeat.side_effect = [locked(), None]
        stop, dispatch, _, logs = self.drive(store)
        self.assertEqual(stop.wait.call_args_list, [call(2), call(4), call(2)])
        self.assertEqual(store.heartbeat.call_count, 2)
        dispatch.assert_called_once()
        self.assertNotIn("database is locked", " ".join(logs))

    def test_initialization_lock_also_recovers(self):
        store = self.store()
        _, dispatch, factory, _ = self.drive(store, store_effect=[locked(), store])
        self.assertEqual(factory.call_count, 2)
        dispatch.assert_called_once()

    def test_claim_lock_retries_cycle_with_extended_busy_code(self):
        _, dispatch, _, _ = self.drive(self.store(), tick_effect=[locked(sqlite3.SQLITE_BUSY | 0x200), False])
        self.assertEqual(dispatch.call_count, 2)

    def test_persistent_lock_has_capped_interruptible_backoff(self):
        store = self.store()
        store.heartbeat.side_effect = locked(sqlite3.SQLITE_LOCKED)
        stop, dispatch, _, _ = self.drive(store, iterations=6)
        self.assertEqual(stop.wait.call_args_list, [call(n) for n in [2, 4, 8, 16, 30, 30, 30]])
        dispatch.assert_not_called()

    def test_unrelated_database_error_is_not_silenced(self):
        store = self.store()
        error = locked(sqlite3.SQLITE_ERROR)
        store.heartbeat.side_effect = error
        stop = Mock()
        stop.wait.return_value = False
        with patch("crm_api.store_for", return_value=store), \
                patch("crm_worker.EvolutionGateway"), patch("crm_worker.sends_enabled", return_value=True):
            with self.assertRaises(sqlite3.OperationalError):
                run(object(), stop)


class WorkerRecoveryQueueTests(unittest.TestCase):
    setUp = fixture.CrmTests.setUp
    lead = fixture.CrmTests.lead
    campaign = fixture.CrmTests.campaign

    def test_lock_after_provider_acceptance_never_replays_the_message(self):
        lead = self.lead()
        contact = self.store.detail(lead, self.a)["lead"]["contact_id"]
        with self.store.db() as db:
            db.execute("UPDATE channels SET state='open' WHERE id=?", (self.channel,))
        target = self.store.reply({"doctor_id": self.a, "channel_id": self.channel,
            "contact_id": contact, "body": "Resposta sintetica", "request_id": "inflight"},
            "operator", True, sender_name="Equipe")
        gateway = Mock()
        gateway.send.return_value = "synthetic-acceptance"
        stop = Mock()
        stop.wait.side_effect = [False, False, True]
        with patch("crm_api.store_for", return_value=self.store), \
                patch("crm_worker.EvolutionGateway", return_value=gateway), \
                patch("crm_worker.sends_enabled", return_value=True), \
                patch("crm_worker.campaigns_enabled", return_value=True), \
                patch.object(self.store, "finish", side_effect=locked()), \
                self.assertLogs(level="WARNING"):
            run(object(), stop)
        gateway.send.assert_called_once()
        with self.store.db() as db:
            self.assertEqual(self.store.row(db, "messages", target)["status"], "sending")
            db.execute("UPDATE messages SET claimed_at=claimed_at-121 WHERE id=?", (target,))
        self.assertFalse(tick(self.store, gateway))
        gateway.send.assert_called_once()
        with self.store.db() as db:
            self.assertEqual(self.store.row(db, "messages", target)["status"], "unknown")

    def test_recovery_preserves_paused_campaign_and_never_repeats_uncertain_send(self):
        lead = self.lead()
        contact = self.store.detail(lead, self.a)["lead"]["contact_id"]
        campaign = self.campaign([lead])
        with self.store.db() as db:
            db.execute("UPDATE channels SET state='open' WHERE id=?", (self.channel,))
            db.execute("UPDATE campaigns SET status='paused' WHERE id=?", (campaign,))
        payload = {"doctor_id": self.a, "channel_id": self.channel, "contact_id": contact}
        previous = self.store.reply({**payload, "body": "Mensagem anterior", "request_id": "old"},
                                   "operator", True, sender_name="Equipe")
        failing_gateway = Mock()
        failing_gateway.send.side_effect = TimeoutError()
        tick(self.store, failing_gateway)
        target = self.store.reply({**payload, "body": "Nova resposta", "request_id": "new"},
                                 "operator", True, sender_name="Equipe")
        gateway = Mock()
        gateway.send.return_value = "synthetic-acceptance"
        heartbeat = self.store.heartbeat
        effects = iter([locked(), None, None])

        def heartbeat_with_contention():
            error = next(effects)
            if error:
                raise error
            heartbeat()

        stop = Mock()
        stop.wait.side_effect = [False, False, False, True]
        with patch("crm_api.store_for", return_value=self.store), \
                patch("crm_worker.EvolutionGateway", return_value=gateway), \
                patch("crm_worker.sends_enabled", return_value=True), \
                patch("crm_worker.campaigns_enabled", return_value=True), \
                patch.object(self.store, "heartbeat", side_effect=heartbeat_with_contention), \
                self.assertLogs(level="WARNING"):
            run(object(), stop)
        gateway.send.assert_called_once()
        self.assertEqual(gateway.send.call_args.args[0]["id"], target)
        self.assertTrue(self.store.worker_online())
        with self.store.db() as db:
            self.assertEqual(self.store.row(db, "messages", previous)["status"], "unknown")
            self.assertEqual(self.store.row(db, "messages", target)["status"], "sent")
            self.assertEqual(self.store.row(db, "campaigns", campaign)["status"], "paused")
            self.assertEqual(db.execute("SELECT status FROM messages WHERE campaign_id=?", (campaign,)).fetchone()[0], "queued")
