
from pathlib import Path

from agents.orchestrator import OrchestratorAgent
from agents.ticket_quality_checker import TicketQualityChecker
from agents.software_architect import SoftwareArchitectAgent
from integrations.github_client import GitHubClient

from datetime import datetime

from agents.backend_developer import BackendDeveloperAgent
from agents.agent_contracts import BackendDeveloperResult


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
    """
    Generate and validate tasks before requesting H1 approval.
    Revisions must pass validation and receive approval again.
    """

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

        # H1: approve the plan and exact structured task backlog.
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

        # The loop regenerates and validates the tasks before H1 is requested again.


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
    """
    H2: approve only an existing architecture that passes task-ID validation.

    If revision is requested, the human edits the file manually.
    The edited file is checked again before approval can be recorded.
    """

    while True:
        print("\n" + "=" * 60)
        print("H2 — HUMAN APPROVAL: SOFTWARE ARCHITECTURE")
        print("=" * 60)
        print(f"Review this document:\n{architecture_path}")

        decision = input(
            "\nEnter 'approve', 'revise', or 'reject': "
        ).strip().lower()

        if decision == "approve":
            # Read the current file, not the earlier in-memory architecture.
            try:
                architecture = architecture_path.read_text(encoding="utf-8")
            except OSError as error:
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
        raise RuntimeError("H1 approval is required before coding-agent execution.")

    if not orchestrator.workflow_state["architecture_approved"]:
        raise RuntimeError("H2 approval is required before coding-agent execution.")

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

    patch_path.write_text(result.patch, encoding="utf-8")
    report_path.write_text(
        result.model_dump_json(indent=2),
        encoding="utf-8",
    )

    return patch_path, report_path


def main():
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

    specification = specification_path.read_text(encoding="utf-8")

    orchestrator = OrchestratorAgent()
    checker = TicketQualityChecker()

    # STEP 1: Analyze requirements and prepare the initial plan.
    print("\nSTEP 1 — Analyzing the project specification...")

    try:
        plan = orchestrator.analyze_project(specification)
    except Exception as error:
        print(f"\nProject analysis failed: {error}")
        return

    # STEP 2: Generate, validate, and approve the task backlog.
    # H1 occurs before creating GitHub Issues.
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

    # STEP 3: Generate and validate the technical architecture.
    print("\nSTEP 3 — Generating the technical architecture...")

    try:
        architect = SoftwareArchitectAgent()

        architecture = architect.design_architecture(
            specification,
            tasks,
        )

        if not architecture or not architecture.strip():
            print("The architect returned an empty document.")
            print("Workflow stopped before H2 approval and GitHub synchronization.")
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

        # Save only after the generated architecture passes validation.
        architecture_path.parent.mkdir(parents=True, exist_ok=True)
        architecture_path.write_text(
            architecture,
            encoding="utf-8",
        )

        print("\nArchitecture document saved and validated.")
        print(f"Saved to: {architecture_path}")

    except Exception as error:
        print(f"\nArchitecture generation, validation, or saving failed: {error}")
        print("Workflow stopped before H2 approval and GitHub synchronization.")
        return

    # STEP 4: H2 — Human architecture approval.
    print("\nSTEP 4 — Requesting H2 architecture approval...")

    if not review_and_approve_architecture(
        orchestrator=orchestrator,
        architect=architect,
        architecture_path=architecture_path,
        tasks=tasks,
    ):
        print("\nGitHub synchronization cancelled.")
        return

    # STEP 5: Check approval state before GitHub synchronization.
    if not orchestrator.workflow_state["backlog_approved"]:
        print("\nSafety check failed: H1 approval is missing.")
        print("GitHub synchronization cancelled.")
        return

    if not orchestrator.workflow_state["architecture_approved"]:
        print("\nSafety check failed: H2 approval is missing.")
        print("GitHub synchronization cancelled.")
        return


    patch_confirmation = input(
        "\nGenerate a proposed T1 backend patch? "
        "Enter 'yes' to create a patch artifact: "
    ).strip().lower()

    if patch_confirmation == "yes":
        try:
            readme_path = project_root / "README.md"
            project_context = {
                "pilot_specification": specification,
                "readme": (
                    readme_path.read_text(encoding="utf-8")
                    if readme_path.exists()
                    else ""
                ),
            }

            patch_path, report_path = create_t1_backend_patch(
                orchestrator=orchestrator,
                tasks=tasks,
                architecture=architecture,
                project_context=project_context,
                output_dir=project_root / "outputs" / "proposed_changes",
            )

            print("\nPatch proposal created. No project source files were changed.")
            print(f"Patch:  {patch_path}")
            print(f"Report: {report_path}")
            print("Review the patch before applying it.")
            print("No GitHub synchronization was performed.")
        except Exception as error:
            print(f"\nPatch generation failed: {error}")
            print("No GitHub synchronization was performed.")

        return

    

    confirmation = input(
        "\nH1 and H2 are approved. Synchronize the approved tasks "
        "with GitHub Issues? Enter 'yes' to continue: "
    ).strip().lower()

    if confirmation != "yes":
        print("\nGitHub synchronization cancelled by the user.")
        return

    # STEP 6: Synchronize the approved backlog with GitHub.
    print("\nSTEP 6 — Synchronizing approved tasks with GitHub...")

    try:
        github = GitHubClient()
        github.test_connection()

        for task in tasks:
            github.create_task_issue(task)

        print("\nGitHub task synchronization finished.")

    except Exception as error:
        print(f"\nGitHub synchronization failed: {error}")
        print(
            "Some Issues may already have been created. "
            "Check GitHub before retrying."
        )
        return

    print("\n" + "=" * 60)
    print("WORKFLOW CHECKPOINT COMPLETE")
    print("=" * 60)
    print("H1: Plan and task backlog approved.")
    print("H2: Architecture approved and validated.")
    print("GitHub: Approved tasks synchronized.")
    print(
        "\nNext planned stage: delegate approved tasks to developer "
        "agents, run tests, and request H3 implementation acceptance."
    )


if __name__ == "__main__":
    main()
