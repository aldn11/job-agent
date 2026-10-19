import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import email_collector
import store
from wrapper import extract_job_description


class FakeImap:
    def __init__(self, *args, **kwargs):
        self.events = []
        self.ids = [str(index).encode() for index in range(1, 8)]

    def login(self, user, password):
        self.events.append(("login", user))

    def select(self, mailbox):
        self.events.append(("select", mailbox))
        return "OK", [b"7"]

    def search(self, *args):
        self.events.append(("search", args))
        return "OK", [b" ".join(self.ids)]

    def fetch(self, email_id, query):
        self.events.append(("fetch", email_id, query))
        return "OK", [(b"body", b"From: jobs@linkedin.com\r\n\r\nalert")]

    def store(self, email_id, operation, flag):
        self.events.append(("store", email_id, operation, flag))
        return "OK", []

    def logout(self):
        self.events.append(("logout",))
        return "BYE", []


class EmailCollectorTests(unittest.TestCase):
    def test_page_without_a_real_description_is_rejected(self):
        page = b"""<html><body>
          <h1>IT Support Analyst</h1>
          <div class="topcard">This page asks you to sign in to view the job.</div>
        </body></html>"""

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return page

        with patch.object(email_collector.urllib.request, "urlopen", return_value=Response()):
            with self.assertRaises(email_collector.ScrapeError):
                email_collector.fetch_public_job_text("https://www.linkedin.com/jobs/view/123")

    def test_url_only_job_file_is_rejected_before_ai_evaluation(self):
        with self.assertRaisesRegex(ValueError, "curta demais"):
            extract_job_description(
                "SOURCE: LinkedIn\nURL: https://linkedin.com/jobs/view/123\n"
                "JOB TEXT:\nLink original para consulta: https://linkedin.com/jobs/view/123\n"
                "PUBLIC PAGE METADATA:\nLOCATION: Not found"
            )

    def test_public_page_extracts_location_and_workplace_metadata(self):
        page = b"""<html><body>
          <h1>IT Support Analyst</h1>
          <a class="topcard__org-name-link">Example Inc</a>
          <span class="topcard__flavor--bullet">Caruaru, Brazil</span>
          <span class="jobs-unified-top-card__workplace-type">Remote</span>
          <div class="show-more-less-html__markup">Support users and resolve technical issues. Troubleshoot account access, configure systems, document incidents, communicate with customers, and follow support procedures. Provide clear updates and escalate complex technical problems to the engineering team.</div>
        </body></html>"""

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return page

        with patch.object(email_collector.urllib.request, "urlopen", return_value=Response()):
            result = email_collector.fetch_public_job_text("https://www.linkedin.com/jobs/view/123")

        self.assertEqual(result["location"], "Caruaru, Brazil")
        self.assertEqual(result["workplace_type"], "Remote")

    def test_scraped_metadata_is_appended_to_saved_job_text(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db = store.connect(":memory:")
            job_data = {
                "title": "IT Support Analyst",
                "company": "Example Inc",
                "description": "Support users and resolve technical issues. " * 5,
                "location": "Caruaru, Brazil",
                "workplace_type": "Remote",
            }
            alert = '<a href="https://www.linkedin.com/jobs/view/123?tracking=abc">Job</a>'
            with (
                patch.object(email_collector, "INBOX_DIR", Path(temp_dir)),
                patch.object(email_collector, "fetch_public_job_text", return_value=job_data),
                patch.object(email_collector.time, "sleep"),
            ):
                email_collector.parse_linkedin_jobs(alert, db)
                email_collector.process_pending_scrapes(db)

            job_id = db.execute("SELECT id FROM jobs").fetchone()[0]
            text = (Path(temp_dir) / f"{job_id}.txt").read_text(encoding="utf-8")
            db.close()

        self.assertIn("PUBLIC PAGE METADATA:", text)
        self.assertIn("LOCATION: Caruaru, Brazil", text)
        self.assertIn("WORKPLACE TYPE: Remote", text)

    def test_failed_scraping_stays_pending_then_retry_queues_complete_text(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db = store.connect(":memory:")
            alert = '<a href="https://www.linkedin.com/jobs/view/456">Job</a>'
            with patch.object(email_collector, "INBOX_DIR", Path(temp_dir)):
                email_collector.parse_linkedin_jobs(alert, db)
                with patch.object(
                    email_collector,
                    "fetch_public_job_text",
                    side_effect=email_collector.ScrapeError("HTTP 429"),
                ):
                    email_collector.process_pending_scrapes(db)
                status, = db.execute(
                    "SELECT status FROM jobs WHERE key = 'linkedin:456'"
                ).fetchone()
                self.assertEqual(status, "SCRAPE_PENDING")
                self.assertEqual(list(Path(temp_dir).glob("*.txt")), [])
                db.execute(
                    "UPDATE jobs SET scrape_next_attempt = NULL WHERE key = 'linkedin:456'"
                )
                db.commit()

                job_data = {
                    "title": "IT Support Analyst",
                    "company": "Example Inc",
                    "description": "Resolve support tickets and troubleshoot systems. " * 5,
                    "location": "Brazil",
                    "workplace_type": "Remote",
                }
                with (
                    patch.object(email_collector, "fetch_public_job_text", return_value=job_data),
                    patch.object(email_collector.time, "sleep"),
                ):
                    email_collector.process_pending_scrapes(db)

                status, = db.execute(
                    "SELECT status FROM jobs WHERE key = 'linkedin:456'"
                ).fetchone()
                self.assertEqual(status, "NEW")
                job_id, = db.execute(
                    "SELECT id FROM jobs WHERE key = 'linkedin:456'"
                ).fetchone()
                text = (Path(temp_dir) / f"{job_id}.txt").read_text(encoding="utf-8")
                self.assertIn("Resolve support tickets", text)
                self.assertNotIn("Link original para consulta", text)
                db.close()

    def test_processes_every_unread_email_and_marks_it_after_persisting(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            imap = FakeImap()
            with (
                patch.object(email_collector.imaplib, "IMAP4_SSL", return_value=imap),
                patch.object(email_collector, "EMAIL_USER", "user"),
                patch.object(email_collector, "EMAIL_PASS", "secret"),
                patch.object(email_collector, "DB_PATH", Path(temp_dir) / "jobs.db"),
                patch.object(email_collector, "queue_legacy_incomplete_linkedin_files"),
                patch.object(email_collector, "parse_linkedin_jobs") as parse_mock,
            ):
                processed = email_collector.fetch_linkedin_emails()

            self.assertEqual(processed, 7)
            searches = [event for event in imap.events if event[0] == "search"]
            self.assertIn("UNSEEN", searches[0][1])
            self.assertEqual(
                len([event for event in imap.events if event[0] == "fetch"]), 7
            )
            self.assertEqual(parse_mock.call_count, 7)
            for email_id in imap.ids:
                self.assertIn(
                    ("store", email_id, "+FLAGS", "\\Seen"),
                    imap.events,
                )


if __name__ == "__main__":
    unittest.main()
