
from pathlib import Path

from agents.orchestrator import OrchestratorAgent
from agents.ticket_quality_checker import TicketQualityChecker
from agents.software_architect import SoftwareArchitectAgent


def main():
    project_root = Path(__file__).parent

    # Load the project specification.
    specification_path = (
        project_root / "pilot" / "pilot_specification.md"
    )
    specification = specification_path.read_text(encoding="utf-8")

    # Generate tasks using the Orchestrator.
    print("Generating project tasks...")
    orchestrator = OrchestratorAgent()
    plan = orchestrator.analyze_project(specification)
    tasks = orchestrator.convert_plan_to_tasks(plan)

    # Validate task quality before architecture generation.
    checker = TicketQualityChecker()
    report = checker.validate_tasks(tasks)

    if not report["valid"]:
        print("\nTask validation failed:")
        for error in report["errors"]:
            print(f"- {error}")
        return

    print(f"\nTask validation passed: {len(tasks)} tasks")

    # Generate architecture using the complete task list.
    print("\nGenerating architecture...")
    architect = SoftwareArchitectAgent()
    architecture = architect.design_architecture(
        specification,
        tasks,
    )

    # Save the result locally. No GitHub calls are made.
    output_path = project_root / "pilot" / "architecture_design.md"
    output_path.write_text(architecture, encoding="utf-8")

    print(f"\nArchitecture saved to: {output_path}")
    print(f"Architecture length: {len(architecture)} characters")
    print("\nFirst 500 characters:\n")
    print(architecture[:500])


if __name__ == "__main__":
    main()
