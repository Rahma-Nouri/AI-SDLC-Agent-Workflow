
import os
import time

from dotenv import load_dotenv
from google import genai


class OrchestratorAgent:
    def __init__(self):
        load_dotenv()

        api_key = os.getenv("GEMINI_API_KEY")

        if not api_key:
            raise ValueError("GEMINI_API_KEY was not found.")

        self.client = genai.Client(api_key=api_key)

    def analyze_project(self, specification):
        prompt = f"""
You are the Orchestrator Agent in an AI-supported
Software Development Life Cycle (SDLC).

Your responsibilities are:
- Understand the project requirements.
- Identify major features.
- Break the project into concrete development tasks.
- Identify task dependencies.
- Organize tasks in a logical implementation order.
- Identify missing information and assumptions.
- Do not invent requirements that are not in the specification.

Analyze the following project specification.

PROJECT SPECIFICATION:
{specification}

Return the result in this structure:

PROJECT UNDERSTANDING:
Briefly explain what the system should do.

MAJOR FEATURES:
List the main features supported by the specification.

MISSING INFORMATION AND ASSUMPTIONS:
Separate confirmed requirements from uncertainties.
Do not present assumptions as confirmed requirements.

DEVELOPMENT TASKS:
For each task include:
- Task ID
- Task name
- Description
- Acceptance criteria
- Dependencies

IMPLEMENTATION ORDER:
Give the recommended order for implementing the tasks.

Do not write code or start implementation.
The plan must be reviewed by a human first.
"""

        for attempt in range(3):
            try:
                response = self.client.models.generate_content(
                    model="gemini-3.8-flash",
                    contents=prompt,
                )

                if not response.text:
                    raise ValueError("Gemini returned an empty response.")

                return response.text

            except Exception as error:
                print(
                    f"Gemini request failed "
                    f"(attempt {attempt + 1}/3): {error}"
                )

                if attempt < 2:
                    print("Retrying in 5 seconds...")
                    time.sleep(5)
                else:
                    raise

    def request_plan_approval(self, plan):
        print("\n" + "=" * 60)
        print("PLAN REVIEW — HUMAN APPROVAL REQUIRED")
        print("=" * 60)
        print(plan)
        print("=" * 60)

        while True:
            decision = input(
                "\nApprove this plan? "
                "Enter 'approve', 'reject', or 'revise': "
            ).strip().lower()

            if decision in {"approve", "reject", "revise"}:
                return decision

            print("Invalid choice. Please enter approve, reject, or revise.")

    
    def revise_plan(self, specification, previous_plan, feedback):
        prompt = f"""
You are the Orchestrator Agent in an AI-supported SDLC.

A human reviewer has requested changes to a proposed development plan.

ORIGINAL PROJECT SPECIFICATION:
{specification}

PREVIOUS PLAN:
{previous_plan}

HUMAN REVIEWER FEEDBACK:
{feedback}

Revise the plan according to the feedback.

Rules:
- Preserve valid parts of the previous plan.
- Do not invent requirements that are not in the specification.
- Identify assumptions and missing information.
- Give each task a unique Task ID.
- Include task descriptions, measurable acceptance criteria,
  and dependencies.
- Ensure dependencies are consistent with the implementation order.
- Do not write code.
- Return the complete revised plan for human review.
"""

        for attempt in range(3):
            try:
                response = self.client.models.generate_content(
                    model="gemini-3.8-flash",
                    contents=prompt,
                )

                if not response.text:
                    raise ValueError("Gemini returned an empty response.")

                return response.text

            except Exception as error:
                print(
                    f"Gemini request failed "
                    f"(attempt {attempt + 1}/3): {error}"
                )

                if attempt < 2:
                    print("Retrying in 5 seconds...")
                    time.sleep(5)
                else:
                    raise
