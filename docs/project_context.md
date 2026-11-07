# Project Context

*Written 7 November 2026 (phase P2.1). Section 3 describes the code as it is today. Section 4 is a design and is not built yet.*

## 1. Purpose

**Project context** is the knowledge an AI agent receives when it runs: the requirements, the task, the architecture, the rules and the existing code. An agent knows nothing about the project except what it is given in that run.

This matters for two reasons:

- **Hallucination:** when an AI model lacks a fact, it may invent a believable answer instead of saying it does not know. Missing context makes this more likely.
- **Context loss:** knowledge that exists in the project does not reach the agent, or does not survive from one run to the next. Agents then repeat old mistakes or contradict earlier decisions.

This document records where project knowledge lives, what each agent receives today, the target design, and which gaps are fixed when.

## 2. Knowledge Sources

Paths under `pilot/` and `outputs/` are in the pilot workspace (World 2), `AI-SDLC-pilot-workspace`.

**Decision C1:** the approved snapshot, `outputs/approved_context/current_approved_context.json`, is the single source of truth for the plan, the tasks and the approved architecture. `pilot/architecture_design.md` is a working draft before H2 only. GitHub issues are a one-way copy for humans: agents never read tasks from issues. *(The code does not fully follow C1 yet; see Section 5.)*

