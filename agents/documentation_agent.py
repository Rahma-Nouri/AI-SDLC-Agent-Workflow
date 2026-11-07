"""Documentation Agent: write one reviewable document for a pilot task (P2.3).

Minimal version: only T1 ("System Architecture and Interface Design"). Its
acceptance criteria 1 and 2 ask for Architecture Decision Records (ADRs) and
diagrams, which the Backend Agent marked not_addressed. This agent writes them
as ONE Markdown file, with diagrams in Mermaid (diagrams written as text, so
Git can store and compare them).

The agent receives only what this document needs (docs/project_context.md):

L0 always:    the task with its acceptance criteria + the pilot specification
L1 per task:  the architecture sections a design document needs (not the
              endpoint and table details)
L2 on demand: the task's real code files (read-only) and the newest report of
              the agent that wrote the code (its assumptions and open issues)

Like the other agents it returns complete files, and Python builds the patch.
It may only write Markdown under pilot/docs/, and it never adds tests: a
document is checked by the human at H3 plus the automatic reference checks
(output_validation: docs_paths, docs_names).
"""

import json
from pathlib import Path, PurePosixPath

from google.genai import types
from pydantic import ValidationError

from agents.agent_contracts import ChangedFile, DocumentationModelResponse, DocumentationResult
from agents.backend_developer import BackendDeveloperAgent
from agents.frontend_developer import markdown_section

DOCS_FOLDER = "pilot/docs"

# Which document each task gets, and what the agent reads for it.
# Only T1 for now (minimal P2.3); another task needs its own entry.
DOC_TASKS = {
    "T1": {
        "document": f"{DOCS_FOLDER}/T1_architecture.md",
        # L1: the architecture sections a design document needs.
        "architecture_sections": [
            "2. Architecture Style",
            "3. System Components",
            "4.1. Communication Style",
            "5.1. Entity-Relationship Model",
            "6. Technology Recommendations",
            "8. Risks and Open Questions",
            "9. Human Review Decisions",
        ],
        # L2: the code T1 really added (read-only for this agent).
        "source_files": [
            "pilot/app/backend/database.py",
            "pilot/app/backend/models.py",
            "pilot/app/backend/openapi_specs.py",
            "tests/test_backend_database.py",
            "requirements.txt",
        ],
        # The agent whose newest report is shown (assumptions, open issues).
        "code_role": "backend",
    },
}


def docs_architecture_context(architecture, task_id):
    """Layer L1: only the architecture sections this task's document needs."""

    settings = DOC_TASKS[task_id]
    task_row = next(
        (line for line in architecture.splitlines() if line.startswith(f"| **{task_id}** |")),
        "Not found in the architecture's task mapping.",
    )
    sections = [markdown_section(architecture, heading) for heading in settings["architecture_sections"]]
    return "\n\n".join([*sections, f"Mapping of {task_id}:\n{task_row}"])


def newest_agent_report(pilot_root, task_id, role):
    """The newest report of the agent that wrote the task's code, or None.

    Report names end with a timestamp (..._20261107_111605_007552_report.json),
    so the newest is the one with the largest timestamp.
    """

    folder = Path(pilot_root) / "outputs" / "proposed_changes"
    if not folder.is_dir():
        return None
    reports = sorted(
        folder.glob(f"{task_id}_{role}_*_report.json"),
        key=lambda path: "_".join(path.stem.split("_")[-4:-1]),
    )
    if not reports:
        return None

    data = json.loads(reports[-1].read_text(encoding="utf-8"))
    # Only the claims a document writer needs; the patch itself is not useful here.
    return {
        "file_name": reports[-1].name,
        "summary": data.get("summary"),
        "acceptance_criteria": data.get("acceptance_criteria", []),
        "assumptions": data.get("assumptions", []),
        "unresolved_issues": data.get("unresolved_issues", []),
    }


