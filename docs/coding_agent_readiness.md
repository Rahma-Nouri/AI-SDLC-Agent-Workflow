# Coding Agent Readiness — Contracts and Safety Checks

## 1. Purpose

This document defines the minimum requirements that the AI-SDLC-Agent-Workflow must satisfy before implementing coding agents.

The goal is to build a reliable, traceable workflow in which AI agents work from approved requirements and architecture, produce reviewable changes, and provide evidence that their work has been tested.

The project should remain a lightweight proof of concept. New abstractions should be introduced only when they are needed by the working implementation.

## 2. Current Workflow

The intended workflow is:

1. The user provides or approves the pilot specification.
2. The Orchestrator analyzes the specification and generates a task backlog.
3. **H1 — Backlog Approval:** the user reviews and approves the generated tasks.
4. The Software Architect generates an architecture proposal from the approved tasks.
5. The system validates architecture coverage against the approved task IDs.
6. **H2 — Architecture Approval:** the user reviews and approves the validated architecture.
7. The workflow synchronizes approved tasks with GitHub Issues, when explicitly confirmed.
8. Coding agents implement approved tasks.
9. Testing agents execute relevant tests and report the actual results.
10. **H3 — Implementation and Test Review:** the user reviews the implementation and test evidence.
11. Documentation and final verification are performed before the workflow is considered complete.

Steps 8–11 represent the intended direction of the workflow. They must not be considered implemented until demonstrated in code.

## 3. Human Approval Gates

### H1 — Backlog Approval

**Purpose:** Ensure that the generated tasks correctly represent the requirements before architecture design and downstream actions.

The user should be able to review and revise the tasks.

The approved backlog should have:
- Stable, unique task IDs.
- Clear task descriptions.
- Acceptance criteria.
- Explicit dependencies where applicable.
- Traceability to the pilot requirements.

No architecture generation or GitHub issue synchronization should proceed using a rejected or unapproved backlog.

### H2 — Architecture Approval

**Purpose:** Ensure that the proposed architecture covers the approved tasks and is acceptable before implementation begins.

Before accepting the architecture, the system must validate the current architecture document against the approved backlog.

If the user edits the architecture after generation, the edited document must be validated again before approval.

The system must not:
- Accept an architecture with missing required task coverage.
- Replace the last known-good architecture with an invalid draft.
- Continue to GitHub synchronization or coding-agent execution after failed validation or rejection.

Approval must be an explicit user decision. Successful generation or validation alone does not constitute approval.

### H3 — Implementation and Test Review

**Purpose:** Review implementation changes and actual test evidence before accepting the result.

The review should include:
- The task being implemented.
- Files changed.
- A summary of the implementation.
- Tests added or executed.
- Actual test results.
- Known limitations and unresolved issues.

H3 is planned until its behavior is implemented and tested.

### Future Approval Gates

Additional approval gates may be introduced for documentation, integration, or final release if the pilot requires them. They should be added only when their responsibilities and conditions are clear.

## 4. Approved Task Contract

The approved task backlog is the source of truth for implementation planning.

Each task should contain, at minimum:

| Field | Purpose |
|---|---|
| Task ID | Stable identifier used throughout the workflow |
| Title or objective | What the task must achieve |
| Description | Scope and expected behavior |
| Acceptance criteria | Conditions that determine whether the task is complete |
| Dependencies | Other tasks that must be completed first |
| Source requirement | Requirement or specification that motivates the task |

Optional fields may include priority, implementation notes, affected components, and status.

### Task ID Rules

The workflow must preserve task IDs exactly as supplied by the approved backlog.

The system may encounter formats such as `T1`, `T12`, or `TASK-01`. Validation must support the formats used by the project without silently rewriting one format into another.

The validator should:
- Reject malformed or missing task IDs.
- Detect duplicate IDs.
- Preserve exact identifier spelling.
- Report required task IDs missing from the architecture.
- Report references to unknown task IDs where these are intended to be task references.

The approved task list used for architecture generation must be the same approved list used for architecture validation and subsequent implementation planning.

## 5. Software Architect Contract

### Input

The Software Architect receives:
- The approved task backlog.
- The pilot specification.
- Task dependencies and acceptance criteria.
- Relevant project constraints and conventions.

### Output

The Software Architect produces an architecture proposal that explains:
- The major components and their responsibilities.
- Component interactions and interfaces.
- Relevant data flow.
- Important technology and design decisions.
- How the approved tasks map to components or implementation responsibilities.
- Assumptions and unresolved design questions.

### Validation

Architecture generation and architecture validation are separate responsibilities.

The validation process must check that:
1. Every required approved task is covered.
2. Task IDs are preserved exactly.
3. No approved task is silently omitted.
4. Unknown task references are reported when detectable.
5. Duplicate or ambiguous mappings are identified when relevant.

A generated document must not be accepted merely because the language model returned a non-empty response.

The workflow should return a clear validation report and stop when required checks fail.

## 6. Coding Agent Contract

### Input

A coding agent should receive one approved task at a time, including:
- Task ID and objective.
- Acceptance criteria.
- Dependencies and their current status.
- The approved architecture.
- Relevant repository files.
- Applicable coding conventions and setup instructions.

