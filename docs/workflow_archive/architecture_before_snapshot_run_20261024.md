# System Architecture Document: AI-Powered Adaptive Exam Preparation System

## 1. System Overview

The AI-Powered Adaptive Exam Preparation System is a specialized learning platform designed to ingest students' course materials and historical/sample examination papers, analyze their structure and semantic content, and generate personalized, high-fidelity practice examinations. 

The system operates across three progressive functional tiers:
1. **Sample Exam Analysis & Generation (Core Pilot):** Parses previous or sample exams, derives an abstract structural blueprint (topics, question distribution, difficulty, and format), and generates novel, non-verbatim practice questions mirroring the source exam. It then accepts student submissions and provides automated evaluation with explanatory feedback.
2. **Extended Study Material Synthesis:** Ingests unstructured academic materials (lecture slides, notes, readings), synthesizes structured concept summaries and formulas, and builds multi-source practice questions mapped to explicit course topics.
3. **Adaptive Diagnostic & Remediation Loop:** Tracks student performance across topics longitudinally, quantifies topic mastery, dynamically adjusts practice difficulty and topic frequency to target weak areas, and recommends targeted study remediation.

The primary system design objective is to decouple ingestion, AI reasoning pipelines, and persistence so that the system remains accessible for a proof-of-concept (PoC) while providing deterministic guardrails over non-deterministic Large Language Model (LLM) operations.

---

## 2. Architecture Style

### Recommended Style: Modular Monolith with Asynchronous Job Pipeline
For this pilot project, the recommended architectural style is a **Modular Monolith backed by an Asynchronous Task Worker**.

```
+-----------------------------------------------------------------------+
|                             Web Frontend                              |
|                   (Single Page Application - SPA)                     |
+-----------------------------------+-----------------------------------+
                                    | HTTPS / REST & SSE
                                    v
+-----------------------------------+-----------------------------------+
|                       Backend API Gateway                             |
|          (Routing, Session Management, Validation Layer)              |
+----+-------------------+-------------------+--------------------+-----+
     |                   |                   |                    |
     v                   v                   v                    v
+----+------+      +-----+-----+       +-----+------+       +-----+-----+
| Document  |      | Blueprint |       | Practice   |       | Answer    |
| Ingestion |      | & Analysis|       | Generation |       | Evaluation|
| Module    |      | Module    |       | Module     |       | & Scoring |
+----+------+      +-----+-----+       +-----+------+       +-----+-----+
     |                   |                   |                    |
     +-------------------+-------------------+--------------------+
                                    |
                                    v
+-----------------------------------+-----------------------------------+
|               Asynchronous Job Queue & Orchestrator                   |
+-----------------------------------+-----------------------------------+
        |                           |                           |
        v                           v                           v
+-------+-------+           +-------+-------+           +-------+-------+
|  Local Document           | LLM Gateway   |           |  Relational   |
|  Storage / Temp           | & Guardrails  |           |  Database     |
|  File System              | (Ollama/APIs) |           |  (SQLite/PG)  |
+---------------+           +---------------+           +---------------+
```

### Architectural Justification for the Pilot
* **Minimizes Operational Overhead:** Avoids the premature complexity of microservices (distributed tracing, network partitions, multi-repo deployments) while maintaining strict domain boundaries in code.
* **Accommodates Long-Running AI Operations:** Document parsing, blueprint extraction, and multi-question LLM generation inherently incur latencies ranging from 5 to 60+ seconds. An asynchronous task execution pattern prevents HTTP connection timeouts and maintains frontend responsiveness.
* **Low-Cost and Beginner-Friendly:** The system can be executed entirely on a single development machine using local files and open-source models, satisfying the requirement to avoid mandatory paid infrastructure.

---

## 3. System Components

### 3.1. Web Frontend (Single Page Application)
* **Responsibilities:**
  * Provides document drag-and-drop interfaces with upload progress and parsing feedback.
  * Renders dynamic, interactive exam forms accommodating diverse question formats (Multiple Choice Questions [MCQ], short answer, open-ended).
  * Displays grading outcomes, answer explanations, and longitudinal topic mastery dashboards.
  * Exposes adaptive session configuration and topic remediation suggestions.

