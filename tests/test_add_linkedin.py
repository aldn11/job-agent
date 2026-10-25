import io
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import add_linkedin


class AddLinkedinTests(unittest.TestCase):
    def test_manual_job_is_saved_and_evaluation_is_started(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            inbox = root / "inbox"
            db_path = root / "jobs.db"

            with (
                patch("builtins.input", side_effect=["Acme", "Support Agent", ""]),
                patch.object(
                    add_linkedin.sys,
                    "stdin",
                    io.StringIO("Resolve customer support issues with care."),
                ),
                patch.object(add_linkedin, "INBOX_DIR", inbox),
                patch.object(add_linkedin, "DB_PATH", db_path),
                patch.object(add_linkedin, "WRAPPER_SCRIPT", root / "wrapper.py"),
                patch.object(add_linkedin.subprocess, "run") as run,
            ):
                add_linkedin.add_custom_job()

            files = list(inbox.glob("*.txt"))
            self.assertEqual(len(files), 1)
            self.assertIn("COMPANY: Acme", files[0].read_text(encoding="utf-8"))
            self.assertIn("JOB TEXT:\nResolve customer", files[0].read_text(encoding="utf-8"))

            db = sqlite3.connect(db_path)
            try:
                row = db.execute(
                    "SELECT id, source, company, title, status FROM jobs"
                ).fetchone()
            finally:
                db.close()
            self.assertEqual(row[0], files[0].stem)
            self.assertEqual(row[1:], ("LinkedIn", "Acme", "Support Agent", "NEW"))
            run.assert_called_once_with(
                [
                    str(add_linkedin.VENV_PYTHON),
                    str(root / "wrapper.py"),
                    str(files[0]),
                ],
                check=True,
            )

    def test_empty_manual_job_does_not_write_or_start_evaluation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            inbox = root / "inbox"
            with (
                patch("builtins.input", side_effect=["Acme", "Support Agent", ""]),
                patch.object(add_linkedin.sys, "stdin", io.StringIO("  \n")),
                patch.object(add_linkedin, "INBOX_DIR", inbox),
                patch.object(add_linkedin, "DB_PATH", root / "unused.db"),
                patch.object(add_linkedin.subprocess, "run") as run,
            ):
                add_linkedin.add_custom_job()

            self.assertFalse(inbox.exists())
            self.assertFalse((root / "unused.db").exists())
            run.assert_not_called()

    def test_invalid_url_does_not_create_job_or_start_evaluation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            inbox = root / "inbox"
            db_path = root / "jobs.db"
            with (
                patch(
                    "builtins.input",
                    side_effect=["Acme", "Support Agent", "javascript:alert(1)"],
                ),
                patch.object(
                    add_linkedin.sys,
                    "stdin",
                    io.StringIO("Resolve customer support issues."),
                ),
                patch.object(add_linkedin, "INBOX_DIR", inbox),
                patch.object(add_linkedin, "DB_PATH", db_path),
                patch.object(add_linkedin.subprocess, "run") as run,
            ):
                add_linkedin.add_custom_job()

            self.assertFalse(inbox.exists())
            self.assertFalse(db_path.exists())
            run.assert_not_called()

    def test_exact_url_duplicate_does_not_create_second_ai_evaluation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            inbox = root / "inbox"
            db_path = root / "jobs.db"
            evaluations_started = []
            for _ in range(2):
                with (
                    patch(
                        "builtins.input",
                        side_effect=[
                            "Acme",
                            "Support Agent",
                            "https://jobs.example.com/role/123?tracking=abc",
                        ],
                    ),
                    patch.object(
                        add_linkedin.sys,
                        "stdin",
                        io.StringIO("Resolve customer support issues."),
                    ),
                    patch.object(add_linkedin, "INBOX_DIR", inbox),
                    patch.object(add_linkedin, "DB_PATH", db_path),
                    patch.object(
                        add_linkedin, "WRAPPER_SCRIPT", root / "wrapper.py"
                    ),
                    patch.object(add_linkedin.subprocess, "run") as run,
                ):
                    add_linkedin.add_custom_job()
                    evaluations_started.append(run.call_count)

            self.assertEqual(evaluations_started, [1, 0])
            self.assertEqual(len(list(inbox.glob("*.txt"))), 1)
            db = sqlite3.connect(db_path)
            try:
                count, = db.execute("SELECT COUNT(*) FROM jobs").fetchone()
            finally:
                db.close()
            self.assertEqual(count, 1)

    def test_exact_manual_text_without_url_is_deduplicated(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            inbox = root / "inbox"
            db_path = root / "jobs.db"
            results = []
            for _ in range(2):
                with (
                    patch("builtins.input", side_effect=["Acme", "Support Agent", ""]),
                    patch.object(
                        add_linkedin.sys,
                        "stdin",
                        io.StringIO("Resolve customer support issues."),
                    ),
                    patch.object(add_linkedin, "INBOX_DIR", inbox),
                    patch.object(add_linkedin, "DB_PATH", db_path),
                    patch.object(
                        add_linkedin, "WRAPPER_SCRIPT", root / "wrapper.py"
                    ),
                    patch.object(add_linkedin.subprocess, "run") as run,
                ):
                    add_linkedin.add_custom_job()
                    results.append(run.call_count)

            self.assertEqual(results, [1, 0])
            self.assertEqual(len(list(inbox.glob("*.txt"))), 1)
            db = sqlite3.connect(db_path)
            try:
                count, url = db.execute(
                    "SELECT COUNT(*), MAX(url) FROM jobs"
                ).fetchone()
            finally:
                db.close()
            self.assertEqual(count, 1)
            self.assertEqual(url, "")


if __name__ == "__main__":
    unittest.main()
