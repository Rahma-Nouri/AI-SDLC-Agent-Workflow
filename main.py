import argparse
import difflib
import json
import os
import sys
from datetime import datetime
from functools import partial
from pathlib import Path

from pydantic import ValidationError

from agents.agent_contracts import (
    BackendDeveloperResult,
    DocumentationResult,
    FrontendDeveloperResult,
    UiStackProposal,
)
from agents.backend_developer import BackendDeveloperAgent
from agents.correction_loop import BACKEND_TESTS, frontend_tests, run_correction_loop
from agents.documentation_agent import (
    DOC_TASKS,
    DOCS_FOLDER,
    DocumentationAgent,
    docs_architecture_context,
    newest_agent_report,
)
from agents.frontend_environment import FrontendEnvironment
from agents.frontend_developer import (
    GENERATED_FOLDERS,
    GITIGNORE,
    UI_TASKS,
    FrontendDeveloperAgent,
    ui_architecture_context,
)
from agents.h2b_ui_stack import run_h2b_review
from agents.h3_review import document_checks, run_h3_review, write_decision_record
from agents.issue_sync_agent import IssueSyncAgent, describe_plan, plan_sync
from agents.orchestrator import OrchestratorAgent
from agents.pilot_environment import PilotEnvironment
from agents.pilot_git_agent import PilotGitAgent, PilotGitError, branch_role, branch_task_id
from agents.software_architect import SoftwareArchitectAgent
from agents.testing_workflow import run_frontend_task_tests, run_task_tests
from agents.ticket_quality_checker import TicketQualityChecker
from integrations.github_client import GitHubClient


# The tests that belong to T1. They arrived with the accepted T1 patch
# (see "tests_added" in its report) and were applied in P1.2.
T1_TEST_PATHS = ["tests/test_backend_database_schema.py"]

# Tasks the Backend Agent must not take, and where they belong instead.
# T7's description asks only for the student-facing interface (done by the
# Frontend Agent). No approved task is documentation only yet; such a task
# would get an entry here too.
NOT_BACKEND_TASKS = {
    "T7": "it is UI only. Use: python main.py --frontend-patch T7",
}

# World 2 (the pilot) lives in its own folder: a Git worktree of the
# pilot/main branch, next to this project folder. The workflow code (World 1)
# reads and writes the pilot only there, never inside this project folder.
DEFAULT_PILOT_WORKSPACE_NAME = "AI-SDLC-pilot-workspace"


def get_pilot_root(workflow_root):
    """Return the pilot workspace folder.

    Uses the PILOT_WORKSPACE environment variable when it is set; otherwise the
    folder named DEFAULT_PILOT_WORKSPACE_NAME next to the workflow folder.
    """

    configured = os.getenv("PILOT_WORKSPACE")

    if configured:
        pilot_root = Path(configured)
    else:
        pilot_root = Path(workflow_root).resolve().parent / DEFAULT_PILOT_WORKSPACE_NAME

    if not pilot_root.is_dir():
        raise FileNotFoundError(
            f"Pilot workspace not found: {pilot_root}\n"
            "Create it with: git worktree add <folder> pilot/main"
        )

    # Never let the pilot point back into the workflow project itself.
    if pilot_root.resolve() == Path(workflow_root).resolve():
        raise ValueError("The pilot workspace must not be the workflow folder.")

    return pilot_root.resolve()


def read_text_file(path, default=""):
    """Read a UTF-8 or BOM-marked UTF-16 text file."""

    path = Path(path)

    if not path.is_file():
        return default

    content = path.read_bytes()

    if content.startswith((b"\xff\xfe", b"\xfe\xff")):
        return content.decode("utf-16")

    return content.decode("utf-8-sig")


def collect_backend_file_context(project_root):
    """Collect exact baselines for files the backend agent may propose changing."""

    project_root = Path(project_root)
    candidates = []

    backend_root = project_root / "pilot" / "app" / "backend"
    if backend_root.is_symlink():
        raise ValueError("The backend folder cannot be a symlink.")

    if backend_root.is_dir():
        for path in backend_root.rglob("*"):
            if path.is_symlink():
                raise ValueError(
                    f"Symlink found in backend source snapshot: {path}"
                )
            if path.is_file():
                candidates.append(path)

    requirements_path = project_root / "requirements.txt"
    if requirements_path.is_symlink():
        raise ValueError("requirements.txt cannot be a symlink.")
    if requirements_path.is_file():
        candidates.append(requirements_path)

    tests_root = project_root / "tests"
    if tests_root.is_symlink():
        raise ValueError("The tests folder cannot be a symlink.")

    if tests_root.is_dir():
        for path in tests_root.glob("test_backend_*.py"):
            if path.is_symlink():
                raise ValueError(
                    f"Symlink found in backend test snapshot: {path}"
                )
            if path.is_file():
                candidates.append(path)

    existing_files = {}

    for path in sorted(set(candidates)):
        relative_path = path.relative_to(project_root).as_posix()
        existing_files[relative_path] = read_text_file(path)

    return {
        "existing_files": existing_files,
        "existing_file_paths": sorted(existing_files),
        "existing_files_complete": True,
    }




def collect_frontend_file_context(project_root, frontend_folder):
    """Collect exact baselines for the files the frontend agent may change."""

    project_root = Path(project_root)
    candidates = []

    frontend_root = project_root / frontend_folder
    if frontend_root.is_symlink():
        raise ValueError("The frontend folder cannot be a symlink.")

    if frontend_root.is_dir():
        for path in frontend_root.rglob("*"):
            if GENERATED_FOLDERS & set(path.relative_to(frontend_root).parts):
                continue
            if path.is_symlink():
                raise ValueError(f"Symlink found in frontend source snapshot: {path}")
            if path.is_file():
                candidates.append(path)

    # The baseline of .gitignore is needed too: without it, a change to this
    # file would look like a new file and the patch would not apply.
    gitignore_path = project_root / GITIGNORE
    if gitignore_path.is_file() and not gitignore_path.is_symlink():
        candidates.append(gitignore_path)

    existing_files = {}

    for path in sorted(candidates):
        try:
            existing_files[path.relative_to(project_root).as_posix()] = read_text_file(path)
        except UnicodeDecodeError:
            continue  # An image or other binary file: the agent writes text only.

    return {
        "existing_files": existing_files,
        "existing_file_paths": sorted(existing_files),
        "existing_files_complete": True,
    }


def print_ticket_quality_report(report, tasks):
    """Display task validation results."""

    print("\n" + "=" * 60)
    print("TICKET QUALITY CHECK")
    print("=" * 60)

    if report["errors"]:
        print("\nERRORS:")
        for error in report["errors"]:
            print(f"- {error}")

    if report["warnings"]:
        print("\nWARNINGS:")
        for warning in report["warnings"]:
            print(f"- {warning}")

    if not report["valid"]:
        print("\nTicket validation failed.")
        print("No GitHub Issues will be created.")
        return False

    print("\nAll blocking ticket quality checks passed.")
    print(f"Tasks checked: {len(tasks)}")

    for task in tasks:
        print(f"- {task['task_id']}: {task['title']}")

    return True


def prepare_and_approve_backlog(
    orchestrator,
    checker,
    specification,
    initial_plan,
):
    """Generate and validate tasks before requesting H1 approval."""

    plan = initial_plan

    while True:
        print("\nGenerating structured development tasks...")

        try:
            tasks = orchestrator.convert_plan_to_tasks(plan)
        except (ValueError, TypeError, KeyError) as error:
            print(f"\nTask generation or structural validation failed: {error}")
            print("No GitHub Issues will be created.")
            return None, None

        report = checker.validate_tasks(tasks)

        if not print_ticket_quality_report(report, tasks):
            return None, None

        decision = orchestrator.request_backlog_approval(plan, tasks)

        if decision == "approve":
            print("\nH1 approved: plan and task backlog accepted.")
            return plan, tasks

        if decision == "reject":
            print("\nThe plan and backlog were rejected.")
            print("Workflow stopped. No GitHub Issues will be created.")
            return None, None

        if decision != "revise":
            print("Invalid approval decision. Please try again.")
            continue

        feedback = input(
            "\nDescribe the changes needed in the plan or tasks: "
        ).strip()

        if not feedback:
            print("Feedback cannot be empty. The backlog remains unapproved.")
            continue

        print("\nRevising the plan based on your feedback...")

        try:
            plan = orchestrator.revise_plan(
                specification,
                plan,
                feedback,
            )
        except Exception as error:
            print(f"\nPlan revision failed: {error}")
            print("Workflow stopped. No GitHub Issues will be created.")
            return None, None


