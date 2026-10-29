# AI-Supported End-to-End Software Development Workflow

## 1. Project Overview

This project explores how AI agents can support the software development lifecycle (SDLC), from requirements analysis and task planning to software architecture, implementation, testing, and documentation.

The goal is to develop a proof of concept (PoC) in which specialized AI agents collaborate through a structured workflow, while human approval checkpoints provide oversight over important decisions.

**Project status:** Prototype in development.

## 2. Pilot Project

The workflow is evaluated using a pilot project: an **AI-Powered Adaptive Exam Preparation System**.

The proposed application will help students prepare for examinations using study materials, practice questions, practice exams, summaries, feedback, and readiness assessment. Future adaptive functionality will personalize preparation based on student performance.

The current repository focuses on the AI-supported software development workflow and the generation of the pilot system's specification and technical architecture. It does not yet contain a complete implementation of the exam preparation application.

## 3. Current Workflow

The current prototype follows these stages:

1. **Requirements analysis:** The Orchestrator Agent analyzes the pilot specification and generates an initial plan.
2. **Task generation:** The Orchestrator converts the plan into structured development tasks.
3. **Task quality validation:** The Ticket Quality Checker validates the generated backlog and reports blocking errors and warnings.
4. **Human approval — H1:** A human reviews and approves, requests revisions to, or rejects the plan and task backlog.
5. **Architecture generation:** The Software Architect Agent generates a technical architecture based on the specification and approved tasks.
6. **Human approval — H2:** A human reviews the architecture and can approve, reject, or request manual revisions.
7. **GitHub synchronization:** After H1 and H2 approval and an additional user confirmation, the workflow creates GitHub Issues for the approved tasks.

If the required approvals are not obtained, the workflow stops before GitHub synchronization.

## 4. Implemented Components

| Component | Responsibility |
|---|---|
| `main.py` | Coordinates the current workflow and human approval checkpoints. |
| `agents/orchestrator.py` | Analyzes requirements, generates and revises plans, converts plans into tasks, validates task structure, manages approval-related state, and prepares task delegation. |
| `agents/software_architect.py` | Generates a technical architecture from the project specification and task backlog. |
| `agents/ticket_quality_checker.py` | Checks task quality, including required information, acceptance criteria, task IDs, dependencies, and dependency cycles. |
| `integrations/github_client.py` | Connects to GitHub, lists issues, and creates task-related issues. |
| `pilot/pilot_specification.md` | Contains the pilot project's specification. |
| `pilot/architecture_design.md` | Contains the generated technical architecture document. |
| `docs/ai_agent_organization.md` | Intended location for documentation of the agent organization; currently empty. |

The Orchestrator and Software Architect are the two currently implemented agent classes. The Ticket Quality Checker is a validation component, not a separate autonomous AI agent.

## 5. Human Approval Checkpoints

The workflow is designed around human oversight.

- **H1 — Plan and task backlog:** Review and approve the development plan and structured tasks before proceeding.
- **H2 — Software architecture:** Review and approve the generated architecture before GitHub synchronization.
- **H3 — Implementation and testing:** Planned checkpoint for reviewing implementation results and test evidence.
- **H4 — Deployment authorization:** Planned checkpoint for authorizing deployment.

H1 and H2 are implemented in the current main workflow. H3 and H4 remain planned and are not yet enforced as complete workflow stages.

## 6. Technology Stack

- **Language:** Python
- **LLM integration:** Google Gemini through `google-genai`
- **Configuration:** Environment variables loaded using `python-dotenv`
- **Task tracking and integration:** GitHub Issues
- **Testing:** Python test scripts under `tests/`

## 7. Repository Structure

```text
AI-SDLC-Agent-Workflow/
├── agents/
│   ├── orchestrator.py
│   ├── software_architect.py
│   └── ticket_quality_checker.py
├── config/
├── context/
├── docs/
│   └── ai_agent_organization.md
├── integrations/
│   └── github_client.py
├── outputs/
├── pilot/
│   ├── pilot_specification.md
│   └── architecture_design.md
├── tests/
├── main.py
├── requirements.txt
├── .env                 # Local configuration; not committed
└── .gitignore
```

## 8. Setup

### Prerequisites

- Python compatible with the installed dependencies
- A Google Gemini API key
- A GitHub personal access token with appropriate repository permissions
- Access to the target GitHub repository

### Create and activate a virtual environment

On Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### Install dependencies

```powershell
python -m pip install -r requirements.txt
```

### Configure environment variables

Create a local `.env` file in the project root:

```dotenv
GEMINI_API_KEY=your_gemini_api_key
GITHUB_TOKEN=your_github_token
GITHUB_REPOSITORY=owner/repository
```

Replace the example values with your own credentials and repository identifier. Never commit `.env` or expose API keys and tokens.

## 9. Run the Workflow

With the virtual environment activated and `.env` configured, run:

```powershell
python main.py
```

The current workflow uses interactive prompts for human decisions. It may call the Gemini API and create GitHub Issues after the required approvals and final confirmation.

**Important:** Running the workflow can create real GitHub Issues. Review the target repository and generated tasks before confirming synchronization.

## 10. Tests

The repository contains test scripts for the Orchestrator, Software Architect, Ticket Quality Checker, GitHub integration, and Gemini integration.

Run the test suite with:

```powershell
python -m pytest
```

If `pytest` is not installed in the environment, install it separately before running this command. Some integration tests may require credentials, network access, or a configured GitHub repository. Review each test before running it.

## 11. Current Limitations and Next Steps

The project is a work in progress. Current priorities include:

- Preserve exact task IDs in generated architecture documents.
- Add automated validation to ensure architecture coverage of all expected tasks.
- Complete documentation of the agent organization, workflow, and project context.
- Improve the integration between task validation and workflow orchestration where needed.
- Implement specialist agents for software development, testing, and documentation.
- Extend automated execution, result verification, and human approval stages.
- Evaluate the workflow using the pilot project.

Planned functionality should not be interpreted as already implemented.

## 12. Academic Context

This project is developed as part of **Project Lab 1** at Budapest University of Technology and Economics (BME), Faculty of Electrical Engineering and Informatics.

The work investigates a practical approach to AI-supported software engineering using specialized agents, structured tasks, GitHub integration, and human approval checkpoints.
