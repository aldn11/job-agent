import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import store


class StoreRetryTests(unittest.TestCase):
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
                    "linkedin:retry",
                    "retry123",
                    "linkedin",
                    "https://www.linkedin.com/jobs/view/123",
                    "Company",
                    "Role",
                    "fingerprint",
                    "2026-01-01T00:00:00+00:00",
                ),
            )
        db.close()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_job_claim_prevents_duplicate_concurrent_evaluation(self):
        self.assertEqual(store.pending_evaluation_jobs(self.db_path), [("retry123",)])
        self.assertTrue(store.claim_evaluation("retry123", 3600, self.db_path))
        self.assertFalse(store.claim_evaluation("retry123", 3600, self.db_path))
        self.assertEqual(store.pending_evaluation_jobs(self.db_path), [])

    def test_failed_job_is_retried_after_backoff_without_attempt_limit(self):
        self.assertTrue(store.claim_evaluation("retry123", 3600, self.db_path))
        store.mark_evaluation_failed("retry123", "temporary failure", self.db_path)
        self.assertEqual(store.pending_evaluation_jobs(self.db_path), [])

        db = store.connect(self.db_path)
        with db:
            db.execute(
                "UPDATE jobs SET evaluation_next_attempt = NULL WHERE id = ?",
                ("retry123",),
            )
        db.close()
        self.assertEqual(store.pending_evaluation_jobs(self.db_path), [("retry123",)])

    def test_incomplete_evaluation_stays_queued_with_retry_metadata(self):
        store.claim_evaluation("retry123", 3600, self.db_path)
        store.save_evaluation(
            "retry123",
            {"verdict": "MAYBE", "score": None, "info_status": "INCOMPLETE"},
            self.db_path,
        )
        db = store.connect(self.db_path)
        status, attempts, next_attempt = db.execute(
            """SELECT status, evaluation_attempts, evaluation_next_attempt
               FROM jobs WHERE id = ?""",
            ("retry123",),
        ).fetchone()
        db.close()
        self.assertEqual(status, "NEW")
        self.assertEqual(attempts, 1)
        self.assertIsNotNone(next_attempt)

    def test_complete_rejection_without_score_is_not_retried_forever(self):
        store.claim_evaluation("retry123", 3600, self.db_path)
        store.save_evaluation(
            "retry123",
            {"verdict": "SKIP", "score": None, "info_status": "COMPLETE"},
            self.db_path,
        )
        db = store.connect(self.db_path)
        status, = db.execute(
            "SELECT status FROM jobs WHERE id = ?", ("retry123",)
        ).fetchone()
        db.close()
        self.assertEqual(status, "EVALUATED_SKIP")
        self.assertEqual(store.pending_evaluation_jobs(self.db_path), [])

    def test_notification_claim_prevents_duplicate_delivery(self):
        store.save_evaluation(
            "retry123",
            {
                "verdict": "MAYBE",
                "score": 60,
                "info_status": "COMPLETE",
            },
            self.db_path,
        )
        notification_id = store.pending_notifications(self.db_path)[0][0]
        self.assertTrue(
            store.claim_notification(notification_id, 3600, self.db_path)
        )
        self.assertFalse(
            store.claim_notification(notification_id, 3600, self.db_path)
        )
        self.assertEqual(store.pending_notifications(self.db_path), [])

    def test_legacy_database_is_migrated_with_retry_columns(self):
        legacy_path = Path(self.temp_dir.name) / "legacy.db"
        db = sqlite3.connect(legacy_path)
        db.executescript(
            """CREATE TABLE jobs (
                key TEXT PRIMARY KEY, id TEXT, source TEXT, url TEXT,
                company TEXT, title TEXT, fingerprint TEXT, first_seen TEXT,
                status TEXT DEFAULT 'NEW', evaluation TEXT
            );
            CREATE TABLE notifications (
                id INTEGER PRIMARY KEY, job_key TEXT NOT NULL UNIQUE,
                payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'PENDING',
                attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT,
                created_at TEXT NOT NULL, sent_at TEXT
            );"""
        )
        db.execute(
            """INSERT INTO jobs (key, id, source, status, evaluation)
               VALUES (?, ?, ?, ?, ?)""",
            (
                "linkedin:incomplete",
                "incomplete",
                "linkedin",
                "SCRAPE_PENDING",
                json.dumps({"verdict": "MAYBE", "info_status": "INCOMPLETE"}),
            ),
        )
        db.execute(
            """INSERT INTO jobs
               (key, id, source, status, evaluation, first_seen)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                "linkedin:old-pending",
                "old-pending",
                "linkedin",
                "SCRAPE_PENDING",
                None,
                "2020-01-01T00:00:00+00:00",
            ),
        )
        db.commit()
        db.close()

        db = store.connect(legacy_path)
        job_columns = {row[1] for row in db.execute("PRAGMA table_info(jobs)")}
        notification_columns = {
            row[1] for row in db.execute("PRAGMA table_info(notifications)")
        }
        status, = db.execute(
            "SELECT status FROM jobs WHERE id = ?", ("incomplete",)
        ).fetchone()
        old_status, = db.execute(
            "SELECT status FROM jobs WHERE id = ?", ("old-pending",)
        ).fetchone()
        db.close()

        self.assertTrue(
            {"evaluation_attempts", "evaluation_next_attempt", "evaluation_lease_until"}
            <= job_columns
        )
        self.assertTrue(
            {"alert_sent", "letter_sent", "next_attempt_at", "delivery_lease_until"}
            <= notification_columns
        )
        self.assertEqual(status, "SCRAPE_UNAVAILABLE")
        self.assertEqual(old_status, "SCRAPE_UNAVAILABLE")

    def test_legacy_schema_migration_restores_completed_evaluations(self):
        legacy_path = Path(self.temp_dir.name) / "legacy-complete.db"
        db = sqlite3.connect(legacy_path)
        db.executescript(
            """CREATE TABLE jobs (
                key TEXT PRIMARY KEY, id TEXT, source TEXT, url TEXT,
                company TEXT, title TEXT, fingerprint TEXT, first_seen TEXT,
                status TEXT DEFAULT 'NEW', evaluation TEXT
            );
            CREATE TABLE notifications (
                id INTEGER PRIMARY KEY, job_key TEXT NOT NULL UNIQUE,
                payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'PENDING',
                attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT,
                created_at TEXT NOT NULL, sent_at TEXT
            );"""
        )
        db.execute(
            """INSERT INTO jobs (key, id, source, status, evaluation)
               VALUES (?, ?, ?, ?, ?)""",
            (
                "linkedin:complete",
                "complete",
                "linkedin",
                "SCRAPE_PENDING",
                json.dumps(
                    {"verdict": "MAYBE", "score": 70, "info_status": "COMPLETE"}
                ),
            ),
        )
        db.commit()
        db.close()

        db = store.connect(legacy_path)
        status, = db.execute(
            "SELECT status FROM jobs WHERE id = ?", ("complete",)
        ).fetchone()
        db.close()
        self.assertEqual(status, "EVALUATED_MAYBE")


if __name__ == "__main__":
    unittest.main()