### 3.2. API Gateway & Session Coordinator
* **Responsibilities:**
  * Exposes unified REST endpoints for frontend interaction.
  * Handles student identity/session persistence and request validation.
  * Coordinates module workflows and dispatches long-running jobs to the background task runner.

### 3.3. Document Ingestion & Parsing Service
* **Responsibilities:**
  * Validates file formats and guards against malicious or unparseable payloads.
  * Extracts structural layout and raw text from sample exams (`.pdf`, `.docx`, `.txt`) and lecture materials (`.pptx`, multi-column `.pdf`).
  * Stores normalized text documents and document metadata.

### 3.4. Blueprint & Question Analysis Pipeline
* **Responsibilities:**
  * Analyzes parsed exam text using structured prompt pipelines.
  * Identifies subject taxonomy, topic distributions, question formats, and cognitive difficulty tiers (e.g., Bloom’s taxonomy: Recall, Application, Analysis).
  * Emits an immutable, validated `ExamBlueprint` JSON document.

### 3.5. Novel Practice Exam Generation Engine
* **Responsibilities:**
  * Consumes an `ExamBlueprint` (and optionally indexed lecture materials) to generate new, fully populated practice exams.
  * Enforces anti-plagiarism guardrails by comparing generated candidate questions against source questions using n-gram overlap and semantic similarity checks.
  * Validates LLM responses against strict JSON schemas before committing to storage.

### 3.6. Evaluation & Feedback Engine
* **Responsibilities:**
  * Evaluates student exam submissions deterministically for objective items (MCQ matching).
  * Executes rubric-based LLM evaluation for open-ended or short-answer questions.
  * Produces itemized scores, actionable corrective guidance, and identifies the core concept responsible for any error.

### 3.7. Topic Mastery & Adaptive Calibration Engine
* **Responsibilities:**
  * Aggregates item-level attempt results into longitudinal topic mastery scores.
  * Computes error frequency vectors per topic.
  * Dynamically calculates target question counts and difficulty distributions for subsequent adaptive practice sessions.

### 3.8. LLM Gateway & Guardrails Module
* **Responsibilities:**
  * Acts as the single internal abstraction layer over LLM execution providers (e.g., local Ollama instance or external API).
  * Enforces JSON structured outputs, response retries on malformed schemas, and token truncation safeguards.

---

## 4. Component Interfaces and APIs

All asynchronous operations return a `202 Accepted` response with a polling task endpoint (`/api/v1/tasks/{task_id}`). Clients query the task status or receive Server-Sent Events (SSE) updates until completion.

*Assumption: Student identity is handled via a lightweight session identifier passed in an `X-Session-ID` header.*

```
Frontend                     API Gateway               Background Worker / LLM
   |                              |                               |
   |-- POST /exams/ingest ------->|                               |
   |   (Upload sample file)       |-- Enqueue parsing task ------>|
   |<- 202 Accepted (task_id) ----|                               |
   |                              |                               |-- Parses doc & generates blueprint
   |-- GET /tasks/{task_id} ----->|                               |
   |<- 200 In-Progress -----------|                               |
   |                              |                               |-- Blueprint completed
   |-- GET /tasks/{task_id} ----->|                               |
   |<- 200 Completed (result_id) -|                               |
   |                              |                               |
   |-- POST /exams/generate ----->|                               |
   |   (Send blueprint_id)        |-- Enqueue generation task --->|
   |<- 202 Accepted (task_id) ----|                               |
   |                              |                               |-- Generates non-duplicate questions
   |-- GET /tasks/{task_id} ----->|                               |
   |<- 200 Completed (exam_id) ---|                               |
   |                              |                               |
   |-- GET /exams/{exam_id} ----->|                               |
   |<- 200 Practice Exam Payload -|                               |
```

### 4.1. Document Ingestion API

#### `POST /api/v1/documents/upload`
Uploads a sample exam or study material file.
* **Request:** `multipart/form-data`
  * `file`: Binary file stream (`.pdf`, `.docx`, `.txt`, `.pptx`)
  * `document_type`: String (`"sample_exam"` | `"lecture_note"` | `"slides"`)
* **Response:** `202 Accepted`
```json
{
  "task_id": "task_doc_89a7fbc2",
  "status": "PROCESSING",
  "document_id": "doc_550e8400"
}
```

