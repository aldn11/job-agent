import unittest

import remot_parser
import store


class RemotParserTests(unittest.TestCase):
    def test_normalizes_and_deduplicates_official_listing_links(self):
        html = """
        <a href="https://www.workremot.com/jobs/support?utm_source=email#details">
          Support Specialist
        </a>
        <a href="https://workremot.com/jobs/support?ref=newsletter">
          Another label
        </a>
        """
        jobs = remot_parser.parse_remot_jobs(html)

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["url"], "https://workremot.com/jobs/support")
        self.assertEqual(jobs[0]["title"], "Support Specialist")
        self.assertEqual(jobs[0]["company"], "Empresa via Remōt")

    def test_rejects_similar_or_non_https_hosts(self):
        html = """
        <a href="https://workremot.com.attacker.example/jobs/fake">Fake</a>
        <a href="http://workremot.com/jobs/insecure">Insecure</a>
        <a href="https://attacker.example/?next=https://workremot.com/jobs/fake">
          Redirect
        </a>
        """
        self.assertEqual(remot_parser.parse_remot_jobs(html), [])

    def test_keeps_external_application_link_separate_from_listing(self):
        html = """
        <a href="https://workremot.com/jobs/support">Support Specialist</a>
        <a href="https://apply.example/form">Apply now</a>
        """
        jobs = remot_parser.parse_remot_jobs(html)

        self.assertEqual(jobs[0]["url"], "https://workremot.com/jobs/support")
        self.assertEqual(
            jobs[0]["application_url"], "https://apply.example/form"
        )

    def test_plain_text_email_can_contain_official_listing_url(self):
        jobs = remot_parser.parse_remot_jobs(
            "Open this listing: https://workremot.com/jobs/support?utm_campaign=mail."
        )
        self.assertEqual(jobs[0]["url"], "https://workremot.com/jobs/support")
        self.assertEqual(jobs[0]["title"], "Vaga Remōt")

    def test_retains_non_tracking_query_parameters_in_canonical_url(self):
        jobs = remot_parser.parse_remot_jobs(
            '<a href="https://workremot.com/jobs/?job_id=42&utm_source=email">'
            "Support</a>"
        )
        self.assertEqual(jobs[0]["url"], "https://workremot.com/jobs?job_id=42")
        self.assertNotEqual(
            store.job_key("https://workremot.com/jobs/ABC"),
            store.job_key("https://workremot.com/jobs/abc"),
        )


if __name__ == "__main__":
    unittest.main()
