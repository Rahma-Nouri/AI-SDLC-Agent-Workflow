"""Run approved test targets (pytest, or Vitest for the frontend) and report execution evidence."""

import os
import re
import subprocess
import sys
from pathlib import Path

from agents.agent_contracts import TestCheckResult, TestingAgentResult
from agents.frontend_environment import npm_command

# Letters, digits, and . _ - / only (see run_vitest).
SAFE_TEST_PATH = re.compile(r"^[A-Za-z0-9._/-]+$")


class TestingAgent:
    """Run tests locally without making approval decisions."""

    def __init__(
        self, project_root, timeout_seconds=300, output_limit=12000, python_executable=None,
        runner=None, platform=None,
    ):
        self.project_root = Path(project_root).resolve()
        # The pilot runs its tests with its own environment's Python, so its
        # packages stay separate from the workflow's (World 1 vs World 2).
        self.python_executable = str(python_executable or sys.executable)
        self.timeout_seconds = timeout_seconds
        self.output_limit = output_limit
        self.runner = runner  # a fake in the offline frontend tests; None = subprocess.run
        self.platform = platform

    def run_vitest(self, task_id, frontend_folder, test_paths):
        """Run Vitest in the frontend folder for the supplied test files.

        test_paths are relative to the pilot workspace, for example
        pilot/app/frontend/src/App.test.tsx; Vitest gets them relative to the
        frontend folder, where it runs.
        """

        if not isinstance(test_paths, list) or not test_paths:
            raise ValueError("Provide at least one test path.")

        frontend_root = (self.project_root / frontend_folder).resolve()
        filters = []

        for test_path in test_paths:
            if not isinstance(test_path, str) or not test_path.strip() or test_path.startswith("-"):
                raise ValueError(f"Invalid test path: {test_path!r}")

            resolved = (self.project_root / test_path).resolve()
            try:
                relative = resolved.relative_to(frontend_root)
            except ValueError as error:
                raise ValueError(f"Test path is outside the frontend folder: {test_path}") from error

            if not resolved.is_file():
                raise ValueError(f"Test path does not exist: {test_path}")

            # On Windows npx is a batch file, so cmd.exe reads these arguments,
            # and characters such as & | > ^ % could start another command.
            # The file names come from the agent, so only plain names are allowed.
            if not SAFE_TEST_PATH.match(relative.as_posix()):
                raise ValueError(f"Test path has characters that are not allowed: {test_path}")

            filters.append(relative.as_posix())

        # "--no": npx must not download Vitest when it is missing. That would
        # install a package the human never approved; the run fails instead.
        command_args = [npm_command("npx", self.platform), "--no", "vitest", "run", *filters]
        # CI=true: Vitest runs once and exits, and never waits for a key press.
        # NO_COLOR: plain text output, without color codes, in the saved evidence.
        environment = {**os.environ, "CI": "true", "NO_COLOR": "1"}

        check = self._execute("Vitest", command_args, frontend_root, environment)
        return self._result(task_id, check, "Vitest")

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

        # "-p no:cacheprovider" stops pytest from creating .pytest_cache, and
        # PYTHONDONTWRITEBYTECODE stops Python from creating __pycache__.
        # Running tests must not leave new files in the project it checks:
        # in the pilot workspace those files would look like unexpected changes.
        command_args = [
            self.python_executable,
            "-m",
            "pytest",
            *safe_paths,
            "-q",
            "-p",
            "no:cacheprovider",
        ]
        # PYTHONIOENCODING makes the test process write UTF-8, and we read it as
        # UTF-8, so non-English letters in paths or messages stay readable.
        environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8"}

        check = self._execute("Pytest", command_args, self.project_root, environment)
        return self._result(task_id, check, "Pytest")

    def _execute(self, check_name, command_args, cwd, environment):
        """Run one test command (no shell, time limit, UTF-8) and record what happened."""

        display_command = subprocess.list2cmdline(command_args)

        # subprocess.run is looked up here, at call time, so tests that replace
        # it (monkeypatch) still reach the replacement.
        run = self.runner or subprocess.run

        try:
            completed = run(
                command_args,
                cwd=cwd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_seconds,
                check=False,
                shell=False,
                env=environment,
            )
            output = (completed.stdout or "") + (completed.stderr or "")
            output = output[-self.output_limit :]

            return TestCheckResult(
                check_name=check_name,
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

            return TestCheckResult(
                check_name=check_name,
                command=display_command,
                passed=False,
                exit_code=None,
                output=output,
            )

        except OSError as error:
            return TestCheckResult(
                check_name=check_name,
                command=display_command,
                passed=False,
                exit_code=None,
                output=f"Could not start {check_name.lower()}: {error}",
            )

    @staticmethod
    def _result(task_id, check, check_name):
        return TestingAgentResult(
            task_id=task_id,
            summary=(
                f"{check_name} execution completed."
                if check.exit_code is not None
                else f"{check_name} execution did not complete."
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