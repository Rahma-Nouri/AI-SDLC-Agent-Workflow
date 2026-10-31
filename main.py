import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from agents.agent_contracts import BackendDeveloperResult
from agents.backend_developer import BackendDeveloperAgent
from agents.orchestrator import OrchestratorAgent
from agents.software_architect import SoftwareArchitectAgent
from agents.testing_workflow import run_task_tests
from agents.ticket_quality_checker import TicketQualityChecker


# The tests that belong to T1. They arrived with the accepted T1 patch
# (see "tests_added" in its report) and were applied in P1.2.
T1_TEST_PATHS = ["tests/test_backend_database_schema.py"]


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

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    patch_path = output_dir / f"T1_backend_{timestamp}.patch"
    report_path = output_dir / f"T1_backend_{timestamp}_report.json"

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


def request_fresh_approvals(project_root, orchestrator, next_step):
    """Load the saved approved context and ask for fresh H1 and H2.

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


def run_resume_patch(project_root, orchestrator, agent=None):
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

    print("\nPatch proposal created. No project source files were changed.")
    print(f"Patch:  {patch_path}")
    print(f"Report: {report_path}")
    print("Review the patch before applying it.")
    print("No GitHub synchronization was performed.")

    return patch_path, report_path


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
    args = parser.parse_args(argv)

    project_root = Path(__file__).resolve().parent

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
