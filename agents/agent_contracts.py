from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator


class ContractModel(BaseModel):
    """Base model that rejects fields outside the agreed contract."""

    model_config = ConfigDict(extra="forbid")


class LlmUsage(ContractModel):
    """Gemini usage of one or more requests (evidence for the evaluation).

    A token count is None when Gemini did not report it: we never guess.
    total_tokens is Gemini's own total, which also counts the model's thinking
    tokens, so it can be larger than prompt_tokens + output_tokens.
    """

    requests: int
    prompt_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None


def llm_usage_from_response(response):
    """The usage of ONE Gemini request, read from the response's usage metadata."""

    usage = getattr(response, "usage_metadata", None)

    def count(name):
        value = getattr(usage, name, None)
        # Only a real whole number counts; anything else (missing, a test fake) is None.
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    return LlmUsage(
        requests=1,
        prompt_tokens=count("prompt_token_count"),
        output_tokens=count("candidates_token_count"),
        total_tokens=count("total_token_count"),
    )


def sum_llm_usage(usages):
    """Add up several usages, for example all Gemini requests of one correction loop.

    If one request has an unknown (None) count, the sum of that count is unknown too.
    No usages at all = no Gemini request was made, so every count is 0.
    """

    usages = [usage for usage in usages if usage is not None]

    def total(field):
        values = [getattr(usage, field) for usage in usages]
        return None if None in values else sum(values)

    return LlmUsage(
        requests=sum(usage.requests for usage in usages),
        prompt_tokens=total("prompt_tokens"),
        output_tokens=total("output_tokens"),
        total_tokens=total("total_tokens"),
    )


def usage_record(usage):
    """An LlmUsage as plain JSON data for a decision record; anything else is None."""
    return usage.model_dump() if isinstance(usage, LlmUsage) else None


class ChangedFile(ContractModel):
    path: str = Field(min_length=1)
    purpose: str = Field(min_length=1)


class ProposedFileChange(ContractModel):
    """A proposed complete file body returned by the coding model."""

    path: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    content: str = Field(min_length=1)


class AcceptanceCriterionResult(ContractModel):
    criterion: str = Field(min_length=1)
    status: Literal["addressed", "not_addressed"]
    evidence: str = Field(min_length=1)


class BackendDeveloperModelResponse(ContractModel):
    """Structured response Gemini returns before Python builds the patch."""

    task_id: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    proposed_files: list[ProposedFileChange] = Field(min_length=1)
    acceptance_criteria: list[AcceptanceCriterionResult] = Field(min_length=1)
    tests_added: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    unresolved_issues: list[str] = Field(default_factory=list)


class BackendDeveloperResult(ContractModel):
    """Final agent report consumed by main.py after local diff generation."""

    task_id: str = Field(min_length=1)
    summary: str = Field(min_length=1)

    # Python generates this diff from proposed_files and the supplied file snapshot.
    # A human must review and apply it.
    patch: str = Field(min_length=1)

    changed_files: list[ChangedFile] = Field(default_factory=list)
    acceptance_criteria: list[AcceptanceCriterionResult] = Field(min_length=1)
    tests_added: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    unresolved_issues: list[str] = Field(default_factory=list)
    # The Gemini request behind this result. None = no Gemini request (a test fake).
    llm_usage: LlmUsage | None = None


class FrontendDeveloperModelResponse(BackendDeveloperModelResponse):
    """Same fields as the backend response; only the agent differs."""


class FrontendDeveloperResult(BackendDeveloperResult):
    """Same fields as the backend result.

    Because it is a kind of BackendDeveloperResult, the steps that accept the
    backend result (saving the proposal, H3) accept this one too.
    """


class DocumentationModelResponse(BackendDeveloperModelResponse):
    """Same fields as the backend response; tests_added must stay empty (checked by the agent)."""


class DocumentationResult(BackendDeveloperResult):
    """Same fields as the backend result, so saving the proposal and H3 accept it too."""


class TestCheckResult(ContractModel):
    # This Pydantic model is imported by tests, but is not itself a test class.
    __test__ = False

    check_name: str = Field(min_length=1)
    command: str = Field(min_length=1)
    passed: StrictBool
    exit_code: int | None
    output: str


class TestingAgentResult(ContractModel):
    """Report actual test execution evidence for one approved task."""

    # Prevent pytest from trying to collect this Pydantic model as a test.
    __test__ = False

    task_id: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    checks: list[TestCheckResult] = Field(min_length=1)


class UiStackReasons(ContractModel):
    """One reason for each UI stack choice."""

    # str_strip_whitespace: a text of only spaces counts as empty.
    model_config = ConfigDict(str_strip_whitespace=True)

    framework: str = Field(min_length=1)
    language: str = Field(min_length=1)
    build_tool: str = Field(min_length=1)
    test_runner: str = Field(min_length=1)
    package_manager: str = Field(min_length=1)
    frontend_folder: str = Field(min_length=1)


class UiStackProposal(ContractModel):
    """The Architect's UI stack proposal, reviewed by the human at gate H2b."""

    model_config = ConfigDict(str_strip_whitespace=True)

    framework: str = Field(min_length=1)
    language: Literal["JavaScript", "TypeScript"]
    build_tool: str = Field(min_length=1)
    test_runner: str = Field(min_length=1)
    package_manager: str = Field(min_length=1)
    frontend_folder: str = Field(min_length=1)
    reasons: UiStackReasons
    alternatives_considered: list[str] = Field(min_length=1)
    risks: list[str] = Field(min_length=1)

    @field_validator("frontend_folder")
    @classmethod
    def must_be_inside_the_frontend_folder(cls, value):
        # Compare folder names, not text: a plain "starts with" check would
        # also accept pilot/app/frontend-old or pilot/app/frontend/../backend.
        path = PurePosixPath(value)
        if "\\" in value or ".." in path.parts or path.parts[:3] != ("pilot", "app", "frontend"):
            raise ValueError("frontend_folder must start with pilot/app/frontend")
        return value