def print_architecture_coverage_report(report):
    """Display the architecture task-ID coverage results."""

    print("\nArchitecture Task Coverage Report")
    print(f"Valid: {report['valid']}")
    print(f"Missing task IDs: {report['missing_ids']}")
    print(f"Unexpected task IDs: {report['unexpected_ids']}")
    print(f"Repeated task IDs: {report['duplicated_ids']}")


def validate_architecture(architecture, tasks):
    """Validate architecture coverage and report the result."""

    report = SoftwareArchitectAgent.validate_task_coverage(
        architecture,
        tasks,
    )

    print_architecture_coverage_report(report)
    return report["valid"]


def markdown_sections(text):
    """Split a Markdown document into {heading: section text}."""

    sections = {}
    heading = "(text before the first heading)"
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            heading = line.strip().lstrip("#").strip()
        sections.setdefault(heading, []).append(line)
    return {name: "\n".join(lines) for name, lines in sections.items()}


def print_revision_summary(old, new, branch, commit):
    """What the Architect changed: the changed sections and the line counts."""

    old_sections = markdown_sections(old)
    new_sections = markdown_sections(new)
    changed = [name for name in new_sections if old_sections.get(name) != new_sections[name]]
    removed = [name for name in old_sections if name not in new_sections]

    diff = list(difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm=""))
    added_lines = sum(1 for line in diff if line.startswith("+") and not line.startswith("+++"))
    removed_lines = sum(1 for line in diff if line.startswith("-") and not line.startswith("---"))

    print(f"\nThe Architect Agent committed a revision on {branch} ({commit}).")
    print(f"Lines: +{added_lines} -{removed_lines}")
    print("Changed sections:")
    for name in changed:
        print(f"  ~ {name}")
    for name in removed:
        print(f"  - {name} (removed)")
    if not changed and not removed:
        print("  none")


def run_architect_revision(pilot_root, architecture_path, tasks, specification, notes, architect, git_agent, branch):
    """One H2 revision: the Architect rewrites the document, the Pilot Git Agent commits it.

    branch: the revision branch of this H2 review, or None before the first revision.
    Returns (branch, status). status is "committed", "coverage_failed" (refused,
    nothing committed) or "failed" (the problem was printed).
    """

    if branch is None:
        # Check the workspace first: a Gemini request must not be spent on a
        # revision that could not be committed anyway.
        try:
            git_agent.ensure_ready()
        except PilotGitError as error:
            print(f"\nThe Architect cannot revise: {error}")
            return branch, "failed"

    old = read_text_file(architecture_path)
    try:
        revised = architect.revise_architecture(specification, old, tasks, notes)
    except Exception as error:
        print(f"\nThe Architect Agent could not revise the architecture: {error}")
        return branch, "failed"

    # The task IDs are frozen after H1: every approved task must still be covered.
    print("\nChecking the revision's task-ID coverage...")
    if not validate_architecture(revised, tasks):
        print("\nThe revision is refused: it must keep every approved task ID. Nothing was committed.")
        return branch, "coverage_failed"

    try:
        if branch is None:
            branch = git_agent.start_architecture_revision()
        record_path = write_decision_record(pilot_root, "architecture", {
            "gate": "H2",
            "decision": "revise",
            "branch": branch,
            "notes": notes,
            "decided_at": datetime.now().isoformat(timespec="seconds"),
        }, gate="H2")
        commit = git_agent.commit_architecture_revision(revised, record_path)
    except PilotGitError as error:
        print(f"\nThe Pilot Git Agent could not commit the revision: {error}")
        return branch, "failed"

    print_revision_summary(old, revised, branch, commit)
    return branch, "committed"


def merge_architecture_revision(pilot_root, git_agent, branch, architecture, notes):
    """H2 approve after a revision: record it, update the snapshot, merge and push.

    Returns True when the revision is merged into pilot/main.
    """

    record_path = write_decision_record(pilot_root, "architecture", {
        "gate": "H2",
        "decision": "approve",
        "branch": branch,
        "notes": notes,
        "decided_at": datetime.now().isoformat(timespec="seconds"),
    }, gate="H2")

    # The snapshot gets the approved architecture; its task list stays as it is.
    snapshot_path = Path(pilot_root) / "outputs" / "approved_context" / "current_approved_context.json"
    snapshot = read_snapshot_if_valid(snapshot_path)
    if snapshot:
        snapshot["architecture"] = architecture
        write_snapshot(snapshot_path, snapshot)

    try:
        # Records left over from earlier steps are committed too, so the merge can run.
        leftovers = [Path(pilot_root) / path for path in git_agent.pending_records()]
        git_agent.commit_records("architecture", [record_path, *leftovers], "Record the H2 approval")
        merge_commit = git_agent.merge_into_base("architecture", branch, gate="H2")
    except PilotGitError as error:
        print(f"\nThe Pilot Git Agent could not merge the revision: {error}")
        return False

    print(f"\nMerged {branch} into pilot/main: {merge_commit}")
    try:
        git_agent.push_base()
        print("Pushed pilot/main to GitHub.")
    except Exception as error:
        print(f"Push failed (the merge is kept locally): {error}")

    if snapshot.get("ui_stack"):
        print("\nWARNING: the snapshot has an approved UI stack (H2b) based on the old architecture.")
        print("Check it, and run python main.py --h2b-ui-stack again if it no longer fits.")
    return True


def review_and_approve_architecture(
    orchestrator,
    architect,
    architecture_path,
    tasks,
    specification=None,
    git_agent=None,
    ask=None,
):
    """H2: approve only the current on-disk architecture if it validates.

    "revise" never asks the human to edit a file: the human writes notes, and the
    Architect Agent changes the document on its own branch
    (agent/architecture-revision-N). Approve merges that branch; reject deletes it.

    The Architect, the Pilot Git Agent and the specification are only created or
    read when "revise" is chosen, so an approve-only review makes no Gemini call.
    ask: how to ask the human (input by default, looked up at call time).
    """

    ask = ask or input
    architecture_path = Path(architecture_path)
    pilot_root = architecture_path.parent.parent  # <pilot>/pilot/architecture_design.md
    revision_branch = None  # set once the Architect committed a revision
    coverage_failed = False  # the last revision lost a task ID: only revise or reject
    last_notes = ""

    while True:
        print("\n" + "=" * 60)
        print("H2 — HUMAN APPROVAL: SOFTWARE ARCHITECTURE")
        print("=" * 60)
        print(f"Review this document:\n{architecture_path}")
        if revision_branch:
            print(f"(Revised by the Architect Agent on branch {revision_branch}.)")

        if coverage_failed:
            decision = ask("\nEnter 'revise' or 'reject': ").strip().lower()
            if decision == "approve":
                print("The last revision was refused. Enter revise or reject.")
                continue
        else:
            decision = ask(
                "\nEnter 'approve', 'revise', or 'reject': "
            ).strip().lower()

        if decision == "approve":
            try:
                architecture = read_text_file(architecture_path)
            except (OSError, UnicodeError) as error:
                print(f"\nCould not read the architecture file: {error}")
                print("H2 approval cannot be granted.")
                return False

            if not architecture.strip():
                print("\nThe architecture document is empty.")
                print("H2 approval cannot be granted.")
                continue

            try:
                valid = validate_architecture(architecture, tasks)
            except (ValueError, TypeError, KeyError) as error:
                print(f"\nArchitecture validation failed: {error}")
                print("H2 approval cannot be granted.")
                continue

            if not valid:
                print("\nThe current architecture does not pass validation.")
                print("Choose 'revise' to send notes to the Architect Agent.")
                continue

            if revision_branch and not merge_architecture_revision(
                pilot_root, git_agent, revision_branch, architecture, last_notes
            ):
                print("H2 approval cannot be granted.")
                return False

            orchestrator.record_approval("H2", "approve")
            print("\nH2 approved: validated architecture accepted.")
            return True

        if decision == "reject":
            if revision_branch is None:
                orchestrator.record_approval("H2", "reject")
                print("\nArchitecture rejected.")
                return False

            # Reject the Architect's revision: back to the old architecture.
            reason = ask("Why do you reject the revision? (saved in the record): ").strip()
            orchestrator.record_approval("H2", "reject", reason)
            try:
                git_agent.delete_agent_branch(revision_branch)
            except PilotGitError as error:
                print(f"\nThe Pilot Git Agent could not delete {revision_branch}: {error}")
                return False
            # Written after the deletion, so the record lands on pilot/main as a run
            # record; the next agent branch commits it, so the decision is not lost.
            write_decision_record(pilot_root, "architecture", {
                "gate": "H2",
                "decision": "reject",
                "branch": revision_branch,
                "notes": reason,
                "decided_at": datetime.now().isoformat(timespec="seconds"),
            }, gate="H2")
            print(f"\nDeleted {revision_branch}. The old architecture stays on pilot/main.")
            revision_branch = None
            coverage_failed = False
            continue

        if decision == "revise":
            notes = ask(
                "\nWhat needs to change in the architecture? "
                "Enter your review notes (the Architect Agent makes the change): "
            ).strip()

            if not notes:
                print("Review notes cannot be empty.")
                continue

            orchestrator.record_approval("H2", "revise", notes)
            last_notes = notes

            # Created only now, so an approve-only review makes no Gemini call.
            architect = architect or SoftwareArchitectAgent()
            git_agent = git_agent or PilotGitAgent(pilot_root, workflow_root=Path(__file__).resolve().parent)
            if specification is None:
                specification = read_text_file(pilot_root / "pilot" / "pilot_specification.md")

            revision_branch, status = run_architect_revision(
                pilot_root, architecture_path, tasks, specification, notes, architect, git_agent, revision_branch
            )
            coverage_failed = status == "coverage_failed"
            continue

        print("Invalid choice. Enter approve, revise, or reject.")


