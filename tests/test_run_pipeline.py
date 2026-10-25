import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import run_pipeline
import store


class RunPipelineStageTests(unittest.TestCase):
    def test_stage_failure_is_reported_and_recorded(self):
        failures = []
        with patch.object(
            run_pipeline.subprocess,
            "run",
            side_effect=subprocess.CalledProcessError(2, ["collector.py"]),
        ):
            result = run_pipeline.run_stage("RSS collector", ["collector.py"], failures)

        self.assertFalse(result)
        self.assertEqual(failures, ["RSS collector"])

    def test_quota_exit_is_a_distinct_stage_result(self):
        failures = []
        with patch.object(
            run_pipeline.subprocess,
            "run",
            side_effect=subprocess.CalledProcessError(
                75, ["python", "wrapper.py", "job.txt"]
            ),
        ):
            result = run_pipeline.run_stage(
                "Job evaluation",
                ["python", "wrapper.py", "job.txt"],
                failures,
            )

        self.assertEqual(result, run_pipeline.STAGE_QUOTA_EXHAUSTED)
        self.assertEqual(failures, ["Job evaluation (cota de IA)"])

    def test_pipeline_returns_failure_when_job_evaluation_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            inbox = root / "inbox"
            inbox.mkdir()
            (inbox / "job123.txt").write_text(
                "JOB TEXT:\n" + "Support users and resolve technical issues. " * 5,
                encoding="utf-8",
            )

            db_path = root / "jobs.db"
            db = store.connect(db_path)
            db.execute(
                """INSERT INTO jobs
                   (key, id, source, url, company, title, fingerprint, first_seen)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                ("test:123", "job123", "test", "https://example.com", "Company",
                 "Role", "fingerprint", "2026-01-01"),
            )
            db.commit()
            db.close()

            def run_command(command, check, timeout=None):
                if str(command[1]).endswith("wrapper.py"):
                    raise subprocess.CalledProcessError(1, command)

            with (
                patch.object(run_pipeline, "BASE_DIR", root),
                patch.object(run_pipeline, "INBOX_DIR", inbox),
                patch.object(run_pipeline, "WRAPPER_SCRIPT", root / "wrapper.py"),
                patch.object(run_pipeline, "DB_PATH", db_path),
                patch.object(run_pipeline, "VENV_PYTHON", "python"),
                patch.object(run_pipeline.subprocess, "run", side_effect=run_command),
                patch(
                    "telegram_notify.deliver_pending_notifications",
                    return_value=0,
                ),
            ):
                result = run_pipeline.run_pipeline()

        self.assertEqual(result, 1)

    def test_pipeline_skips_ai_for_missing_description_and_queues_linkedin_scrape(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            inbox = root / "inbox"
            inbox.mkdir()
            (inbox / "shortjob.txt").write_text(
                "SOURCE: LinkedIn Email Alert\n"
                "URL: https://www.linkedin.com/jobs/view/123\n"
                "COMPANY: Company\nTITLE: Role\n\nJOB TEXT:\nShort",
                encoding="utf-8",
            )
            db_path = root / "jobs.db"
            db = store.connect(db_path)
            db.execute(
                """INSERT INTO jobs
                   (key, id, source, url, company, title, fingerprint, first_seen)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    "linkedin:123",
                    "shortjob",
                    "linkedin",
                    "https://www.linkedin.com/jobs/view/123",
                    "Company",
                    "Role",
                    "fp-short",
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            db.commit()
            db.close()

            with (
                patch.object(run_pipeline, "BASE_DIR", root),
                patch.object(run_pipeline, "INBOX_DIR", inbox),
                patch.object(run_pipeline, "WRAPPER_SCRIPT", root / "wrapper.py"),
                patch.object(run_pipeline, "DB_PATH", db_path),
                patch.object(run_pipeline, "VENV_PYTHON", "python"),
                patch.object(run_pipeline.subprocess, "run") as run_command,
                patch(
                    "telegram_notify.deliver_pending_notifications",
                    return_value=0,
                ),
            ):
                result = run_pipeline.run_pipeline()

            db = store.connect(db_path)
            status, retry_at, evaluation_lease = db.execute(
                """SELECT status, scrape_next_attempt, evaluation_lease_until
                   FROM jobs WHERE id = ?""",
                ("shortjob",),
            ).fetchone()
            db.close()

        self.assertEqual(result, 0)
        self.assertEqual(status, "SCRAPE_PENDING")
        self.assertIsNotNone(retry_at)
        self.assertIsNone(evaluation_lease)
        self.assertFalse(
            any(
                str(call.args[0][1]).endswith("wrapper.py")
                for call in run_command.call_args_list
            )
        )

    def test_pipeline_skips_ai_and_routes_invalid_url_to_input_review(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            inbox = root / "inbox"
            inbox.mkdir()
            (inbox / "badurl.txt").write_text(
                "URL: javascript:alert(1)\nJOB TEXT:\n"
                + "Support users and resolve technical issues. " * 5,
                encoding="utf-8",
            )
            db_path = root / "jobs.db"
            db = store.connect(db_path)
            db.execute(
                """INSERT INTO jobs
                   (key, id, source, url, company, title, fingerprint, first_seen)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    "manual:badurl",
                    "badurl",
                    "Manual",
                    "javascript:alert(1)",
                    "Company",
                    "Role",
                    "fp-badurl",
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            db.commit()
            db.close()

            with (
                patch.object(run_pipeline, "BASE_DIR", root),
                patch.object(run_pipeline, "INBOX_DIR", inbox),
                patch.object(run_pipeline, "WRAPPER_SCRIPT", root / "wrapper.py"),
                patch.object(run_pipeline, "DB_PATH", db_path),
                patch.object(run_pipeline, "VENV_PYTHON", "python"),
                patch.object(run_pipeline.subprocess, "run") as run_command,
                patch(
                    "telegram_notify.deliver_pending_notifications",
                    return_value=0,
                ),
            ):
                result = run_pipeline.run_pipeline()

            db = store.connect(db_path)
            status, retry_at = db.execute(
                """SELECT status, evaluation_next_attempt
                   FROM jobs WHERE id = ?""",
                ("badurl",),
            ).fetchone()
            db.close()

        self.assertEqual(result, 0)
        self.assertEqual(status, "INPUT_REVIEW")
        self.assertIsNone(retry_at)
        self.assertFalse(
            any(
                str(call.args[0][1]).endswith("wrapper.py")
                for call in run_command.call_args_list
            )
        )

    def test_pipeline_stops_ai_calls_after_provider_quota_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            inbox = root / "inbox"
            inbox.mkdir()
            db_path = root / "jobs.db"
            db = store.connect(db_path)
            for job_id in ("firstjob", "secondjob"):
                (inbox / f"{job_id}.txt").write_text(
                    "JOB TEXT:\n" + "Support users and resolve technical issues. " * 5,
                    encoding="utf-8",
                )
                db.execute(
                    """INSERT INTO jobs
                       (key, id, source, url, company, title, fingerprint, first_seen)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        f"test:{job_id}",
                        job_id,
                        "test",
                        "https://example.com",
                        "Company",
                        "Role",
                        job_id,
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
            db.commit()
            db.close()

            wrapper_calls = []

            def run_command(command, check, timeout=None):
                if str(command[1]).endswith("wrapper.py"):
                    wrapper_calls.append(command)
                    raise subprocess.CalledProcessError(75, command)

            with (
                patch.object(run_pipeline, "BASE_DIR", root),
                patch.object(run_pipeline, "INBOX_DIR", inbox),
                patch.object(run_pipeline, "WRAPPER_SCRIPT", root / "wrapper.py"),
                patch.object(run_pipeline, "DB_PATH", db_path),
                patch.object(run_pipeline, "VENV_PYTHON", "python"),
                patch.object(run_pipeline.subprocess, "run", side_effect=run_command),
                patch(
                    "telegram_notify.deliver_pending_notifications",
                    return_value=0,
                ),
            ):
                result = run_pipeline.run_pipeline()

            db = store.connect(db_path)
            states = dict(
                db.execute(
                    "SELECT id, status FROM jobs ORDER BY id"
                ).fetchall()
            )
            retry_at, attempts = db.execute(
                """SELECT evaluation_next_attempt, evaluation_attempts
                   FROM jobs WHERE id = 'firstjob'"""
            ).fetchone()
            db.close()
            pending = store.pending_evaluation_jobs(db_path)

        self.assertEqual(result, 1)
        self.assertEqual(len(wrapper_calls), 1)
        self.assertEqual(states, {"firstjob": "NEW", "secondjob": "NEW"})
        self.assertIsNotNone(retry_at)
        self.assertEqual(attempts, 1)
        self.assertIn(("secondjob",), pending)

    def test_pipeline_does_not_report_success_while_evaluations_are_pending(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            inbox = root / "inbox"
            inbox.mkdir()
            db_path = root / "jobs.db"
            db = store.connect(db_path)
            with db:
                db.execute(
                    """INSERT INTO jobs
                       (key, id, source, url, company, title, fingerprint, first_seen,
                        evaluation_next_attempt)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        "test:pending",
                        "pending123",
                        "test",
                        "https://example.com",
                        "Company",
                        "Role",
                        "fingerprint",
                        "2026-01-01",
                        (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
                    ),
                )
            db.close()

            with (
                patch.object(run_pipeline, "BASE_DIR", root),
                patch.object(run_pipeline, "INBOX_DIR", inbox),
                patch.object(run_pipeline, "WRAPPER_SCRIPT", root / "wrapper.py"),
                patch.object(run_pipeline, "DB_PATH", db_path),
                patch.object(run_pipeline, "VENV_PYTHON", "python"),
                patch.object(run_pipeline.subprocess, "run") as run_command,
                patch(
                    "telegram_notify.deliver_pending_notifications",
                    return_value=0,
                ),
            ):
                result = run_pipeline.run_pipeline()

        self.assertEqual(result, 1)
        self.assertTrue(
            any(
                str(call.args[0][1]).endswith("jobspresso_collector.py")
                for call in run_command.call_args_list
            )
        )
        self.assertTrue(
            any(
                str(call.args[0][1]).endswith("remotive_collector.py")
                for call in run_command.call_args_list
            )
        )
        self.assertTrue(
            any(
                str(call.args[0][1]).endswith("bairesdev_collector.py")
                for call in run_command.call_args_list
            )
        )


if __name__ == "__main__":
    unittest.main()
