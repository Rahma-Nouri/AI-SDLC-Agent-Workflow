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

# Which agent wrote the work. The role decides the commit author and the branch
# name; a frontend attempt is called agent/T7-frontend-attempt-1, so it can
# never clash with the backend's agent/T7-attempt-1.
AGENT_ROLES = {
    "backend": {
        "name": AGENT_NAME,
        "email": AGENT_EMAIL,
        "title": "Backend Developer Agent",
        "branch_part": "",
    },
    "frontend": {
        "name": "AI-SDLC Frontend Agent",
        "email": "frontend-agent@ai-sdlc.local",
        "title": "Frontend Developer Agent",
        "branch_part": "-frontend",
    },
    # P2.3: the Documentation Agent writes only Markdown under pilot/docs/.
    "docs": {
        "name": "AI-SDLC Documentation Agent",
        "email": "docs-agent@ai-sdlc.local",
        "title": "Documentation Agent",
        "branch_part": "-docs",
    },
    # P2.5b-2: the Software Architect Agent revises the architecture after the
    # human's H2 notes. Its branches are not task attempts, so they have their
    # own name: agent/architecture-revision-1 (see ARCHITECTURE_BRANCH).
    "architect": {
        "name": "AI-SDLC Architect Agent",
        "email": "architect-agent@ai-sdlc.local",
        "title": "Software Architect Agent",
        "branch_part": "",
    },
}

# Everything under outputs/ is a run record (proposals, reports, snapshot).
# Records never block a run, and the agent commits them on its branch, so
# evidence of earlier failed attempts is kept, not lost.
RECORDS_PREFIX = "outputs/"

# The only file the Architect Agent may change, and the name of its branches.
ARCHITECTURE_FILE = "pilot/architecture_design.md"
ARCHITECTURE_BRANCH = f"{BRANCH_PREFIX}architecture-revision-"


def _branch_work_name(branch):
    """agent/T7-frontend-attempt-1 -> T7-frontend"""
    return branch[len(BRANCH_PREFIX):].split("-attempt-")[0]


def branch_role(branch):
    """Which agent owns a branch: 'frontend', 'docs' or 'backend'.

    The role is the branch name's ending before "-attempt-": agent/T1-docs-attempt-1
    is a docs branch. Backend branches have no ending (agent/T1-attempt-1).
    agent/architecture-revision-1 belongs to the Architect Agent.
    """
    if branch.startswith(ARCHITECTURE_BRANCH):
        return "architect"
    work_name = _branch_work_name(branch)
    for role, details in AGENT_ROLES.items():
        if details["branch_part"] and work_name.endswith(details["branch_part"]):
            return role
    return "backend"


def branch_task_id(branch):
    """The task of an agent branch: agent/T7-frontend-attempt-1 -> T7"""
    return _branch_work_name(branch).removesuffix(AGENT_ROLES[branch_role(branch)]["branch_part"])


