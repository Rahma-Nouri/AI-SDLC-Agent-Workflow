from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator


class ContractModel(BaseModel):
    """Base model that rejects fields outside the agreed contract."""

    model_config = ConfigDict(extra="forbid")


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


class FrontendDeveloperModelResponse(BackendDeveloperModelResponse):
    """Same fields as the backend response; only the agent differs."""


class FrontendDeveloperResult(BackendDeveloperResult):
    """Same fields as the backend result.

    Because it is a kind of BackendDeveloperResult, the steps that accept the
    backend result (saving the proposal, H3) accept this one too.
    """


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
