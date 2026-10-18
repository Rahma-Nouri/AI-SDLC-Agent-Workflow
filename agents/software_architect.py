
import os

from dotenv import load_dotenv
from google import genai


class SoftwareArchitectAgent:
    """Design the system architecture from requirements and approved tasks."""

    def __init__(self):
        load_dotenv()

        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError(
                "GEMINI_API_KEY is missing. Check your .env file."
            )

        self.client = genai.Client(api_key=api_key)
        self.model = "gemini-3.8-flash"

    def design_architecture(self, specification, tasks):
        """Generate a technical design without implementing application code."""

        task_text = "\n\n".join(
            (
                f"Task ID: {task['task_id']}\n"
                f"Title: {task['title']}\n"
                f"Description: {task['description']}\n"
                f"Acceptance criteria:\n"
                + "\n".join(
                    f"- {criterion}"
                    for criterion in task["acceptance_criteria"]
                )
                + "\nDependencies: "
                + (", ".join(task["dependencies"]) or "None")
            )
            for task in tasks
        )

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
   - Explain which components support each task.
   - Identify missing dependencies, overlaps, or tasks that need
     clarification. Do not silently change the approved tasks.

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

Return the architecture document in Markdown.
"""

        response = self.client.models.generate_content(
            model=self.model,
            contents=prompt,
        )

        architecture = response.text

        if not architecture or not architecture.strip():
            raise RuntimeError(
                "The Software Architect Agent returned an empty design."
            )

        return architecture.strip()
