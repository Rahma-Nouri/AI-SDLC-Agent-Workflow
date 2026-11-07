"""Frontend environment: the pilot UI's npm packages, with human approval (P2.2c).

The frontend sibling of agents/pilot_environment.py. Before the frontend tests
run, the packages from <frontend folder>/package.json are installed into
<frontend folder>/node_modules (ignored by the pilot's .gitignore).

The packages are chosen by an AI agent. Installing an unknown or misspelled
package is a security risk, so a human approves the list first, but only when
the list has changed since the last approval. The install runs without the
packages' own install scripts.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

from agents.pilot_environment import EnvironmentResult

# Saved inside node_modules, so deleting the packages also forgets the approval.
APPROVAL_FILE_NAME = ".ai-sdlc-approved-packages.txt"

INSTALL_ARGS = ["install", "--ignore-scripts", "--no-package-lock", "--no-audit", "--no-fund"]

# npm installs packages from these package.json fields too, without them being
# in "dependencies" or "devDependencies". The human would not see them in the
# list, so a package.json that uses them is refused.
HIDDEN_INSTALL_FIELDS = (
    "optionalDependencies",
    "peerDependencies",
    "bundleDependencies",
    "bundledDependencies",
    "overrides",
    "workspaces",
)

# A package name from the npm registry, optionally with a scope: "vitest", "@vitejs/plugin-react".
PACKAGE_NAME = re.compile(r"^(@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*$", re.IGNORECASE)

# A registry version: "^3.2.4", "~1.0.0", ">=1 <2", "1.x", "latest", "*".
# No ":" (git:, file:, http:, npm: ...) and no "/" or "\" (paths, user/repo).
REGISTRY_VERSION = re.compile(r"^[A-Za-z0-9.^~<>=|*+\- ]*$")

GITIGNORE_LINES = ("node_modules", "dist")


def npm_command(name, platform=None):
    """'npm' or 'npx' as a program name that runs without a shell.

    On Windows these are batch files (npm.cmd, npx.cmd). Without a shell,
    Windows does not try the .cmd ending, so plain "npm" would not be found.
    """
    return f"{name}.cmd" if (platform or sys.platform) == "win32" else name


def registry_problem(name, version):
    """Why this dependency is not a plain registry package, or None if it is."""
    if not PACKAGE_NAME.match(name):
        return f"{name}: not a valid npm package name"
    if not isinstance(version, str) or not REGISTRY_VERSION.match(version):
        return f"{name}: '{version}' is not a registry version (git, file, link, URL or path)"
    return None


class FrontendEnvironment:
    def __init__(self, pilot_root, frontend_folder, timeout_seconds=900, ask=input, runner=None, platform=None):
        self.pilot_root = Path(pilot_root)
        self.frontend_root = self.pilot_root / frontend_folder
        self.timeout_seconds = timeout_seconds
        self.ask = ask
        self.runner = runner  # a fake in the offline tests; None = subprocess.run
        self.platform = platform

    @property
    def approval_file(self):
        return self.frontend_root / "node_modules" / APPROVAL_FILE_NAME

    def _result(self, status, packages, detail, installed_now=False):
        # The frontend uses no Python; "python" stays empty.
        return EnvironmentResult(status=status, python=None, packages=packages, installed_now=installed_now, detail=detail)

    def gitignore_problem(self):
        """node_modules/ and dist/ must be ignored before anything is installed."""
        gitignore = self.pilot_root / ".gitignore"
        lines = gitignore.read_text(encoding="utf-8").splitlines() if gitignore.is_file() else []
        ignored = {line.strip().strip("/") for line in lines}
        missing = [f"{name}/" for name in GITIGNORE_LINES if name not in ignored]
        if missing:
            return (
                f"The pilot .gitignore does not ignore {', '.join(missing)}. Installing now would put "
                "thousands of package files into the pilot's Git status. The Frontend Agent adds these lines in its patch."
            )
        return None

    def needed_packages(self):
        """'name@version' for every dependency and devDependency, sorted.

        Raises ValueError when package.json is missing, invalid, or not safe to install.
        """
        if (self.frontend_root / ".npmrc").exists():
            raise ValueError("A .npmrc file in the frontend folder could change where npm downloads from; it is not allowed.")

        package_json = self.frontend_root / "package.json"
        try:
            data = json.loads(package_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f"Could not read {package_json}: {error}") from error
        if not isinstance(data, dict):
            raise ValueError("package.json must be a JSON object.")

        hidden = [field for field in HIDDEN_INSTALL_FIELDS if field in data]
        if hidden:
            raise ValueError("package.json uses fields that install packages outside the approved list: " + ", ".join(hidden))

        packages, problems = [], []
        for field in ("dependencies", "devDependencies"):
            section = data.get(field) or {}
            if not isinstance(section, dict):
                raise ValueError(f"package.json '{field}' must be an object.")
            for name, version in section.items():
                problem = registry_problem(name, version)
                if problem:
                    problems.append(problem)
                packages.append(f"{name}@{version}")

        if problems:
            raise ValueError("Only packages from the npm registry are allowed: " + "; ".join(problems))
        return sorted(packages)

    def approved_packages(self):
        if not self.approval_file.is_file():
            return None
        return sorted(line for line in self.approval_file.read_text(encoding="utf-8").splitlines() if line)

    def prepare(self):
        """Make the frontend packages ready, asking the human when needed."""

        problem = self.gitignore_problem()
        if problem:
            return self._result("failed", [], problem)

        try:
            packages = self.needed_packages()
        except ValueError as error:
            return self._result("failed", [], str(error))

        if self.approved_packages() == packages:
            return self._result("ready", packages, "Packages unchanged since the last approval.")

        print("\nFRONTEND ENVIRONMENT - npm packages to install in the frontend folder:")
        for package in packages or ["(none)"]:
            print(f"  - {package}")
        print("These come from package.json (written by an agent). npm also installs the packages they depend on.")
        print("The packages' own install scripts are not run (--ignore-scripts).")
        decision = self.ask("Enter 'approve' to install them, or 'reject': ").strip().lower()

        if decision != "approve":
            return self._result("rejected", packages, "The human rejected the package list.")

        command = [npm_command("npm", self.platform), *INSTALL_ARGS]
        try:
            installed = (self.runner or subprocess.run)(
                command,
                cwd=self.frontend_root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_seconds,
                shell=False,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            return self._result("failed", packages, f"npm install could not run: {error}")

        if installed.returncode != 0:
            output = ((installed.stderr or "") + (installed.stdout or "")).strip()
            return self._result("failed", packages, "npm install failed: " + output[-2000:])

        # Remember exactly what was approved and installed.
        self.approval_file.parent.mkdir(parents=True, exist_ok=True)
        self.approval_file.write_text("\n".join(packages) + "\n", encoding="utf-8", newline="\n")
        return self._result("ready", packages, "Packages approved and installed.", installed_now=True)
