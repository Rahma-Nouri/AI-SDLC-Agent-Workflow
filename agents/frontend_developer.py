"""Frontend Developer Agent: propose a UI patch for human review (P2.2b).

The agent receives only what a UI task needs (the context layers of
docs/project_context.md):

L0 always:    the approved UI stack (the snapshot's "ui_stack") + the prompt rules
L1 per task:  the task, and the UI-relevant parts of the approved architecture
L2 on demand: the existing files in the frontend folder, up to a size limit

Like the Backend Agent it returns complete files, and Python builds the patch.
It may only write inside the frontend folder, plus the two allowed lines in the
pilot's .gitignore. Anything else is refused before a branch exists.
"""

import json
import re
from pathlib import PurePosixPath

from google.genai import types
from pydantic import ValidationError

from agents.agent_contracts import (
    ChangedFile,
    FrontendDeveloperModelResponse,
    FrontendDeveloperResult,
    UiStackProposal,
)
from agents.backend_developer import BackendDeveloperAgent

# The tasks with UI work, and the API endpoints each one needs. From the P2.2
# inventory; the endpoints are named exactly as in the architecture (4.2).
# T12 has none: the architecture defines no ad-hoc endpoint.
UI_TASKS = {
    "T4": ["POST /api/v1/documents", "GET /api/v1/documents/{document_id}"],
    "T6": ["POST /api/v1/exams/generate", "GET /api/v1/jobs/{job_id}"],
    "T7": [
        "GET /api/v1/exams/{exam_id}",
        "POST /api/v1/exams/{exam_id}/sessions",
        "PUT /api/v1/exams/sessions/{session_id}/answers",
        "POST /api/v1/exams/sessions/{session_id}/submit",
    ],
    "T8": ["GET /api/v1/exams/sessions/{session_id}/report"],
    "T9": ["POST /api/v1/exams/generate", "GET /api/v1/jobs/{job_id}"],
    "T10": ["POST /api/v1/documents", "GET /api/v1/documents/{document_id}"],
    "T11": ["POST /api/v1/study/summarize"],
    "T12": [],
    "T13": ["GET /api/v1/diagnostics/readiness"],
    "T16": ["GET /api/v1/diagnostics/readiness"],
}

GITIGNORE = ".gitignore"
ALLOWED_GITIGNORE_LINES = {"node_modules/", "dist/"}

# Generated folders: never written by the agent, never read into its context.
GENERATED_FOLDERS = {"node_modules", "dist"}


def markdown_section(text, heading):
    """The Markdown section whose heading contains this text, with its subsections."""

    lines = text.splitlines()

    for start, line in enumerate(lines):
        if line.startswith("#") and heading in line:
            level = len(line) - len(line.lstrip("#"))
            break
    else:
        raise ValueError(f"The architecture has no section '{heading}'.")

    end = len(lines)
    for index in range(start + 1, len(lines)):
        stripped = lines[index].lstrip("#")
        if lines[index].startswith("#") and len(lines[index]) - len(stripped) <= level:
            end = index
            break

    return "\n".join(lines[start:end]).strip()


def endpoint_description(architecture, endpoint):
    """The bullet of one endpoint in the architecture, with its request and response."""

    lines = architecture.splitlines()
    marker = f"- `{endpoint}`"

    for start, line in enumerate(lines):
        if line.strip() == marker:
            break
    else:
        raise ValueError(f"The architecture does not describe the endpoint {endpoint}.")

    end = len(lines)
    for index in range(start + 1, len(lines)):
        if lines[index].startswith(("- `", "#", "---")):
            end = index
            break

    return "\n".join(lines[start:end]).strip()


def ui_disclaimer(architecture):
    """The human review decision that defines the UI disclaimer."""

    decisions = markdown_section(architecture, "Human Review Decisions")

    # Each decision is a numbered item: split at the "1. ", "2. " line starts.
    for item in re.split(r"(?m)^\d+\.\s+", decisions):
        if "disclaimer" in item.lower():
            return item.strip()

    raise ValueError("The architecture defines no UI disclaimer.")


def ui_architecture_context(architecture, task_id):
    """Layer L1: only the UI-relevant parts of the approved architecture."""

    task_row = next(
        (line for line in architecture.splitlines() if line.startswith(f"| **{task_id}** |")),
        "Not found in the architecture's task mapping.",
    )
    endpoints = [endpoint_description(architecture, endpoint) for endpoint in UI_TASKS[task_id]]

    return "\n\n".join([
        markdown_section(architecture, "User Interface"),
        f"Mapping of {task_id}:\n{task_row}",
        "API endpoints this task needs:\n"
        + ("\n\n".join(endpoints) or "None defined in the architecture."),
        f"Human review decision about the UI:\n{ui_disclaimer(architecture)}",
    ])


