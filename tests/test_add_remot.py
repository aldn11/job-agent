import tempfile
import unittest
from pathlib import Path

import add_remot
import store


class AddRemotTests(unittest.TestCase):
    def test_manual_description_promotes_email_alert_to_new_inbox_job(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "jobs.db"
            db = store.connect(db_path)
            _, key = store.add_job(
                db,
                "https://workremot.com/jobs/support",
                "Empresa via Remōt",
                "Vaga Remōt",
                status="DESCRIPTION_REQUIRED",
                source="Remōt",
                application_url="https://apply.example/form",
            )
            db.close()

            description = (
                "The support specialist will resolve customer issues, document "
                "incidents, troubleshoot account access, communicate clearly, "
                "and coordinate with the engineering team on complex cases."
            )
            path = add_remot.add_manual_remot_job(
                "https://www.workremot.com/jobs/support?utm_source=email",
                "Example Inc",
                "Support Specialist",
                description,
                inbox_dir=root / "inbox",
                db_path=db_path,
            )
            text = path.read_text(encoding="utf-8")
            db = store.connect(db_path)
            row = db.execute(
                """SELECT source, url, title, status, application_url
                   FROM jobs WHERE key = ?""",
                (key,),
            ).fetchone()
            db.close()

        self.assertIn("JOB TEXT:\n" + description, text)
        self.assertIn("APPLICATION URL: https://apply.example/form", text)
        self.assertEqual(
            row,
            (
                "Remōt",
                "https://workremot.com/jobs/support",
                "Support Specialist",
                "NEW",
                "https://apply.example/form",
            ),
        )

    def test_manual_description_rejects_insufficient_text(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(ValueError, "100 caracteres"):
                add_remot.add_manual_remot_job(
                    "https://workremot.com/jobs/support",
                    "",
                    "Support",
                    "Too short.",
                    inbox_dir=Path(temp_dir) / "inbox",
                    db_path=Path(temp_dir) / "jobs.db",
                )
            self.assertFalse((Path(temp_dir) / "inbox").exists())


if __name__ == "__main__":
    unittest.main()