def create_t1_backend_patch(
    orchestrator,
    tasks,
    architecture,
    project_context,
    output_dir,
    agent=None,
):
    """The T1 patch of the legacy run (kept under its old name)."""

    return create_backend_patch(
        orchestrator=orchestrator,
        tasks=tasks,
        architecture=architecture,
        project_context=project_context,
        output_dir=output_dir,
        agent=agent,
        task_id="T1",
    )


def create_backend_patch(
    orchestrator,
    tasks,
    architecture,
    project_context,
    output_dir,
    agent=None,
    task_id="T1",
):
    """Generate and save a backend patch for one approved task, without applying it."""

    if not orchestrator.workflow_state["backlog_approved"]:
        raise RuntimeError(
            "H1 approval is required before coding-agent execution."
        )

    if not orchestrator.workflow_state["architecture_approved"]:
        raise RuntimeError(
            "H2 approval is required before coding-agent execution."
        )

    task = next(
        (
            item
            for item in tasks
            if item.get("task_id") == task_id
        ),
        None,
    )

    if task is None:
        raise ValueError(f"Approved task {task_id} was not found in the task list.")

    work_package = orchestrator.delegate_task(
        task,
        architecture=architecture,
        project_context=project_context,
    )

    coding_agent = agent or BackendDeveloperAgent()
    result = coding_agent.create_patch(work_package)

    if not isinstance(result, BackendDeveloperResult):
        raise TypeError(
            "The Backend Developer Agent must return a BackendDeveloperResult."
        )

    return save_patch_proposal(result, output_dir)


def save_patch_proposal(result, output_dir, label="", role="backend"):
    """Save an agent's patch and report; label marks fixes, e.g. "fix1_"."""

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    patch_path = output_dir / f"{result.task_id}_{role}_{label}{timestamp}.patch"
    report_path = output_dir / f"{result.task_id}_{role}_{label}{timestamp}_report.json"

    # Write the patch with LF ("\n") line endings on every operating system.
    # Without newline="\n", Windows text mode turns each "\n" into "\r\n",
    # and the CRLF patch then fails `git apply --check`.
    patch_path.write_text(result.patch, encoding="utf-8", newline="\n")
    report_path.write_text(
        result.model_dump_json(indent=2),
        encoding="utf-8",
    )

    return patch_path, report_path


