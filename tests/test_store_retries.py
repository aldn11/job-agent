import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
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

    def test_quota_failure_uses_long_retry_delay(self):
        self.assertTrue(store.claim_evaluation("retry123", 3600, self.db_path))
        before = datetime.now(timezone.utc)
        store.mark_evaluation_failed(
            "retry123",
            "provider quota",
            self.db_path,
            retry_after_seconds=86400,
        )

        db = store.connect(self.db_path)
        retry_at, attempts = db.execute(
            """SELECT evaluation_next_attempt, evaluation_attempts
               FROM jobs WHERE id = ?""",
            ("retry123",),
        ).fetchone()
        db.close()
        retry_at = datetime.fromisoformat(retry_at)
        self.assertGreaterEqual(
            retry_at,
            before + timedelta(hours=23, minutes=59),
        )
        self.assertEqual(attempts, 1)
        self.assertEqual(store.pending_evaluation_jobs(self.db_path), [])

    def test_missing_linkedin_description_returns_to_scrape_queue(self):
        self.assertTrue(store.claim_evaluation("retry123", 3600, self.db_path))
        status = store.defer_missing_description(
            "retry123", "Description is too short.", self.db_path
        )

        db = store.connect(self.db_path)
        stored_status, retry_at, lease = db.execute(
            """SELECT status, scrape_next_attempt, evaluation_lease_until
               FROM jobs WHERE id = ?""",
            ("retry123",),
        ).fetchone()
        db.close()
        self.assertEqual(status, "SCRAPE_PENDING")
        self.assertEqual(stored_status, "SCRAPE_PENDING")
        self.assertIsNotNone(retry_at)
        self.assertIsNone(lease)
        self.assertEqual(store.pending_evaluation_jobs(self.db_path), [])

    def test_invalid_job_url_moves_claimed_job_to_input_review(self):
        self.assertTrue(store.claim_evaluation("retry123", 3600, self.db_path))
        self.assertTrue(
            store.defer_invalid_job_url(
                "retry123", "URL da vaga inválida.", self.db_path
            )
        )

        db = store.connect(self.db_path)
        status, error, retry_at, lease = db.execute(
            """SELECT status, evaluation_last_error, evaluation_next_attempt,
                      evaluation_lease_until
               FROM jobs WHERE id = ?""",
            ("retry123",),
        ).fetchone()
        db.close()
        self.assertEqual(status, "INPUT_REVIEW")
        self.assertEqual(error, "URL da vaga inválida.")
        self.assertIsNone(retry_at)
        self.assertIsNone(lease)
        self.assertEqual(store.pending_evaluation_jobs(self.db_path), [])

    def test_missing_non_linkedin_description_is_sent_to_manual_review(self):
        db = store.connect(self.db_path)
        with db:
            db.execute(
                """INSERT INTO jobs
                   (key, id, source, url, company, title, fingerprint, first_seen,
                    status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'EVALUATING')""",
                (
                    "rss:review",
                    "review123",
                    "remotive",
                    "https://remotive.com/remote-jobs/123",
                    "Company",
                    "Role",
                    "fp-review",
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        db.close()

        status = store.defer_missing_description(
            "review123", "Description is too short.", self.db_path
        )
        self.assertEqual(status, "SCRAPE_REVIEW")
        db = store.connect(self.db_path)
        stored_status, retry_at = db.execute(
            "SELECT status, scrape_next_attempt FROM jobs WHERE id = ?",
            ("review123",),
        ).fetchone()
        db.close()
        self.assertEqual(stored_status, "SCRAPE_REVIEW")
        self.assertIsNone(retry_at)

    def test_linkedin_source_without_listing_url_is_not_sent_to_scraper(self):
        db = store.connect(self.db_path)
        with db:
            db.execute(
                """INSERT INTO jobs
                   (key, id, source, url, company, title, fingerprint, first_seen,
                    status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'EVALUATING')""",
                (
                    "linkedin:manual",
                    "manual123",
                    "LinkedIn",
                    "https://www.linkedin.com/",
                    "Company",
                    "Role",
                    "fp-manual",
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        db.close()

        status = store.defer_missing_description(
            "manual123", "Description is too short.", self.db_path
        )
        self.assertEqual(status, "SCRAPE_REVIEW")

    def test_ai_usage_records_only_sizes_and_estimated_tokens(self):
        store.record_ai_usage(
            "retry123",
            "test-model",
            prompt_chars=101,
            response_chars=21,
            outcome="success",
            path=self.db_path,
        )

        db = store.connect(self.db_path)
        row = db.execute(
            """SELECT job_id, model, prompt_chars, input_tokens_estimate,
                      response_chars, output_tokens_estimate, outcome, error_type
               FROM ai_usage"""
        ).fetchone()
        db.close()
        self.assertEqual(
            row,
            ("retry123", "test-model", 101, 26, 21, 6, "success", None),
        )

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
        review_columns = {
            row[1] for row in db.execute("PRAGMA table_info(email_review_queue)")
        }
        usage_columns = {
            row[1] for row in db.execute("PRAGMA table_info(ai_usage)")
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
            {"alert_sent", "next_attempt_at", "delivery_lease_until"}
            <= notification_columns
        )
        self.assertTrue(
            {
                "message_fingerprint", "imap_sequence_id", "message_id",
                "sender", "subject", "sent_at", "reason", "recorded_at",
            }
            <= review_columns
        )
        self.assertTrue(
            {
                "job_id", "recorded_at", "model", "prompt_chars",
                "input_tokens_estimate", "response_chars",
                "output_tokens_estimate", "outcome", "error_type",
            }
            <= usage_columns
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

    def test_legacy_migration_retires_evaluation_with_excessive_json_nesting(self):
        db = store.connect(self.db_path)
        with db:
            db.execute(
                """INSERT INTO jobs
                   (key, id, source, url, company, title, fingerprint, first_seen,
                    status, evaluation)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    "linkedin:nested",
                    "nested",
                    "linkedin",
                    "https://www.linkedin.com/jobs/view/115",
                    "Company",
                    "Role",
                    "fp-nested",
                    "2026-01-01T00:00:00+00:00",
                    "SCRAPE_PENDING",
                    "[" * 2000 + "]" * 2000,
                ),
            )
        store.restore_legacy_evaluations_from_scrape_queue(db)
        status, = db.execute(
            "SELECT status FROM jobs WHERE id = ?", ("nested",)
        ).fetchone()
        db.close()
        self.assertEqual(status, "SCRAPE_UNAVAILABLE")

    def test_new_migration_retires_backlog_with_missing_or_invalid_dates(self):
        db = store.connect(self.db_path)
        with db:
            db.execute(
                "DELETE FROM schema_migrations WHERE migration_id = ?",
                ("retire-stale-pending-scrapes-v2",),
            )
            db.execute(
                """INSERT INTO jobs
                   (key, id, source, url, company, title, fingerprint, first_seen,
                    status, scrape_attempts)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    "linkedin:null-date",
                    "null-date",
                    "linkedin",
                    "https://www.linkedin.com/jobs/view/111",
                    "Company",
                    "Old role",
                    "fp-null",
                    None,
                    "SCRAPE_PENDING",
                    4,
                ),
            )
            db.execute(
                """INSERT INTO jobs
                   (key, id, source, url, company, title, fingerprint, first_seen,
                    status, scrape_attempts)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    "linkedin:invalid-date",
                    "invalid-date",
                    "linkedin",
                    "https://www.linkedin.com/jobs/view/112",
                    "Company",
                    "Old role",
                    "fp-invalid",
                    "not-a-date",
                    "SCRAPE_PENDING",
                    4,
                ),
            )
        db.close()

        db = store.connect(self.db_path)
        statuses = dict(
            db.execute(
                "SELECT id, status FROM jobs WHERE id IN ('null-date', 'invalid-date')"
            ).fetchall()
        )
        db.close()
        self.assertEqual(statuses["null-date"], "SCRAPE_UNAVAILABLE")
        self.assertEqual(statuses["invalid-date"], "SCRAPE_UNAVAILABLE")

    def test_backlog_migration_does_not_retire_unknown_failure_reasons(self):
        db = store.connect(self.db_path)
        with db:
            db.execute(
                "DELETE FROM schema_migrations WHERE migration_id = ?",
                ("retire-stale-pending-scrapes-v2",),
            )
            db.execute(
                """INSERT INTO jobs
                   (key, id, source, url, company, title, fingerprint, first_seen,
                    status, scrape_attempts)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    "linkedin:repeated-failure",
                    "repeated-failure",
                    "linkedin",
                    "https://www.linkedin.com/jobs/view/114",
                    "Company",
                    "Role",
                    "fp-retry",
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "SCRAPE_PENDING",
                    4,
                ),
            )
        db.close()

        db = store.connect(self.db_path)
        status, = db.execute(
            "SELECT status FROM jobs WHERE id = ?", ("repeated-failure",)
        ).fetchone()
        db.close()
        self.assertEqual(status, "SCRAPE_PENDING")

    def test_migration_routes_known_exhausted_short_descriptions_to_review(self):
        db = store.connect(self.db_path)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with db:
            db.execute(
                "DELETE FROM schema_migrations WHERE migration_id = ?",
                ("review-exhausted-short-descriptions-v1",),
            )
            db.executemany(
                """INSERT INTO jobs
                   (key, id, source, url, company, title, fingerprint, first_seen,
                    status, scrape_attempts, scrape_last_error)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        "linkedin:known-short",
                        "known-short",
                        "linkedin",
                        "https://www.linkedin.com/jobs/view/116",
                        "Company",
                        "Short description",
                        "fp-short",
                        now,
                        "SCRAPE_PENDING",
                        5,
                        "A descrição extraída é curta demais para avaliação.",
                    ),
                    (
                        "linkedin:unknown-failure",
                        "unknown-failure",
                        "linkedin",
                        "https://www.linkedin.com/jobs/view/117",
                        "Company",
                        "Unknown failure",
                        "fp-unknown",
                        now,
                        "SCRAPE_PENDING",
                        8,
                        "Falha temporária de rede.",
                    ),
                    (
                        "linkedin:below-threshold",
                        "below-threshold",
                        "linkedin",
                        "https://www.linkedin.com/jobs/view/118",
                        "Company",
                        "Below threshold",
                        "fp-below",
                        now,
                        "SCRAPE_PENDING",
                        4,
                        "A descrição extraída é curta demais para avaliação.",
                    ),
                ],
            )
        db.close()

        db = store.connect(self.db_path)
        statuses = dict(
            db.execute(
                """SELECT id, status FROM jobs
                   WHERE id IN ('known-short', 'unknown-failure', 'below-threshold')"""
            ).fetchall()
        )
        db.close()
        self.assertEqual(statuses["known-short"], "SCRAPE_REVIEW")
        self.assertEqual(statuses["unknown-failure"], "SCRAPE_PENDING")
        self.assertEqual(statuses["below-threshold"], "SCRAPE_PENDING")

    def test_new_jobs_with_recent_dates_remain_in_scrape_queue(self):
        db = store.connect(self.db_path)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        db.execute(
            """INSERT INTO jobs
               (key, id, source, url, company, title, fingerprint, first_seen, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "linkedin:fresh",
                "fresh",
                "linkedin",
                "https://www.linkedin.com/jobs/view/113",
                "Company",
                "Fresh role",
                "fp-fresh",
                now,
                "SCRAPE_PENDING",
            ),
        )
        db.commit()
        self.assertEqual(store.retire_stale_pending_scrapes(db), 0)
        rows = db.execute(
            "SELECT id, status FROM jobs WHERE id = ?", ("fresh",)
        ).fetchall()
        db.close()
        self.assertEqual(rows, [("fresh", "SCRAPE_PENDING")])


if __name__ == "__main__":
    unittest.main()
