
import os
import re

from dotenv import load_dotenv
from google import genai


class SoftwareArchitectAgent:
    """Design the system architecture from requirements and approved tasks."""

    TASK_ID_PATTERN = re.compile(
        r"(?<![A-Za-z0-9_-])(?:TASK-\d+|T\d+)(?![A-Za-z0-9_-])",
        re.IGNORECASE,
    )

    VALID_TASK_ID_PATTERN = re.compile(
        r"(?:T\d+|TASK-\d+)",
        re.IGNORECASE,
    )

    def __init__(self):
        load_dotenv()

        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError(
                "GEMINI_API_KEY is missing. Check your .env file."
            )

        self.client = genai.Client(api_key=api_key)
        self.model = "gemini-3.8-flash"

    @staticmethod
    def validate_task_coverage(architecture, tasks):
        """
        Check that every approved task ID appears in the architecture.

        Supports IDs such as T1, T12, TASK-01, and TASK-12.

        Repeated mentions are not considered duplicate task definitions.
        This method validates ID coverage, not semantic design quality.
        """
        if not isinstance(architecture, str):
            raise TypeError("Architecture must be a string.")

        if not isinstance(tasks, list):
            raise TypeError("Tasks must be provided as a list.")

        expected_ids = []

        for index, task in enumerate(tasks):
            if not isinstance(task, dict):
                raise TypeError(
                    f"Task at index {index} must be a dictionary."
                )

            task_id = task.get("task_id")

            if not isinstance(task_id, str) or not task_id.strip():
                raise ValueError(
                    f"Task at index {index} has a missing or invalid task_id."
                )

            task_id = task_id.strip()

            if not SoftwareArchitectAgent.VALID_TASK_ID_PATTERN.fullmatch(
                task_id
            ):
                raise ValueError(
                    f"Unsupported task ID format: {task_id!r}. "
                    "Expected formats such as T1 or TASK-01."
                )

            expected_ids.append(task_id)

        normalized_expected = [
            task_id.upper() for task_id in expected_ids
        ]

        if len(normalized_expected) != len(set(normalized_expected)):
            raise ValueError("Input tasks contain duplicate task IDs.")

        expected_by_normalized = {
            task_id.upper(): task_id for task_id in expected_ids
        }

        found_ids = SoftwareArchitectAgent.TASK_ID_PATTERN.findall(
            architecture
        )
        normalized_found = [task_id.upper() for task_id in found_ids]

        expected_set = set(expected_by_normalized)
        found_set = set(normalized_found)

        missing_ids = sorted(
            expected_by_normalized[task_id]
            for task_id in expected_set - found_set
        )

        unexpected_ids = sorted(
            task_id for task_id in found_set - expected_set
        )

        canonical_found_ids = sorted(
            expected_by_normalized.get(task_id, task_id)
            for task_id in found_set
        )

        return {
            "valid": not missing_ids and not unexpected_ids,
            "expected_ids": expected_ids,
            "found_ids": canonical_found_ids,
            "missing_ids": missing_ids,
            "unexpected_ids": unexpected_ids,
            # Repeated mentions do not establish duplicate task definitions.
            "duplicated_ids": [],
        }

    def design_architecture(self, specification, tasks):
        """Generate a technical design without implementing application code."""

        if not isinstance(specification, str) or not specification.strip():
            raise ValueError("Project specification cannot be empty.")

        if not isinstance(tasks, list) or not tasks:
            raise ValueError("At least one approved task is required.")

        # Validate task IDs and reject duplicate IDs before calling Gemini.
        self.validate_task_coverage("", tasks)

        task_text_parts = []

        required_fields = (
            "task_id",
            "title",
            "description",
            "acceptance_criteria",
            "dependencies",
        )

        for task in tasks:
            if not isinstance(task, dict):
                raise TypeError("Every task must be a dictionary.")

            missing_fields = [
                field for field in required_fields if field not in task
            ]

            if missing_fields:
                raise ValueError(
                    f"Task {task.get('task_id', '<unknown>')} is missing "
                    f"required fields: {', '.join(missing_fields)}"
                )

            criteria = task["acceptance_criteria"]
            dependencies = task["dependencies"]

            if not isinstance(criteria, list):
                raise TypeError(
                    f"Acceptance criteria for {task['task_id']} "
                    "must be a list."
                )

            if not isinstance(dependencies, list):
                raise TypeError(
                    f"Dependencies for {task['task_id']} must be a list."
                )

            if not all(isinstance(item, str) for item in criteria):
                raise TypeError(
                    f"Every acceptance criterion for {task['task_id']} "
                    "must be a string."
                )

            if not all(isinstance(item, str) for item in dependencies):
                raise TypeError(
                    f"Every dependency for {task['task_id']} "
                    "must be a string."
                )

            task_text_parts.append(
                f"Task ID: {task['task_id']}\n"
                f"Title: {task['title']}\n"
                f"Description: {task['description']}\n"
                "Acceptance criteria:\n"
                + "\n".join(f"- {criterion}" for criterion in criteria)
                + "\nDependencies: "
                + (", ".join(dependencies) if dependencies else "None")
            )

        task_text = "\n\n".join(task_text_parts)

        prompt = f"""
You are the Software Architect Agent in an AI-supported
end-to-end software development workflow.

Your responsibility is to DESIGN the system, not implement it.

PILOT PROJECT SPECIFICATION
{specification}

APPROVED DEVELOPMENT TASKS
{task_text}

Produce a technical architecture document with these sections:

1. System Overview
   - Explain the proposed system and its main responsibilities.

2. Architecture Style
   - Recommend an appropriate architecture.
   - Explain why it fits this pilot project.

3. System Components
   - Identify the components.
   - Explain each component's responsibilities.

4. Component Interfaces and APIs
   - Describe how components communicate.
   - Give proposed API endpoints and request/response examples
     where appropriate.
   - Clearly mark assumptions.

5. Data Design
   - Identify the main entities and their relationships.
   - Recommend persistence only when justified by the requirements.
   - Mark unresolved data requirements.

6. Technology Recommendations
   - Recommend suitable technologies.
   - Explain the reason for each choice.
   - Prefer simple, free or open-source options suitable for a proof
     of concept. Do not assume paid services are acceptable.

7. Mapping to Approved Tasks
   - Include every approved task ID exactly as provided.
   - Create exactly one mapping entry for each approved task ID.
   - Use the original IDs without renaming them.
   - Explain which components support each task.
   - Identify missing dependencies, overlaps, or tasks that need
     clarification. Do not silently change the approved tasks.
   - Task IDs may be referenced in other sections as needed.

8. Risks and Open Questions
   - List important risks and unanswered requirements.
   - Separate confirmed requirements from assumptions.

9. Human Review Decisions
   - List decisions that need human approval before implementation.

Important constraints:
- Do not generate application source code.
- Do not invent requirements and present them as confirmed facts.
- Keep the architecture appropriate for a beginner-friendly proof
  of concept.
- Make the design specific enough to guide future developer agents.
- If information is missing, state the assumption or ask a question.
- Preserve every approved task ID exactly.

Return the architecture document in Markdown.
"""

        response = self.client.models.generate_content(
            model=self.model,
            contents=prompt,
        )

        architecture = getattr(response, "text", None)

        if not isinstance(architecture, str) or not architecture.strip():
            raise RuntimeError(
                "The Software Architect Agent returned an empty design."
            )

        return architecture.strip()