#### `GET /api/v1/tasks/{task_id}`
Checks the progress of an asynchronous extraction or generation task.
* **Response:** `200 OK`
```json
{
  "task_id": "task_doc_89a7fbc2",
  "status": "SUCCESS",
  "progress_percentage": 100,
  "result_reference_id": "doc_550e8400",
  "error_message": null
}
```

### 4.2. Blueprint & Exam Generation API

#### `POST /api/v1/blueprints/analyze`
Triggers structural and taxonomy analysis on an ingested sample exam.
* **Request:** `application/json`
```json
{
  "document_id": "doc_550e8400"
}
```
* **Response:** `202 Accepted` (`task_id` returned)

#### Blueprint Retrieval Schema (Result of Blueprint Analysis)
* **Endpoint:** `GET /api/v1/blueprints/{blueprint_id}`
* **Response:** `200 OK`
```json
{
  "blueprint_id": "bp_4a11b6c8",
  "source_document_id": "doc_550e8400",
  "exam_title": "Introduction to Operating Systems - Midterm",
  "estimated_time_minutes": 60,
  "topics": [
    {
      "topic_id": "top_proc_sync",
      "name": "Process Synchronization",
      "weight_percentage": 40.0
    },
    {
      "topic_id": "top_mem_mgmt",
      "name": "Virtual Memory and Paging",
      "weight_percentage": 60.0
    }
  ],
  "question_breakdown": [
    {
      "topic_id": "top_proc_sync",
      "question_type": "MULTIPLE_CHOICE",
      "count": 2,
      "difficulty": "INTERMEDIATE"
    },
    {
      "topic_id": "top_mem_mgmt",
      "question_type": "SHORT_ANSWER",
      "count": 2,
      "difficulty": "ADVANCED"
    }
  ]
}
```

#### `POST /api/v1/exams/generate`
Generates a novel exam conforming to a blueprint.
* **Request:** `application/json`
```json
{
  "blueprint_id": "bp_4a11b6c8",
  "study_material_ids": ["doc_661f9511"],
  "mode": "STANDARD" 
}
```
* **Response:** `202 Accepted` (Returns `task_id`)

#### Practice Exam Schema (Result of Exam Generation)
* **Endpoint:** `GET /api/v1/exams/{exam_id}`
* **Response:** `200 OK`
```json
{
  "exam_id": "exam_7c9e6679",
  "blueprint_id": "bp_4a11b6c8",
  "title": "Practice Exam: Operating Systems",
  "questions": [
    {
      "question_id": "q_001",
      "topic_id": "top_proc_sync",
      "question_type": "MULTIPLE_CHOICE",
      "difficulty": "INTERMEDIATE",
      "prompt": "Which of the following conditions is NOT required for a deadlock to occur?",
      "options": [
        "Mutual Exclusion",
        "Hold and Wait",
        "Preemption Allowed",
        "Circular Wait"
      ],
      "max_points": 5
    },
    {
      "question_id": "q_002",
      "topic_id": "top_mem_mgmt",
      "question_type": "SHORT_ANSWER",
      "difficulty": "ADVANCED",
      "prompt": "Explain the difference between internal and external fragmentation in paging systems.",
      "options": null,
      "max_points": 10
    }
  ]
}
```
*(Note: Correct answers and grading rubrics are withheld in the student-facing exam endpoint and stored securely on the backend).*

### 4.3. Exam Submission & Evaluation API

#### `POST /api/v1/exams/{exam_id}/submit`
Submits student answers for immediate or asynchronous evaluation.
* **Request:** `application/json`
```json
{
  "exam_id": "exam_7c9e6679",
  "responses": [
    {
      "question_id": "q_001",
      "selected_option_index": 2,
      "text_answer": null
    },
    {
      "question_id": "q_002",
      "selected_option_index": null,
      "text_answer": "Internal fragmentation occurs when allocated memory is larger than requested. External happens when total free memory satisfies a request, but it is not contiguous."
    }
  ]
}
```
* **Response:** `200 OK` (or `202 Accepted` if open-ended questions require asynchronous evaluation)
```json
{
  "attempt_id": "att_88b12e34",
  "exam_id": "exam_7c9e6679",
  "score_achieved": 14.0,
  "total_possible": 15.0,
  "evaluations": [
    {
      "question_id": "q_001",
      "is_correct": true,
      "points_awarded": 5.0,
      "feedback": "Correct. Preemption must be disallowed for deadlock to persist."
    },
    {
      "question_id": "q_002",
      "is_correct": true,
      "points_awarded": 9.0,
      "feedback": "Accurate definition. Full credit requires noting that paging eliminates external fragmentation entirely."
    }
  ]
}
```