| Knowledge | Location | Written by | When | Official? |
|---|---|---|---|---|
| Requirements | `pilot/pilot_specification.md` | Human | Once, before the workflow runs | **Yes** |
| Plan | Snapshot, field `plan` | Orchestrator; human approves | H1 | **Yes** (C1) |
| Tasks (T1–T17) | Snapshot, field `tasks` | Orchestrator; human approves | H1 | **Yes** (C1) |
| Approved architecture | Snapshot, field `architecture` | Architect; human approves | H2 | **Yes** (C1) |
| Architecture draft | `pilot/architecture_design.md` | Architect | Before H2 | No: working draft |
| Coding conventions | `pilot/conventions.md` | Architect proposes; human approves | H2 | Designed, not built (B1) |
| Hard-coded agent rules | `_build_prompt` in `agents/backend_developer.py` | Workflow developer | Development time | Only rules today |
| Pilot code and tests | `pilot/app/backend/`, `tests/`, `requirements.txt` | Backend Agent; committed by Pilot Git Agent | Agent branch, merged after H3 | **Yes**, once merged into `pilot/main` |
| Patches and agent reports | `outputs/proposed_changes/` | `save_patch_proposal` | Each patch, fix or revision | Evidence |
| Test and loop reports | `outputs/test_results/` | `record_loop_report` | After the loop, a re-test or a revision | Evidence |
| H3 decisions | `outputs/approvals/` | `write_decision_record` (human's decision) | H3 | **Yes** |
| H1 and H2 decisions | `approval_history` in memory; fixed values in the snapshot | `record_approval`, `request_backlog_approval`, `save_approved_context` | H1, H2 | Weak: no record file |
| Pilot GitHub issues | Label `world: pilot` | Issue Sync Agent; H3 comments | After H1; after H3 | No: one-way copy (C1) |
| Workflow documentation | `README.md`, `docs/` (World 1) | Human | During development | For the workflow; not given to agents |
| Failure cases | Author's development notes | Human | During development | Not in the repo |

## 3. Today: What Each Agent Receives

"LLM" means the agent calls the Gemini AI model. The other agents are plain Python. This table shows the state before the P2.1b fixes.

| Agent | Context received | Whole/part |
|---|---|---|
| Orchestrator (LLM) | Spec; previous plan and feedback when revising; only the plan when creating tasks | Whole |
| Software Architect (LLM) | Spec and all tasks; not the plan | Whole |
| Backend Agent (LLM) | One task, architecture, `instructions`, `requirements`, `python_version` | Whole |
| | `pilot_specification` | **Part:** first 3,000 of 5,684 characters |
| | `readme` | **Empty:** the pilot has no `README.md` |
| | `existing_files`: `pilot/app/backend/`, `requirements.txt`, `tests/test_backend_*.py` | Whole, no size limit |
| Correction loop | Same package, plus failed command, exit code and frozen test list | Test output: **part** (last 4,000 characters) |
| H3 revision | Same package, plus human feedback and last test output | Feedback whole; test output part |
| H3 review | Task ID and title, commits, `git diff --stat`, latest loop report | **Part:** no criteria or agent warnings |
| Testing Agent | `task_id`, `test_paths` | Part: no criteria |
| Issue Sync Agent | Snapshot tasks; GitHub issue data | Tasks whole |
| Pilot Git Agent | Patch; changed paths and short summary from the report | Patch whole; report part |
| Output validation | Changed `.py` files, `requirements.txt`, top-level pilot names | Whole; changed files only |
| Pilot Environment | `requirements.txt`, approval file, pytest version | Whole |
| Ticket Quality Checker | All generated tasks | Whole; legacy `python main.py` run only |

## 4. Target Design

*Status: designed, not built.*

The goal is to give each agent what it needs and nothing else. Context is organized in three layers:

- **L0, always:** project rules, coding conventions and the Python version.
- **L1, per task:** the task and its acceptance criteria; dependency status (done or not done, and what it produced); the relevant architecture slice; the relevant spec sections.
- **L2, on demand:** relevant existing code files, earlier reports, human feedback, and warnings from the automatic checks (`agents/output_validation.py`).

An **architecture slice** is only the part of the architecture that concerns one task. The whole spec is short today, so fix (a) in Section 5 sends all of it. Selecting relevant spec sections is the later target.

| Agent | L0 | L1 | L2 | Why |
|---|---|---|---|---|
| Orchestrator | Project rules | No | No | It creates the tasks from the whole spec, so no task exists yet |
| Software Architect | Yes | Whole backlog and spec | No | It designs the whole system and proposes the conventions |
| Backend Agent, new task | Yes | Yes | Relevant code files | It writes the code, so it needs the most precise context |
| Backend Agent, fix or revision | Yes | Yes | Code, reports, test output, feedback, check warnings | It must learn from what already failed |
| Testing Agent | No | Task and criteria | Test paths | It can report which criteria remain unverified |
| Output validation | No | No | Changed code files | It produces warnings; it needs no project knowledge |
| H3 review (human screen) | No | Task, criteria, dependency status | Reports, check warnings, agent's open issues | The human needs the evidence to decide |
| Issue Sync Agent | No | Tasks only | No | It only copies tasks to GitHub |
| Pilot Git Agent, Pilot Environment | No | No | No | They use only the patch or package list for their step |

**Decision B1:** the pilot's coding conventions will live in `pilot/conventions.md`. The Architect agent proposes the file and the human approves it at H2, because in World 2 the human writes nothing. **Status: designed, not built.** B1 depends on a known gap: H2 "revise" still asks the human to edit by hand (see Section 5).

## 5. Gaps and Decisions

**Decision A1:** P2.1b makes exactly the first three fixes below. Everything else waits.

| Gap | Effect | Decision |
|---|---|---|
| (a) Spec cut at 3,000 characters | Backend Agent never sees the end of section 4 or sections 5–8 | **P2.1b now:** send the whole spec |
| (b) Empty `readme` field sent | Wastes a field and suggests a document exists | **P2.1b now:** stop sending it |
| (c) H3 does not show `unresolved_issues`, `assumptions` or criteria status | Human decides without the agent's own warnings | **P2.1b now:** show them at H3 |
| No conventions file | Agents follow only hard-coded rules | Later: P2.5 (B1) |
| H2 "revise" needs a manual edit | Breaks the rule that the human writes nothing | Later: P2.5 (the end-to-end run passes H2, and B1 needs an agent-based H2 revise) |
| Code does not follow C1: agents use `pilot/architecture_design.md`; only `run_resume_patch` refreshes the snapshot | The two copies can drift apart | Later: P2.5 |
| Legacy `python main.py` overwrites the snapshot | The source of truth can be replaced by accident | Later: P2.5 |
| T1 hard-coded in `create_t1_backend_patch` and in the prompt | The chain cannot start T2 | Later: P2.5 |
| `T1_TEST_PATHS` names `tests/test_backend_database_schema.py`, which is not in the pilot | `--run-t1-tests` is broken | Later: P2.5 |
| `latest_test_report` is not filtered by task | A branch with no report could show T1's evidence at H3 | Later: P2.5 |
| No dependency status or finished-work context | Agent cannot see what earlier tasks produced | Later: P2.5 (L1) |
| Unmet criteria are not carried forward | Unfinished T1 work (issue #47) is invisible to agents | Later: P2.5 (L1) |
| Whole architecture sent for every task | 32 KB per request; about 15 KB of code today | Later: P2.5 (L1 slice) |
| `existing_files` has no limit or relevance filter | Requests grow with every task | Later: P2.5 (L2) |
| Fixes and revisions get no earlier summaries or feedback | Agent may repeat a failed approach | Later: P2.5 (L2) |
| Check warnings are not sent to fix attempts | Agent is not told what the checks found | Later: P2.5 (L2) |
| Failure lessons are not in the prompt | Checks warn after the code is written, not before | Later: P2.5 (L0) |
| Project context is built in three places; Python version is hard-coded in the prompt | The copies can disagree | Later: P2.5 (L0) |
| Testing Agent gets no criteria | It cannot report unverified criteria | Later: P3 |
| Tasks are created without the spec and have no "source requirement" field | A task cannot be traced to a requirement | Later: P3 |
| H1 and H2 have no decision record; H2 "revise" feedback is not stored | Those decisions cannot be traced in Git | Later: P3 |
| `requirements` sent twice; `get_task_issues` and `create_task_issue` unused by the workflow | Small duplication and dead code | Later: P3 |
| Issue comments are read by no agent | Follows from C1 (one-way copy) | Evaluation evidence |
| Plan is only redisplayed after H1 | Agents never use the plan's reasoning | Evaluation evidence |
| Same agent writes the code and the tests it is judged by | Tests may share the code's blind spots | Evaluation evidence (P3) |

## 6. Real Cases

These cases come from this project. The full failure-case list is kept in the author's development notes, not in the repo. Failure case numbers are not GitHub issue numbers.

**39 lost issues (#1–#39, closed as `not_planned`).** Issue #1 was a connection test. The other 38 came from three Gemini backlogs created on 18, 22 and 23 October 2026, with 10, 12 and 16 tasks. The numbering changed from `TASK-01` to `T01` to `T1`, and the same ID meant different work: `[T1]` in issue #24 is "Establish Tech Stack and System Architecture", but the official T1 is "System Architecture and Interface Design". After the official backlog decision of 29 October, the Issue Sync Agent closed all 39 as superseded. This is why C1 names one source of truth.

**Unclosed connection, twice (failure cases #12 and #18).** Case #12 (29 October): the agent's `db.py` used `with get_connection(...)`, which only commits and never closes the connection. Case #18 (2 November): the agent's test never called `engine.dispose()`, so the test database stayed locked on Windows, causing `WinError 32` in `tearDown`. The agent fixed this from the human's H3 feedback (commit `45735b6`). The lesson existed after the first case but never reached the agent. `agents/output_validation.py` now warns after the code is written, but the prompt still has no rule that would prevent the mistake. This is why L0 contains project rules and L2 contains check warnings.

**Spec cut mid-word.** The 3,000-character limit in `CONTEXT_FIELD_LIMITS` falls inside the word "different" in section 4. The agent received "dif", then a truncation note. The whole adaptive preparation section, the user scenario and the component list were missing. Which T1 results this affected is not known, but the agent was working from a partial spec. This is fix (a) in P2.1b.
