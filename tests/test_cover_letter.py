import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cover_letter import generate_cover_letter, save_cover_letter


class CoverLetterTests(unittest.TestCase):
    def test_generate_requests_job_language_and_candidate_evidence(self):
        result = subprocess.CompletedProcess(
            args=["docker"],
            returncode=0,
            stdout=json.dumps(
                {"cover_letter": "Prezada equipe de contratação,\n\n" + ("Experiência comprovada. " * 5)}
            ),
            stderr="",
        )
        with patch("cover_letter.subprocess.run", return_value=result) as run:
            letter = generate_cover_letter(
                "JOB TEXT:\nEsta vaga de suporte exige atendimento técnico. " * 5,
                {"title": "Analista de Suporte", "company": "Example"},
            )

        self.assertTrue(letter.startswith("Prezada equipe"))
        prompt = run.call_args.kwargs["input"]
        self.assertIn("same language as the job posting", prompt)
        self.assertIn("Do not invent", prompt)
        self.assertIn("untrusted data", prompt)
        self.assertIn("Do not send an application", prompt)

    def test_rejects_empty_job_and_invalid_short_letter(self):
        with self.assertRaises(ValueError):
            generate_cover_letter("URL only", {})

        result = subprocess.CompletedProcess(
            args=["docker"],
            returncode=0,
            stdout=json.dumps({"cover_letter": "Short"}),
            stderr="",
        )
        with patch("cover_letter.subprocess.run", return_value=result):
            with self.assertRaisesRegex(ValueError, "curta demais"):
                generate_cover_letter("Job description " * 10, {})

    def test_rejects_excessively_nested_json_from_vargas(self):
        result = subprocess.CompletedProcess(
            args=["docker"],
            returncode=0,
            stdout="[" * 2000 + "]" * 2000,
            stderr="",
        )
        with patch("cover_letter.subprocess.run", return_value=result):
            with self.assertRaisesRegex(ValueError, "Vargas retornou campos inválidos"):
                generate_cover_letter("Job description " * 10, {})

    def test_saves_draft_atomically_in_private_output_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = save_cover_letter("job-123", "Draft letter", Path(temp_dir))
            self.assertEqual(path.name, "cover-letter-job-123.txt")
            self.assertEqual(path.read_text(encoding="utf-8"), "Draft letter\n")
            with self.assertRaises(ValueError):
                save_cover_letter("../bad", "Draft letter", Path(temp_dir))


if __name__ == "__main__":
    unittest.main()
