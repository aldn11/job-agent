import os
import unittest
from unittest.mock import patch

from location_policy import classify_location
from wrapper import calculate_score_and_verdict


class LocationPolicyTests(unittest.TestCase):
    def setUp(self):
        self.base_evaluation = {
            "work_mode": "REMOTE_OPEN",
            "location_text": "Brazil",
            "location_evidence": "Remote within Brazil",
            "info_status": "COMPLETE",
            "hard_reject_flags": [],
            "mandatory_requirements": [],
            "nice_to_have": [],
            "role_match": "STRONG",
            "seniority_level": "MID",
            "years_required": 3,
            "spoken_english_emphasis": False,
        }

    def test_remote_country_is_eligible_with_exact_evidence(self):
        with patch.dict(os.environ, {"ALLOWED_REMOTE_COUNTRIES": "Brazil"}):
            result = classify_location(
                self.base_evaluation,
                "Job details. Remote within Brazil. Apply online.",
            )
        self.assertEqual(result, "ELIGIBLE")

    def test_unmentioned_remote_country_is_uncertain(self):
        evaluation = dict(self.base_evaluation, location_evidence="")
        with patch.dict(os.environ, {"ALLOWED_REMOTE_COUNTRIES": "Brazil"}):
            result = calculate_score_and_verdict(
                evaluation,
                "The position is remote. No country is listed.",
            )
        self.assertEqual(result["location_decision"], "UNCERTAIN")
        self.assertEqual(result["verdict"], "MAYBE")

    def test_explicit_restricted_country_is_rejected(self):
        evaluation = dict(
            self.base_evaluation,
            work_mode="REMOTE_RESTRICTED",
            location_text="United States only",
            location_evidence="Remote, United States only",
        )
        with patch.dict(os.environ, {"ALLOWED_REMOTE_COUNTRIES": "Brazil"}):
            result = calculate_score_and_verdict(
                evaluation,
                "Location: Remote, United States only",
            )
        self.assertEqual(result["location_decision"], "INELIGIBLE")
        self.assertEqual(result["verdict"], "SKIP")

    def test_on_site_location_uses_private_configured_allowlist(self):
        evaluation = dict(
            self.base_evaluation,
            work_mode="ONSITE_ALLOWED",
            location_text="Caruaru, PE",
            location_evidence="Hybrid in Caruaru, PE",
        )
        with patch.dict(os.environ, {"ALLOWED_ONSITE_LOCATIONS": "Caruaru;Gravatá"}):
            result = calculate_score_and_verdict(
                evaluation,
                "The role is Hybrid in Caruaru, PE.",
            )
        self.assertEqual(result["location_decision"], "ELIGIBLE")
        self.assertEqual(result["verdict"], "APPLY")

    def test_evidence_not_present_in_job_text_is_uncertain(self):
        with patch.dict(os.environ, {"ALLOWED_REMOTE_COUNTRIES": "Brazil"}):
            result = classify_location(
                self.base_evaluation,
                "Remote within another country.",
            )
        self.assertEqual(result, "UNCERTAIN")

    def test_country_name_inside_another_word_does_not_match_allowlist(self):
        evaluation = dict(
            self.base_evaluation,
            location_text="Brazilian Portuguese",
            location_evidence="Brazilian Portuguese is required",
        )
        with patch.dict(os.environ, {"ALLOWED_REMOTE_COUNTRIES": "Brazil"}):
            result = classify_location(
                evaluation,
                "Brazilian Portuguese is required.",
            )
        self.assertEqual(result, "UNCERTAIN")

    def test_location_not_supported_by_the_quote_cannot_make_remote_eligible(self):
        evaluation = dict(
            self.base_evaluation,
            location_text="Brazil",
            location_evidence="Remote anywhere in the United States",
        )
        with patch.dict(os.environ, {"ALLOWED_REMOTE_COUNTRIES": "Brazil"}):
            result = classify_location(
                evaluation,
                "Remote anywhere in the United States",
            )
        self.assertEqual(result, "UNCERTAIN")


if __name__ == "__main__":
    unittest.main()
