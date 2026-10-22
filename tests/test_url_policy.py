import unittest
from types import SimpleNamespace

import url_policy


class UrlPolicyTests(unittest.TestCase):
    def test_allows_only_https_urls_on_supported_domains(self):
        for url in (
            "https://linkedin.com/jobs/view/123",
            "https://www.linkedin.com/jobs/view/123",
            "https://weworkremotely.com/remote-jobs/example",
            "https://www.weworkremotely.com/categories/jobs.rss",
            "https://workremot.com/jobs/example",
            "https://www.workremot.com/jobs/example",
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
