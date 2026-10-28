from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import subprocess
import unittest
from unittest.mock import patch

from scripts import validate


class ValidateScriptTests(unittest.TestCase):
    def test_hermes_runtime_check_uses_strict_reader_and_reports_valid_registry(self):
        result = subprocess.CompletedProcess(
            args=["docker"],
            returncode=0,
            stdout="active_sessions.json válido; leases=0\n",
            stderr="",
        )
        with (
            patch.object(validate.subprocess, "run", return_value=result) as run,
            redirect_stdout(StringIO()),
        ):
            self.assertTrue(
                validate.check_hermes_runtime("hermes-test", "vargas")
            )

        command = run.call_args.args[0]
        self.assertEqual(command[:5], ["docker", "exec", "-u", "hermes", "hermes-test"])
        self.assertIn("_read_entries", command[7])
        self.assertEqual(
            command[-1],
            "/opt/data/profiles/vargas/runtime/active_sessions.json",
        )
        self.assertTrue(run.call_args.kwargs["capture_output"])
        self.assertEqual(run.call_args.kwargs["timeout"], 15)

    def test_hermes_runtime_check_fails_on_registry_error(self):
        result = subprocess.CompletedProcess(
            args=["docker"],
            returncode=1,
            stdout="",
            stderr="UnicodeDecodeError: invalid start byte",
        )
        with (
            patch.object(validate.subprocess, "run", return_value=result),
            redirect_stderr(StringIO()),
        ):
            self.assertFalse(validate.check_hermes_runtime())

    def test_hermes_runtime_check_fails_when_docker_is_unavailable(self):
        with patch.object(
            validate.subprocess, "run", side_effect=FileNotFoundError("docker")
        ), redirect_stderr(StringIO()):
            self.assertFalse(validate.check_hermes_runtime())


if __name__ == "__main__":
    unittest.main()
