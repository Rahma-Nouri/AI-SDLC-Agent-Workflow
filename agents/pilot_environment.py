"""Pilot Environment: the pilot's own Python environment, with human-approved packages.

World 2 (the pilot) keeps its packages separate from the workflow (World 1).
Before tests run, this step makes sure the pilot workspace has its own
virtual environment (.venv, ignored by the pilot's .gitignore) with the
packages from the pilot's requirements.txt installed.

The packages are chosen by an AI agent. Installing an unknown or misspelled
package is a security risk, so a human approves the list before anything is
installed, but only when the list has changed since the last approval.
"""

import subprocess
import sys
from importlib import metadata
from pathlib import Path
from typing import Literal

from agents.agent_contracts import ContractModel

# Saved inside .venv, so deleting the environment also forgets the approval.
APPROVAL_FILE_NAME = "ai-sdlc-approved-packages.txt"


class EnvironmentResult(ContractModel):
    status: Literal["ready", "rejected", "failed"]
    python: str | None
    packages: list[str]  # what the pilot needs: requirements.txt + pytest
    installed_now: bool  # True when this run installed packages
    detail: str


def read_requirements(requirements_path):
    """Package lines from requirements.txt, without comments or empty lines."""
    path = Path(requirements_path)
    if not path.is_file():
        return []
    packages = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            packages.append(line)
    return packages


def workflow_pytest_requirement():
    """The tests run with the same pytest version as the workflow itself."""
    try:
        return f"pytest=={metadata.version('pytest')}"
    except metadata.PackageNotFoundError:
        return "pytest"


class PilotEnvironment:
    def __init__(self, pilot_root, base_python=sys.executable, timeout_seconds=900, ask=input):
        self.pilot_root = Path(pilot_root)
        self.venv_dir = self.pilot_root / ".venv"
        self.base_python = base_python
        self.timeout_seconds = timeout_seconds
        self.ask = ask

    @property
    def python(self):
        folder = "Scripts" if sys.platform == "win32" else "bin"
        name = "python.exe" if sys.platform == "win32" else "python"
        return self.venv_dir / folder / name

    @property
    def approval_file(self):
        return self.venv_dir / APPROVAL_FILE_NAME

    def needed_packages(self):
        # pytest is a workflow tool, not chosen by the agent, but it is shown
        # in the same list so the human sees everything that gets installed.
        return sorted(set(read_requirements(self.pilot_root / "requirements.txt")) | {workflow_pytest_requirement()})

    def approved_packages(self):
        if not self.approval_file.is_file():
            return None
        return sorted(line for line in self.approval_file.read_text(encoding="utf-8").splitlines() if line)

    def _run(self, *args):
        return subprocess.run(
            list(args),
            cwd=self.pilot_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=self.timeout_seconds,
            shell=False,
            check=False,
        )

    def prepare(self):
        """Make the pilot environment ready, asking the human when needed."""

        packages = self.needed_packages()

        # A line starting with "-" is a pip option (for example --index-url,
        # which would download from another server). Only package names allowed.
        options = [package for package in packages if package.startswith("-")]
        if options:
            return EnvironmentResult(
                status="failed", python=None, packages=packages, installed_now=False,
                detail="requirements.txt contains pip options, which are not allowed: " + ", ".join(options),
            )

        if self.python.is_file() and self.approved_packages() == packages:
            return EnvironmentResult(
                status="ready", python=str(self.python), packages=packages,
                installed_now=False, detail="Packages unchanged since the last approval.",
            )

        print("\nPILOT ENVIRONMENT - packages to install in the pilot's own .venv:")
        for package in packages:
            print(f"  - {package}")
        print("These come from the pilot's requirements.txt (written by an agent), plus pytest.")
        decision = self.ask("Enter 'approve' to install them, or 'reject': ").strip().lower()

        if decision != "approve":
            return EnvironmentResult(
                status="rejected", python=None, packages=packages,
                installed_now=False, detail="The human rejected the package list.",
            )

        try:
            if not self.python.is_file():
                created = self._run(self.base_python, "-m", "venv", str(self.venv_dir))
                if created.returncode != 0:
                    raise RuntimeError(f"Could not create .venv: {created.stderr.strip()}")

            installed = self._run(str(self.python), "-m", "pip", "install", "--disable-pip-version-check", *packages)
            if installed.returncode != 0:
                raise RuntimeError("pip install failed: " + (installed.stderr or installed.stdout).strip()[-2000:])
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
            return EnvironmentResult(
                status="failed", python=None, packages=packages,
                installed_now=False, detail=str(error),
            )

        # Remember exactly what was approved and installed.
        self.approval_file.write_text("\n".join(packages) + "\n", encoding="utf-8", newline="\n")
        return EnvironmentResult(
            status="ready", python=str(self.python), packages=packages,
            installed_now=True, detail="Packages approved and installed.",
        )
