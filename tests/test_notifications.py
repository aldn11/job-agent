import json
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import patch

import store
import telegram_notify


class NotificationOutboxTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "jobs.db"
        db = store.connect(self.db_path)
        with db:
            db.execute(
                """INSERT INTO jobs
                   (key, id, source, url, company, title, fingerprint, first_seen)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    "linkedin:123",
                    "short123",
                    "linkedin",
                    "https://example.com/job",
                    "Company",
                    "Support Agent",
                    "fingerprint",
                    "2026-01-01T00:00:00Z",
                ),
            )
        db.close()

    def tearDown(self):
        self.temp_dir.cleanup()

    def _save_apply_evaluation(self):
        store.save_evaluation(
            "short123",
            {
                "verdict": "APPLY",
                "score": 90,
                "title": "Support Agent",
                "company": "Company",
                "url": "https://example.com/job",
            },
            self.db_path,
            notification_context={
                "job_id": "short123",
                "job_text": "JOB TEXT:\nA support job description. " * 10,
            },
        )

    def test_evaluation_and_notification_are_persisted_together(self):
        self._save_apply_evaluation()
        pending = store.pending_notifications(self.db_path)
        self.assertEqual(len(pending), 1)
        self.assertEqual(json.loads(pending[0][1])["verdict"], "APPLY")

    def test_failed_alert_stays_pending_and_can_be_retried(self):
        store.save_evaluation(
            "short123",
            {
                "verdict": "MAYBE",
                "score": 80,
                "title": "Support Agent",
                "company": "Company",
                "url": "https://example.com/job",
            },
            self.db_path,
            notification_context={
                "job_id": "short123",
                "job_text": "JOB TEXT:\nA support role with real content. " * 10,
            },
        )
        with patch.object(
            telegram_notify,
            "send_telegram_alert",
            side_effect=RuntimeError("temporary network error"),
        ):
            self.assertEqual(
                telegram_notify.deliver_pending_notifications(self.db_path), 1
            )
        self.assertEqual(store.notification_counts(self.db_path)["PENDING"], 1)
        db = store.connect(self.db_path)
        with db:
            db.execute("UPDATE notifications SET next_attempt_at = NULL")
        db.close()

        with patch.object(telegram_notify, "send_telegram_alert") as send_alert:
            self.assertEqual(
                telegram_notify.deliver_pending_notifications(self.db_path), 0
            )
        send_alert.assert_called_once()
        self.assertEqual(store.notification_counts(self.db_path)["SENT"], 1)

    def test_missing_job_does_not_create_notification(self):
        with self.assertRaises(LookupError):
            store.save_evaluation(
                "missing",
                {"verdict": "APPLY"},
                self.db_path,
            )
        self.assertEqual(store.pending_notifications(self.db_path), [])

    def test_incomplete_evaluation_stays_new_without_notification(self):
        store.save_evaluation(
            "short123",
            {
                "verdict": "MAYBE",
                "score": None,
                "info_status": "INCOMPLETE",
            },
            self.db_path,
        )
        db = store.connect(self.db_path)
        status, = db.execute(
            "SELECT status FROM jobs WHERE id = ?", ("short123",)
        ).fetchone()
        db.close()

        self.assertEqual(status, "NEW")
        self.assertEqual(store.pending_notifications(self.db_path), [])

    def test_telegram_sender_rejects_an_alert_without_numeric_score(self):
        with self.assertRaisesRegex(ValueError, "pontuação numérica"):
            telegram_notify.send_telegram_alert(
                {"verdict": "MAYBE", "score": None}
            )

    def test_apply_delivery_sends_only_text_alert(self):
        store.save_evaluation(
            "short123",
            {
                "verdict": "APPLY",
                "score": 90,
                "title": "Support Agent",
                "company": "Company",
                "url": "https://example.com/job",
            },
            self.db_path,
            notification_context={
                "job_id": "short123",
                "job_text": "JOB TEXT:\nSupport role. " * 10,
            },
        )
        sent_payloads = []
        with patch.object(
            telegram_notify,
            "send_telegram_alert",
            side_effect=lambda payload: sent_payloads.append(payload.copy()),
        ) as send_alert:
            self.assertEqual(
                telegram_notify.deliver_pending_notifications(self.db_path), 0
            )

        send_alert.assert_called_once()
        self.assertNotIn("cover_letter", sent_payloads[0])
        self.assertEqual(store.notification_counts(self.db_path)["SENT"], 1)
        db = store.connect(self.db_path)
        letter_sent, = db.execute(
            "SELECT letter_sent FROM notifications"
        ).fetchone()
        db.close()
        self.assertEqual(letter_sent, 0)

    def test_alert_text_is_sent_in_full_in_a_text_message(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b'{"ok": true}'

        evaluation = {
            "verdict": "MAYBE",
            "score": 90,
            "title": "Support Agent",
            "company": "Company",
            "url": "https://example.com/job",
            "reasons_pt": ["Experiência prática em integração e suporte técnico " * 10],
            "interview_risks_pt": ["Pode ser necessário demonstrar fluência oral " * 10],
        }
        with (
            patch.dict(
                "os.environ",
                {"TELEGRAM_TOKEN": "test-token", "TELEGRAM_CHAT_ID": "123"},
            ),
            patch(
                "telegram_notify.urllib.request.urlopen",
                return_value=Response(),
            ) as urlopen,
        ):
            self.assertTrue(telegram_notify.send_telegram_alert(evaluation))

        request = urlopen.call_args.args[0]
        self.assertTrue(request.full_url.endswith("/sendMessage"))
        message = urllib.parse.parse_qs(request.data.decode("utf-8"))["text"][0]
        self.assertIn("Experiência prática em integração e suporte técnico " * 10, message)
        self.assertIn("Pode ser necessário demonstrar fluência oral " * 10, message)
        self.assertIn("Ver Anúncio da Vaga", message)

    def test_maybe_delivery_sends_only_text_alert(self):
        store.save_evaluation(
            "short123",
            {
                "verdict": "MAYBE",
                "score": 80,
                "title": "Support Agent",
                "company": "Company",
                "url": "https://example.com/job",
            },
            self.db_path,
            notification_context={
                "job_id": "short123",
                "job_text": "JOB TEXT:\nA support role with real content. " * 10,
            },
        )
        with patch.object(telegram_notify, "send_telegram_alert") as send_alert:
            self.assertEqual(
                telegram_notify.deliver_pending_notifications(self.db_path), 0
            )

        send_alert.assert_called_once()

    def test_already_sent_alert_completes_pending_notification_without_resending(self):
        store.save_evaluation(
            "short123",
            {
                "verdict": "MAYBE",
                "score": 80,
                "title": "Support Agent",
                "company": "Company",
                "url": "https://example.com/job",
            },
            self.db_path,
        )
        db = store.connect(self.db_path)
        with db:
            db.execute(
                "UPDATE notifications SET alert_sent = 1 WHERE status = 'PENDING'"
            )
        db.close()

        with patch.object(telegram_notify, "send_telegram_alert") as send_alert:
            self.assertEqual(
                telegram_notify.deliver_pending_notifications(self.db_path), 0
            )
        send_alert.assert_not_called()
        self.assertEqual(store.notification_counts(self.db_path)["SENT"], 1)

    def test_schema_upgrade_does_not_backfill_old_incomplete_evaluations(self):
        db = store.connect(self.db_path)
        with db:
            db.execute(
                """UPDATE jobs SET status = 'EVALUATED_MAYBE',
                   evaluation = ? WHERE id = ?""",
                (
                    json.dumps(
                        {
                            "verdict": "MAYBE",
                            "score": None,
                            "info_status": "INCOMPLETE",
                        }
                    ),
                    "short123",
                ),
            )
        db.close()
        db = store.connect(self.db_path)
        status, = db.execute(
            "SELECT status FROM jobs WHERE id = ?", ("short123",)
        ).fetchone()
        db.close()

        self.assertEqual(status, "EVALUATED_MAYBE")


if __name__ == "__main__":
    unittest.main()
