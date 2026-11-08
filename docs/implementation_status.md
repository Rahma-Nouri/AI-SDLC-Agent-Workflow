# Implementation Status

*Updated 7 November 2026 (end of phase P1). "Verified" means: covered by the offline test suite **and** seen working in a real run, unless marked otherwise.*

## Project

- **Name:** AI-Supported End-to-End Software Development Workflow
- **Pilot:** AI-Powered Adaptive Exam Preparation System
- **Repository:** `Rahma-Nouri/AI-SDLC-Agent-Workflow` — World 1 on `main`, World 2 on `pilot/main`

## 1. Test Baseline

| Date | Command | Result |
|---|---|---|
| 29 Oct 2026 | `python -m pytest -q` | 63 passed, 1 skipped |
| 2 Nov 2026 (after P1.8, `434002f`) | `python -m pytest -q` | 120 passed, 1 skipped |
| 7 Nov 2026 (after P2.2a, `b9f0187`) | `python -m pytest -q` | 154 passed, 1 skipped |
| 7 Nov 2026 (after P2.2b, `23ad374`) | `python -m pytest -q` | 193 passed, 1 skipped |
| 7 Nov 2026 (after P2.2c) | `python -m pytest -q` | **236 passed, 2 skipped** |
| 7 Nov 2026 (after P2.2c) | `AI_SDLC_REAL_NPM=1`, real npm test only | 1 passed (real `npm install` of Vitest, passing and failing test) |

The two skipped tests are opt-in: the live Gemini check (`RUN_LIVE_GEMINI_TESTS=1`) and the real npm check (`AI_SDLC_REAL_NPM=1`). All other tests run offline, using a fake Gemini agent, a fake npm and temporary Git repositories.

## 2. Official Backlog Decision (29 Oct 2026)

The approved snapshot `outputs/approved_context/current_approved_context.json` (17 tasks, T1–T17) is the source of truth. Task IDs are frozen after H1 approval. The pilot's GitHub Issues are now created from this snapshot by the Issue Sync Agent (N3.4); the 39 older generated issues were closed as "not planned".

## 3. Implemented Components (by step)

| Step | Commit | What was added |
|---|---|---|
| Base | `4b97037`, `153f8aa`, `7b55ed8` | Orchestrator, H1, Software Architect, H2 with coverage validation, Backend Developer Agent (structured JSON, local patch building, path and syntax checks), Testing Agent, agent contracts |
| P1.1 | `f35d9ea` | Patch files written with LF line endings |
| P1.3 | `589f0e9` | `--run-t1-tests`: runs and saves the T1 test evidence |
| N2 | `26869c7`, `65d1765` | Two worlds on disk: the workflow uses the pilot only in its own workspace (`pilot/main` worktree); pilot files removed from `main` |
| N3.4 | `ffc21c4` | Issue Sync Agent and `--sync-pilot-issues` (GitHub Issues + two Projects boards) |
| P1.4 | `65aaef5` | Pilot Git Agent: own agent branch, agent commit identity |
| P1.5 | `6d9e07f` | Correction loop: tests → one automatic fix (tests frozen) → tests |
| P1.5b | `5ff1027` | Git and test output read as UTF-8 (Hungarian folder names); run records under `outputs/` kept and committed as evidence |
| P1.5c | `71e4eb1` | Pilot's own `.venv` with human-approved packages; `--retest-agent-branch` |
| P1.6 | `0eeecae` | H3 gate: approve (merge + push + issue comment) / revise (feedback → agent) / reject (branch deleted) |
| P1.8 | `434002f` | Automatic checks on the agent's code (imports vs requirements, unclosed resources), shown at H3 |
| P1.9 | `b839709` | README and documentation updated to the current state |
| P2.1, P2.1b | `a18a30b`, `8129a55` | Project-context document; whole spec for the Backend Agent, empty README removed, agent's own claims shown at H3 |
| P2.2a | `b9f0187` | H2b gate (`--h2b-ui-stack`): the Architect proposes the UI stack, the human decides, approved stack stored as `ui_stack` in the snapshot (React, TypeScript, Vite, Vitest, npm) |
| P2.2b | `23ad374` | Frontend Developer Agent and `--frontend-patch <task>`: UI patch on `agent/<task>-frontend-attempt-N`, committed as "AI-SDLC Frontend Agent"; H3 handles frontend branches, and shows only test reports from the branch under review. Built and tested offline with a fake Gemini client; not yet run with real Gemini |
| P2.2c | this change | Frontend environment (npm packages approved by the human when the list changes; registry packages only; `--ignore-scripts`) and Vitest runner; `--frontend-patch` now runs tests → one fix (frontend test files frozen) → tests; re-test and H3 revise use Vitest on frontend branches. Offline tests plus one real npm test; not yet run on the real pilot |
| P2.5b-1 | this change | `--backend-patch <task>`: the Backend Agent works on any approved backend task (same chain as `--resume-patch`, branch `agent/<task>-attempt-N`); the legacy run (no flag) is blocked once an approved snapshot exists |

## 4. Real-Run Evidence

The full chain ran for real on pilot task **T1** (database layer):

H1 → H2 → Backend Developer Agent (Gemini) → Pilot Git Agent (branch `agent/T1-attempt-1`) → package approval → tests → automatic fix → H3 revise (Windows file-lock bug, failure case #18, fixed by the agent from the human's written feedback) → H3 approve → merge into `pilot/main` at **`094028e`** (agent author) → push → comment on the T1 issue.

The human wrote no pilot code. Every agent action and every decision is committed under `outputs/` in the pilot history.

Failure cases #11–#18 found during these runs, and a comparison of the manual and agent-built T1, are recorded in the development notes.

## 5. Known Gaps

| Gap | Effect | Planned |
|---|---|---|
| ~~H2 "revise" asks the human to edit the architecture file by hand~~ | Fixed in P2.5b-2: revise = feedback to the Architect agent, which commits the change on `agent/architecture-revision-N` (approve merges, reject deletes) | Done |
| Legacy full run (`python main.py` without a flag) | Regenerates the backlog, overwrites the official snapshot, writes into the pilot without an agent commit | Align with the two-worlds design or remove |
| `pytest` is missing from `requirements.txt` | Needs a separate install (see README) | Small fix |
| Ticket Quality Checker not connected to the main flow | Task quality is not checked automatically | Later |
| T1 acceptance criteria not fully met (e.g. OpenAPI claim overstated, failure case #17) | Issue #47 stays open | Next T1 attempt or follow-up task |
| Pilot issues are created with the student's GitHub token | Issues show the student as the GitHub author (the text names the agent) | Possible bot account / GitHub App |
| Frontend code has no syntax or automatic checks (only `package.json` must be valid JSON; the automatic checks read `.py` files only) | Problems show up only when Vitest runs | Later |
| Automatic checks are heuristics | Can miss problems or warn without reason | Accepted for the PoC |
| Gemini free tier: 20 requests/day; occasional 503 errors | Limits how many runs fit in a day | Accepted for the PoC |

## 6. Next Steps (P2)

- Run further pilot tasks through the same chain and collect the evaluation data.
- First real frontend run (`--frontend-patch T7`) and Documentation Agent.
- Evaluation of the agents' work quality for the thesis.