def commit_identity(branch):
    """Git options that make the branch's own agent the commit author."""
    role = AGENT_ROLES[branch_role(branch)]
    return ["-c", f"user.name={role['name']}", "-c", f"user.email={role['email']}"]


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

    def next_branch_name(self, task_id, role="backend"):
        """agent/<task>-attempt-<n>, where n counts earlier attempts of this role."""
        base = f"{BRANCH_PREFIX}{task_id}{AGENT_ROLES[role]['branch_part']}-attempt-"
        existing = self._git("branch", "--list", f"{base}*").stdout
        attempts = [line for line in existing.splitlines() if line.strip()]
        return f"{base}{len(attempts) + 1}"

    # --- the main step -----------------------------------------------------

    def apply_patch_on_new_branch(self, task_id, patch_path, changed_files, summary, record_paths=(), role="backend"):
        """Create the agent branch, apply the patch, and commit it.

        changed_files: the files the agent declared in its result.
        record_paths: run records to commit too (patch, report, snapshot).
        role: which agent's work this is ("backend", "frontend" or "docs").
        """

        patch_path = Path(patch_path).resolve()
        records = [self._relative(path) for path in record_paths]
        declared = sorted(set(changed_files))

        self.ensure_ready(allowed_paths=records)

        # Check before creating anything: a patch that does not apply leaves no trace.
        self._git("apply", "--check", str(patch_path))

        branch = self.next_branch_name(task_id, role)
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

            identity = commit_identity(branch)

            self._git("add", "--", *declared)
            self._git(
                *identity, "commit", "-m", f"[{task_id}] {summary}",
                "-m", f"Proposed by the {AGENT_ROLES[role]['title']}. Not approved yet: waiting for tests and the H3 decision.",
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
        self, task_id, patch_path, changed_files, summary, record_paths=(), frozen_prefixes=("tests/",),
        frozen_check=None, reason=None,
    ):
        """Apply a fix on the CURRENT agent branch and commit it.

        reason: the second line of the commit message. The default describes a
        fix after failing tests; a document revision has no tests, so it passes
        its own reason.

        Files under frozen_prefixes (the tests) may not change during a fix:
        an AI asked to "make the tests pass" could otherwise weaken the tests.
        If anything fails, only the fix is undone; the earlier commits stay.
        """

        patch_path = Path(patch_path).resolve()
        records = [self._relative(path) for path in record_paths]
        declared = sorted(set(changed_files))

        # frozen_check(path) -> True also freezes a file, for tests found by
        # name rather than by folder (frontend tests such as App.test.tsx).
        frozen = [
            path for path in declared
            if path.startswith(tuple(frozen_prefixes)) or (frozen_check is not None and frozen_check(path))
        ]
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

            title = AGENT_ROLES[branch_role(branch)]["title"]
            self._git("add", "--", *declared)
            self._git(
                *commit_identity(branch), "commit", "-m", f"[{task_id}] Fix: {summary}",
                "-m", reason or f"Proposed by the {title} after failing tests (correction loop). Not approved yet: waiting for the H3 decision.",
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

    def files_on_base(self, prefix):
        """Files under prefix that are committed on pilot/main (whatever branch is checked out)."""
        output = self._git("ls-tree", "-r", "--name-only", BASE_BRANCH, "--", prefix).stdout
        return sorted(line.strip() for line in output.splitlines() if line.strip())

    def files_added_on_branch(self, prefix="tests/"):
        """Files under prefix that the agent branch added or changed vs. the base."""
        output = self._git("diff", "--name-only", f"{BASE_BRANCH}...HEAD", "--", prefix).stdout
        return sorted(line.strip() for line in output.splitlines() if line.strip())

    # --- H3 decisions (P1.6) -----------------------------------------------

    def branch_summary(self):
        """What the human reviews at H3: the agent's commits and changed code files."""
        branch = self.current_agent_branch()
        commits = self._git("log", "--format=%h %an: %s", f"{BASE_BRANCH}..HEAD").stdout.strip().splitlines()
        files = self._git("diff", "--stat", f"{BASE_BRANCH}...HEAD", "--", ".", f":(exclude){RECORDS_PREFIX}").stdout.strip()
        return {"branch": branch, "commits": commits, "changed_files": files}

    def merge_into_base(self, task_id, branch, gate="H3"):
        """After an H3 (or H2) approval: merge the agent branch into pilot/main."""
        if self.current_agent_branch() != branch:
            raise PilotGitError(f"Expected to be on {branch}.")
        if self._changed_paths():
            raise PilotGitError("Commit all records before merging.")

        self._git("switch", BASE_BRANCH)
        merged = self._git(
            *commit_identity(branch), "merge", "--no-ff", branch,
            "-m", f"[{task_id}] Merge {branch} after the human's {gate} approval",
            check=False,
        )
        if merged.returncode != 0:
            # Never leave a half-finished merge behind.
            self._git("merge", "--abort", check=False)
            self._git("switch", branch, check=False)
            raise PilotGitError("Merge failed: " + (merged.stderr or merged.stdout).strip())

        self._git("branch", "-d", branch)
        return self._git("rev-parse", "--short", "HEAD").stdout.strip()

    def push_base(self, remote="origin"):
        """Push pilot/main (only ever after an H3 approval)."""
        self._git("push", remote, BASE_BRANCH)

    def delete_agent_branch(self, branch):
        """After an H3 rejection: go back to pilot/main and delete the attempt."""
        if not branch.startswith(BRANCH_PREFIX):
            raise PilotGitError("Only agent/ branches may be deleted.")
        if self.current_agent_branch() != branch:
            raise PilotGitError(f"Expected to be on {branch}.")
        if self._changed_paths():
            raise PilotGitError("Commit all records before deleting the branch.")
        self._git("switch", BASE_BRANCH)
        self._git("branch", "-D", branch)

    def commit_records(self, task_id, record_paths, message):
        """Commit run records (reports, proposals) on the current agent branch."""
        branch = self.current_agent_branch()
        records = [self._relative(path) for path in record_paths]
        to_commit = [path for path in records if path in self._changed_paths()]
        if not to_commit:
            return None

        self._git("add", "--", *to_commit)
        self._git(*commit_identity(branch), "commit", "-m", f"[{task_id}] {message}")
        return self._git("rev-parse", "--short", "HEAD").stdout.strip()

    def pending_records(self):
        """Run records under outputs/ that are not committed yet."""
        return [path for path in self._changed_paths() if path.startswith(RECORDS_PREFIX)]

    # --- H2 architecture revision (P2.5b-2) --------------------------------

    def start_architecture_revision(self):
        """Create agent/architecture-revision-<n> from a clean pilot/main and switch to it."""
        self.ensure_ready()
        existing = self._git("branch", "--list", f"{ARCHITECTURE_BRANCH}*").stdout
        number = len([line for line in existing.splitlines() if line.strip()]) + 1
        branch = f"{ARCHITECTURE_BRANCH}{number}"
        self._git("switch", "-c", branch)
        return branch

    def commit_architecture_revision(self, architecture, record_path):
        """Write the revised architecture and commit it with the record of the human's notes.

        Only these two files are committed, by the Architect Agent. If anything
        goes wrong, the architecture file is restored, so no half-done change stays.
        """
        branch = self.current_agent_branch()
        if branch_role(branch) != "architect":
            raise PilotGitError(f"Architecture revisions belong on {ARCHITECTURE_BRANCH}<n>, not on {branch}.")

        record = self._relative(record_path)
        if not record.startswith(RECORDS_PREFIX):
            raise PilotGitError(f"The notes record must be under {RECORDS_PREFIX}: {record}")

        # newline="\n": Git stores LF, like the files written by the other agents.
        (self.pilot_root / ARCHITECTURE_FILE).write_text(architecture, encoding="utf-8", newline="\n")
        try:
            changed = [path for path in self._changed_paths() if not path.startswith(RECORDS_PREFIX)]
            if changed != [ARCHITECTURE_FILE]:
                raise PilotGitError(
                    f"The revision must change only {ARCHITECTURE_FILE}; changed: {changed or 'nothing'}"
                )
            self._git("add", "--", ARCHITECTURE_FILE, record)
            self._git(
                *commit_identity(branch), "commit", "-m", "[architecture] Revise the architecture after the H2 notes",
                "-m", "Proposed by the Software Architect Agent. Not approved yet: waiting for the human's H2 decision.",
            )
        except Exception:
            self._git("reset", "--quiet", check=False)
            self._git("checkout", "--", ARCHITECTURE_FILE, check=False)
            raise
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
