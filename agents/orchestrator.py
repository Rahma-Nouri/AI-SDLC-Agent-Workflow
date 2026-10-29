import json
import os
import time
from collections import defaultdict, deque

from dotenv import load_dotenv
from google import genai


class OrchestratorAgent:
    """
    Central workflow coordinator for the AI-supported SDLC.

    The Orchestrator is responsible for:
    - Understanding project requirements.
    - Creating and revising the development plan.
    - Generating and validating the task backlog.
    - Coordinating human approval gates.
    - Preparing work packages for specialized agents.
    - Tracking task readiness and workflow state.
    - Reviewing agent results and managing revisions.

    Specialized agents remain responsible for their own domain-specific work.
    """

    def __init__(self, model=None, max_retries=3, retry_delay=5):
        load_dotenv()

        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY was not found.")

        self.client = genai.Client(api_key=api_key)
        self.model = model or os.getenv(
            "GEMINI_MODEL", "gemini-3.8-flash"
        )
        self.max_retries = max_retries
        self.retry_delay = retry_delay

        # In-memory workflow state for the current process.
        # Persistent storage will be needed for recovery across restarts.
        self.workflow_state = {
            "plan_approved": False,
            "backlog_approved": False,
            "architecture_approved": False,
            "implementation_accepted": False,
            "deployment_authorized": False,
            "tasks": {},
            "approval_history": [],
        }

    # ---------------------------------------------------------
    # LLM communication
    # ---------------------------------------------------------

    def _generate_text(self, prompt):
        """Generate text with retries and basic response validation."""

        for attempt in range(self.max_retries):
            try:
                response = self.client.models.generate_content(
                    model=self.model,
                    contents=prompt,
                )

                if not response.text or not response.text.strip():
                    raise ValueError("Gemini returned an empty response.")

                return response.text.strip()

            except Exception as error:
                print(
                    f"Gemini request failed "
                    f"(attempt {attempt + 1}/{self.max_retries}): {error}"
                )

                if attempt < self.max_retries - 1:
                    print(f"Retrying in {self.retry_delay} seconds...")
                    time.sleep(self.retry_delay)
                else:
                    raise

    # ---------------------------------------------------------
    # 1. Requirements analysis and planning
    # ---------------------------------------------------------

    def analyze_project(self, specification):
        """
        Analyze requirements and propose a plan.

        This method does not approve the plan, create GitHub Issues,
        write application code, or start implementation.
        """

        if not specification or not specification.strip():
            raise ValueError("The project specification cannot be empty.")

        prompt = f"""
You are the Orchestrator Agent in an AI-supported Software
Development Life Cycle (SDLC).

Your role is to coordinate the complete development workflow,
not merely to write a project plan.

Analyze the project specification and prepare a proposed plan.

Cover:
1. Project understanding and goals.
2. Major features and requirements.
3. Functional and non-functional requirements.
4. Constraints and dependencies.
5. Confirmed requirements, assumptions, and missing information.
6. Risks, blockers, and questions that require human clarification.
7. Proposed development phases and implementation order.
8. The specialized agent or role best suited to each type of work.

Rules:
- Do not invent requirements.
- Clearly distinguish facts from assumptions and recommendations.
- Identify prerequisites and dependencies between work items.
- Do not assign implementation work before the plan and backlog
  have passed the required human approval.
- Do not write application code or create GitHub Issues.

PROJECT SPECIFICATION:
{specification}

Return a clearly structured, complete plan for conversion into
a task backlog and subsequent human review.
"""

        self.workflow_state["plan_approved"] = False
        self.workflow_state["backlog_approved"] = False

        return self._generate_text(prompt)

    def revise_plan(self, specification, previous_plan, feedback):
        """Revise a proposal using explicit human feedback."""

        if not feedback or not feedback.strip():
            raise ValueError("Revision feedback cannot be empty.")

        prompt = f"""
You are the Orchestrator Agent coordinating an AI-supported SDLC.

A human reviewer requested changes to the proposed development plan.

ORIGINAL PROJECT SPECIFICATION:
{specification}

PREVIOUS PLAN:
{previous_plan}

HUMAN REVIEWER FEEDBACK:
{feedback}

Produce the complete revised plan.

Rules:
- Address the reviewer's feedback explicitly.
- Preserve valid parts of the previous plan.
- Do not invent requirements.
- Separate confirmed requirements from assumptions.
- Identify unresolved questions, risks, and dependencies.
- Keep the development phases logically ordered.
- Do not write application code.
- Return the complete revised plan for another human review.
"""

        self.workflow_state["plan_approved"] = False
        self.workflow_state["backlog_approved"] = False

        return self._generate_text(prompt)

    # ---------------------------------------------------------
    # 2. Task generation and validation
    # ---------------------------------------------------------

    def convert_plan_to_tasks(self, plan):
        """
        Convert the proposed plan into structured tasks.

        The tasks are not yet approved for GitHub synchronization.
        Human approval is requested after validation.
        """

        prompt = f"""
Convert the following proposed software development plan into
a structured task backlog.

Return ONLY a valid JSON array. Do not use Markdown fences or
include text outside the JSON.

Each task must contain exactly these fields:
- task_id: unique string in the format T1, T2, T3, and so on
- title: concise task title
- description: clear explanation of the work
- acceptance_criteria: array of measurable criteria
- dependencies: array of task IDs, or [] if there are none

Rules:
- Extract only actionable development tasks.
- Do not create tasks for headings, summaries, or assumptions alone.
- Assign task IDs sequentially in the order tasks appear in the returned
  array, starting with T1.
- Use only IDs in the exact format T1, T2, T3, and so on.
- Never use hierarchical labels such as "Work Item 1.1" as task IDs.
- If the plan contains other labels or IDs, convert them to sequential T IDs.
- Rewrite all dependencies to refer to the corresponding T IDs in this
  returned array.
- Do not preserve incompatible IDs from the plan.
- Use exactly the same T ID whenever referring to a task.
- Do not invent requirements.
- Dependencies must refer to task IDs in this returned array.
- Do not list a task as its own dependency.
- Include enough tasks to cover the development scope.
- Return at least one task.

PROPOSED PLAN:
{plan}
"""

        raw_result = self._generate_text(prompt)
        tasks = self._parse_json_array(raw_result)
        self.validate_tasks(tasks)
        self.validate_generated_task_ids(tasks)

        self.workflow_state["tasks"] = {
            task["task_id"]: {
                **task,
                "status": "pending_approval",
            }
            for task in tasks
        }
        self.workflow_state["backlog_approved"] = False

        return tasks

    @staticmethod
    def validate_generated_task_ids(tasks):
        """
        Require generated task IDs to be sequential T1, T2, T3, and so on.

        This strict check applies to newly generated backlogs. The more
        general validate_tasks() method remains available for existing
        task data that may use other valid ID formats.
        """

        for position, task in enumerate(tasks, start=1):
            expected_id = f"T{position}"
            actual_id = task.get("task_id")

            if actual_id != expected_id:
                raise ValueError(
                    "Generated task IDs must be sequential as T1, T2, and "
                    f"so on; expected {expected_id} at position {position}, "
                    f"got {actual_id!r}."
                )

        return True

    @staticmethod
    def _parse_json_array(result):
        """Parse a JSON array, allowing optional Markdown code fences."""

        result = result.strip()

        if result.startswith("```"):
            lines = result.splitlines()

            if lines and lines[0].strip().lower() in {
                "```json", "```"
            }:
                lines = lines[1:]

            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]

            result = "\n".join(lines).strip()

        try:
            tasks = json.loads(result)
        except json.JSONDecodeError as error:
            raise ValueError(
                f"Gemini returned invalid task JSON: {error}"
            ) from error

        if not isinstance(tasks, list) or not tasks:
            raise ValueError(
                "The generated backlog must be a non-empty JSON array."
            )

        return tasks

    @staticmethod
    def validate_tasks(tasks):
        """
        Validate task structure, unique IDs, dependencies, and cycles.

        TicketQualityChecker should still be used for additional
        quality checks such as vague descriptions and weak criteria.
        """

        if not isinstance(tasks, list) or not tasks:
            raise ValueError("Tasks must be a non-empty list.")

        required_fields = {
            "task_id",
            "title",
            "description",
            "acceptance_criteria",
            "dependencies",
        }

        task_ids = set()

        for task in tasks:
            if not isinstance(task, dict):
                raise ValueError("Each task must be a JSON object.")

            missing = required_fields - task.keys()
            if missing:
                raise ValueError(
                    f"Task is missing fields: {sorted(missing)}"
                )

            for field in ("task_id", "title", "description"):
                if not isinstance(task[field], str) or not task[field].strip():
                    raise ValueError(
                        f"Task field '{field}' must be a non-empty string."
                    )

            task_id = task["task_id"].strip()
            task["task_id"] = task_id

            if task_id in task_ids:
                raise ValueError(f"Duplicate task ID: {task_id}")

            task_ids.add(task_id)

            if not isinstance(task["acceptance_criteria"], list):
                raise ValueError(
                    f"Acceptance criteria for {task_id} must be a list."
                )

            if not task["acceptance_criteria"]:
                raise ValueError(
                    f"Task {task_id} has no acceptance criteria."
                )

            if any(
                not isinstance(item, str) or not item.strip()
                for item in task["acceptance_criteria"]
            ):
                raise ValueError(
                    f"Task {task_id} has an empty or invalid criterion."
                )

            if not isinstance(task["dependencies"], list):
                raise ValueError(
                    f"Dependencies for {task_id} must be a list."
                )

            if any(
                not isinstance(dep, str) or not dep.strip()
                for dep in task["dependencies"]
            ):
                raise ValueError(
                    f"Task {task_id} has an invalid dependency."
                )

            task["dependencies"] = [
                dep.strip() for dep in task["dependencies"]
            ]

            if len(task["dependencies"]) != len(set(task["dependencies"])):
                raise ValueError(
                    f"Task {task_id} has duplicate dependencies."
                )

            if task_id in task["dependencies"]:
                raise ValueError(
                    f"Task {task_id} cannot depend on itself."
                )

        for task in tasks:
            unknown = set(task["dependencies"]) - task_ids

            if unknown:
                raise ValueError(
                    f"Task {task['task_id']} has unknown dependencies: "
                    f"{sorted(unknown)}"
                )

        # Detect circular dependencies using topological sorting.
        dependents = defaultdict(list)
        in_degree = {}

        for task in tasks:
            task_id = task["task_id"]
            in_degree[task_id] = len(task["dependencies"])

            for dependency in task["dependencies"]:
                dependents[dependency].append(task_id)

        ready = deque(
            task_id
            for task_id, degree in in_degree.items()
            if degree == 0
        )

        visited_count = 0

        while ready:
            current = ready.popleft()
            visited_count += 1

            for dependent in dependents[current]:
                in_degree[dependent] -= 1

                if in_degree[dependent] == 0:
                    ready.append(dependent)

        if visited_count != len(tasks):
            raise ValueError(
                "The task backlog contains circular dependencies."
            )

        return True

    # ---------------------------------------------------------
    # 3. Human approval gates
    # ---------------------------------------------------------

    def request_backlog_approval(self, plan, tasks):
        """
        H1: Ask a human to approve the plan and validated task backlog.

        Call this only after task generation and validation, and before
        creating new GitHub Issues.
        """

        if not tasks:
            raise ValueError("Cannot approve an empty task backlog.")

        self.validate_tasks(tasks)

        print("\n" + "=" * 70)
        print("H1 — HUMAN APPROVAL: PLAN AND TASK BACKLOG")
        print("=" * 70)
        print("\nPROPOSED PLAN\n")
        print(plan)

        print("\nSTRUCTURED TASK BACKLOG\n")

        for task in tasks:
            print(f"\n{task['task_id']}: {task['title']}")
            print(f"Description: {task['description']}")
            print("Acceptance criteria:")

            for criterion in task["acceptance_criteria"]:
                print(f"  - {criterion}")

            dependencies = task["dependencies"] or ["None"]
            print(f"Dependencies: {', '.join(dependencies)}")

        print("\n" + "=" * 70)
        print("Review scope, task completeness, criteria, and dependencies.")
        print("=" * 70)

        while True:
            decision = input(
                "\nEnter 'approve', 'revise', or 'reject': "
            ).strip().lower()

            if decision in {"approve", "revise", "reject"}:
                self.workflow_state["approval_history"].append({
                    "gate": "H1",
                    "decision": decision,
                    "task_count": len(tasks),
                })

                if decision == "approve":
                    self.workflow_state["plan_approved"] = True
                    self.workflow_state["backlog_approved"] = True

                    for task in tasks:
                        task_id = task["task_id"]
                        if task_id in self.workflow_state["tasks"]:
                            self.workflow_state["tasks"][task_id][
                                "status"
                            ] = "approved"

                else:
                    self.workflow_state["plan_approved"] = False
                    self.workflow_state["backlog_approved"] = False

                return decision

            print("Invalid choice. Enter approve, revise, or reject.")

    def request_plan_approval(self, plan):
        """
        Compatibility method for older main.py versions.

        The recommended H1 flow is request_backlog_approval(plan, tasks).
        This legacy method approves only a plan and should not be used
        to authorize GitHub Issue creation.
        """

        print("\n" + "=" * 60)
        print("PLAN REVIEW — HUMAN FEEDBACK")
        print("=" * 60)
        print(plan)
        print("=" * 60)

        while True:
            decision = input(
                "\nReview the plan. Enter 'approve', 'reject', or 'revise': "
            ).strip().lower()

            if decision in {"approve", "reject", "revise"}:
                self.workflow_state["approval_history"].append({
                    "gate": "PLAN_DRAFT",
                    "decision": decision,
                })
                return decision

            print("Invalid choice. Please try again.")

    def record_approval(self, gate, decision, notes=""):
        """
        Record an approval decision for a later workflow gate.

        This records state in memory only. It does not replace a
        persistent audit log or the actual approval process for that gate.
        """

        valid_gates = {"H2", "H3", "H4"}
        valid_decisions = {"approve", "revise", "reject"}

        if gate not in valid_gates:
            raise ValueError(f"Unsupported approval gate: {gate}")

        if decision not in valid_decisions:
            raise ValueError(f"Unsupported decision: {decision}")

        self.workflow_state["approval_history"].append({
            "gate": gate,
            "decision": decision,
            "notes": notes,
        })

        state_key = {
            "H2": "architecture_approved",
            "H3": "implementation_accepted",
            "H4": "deployment_authorized",
        }[gate]

        self.workflow_state[state_key] = decision == "approve"

        return decision

    # ---------------------------------------------------------
    # 4. Workflow coordination and delegation
    # ---------------------------------------------------------

    def get_ready_tasks(self, task_statuses):
        """
        Return approved tasks whose dependencies are all completed.

        task_statuses example:
        {"T1": "completed", "T2": "in_progress"}

        Tasks must already have passed H1 and H2 approval.
        """

        if not self.workflow_state["backlog_approved"]:
            raise RuntimeError(
                "H1 approval is required before tasks can be dispatched."
            )

        if not self.workflow_state["architecture_approved"]:
            raise RuntimeError(
                "H2 architecture approval is required before tasks can be dispatched."
            )

        ready = []

        for task_id, task in self.workflow_state["tasks"].items():
            status = task_statuses.get(task_id, task["status"])

            if status != "approved" and status != "pending":
                continue

            dependencies_completed = all(
                task_statuses.get(dep) == "completed"
                for dep in task["dependencies"]
            )

            if dependencies_completed:
                ready.append(task)

        return ready

    def delegate_task(self, task, architecture=None, project_context=None):
        """
        Prepare a work package for a specialized agent.

        This method prepares the assignment; it does not yet call
        a backend/frontend/testing agent automatically.
        """

        if not self.workflow_state["backlog_approved"]:
            raise RuntimeError(
                "Cannot delegate work before H1 backlog approval."
            )

        if not self.workflow_state["architecture_approved"]:
            raise RuntimeError(
                "H2 architecture approval is required before delegation."
            )

        task_id = task.get("task_id")

        if task_id not in self.workflow_state["tasks"]:
            raise ValueError(
                f"Task {task_id} is not part of the approved backlog."
            )

        if self.workflow_state["tasks"][task_id]["status"] != "approved":
            raise RuntimeError(
                f"Task {task_id} is not approved for implementation."
            )

        return {
            "task": task,
            "architecture": architecture,
            "project_context": project_context or {},
            "instructions": (
                "Implement only the approved task. Follow the approved "
                "architecture and acceptance criteria. Report changes, "
                "test evidence, limitations, and unresolved issues. "
                "Do not expand the scope without approval."
            ),
        }

    def evaluate_agent_result(self, task, result, checks_passed):
        """
        Evaluate reported checks and package a review decision.

        checks_passed must come from actual test/validation results.
        The Orchestrator does not assume that code works merely because
        an agent says it is complete.
        """

        if not isinstance(checks_passed, list):
            raise ValueError("checks_passed must be a list of check results.")

        failed_checks = [
            check for check in checks_passed
            if not isinstance(check, dict) or check.get("passed") is not True
        ]

        return {
            "task_id": task.get("task_id"),
            "result": result,
            "checks_passed": checks_passed,
            "failed_checks": failed_checks,
            "decision": "needs_revision" if failed_checks else "ready_for_review",
        }

    def get_workflow_state(self):
        """Return a copy of the current in-memory workflow state."""

        return {
            **self.workflow_state,
            "tasks": {
                task_id: dict(task)
                for task_id, task in self.workflow_state["tasks"].items()
            },
            "approval_history": [
                dict(item)
                for item in self.workflow_state["approval_history"]
            ],
        }