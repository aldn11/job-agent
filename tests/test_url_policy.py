import unittest
from types import SimpleNamespace

import url_policy


class UrlPolicyTests(unittest.TestCase):
    def test_generic_https_job_links_allow_external_sites_but_reject_bad_urls(self):
        self.assertEqual(
            url_policy.validate_https_url("https://careers.example.org/jobs/123"),
            "https://careers.example.org/jobs/123",
        )
        for url in (
            "http://careers.example.org/jobs/123",
            "javascript:alert(1)",
            "https://user:password@careers.example.org/jobs/123",
            "https://careers.example.org:444/jobs/123",
            "https:///jobs/123",
            "https://careers.example.org/jobs/a b",
        ):
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    url_policy.validate_https_url(url)

    def test_allows_only_https_urls_on_supported_domains(self):
        for url in (
            "https://linkedin.com/jobs/view/123",
            "https://www.linkedin.com/jobs/view/123",
            "https://weworkremotely.com/remote-jobs/example",
            "https://www.weworkremotely.com/categories/jobs.rss",
            "https://workremot.com/jobs/example",
            "https://www.workremot.com/jobs/example",
            "https://jobspresso.co/job/example",
            "https://www.jobspresso.co/job/example",
            "https://remotive.com/remote-jobs/feed",
            "https://www.remotive.com/remote-jobs/sales/feed",
        ):
            with self.subTest(url=url):
                self.assertEqual(url_policy.validate_allowed_url(url), url)

    def test_rejects_untrusted_hosts_and_url_variants(self):
        for url in (
            "http://linkedin.com/jobs/view/123",
            "https://linkedin.com.attacker.example/jobs/view/123",
            "https://linkedin.com@attacker.example/jobs/view/123",
            "https://attacker.example/?next=https://linkedin.com",
            "https://linkedin.com:444/jobs/view/123",
            "https://workremot.com.attacker.example/jobs/example",
            "https://workremot.com@attacker.example/jobs/example",
            "https://jobspresso.co.attacker.example/job/example",
            "https://jobspresso.co@attacker.example/job/example",
            "https://remotive.com.attacker.example/remote-jobs/job",
            "https://remotive.com@attacker.example/remote-jobs/job",
        ):
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    url_policy.validate_allowed_url(url)

    def test_redirect_handler_rejects_external_redirect_before_following(self):
        handler = url_policy._AllowedRedirectHandler(("linkedin.com",))
        request = SimpleNamespace(full_url="https://www.linkedin.com/jobs/view/123")
        with self.assertRaises(ValueError):
            handler.redirect_request(
                request,
                None,
                302,
                "Found",
                {},
                "https://attacker.example/collect",
            )


if __name__ == "__main__":
    unittest.main()
