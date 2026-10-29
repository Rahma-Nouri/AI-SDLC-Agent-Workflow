
from pathlib import Path

from agents.software_architect import SoftwareArchitectAgent


def main():
    project_root = Path(__file__).parent

    specification_path = (
        project_root / "pilot" / "pilot_specification.md"
    )

    if not specification_path.exists():
        raise FileNotFoundError(
            f"Pilot specification not found: {specification_path}"
        )

    specification = specification_path.read_text(encoding="utf-8")

    # Small sample for the first independent test.
    # These are test inputs, not changes to GitHub Issues.
    tasks = [
        {
            "task_id": "TASK-01",
            "title": "Exam Material Upload and Parsing Service",
            "description": (
                "Allow users to upload supported exam materials "
                "and extract their text for later processing."
            ),
            "acceptance_criteria": [
                "The service accepts supported exam files.",
                "Extracted text is returned or made available to the next component.",
            ],
            "dependencies": [],
        },
        {
            "task_id": "TASK-02",
            "title": "Exam Structure and Topic Analysis Engine",
            "description": (
                "Analyze extracted exam content to identify topics "
                "and the structure of the exam."
            ),
            "acceptance_criteria": [
                "The analysis identifies topics from the provided content.",
                "The result is structured for use by the exam generation component.",
            ],
            "dependencies": ["TASK-01"],
        },
    ]

    architect = SoftwareArchitectAgent()

    print("Generating the initial architecture design...")
    architecture = architect.design_architecture(
        specification=specification,
        tasks=tasks,
    )

    output_path = project_root / "pilot" / "architecture_design.md"
    output_path.write_text(architecture, encoding="utf-8")

    print(f"\nArchitecture design saved to:\n{output_path}")
    print("\nPreview:\n")
    print(architecture[:2000])


if __name__ == "__main__":
    main()
