import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import collector


class CollectorTests(unittest.TestCase):
    def test_parse_keeps_only_supported_unique_feed_links(self):
        feed = b"""<?xml version="1.0"?>
        <rss><channel>
          <item>
            <title>Acme &amp; Co: Technical Support Agent</title>
            <link>https://weworkremotely.com/remote-jobs/support-agent</link>
            <region>Worldwide</region>
            <description><![CDATA[<p>Support &amp; service</p>]]></description>
          </item>
          <item>
            <title>Duplicate: Help Desk</title>
            <link>https://weworkremotely.com/remote-jobs/support-agent</link>
          </item>
          <item>
            <title>External: Customer Support</title>
            <link>https://example.com/jobs/123</link>
          </item>
        </channel></rss>"""

        jobs = collector.parse(feed)

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["title"], "Acme & Co: Technical Support Agent")
        self.assertEqual(jobs[0]["description"], "Support & service")
        self.assertEqual(jobs[0]["region"], "Worldwide")

    def test_title_filter_and_split_keep_target_roles(self):
        self.assertTrue(collector.keep({"title": "Technical Support Specialist"}))
        self.assertFalse(collector.keep({"title": "Senior Customer Support Manager"}))
        self.assertEqual(
            collector.split_title("Acme & Co: Support Engineer"),
            ("Acme & Co", "Support Engineer"),
        )
        self.assertEqual(collector.split_title("Support Engineer"), ("", "Support Engineer"))

    def test_save_text_writes_a_complete_inbox_record(self):
        job = {
            "link": "https://weworkremotely.com/remote-jobs/support-agent",
            "region": "Worldwide",
            "description": "Support customers and troubleshoot technical issues.",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(collector, "INBOX_DIR", Path(temp_dir)):
                path = collector.save_text(
                    "job123", job, "Acme", "Support Agent", "possible duplicate"
                )
                text = path.read_text(encoding="utf-8")

        self.assertIn("SOURCE: We Work Remotely", text)
        self.assertIn("COMPANY: Acme", text)
        self.assertIn("TITLE: Support Agent", text)
        self.assertIn("NOTE: possible duplicate", text)
        self.assertIn("JOB TEXT:\nSupport customers", text)

    def test_fetch_uses_guarded_request_and_rejects_oversized_feed(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, size):
                self.requested_size = size
                return b"12345"

        response = Response()
        with (
            patch.object(collector, "MAX_FEED_BYTES", 4),
            patch.object(
                collector.url_policy, "open_allowed_url", return_value=response
            ) as open_url,
        ):
            with self.assertRaisesRegex(ValueError, "tamanho máximo"):
                collector.fetch("https://weworkremotely.com/feed.rss")

        self.assertEqual(response.requested_size, 5)
        self.assertEqual(open_url.call_args.kwargs["timeout"], 20)
        self.assertEqual(
            open_url.call_args.kwargs["allowed_domains"], ("weworkremotely.com",)
        )


if __name__ == "__main__":
    unittest.main()
