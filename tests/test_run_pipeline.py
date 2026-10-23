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

    def test_pipeline_returns_failure_when_job_evaluation_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            inbox = root / "inbox"
            inbox.mkdir()
            (inbox / "job123.txt").write_text("job text", encoding="utf-8")

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


if __name__ == "__main__":
    unittest.main()