The agent must not invent new requirements or silently change the approved architecture.

If requirements are ambiguous or dependencies are unresolved, the agent should report the issue rather than guessing.

### Output

For each task, the coding agent should provide:
- Task ID.
- Summary of changes.
- Files created or modified.
- Acceptance criteria addressed.
- Tests added or executed.
- Actual test results, including failures.
- Assumptions, limitations, and unresolved issues.

The agent must not claim that tests passed unless the tests were actually executed and their results support that claim.

### Initial Implementation Strategy

Start with one narrow, end-to-end coding-agent path for a single approved task.

Verify that the agent receives the correct context, makes bounded changes, and returns reviewable evidence before introducing multiple developer agents or parallel execution.

## 7. Testing Agent Contract

The Testing Agent should:
- Identify relevant tests for the approved task.
- Create or update tests where necessary.
- Execute tests in the available project environment when possible.
- Capture actual results and failure details.
- Report which requirements or acceptance criteria remain unverified.
- Return failures to the implementation stage for correction and retesting.

Test generation and test execution are different activities. Generated test code alone is not evidence of a passing implementation.

If a test cannot be executed because of missing dependencies, unavailable services, or environment limitations, the agent must report that limitation explicitly.

H3 should use the test evidence and implementation summary, not just the agent's verbal claim of success.

## 8. Shared Project Context

Agents should use explicit, current project artifacts rather than relying on assumptions or undocumented conversation history.

Relevant artifacts include:

| Artifact | Intended role |
|---|---|
| `pilot/pilot_specification.md` | Defines the pilot's requirements and scope |
| Approved task backlog | Canonical set of tasks approved at H1 |
| `pilot/architecture_design.md` | Architecture proposal reviewed at H2 |
| `README.md` | Project setup, usage, and general overview |
| `docs/` | Workflow contracts, implementation status, and project decisions |
| GitHub Issues | Work tracking and task synchronization, when enabled |

The canonical persistent location and format of the approved task backlog must be confirmed. Until then, the workflow must consistently pass the same approved in-memory task list to the Architect and validator.

Agents should receive only the relevant context needed for their assigned task.

A general-purpose context-management subsystem is not required for the initial proof of concept.

## 9. Failure Handling and Safety

The workflow should fail safely.

- Failed task validation must stop downstream processing.
- Failed architecture coverage validation must stop before H2.
- An invalid architecture draft must not overwrite the last known-good architecture.
- Rejected or unapproved work must not be sent to coding agents.
- GitHub issue synchronization must not happen before the required approvals and explicit confirmation.
- Coding agents must not claim completion when acceptance criteria remain unverified.
- Test failures must be recorded and returned for correction rather than hidden.
- External API failures must be reported clearly, without being confused with validation failures.

Where possible, these behaviors should be enforced through automated tests rather than relying only on prompts or documentation.

## 10. Readiness Checklist

### Workflow validation
- [ ] Task ID validation supports all task ID formats currently used by the project.
- [ ] Exact task IDs are preserved.
- [ ] Duplicate and malformed IDs are tested.
- [ ] Missing architecture coverage is detected.
- [ ] Unknown task references are handled where applicable.
- [ ] The architecture validator uses the approved backlog.

### Approval and persistence
- [ ] H1 occurs before architecture generation.
- [ ] H2 validates the current architecture document.
- [ ] Edited architecture is revalidated before acceptance.
- [ ] Invalid drafts cannot overwrite a known-good architecture.
- [ ] Rejection and validation failure stop downstream actions.
- [ ] GitHub synchronization requires the appropriate approval and explicit confirmation.

### Coding agent
- [ ] Input and output contracts are defined.
- [ ] One approved task can be implemented end to end.
- [ ] Changes and test evidence are reported.
- [ ] Unresolved requirements and dependencies are surfaced.

### Testing and review
- [ ] Tests are actually executed when the environment permits.
- [ ] Test results and limitations are recorded.
- [ ] Failures can be returned for correction and retesting.
- [ ] H3 reviews implementation and test evidence.

### Context and documentation
- [ ] The canonical approved-task artifact is identified.
- [ ] Agent inputs refer to current, approved artifacts.
- [ ] Implemented and planned capabilities are clearly distinguished.

## 11. Recommended Implementation Order

1. Fix task ID matching and architecture coverage validation.
2. Add regression tests for task IDs, missing coverage, and invalid architecture persistence.
3. Verify the H1 → Architect → validation → H2 flow.
4. Verify rejection, edited-document revalidation, and failure-stop behavior.
5. Confirm the canonical approved-task artifact and document the Orchestrator/Architect interfaces.
6. Implement one coding-agent path for a single approved task.
7. Add test execution, evidence reporting, and H3 review.
8. Expand to additional coding agents only after the first path is reliable.

## 12. Status Discipline

This document describes both requirements and intended future behavior.

A checklist item should be marked complete only after the behavior has been demonstrated in code or an automated test.

Planned agents and approval gates must remain labelled as planned until implemented.
