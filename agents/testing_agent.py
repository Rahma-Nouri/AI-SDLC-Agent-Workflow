"""Run approved pytest targets and report execution evidence."""

import subprocess
import sys
from pathlib import Path

from agents.agent_contracts import TestCheckResult, TestingAgentResult


class TestingAgent:
    """Run tests locally without making approval decisions."""

    def __init__(self, project_root, timeout_seconds=300, output_limit=12000):
        self.project_root = Path(project_root).resolve()
        self.timeout_seconds = timeout_seconds
        self.output_limit = output_limit

    def run_pytest(self, task_id, test_paths):
        """Run pytest for the supplied, project-relative test paths."""

        if not isinstance(test_paths, list) or not test_paths:
            raise ValueError("Provide at least one project-relative test path.")

        safe_paths = []

        for test_path in test_paths:
            if not isinstance(test_path, str) or not test_path.strip():
                raise ValueError("Each test path must be a non-empty string.")

            candidate = Path(test_path)

            if candidate.is_absolute() or test_path.startswith("-"):
                raise ValueError(
                    f"Test path must be relative and cannot be an option: {test_path}"
                )

            resolved = (self.project_root / candidate).resolve()

            try:
                resolved.relative_to(self.project_root)
            except ValueError as error:
                raise ValueError(
                    f"Test path escapes the project folder: {test_path}"
                ) from error

            if not resolved.exists():
                raise ValueError(f"Test path does not exist: {test_path}")

            safe_paths.append(candidate.as_posix())

        command_args = [
            sys.executable,
            "-m",
            "pytest",
            *safe_paths,
            "-q",
        ]
        display_command = subprocess.list2cmdline(command_args)

        try:
            completed = subprocess.run(
                command_args,
                cwd=self.project_root,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
                shell=False,
            )
            output = (completed.stdout or "") + (completed.stderr or "")
            output = output[-self.output_limit :]

            check = TestCheckResult(
                check_name="Pytest",
                command=display_command,
                passed=completed.returncode == 0,
                exit_code=completed.returncode,
                output=output,
            )

        except subprocess.TimeoutExpired as error:
            output = self._as_text(error.stdout) + self._as_text(error.stderr)
            output = (
                output[-self.output_limit :]
                + f"\nTest run timed out after {self.timeout_seconds} seconds."
            )

            check = TestCheckResult(
                check_name="Pytest",
                command=display_command,
                passed=False,
                exit_code=None,
                output=output,
            )

        except OSError as error:
            check = TestCheckResult(
                check_name="Pytest",
                command=display_command,
                passed=False,
                exit_code=None,
                output=f"Could not start pytest: {error}",
            )

        return TestingAgentResult(
            task_id=task_id,
            summary=(
                "Pytest execution completed."
                if check.exit_code is not None
                else "Pytest execution did not complete."
            ),
            checks=[check],
        )

    @staticmethod
    def _as_text(value):
        if value is None:
            return ""
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value)
    __test__ = False