class DocumentationAgent(BackendDeveloperAgent):
    """Propose one Markdown document for a task.

    It inherits only what is not backend-specific: the Gemini client, the JSON
    parsing, _build_local_patch and _validate_unified_diff.
    """

    MAX_OUTPUT_TOKENS = 16384

    def create_patch(self, work_package):
        """Generate a locally-built, human-reviewable document patch (1 Gemini request)."""

        task = work_package["task"]
        task_id = task["task_id"]
        project_context = work_package["project_context"]

        if task_id not in DOC_TASKS:
            raise ValueError(f"The Documentation Agent has no document defined for {task_id}.")
        if project_context.get("existing_files_complete") is not True:
            raise ValueError("project_context must include a complete existing-file snapshot.")

        document = DOC_TASKS[task_id]["document"]

        print("Sending the structured documentation request to Gemini...")
        response = self.client.models.generate_content(
            model=self.model,
            contents=self._build_prompt(task, work_package["architecture"], project_context, document,
                                        work_package["instructions"]),
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_json_schema=DocumentationModelResponse.model_json_schema(),
                max_output_tokens=self.MAX_OUTPUT_TOKENS,
                thinking_config=types.ThinkingConfig(thinking_level="low"),
            ),
        )

        candidates = getattr(response, "candidates", None) or []
        finish_reason = getattr(candidates[0], "finish_reason", None) if candidates else None
        if getattr(finish_reason, "name", str(finish_reason)).upper().endswith("MAX_TOKENS"):
            raise RuntimeError("Gemini stopped at the output-token limit; the response may be incomplete.")

        response_text = getattr(response, "text", None)
        if not isinstance(response_text, str) or not response_text.strip():
            raise RuntimeError("The Documentation Agent returned an empty response.")

        try:
            model_response = DocumentationModelResponse.model_validate(self._parse_json_response(response_text))
        except ValidationError as error:
            raise ValueError(f"The Documentation Agent returned an invalid result: {error}") from error

        if model_response.task_id != task_id:
            raise ValueError("The returned task_id does not match the delegated task.")

        self._validate_paths(model_response)

        patch = self._build_local_patch(model_response.proposed_files, project_context)
        result = DocumentationResult(
            task_id=model_response.task_id,
            summary=model_response.summary,
            patch=patch,
            changed_files=[
                ChangedFile(path=item.path, purpose=item.purpose) for item in model_response.proposed_files
            ],
            acceptance_criteria=model_response.acceptance_criteria,
            tests_added=[],
            assumptions=model_response.assumptions,
            unresolved_issues=model_response.unresolved_issues,
        )

        self._validate_unified_diff(result.patch, result.changed_files)
        return result

    @staticmethod
    def _parse_json_response(response_text):
        """The inherited JSON parsing, with error messages that name this agent."""
        try:
            return BackendDeveloperAgent._parse_json_response(response_text)
        except ValueError as error:
            message = str(error).replace("Backend Developer Agent", "Documentation Agent")
            raise ValueError(message) from error

    @staticmethod
    def is_docs_path(path):
        """True for a safe relative Markdown path inside pilot/docs/."""
        parts = PurePosixPath(path)
        return (
            "\\" not in path
            and not parts.is_absolute()
            and ".." not in parts.parts
            and parts.is_relative_to(PurePosixPath(DOCS_FOLDER))
            and parts.suffix == ".md"
        )

    @classmethod
    def _validate_paths(cls, model_response):
        for proposed_file in model_response.proposed_files:
            if not cls.is_docs_path(proposed_file.path):
                raise ValueError(f"The Documentation Agent may only write Markdown under {DOCS_FOLDER}/; got {proposed_file.path}")

        # A document has no tests; a "test" from this agent would be out of scope.
        if model_response.tests_added:
            raise ValueError(f"The Documentation Agent must not add tests; got {model_response.tests_added}")

    @staticmethod
    def _build_prompt(task, architecture, project_context, document, instructions):
        return f"""
You are the Documentation Agent.

Return one structured response with:
- task_id: the exact approved task ID
- summary: a short description of the document
- proposed_files: exactly one file, {document}, with its complete Markdown content
- acceptance_criteria: every acceptance criterion of the task, with status and evidence
- tests_added: an empty list
- assumptions: assumptions, if any
- unresolved_issues: unresolved issues, if any

The document must contain these sections, in this order:
1. Purpose: what the document covers and which acceptance criteria it serves.
2. Architecture Decision Records: one ADR per important decision in the approved
   architecture (for example ADR-001, ADR-002). Each ADR has: Status, Context,
   Decision, Alternatives considered, Consequences. Status is always
   "Proposed - waiting for human approval at H3".
3. Component interaction diagram: one Mermaid diagram (```mermaid flowchart).
4. Data flow diagram: one Mermaid diagram of how data moves through the system.
5. Database schema as implemented: what the supplied code really creates.
6. Differences between architecture and code: everything the architecture
   describes that the code does not do yet, or does differently.
7. Open questions: from the architecture and from the earlier agent's report.

Rules:
- Use only the supplied architecture, specification, code and report. Do not invent
  components, files, functions, tables or decisions.
- Describe the code as it is. If the architecture and the code disagree, say so in
  section 6; do not hide the difference.
- Use `backticks` only for real file paths and real code names that appear in the
  supplied files (for example a function as `name()`, a class as `ClassName`).
  Write technology names (for example FastAPI, SQLite, React) as plain text.
  Automatic checks warn about every path or code name in backticks that does not exist.
- An acceptance criterion is "addressed" only with concrete evidence (a section of
  this document or a supplied file). Approval itself happens at H3, by the human.
- A criterion that needs code which is missing stays "not_addressed".
- Write in simple, clear English for a student developer.
- Do not write code files or tests. Do not claim anything was run or approved.
- Return the complete file content, not a diff.

Delegated task:
{json.dumps(task, ensure_ascii=False, indent=2)}

Pilot specification:
{project_context.get("pilot_specification", "")}

Relevant parts of the approved architecture:
{architecture}

The task's code, exact content (read-only for you):
{json.dumps(project_context.get("source_files", {}), ensure_ascii=False, indent=2)}

Newest report of the agent that wrote this code (its own claims, not proven):
{json.dumps(project_context.get("code_agent_report"), ensure_ascii=False, indent=2)}

The current content of the documentation folder (empty if the document is new):
{json.dumps(project_context.get("existing_files", {}), ensure_ascii=False, indent=2)}

Orchestrator instructions:
{instructions}
""".strip()
