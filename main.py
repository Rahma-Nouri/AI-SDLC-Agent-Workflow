
from pathlib import Path

from agents.project_manager import ProjectManagerAgent



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
    orchestrator = ProjectManagerAgent()

    plan = orchestrator.analyze_project(specification)

    while True:
        decision = orchestrator.request_plan_approval(plan)

        if decision == "approve":
            print(
                "\nPlan approved. It is ready for the next workflow stage."
            )
            break

        elif decision == "reject":
            print("\nPlan rejected. Workflow stopped.")
            return

        else:
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


if __name__ == "__main__":
    main()
