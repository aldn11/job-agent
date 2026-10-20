import copy
import unittest

from wrapper import calculate_score_and_verdict, validate_evaluation_data


class EvaluationValidationTests(unittest.TestCase):
    def setUp(self):
        self.job_text = (
            "JOB TEXT:\nSupport users, resolve technical issues, and document "
            "incidents. Remote within Brazil. " * 5
        )
        self.evaluation = {
            "title": "Support Specialist",
            "company": "Example Inc",
            "source": "LinkedIn",
            "url": "https://www.linkedin.com/jobs/view/123",
            "text_source": "company_site",
            "info_status": "COMPLETE",
            "work_mode": "REMOTE_OPEN",
            "location_text": "Brazil",
            "role_match": "STRONG",
            "location_evidence": "Remote within Brazil.",
            "years_required": 3,
            "seniority_level": "MID",
            "english_level_required": "NOT_STATED",
            "spoken_english_emphasis": False,
            "pay_currency": "NOT_STATED",
            "mandatory_requirements": [
                {
                    "requirement": "Support experience",
                    "status": "MET",
                    "quote": "Support users",
                }
            ],
            "nice_to_have": [],
            "hard_reject_flags": [],
            "prompt_injection_detected": False,
            "red_flags": [],
            "reasons_pt": ["A vaga corresponde ao perfil."],
            "gaps_pt": [],
            "interview_risks_pt": [],
        }

    def test_accepts_complete_schema_and_exact_job_quotes(self):
        result = validate_evaluation_data(self.evaluation, self.job_text)
        self.assertIs(result, self.evaluation)

    def test_rejects_missing_fields_and_unexpected_fields(self):
        missing = copy.deepcopy(self.evaluation)
        del missing["work_mode"]
        with self.assertRaisesRegex(ValueError, "campos ausentes"):
            validate_evaluation_data(missing, self.job_text)

        unexpected = dict(self.evaluation, fabricated_score=100)
        with self.assertRaisesRegex(ValueError, "campos inesperados"):
            validate_evaluation_data(unexpected, self.job_text)

    def test_rejects_invalid_types_and_quotes_not_found_in_job(self):
        invalid = dict(self.evaluation, spoken_english_emphasis="false")
        with self.assertRaisesRegex(ValueError, "booleano"):
            validate_evaluation_data(invalid, self.job_text)

        invalid = copy.deepcopy(self.evaluation)
        invalid["mandatory_requirements"][0]["quote"] = "invented quote"
        with self.assertRaisesRegex(ValueError, "citação ausente"):
            validate_evaluation_data(invalid, self.job_text)

    def test_rejects_url_that_does_not_match_ingested_job(self):
        job_text = (
            "SOURCE: LinkedIn Email Alert\n"
            "URL: https://www.linkedin.com/jobs/view/123\n"
            + self.job_text
        )
        invalid = dict(
            self.evaluation,
            url="https://attacker.example/fake-job",
        )
        with self.assertRaisesRegex(ValueError, "URL da resposta"):
            validate_evaluation_data(invalid, job_text)

    def test_null_years_required_does_not_break_deterministic_scoring(self):
        evaluation = dict(self.evaluation, years_required=None)
        result = calculate_score_and_verdict(evaluation, self.job_text)
        self.assertIsInstance(result["score"], int)


if __name__ == "__main__":
    unittest.main()