def read_snapshot_if_valid(snapshot_path):
    """The saved snapshot as a dict; {} if it is missing or unreadable."""

    try:
        snapshot = json.loads(Path(snapshot_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    return snapshot if isinstance(snapshot, dict) else {}


def write_snapshot(snapshot_path, snapshot):
    """Write to a temporary file first, so a crash cannot leave half a snapshot."""

    temporary_path = snapshot_path.with_suffix(".json.tmp")

    temporary_path.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary_path.replace(snapshot_path)


def save_approved_context(
    orchestrator,
    project_root,
    plan,
    tasks,
    architecture,
):
    """Save the exact H1/H2-approved inputs for a future resume mode."""

    state = orchestrator.workflow_state

    if not state["backlog_approved"]:
        raise RuntimeError("Cannot save context before H1 backlog approval.")

    if not state["architecture_approved"]:
        raise RuntimeError(
            "Cannot save context before H2 architecture approval."
        )

    snapshot_dir = Path(project_root) / "outputs" / "approved_context"
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    snapshot_path = snapshot_dir / "current_approved_context.json"

    # Start from the saved snapshot, so fields this function does not write
    # itself (such as "ui_stack" from gate H2b) are kept.
    snapshot = {
        **read_snapshot_if_valid(snapshot_path),
        "schema_version": 1,
        "saved_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": "approved workflow run",
        "note": (
            "A future resume run must request fresh H1 and H2 approvals. "
            "These saved approval values are historical only."
        ),
        "human_approvals": {
            "H1": "approved",
            "H2": "approved",
        },
        "plan": plan,
        "tasks": tasks,
        "architecture": architecture,
    }

    write_snapshot(snapshot_path, snapshot)

    return snapshot_path


def load_approved_context(snapshot_path):
    """Load and validate a saved plan, backlog, and architecture."""

    snapshot_path = Path(snapshot_path)

    try:
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(
            f"Could not read a valid approved-context snapshot: {error}"
        ) from error

    if not isinstance(snapshot, dict):
        raise ValueError("The approved-context snapshot must be a JSON object.")

    if snapshot.get("schema_version") != 1:
        raise ValueError("Unsupported approved-context snapshot version.")

    plan = snapshot.get("plan")
    tasks = snapshot.get("tasks")
    architecture = snapshot.get("architecture")

    if not isinstance(plan, str) or not plan.strip():
        raise ValueError("The snapshot is missing a valid plan.")

    if not isinstance(tasks, list) or not tasks:
        raise ValueError("The snapshot is missing a non-empty task list.")

    if not isinstance(architecture, str) or not architecture.strip():
        raise ValueError("The snapshot is missing a valid architecture.")

    OrchestratorAgent.validate_tasks(tasks)

    return snapshot


def request_fresh_approvals(project_root, orchestrator, next_step, require_h2=True):
    """Load the saved approved context and ask for fresh H1 and H2.

    With require_h2=False only H1 is asked (for steps that need the approved
    backlog but not the architecture); then it returns (snapshot, None).

    Shared by the resume modes. Returns (snapshot, architecture) when both
    gates are approved, or None when the run must stop. next_step is only
    used in messages, for example "patch generation" or "the T1 tests".
    """

    project_root = Path(project_root)
    snapshot_path = (
        project_root
        / "outputs"
        / "approved_context"
        / "current_approved_context.json"
    )

    try:
        snapshot = load_approved_context(snapshot_path)
    except ValueError as error:
        print(f"\nCannot resume from the saved context: {error}")
        return None

    tasks = snapshot["tasks"]

    # Rebuild in-memory task state. Saved approvals do not carry into this run.
    orchestrator.workflow_state["tasks"] = {
        task["task_id"]: {
            **task,
            "status": "pending_approval",
        }
        for task in tasks
    }

    print("\nRESUMING FROM THE SAVED APPROVED CONTEXT")
    print(f"Snapshot: {snapshot_path}")

    decision = orchestrator.request_backlog_approval(
        snapshot["plan"],
        tasks,
    )

    if decision != "approve":
        print(f"\nH1 was not approved. Stopped before H2 and {next_step}.")
        print("Use the full workflow if you want to revise or regenerate the plan.")
        return None

    if not require_h2:
        return snapshot, None

    architecture_path = project_root / "pilot" / "architecture_design.md"

    if not architecture_path.is_file():
        print(f"\nArchitecture file not found: {architecture_path}")
        print(f"Stopped before H2 and {next_step}.")
        return None

    try:
        current_architecture = read_text_file(architecture_path)
    except (OSError, UnicodeError) as error:
        print(f"\nCould not read the architecture file: {error}")
        print(f"Stopped before H2 and {next_step}.")
        return None

    if current_architecture != snapshot["architecture"]:
        print(
            "\nThe architecture file differs from the saved snapshot. "
            "H2 will validate the current file."
        )

    if not review_and_approve_architecture(
        orchestrator=orchestrator,
        architect=None,
        architecture_path=architecture_path,
        tasks=tasks,
    ):
        print(f"\nH2 was not approved. Stopped before {next_step}.")
        return None

    # Use the exact architecture text that passed the fresh H2 review.
    try:
        architecture = read_text_file(architecture_path)
    except (OSError, UnicodeError) as error:
        print(f"\nCould not reread the H2-approved architecture: {error}")
        print(f"Stopped before {next_step}.")
        return None

    if not architecture.strip():
        print("\nThe H2-approved architecture file is empty.")
        print(f"Stopped before {next_step}.")
        return None

    return snapshot, architecture


def run_resume_patch(project_root, orchestrator, agent=None, git_agent=None, task_id="T1", environment=None, ask=None):
    """Load saved context, request fresh approvals, and offer a backend patch.

    task_id: the task the Backend Agent works on. --resume-patch uses T1;
    --backend-patch TASK checks the task first (run_backend_patch), then comes here.
    environment replaces the pilot's .venv in the tests.
    ask: how to ask the human (input by default; looked up at call time, so
    tests that replace input still work).
    """

    project_root = Path(project_root)
    ask = ask or input

    approved = request_fresh_approvals(
        project_root,
        orchestrator,
        next_step="patch generation",
    )

    if approved is None:
        return None

    snapshot, architecture = approved
    tasks = snapshot["tasks"]

    try:
        snapshot_path = save_approved_context(
            orchestrator=orchestrator,
            project_root=project_root,
            plan=snapshot["plan"],
            tasks=tasks,
            architecture=architecture,
        )
    except Exception as error:
        print(f"\nCould not refresh the approved-context snapshot: {error}")
        print("Resume stopped before patch generation.")
        return None

    print(f"\nRefreshed approved-context snapshot: {snapshot_path}")

    confirmation = ask(
        f"\nGenerate a proposed {task_id} backend patch from this approved context? "
        "Enter 'yes' to continue: "
    ).strip().lower()

    if confirmation != "yes":
        print("\nPatch generation cancelled.")
        return None

    specification_path = (
        project_root / "pilot" / "pilot_specification.md"
    )
    requirements_path = project_root / "requirements.txt"

    try:
        project_context = {
            "pilot_specification": read_text_file(specification_path),
            "requirements": read_text_file(requirements_path),
            "python_version": sys.version,
            "snapshot_saved_at": snapshot.get("saved_at", ""),
        }
        project_context.update(collect_backend_file_context(project_root))
    except (OSError, UnicodeError) as error:
        print(f"\nCould not read project context: {error}")
        print("No patch was generated.")
        return None

    try:
        patch_path, report_path = create_backend_patch(
            orchestrator=orchestrator,
            tasks=tasks,
            architecture=architecture,
            project_context=project_context,
            output_dir=project_root / "outputs" / "proposed_changes",
            agent=agent,
            task_id=task_id,
        )
    except Exception as error:
        print(f"\nPatch generation failed: {error}")
        print("No GitHub synchronization was performed.")
        return None

    print("\nPatch proposal created.")
    print(f"Patch:  {patch_path}")
    print(f"Report: {report_path}")

    # The Pilot Git Agent applies the proposal on its own agent/ branch in the
    # pilot workspace. Nothing is pushed or merged before the H3 decision.
    git_agent = git_agent or PilotGitAgent(
        project_root,
        workflow_root=Path(__file__).resolve().parent,
    )
    agent_commit = apply_patch_with_pilot_git_agent(
        project_root=project_root,
        patch_path=patch_path,
        report_path=report_path,
        record_paths=[patch_path, report_path, snapshot_path],
        git_agent=git_agent,
    )

    loop_report = None
    if agent_commit is not None:
        loop_report = run_and_record_correction_loop(
            orchestrator=orchestrator,
            task=next(item for item in tasks if item.get("task_id") == agent_commit.task_id),
            architecture=architecture,
            project_root=project_root,
            report_path=report_path,
            agent_commit=agent_commit,
            backend_agent=agent,
            git_agent=git_agent,
            environment=environment,
        )

    print("No GitHub synchronization was performed.")

    return {
        "patch_path": patch_path,
        "report_path": report_path,
        "agent_commit": agent_commit,
        "loop_report": loop_report,
    }


def missing_dependency_approvals(project_root, task):
    """The task's dependencies that have no H3 approval record yet.

    An H3 approve writes outputs/approvals/<task>_H3_approve_<time>.json, and
    the agent commits it on its branch just before the merge into pilot/main.
    So on pilot/main, the record is there exactly when the task was merged.
    """

    approvals = Path(project_root) / "outputs" / "approvals"
    return [
        dependency for dependency in task.get("dependencies", [])
        if not any(approvals.glob(f"{dependency}_H3_approve_*.json"))
    ]


def run_backend_patch(project_root, orchestrator, task_id, agent=None, git_agent=None, environment=None, ask=input):
    """--backend-patch TASK: check the task, then run the --resume-patch chain for it.

    fresh H1 -> H2 -> snapshot refresh -> Backend Agent -> agent branch ->
    pilot environment -> tests -> (one fix) -> tests -> loop report; then H3.
    All checks run before the Gemini request, so a refused task costs nothing.
    """

    project_root = Path(project_root)
    snapshot_path = project_root / "outputs" / "approved_context" / "current_approved_context.json"

    try:
        snapshot = load_approved_context(snapshot_path)
    except ValueError as error:
        print(f"\nThe backend patch needs an approved snapshot: {error}")
        return None

    task = next((item for item in snapshot["tasks"] if item.get("task_id") == task_id), None)
    if task is None:
        known = ", ".join(item["task_id"] for item in snapshot["tasks"])
        print(f"\nTask {task_id} is not in the approved backlog. Approved tasks: {known}.")
        return None

    if task_id in NOT_BACKEND_TASKS:
        print(f"\n{task_id} has no backend work: {NOT_BACKEND_TASKS[task_id]}")
        return None

    git_agent = git_agent or PilotGitAgent(project_root, workflow_root=Path(__file__).resolve().parent)

    # Check the workspace first: a Gemini request must not be spent on a
    # patch that could not be committed anyway.
    try:
        git_agent.ensure_ready()
    except PilotGitError as error:
        print(f"\nThe pilot workspace is not ready: {error}")
        return None

    # A warning only: the human may know why it is fine to go ahead.
    missing = missing_dependency_approvals(project_root, task)
    if missing:
        print(f"\nWARNING: {task_id} depends on {', '.join(missing)}, but no H3 approval record was found for it.")
        print("Its code may not be merged into pilot/main yet.")
        answer = ""
        while answer not in ("yes", "no"):
            answer = ask("Continue anyway? (yes/no): ").strip().lower()
        if answer == "no":
            print("Stopped. Nothing was changed.")
            return None

    return run_resume_patch(
        project_root, orchestrator, agent=agent, git_agent=git_agent,
        task_id=task_id, environment=environment, ask=ask,
    )


def build_pilot_project_context(project_root):
    """What the Backend Agent sees: pilot documents plus the current files."""

    project_root = Path(project_root)
    context = {
        "pilot_specification": read_text_file(project_root / "pilot" / "pilot_specification.md"),
        "requirements": read_text_file(project_root / "requirements.txt"),
        "python_version": sys.version,
    }
    context.update(collect_backend_file_context(project_root))
    return context


def frontend_loop_options(project_root, ui_stack, environment=None, run_command=None):
    """What the correction loop needs to work on a frontend branch.

    The loop itself is shared; only these parts differ from the backend:
    the agent's context, the npm environment, the Vitest runner, and which
    files count as (frozen) tests. run_command replaces subprocess.run in tests.
    """

    if "vitest" not in ui_stack.test_runner.lower():
        raise ValueError(f"Only Vitest can run frontend tests so far; the approved test runner is {ui_stack.test_runner}.")

    return {
        "build_project_context": lambda root: build_frontend_project_context(root, ui_stack),
        "prepare_environment": (
            environment or FrontendEnvironment(project_root, ui_stack.frontend_folder)
        ).prepare,
        "run_tests": partial(run_frontend_task_tests, frontend_folder=ui_stack.frontend_folder, runner=run_command),
        "frozen": frontend_tests(ui_stack.frontend_folder),
    }


def run_and_record_correction_loop(
    orchestrator, task, architecture, project_root, report_path, agent_commit, backend_agent, git_agent,
    environment=None, role="backend", loop_options=None,
):
    """Run the correction loop on the agent branch and commit its report there.

    loop_options: settings that replace the backend ones (see frontend_loop_options).
    """

    project_root = Path(project_root)
    first_result = BackendDeveloperResult.model_validate_json(
        Path(report_path).read_text(encoding="utf-8")
    )

    options = {
        "build_project_context": build_pilot_project_context,
        "prepare_environment": (environment or PilotEnvironment(project_root)).prepare,
        **(loop_options or {}),
    }

    print(f"\nCORRECTION LOOP on {agent_commit.branch}")
    loop_report = run_correction_loop(
        orchestrator=orchestrator,
        task=task,
        architecture=architecture,
        pilot_root=project_root,
        first_result=first_result,
        branch=agent_commit.branch,
        backend_agent=backend_agent or BackendDeveloperAgent(),
        git_agent=git_agent,
        save_proposal=lambda result, label: save_patch_proposal(
            result, project_root / "outputs" / "proposed_changes", label, role=role
        ),
        **options,
    )

    record_loop_report(project_root, task["task_id"], loop_report, git_agent, "correction_loop")
    return loop_report


def record_loop_report(project_root, task_id, loop_report, git_agent, kind):
    """Save the loop report, let the agent commit it, and print a summary."""

    output_dir = Path(project_root) / "outputs" / "test_results"
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    loop_path = output_dir / f"{task_id}_{kind}_{timestamp}.json"
    loop_path.write_text(loop_report.model_dump_json(indent=2), encoding="utf-8", newline="\n")
    git_agent.commit_records(task_id, [loop_path], f"Record the {kind.replace('_', ' ')} report")

    for check in loop_report.environment:
        print(f"Environment: {check.status} - {check.detail}")
    for finding in loop_report.validation:
        print(f"WARNING ({finding.check}) {finding.file}:{finding.line} - {finding.message}")
    print(f"Tests generated: {', '.join(loop_report.tests_generated) or 'none'}")
    print(f"Tests run:       {', '.join(loop_report.tests_run) or 'none'}")
    for run in loop_report.runs:
        print(f"Run {run.attempt}: exit code {run.exit_code}, passed {run.passed}")
    for fix in loop_report.fix_attempts:
        print(f"Fix {fix.attempt}: {fix.outcome} - {fix.detail}")
    print(f"Final result: {loop_report.final_result} -> {loop_report.decision}")
    print(f"Report committed on {loop_report.branch}: {loop_path}")
    print("Waiting for the H3 decision. Nothing was pushed or merged.")


def run_retest_agent_branch(project_root, orchestrator, git_agent=None, environment=None, run_command=None):
    """Re-run the tests on the waiting agent branch, without any Gemini request.

    Useful after the environment changed (for example: packages now installed).
    The tests are the test files the agent branch added; no fix is attempted.
    On a frontend branch the npm environment and Vitest are used.
    """

    project_root = Path(project_root)
    approved = request_fresh_approvals(project_root, orchestrator, next_step="the re-test")
    if approved is None:
        return None
    snapshot, architecture = approved

    git_agent = git_agent or PilotGitAgent(project_root, workflow_root=Path(__file__).resolve().parent)
    try:
        branch = git_agent.current_agent_branch()
    except PilotGitError as error:
        print(f"\nNothing to re-test: {error}")
        return None

    task_id = branch_task_id(branch)
    task = next((item for item in snapshot["tasks"] if item.get("task_id") == task_id), None)
    if task is None:
        print(f"\nTask {task_id} of branch {branch} is not in the approved backlog.")
        return None

    options = {
        "build_project_context": build_pilot_project_context,
        "prepare_environment": (environment or PilotEnvironment(project_root)).prepare,
    }

    if branch_role(branch) == "frontend":
        ui_stack = read_ui_stack(snapshot)
        if ui_stack is None:
            return None
        try:
            architecture = ui_architecture_context(architecture, task_id)
            options = frontend_loop_options(project_root, ui_stack, environment, run_command)
        except ValueError as error:
            print(f"\nCannot re-test this frontend branch: {error}")
            return None

    try:
        tests = options.get("frozen", BACKEND_TESTS).on_branch(git_agent)
    except PilotGitError as error:
        print(f"\nNothing to re-test: {error}")
        return None

    print(f"\nRE-TEST on {branch} (no Gemini request)")
    loop_report = run_correction_loop(
        orchestrator=orchestrator,
        task=task,
        architecture=architecture,
        pilot_root=project_root,
        first_result=None,
        tests_generated=tests,
        branch=branch,
        backend_agent=None,
        git_agent=git_agent,
        save_proposal=None,
        max_fix_attempts=0,
        **options,
    )
    loop_report.trigger = "retest"
    record_loop_report(project_root, task_id, loop_report, git_agent, "retest")
    return loop_report


def short_subject(summary, limit=72):
    """First sentence of the agent's summary, short enough for a commit title."""
    first = summary.strip().split(". ")[0].rstrip(".")
    return first if len(first) <= limit else first[: limit - 3].rstrip() + "..."


def apply_patch_with_pilot_git_agent(project_root, patch_path, report_path, record_paths, git_agent=None, role="backend"):
    """Let the Pilot Git Agent commit the proposal on a new agent/ branch.

    Returns the AgentCommitResult, or None when the step could not run; the
    proposal files are then kept for review and the workspace is unchanged.
    """

    try:
        report = json.loads(Path(report_path).read_text(encoding="utf-8"))
        git_agent = git_agent or PilotGitAgent(
            project_root,
            workflow_root=Path(__file__).resolve().parent,
        )
        agent_commit = git_agent.apply_patch_on_new_branch(
            task_id=report["task_id"],
            patch_path=patch_path,
            changed_files=[item["path"] for item in report["changed_files"]],
            summary=short_subject(report["summary"]),
            record_paths=record_paths,
            role=role,
        )
    except Exception as error:
        print(f"\nThe Pilot Git Agent could not apply the patch: {error}")
        print("The proposal is kept for review. No agent branch was created.")
        return None

    print(f"\nThe Pilot Git Agent committed the patch on branch: {agent_commit.branch}")
    print(f"Code commit:    {agent_commit.code_commit}")
    print(f"Records commit: {agent_commit.records_commit}")
    print(f"Files:          {', '.join(agent_commit.committed_files)}")
    waits_for = "the H3 decision" if role == "docs" else "the tests and the H3 decision"
    print(f"Not pushed and not merged: this waits for {waits_for}.")
    return agent_commit


def run_t1_tests(project_root, orchestrator, test_paths=None):
    """Request fresh H1/H2, run the T1 tests, and save the evidence.

    The Testing Agent only runs pytest and reports what happened. It does not
    approve anything: the saved review says "ready_for_review" or
    "needs_revision", and a human still decides at H3.
    """

    project_root = Path(project_root)
    test_paths = test_paths or T1_TEST_PATHS

    approved = request_fresh_approvals(
        project_root,
        orchestrator,
        next_step="the T1 tests",
    )

    if approved is None:
        return None

    snapshot, architecture = approved

    task = next(
        (item for item in snapshot["tasks"] if item.get("task_id") == "T1"),
        None,
    )

    if task is None:
        print("\nApproved task T1 was not found. No tests were run.")
        return None

    print(f"\nRunning the T1 tests: {', '.join(test_paths)}")

    try:
        evidence = run_task_tests(
            orchestrator=orchestrator,
            task=task,
            project_root=project_root,
            test_paths=test_paths,
            architecture=architecture,
        )
    except (RuntimeError, ValueError) as error:
        print(f"\nThe T1 tests could not be run: {error}")
        return None

    output_dir = project_root / "outputs" / "test_results"
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    result_path = output_dir / f"T1_testing_{timestamp}.json"

    # newline="\n" keeps the saved evidence identical on Windows and Linux.
    result_path.write_text(
        json.dumps(evidence, indent=2),
        encoding="utf-8",
        newline="\n",
    )

    check = evidence["testing_result"]["checks"][0]

    print(f"Command:   {check['command']}")
    print(f"Exit code: {check['exit_code']}")
    print(f"Passed:    {check['passed']}")
    print(f"Decision:  {evidence['review']['decision']}")
    print(f"Evidence saved: {result_path}")
    print("No project source files were changed.")

    return result_path


def run_sync_pilot_issues(project_root, orchestrator, github_client=None):
    """After a fresh H1, let the Issue Sync Agent align the pilot's GitHub Issues.

    The agent shows its plan first. Nothing changes on GitHub unless the human
    types 'yes'. World 1 issues (label "world: workflow") are never touched.
    """

    approved = request_fresh_approvals(
        project_root,
        orchestrator,
        next_step="the issue sync",
        require_h2=False,
    )

    if approved is None:
        return None

    snapshot, _ = approved
    tasks = snapshot["tasks"]

    try:
        github = github_client or GitHubClient()
        issues = github.list_all_issues()
    except Exception as error:
        print(f"\nCould not read GitHub Issues: {error}")
        print("No GitHub changes were made.")
        return None

    plan = plan_sync(tasks, issues)

    print("\nISSUE SYNC PLAN (nothing has changed yet)")
    print(describe_plan(plan))

    if not plan["create"] and not plan["close"]:
        print("\nGitHub Issues already match the approved backlog. Nothing to do.")
        return {"created": [], "closed": []}

    confirmation = input(
        "\nApply this plan on GitHub? Enter 'yes' to continue: "
    ).strip().lower()

    if confirmation != "yes":
        print("\nIssue sync cancelled. No GitHub changes were made.")
        return None

    result = IssueSyncAgent(github).execute(plan, tasks)

    print(
        f"\nIssue sync finished: created {len(result['created'])}, "
        f"closed {len(result['closed'])}."
    )
    return result


def run_h2b_ui_stack_command(project_root, orchestrator, architect=None, ask=input):
    """H2b: the Architect proposes the UI stack and the human decides."""

    project_root = Path(project_root)
    snapshot_path = (
        project_root
        / "outputs"
        / "approved_context"
        / "current_approved_context.json"
    )

    try:
        snapshot = load_approved_context(snapshot_path)
    except ValueError as error:
        print(f"\nH2b needs an approved snapshot: {error}")
        print("Stopped. No Gemini request was made.")
        return None

    if snapshot.get("human_approvals", {}).get("H2") != "approved":
        print("\nH2b needs an architecture approved at H2, and the snapshot has none.")
        print("Stopped. No Gemini request was made.")
        return None

    if "ui_stack" in snapshot:
        print("\nThe snapshot already has a UI stack. Approving a new one replaces it.")

    try:
        architect = architect or SoftwareArchitectAgent()
    except ValueError as error:
        print(f"\nThe Architect could not start: {error}")
        return None

    return run_h2b_review(
        orchestrator=orchestrator,
        architect=architect,
        specification=read_text_file(project_root / "pilot" / "pilot_specification.md"),
        snapshot=snapshot,
        pilot_root=project_root,
        store_ui_stack=lambda proposal: write_snapshot(
            snapshot_path,
            {**snapshot, "ui_stack": proposal.model_dump()},
        ),
        ask=ask,
    )


def read_ui_stack(snapshot):
    """The approved UI stack of the snapshot, or None (the reason is printed)."""

    try:
        return UiStackProposal.model_validate(snapshot.get("ui_stack"))
    except ValidationError:
        print("\nThe approved snapshot has no valid UI stack.")
        print("Approve one first with: python main.py --h2b-ui-stack")
        return None


def build_frontend_project_context(project_root, ui_stack):
    """What the Frontend Agent sees: the approved stack plus its current files."""

    return {
        "ui_stack": ui_stack.model_dump(),
        **collect_frontend_file_context(project_root, ui_stack.frontend_folder),
    }


def run_frontend_patch(project_root, orchestrator, task_id, agent=None, git_agent=None, environment=None, run_command=None):
    """One UI task: checks -> agent patch -> agent commit -> npm environment ->
    Vitest -> (one fix) -> Vitest -> loop report committed; then it waits for H3.

    environment and run_command replace the real npm and Vitest in the tests.
    """

    project_root = Path(project_root)

    if task_id not in UI_TASKS:
        print(f"\n{task_id} has no UI work. UI tasks: {', '.join(UI_TASKS)}.")
        return None

    snapshot_path = project_root / "outputs" / "approved_context" / "current_approved_context.json"

    try:
        snapshot = load_approved_context(snapshot_path)
    except ValueError as error:
        print(f"\nThe frontend patch needs an approved snapshot: {error}")
        return None

    ui_stack = read_ui_stack(snapshot)
    if ui_stack is None:
        return None

    try:
        loop_options = frontend_loop_options(project_root, ui_stack, environment, run_command)
    except ValueError as error:
        print(f"\n{error}")
        return None

    git_agent = git_agent or PilotGitAgent(project_root, workflow_root=Path(__file__).resolve().parent)

    # Check the workspace first: a Gemini request must not be spent on a
    # patch that could not be committed anyway.
    try:
        git_agent.ensure_ready()
    except PilotGitError as error:
        print(f"\nThe pilot workspace is not ready: {error}")
        return None

    approved = request_fresh_approvals(project_root, orchestrator, next_step="the frontend patch")
    if approved is None:
        return None

    snapshot, architecture = approved
    task = next((item for item in snapshot["tasks"] if item.get("task_id") == task_id), None)
    if task is None:
        print(f"\nTask {task_id} is not in the approved backlog.")
        return None

    try:
        ui_architecture = ui_architecture_context(architecture, task_id)
        work_package = orchestrator.delegate_task(
            task,
            architecture=ui_architecture,
            project_context=build_frontend_project_context(project_root, ui_stack),
        )
        agent = agent or FrontendDeveloperAgent()
        result = agent.create_patch(work_package)
        if not isinstance(result, FrontendDeveloperResult):
            raise TypeError("The Frontend Developer Agent must return a FrontendDeveloperResult.")
        patch_path, report_path = save_patch_proposal(
            result, project_root / "outputs" / "proposed_changes", role="frontend"
        )
    except Exception as error:
        print(f"\nFrontend patch generation failed: {error}")
        print("No agent branch was created.")
        return None

    print("\nPatch proposal created.")
    print(f"Patch:  {patch_path}")
    print(f"Report: {report_path}")

    agent_commit = apply_patch_with_pilot_git_agent(
        project_root=project_root,
        patch_path=patch_path,
        report_path=report_path,
        record_paths=[patch_path, report_path],
        git_agent=git_agent,
        role="frontend",
    )

    loop_report = None
    if agent_commit is not None:
        loop_report = run_and_record_correction_loop(
            orchestrator=orchestrator,
            task=task,
            architecture=ui_architecture,
            project_root=project_root,
            report_path=report_path,
            agent_commit=agent_commit,
            backend_agent=agent,  # the fix comes from the Frontend Agent
            git_agent=git_agent,
            role="frontend",
            loop_options=loop_options,
        )

    return {"patch_path": patch_path, "report_path": report_path, "agent_commit": agent_commit, "loop_report": loop_report}


def build_docs_project_context(project_root, task_id):
    """What the Documentation Agent sees (layers L0 and L2 of docs/project_context.md).

    existing_files holds only the documentation folder: that is all the agent may
    change, and Python needs the exact old content to build the patch. The code
    is given separately, read-only.
    """

    project_root = Path(project_root)
    settings = DOC_TASKS[task_id]

    docs_root = project_root / DOCS_FOLDER
    if docs_root.is_symlink():
        raise ValueError("The documentation folder cannot be a symlink.")
    existing_files = {}
    if docs_root.is_dir():
        for path in sorted(docs_root.rglob("*.md")):
            if path.is_symlink():
                raise ValueError(f"Symlink found in the documentation folder: {path}")
            existing_files[path.relative_to(project_root).as_posix()] = read_text_file(path)

    source_files = {}
    for relative in settings["source_files"]:
        path = project_root / relative
        # A missing file is shown as missing, so the agent cannot pretend it exists.
        source_files[relative] = read_text_file(path) if path.is_file() else "(this file does not exist)"

    return {
        "pilot_specification": read_text_file(project_root / "pilot" / "pilot_specification.md"),
        "source_files": source_files,
        "code_agent_report": newest_agent_report(project_root, task_id, settings["code_role"]),
        "existing_files": existing_files,
        "existing_file_paths": sorted(existing_files),
        "existing_files_complete": True,
    }


def run_docs_patch(project_root, orchestrator, task_id, agent=None, git_agent=None):
    """One document: checks -> agent writes the document -> agent commit ->
    automatic reference checks; then it waits for H3 (no tests for a document).
    """

    project_root = Path(project_root)

    if task_id not in DOC_TASKS:
        print(f"\nThe Documentation Agent has no document defined for {task_id}. Supported: {', '.join(DOC_TASKS)}.")
        return None

    git_agent = git_agent or PilotGitAgent(project_root, workflow_root=Path(__file__).resolve().parent)

    # Check the workspace first: a Gemini request must not be spent on a
    # document that could not be committed anyway.
    try:
        git_agent.ensure_ready()
    except PilotGitError as error:
        print(f"\nThe pilot workspace is not ready: {error}")
        return None

    approved = request_fresh_approvals(project_root, orchestrator, next_step="the documentation patch")
    if approved is None:
        return None

    snapshot, architecture = approved
    task = next((item for item in snapshot["tasks"] if item.get("task_id") == task_id), None)
    if task is None:
        print(f"\nTask {task_id} is not in the approved backlog.")
        return None

    try:
        work_package = orchestrator.delegate_task(
            task,
            architecture=docs_architecture_context(architecture, task_id),
            project_context=build_docs_project_context(project_root, task_id),
        )
        agent = agent or DocumentationAgent()
        result = agent.create_patch(work_package)
        if not isinstance(result, DocumentationResult):
            raise TypeError("The Documentation Agent must return a DocumentationResult.")
        patch_path, report_path = save_patch_proposal(
            result, project_root / "outputs" / "proposed_changes", role="docs"
        )
    except Exception as error:
        print(f"\nDocumentation patch generation failed: {error}")
        print("No agent branch was created.")
        return None

    print("\nDocument proposal created.")
    print(f"Patch:  {patch_path}")
    print(f"Report: {report_path}")

    agent_commit = apply_patch_with_pilot_git_agent(
        project_root=project_root,
        patch_path=patch_path,
        report_path=report_path,
        record_paths=[patch_path, report_path],
        git_agent=git_agent,
        role="docs",
    )

    findings = []
    if agent_commit is not None:
        _documents, findings = document_checks(git_agent, project_root)
        if findings:
            print("\nAutomatic checks (warnings - the human decides at H3):")
            for finding in findings:
                print(f"  ! {finding.file}:{finding.line} - {finding.message}")
        else:
            print("\nAutomatic checks: no warnings.")
        print("Next: read the document, then run: python main.py --h3-review")

    return {"patch_path": patch_path, "report_path": report_path, "agent_commit": agent_commit, "findings": findings}


def run_h3_review_command(
    project_root, orchestrator, git_agent=None, environment=None, backend_agent=None, github_client=None, ask=input,
    run_command=None,
):
    """H3: the human reviews the waiting agent branch and decides; the agents act."""

    project_root = Path(project_root)
    approved = request_fresh_approvals(project_root, orchestrator, next_step="the H3 review")
    if approved is None:
        return None
    snapshot, architecture = approved

    git_agent = git_agent or PilotGitAgent(project_root, workflow_root=Path(__file__).resolve().parent)
    try:
        branch = git_agent.current_agent_branch()
    except PilotGitError as error:
        print(f"\nNothing to review: {error}")
        return None

    task_id = branch_task_id(branch)
    task = next((item for item in snapshot["tasks"] if item.get("task_id") == task_id), None)
    if task is None:
        print(f"\nTask {task_id} of branch {branch} is not in the approved backlog.")
        return None

    # An H3 "revise" must go back to the agent that owns the branch.
    role = branch_role(branch)
    backend_agent_factory = lambda: backend_agent or BackendDeveloperAgent()
    build_project_context = build_pilot_project_context
    default_environment = PilotEnvironment(project_root)
    revision_options = {}

    if role == "frontend":
        ui_stack = read_ui_stack(snapshot)
        if ui_stack is None:
            return None
        try:
            architecture = ui_architecture_context(architecture, task_id)
            options = frontend_loop_options(project_root, ui_stack, None, run_command)
        except ValueError as error:
            print(f"\nCould not prepare the UI context: {error}")
            return None
        backend_agent_factory = lambda: backend_agent or FrontendDeveloperAgent()
        build_project_context = options["build_project_context"]
        default_environment = FrontendEnvironment(project_root, ui_stack.frontend_folder)
        revision_options = {"run_tests": options["run_tests"], "frozen": options["frozen"]}

    if role == "docs":
        if task_id not in DOC_TASKS:
            print(f"\nThe Documentation Agent has no document defined for {task_id}.")
            return None
        try:
            architecture = docs_architecture_context(architecture, task_id)
        except ValueError as error:
            print(f"\nCould not prepare the documentation context: {error}")
            return None
        backend_agent_factory = lambda: backend_agent or DocumentationAgent()
        build_project_context = lambda root: build_docs_project_context(root, task_id)

    if github_client is None:
        try:
            github_client = GitHubClient()
        except Exception as error:
            print(f"GitHub not available ({error}); the decision still works, without issue comments.")

    outcome = run_h3_review(
        orchestrator=orchestrator,
        task=task,
        architecture=architecture,
        pilot_root=project_root,
        git_agent=git_agent,
        environment=environment or default_environment,
        backend_agent_factory=backend_agent_factory,
        github=github_client,
        build_project_context=build_project_context,
        save_proposal=lambda result, label: save_patch_proposal(
            result, project_root / "outputs" / "proposed_changes", label, role=role
        ),
        ask=ask,
        revision_options=revision_options,
    )

    # A document revision has no test report: its proposal and report are
    # already committed by the agent with the revision.
    if outcome.get("decision") == "revise" and "revision_report" in outcome:
        record_loop_report(project_root, task_id, outcome["revision_report"], git_agent, "h3_revise")

    return outcome


def legacy_run_blocked(project_root):
    """True (with an explanation) when the legacy run must not start.

    The legacy run (no flag) writes a new backlog and architecture. Once an
    approved snapshot exists, the task IDs are frozen after H1, and a new run
    would overwrite them. Only a brand-new project (no snapshot) may use it.
    """

    snapshot_path = Path(project_root) / "outputs" / "approved_context" / "current_approved_context.json"
    if not snapshot_path.exists():
        return False

    print("\nThe legacy run (no flag) is blocked for this pilot.")
    print(f"An approved snapshot exists: {snapshot_path}")
    print("The backlog is frozen after H1; the legacy run would overwrite it.")
    print("Use one of these commands instead:")
    print("  python main.py --backend-patch TASK     (Backend Agent, e.g. T2)")
    print("  python main.py --frontend-patch TASK    (Frontend Agent, e.g. T7)")
    print("  python main.py --docs-patch TASK        (Documentation Agent, e.g. T1)")
    print("  python main.py --h3-review              (review the waiting agent branch)")
    print("  python main.py --retest-agent-branch    (re-run the tests on the waiting branch)")
    print("Nothing was changed.")
    return True


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--resume-patch",
        action="store_true",
        help="Load saved context and request fresh H1 and H2 approvals.",
    )
    parser.add_argument(
        "--run-t1-tests",
        action="store_true",
        help="Request fresh H1 and H2 approvals, then run and save the T1 tests.",
    )
    parser.add_argument(
        "--retest-agent-branch",
        action="store_true",
        help="Request fresh H1 and H2, then re-run the tests on the waiting agent branch (no Gemini request).",
    )
    parser.add_argument(
        "--h2b-ui-stack",
        action="store_true",
        help="H2b: the Architect proposes the UI stack; approve, revise, or reject it.",
    )
    parser.add_argument(
        "--backend-patch",
        metavar="TASK",
        help="Let the Backend Agent propose and commit a patch for this task, then test it, for example T2.",
    )
    parser.add_argument(
        "--frontend-patch",
        metavar="TASK",
        help="Let the Frontend Agent propose and commit a UI patch for this task, for example T7.",
    )
    parser.add_argument(
        "--docs-patch",
        metavar="TASK",
        help="Let the Documentation Agent write and commit the document for this task, for example T1.",
    )
    parser.add_argument(
        "--h3-review",
        action="store_true",
        help="H3: review the waiting agent branch and approve, revise, or reject it.",
    )
    parser.add_argument(
        "--sync-pilot-issues",
        action="store_true",
        help="Request a fresh H1, then align the pilot's GitHub Issues with the approved backlog.",
    )
    args = parser.parse_args(argv)

    workflow_root = Path(__file__).resolve().parent

    # From here on, project_root means the PILOT workspace (World 2):
    # specification, architecture, snapshot, patches and pilot code live there.
    project_root = get_pilot_root(workflow_root)
    print(f"Pilot workspace: {project_root}")

    specification_path = (
        project_root / "pilot" / "pilot_specification.md"
    )
    architecture_path = (
        project_root / "pilot" / "architecture_design.md"
    )

    if not specification_path.exists():
        raise FileNotFoundError(
            f"Project specification not found: {specification_path}"
        )

    specification = read_text_file(specification_path)
    orchestrator = OrchestratorAgent()

    if args.resume_patch:
        run_resume_patch(
            project_root=project_root,
            orchestrator=orchestrator,
        )
        return

    if args.run_t1_tests:
        run_t1_tests(
            project_root=project_root,
            orchestrator=orchestrator,
        )
        return

    if args.h2b_ui_stack:
        run_h2b_ui_stack_command(
            project_root=project_root,
            orchestrator=orchestrator,
        )
        return

    if args.backend_patch:
        run_backend_patch(
            project_root=project_root,
            orchestrator=orchestrator,
            task_id=args.backend_patch,
        )
        return

    if args.frontend_patch:
        run_frontend_patch(
            project_root=project_root,
            orchestrator=orchestrator,
            task_id=args.frontend_patch,
        )
        return

    if args.docs_patch:
        run_docs_patch(
            project_root=project_root,
            orchestrator=orchestrator,
            task_id=args.docs_patch,
        )
        return

    if args.h3_review:
        run_h3_review_command(
            project_root=project_root,
            orchestrator=orchestrator,
        )
        return

    if args.retest_agent_branch:
        run_retest_agent_branch(
            project_root=project_root,
            orchestrator=orchestrator,
        )
        return

    if args.sync_pilot_issues:
        run_sync_pilot_issues(
            project_root=project_root,
            orchestrator=orchestrator,
        )
        return

    # The legacy run: only for a brand-new project. Checked before any Gemini call.
    if legacy_run_blocked(project_root):
        return

    checker = TicketQualityChecker()

    print("\nSTEP 1 — Analyzing the project specification...")

    try:
        plan = orchestrator.analyze_project(specification)
    except Exception as error:
        print(f"\nProject analysis failed: {error}")
        return

    print("\nSTEP 2 — Preparing the task backlog and H1 approval...")

    try:
        approved_plan, tasks = prepare_and_approve_backlog(
            orchestrator=orchestrator,
            checker=checker,
            specification=specification,
            initial_plan=plan,
        )
    except Exception as error:
        print(f"\nBacklog preparation failed: {error}")
        print("No GitHub Issues will be created.")
        return

    if approved_plan is None or tasks is None:
        return

    print("\nSTEP 3 — Generating the technical architecture...")

    try:
        architect = SoftwareArchitectAgent()
        architecture = architect.design_architecture(
            specification,
            tasks,
        )

        if not architecture or not architecture.strip():
            print("The architect returned an empty document.")
            print(
                "Workflow stopped before H2 approval and GitHub synchronization."
            )
            return

        print("\nArchitecture document generated.")
        print("\nChecking architecture task-ID coverage...")

        if not validate_architecture(architecture, tasks):
            print(
                "\nArchitecture validation failed. "
                "The previous architecture file has not been overwritten."
            )
            print(
                "Workflow stopped before H2 approval and GitHub synchronization."
            )
            return

        architecture_path.parent.mkdir(parents=True, exist_ok=True)
        architecture_path.write_text(architecture, encoding="utf-8")

        print("\nArchitecture document saved and validated.")
        print(f"Saved to: {architecture_path}")

    except Exception as error:
        print(f"\nArchitecture generation, validation, or saving failed: {error}")
        print(
            "Workflow stopped before H2 approval and GitHub synchronization."
        )
        return

    print("\nSTEP 4 — Requesting H2 architecture approval...")

    if not review_and_approve_architecture(
        orchestrator=orchestrator,
        architect=architect,
        architecture_path=architecture_path,
        tasks=tasks,
    ):
        print("\nWorkflow stopped after H2 was not approved.")
        return

    # Capture the exact on-disk document that passed H2,
    # including any edits made during human review.
    try:
        architecture = read_text_file(architecture_path)
    except (OSError, UnicodeError) as error:
        print(f"\nCould not reread the H2-approved architecture: {error}")
        print("Stopping before coding-agent execution.")
        return

    if not architecture.strip():
        print("\nThe H2-approved architecture file is empty.")
        print("Stopping before coding-agent execution.")
        return

    if not orchestrator.workflow_state["backlog_approved"]:
        print("\nSafety check failed: H1 approval is missing.")
        print("Stopping before coding-agent execution.")
        return

    if not orchestrator.workflow_state["architecture_approved"]:
        print("\nSafety check failed: H2 approval is missing.")
        print("Stopping before coding-agent execution.")
        return

    try:
        snapshot_path = save_approved_context(
            orchestrator=orchestrator,
            project_root=project_root,
            plan=approved_plan,
            tasks=tasks,
            architecture=architecture,
        )
        print("\nApproved workflow context saved:")
        print(snapshot_path)
    except Exception as error:
        print(f"\nCould not save approved workflow context: {error}")
        print("Stopping before coding-agent execution.")
        return

    patch_confirmation = input(
        "\nGenerate a proposed T1 backend patch? "
        "Enter 'yes' to create a patch artifact: "
    ).strip().lower()

    if patch_confirmation != "yes":
        print("\nPatch generation cancelled.")
        print("No GitHub synchronization was performed.")
        return

    requirements_path = project_root / "requirements.txt"

    try:
        project_context = {
            "pilot_specification": specification,
            "requirements": read_text_file(requirements_path),
            "python_version": sys.version,
        }
        project_context.update(collect_backend_file_context(project_root))
    except (OSError, UnicodeError) as error:
        print(f"\nCould not read project context: {error}")
        print("No patch was generated.")
        return

    try:
        patch_path, report_path = create_t1_backend_patch(
            orchestrator=orchestrator,
            tasks=tasks,
            architecture=architecture,
            project_context=project_context,
            output_dir=project_root / "outputs" / "proposed_changes",
        )
    except Exception as error:
        print(f"\nPatch generation failed: {error}")
        print("No GitHub synchronization was performed.")
        return

    print("\nPatch proposal created. No project source files were changed.")
    print(f"Patch:  {patch_path}")
    print(f"Report: {report_path}")
    print("Review the patch before applying it.")
    print("No GitHub synchronization was performed.")


if __name__ == "__main__":
    main()