### 4.4. Study Material Summaries & Diagnostics API

#### `POST /api/v1/materials/{document_id}/summarize`
Generates factual summaries, formulas, and definitions from uploaded notes.
* **Response:** `200 OK`
```json
{
  "document_id": "doc_661f9511",
  "summary": "Covers virtual memory systems, page tables, TLB caching, and replacement algorithms.",
  "key_points": [
    "Paging avoids external fragmentation by allocating fixed-size frames.",
    "TLB hit ratio strongly governs effective memory access time."
  ],
  "formulas": [
    "EAT = Hit_Rate * (TLB_Access_Time + Memory_Access_Time) + (1 - Hit_Rate) * (TLB_Access_Time + 2 * Memory_Access_Time)"
  ]
}
```

#### `GET /api/v1/diagnostics/readiness`
Fetches topic-level mastery and readiness indicators.
* **Response:** `200 OK`
```json
{
  "overall_readiness_score": 68.5,
  "topic_mastery": [
    {
      "topic_id": "top_proc_sync",
      "topic_name": "Process Synchronization",
      "mastery_percentage": 90.0,
      "status": "MASTERED"
    },
    {
      "topic_id": "top_mem_mgmt",
      "topic_name": "Virtual Memory and Paging",
      "mastery_percentage": 47.0,
      "status": "REMEDIATION_REQUIRED"
    }
  ],
  "recommended_focus_topic_id": "top_mem_mgmt"
}
```

---

## 5. Data Design

### 5.1. Entity Relationships (ER Diagram)

```
+--------------------+            1:N            +---------------------+
|      Document      |-------------------------->|    ExamBlueprint    |
|--------------------|                           |---------------------|
| id (PK)            |                           | id (PK)             |
| doc_type           |                           | source_doc_id (FK)  |
| raw_text_path      |                           | blueprint_schema    |
| created_at         |                           | created_at          |
+--------------------+                           +---------------------+
          |                                                 |
          | 1:N                                             | 1:N
          v                                                 v
+--------------------+                           +---------------------+
|  MaterialSummary   |                           |    GeneratedExam    |
|--------------------|                           |---------------------|
| id (PK)            |                           | id (PK)             |
| document_id (FK)   |                           | blueprint_id (FK)   |
| summary_text       |                           | title               |
| key_points (JSON)  |                           | created_at          |
| formulas (JSON)    |                           +---------------------+
+--------------------+                                      |
                                                            | 1:N
                                                            v
+--------------------+            1:N            +---------------------+
|    ExamAttempt     |-------------------------->|      Question       |
|--------------------|                           |---------------------|
| id (PK)            |                           | id (PK)             |
| exam_id (FK)       |                           | exam_id (FK)        |
| student_id         |                           | topic_id (FK)       |
| total_score        |                           | question_type       |
| submitted_at       |                           | prompt              |
+--------------------+                           | options (JSON)      |
          |                                      | solution_rubric     |
          | 1:N                                  | max_points          |
          v                                      +---------------------+
+--------------------+                                      |
|  QuestionResponse  |                                      |
|--------------------|<-------------------------------------+
| id (PK)            |  1:N
| attempt_id (FK)    |
| question_id (FK)   |
| student_answer     |
| score_awarded      |
| feedback_text      |
+--------------------+

+--------------------------+
|   StudentTopicMastery    |
|--------------------------|
| id (PK)                  |
| student_id               |
| topic_id                 |
| attempts_count           |
| correct_count            |
| mastery_level (FLOAT)    |
| last_evaluated_at        |
+--------------------------+
```

### 5.2. Persistence Selection & Justification
* **Relational Database (SQLite for PoC / PostgreSQL for production deployment):**
  * *Justification:* The entity structure is inherently relational. Relationships between exams, blueprints, questions, attempts, and individual responses require referential integrity and transactional isolation. SQLite is embedded, zero-configuration, and requires no external server, making it ideal for the proof of concept.
