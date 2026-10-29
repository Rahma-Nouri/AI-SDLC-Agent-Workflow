import ast
import difflib
import json
import os
import re
from pathlib import PurePosixPath

from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import ValidationError

from agents.agent_contracts import (
    BackendDeveloperModelResponse,
    BackendDeveloperResult,
    ChangedFile,
)


class BackendDeveloperAgent:
    """Propose a scoped backend code patch for human review and application."""

    BACKEND_ROOT = PurePosixPath("pilot/app/backend")
    MAX_API_ATTEMPTS = 3

    CONTEXT_FIELD_LIMITS = {
        "pilot_specification": 3000,
        "readme": 3000,
        "requirements": 8000,
        "python_version": 200,
        "snapshot_saved_at": 200,
    }
    DEFAULT_CONTEXT_FIELD_LIMIT = 2000

    DIFF_HEADER_PATTERN = re.compile(r"^diff --git a/(.+) b/(.+)$")
    HUNK_HEADER_PATTERN = re.compile(
        r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?: .*)?$"
    )

    def __init__(self, client=None, model=None):
        load_dotenv()
        self.model = model or os.getenv("GEMINI_MODEL", "gemini-3.8-flash")

        if client is not None:
            self.client = client
            return

        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY is missing. Check your .env file.")

        self.client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                retry_options=types.HttpRetryOptions(
                attempts=3,
                initial_delay=1.0,
                max_delay=4.0,
                exp_base=2.0,
                http_status_codes=[408, 429, 500, 502, 503, 504],
                )
            ),
        )


    def create_patch(self, work_package):
        """Generate a locally-built, human-reviewable patch."""

        if not isinstance(work_package, dict):
            raise ValueError("The work package must be a dictionary.")

        task = work_package.get("task")
        if not isinstance(task, dict):
            raise ValueError("The work package must include a task dictionary.")

        task_id = task.get("task_id")
        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError("The task must include a non-empty task_id.")

        architecture = work_package.get("architecture")
        if not isinstance(architecture, str) or not architecture.strip():
            raise ValueError(
                "The work package must include the approved architecture."
            )

        project_context = work_package.get("project_context", {})
        if not isinstance(project_context, dict):
            raise ValueError("project_context must be a dictionary.")

        self._validate_project_snapshot(project_context)

        instructions = work_package.get("instructions")
        if not isinstance(instructions, str) or not instructions.strip():
            raise ValueError("The work package must include instructions.")

        prompt = self._build_prompt(
            task=task,
            architecture=architecture,
            project_context=project_context,
            instructions=instructions,
        )

        print("Sending the structured backend change request to Gemini...")
        response = self.client.models.generate_content(
            model=self.model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_json_schema=(
                    BackendDeveloperModelResponse.model_json_schema()
                ),
                max_output_tokens=16384,
                thinking_config=types.ThinkingConfig(
                    thinking_level="low",
                ),
            ),
        )
        candidates = getattr(response, "candidates", None) or []
        finish_reason = (
            getattr(candidates[0], "finish_reason", None)
            if candidates
            else None
        )
        usage = getattr(response, "usage_metadata", None)

        reason_name = getattr(finish_reason, "name", str(finish_reason))
        print(f"Gemini finish reason: {reason_name}")

        if usage is not None:
            print(
                "Gemini token usage: "
                f"prompt={getattr(usage, 'prompt_token_count', None)}, "
                f"response={getattr(usage, 'candidates_token_count', None)}, "
                f"total={getattr(usage, 'total_token_count', None)}"
            )

        if reason_name.upper().endswith("MAX_TOKENS"):
            raise RuntimeError(
                "Gemini stopped at the output-token limit; "
                "the response may be incomplete."
            )

        print("Gemini response received. Validating proposed file contents...")

        response_text = getattr(response, "text", None)
        if not isinstance(response_text, str) or not response_text.strip():
            raise RuntimeError(
                "The Backend Developer Agent returned an empty response."
            )

        data = self._parse_json_response(response_text)

        try:
            model_response = BackendDeveloperModelResponse.model_validate(data)
        except ValidationError as error:
            raise ValueError(
                "The Backend Developer Agent returned an invalid result: "
                f"{error}"
            ) from error

        if model_response.task_id != task_id:
            raise ValueError(
                "The returned task_id does not match the delegated task."
            )

        self._validate_changed_file_paths(model_response.proposed_files)
        self._validate_python_syntax(model_response.proposed_files)

        patch = self._build_local_patch(
            proposed_files=model_response.proposed_files,
            project_context=project_context,
        )

        result = BackendDeveloperResult(
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

   
    @classmethod
    def _compact_project_context(cls, project_context):
        """Shorten long text fields so the model request stays manageable."""

        compacted = {}

        for key, value in project_context.items():
            if isinstance(value, str):
                limit = cls.CONTEXT_FIELD_LIMITS.get(
                    key,
                    cls.DEFAULT_CONTEXT_FIELD_LIMIT,
                )

                if len(value) > limit:
                    compacted[key] = (
                        value[:limit]
                        + f"\n[Context field truncated at {limit} characters.]"
                    )
                else:
                    compacted[key] = value
            else:
                compacted[key] = value

        return compacted

    @classmethod
    def _build_prompt(cls, task, architecture, project_context, instructions):
        compact_context = cls._compact_project_context(project_context)

        return f"""
You are the Backend Developer Agent.

Return one structured response with:
- task_id: the exact approved task ID
- summary: a short description of the proposed backend changes
- proposed_files: complete proposed file contents, each with path and purpose
- acceptance_criteria: criterion, status, and evidence
- tests_added: test file paths if proposed
- assumptions: assumptions, if any
- unresolved_issues: unresolved issues, if any

Rules:
- Use a file-backed SQLite database for local development; never use sqlite:///:memory: as the application database.
- Do not create or modify database tables as a side effect of importing the application module.
- Make database initialization configurable so tests can use a temporary database file.
- Test database creation using a temporary file-backed SQLite database.
- Pin dependencies only when their compatibility with Python 3.14.8 is verified; otherwise report the version as unresolved rather than guessing.
- Treat the supplied approved task and its acceptance criteria as the source of truth.
- Produce only the backend slice required by this task. Do not implement behaviors belonging to other tasks.
- Include API routes only when the approved task or architecture requires their contracts. Contract-only routes must not pretend to perform unfinished work or return invented records.
- Include database models only for entities required by this task; leave future-task entities for those tasks.
- If proposed Python code imports a package absent from the supplied requirements, include the dependency change in requirements.txt. Preserve existing requirements and do not claim the package is installed.
- Proposed tests must check concrete behavior required by this task, not only that names or schema entries exist.
- Return complete file contents, not a unified diff.
- Do not claim files were written or tests were run.
- Propose changes only for files that need to change.
- Use only paths under pilot/app/backend/, the root requirements.txt,
  or top-level tests named tests/test_backend_*.py.
- Do not include absolute paths or paths containing "..".
- Follow only the delegated task and its acceptance criteria.
- Make the smallest patch that addresses this task; do not implement future tasks.
- Do not invent requirements. Put uncertainty in assumptions or unresolved_issues.
- This assignment may be only the backend slice of T1; do not claim full T1 completion.
- Mark acceptance criteria outside the backend slice as not_addressed.
- Preserve exact task IDs and file paths from the provided context.

Delegated task:
{json.dumps(task, ensure_ascii=False, indent=2)}

Approved architecture:
{architecture}

Project context, including exact existing-file snapshots:
{json.dumps(compact_context, ensure_ascii=False, indent=2)}

Orchestrator instructions:
{instructions}
""".strip()

    @classmethod
    def _validate_project_snapshot(cls, project_context):
        """Require a complete, internally consistent allowed-file snapshot."""

        if project_context.get("existing_files_complete") is not True:
            raise ValueError(
                "project_context must include a complete existing-file snapshot."
            )

        existing_file_paths = project_context.get("existing_file_paths")
        existing_files = project_context.get("existing_files")

        if not isinstance(existing_file_paths, list):
            raise ValueError("existing_file_paths must be a list.")

        if not isinstance(existing_files, dict):
            raise ValueError("existing_files must be a dictionary.")

        if any(
            not isinstance(path, str) or not path.strip()
            for path in existing_file_paths
        ):
            raise ValueError(
                "existing_file_paths must contain non-empty path strings."
            )

        if len(existing_file_paths) != len(set(existing_file_paths)):
            raise ValueError("existing_file_paths contains duplicate paths.")

        if set(existing_file_paths) != set(existing_files):
            raise ValueError(
                "The existing-file inventory and file contents do not match."
            )

        for path, content in existing_files.items():
            if not isinstance(content, str):
                raise ValueError(
                    f"Existing file content must be text: {path}"
                )

            path_obj = PurePosixPath(path)
            if path_obj.is_absolute() or ".." in path_obj.parts:
                raise ValueError(
                    f"Unsafe existing-file path in project context: {path}"
                )

            is_backend_file = path_obj.is_relative_to(cls.BACKEND_ROOT)
            is_requirements_file = path_obj.as_posix() == "requirements.txt"
            is_backend_test = (
                len(path_obj.parts) == 2
                and path_obj.parts[0] == "tests"
                and path_obj.name.startswith("test_backend_")
                and path_obj.suffix == ".py"
            )

            if not (
                is_backend_file
                or is_requirements_file
                or is_backend_test
            ):
                raise ValueError(
                    "Existing-file snapshots must stay within the allowed "
                    f"backend scope; got {path}"
                )

    @staticmethod
    def _normalize_newlines(content):
        """Convert Windows and old-style line endings to LF for patch output."""

        return content.replace("\r\n", "\n").replace("\r", "\n")

    @staticmethod
    def _validate_python_syntax(proposed_files):
        """Reject proposed Python files that cannot be parsed."""

        for proposed_file in proposed_files:
            if PurePosixPath(proposed_file.path).suffix != ".py":
                continue

            try:
                ast.parse(
                    proposed_file.content,
                    filename=proposed_file.path,
                )
            except SyntaxError as error:
                raise ValueError(
                    "The proposed Python file is not syntactically valid: "
                    f"{proposed_file.path}, line {error.lineno}, "
                    f"column {error.offset}: {error.msg}"
                ) from error

    @classmethod
    def _build_local_patch(cls, proposed_files, project_context):
        """Build a unified diff locally from exact existing-file snapshots."""

        existing_files = project_context["existing_files"]
        existing_paths = set(project_context["existing_file_paths"])
        patch_parts = []

        for proposed_file in proposed_files:
            path = proposed_file.path

            if path in existing_paths:
                old_content = existing_files[path]
                if old_content and not old_content.endswith(("\n", "\r")):
                    raise ValueError(
                        "Cannot safely build a patch for an existing file "
                        f"without a final newline: {path}"
                    )
                old_name = f"a/{path}"
            else:
                old_content = ""
                old_name = "/dev/null"

            old_content = cls._normalize_newlines(old_content)
            new_content = cls._normalize_newlines(proposed_file.content)

            if not new_content.endswith("\n"):
                new_content += "\n"

            old_lines = old_content.splitlines(keepends=True)
            new_lines = new_content.splitlines(keepends=True)

            diff = "".join(
                difflib.unified_diff(
                    old_lines,
                    new_lines,
                    fromfile=old_name,
                    tofile=f"b/{path}",
                    lineterm="\n",
                )
            )

            if not diff:
                raise ValueError(
                    f"The proposed content makes no change to {path}."
                )

            patch_parts.append(diff)

        patch = "".join(patch_parts)
        if not patch.strip():
            raise ValueError("The generated patch is empty.")

        return patch

    @staticmethod
    def _parse_json_response(response_text):
        text = response_text.strip()

        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError as error:
            start = max(0, error.pos - 120)
            end = min(len(text), error.pos + 120)
            excerpt = text[start:end].replace("\n", "\\n")

            raise ValueError(
                "The Backend Developer Agent response was not valid JSON. "
                f"Parser detail: {error.msg}, line {error.lineno}, "
                f"column {error.colno}. Near the error: {excerpt!r}"
            ) from error

        if not isinstance(data, dict):
            raise ValueError(
                "The Backend Developer Agent JSON must be an object."
            )

        return data

    @classmethod
    def _validate_changed_file_paths(cls, changed_files):
        for changed_file in changed_files:
            path = PurePosixPath(changed_file.path)

            if path.is_absolute() or ".." in path.parts:
                raise ValueError(
                    f"Unsafe changed-file path: {changed_file.path}"
                )

            is_backend_file = path.is_relative_to(cls.BACKEND_ROOT)
            is_requirements_file = path.as_posix() == "requirements.txt"
            is_backend_test = (
                len(path.parts) == 2
                and path.parts[0] == "tests"
                and path.name.startswith("test_backend_")
                and path.suffix == ".py"
            )

            if not (
                is_backend_file
                or is_requirements_file
                or is_backend_test
            ):
                raise ValueError(
                    "Backend changes must stay under pilot/app/backend/, "
                    "use the root requirements.txt, or use a top-level "
                    f"tests/test_backend_*.py file; got {changed_file.path}"
                )

    @classmethod
    def _validate_unified_diff(cls, patch, changed_files):
        """Reject malformed diffs and mismatches with the changed-file report."""

        if not isinstance(patch, str) or not patch.strip():
            raise ValueError("The proposed patch is empty.")

        lines = patch.splitlines()
        index = 0
        diff_paths = []

        def normalized_header_path(header):
            raw_path = header[4:].split("\t", 1)[0].strip()

            if raw_path == "/dev/null":
                return raw_path

            if raw_path.startswith(("a/", "b/")):
                raw_path = raw_path[2:]

            return PurePosixPath(raw_path).as_posix()

        while index < len(lines):
            git_header_match = cls.DIFF_HEADER_PATTERN.match(lines[index])

            if git_header_match:
                index += 1

                # Skip optional git metadata, such as index or file mode lines.
                while (
                    index < len(lines)
                    and not lines[index].startswith("--- ")
                    and not lines[index].startswith("diff --git ")
                ):
                    index += 1

                if index >= len(lines) or not lines[index].startswith("--- "):
                    raise ValueError(
                        "A git diff is missing its --- file header."
                    )

                old_path = normalized_header_path(lines[index])
                index += 1
                new_path = None
            elif lines[index].startswith("--- "):
                old_path = normalized_header_path(lines[index])
                index += 1
                new_path = None
            else:
                raise ValueError(
                    f"Invalid diff file header at patch line {index + 1}."
                )

            if index >= len(lines) or not lines[index].startswith("+++ "):
                raise ValueError(
                    "A unified diff is missing its +++ file header."
                )

            new_path = normalized_header_path(lines[index])
            index += 1

            if old_path != "/dev/null":
                old_path_obj = PurePosixPath(old_path)
                if old_path_obj.is_absolute() or ".." in old_path_obj.parts:
                    raise ValueError(
                        "The patch contains an unsafe old file path."
                    )

            if new_path != "/dev/null":
                new_path_obj = PurePosixPath(new_path)
                if new_path_obj.is_absolute() or ".." in new_path_obj.parts:
                    raise ValueError(
                        "The patch contains an unsafe new file path."
                    )

            changed_path = old_path if new_path == "/dev/null" else new_path
            diff_paths.append(changed_path)

            saw_hunk = False

            while index < len(lines) and not lines[index].startswith(
                "diff --git "
            ):
                if lines[index].startswith("--- "):
                    break

                hunk_match = cls.HUNK_HEADER_PATTERN.match(lines[index])
                if not hunk_match:
                    raise ValueError(
                        f"Invalid hunk header at patch line {index + 1}: "
                        f"{lines[index]}"
                    )

                expected_old = int(hunk_match.group(2) or "1")
                expected_new = int(hunk_match.group(4) or "1")
                actual_old = 0
                actual_new = 0
                saw_hunk = True
                index += 1

                while actual_old < expected_old or actual_new < expected_new:
                    if index >= len(lines):
                        raise ValueError(
                            "The patch ended before a hunk's declared lines "
                            "were present."
                        )

                    hunk_line = lines[index]

                    if hunk_line.startswith(("diff --git ", "@@ ")):
                        raise ValueError(
                            "A hunk contains fewer lines than its header "
                            "declares."
                        )

                    if hunk_line.startswith(" "):
                        actual_old += 1
                        actual_new += 1
                    elif hunk_line.startswith("-"):
                        actual_old += 1
                    elif hunk_line.startswith("+"):
                        actual_new += 1
                    elif hunk_line.startswith("\\"):
                        # Standard marker for a missing final newline.
                        pass
                    else:
                        raise ValueError(
                            "Invalid unified-diff content at patch "
                            f"line {index + 1}."
                        )

                    if actual_old > expected_old or actual_new > expected_new:
                        raise ValueError(
                            "A hunk contains more lines than its header "
                            "declares."
                        )

                    index += 1

            if not saw_hunk:
                raise ValueError(
                    f"Diff for {changed_path} contains no text changes."
                )

        reported_paths = [item.path for item in changed_files]

        if len(reported_paths) != len(set(reported_paths)):
            raise ValueError(
                "The changed_files report contains duplicate paths."
            )

        if set(diff_paths) != set(reported_paths):
            raise ValueError(
                "The paths in the patch do not match the changed_files "
                "report. "
                f"Patch paths: {sorted(diff_paths)}; "
                f"reported paths: {sorted(reported_paths)}."
            )