class FrontendDeveloperAgent(BackendDeveloperAgent):
    """Propose a scoped UI patch.

    It inherits only what is not backend-specific: the Gemini client, the JSON
    parsing, _build_local_patch and _validate_unified_diff. The rules and the
    path checks below are its own, so the Backend Agent is not changed.
    """

    # A UI starts with many small files (config, components, tests), so it
    # needs more room than a backend patch.
    MAX_OUTPUT_TOKENS = 32768
    MAX_EXISTING_FILE_CHARS = 30000

    def create_patch(self, work_package):
        """Generate a locally-built, human-reviewable patch (1 Gemini request)."""

        task = work_package["task"]
        task_id = task["task_id"]
        architecture = work_package["architecture"]
        project_context = work_package["project_context"]

        if project_context.get("existing_files_complete") is not True:
            raise ValueError("project_context must include a complete existing-file snapshot.")

        ui_stack = UiStackProposal.model_validate(project_context.get("ui_stack"))
        shown_files, omitted_files = self._fit_existing_files(project_context["existing_files"])

        print("Sending the structured frontend change request to Gemini...")
        response = self.client.models.generate_content(
            model=self.model,
            contents=self._build_prompt(
                task, architecture, ui_stack, shown_files, omitted_files, work_package["instructions"]
            ),
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_json_schema=FrontendDeveloperModelResponse.model_json_schema(),
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
            raise RuntimeError("The Frontend Developer Agent returned an empty response.")

        try:
            model_response = FrontendDeveloperModelResponse.model_validate(self._parse_json_response(response_text))
        except ValidationError as error:
            raise ValueError(f"The Frontend Developer Agent returned an invalid result: {error}") from error

        if model_response.task_id != task_id:
            raise ValueError("The returned task_id does not match the delegated task.")

        self._validate_paths(model_response, ui_stack.frontend_folder)
        self._validate_not_blind_changes(model_response.proposed_files, omitted_files)
        self._validate_package_json(model_response.proposed_files)
        self._validate_gitignore(model_response.proposed_files, project_context["existing_files"])

        patch = self._build_local_patch(model_response.proposed_files, project_context)

        result = FrontendDeveloperResult(
            task_id=model_response.task_id,
            summary=model_response.summary,
            patch=patch,
            changed_files=[
                ChangedFile(path=item.path, purpose=item.purpose)
                for item in model_response.proposed_files
            ],
            acceptance_criteria=model_response.acceptance_criteria,
            tests_added=model_response.tests_added,
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
            message = str(error).replace("Backend Developer Agent", "Frontend Developer Agent")
            raise ValueError(message) from error

    @classmethod
    def _fit_existing_files(cls, existing_files):
        """Layer L2: choose the files for the prompt, smallest first, up to the limit.

        Only the prompt is limited: Python keeps every file for building the patch.
        Files that do not fit are listed by name, so the agent knows they exist.
        """

        shown, omitted, used = {}, [], 0

        for path, content in sorted(existing_files.items(), key=lambda item: len(item[1])):
            if used + len(content) > cls.MAX_EXISTING_FILE_CHARS:
                omitted.append(path)
            else:
                shown[path] = content
                used += len(content)

        return shown, sorted(omitted)

    @classmethod
    def _build_prompt(cls, task, architecture, ui_stack, shown_files, omitted_files, instructions):
        folder = ui_stack.frontend_folder

        return f"""
You are the Frontend Developer Agent.

Return one structured response with:
- task_id: the exact approved task ID
- summary: a short description of the proposed UI changes
- proposed_files: complete proposed file contents, each with path and purpose
- acceptance_criteria: criterion, status, and evidence
- tests_added: test file paths
- assumptions: assumptions, if any
- unresolved_issues: unresolved issues, if any

Approved UI stack (use exactly this; do not change or add a framework):
- Framework: {ui_stack.framework}
- Language: {ui_stack.language}
- Build tool: {ui_stack.build_tool}
- Test runner: {ui_stack.test_runner}
- Package manager: {ui_stack.package_manager}
- Frontend folder: {folder}

Rules:
- Put every file under {folder}/, including package.json, the configuration, the source code and the tests.
- The only allowed file outside that folder is the root {GITIGNORE}, and only to add the lines node_modules/ and dist/. Keep its existing lines.
- Do not write backend code. The real backend routes do not exist yet.
- Create a MOCK API inside {folder}/ for every endpoint this task needs. Its request and response shapes must match the approved architecture exactly. The UI must call the API only through this mock layer, so the real backend can replace it later.
- Cache the student's answers in the browser's localStorage, so an answer survives a page reload.
- Show this advisory text wherever AI feedback or grading results appear, and on the exam screen: "AI feedback is advisory. Review model answers for authoritative validation."
- Write {ui_stack.test_runner} tests inside {folder}/ that check concrete behavior of this task, and list them in tests_added.
- package.json must be valid JSON, and its "test" script must run {ui_stack.test_runner} once and exit, without watch mode.
- Pin only versions you are sure exist. Put uncertainty in unresolved_issues. Do not create a lock file.
- Treat the supplied approved task and its acceptance criteria as the source of truth.
- Mark a criterion that needs the real backend as not_addressed, even if the mock imitates it.
- Make the smallest change that addresses this task; do not implement future tasks.
- Do not invent requirements. Put uncertainty in assumptions or unresolved_issues.
- Return complete file contents, not a unified diff.
- Do not claim files were written, packages were installed, or tests were run.
- Propose changes only for files that need to change.
- Do not change a file listed under "too large to show".
- Do not include absolute paths or paths containing "..".
- Preserve exact task IDs and file paths from the provided context.

Delegated task:
{json.dumps(task, ensure_ascii=False, indent=2)}

Relevant parts of the approved architecture:
{architecture}

Existing files, exact content:
{json.dumps(shown_files, ensure_ascii=False, indent=2)}

Existing files too large to show:
{json.dumps(omitted_files, ensure_ascii=False)}

Orchestrator instructions:
{instructions}
""".strip()

    @staticmethod
    def _is_in_frontend_folder(path, frontend_folder):
        """True for a safe relative path inside the frontend folder."""
        parts = PurePosixPath(path)
        return (
            "\\" not in path
            and not parts.is_absolute()
            and ".." not in parts.parts
            and parts.is_relative_to(PurePosixPath(frontend_folder))
            and not GENERATED_FOLDERS & set(parts.parts)
        )

    @classmethod
    def _validate_paths(cls, model_response, frontend_folder):
        for proposed_file in model_response.proposed_files:
            path = proposed_file.path
            if path != GITIGNORE and not cls._is_in_frontend_folder(path, frontend_folder):
                raise ValueError(f"Frontend changes must stay under {frontend_folder}/; got {path}")

        for path in model_response.tests_added:
            if not cls._is_in_frontend_folder(path, frontend_folder):
                raise ValueError(f"Frontend tests must stay under {frontend_folder}/; got {path}")

    @staticmethod
    def _validate_not_blind_changes(proposed_files, omitted_files):
        """A file the agent never saw must not be replaced: its content would be lost."""
        for proposed_file in proposed_files:
            if proposed_file.path in omitted_files:
                raise ValueError(f"The agent changed a file it could not see: {proposed_file.path}")

    @staticmethod
    def _validate_package_json(proposed_files):
        """There is no frontend syntax check yet, but package.json must at least parse."""
        for proposed_file in proposed_files:
            if PurePosixPath(proposed_file.path).name != "package.json":
                continue

            try:
                data = json.loads(proposed_file.content)
            except json.JSONDecodeError as error:
                raise ValueError(f"{proposed_file.path} is not valid JSON: {error}") from error

            if not isinstance(data, dict):
                raise ValueError(f"{proposed_file.path} must be a JSON object.")

    @staticmethod
    def _validate_gitignore(proposed_files, existing_files):
        """.gitignore may only gain the two allowed lines; nothing may be removed."""
        for proposed_file in proposed_files:
            if proposed_file.path != GITIGNORE:
                continue

            old_lines = {line.strip() for line in existing_files.get(GITIGNORE, "").splitlines() if line.strip()}
            new_lines = {line.strip() for line in proposed_file.content.splitlines() if line.strip()}

            if old_lines - new_lines:
                raise ValueError(f"{GITIGNORE} must keep its lines; removed: {sorted(old_lines - new_lines)}")

            if new_lines - old_lines - ALLOWED_GITIGNORE_LINES:
                raise ValueError(
                    f"{GITIGNORE} may only add {sorted(ALLOWED_GITIGNORE_LINES)}; "
                    f"got: {sorted(new_lines - old_lines - ALLOWED_GITIGNORE_LINES)}"
                )
