import copy
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import wrapper
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

    def test_hard_reject_validation_reports_unknown_flag_and_allowed_codes(self):
        invalid = copy.deepcopy(self.evaluation)
        invalid["hard_reject_flags"] = ["LANGUAGE_REQUIREMENT"]
        with self.assertRaisesRegex(
            ValueError, "LANGUAGE_REQUIREMENT.*LANGUAGE_REQUIRED"
        ):
            validate_evaluation_data(invalid, self.job_text)

    def test_location_validation_reports_nonliteral_evidence(self):
        invalid = dict(self.evaluation, location_evidence="Remote in Brazil")
        with self.assertRaisesRegex(
            ValueError, "Valor recebido.*Remote in Brazil"
        ):
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

    def test_rejects_excessively_nested_json_from_vargas(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            job_file = Path(temp_dir) / "job.txt"
            job_file.write_text(self.job_text, encoding="utf-8")
            result = subprocess.CompletedProcess(
                args=["docker"],
                returncode=0,
                stdout="[" * 2000 + "]" * 2000,
                stderr="",
            )
            with patch.object(wrapper.subprocess, "run", return_value=result):
                with self.assertRaisesRegex(ValueError, "objeto JSON"):
                    wrapper.run_vargas(job_file)

    def test_invalid_json_response_includes_bounded_stdout_and_stderr(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            job_file = Path(temp_dir) / "job.txt"
            job_file.write_text(self.job_text, encoding="utf-8")
            terminal_error = "FINAL: Hermes returned no evaluation payload"
            stdout = "session_id: test-session\n" + "progress " * 500 + terminal_error
            result = subprocess.CompletedProcess(
                args=["docker"],
                returncode=0,
                stdout=stdout,
                stderr="warning: response was empty",
            )
            with patch.object(wrapper.subprocess, "run", return_value=result):
                with self.assertRaises(ValueError) as raised:
                    wrapper.run_vargas(job_file)

        message = str(raised.exception)
        self.assertIn("código de saída 0", message)
        self.assertIn("JSON válido", message)
        self.assertIn("session_id: test-session", message)
        self.assertIn("caracteres omitidos", message)
        self.assertIn(terminal_error, message)
        self.assertIn("warning: response was empty", message)

    def test_hermes_nonzero_exit_includes_bounded_stdout_and_stderr(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            job_file = Path(temp_dir) / "job.txt"
            job_file.write_text(self.job_text, encoding="utf-8")
            error = subprocess.CalledProcessError(
                1,
                ["docker", "exec"],
                output="Hermes startup output",
                stderr="profile was not found",
            )
            with patch.object(wrapper.subprocess, "run", side_effect=error):
                with self.assertRaises(RuntimeError) as raised:
                    wrapper.run_vargas(job_file)

        message = str(raised.exception)
        self.assertIn("código de saída 1", message)
        self.assertIn("stderr:\nprofile was not found", message)
        self.assertIn("stdout:\nHermes startup output", message)

    def test_hermes_timeout_includes_partial_process_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            job_file = Path(temp_dir) / "job.txt"
            job_file.write_text(self.job_text, encoding="utf-8")
            error = subprocess.TimeoutExpired(
                ["docker", "exec"], 10, output=b"partial stdout", stderr=b"partial stderr"
            )
            with patch.object(wrapper.subprocess, "run", side_effect=error):
                with self.assertRaises(RuntimeError) as raised:
                    wrapper.run_vargas(job_file)
        self.assertIn("partial stderr", str(raised.exception))
        self.assertIn("partial stdout", str(raised.exception))

    def test_hermes_diagnostic_output_is_truncated(self):
        terminal_error = "FINAL HERMES ERROR: generation failed"
        raw = (
            "Hermes session started\n"
            + "middle output " * 500
            + terminal_error
        )
        output = wrapper._diagnostic_output(raw)
        self.assertLessEqual(len(output), wrapper.HERMES_DIAGNOSTIC_LIMIT)
        self.assertIn("Hermes session started", output)
        self.assertIn("caracteres omitidos", output)
        self.assertIn(terminal_error, output)

    def test_nonzero_hermes_exit_is_failure_even_when_stdout_is_valid_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            job_file = Path(temp_dir) / "job.txt"
            job_file.write_text(self.job_text, encoding="utf-8")
            error = subprocess.CalledProcessError(
                1,
                ["docker", "exec"],
                output='{"result":"looks valid"}',
                stderr="process failed after producing output",
            )
            with patch.object(wrapper.subprocess, "run", side_effect=error):
                with self.assertRaises(RuntimeError) as raised:
                    wrapper.run_vargas(job_file)
        self.assertIn("código de saída 1", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
