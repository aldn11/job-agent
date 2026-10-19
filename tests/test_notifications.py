import json
import tempfile
import unittest
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

    def test_failed_delivery_stays_pending_and_can_be_retried(self):
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
        with patch.object(
            telegram_notify,
            "send_telegram_alert",
            side_effect=RuntimeError("temporary network error"),
        ):
            self.assertEqual(
                telegram_notify.deliver_pending_notifications(self.db_path), 1
            )
        self.assertEqual(store.notification_counts(self.db_path)["PENDING"], 1)

        with patch.object(
            telegram_notify, "send_telegram_alert", return_value=True
        ):
            self.assertEqual(
                telegram_notify.deliver_pending_notifications(self.db_path), 0
            )
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

    def test_apply_delivery_generates_and_persists_letter_before_sending(self):
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
        generated_letter = "Dear hiring team,\n\n" + ("Relevant support experience. " * 5)
        sent_payloads = []
        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch.object(telegram_notify, "BASE_DIR", Path(temp_dir)),
                patch(
                    "cover_letter.generate_cover_letter",
                    return_value=generated_letter,
                ),
                patch.object(
                    telegram_notify,
                    "send_telegram_alert",
                    side_effect=lambda payload: sent_payloads.append(payload.copy()),
                ),
            ):
                self.assertEqual(
                    telegram_notify.deliver_pending_notifications(self.db_path), 0
                )

            saved_file = Path(temp_dir) / "cover_letters" / "cover-letter-short123.txt"
            self.assertEqual(
                saved_file.read_text(encoding="utf-8").strip(),
                generated_letter.strip(),
            )

        self.assertEqual(sent_payloads[0]["cover_letter"], generated_letter)
        self.assertEqual(store.notification_counts(self.db_path)["SENT"], 1)

    def test_apply_telegram_alert_sends_cover_letter_as_document_captioned_with_job(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b'{"ok": true}'

        evaluation = {
            "verdict": "APPLY",
            "score": 90,
            "title": "Support Agent",
            "company": "Company",
            "url": "https://example.com/job",
            "cover_letter": "Dear hiring team. " * 10,
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
        self.assertTrue(request.full_url.endswith("/sendDocument"))
        self.assertIn(b'name="caption"', request.data)
        self.assertIn(b'name="document"', request.data)
        self.assertIn(b"Dear hiring team", request.data)
        self.assertIn(b"NOVA VAGA ENCONTRADA", request.data)

    def test_failed_cover_letter_generation_keeps_apply_notification_pending(self):
        store.save_evaluation(
            "short123",
            {"verdict": "APPLY", "score": 90},
            self.db_path,
            notification_context={"job_id": "short123", "job_text": "A" * 200},
        )
        with patch(
            "cover_letter.generate_cover_letter",
            side_effect=RuntimeError("Hermes unavailable"),
        ):
            self.assertEqual(
                telegram_notify.deliver_pending_notifications(self.db_path), 1
            )
        self.assertEqual(store.notification_counts(self.db_path)["PENDING"], 1)

    def test_legacy_incomplete_linkedin_evaluation_is_queued_for_rescrape(self):
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
        store.migrate_incomplete_linkedin_evaluations(db)
        status, = db.execute(
            "SELECT status FROM jobs WHERE id = ?", ("short123",)
        ).fetchone()
        db.close()

        self.assertEqual(status, "SCRAPE_PENDING")


if __name__ == "__main__":
    unittest.main()