* **File Storage:**
  * Raw uploaded files (`.pdf`, `.docx`) and large intermediate parse trees are stored on the local file system with relative paths referenced in the `Document` database table. This avoids database bloat.

### 5.3. Unresolved Data Requirements
* **Data Retention Policy:** How long generated exams, parsed student notes, and session logs should be retained before automated garbage collection.
* **Multi-tenant / Multi-student Isolation:** For this pilot, the data model isolates records using a simple `student_id` attribute. If enterprise single sign-on (SSO) or institutional multi-tenancy is added later, row-level tenant keys will be required.

---

## 6. Technology Recommendations

To ensure beginner-friendliness and zero required software licensing costs, all recommendations favor open-source software and tools with vibrant communities.

| Category | Recommended Technology | Rationale |
| :--- | :--- | :--- |
| **Backend Language & Framework** | **Python 3.11+ / FastAPI** | FastAPI provides high throughput, automatic OpenAPI documentation, asynchronous endpoint handling, and native integration with Python's data-science and LLM libraries. |
| **Data Validation** | **Pydantic v2** | Native to FastAPI; provides schema parsing and serialization for LLM structured outputs. |
| **Relational Database** | **SQLite (via SQLAlchemy ORM)** | Embedded, file-based, zero-cost database. Allows instant local setup. SQLAlchemy abstracts dialect specifics, enabling seamless migration to PostgreSQL if required. |
| **Document Extractors** | `pypdf`, `python-docx`, `python-pptx`, `pdfplumber` | Lightweight, open-source Python libraries handling parsing across target formats without external cloud dependency. |
| **Asynchronous Job Runner** | **FastAPI `BackgroundTasks` / Celery (Optional)** | For initial pilot scale, FastAPI's built-in `BackgroundTasks` avoids spinning up external Redis/RabbitMQ infrastructure while keeping long LLM queries non-blocking. |
| **LLM Inference Engine** | **Ollama (local) or Pluggable OpenAI-compatible API** | Ollama runs open-weight models (e.g., Llama 3 8B, Mistral 7B) locally at zero API cost. The client layer can use standard client SDKs with configurable base URLs to allow swapping between local and hosted endpoints. |
| **Frontend Framework** | **React with Vite + Tailwind CSS** | Standard, well-documented web development stack. Offers component libraries and fast compile times. |
| **Similarity / Anti-Plagiarism**| `scikit-learn` (TF-IDF & Cosine Similarity) | Standard open-source library used to calculate textual overlap and prevent generated practice questions from copying source exams verbatim. |

---

## 7. Mapping to Approved Tasks

