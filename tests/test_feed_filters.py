import unittest

import feed_filters


class FeedFiltersTests(unittest.TestCase):
    def setUp(self):
        self.jobs = [
            {
                "title": "Customer Support Specialist",
                "description": "Help customers and investigate product reports.",
                "job_type": "Full time",
                "category": "Customer Service",
                "categories": [],
                "location": "Worldwide",
            },
            {
                "title": "Sales Manager",
                "description": "Lead a distributed sales team.",
                "job_type": "Contract",
                "category": "Sales",
                "categories": [],
                "location": "Canada",
            },
            {
                "title": "Engineering Lead",
                "description": "Build dependable systems.",
                "job_type": "Full time",
                "category": "",
                "categories": [],
                "location": "",
            },
        ]

    def test_empty_filters_preserve_all_jobs(self):
        self.assertEqual(feed_filters.filter_jobs(self.jobs), self.jobs)

    def test_terms_use_or_and_exclusions_win(self):
        filtered = feed_filters.filter_jobs(
            self.jobs,
            include_terms="customer; distributed",
            exclude_terms="SPECIALIST",
        )
        self.assertEqual([job["title"] for job in filtered], ["Sales Manager"])

    def test_different_filter_options_use_and_semantics(self):
        filtered = feed_filters.filter_jobs(
            self.jobs,
            include_terms="support; sales",
            include_categories="customer service; sales",
            include_locations="WORLD",
        )
        self.assertEqual(
            [job["title"] for job in filtered], ["Customer Support Specialist"]
        )

    def test_categories_can_be_a_list_and_match_exactly(self):
        jobs = [
            {"title": "Design role", "categories": ["Design", "Product"]},
            {"title": "Product designer", "categories": ["Product Design"]},
        ]
        self.assertEqual(
            [
                job["title"]
                for job in feed_filters.filter_jobs(
                    jobs, include_categories="product"
                )
            ],
            ["Design role"],
        )

    def test_missing_location_fails_a_configured_location_filter(self):
        filtered = feed_filters.filter_jobs(self.jobs, include_locations="Remote")
        self.assertEqual(filtered, [])


if __name__ == "__main__":
    unittest.main()
