
from pathlib import Path

from agents.orchestrator import OrchestratorAgent
from integrations.github_client import GitHubClient
from agents.ticket_quality_checker import TicketQualityChecker
from agents.software_architect import SoftwareArchitectAgent


def main():
    specification_path = (
        Path(__file__).parent
        / "pilot"
        / "pilot_specification.md"
    )

    if not specification_path.exists():
        raise FileNotFoundError(
            f"Project specification not found: {specification_path}"
        )

    specification = specification_path.read_text(encoding="utf-8")

    orchestrator = OrchestratorAgent()
    plan = orchestrator.analyze_project(specification)

    while True:
        decision = orchestrator.request_plan_approval(plan)

        if decision == "approve":
            break

        if decision == "reject":
            print("\nPlan rejected. Workflow stopped.")
            return

        feedback = input(
            "\nDescribe the changes you want in the plan: "
        ).strip()

        if not feedback:
            print("Feedback cannot be empty. Please try again.")
            continue

        plan = orchestrator.revise_plan(
            specification,
            plan,
            feedback,
        )

    # Nothing is sent to GitHub before this point.
    print("\nPlan approved. Preparing GitHub Issues...")

        
    tasks = orchestrator.convert_plan_to_tasks(plan)

    # Run ticket quality checks before asking to create GitHub issues.
    checker = TicketQualityChecker()
    report = checker.validate_tasks(tasks)

    print("\n" + "=" * 50)
    print("TICKET QUALITY CHECK")
    print("=" * 50)

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
        print("No GitHub issues will be created.")
        return

    print("\nAll blocking validation checks passed.")
    print(f"Tasks checked: {len(tasks)}")

    for task in tasks:
        print(f"- {task['task_id']}: {task['title']}")


    
    # Generate the technical architecture from the full task list.
    print("\nGenerating the technical architecture...")

    architect = SoftwareArchitectAgent()
    architecture = architect.design_architecture(
        specification,
        tasks,
    )

    # Save the architecture document for human review.
    architecture_path = (
        Path(__file__).parent
        / "pilot"
        / "architecture_design.md"
    )

    architecture_path.write_text(
        architecture,
        encoding="utf-8",
    )

    print("\nArchitecture document generated.")
    print(f"Saved to: {architecture_path}")

    # Require human approval before GitHub synchronization.
    print("\nPlease review pilot/architecture_design.md.")
    architecture_decision = input(
        "Approve the architecture to continue? "
        "Enter 'yes' to continue: "
    ).strip().lower()

    if architecture_decision != "yes":
        print("\nArchitecture not approved.")
        print("GitHub synchronization cancelled.")
        return



    confirmation = input(
        "\nCreate or reuse GitHub Issues for these tasks? "
        "Enter 'yes' to continue: "
    ).strip().lower()

    if confirmation != "yes":
        print("GitHub issue creation cancelled.")
        return

    github = GitHubClient()
    github.test_connection()

    for task in tasks:
        github.create_task_issue(task)

    print("\nGitHub task synchronization finished.")


if __name__ == "__main__":
    main()