| Task ID | Task Title | Supporting Components | Implementation Details & Architectural Notes | Task Dependencies & Clarification Notes |
| :--- | :--- | :--- | :--- | :--- |
| **T1** | System Architecture and API Design | Entire System | Defines architectural blueprints, REST endpoints, data entity models, component boundaries, and technology stack. | **Dependencies:** None.<br>*Note:* Satisfied by this document. |
| **T2** | LLM Strategy and Schema Specification | LLM Gateway, Blueprint Module, Generation Engine, Evaluation Engine | Specifies exact JSON schemas for inputs/outputs, anti-plagiarism prompts, and rubrics for scoring. | **Dependencies:** T1.<br>*Note:* Must establish deterministic JSON extraction contracts. |
| **T3** | Exam Document Ingestion and Parsing Service | Document Ingestion Module | Implements file upload endpoints, security sanitization, and parsers for `.pdf`, `.docx`, and `.txt` files containing exams. | **Dependencies:** T1.<br>*Note:* Dependent only on initial API and storage design. |
| **T4** | Exam Structure and Taxonomy Analysis Pipeline | Blueprint & Question Analysis Pipeline | Runs prompt-driven analysis on extracted exam text to build an abstract `ExamBlueprint` JSON instance. | **Dependencies:** T2, T3.<br>*Note:* Requires both schema definition and ingested text. |
| **T5** | Novel Practice Exam Generation Engine | Practice Exam Generation Module, LLM Gateway | Synthesizes new questions based on an `ExamBlueprint`. Checks candidate questions against the source exam using similarity algorithms to ensure originality. | **Dependencies:** T4.<br>*Note:* Directly consumes blueprint objects produced by T4. |
| **T6** | Exam Upload and Interactive Taking Interface | Web Frontend (SPA) | Implements student UI for file upload, rendering dynamic test question items (MCQ, text), and answer submission. | **Dependencies:** T1.<br>*Note:* Frontend UI design can proceed once T1 APIs are specified; will consume mock endpoints before backend completion. |
| **T7** | Automated Answer Evaluation and Feedback Service | Answer Evaluation & Scoring Engine | Evaluates objective responses deterministically and evaluates open-ended responses using rubric-guided LLM calls. | **Dependencies:** T5, T6.<br>*Note:* Requires practice exam schemas from T5 and submission payloads from T6. |
| **T8** | Phase 1 Core Workflow Integration and Validation | Web Frontend, API Gateway, Storage, LLM Gateway | End-to-end integration and automated validation covering the full workflow: upload -> blueprinting -> generation -> taking -> evaluation. | **Dependencies:** T7.<br>*Clarification:* T8 formally validates all Phase 1 components (T1 through T7). |
| **T9** | Multi-Format Study Material Ingestion Pipeline | Document Ingestion Module | Extends parsing infrastructure to process lecture notes and presentation slides (`.pptx`, complex layout PDFs). | **Dependencies:** T8.<br>*Clarification:* Commences Phase 2 extended capabilities after Phase 1 validation. |
| **T10** | Study Material Summarization and Key Points Extraction | Blueprint & Analysis Module, LLM Gateway | Implements prompt pipelines to generate summaries, formula sheets, and key concept definitions from ingested notes. | **Dependencies:** T9.<br>*Note:* Consumes parsed material output from T9. |
| **T11** | Multi-Source Question Synthesis Engine | Practice Exam Generation Module, LLM Gateway | Generates targeted practice questions directly from lecture notes and slide concepts, linking items to source citations. | **Dependencies:** T10.<br>*Note:* Depends on extracted concepts and summaries from T10. |
| **T12** | Topic Readiness Checking and Diagnostics Dashboard | Topic Mastery & Calibration Engine, Web Frontend | Calculates topic mastery scores and displays readiness indicators, highlighting weak areas on the frontend dashboard. | **Dependencies:** T8, T11.<br>*Note:* Combines exam evaluation outcomes with topic models derived from study materials. |
| **T13** | Longitudinal Performance Profiling Service | Topic Mastery & Calibration Engine, Relational DB | Persists and indexes student attempts, error frequencies, and question-level historical metrics across sessions. | **Dependencies:** T12.<br>*Note:* Extends diagnostic scoring into a persistent profile over time. |
| **T14** | Adaptive Question Distribution and Difficulty Calibration | Topic Mastery & Calibration Engine, Generation Module | Modulates the question selection algorithm to prioritize weak topics and dynamically calibrate question difficulty. | **Dependencies:** T13.<br>*Note:* Uses historical metrics from T13 to adjust generation blueprints. |
| **T15** | Remediation Recommendation and Adaptive Session UI | Web Frontend, API Gateway | Presents personalized remediation suggestions and allows students to launch targeted, adaptive study sessions. | **Dependencies:** T14.<br>*Clarification:* Final capstone task closing the adaptive feedback loop. |

---

## 8. Risks and Open Questions

### 8.1. Technical and Operational Risks

```
+-----------------------------------------------------------------------------------------+
|                                    RISK MATRIX                                          |
|                                                                                         |
|      High  |                                   | * Hallucination / Fact Distortion      |
|            |                                   | * Context Window / Token Exhaustion    |
|  S         |                                   |                                        |
|  E  Medium | * Non-Deterministic Output Schema | * Verbatim Plagiarism Leaks            |
|  V         | * Complex PDF Parsing Failures    |                                        |
|  E         |                                   |                                        |
|  R   Low   | * SQLite File Locking Contention  |                                        |
|  I         |                                   |                                        |
|  T         +-----------------------------------+----------------------------------------+
|  Y                         Low                                   High                   |
|                                         P R O B A B I L I T Y                           |
+-----------------------------------------------------------------------------------------+
```

1. **LLM Output Hallucination & Fact Distortion:**
   * *Risk:* When generating questions or grading subjective answers from complex lecture topics, the LLM may introduce factual inaccuracies or grade a correct answer as incorrect.
   * *Mitigation:* Constrain generation using grounded context prompts (Retrieval/Few-Shot extracts). Expose explicit rubrics and allow students to flag disputed evaluations.
