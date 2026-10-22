import contextlib
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import list_jobs


class ListJobsTests(unittest.TestCase):
    def test_lists_apply_jobs_with_score_reasons_and_interview_risks(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.db"
            db = sqlite3.connect(db_path)
            try:
                with db:
                    db.execute(
                        """CREATE TABLE jobs (
                            id TEXT, company TEXT, title TEXT, url TEXT,
                            evaluation TEXT, status TEXT
                        )"""
                    )
                    db.execute(
                        """INSERT INTO jobs
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (
                            "job123",
                            "Acme",
                            "Support Agent",
                            "https://example.com/jobs/123",
                            json.dumps(
                                {
                                    "score": 88,
                                    "reasons_pt": ["Experiência compatível"],
                                    "interview_risks_pt": ["Confirmar nível de inglês"],
                                },
                            ),
                            "EVALUATED_APPLY",
                        ),
                    )
            finally:
                db.close()

            output = io.StringIO()
            with (
                patch.object(list_jobs, "DB_PATH", db_path),
                contextlib.redirect_stdout(output),
            ):
                list_jobs.list_apply_jobs()

        text = output.getvalue()
        self.assertIn("TOTAL: 1", text)
        self.assertIn("[88 pts] Support Agent @ Acme", text)
        self.assertIn("https://example.com/jobs/123", text)
        self.assertIn("Experiência compatível", text)
        self.assertIn("Confirmar nível de inglês", text)

    def test_reports_empty_apply_list(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.db"
            db = sqlite3.connect(db_path)
            try:
                db.execute(
                    """CREATE TABLE jobs (
                        id TEXT, company TEXT, title TEXT, url TEXT,
                        evaluation TEXT, status TEXT
                    )"""
                )
            finally:
                db.close()

            output = io.StringIO()
            with (
                patch.object(list_jobs, "DB_PATH", db_path),
                contextlib.redirect_stdout(output),
            ):
                list_jobs.list_apply_jobs()

        self.assertIn("TOTAL: 0", output.getvalue())


if __name__ == "__main__":
    unittest.main()
