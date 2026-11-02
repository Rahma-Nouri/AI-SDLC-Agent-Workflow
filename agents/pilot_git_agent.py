"""Pilot Git Agent: apply an agent's patch on the agent's own branch.

World 2 (the pilot) is built only by agents. This step takes a patch proposed
by the Backend Developer Agent and, inside the pilot workspace:

1. checks that the workspace is clean and on the pilot base branch,
2. creates a new branch for this attempt (for example agent/T1-attempt-1),
3. applies the patch there and commits only the declared files,
4. commits the run's records (patch proposal, report, refreshed snapshot).

It never pushes and never merges: that happens only after the human's H3
decision. "Reject" stays safe because everything lives on the agent's branch.
"""

import subprocess
from pathlib import Path

from agents.agent_contracts import ContractModel

AGENT_NAME = "AI-SDLC Backend Agent"
AGENT_EMAIL = "backend-agent@ai-sdlc.local"
BASE_BRANCH = "pilot/main"
BRANCH_PREFIX = "agent/"

# Everything under outputs/ is a run record (proposals, reports, snapshot).
# Records never block a run, and the agent commits them on its branch, so
# evidence of earlier failed attempts is kept, not lost.
RECORDS_PREFIX = "outputs/"


class AgentCommitResult(ContractModel):
    """What the Pilot Git Agent did, for the next workflow steps and the report."""

    task_id: str
    branch: str
    base_branch: str
    code_commit: str
    records_commit: str | None
    committed_files: list[str]


class PilotGitError(RuntimeError):
    """Raised when the Git step must stop; the workspace is left as it was."""


class FrozenFilesError(PilotGitError):
    """Raised when a fix tries to change files that are frozen (the tests)."""


