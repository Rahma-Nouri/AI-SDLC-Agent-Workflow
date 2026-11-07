# AI-Supported End-to-End Software Development Workflow

## 1. Project Overview

This project investigates how AI agents can support the software development lifecycle (SDLC), from requirements analysis and task planning to architecture, implementation, testing, and documentation.

It is a proof of concept (PoC): specialized AI agents do the work, and **human approval checkpoints** decide what happens next. The aim is not to build a new tool, but to combine existing models, agent patterns, and developer tools into one project-context-aware workflow, and to **evaluate how good the agents' work is**.

**Status:** prototype in development (Project Lab 1, BME VIK). See `docs/implementation_status.md` for what is implemented and verified today.

## 2. Two Separate Worlds

The repository holds two clearly separated parts:

| | World 1 — the workflow | World 2 — the pilot |
|---|---|---|
| What | The agents, the Orchestrator, the approval gates, `main.py`, the workflow tests, the docs | The pilot application: **AI-Powered Adaptive Exam Preparation System** |
| Built by | The student (human developer) | **Only the AI agents** |
| Human role | Developer | Decides at the gates only: **approve / revise / reject** ("revise" = written feedback that an agent acts on) |
| Branch | `main` (+ feature branches) | `pilot/main` (+ agent branches such as `agent/T1-attempt-1`), separate history, never merged into `main` |
| Folder on disk | `AI-SDLC-Agent-Workflow` | `AI-SDLC-pilot-workspace` (a Git worktree of `pilot/main`, next to the workflow folder) |

The workflow code reads and writes the pilot **only** in the pilot workspace (or in the folder named by the `PILOT_WORKSPACE` environment variable).

## 3. How a Task Moves Through the Workflow

```text
H1  approve plan + backlog (official snapshot, 17 tasks T1–T17)
H2  approve architecture (task coverage is validated automatically)
 │
 ├─ Backend Developer Agent (Gemini) proposes a change as structured JSON;
 │  the patch is built locally and checked (paths, Python syntax)
 ├─ Pilot Git Agent creates agent/<task>-attempt-N in the pilot workspace,
 │  applies the patch, and commits it as "AI-SDLC Backend Agent"
 ├─ Pilot Environment: the pilot's own .venv; new packages are installed only
 │  after the human approves the list
 ├─ Testing Agent runs the agent's tests; on failure ONE automatic fix
 │  (tests are frozen), then the tests run again
 ├─ Automatic checks warn about missing packages and unclosed resources
 │
H3  the human reviews commits, files, test evidence and warnings:
     approve → the agent merges into pilot/main, pushes, comments on the issue
     revise  → feedback goes to the Backend Agent; one change; tests run again
     reject  → the agent deletes its branch; pilot/main is unchanged
```

Every run record (proposals, reports, test results, H3 decisions) is committed by the agent under `outputs/` on the agent branch, so the evidence stays traceable.

## 4. Commands

Run from the workflow folder with the virtual environment active:

| Command | What it does | Gemini requests |
|---|---|---|
| `python main.py --resume-patch` | Fresh H1/H2 on the saved approved context, then: patch → agent branch → environment → tests → (one fix) → report; waits for H3 | 1 (+1 if a fix is needed) |
| `python main.py --h3-review` | H3 on the waiting agent branch: approve / revise / reject | 0 (revise: 1) |
| `python main.py --retest-agent-branch` | Re-runs the tests on the waiting agent branch | 0 |
| `python main.py --sync-pilot-issues` | Fresh H1, then the Issue Sync Agent aligns the pilot's GitHub Issues with the approved backlog (shows its plan first) | 0 |
| `python main.py --run-t1-tests` | Early T1-only test command (from P1.3) | 0 |
| `python main.py` | **Legacy full run:** regenerates the plan, backlog and architecture, **overwrites the official snapshot** (new task IDs), writes into the pilot workspace without an agent commit, then offers a patch. Not aligned with the two-worlds design yet; use only on purpose | several |

## 5. Task Management

GitHub Issues with two GitHub Projects boards:

- **Workflow development** — World 1 tasks, label `world: workflow`, created by the student.
- **Pilot: Exam Preparation System** — World 2 tasks, label `world: pilot`, created and updated by the Issue Sync Agent after H1 approval.

Each board only auto-adds issues with its own label, so the two worlds never mix.

## 6. Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m pip install pytest
```

Create the pilot workspace once (next to this folder):

```powershell
git fetch origin
git worktree add ..\AI-SDLC-pilot-workspace pilot/main
```

Create a local `.env` file (never commit it):

```dotenv
GEMINI_API_KEY=your_gemini_api_key
GITHUB_TOKEN=your_github_token
GITHUB_REPOSITORY=owner/repository
```

## 7. Tests

```powershell
python -m pytest -q
```

The default suite works offline (fake Gemini agent, temporary Git repositories). The live Gemini check is opt-in: `RUN_LIVE_GEMINI_TESTS=1`.

## 8. Repository Structure (World 1)

```text
AI-SDLC-Agent-Workflow/
├── agents/            # agents, gates and helpers (see docs/ai_agent_organization.md)
├── integrations/      # GitHub client
├── docs/              # agent organization, implementation status, archive
├── tests/             # offline test suite
├── main.py            # command-line entry point and gate flow
└── requirements.txt
```

## 9. Documentation

- `docs/ai_agent_organization.md` — agents, responsibilities, gates, diagram.
- `docs/implementation_status.md` — what is implemented and verified, known gaps.
- `docs/coding_agent_readiness.md` — historical design notes (October 2026).

## 10. Academic Context

Developed as part of **Project Lab 1** at the Budapest University of Technology and Economics (BME), Faculty of Electrical Engineering and Informatics. Supervisor: Pálfy Miklós Gábor. Industry partner: IRF Solutions Kft.
