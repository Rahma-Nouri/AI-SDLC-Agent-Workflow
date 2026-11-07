import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from agents.agent_contracts import BackendDeveloperResult
from agents.backend_developer import BackendDeveloperAgent
from agents.correction_loop import run_correction_loop
from agents.h3_review import run_h3_review
from agents.issue_sync_agent import IssueSyncAgent, describe_plan, plan_sync
from agents.orchestrator import OrchestratorAgent
from agents.pilot_environment import PilotEnvironment
from agents.pilot_git_agent import PilotGitAgent, PilotGitError
from agents.software_architect import SoftwareArchitectAgent
from agents.testing_workflow import run_task_tests
from agents.ticket_quality_checker import TicketQualityChecker
from integrations.github_client import GitHubClient


# The tests that belong to T1. They arrived with the accepted T1 patch
# (see "tests_added" in its report) and were applied in P1.2.
T1_TEST_PATHS = ["tests/test_backend_database_schema.py"]

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


def review_and_approve_architecture(
    orchestrator,
    architect,
    architecture_path,
    tasks,
):
    """H2: approve only the current on-disk architecture if it validates."""

    while True:
        print("\n" + "=" * 60)
        print("H2 — HUMAN APPROVAL: SOFTWARE ARCHITECTURE")
        print("=" * 60)
        print(f"Review this document:\n{architecture_path}")

        decision = input(
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
                print("Revise the document before requesting approval again.")
                continue

            orchestrator.record_approval("H2", "approve")
            print("\nH2 approved: validated architecture accepted.")
            return True

        if decision == "reject":
            orchestrator.record_approval("H2", "reject")
            print("\nArchitecture rejected.")
            return False

        if decision == "revise":
            feedback = input(
                "\nWhat needs to change in the architecture? "
                "Enter your review notes: "
            ).strip()

            if not feedback:
                print("Review notes cannot be empty.")
                continue

            print("\nRevision notes:")
            print(feedback)
            print("\nPlease edit the architecture document manually:")
            print(architecture_path)
            print(
                "\nAfter editing, return here and choose 'approve'. "
                "The current file will be validated again."
            )

            input("Press Enter after you have finished editing the file...")
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
    """Generate and save a T1 backend patch without applying it."""

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
            if item.get("task_id") == "T1"
        ),
        None,
    )

    if task is None:
        raise ValueError("Approved task T1 was not found in the task list.")

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