2. **Context Window & Token Exhaustion:**
   * *Risk:* Uploading a semester's worth of slides (100+ pages) will exceed the context window of open-weight models (e.g., 8k–32k context).
   * *Mitigation:* Apply chunking during ingestion in T9. Summarize materials hierarchically (slide-by-slide or section-by-section) rather than passing entire decks in a single prompt.
3. **Verbatim Question Copying (Plagiarism):**
   * *Risk:* The LLM may reproduce example questions word-for-word rather than creating novel variations.
   * *Mitigation:* Implement a post-generation validation step that computes lexical (TF-IDF / ROUGE) and semantic similarity between candidate questions and source exam items. Reject candidates with high similarity scores (>0.75).
4. **Structural Output Violations:**
   * *Risk:* An LLM may output unparseable text or markdown instead of valid JSON, breaking downstream pipelines.
   * *Mitigation:* Enforce JSON mode (e.g., Ollama JSON format flags or Pydantic validation loops) with an automated retry loop (max 3 retries) on parsing failures.
5. **Document Formatting and Layout Corruption:**
   * *Risk:* Multi-column PDFs, tables, or formula-heavy slides may parse into jumbled or unreadable text streams.
   * *Mitigation:* Use layout-aware extraction libraries (`pdfplumber` over plain text dumps). For tabular structures, extract layout hints and surface informative errors when text is unreadable.

### 8.2. Confirmed Requirements vs. Architectural Assumptions

* **Confirmed Requirements:**
  * System ingests exams and outputs novel, non-verbatim practice questions matching structure, topic, and difficulty.
  * System evaluates student answers and provides corrective explanations.
  * Extended capability ingests lecture notes/slides to extract summaries and key points.
  * System supports adaptive question weighting based on student performance.
  * System must be designed without code implementation at this stage.

* **Architectural Assumptions (Subject to Team Confirmation):**
  * *Assumption 1 (Auth Scope):* For the pilot PoC, student identity can be handled through lightweight session tokens without requiring full institutional SSO or third-party OAuth.
  * *Assumption 2 (Mathematical Formulas):* Formulas in notes and exams can be represented in standard LaTeX format within markdown strings.
  * *Assumption 3 (Execution Environment):* The system will be evaluated on commodity developer machines, making local models (e.g., Llama 3 8B via Ollama) or pluggable API mocks the primary inference targets.
  * *Assumption 4 (Language Support):* The initial pilot will operate on English-language text documents.

---

## 9. Human Review Decisions

The following architectural decisions require human stakeholder review and sign-off before downstream agents proceed with implementation:

1. **LLM Hosting Strategy for Development and Testing:**
   * *Option A:* Local inference using Ollama (free, private, hardware-dependent; requires ~8GB–16GB VRAM for decent latency).
   * *Option B:* Cloud-based external LLM APIs (consistent speed, small per-call cost, requires API keys and network access).
   * *Recommendation:* Default to an OpenAI-compatible interface pattern that supports Ollama locally by default, but allows configuring external endpoint URLs and API keys via environment variables.

2. **Subjective Grading Tolerance Threshold:**
   * Free-text grading by LLMs can vary in strictness. Stakeholders must decide whether the system should offer binary scoring (full/no credit) or partial credit scoring with an uncertainty threshold that prompts the student to self-assess borderline answers.
   * *Recommendation:* Implement a 3-tier rubric (Correct, Partially Correct, Incorrect) with mandatory explanatory feedback.

3. **Plagiarism Similarity Cutoff:**
   * Stakeholders must approve the similarity threshold for rejecting generated questions.
   * *Recommendation:* Set a strict maximum cosine similarity threshold of 0.70 against source exam questions. Any question scoring higher must be regenerated.

4. **Multi-Media & Non-Text Content Scope:**
   * Engineering drawings, circuit schematics, and chemical structures cannot be parsed reliably by pure-text extractors.
   * *Recommendation:* For this pilot phase, explicitly restrict parsing to text-based questions, equations, and tables, returning a descriptive warning when complex raster graphics are detected. Vision-model integration should be deferred to a later milestone.