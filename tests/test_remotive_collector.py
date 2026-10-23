import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import remotive_collector
import store
from wrapper import extract_job_description


FIXTURE = (
    Path(__file__).parent / "fixtures" / "remotive_feed.xml"
).read_bytes()


class RemotiveCollectorTests(unittest.TestCase):
    def test_parse_reads_fields_canonicalizes_deduplicates_and_rejects_bad_jobs(self):
        jobs = remotive_collector.parse(FIXTURE)

        self.assertEqual(len(jobs), 2)
        support, sales = jobs
        self.assertEqual(support["title"], "Customer Support Specialist")
        self.assertEqual(support["company"], "Example Support Co.")
        self.assertEqual(support["location"], "Worldwide")
        self.assertEqual(support["job_type"], "full_time")
        self.assertEqual(support["category"], "Customer Service")
        self.assertEqual(
            support["published"], "2026-10-22T18:40:19+00:00"
        )
        self.assertIn("investigate reports", support["description"])
        self.assertEqual(
            support["url"],
            "https://remotive.com/remote-jobs/customer-support/customer-support-specialist-300001",
        )
        self.assertEqual(sales["category"], "Sales")
        self.assertNotIn("Fake listing", [job["title"] for job in jobs])
        self.assertNotIn("Short listing", [job["title"] for job in jobs])

    def test_category_feeds_default_to_general_or_use_verified_categories(self):
        self.assertEqual(
            remotive_collector.configured_feed_urls(""),
            ["https://remotive.com/remote-jobs/feed"],
        )
        self.assertEqual(
            remotive_collector.configured_feed_urls(
                "customer-service; sales ;sales"
            ),
            [
                "https://remotive.com/remote-jobs/customer-service/feed",
                "https://remotive.com/remote-jobs/sales/feed",
            ],
        )
        with self.assertRaisesRegex(ValueError, "não suportada"):
            remotive_collector.configured_feed_urls("../attacker")

    def test_canonical_url_requires_official_remotive_listing_url(self):
        self.assertEqual(
            remotive_collector.canonical_job_url(
                "https://www.remotive.com/remote-jobs/sales/job-12?utm_source=rss#apply"
            ),
            "https://remotive.com/remote-jobs/sales/job-12",
        )
        for url in (
            "http://remotive.com/remote-jobs/job-1",
            "https://remotive.com.attacker.example/remote-jobs/job-1",
            "https://remotive.com@attacker.example/remote-jobs/job-1",
            "https://attacker.example/?next=https://remotive.com/remote-jobs/job-1",
            "https://remotive.com:444/remote-jobs/job-1",
            "https://remotive.com/api/remote-jobs",
        ):
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    remotive_collector.canonical_job_url(url)

    def test_fetch_only_allows_general_or_category_feed_endpoints(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, size):
                self.requested_size = size
                return FIXTURE

        with patch.object(
            remotive_collector.url_policy,
            "open_allowed_url",
            return_value=Response(),
        ) as open_url:
            payload = remotive_collector.fetch(
                "https://remotive.com/remote-jobs/sales/feed"
            )
        self.assertEqual(payload, FIXTURE)
        self.assertEqual(
            open_url.call_args.args[0].full_url,
            "https://remotive.com/remote-jobs/sales/feed",
        )
        self.assertEqual(
            open_url.call_args.kwargs["allowed_domains"], ("remotive.com",)
        )
        for url in (
            "https://remotive.com/feed",
            "https://remotive.com/remote-jobs/sales",
            "https://remotive.com.attacker.example/remote-jobs/feed",
            "https://remotive.com/remote-jobs/sales/feed?next=https://attacker.example",
        ):
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    remotive_collector.fetch(url)

    def test_main_persists_attribution_and_inbox_jobs_once_without_page_fetch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            inbox = root / "inbox"
            db_path = root / "jobs.db"
            feed_urls = [
                "https://remotive.com/remote-jobs/customer-service/feed",
                "https://remotive.com/remote-jobs/sales/feed",
            ]
            with (
                patch.object(
                    remotive_collector, "configured_feed_urls", return_value=feed_urls
                ),
                patch.object(
                    remotive_collector,
                    "fetch",
                    side_effect=[FIXTURE, FIXTURE, FIXTURE, FIXTURE],
                ) as fetch,
                patch.object(remotive_collector, "INBOX_DIR", inbox),
                patch.object(remotive_collector, "DB_PATH", db_path),
                patch.object(
                    remotive_collector.url_policy,
                    "open_allowed_url",
                ) as open_url,
            ):
                remotive_collector.main()
                remotive_collector.main()

            db = store.connect(db_path)
            rows = db.execute(
                "SELECT id, source, url, company, title, status FROM jobs ORDER BY title"
            ).fetchall()
            db.close()
            self.assertEqual(len(rows), 2)
            self.assertTrue(all(row[1] == "Remotive" and row[5] == "NEW" for row in rows))
            support_row = next(row for row in rows if "Customer Support" in row[4])
            text = (inbox / f"{support_row[0]}.txt").read_text(encoding="utf-8")
            description = extract_job_description(text)
            self.assertEqual(len(list(inbox.glob("*.txt"))), 2)

        self.assertIn("SOURCE: Remotive (public RSS)", text)
        self.assertIn(f"URL: {support_row[2]}", text)
        self.assertIn("Remotive é a fonte", text)
        self.assertIn("PUBLISHED: 2026-10-22T18:40:19+00:00", text)
        self.assertIn("CATEGORY: Customer Service", text)
        self.assertIn("investigate reports", description)
        self.assertEqual(fetch.call_count, 4)
        open_url.assert_not_called()

    def test_main_applies_configured_local_filters(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with (
                patch.dict(
                    os.environ,
                    {
                        "REMOTIVE_INCLUDE_TERMS": "",
                        "REMOTIVE_EXCLUDE_TERMS": "",
                        "REMOTIVE_INCLUDE_CATEGORIES": "sales",
                        "REMOTIVE_INCLUDE_LOCATIONS": "",
                    },
                ),
                patch.object(
                    remotive_collector,
                    "configured_feed_urls",
                    return_value=["https://remotive.com/remote-jobs/feed"],
                ),
                patch.object(
                    remotive_collector, "fetch", return_value=FIXTURE
                ),
                patch.object(remotive_collector, "INBOX_DIR", root / "inbox"),
                patch.object(remotive_collector, "DB_PATH", root / "jobs.db"),
            ):
                remotive_collector.main()
            db = store.connect(root / "jobs.db")
            rows = db.execute("SELECT title FROM jobs").fetchall()
            db.close()
        self.assertEqual(rows, [("Sales Development Representative",)])


if __name__ == "__main__":
    unittest.main()