def save_patch_proposal(result, output_dir, label=""):
    """Save an agent's patch and report; label marks fixes, e.g. "fix1_"."""

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    patch_path = output_dir / f"{result.task_id}_backend_{label}{timestamp}.patch"
    report_path = output_dir / f"{result.task_id}_backend_{label}{timestamp}_report.json"

    # Write the patch with LF ("\n") line endings on every operating system.
    # Without newline="\n", Windows text mode turns each "\n" into "\r\n",
    # and the CRLF patch then fails `git apply --check`.
    patch_path.write_text(result.patch, encoding="utf-8", newline="\n")
    report_path.write_text(
        result.model_dump_json(indent=2),
        encoding="utf-8",
    )

    return patch_path, report_path


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

    snapshot = {
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

    snapshot_dir = Path(project_root) / "outputs" / "approved_context"
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    snapshot_path = snapshot_dir / "current_approved_context.json"
    temporary_path = snapshot_path.with_suffix(".json.tmp")

    temporary_path.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary_path.replace(snapshot_path)

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


def run_resume_patch(project_root, orchestrator, agent=None, git_agent=None):
    """Load saved context, request fresh approvals, and offer a patch."""

    project_root = Path(project_root)

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

    confirmation = input(
        "\nGenerate a proposed T1 backend patch from this approved context? "
        "Enter 'yes' to continue: "
    ).strip().lower()

    if confirmation != "yes":
        print("\nPatch generation cancelled.")
        return None

    specification_path = (
        project_root / "pilot" / "pilot_specification.md"
    )
    readme_path = project_root / "README.md"
    requirements_path = project_root / "requirements.txt"

    try:
        project_context = {
            "pilot_specification": read_text_file(specification_path),
            "readme": read_text_file(readme_path),
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
        patch_path, report_path = create_t1_backend_patch(
            orchestrator=orchestrator,
            tasks=tasks,
            architecture=architecture,
            project_context=project_context,
            output_dir=project_root / "outputs" / "proposed_changes",
            agent=agent,
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
        )

    print("No GitHub synchronization was performed.")

    return {
        "patch_path": patch_path,
        "report_path": report_path,
        "agent_commit": agent_commit,
        "loop_report": loop_report,
    }


def build_pilot_project_context(project_root):
    """What the Backend Agent sees: pilot documents plus the current files."""

    project_root = Path(project_root)
    context = {
        "pilot_specification": read_text_file(project_root / "pilot" / "pilot_specification.md"),
        "readme": read_text_file(project_root / "README.md"),
        "requirements": read_text_file(project_root / "requirements.txt"),
        "python_version": sys.version,
    }
    context.update(collect_backend_file_context(project_root))
    return context


def run_and_record_correction_loop(
    orchestrator, task, architecture, project_root, report_path, agent_commit, backend_agent, git_agent,
    environment=None,
):
    """Run the correction loop on the agent branch and commit its report there."""

    project_root = Path(project_root)
    first_result = BackendDeveloperResult.model_validate_json(
        Path(report_path).read_text(encoding="utf-8")
    )

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
        build_project_context=build_pilot_project_context,
        save_proposal=lambda result, label: save_patch_proposal(
            result, project_root / "outputs" / "proposed_changes", label
        ),
        prepare_environment=(environment or PilotEnvironment(project_root)).prepare,
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


def run_retest_agent_branch(project_root, orchestrator, git_agent=None, environment=None):
    """Re-run the tests on the waiting agent branch, without any Gemini request.

    Useful after the environment changed (for example: packages now installed).
    The tests are the test files the agent branch added; no fix is attempted.
    """

    project_root = Path(project_root)
    approved = request_fresh_approvals(project_root, orchestrator, next_step="the re-test")
    if approved is None:
        return None
    snapshot, architecture = approved

    git_agent = git_agent or PilotGitAgent(project_root, workflow_root=Path(__file__).resolve().parent)
    try:
        branch = git_agent.current_agent_branch()
        tests = git_agent.files_added_on_branch("tests/")
    except PilotGitError as error:
        print(f"\nNothing to re-test: {error}")
        return None

    # agent/T1-attempt-1 -> T1
    task_id = branch[len("agent/"):].split("-attempt-")[0]
    task = next((item for item in snapshot["tasks"] if item.get("task_id") == task_id), None)
    if task is None:
        print(f"\nTask {task_id} of branch {branch} is not in the approved backlog.")
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
        build_project_context=build_pilot_project_context,
        save_proposal=None,
        prepare_environment=(environment or PilotEnvironment(project_root)).prepare,
        max_fix_attempts=0,
    )
    loop_report.trigger = "retest"
    record_loop_report(project_root, task_id, loop_report, git_agent, "retest")
    return loop_report


def short_subject(summary, limit=72):
    """First sentence of the agent's summary, short enough for a commit title."""
    first = summary.strip().split(". ")[0].rstrip(".")
    return first if len(first) <= limit else first[: limit - 3].rstrip() + "..."


def apply_patch_with_pilot_git_agent(project_root, patch_path, report_path, record_paths, git_agent=None):
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
        )
    except Exception as error:
        print(f"\nThe Pilot Git Agent could not apply the patch: {error}")
        print("The proposal is kept for review. No agent branch was created.")
        return None

    print(f"\nThe Pilot Git Agent committed the patch on branch: {agent_commit.branch}")
    print(f"Code commit:    {agent_commit.code_commit}")
    print(f"Records commit: {agent_commit.records_commit}")
    print(f"Files:          {', '.join(agent_commit.committed_files)}")
    print("Not pushed and not merged: this waits for the tests and the H3 decision.")
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


def run_h3_review_command(
    project_root, orchestrator, git_agent=None, environment=None, backend_agent=None, github_client=None, ask=input
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

    task_id = branch[len("agent/"):].split("-attempt-")[0]
    task = next((item for item in snapshot["tasks"] if item.get("task_id") == task_id), None)
    if task is None:
        print(f"\nTask {task_id} of branch {branch} is not in the approved backlog.")
        return None

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
        environment=environment or PilotEnvironment(project_root),
        backend_agent_factory=lambda: backend_agent or BackendDeveloperAgent(),
        github=github_client,
        build_project_context=build_pilot_project_context,
        save_proposal=lambda result, label: save_patch_proposal(
            result, project_root / "outputs" / "proposed_changes", label
        ),
        ask=ask,
    )

    if outcome.get("decision") == "revise":
        record_loop_report(project_root, task_id, outcome["revision_report"], git_agent, "h3_revise")

    return outcome


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

    readme_path = project_root / "README.md"
    requirements_path = project_root / "requirements.txt"

    try:
        project_context = {
            "pilot_specification": specification,
            "readme": read_text_file(readme_path),
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
