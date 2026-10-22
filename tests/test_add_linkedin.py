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


if __name__ == "__main__":
    unittest.main()