class PilotGitAgent:
    def __init__(self, pilot_root, workflow_root=None, timeout_seconds=60):
        self.pilot_root = Path(pilot_root).resolve()
        self.workflow_root = Path(workflow_root).resolve() if workflow_root else None
        self.timeout_seconds = timeout_seconds

    # --- small helpers -----------------------------------------------------

    def _git(self, *args, check=True):
        """Run one git command inside the pilot workspace (no shell)."""
        completed = subprocess.run(
            # core.quotepath=off: print file names as they are (no octal escapes).
            ["git", "-c", "core.quotepath=off", "-C", str(self.pilot_root), *args],
            capture_output=True,
            text=True,
            # Git writes paths as UTF-8. Without this, Windows Python reads them
            # with its default code page, so a folder like "Budapesti Műszaki
            # és ..." becomes garbled text and no longer matches the real path.
            encoding="utf-8",
            errors="replace",
            timeout=self.timeout_seconds,
            shell=False,
            check=False,
        )
        if check and completed.returncode != 0:
            raise PilotGitError(
                f"git {' '.join(args)} failed: "
                f"{(completed.stderr or completed.stdout).strip()}"
            )
        return completed

    def _changed_paths(self):
        """Paths that differ from the last commit (modified or untracked)."""
        output = self._git("status", "--porcelain", "--untracked-files=all").stdout
        # Each line looks like "XY path"; the path starts at column 4.
        return sorted(line[3:].strip().strip('"') for line in output.splitlines() if line.strip())

    def _relative(self, path):
        return Path(path).resolve().relative_to(self.pilot_root).as_posix()

    # --- safety checks -----------------------------------------------------

    def ensure_ready(self, allowed_paths=()):
        """Stop unless the pilot workspace is safe to work in."""

        if self.workflow_root and self.pilot_root == self.workflow_root:
            raise PilotGitError("Refusing to work in the workflow (World 1) folder.")

        top = Path(self._git("rev-parse", "--show-toplevel").stdout.strip())
        # samefile() compares the real folders, so slash style or letter case
        # in the path text (C:/ vs C:\\) cannot cause a false mismatch.
        if not top.is_dir() or not top.samefile(self.pilot_root):
            raise PilotGitError(f"The pilot workspace is not a Git worktree root: {self.pilot_root}")

        branch = self._git("branch", "--show-current").stdout.strip()
        if branch != BASE_BRANCH:
            raise PilotGitError(
                f"The pilot workspace is on '{branch}', expected '{BASE_BRANCH}'. "
                "An earlier agent branch may still wait for an H3 decision."
            )

        unexpected = [
            path for path in self._changed_paths()
            if path not in set(allowed_paths) and not path.startswith(RECORDS_PREFIX)
        ]
        if unexpected:
            raise PilotGitError(
                "The pilot workspace has unexpected changes: " + ", ".join(unexpected)
            )

    def next_branch_name(self, task_id):
        """agent/<task>-attempt-<n>, where n counts earlier attempts."""
        existing = self._git("branch", "--list", f"{BRANCH_PREFIX}{task_id}-attempt-*").stdout
        attempts = [line for line in existing.splitlines() if line.strip()]
        return f"{BRANCH_PREFIX}{task_id}-attempt-{len(attempts) + 1}"

    # --- the main step -----------------------------------------------------

    def apply_patch_on_new_branch(self, task_id, patch_path, changed_files, summary, record_paths=()):
        """Create the agent branch, apply the patch, and commit it.

        changed_files: the files the agent declared in its result.
        record_paths: run records to commit too (patch, report, snapshot).
        """

        patch_path = Path(patch_path).resolve()
        records = [self._relative(path) for path in record_paths]
        declared = sorted(set(changed_files))

        self.ensure_ready(allowed_paths=records)

        # Check before creating anything: a patch that does not apply leaves no trace.
        self._git("apply", "--check", str(patch_path))

        branch = self.next_branch_name(task_id)
        if not branch.startswith(BRANCH_PREFIX):
            raise PilotGitError("Agents may only commit on their own agent/ branches.")

        self._git("switch", "-c", branch)
        state = {"applied": False, "committed": False}

        try:
            self._git("apply", str(patch_path))
            state["applied"] = True

            # The patch must change exactly the declared files, nothing else.
            touched = [
                path for path in self._changed_paths()
                if path not in set(records) and not path.startswith(RECORDS_PREFIX)
            ]
            if touched != declared:
                raise PilotGitError(
                    f"The patch changed {touched}, but the agent declared {declared}."
                )

            identity = ["-c", f"user.name={AGENT_NAME}", "-c", f"user.email={AGENT_EMAIL}"]

            self._git("add", "--", *declared)
            self._git(
                *identity, "commit", "-m", f"[{task_id}] {summary}",
                "-m", "Proposed by the Backend Developer Agent. Not approved yet: waiting for tests and the H3 decision.",
            )
            code_commit = self._git("rev-parse", "--short", "HEAD").stdout.strip()
            state["committed"] = True

            records_commit = None
            # This run's records plus any left over from earlier failed attempts.
            records_to_commit = [
                path for path in self._changed_paths()
                if path in set(records) or path.startswith(RECORDS_PREFIX)
            ]
            if records_to_commit:
                self._git("add", "--", *records_to_commit)
                self._git(*identity, "commit", "-m", f"[{task_id}] Record the patch proposal and report")
                records_commit = self._git("rev-parse", "--short", "HEAD").stdout.strip()

        except Exception:
            self._undo_branch(branch, patch_path, state)
            raise

        return AgentCommitResult(
            task_id=task_id,
            branch=branch,
            base_branch=BASE_BRANCH,
            code_commit=code_commit,
            records_commit=records_commit,
            committed_files=declared,
        )

    # --- correction loop (P1.5) --------------------------------------------

    def current_agent_branch(self):
        """The agent branch the workspace is on; refuses any other branch."""
        branch = self._git("branch", "--show-current").stdout.strip()
        if not branch.startswith(BRANCH_PREFIX):
            raise PilotGitError(f"Expected an agent branch, but the workspace is on '{branch}'.")
        return branch

    def commit_fix_on_current_branch(
        self, task_id, patch_path, changed_files, summary, record_paths=(), frozen_prefixes=("tests/",)
    ):
        """Apply a fix on the CURRENT agent branch and commit it.

        Files under frozen_prefixes (the tests) may not change during a fix:
        an AI asked to "make the tests pass" could otherwise weaken the tests.
        If anything fails, only the fix is undone; the earlier commits stay.
        """

        patch_path = Path(patch_path).resolve()
        records = [self._relative(path) for path in record_paths]
        declared = sorted(set(changed_files))

        frozen = [path for path in declared if path.startswith(tuple(frozen_prefixes))]
        if frozen:
            raise FrozenFilesError("The fix tried to change frozen test files: " + ", ".join(frozen))

        branch = self.current_agent_branch()
        unexpected = [
            path for path in self._changed_paths()
            if path not in set(records) and not path.startswith(RECORDS_PREFIX)
        ]
        if unexpected:
            raise PilotGitError("The pilot workspace has unexpected changes: " + ", ".join(unexpected))

        self._git("apply", "--check", str(patch_path))
        state = {"applied": False, "committed": False}

        try:
            self._git("apply", str(patch_path))
            state["applied"] = True

            # A test file the agent did not declare also counts as "not declared".
            touched = [
                path for path in self._changed_paths()
                if path not in set(records) and not path.startswith(RECORDS_PREFIX)
            ]
            if touched != declared:
                raise PilotGitError(f"The fix changed {touched}, but the agent declared {declared}.")

            identity = ["-c", f"user.name={AGENT_NAME}", "-c", f"user.email={AGENT_EMAIL}"]
            self._git("add", "--", *declared)
            self._git(
                *identity, "commit", "-m", f"[{task_id}] Fix: {summary}",
                "-m", "Proposed by the Backend Developer Agent after failing tests (correction loop). Not approved yet: waiting for the H3 decision.",
            )
            code_commit = self._git("rev-parse", "--short", "HEAD").stdout.strip()
            state["committed"] = True
        except Exception:
            if state["applied"] and not state["committed"]:
                self._git("reset", "--quiet", check=False)
                self._git("apply", "-R", str(patch_path), check=False)
            raise

        records_commit = self.commit_records(task_id, record_paths, "Record the fix proposal and report")

        return AgentCommitResult(
            task_id=task_id,
            branch=branch,
            base_branch=BASE_BRANCH,
            code_commit=code_commit,
            records_commit=records_commit,
            committed_files=declared,
        )

    def files_added_on_branch(self, prefix="tests/"):
        """Files under prefix that the agent branch added or changed vs. the base."""
        output = self._git("diff", "--name-only", f"{BASE_BRANCH}...HEAD", "--", prefix).stdout
        return sorted(line.strip() for line in output.splitlines() if line.strip())

    def commit_records(self, task_id, record_paths, message):
        """Commit run records (reports, proposals) on the current agent branch."""
        self.current_agent_branch()
        records = [self._relative(path) for path in record_paths]
        to_commit = [path for path in records if path in self._changed_paths()]
        if not to_commit:
            return None

        identity = ["-c", f"user.name={AGENT_NAME}", "-c", f"user.email={AGENT_EMAIL}"]
        self._git("add", "--", *to_commit)
        self._git(*identity, "commit", "-m", f"[{task_id}] {message}")
        return self._git("rev-parse", "--short", "HEAD").stdout.strip()

    def _undo_branch(self, branch, patch_path, state):
        """Go back to the base branch and remove the failed attempt.

        Only the patch's own changes are undone: run records (the proposal,
        the report and the refreshed snapshot) are kept, so the failure stays
        visible and can be reviewed.
        """
        if state["applied"] and not state["committed"]:
            # Unstage first, then reverse exactly the changes the patch made
            # (this also removes files the patch created).
            self._git("reset", "--quiet", check=False)
            self._git("apply", "-R", str(patch_path), check=False)
        self._git("switch", BASE_BRANCH, check=False)
        self._git("branch", "-D", branch, check=False)
