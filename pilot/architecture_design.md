# System Architecture Document: AI-Powered Adaptive Exam Preparation System

## 1. System Overview

The AI-Powered Adaptive Exam Preparation System is an educational platform designed to transform student-supplied course materials, lecture presentations, and historic or sample examination papers into targeted, syllabus-aligned preparation resources. 

The primary responsibilities of the system encompass:
- **Document Ingestion & Text Extraction**: Ingesting unstructured academic files (PDFs, slide presentations, text documents) and extracting raw content while preserving logical reading order and metadata.
- **Structural and Pedagogical Analysis**: Deconstructing sample examinations into structured taxonomy profiles detailing topic distributions, question formats (multiple-choice, short answer, open response), difficulty tiers, and pedagogical styles.
- **Synthesized Practice Exam Generation**: Orchestrating Large Language Model (LLM) workflows to generate novel, non-verbatim mock exams and ad-hoc question sets strictly grounded in the ingested source materials and matching structural targets.
- **Execution & Automated Evaluation**: Providing an interactive assessment environment, deterministically scoring objective questions, and providing rubric-referenced, formative evaluations for open-ended answers.
- **Mastery Tracking & Adaptive Delivery**: Tracking longitudinal student performance by topic, calculating readiness diagnostics, and adapting downstream exam difficulty and topic weightings to target individual learner deficiencies.

---

## 2. Architecture Style

### Recommended Style: Modular Monolith with Asynchronous Background Job Processing

For this pilot project and proof-of-concept (PoC), a **Modular Monolith** paired with a lightweight **Asynchronous Task Queue** is recommended over a distributed microservices architecture.

```
+-------------------------------------------------------------------------+
|                        Client Application (SPA)                         |
|                   (Vue.js / React or Static Frontend)                   |
+-------------------------------------------------------------------------+
                                     |
                                REST / JSON
                                     v
+-------------------------------------------------------------------------+
|                             Modular Monolith                            |
|  +--------------------+  +--------------------+  +--------------------+ |
|  | Document Ingestion |  | Exam Decomposition |  | Question & Exam    | |
|  | Module             |  | Module             |  | Generation Module  | |
|  +--------------------+  +--------------------+  +--------------------+ |
|  +--------------------+  +--------------------+  +--------------------+ |
|  | Exam Execution &   |  | Study Synthesis    |  | Performance &      | |
|  | Assessment Module  |  | Module             |  | Diagnostics Module | |
|  +--------------------+  +--------------------+  +--------------------+ |
+-------------------------------------------------------------------------+
            |                                           |
      Task Dispatch                               SQL / Relational
            v                                           v
+-----------------------+                   +-----------------------+
| In-Memory Broker /    |                   | Relational Database   |
| Worker Process        |                   | (SQLite / PostgreSQL) |
| (Async Job Execution) |                   +-----------------------+
+-----------------------+                               |
            |                                     Blob Reference
            v                                           v
+-----------------------+                   +-----------------------+
| External LLM Service  |                   | Local File System /   |
| (Local or API via key)|                   | Object Store          |
+-----------------------+                   +-----------------------+
```

### Architectural Rationale
1. **Pilot Scope and Development Simplicity**: The project is a proof of concept. Microservices introduce premature operational complexity (distributed tracing, network partitions, multi-repo synchronization, independent CI/CD pipelines). A modular monolith maintains strict logical domain boundaries inside a single codebase while allowing straightforward deployment on a single container or server.
2. **Heavy I/O and Long-Running AI Operations**: Document parsing, topic extraction, and multi-question synthesis take between 5 to 60 seconds. A purely synchronous request-response architecture would lead to HTTP gateway timeouts (e.g., HTTP 504) and poor user experience. Decoupling long-running tasks via an asynchronous background worker within the monolith allows immediate user feedback through job status polling or Server-Sent Events (SSE).
3. **Low Operational Overhead**: A modular monolith can run during development with a local file-based database (such as SQLite) and an embedded or local task queue, minimizing setup hurdles for novice developers and automated testing pipelines.

---

## 3. System Components

The system is organized into six core backend modules, an asynchronous execution fabric, an persistence layer, and a client interface:

```
[ Web Client (SPA) ]
        |
        +---> [ API Gateway / Controller Layer ]
                     |
                     +---> [ Document Ingestion Module ]
                     +---> [ Exam Decomposition Module ]
                     +---> [ Generation Module ]
                     +---> [ Execution & Assessment Module ]
                     +---> [ Study Synthesis Module ]
                     +---> [ Performance & Diagnostics Module ]
                                    |
                     +--------------+--------------+
                     |                             |
             [ Worker Manager ]           [ Persistence Layer ]
                     |                             |
          +----------+----------+        (DB & File Storage)
          |                     |
   [ Parsers (OCR/PDF) ]  [ LLM Client Adapter ]
```

### 3.1. User Interface (UI) Component
- **Responsibilities**: Provides the student-facing single-page interface for document upload, exam generation configuration, interactive test-taking (with local answer caching), grading report visualization, and readiness dashboards.

### 3.2. Document Ingestion Module
- **Responsibilities**: Manages multipart file uploads for exam papers and study materials. Enforces file size and type validations, saves files to storage, and interfaces with parser libraries to extract raw textual streams and layout elements without character corruption.

### 3.3. Exam Decomposition Module
- **Responsibilities**: Analyzes extracted text from past exams using targeted prompts. Emits a structured pedagogical blueprint consisting of detected topics, question formats (e.g., multiple choice, short explanation, analytical essay), difficulty ratings, and point distributions.

### 3.4. Question & Exam Generation Module
- **Responsibilities**: Orchestrates the synthesis of practice questions based on either decomposed exam blueprints or uploaded lecture materials. Executes prompt generation pipelines, validates JSON output schemas, and executes an automated similarity check against source documents to prevent verbatim duplication.

### 3.5. Exam Execution & Assessment Module
- **Responsibilities**: Manages active test sessions, persists student responses, deterministically grades objective items (e.g., multiple-choice matching), and routes subjective answers to the LLM evaluator against rubrics and reference solutions.

### 3.6. Study Synthesis Module
- **Responsibilities**: Processes non-exam study materials (e.g., lecture slides, reading PDFs) to generate structured topical summaries, key definitions, core formulas, and extracted fact sheets strictly grounded in source texts.

### 3.7. Performance & Diagnostics Module
- **Responsibilities**: Maintains longitudinal mastery metrics per topic, computes readiness scores, detects knowledge gaps, and calculates parameter adjustments for adaptive exam generation.

### 3.8. Worker & External Gateway Adapters
- **Responsibilities**:
  - *Worker Fabric*: Consumes asynchronous tasks (parsing, generation, evaluation) to decouple compute loads from the web server thread pool.
  - *LLM Client Adapter*: Encapsulates token management, rate limiting, retry backoff with jitter, prompt formatting, and fallback handling for external/local AI model endpoints.

---

## 4. Component Interfaces and APIs

### 4.1. Communication Style
- **Synchronous Communication**: HTTP/REST with standard JSON payloads for configuration, data queries, exam state recording, and submission.
- **Asynchronous Communication**: Status endpoints polled via HTTP `GET` (or Server-Sent Events) to monitor long-running parsing and LLM generation jobs.

### 4.2. API Specifications

#### Ingestion Endpoints (Task T4, T10)
- `POST /api/v1/documents`
  - Uploads past exams, lecture notes, or slides.
  - *Request*: `multipart/form-data` with fields: `file` (binary), `doc_type` (Enum: `EXAM_SAMPLE`, `LECTURE_NOTE`, `SLIDE_DECK`), `title` (string).
  - *Response* (`202 Accepted`):
    ```json
    {
      "document_id": "doc_8f9c1e2b",
      "status": "PROCESSING",
      "message": "File accepted for extraction."
    }
    ```
- `GET /api/v1/documents/{document_id}`
  - *Response* (`200 OK`):
    ```json
    {
      "document_id": "doc_8f9c1e2b",
      "title": "CS101_Midterm_2023.pdf",
      "doc_type": "EXAM_SAMPLE",
      "status": "READY",
      "extracted_char_count": 14250,
      "created_at": "2025-10-12T10:00:00Z"
    }
    ```

#### Exam Decomposition & Generation Endpoints (Task T5, T6, T9, T15)
- `POST /api/v1/documents/{document_id}/decompose`
  - Triggers pedagogical blueprint extraction from an uploaded exam paper.
  - *Response* (`202 Accepted`): `{"job_id": "job_01a2b3c"}`
- `GET /api/v1/jobs/{job_id}`
  - Generic job status tracking endpoint.
  - *Response* (`200 OK`):
    ```json
    {
      "job_id": "job_01a2b3c",
      "status": "COMPLETED",
      "progress_percentage": 100,
      "result_id": "blueprint_44a1b2c",
      "error": null
    }
    ```
- `POST /api/v1/exams/generate`
  - Requests synthesis of a new practice exam.
  - *Request* (`application/json`):
    ```json
    {
      "source_blueprint_id": "blueprint_44a1b2c",
      "study_document_ids": ["doc_8f9c1e2b"],
      "adaptive_mode": true
    }
    ```
  - *Response* (`202 Accepted`): `{"job_id": "job_99x88y77"}`
- `GET /api/v1/exams/{exam_id}`
  - Retrieves the generated exam details without revealing correct answers during a student session.
  - *Response* (`200 OK`):
    ```json
    {
      "exam_id": "exam_77d8e9f",
      "title": "Practice Midterm 1: Computer Systems",
      "total_questions": 2,
      "questions": [
        {
          "question_id": "q_1",
          "sequence_order": 1,
          "question_type": "MULTIPLE_CHOICE",
          "prompt": "What is the primary function of the Program Counter register?",
          "options": ["Holds current instruction", "Holds address of next instruction", "Stores ALU flags", "Buffers I/O data"],
          "points": 5
        },
        {
          "question_id": "q_2",
          "sequence_order": 2,
          "question_type": "SHORT_ANSWER",
          "prompt": "Explain the difference between spatial and temporal locality.",
          "options": null,
          "points": 10
        }
      ]
    }
    ```

#### Exam Execution and Evaluation Endpoints (Task T7, T8)
- `POST /api/v1/exams/{exam_id}/sessions`
  - Initiates an active test-taking session.
  - *Response* (`201 Created`): `{"session_id": "sess_102938", "started_at": "2025-10-12T14:30:00Z"}`
- `PUT /api/v1/exams/sessions/{session_id}/answers`
  - Saves in-progress answers incrementally.
  - *Request* (`application/json`):
    ```json
    {
      "answers": [
        { "question_id": "q_1", "student_answer": "Holds address of next instruction" },
        { "question_id": "q_2", "student_answer": "Spatial means nearby memory items are accessed; temporal means the same item is accessed repeatedly." }
      ]
    }
    ```
  - *Response* (`200 OK`): `{"status": "SAVED", "last_saved_at": "2025-10-12T14:45:10Z"}`
- `POST /api/v1/exams/sessions/{session_id}/submit`
  - Finalizes session and triggers evaluation pipeline.
  - *Response* (`202 Accepted`): `{"job_id": "job_eval_4455"}`
- `GET /api/v1/exams/sessions/{session_id}/report`
  - Returns finalized grading and feedback.
  - *Response* (`200 OK`):
    ```json
    {
      "session_id": "sess_102938",
      "total_score": 15,
      "earned_score": 14,
      "evaluations": [
        {
          "question_id": "q_1",
          "score": 5,
          "max_score": 5,
          "is_correct": true,
          "feedback": "Correct. The PC register tracks the address of the subsequent instruction."
        },
        {
          "question_id": "q_2",
          "score": 9,
          "max_score": 10,
          "is_correct": true,
          "feedback": "Strong answer. Correctly differentiated spatial and temporal locality, but lacked explicit mention of cache line implications."
        }
      ]
    }
    ```

#### Study Synthesis, Ad-hoc Generation, and Diagnostics (Task T11, T12, T13, T16)
- `POST /api/v1/study/summarize`
  - *Request*: `{"document_id": "doc_8f9c1e2b", "focus_topics": ["Paging", "Virtual Memory"]}`
  - *Response* (`200 OK`):
    ```json
    {
      "summary": "Paging is a memory management scheme...",
      "key_points": ["Virtual addresses are divided into page number and offset.", "Page table maps virtual pages to physical frames."],
      "formulas": ["Physical Address = (Frame Number * Frame Size) + Offset"]
    }
    ```
- `GET /api/v1/diagnostics/readiness`
  - Retrieves student mastery dashboard metrics.
  - *Response* (`200 OK`):
    ```json
    {
      "overall_readiness_percentage": 78,
      "topic_mastery": [
        { "topic": "Virtual Memory", "mastery_score": 0.85, "status": "MASTERED" },
        { "topic": "Cache Architecture", "mastery_score": 0.45, "status": "NEEDS_REMEDIATION" }
      ],
      "recommendations": [
        {
          "topic": "Cache Architecture",
          "suggested_action": "Review summary and attempt ad-hoc practice questions.",
          "reference_document_id": "doc_8f9c1e2b"
        }
      ]
    }
    ```

---

## 5. Data Design

### 5.1. Entity-Relationship Model (Logical Overview)

```
+---------------+        1:N        +-----------------------+
|     User      |-------------------|       Document        |
+---------------+                   +-----------------------+
        |                                       |
        | 1:N                                   | 1:1
        v                                       v
+---------------+        1:N        +-----------------------+
|  ExamSession  |                   |     ExamBlueprint     |
+---------------+                   +-----------------------+
        ^                                       |
        | 1:N                                   | 1:N
        |                                       v
+---------------+        1:N        +-----------------------+
|  ExamAttempt  |-------------------|     GeneratedExam     |
|   Answer      |                   +-----------------------+
+---------------+                               |
        ^                                       | 1:N
        |                                       v
        +-----------------------------------+ Question |
                                            +-----------------------+
                                                        |
                                                        | N:1
                                                        v
                                            +-----------------------+
                                            |         Topic         |
                                            +-----------------------+
                                                        ^
                                                        | 1:N
                                            +-----------------------+
                                            | LongitudinalMastery   |
                                            +-----------------------+
```

### 5.2. Relational Schema Definitions

#### Table: `users`
- Stores user credentials and profile state.
- *Columns*:
  - `id` (UUID, Primary Key)
  - `email` (VARCHAR(255), Unique, Indexed)
  - `password_hash` (VARCHAR(255), Nullable for PoC single-user mode)
  - `created_at` (TIMESTAMP)

#### Table: `documents`
- Stores ingested files and parse results.
- *Columns*:
  - `id` (UUID, Primary Key)
  - `user_id` (UUID, Foreign Key -> `users.id`)
  - `title` (VARCHAR(255))
  - `doc_type` (VARCHAR(50): `EXAM_SAMPLE`, `LECTURE_NOTE`, `SLIDE_DECK`)
  - `storage_path` (VARCHAR(512): internal filesystem or blob URI)
  - `parsed_text` (TEXT: raw extracted clean text)
  - `status` (VARCHAR(30): `PENDING`, `PARSED`, `FAILED`)
  - `created_at` (TIMESTAMP)

#### Table: `exam_blueprints`
- Stores structural taxonomy extracted from sample exams.
- *Columns*:
  - `id` (UUID, Primary Key)
  - `source_document_id` (UUID, Foreign Key -> `documents.id`)
  - `blueprint_json` (JSON / TEXT: holds topic ratios, difficulty tiers, question taxonomies)
  - `created_at` (TIMESTAMP)

#### Table: `generated_exams`
- Represents a synthesized practice exam instance.
- *Columns*:
  - `id` (UUID, Primary Key)
  - `user_id` (UUID, Foreign Key -> `users.id`)
  - `source_blueprint_id` (UUID, Nullable, Foreign Key -> `exam_blueprints.id`)
  - `title` (VARCHAR(255))
  - `is_adaptive` (BOOLEAN)
  - `status` (VARCHAR(30): `GENERATING`, `READY`, `FAILED`)
  - `created_at` (TIMESTAMP)

#### Table: `questions`
- Individual items associated with a generated exam.
- *Columns*:
  - `id` (UUID, Primary Key)
  - `exam_id` (UUID, Foreign Key -> `generated_exams.id`)
  - `topic_id` (VARCHAR(100), Indexed)
  - `question_type` (VARCHAR(30): `MULTIPLE_CHOICE`, `SHORT_ANSWER`, `ESSAY`)
  - `difficulty` (VARCHAR(20): `BASIC`, `INTERMEDIATE`, `ADVANCED`)
  - `prompt` (TEXT)
  - `options_json` (JSON / TEXT, Nullable: array of choices for multiple choice)
  - `expected_answer` (TEXT: reference answer or correct option key)
  - `grading_rubric` (TEXT, Nullable: guidelines for subjective evaluation)
  - `points` (INTEGER)
  - `similarity_score` (FLOAT: non-verbatim check metric against source)

#### Table: `exam_sessions`
- Represents a student's sitting of an exam.
- *Columns*:
  - `id` (UUID, Primary Key)
  - `exam_id` (UUID, Foreign Key -> `generated_exams.id`)
  - `user_id` (UUID, Foreign Key -> `users.id`)
  - `started_at` (TIMESTAMP)
  - `submitted_at` (TIMESTAMP, Nullable)
  - `total_score` (FLOAT, Nullable)
  - `max_possible_score` (FLOAT, Nullable)

#### Table: `exam_answers`
- Individual question responses submitted by students.
- *Columns*:
  - `id` (UUID, Primary Key)
  - `session_id` (UUID, Foreign Key -> `exam_sessions.id`)
  - `question_id` (UUID, Foreign Key -> `questions.id`)
  - `student_response` (TEXT)
  - `score_awarded` (FLOAT, Nullable)
  - `feedback_text` (TEXT, Nullable)
  - `graded_at` (TIMESTAMP, Nullable)

#### Table: `longitudinal_mastery`
- Tracks rolling proficiency metrics per user per topic.
- *Columns*:
  - `id` (UUID, Primary Key)
  - `user_id` (UUID, Foreign Key -> `users.id`)
  - `topic` (VARCHAR(100), Indexed)
  - `total_attempts` (INTEGER)
  - `successful_attempts` (INTEGER)
  - `mastery_rating` (FLOAT: range 0.00 to 1.00)
  - `last_assessed_at` (TIMESTAMP)
  - *Unique Constraint*: `(user_id, topic)`

### 5.3. Persistence Justifications & Unresolved Data Requirements
- **Justification**: A relational structure is required because the domain has clear relational links (User -> Document -> Blueprint -> Exam -> Questions -> Sessions -> Answers). Relational integrity guarantees that responses correspond strictly to immutable question revisions, and prevents orphaned records.
- **Unresolved Data Requirements (Assumptions flagged)**:
  - *Mathematical Formula Representation*: It is assumed that math equations in parsed documents will be rendered in LaTeX syntax. If source PDFs use rasterized images for equations, plain text parsing will lose content unless multimodal OCR is employed.
  - *Session Expiration / Resumption Policy*: The maximum persistence duration for an incomplete `exam_session` (e.g., student closes browser tab) is not defined; default assumption is an unclosed session remains open indefinitely until explicit submission or manual reset.

---

## 6. Technology Recommendations

| Category | Technology | Rationale & Suitability for PoC |
| :--- | :--- | :--- |
| **Backend Runtime** | **Python 3.11+ (FastAPI)** | High developer ergonomics, native async support, and broad compatibility with modern document processing and data engineering libraries. |
| **Relational Database** | **PostgreSQL (or SQLite for initial dev)** | SQLite provides zero-configuration local execution for early PoC tasks. PostgreSQL provides full JSONB support, strict ACID guarantees, and smooth scalability for staging without license fees. |
| **Document Parsing** | **`pypdf` & `pdfplumber` / `python-pptx`** | Free, open-source libraries that run locally without API charges. Handles multi-column layouts and text streams from exam PDFs and slide decks. |
| **Task Queue & Broker** | **Redis + RQ (Redis Queue) / Celery** (or in-process `BackgroundTasks` for basic dev) | Redis + RQ offers an open-source, lightweight queue to offload heavy LLM and PDF tasks without microservice complexity. |
| **Frontend Framework** | **Vue.js 3 or React (with Tailwind CSS)** | Simple, modern single-page framework allowing rapid prototyping of interactive exam forms and dashboards with zero recurring licensing costs. |
| **LLM Execution & Abstraction** | **LiteLLM / Instructor (Python)** | Open-source interface layer that standardizes API calls to multiple LLM backends (OpenAI, Anthropic, or local open-weights models via Ollama/vLLM) with enforced Pydantic JSON schemas. |
| **Text Similarity & De-duplication** | **`scikit-learn` (TF-IDF / Cosine Similarity)** | Free, local, fast algorithmic similarity verification that detects verbatim question copying without incurring external vector database or embedding API costs. |

---

## 7. Mapping to Approved Tasks

| Approved Task ID | Task Title | Supporting System Components | Analysis, Overlaps & Clarifications |
| :--- | :--- | :--- | :--- |
| **T1** | System Architecture and Interface Design | All Modules, Persistence Layer, API Gateway | Foundational architecture task. Establishes the contracts, database schemas, and data flow patterns described in this document. |
| **T2** | Document Parsing Spike and Framework Integration | Document Ingestion Module, Worker Process | *Clarification*: PDF layouts with multi-column questions or scanned image exams require distinct handling. If documents contain rasterized scans, a fallback OCR library (e.g., Tesseract) will be required. |
| **T3** | Runtime Environment and Secrets Configuration | API Gateway / Runtime Config, LLM Adapter | Manages environment files, prevents API key leakage, and sets up staging configs. Must establish privacy safeguards so student documents are not cached on public third-party LLM endpoints. |
| **T4** | Exam Material Ingestion Pipeline | Document Ingestion Module, Persistence Layer | Relies on parsing utilities from T2. Needs clear file size limits (e.g., max 25MB) to prevent buffer overflows during PoC ingestion. |
| **T5** | Structural and Pedagogical Decomposition Service | Exam Decomposition Module, LLM Client Adapter | *Dependency Notice*: Depends on T4. Prompts must return standardized JSON adhering to strict Pydantic models to avoid runtime parsing crashes. |
| **T6** | Novel Practice Question Generation Pipeline | Generation Module, LLM Adapter, Scikit-Learn Similarity Checker | *Potential Overlap*: Non-duplication checks must be run both against the source exam (T4/T5) and previously generated exams (T9). |
| **T7** | Practice Exam Execution Interface | UI Component, Execution & Assessment Module | Requires local client state preservation (e.g., browser `localStorage`) to prevent loss of student work during unexpected network disconnects. |
| **T8** | Automated Answer Evaluation and Formative Feedback Engine | Execution & Assessment Module, LLM Client Adapter | Objective questions are evaluated deterministically in Python; subjective questions invoke prompt-based rubric scoring. Fallbacks must exist for malformed student inputs. |
| **T9** | Iterative Practice Exam Generation Workflow | Generation Module, Persistence Layer, UI Component | *Clarification*: Must reuse the structural blueprint from T5 while sampling distinct question variations without re-parsing the original document. |
| **T10** | Multi-Format Study Material Ingestion Engine | Document Ingestion Module, Parser Framework | Expands T2 and T4 to include slide decks (`.pptx`) and text notes. Ingestion logic should reuse the base pipeline built in T4. |
| **T11** | Summarization and Key Point Extraction Engine | Study Synthesis Module, LLM Client Adapter | Strictly grounds generation in uploaded documents via zero-shot prompts to prevent external model hallucinations. |
| **T12** | Ad-Hoc Practice Question Generation | Generation Module, Study Synthesis Module | Merges functionality from T6 and T11. Allows arbitrary text selections from study materials to generate targeted micro-quizzes. |
| **T13** | Topic Mastery and Readiness Diagnostics Dashboard | Performance & Diagnostics Module, UI Component | *Dependency / Overlap*: Relies on aggregate calculations derived from T8 and T12. Read-heavy module; requires index optimization on topic scores. |
| **T14** | Longitudinal Student Performance Tracking Database | Persistence Layer (Table: `longitudinal_mastery`) | *Dependency Warning*: Approved dependencies show T1 and T13. However, T13 logically requires T14's database to query historical trends. T14 must be implemented concurrently with or before T13. |
| **T15** | Adaptive Rebalancing and Dynamic Difficulty Engine | Generation Module, Performance & Diagnostics Module | Modulates prompt instructions for T6 based on mastery deficits recorded in T14. Adjusts topic ratios and shifts difficulty tiers. |
| **T16** | Automated Remediation Recommendation Engine | Performance & Diagnostics Module, Study Synthesis Module | Pairs diagnostic weaknesses identified in T13/T15 with direct anchor links to summary and key-point entities generated in T11. |
| **T17** | End-to-End System Hardening, Safety Verification, and Regression Testing | Entire System / Test Automation Harness | System-wide automated integration suites. Validates JSON schemas, prompt resilience against injection, and deterministic grading benchmarks. |

---

## 8. Risks and Open Questions

### 8.1. Confirmed Requirements vs. Architectural Assumptions

| Category | Item Description | Status |
| :--- | :--- | :--- |
| **Requirement** | Non-verbatim question synthesis matching topic distribution, question format, and difficulty. | Confirmed (Pilot Spec Section 3) |
| **Requirement** | Automated grading of objective questions and formative feedback for open-ended questions. | Confirmed (Pilot Spec Section 3 & Task T8) |
| **Requirement** | Dynamic difficulty and topic reallocation based on student historical performance. | Confirmed (Pilot Spec Section 5 & Task T15) |
| **Assumption** | Ingested PDF documents are modern, digital-native exports containing extractable text streams (not low-DPI flat scans). | Assumption (Needs validation in T2) |
| **Assumption** | System operates under a single-tenant or lightweight multi-tenant model without enterprise LMS integration (e.g., Canvas/Moodle LTI). | Assumption (Pending human review) |
| **Assumption** | Evaluation of subjective answers does not require immediate sub-second responses; asynchronous evaluation completion within 5–15 seconds is acceptable. | Assumption (Drives async UI pattern) |

### 8.2. Key Technical Risks & Mitigations

1. **Hallucination and Ungrounded Content**:
   - *Risk*: LLMs generating factually incorrect questions or assessing subjective answers based on fabricated course concepts.
   - *Mitigation*: Prompts must use strict context injection ("grounding") containing extracted document snippets. Prompts must instruct the model: *"Use only the provided context. If an answer cannot be deduced from the material, output UNCERTAIN."*
2. **LLM Output Schema Inconsistency**:
   - *Risk*: Parsing errors when LLM responses return malformed JSON, markdown-wrapped JSON, or omitted fields.
   - *Mitigation*: Enforce structured JSON generation using schema-enforcement libraries (e.g., Pydantic with Instructor or native model JSON Schema response modes). Implement retries with validation error back-propagation.
3. **Question Duplication and Copyright**:
   - *Risk*: The generation model regurgitating identical questions from the professor's sample exam, violating academic integrity or pedagogical utility.
   - *Mitigation*: Run an automated TF-IDF cosine-similarity and n-gram overlap check against the source text. Any generated question with >75% lexical similarity is automatically rejected and re-prompted.
4. **Processing Latencies and Timeouts**:
   - *Risk*: Document parsing and batch question generation blocking HTTP connections.
   - *Mitigation*: Offload parsing and generation to the Redis task queue; the UI tracks progress via the polling endpoint (`/api/v1/jobs/{job_id}`).

---

## 9. Human Review Decisions

The following architectural decisions require review and formal approval by human stakeholders prior to the implementation of dependent tasks:

1. **Document Format & Scanned PDF Policy (Task T2, T4)**:
   - *Decision*: Will the system support flat raster scans or mobile phone camera photos of exams for the PoC?
   - *Recommendation*: Limit the pilot strictly to text-based digital PDFs, DOCX, and PPTX files. If OCR for photographic scans is required, Tesseract or an external OCR engine must be added, introducing operational overhead and potential text extraction inaccuracies.

2. **Choice of LLM Provider & Cost Model (Task T3, T6, T8)**:
   - *Decision*: Should the runtime integrate with external commercial LLM APIs (e.g., OpenAI, Anthropic) via API keys, or use locally hosted open-weights models (e.g., Ollama running Llama-3/Mistral)?
   - *Recommendation*: Use an abstraction library (LiteLLM) configured for a commercial API in early staging for higher generation fidelity, while supporting a local Ollama endpoint for developers running without paid API access.

3. **Task Dependency Realignment for T13 and T14 (Tasks T13, T14)**:
   - *Decision*: In the approved task list, T14 (Longitudinal Performance Tracking Database) lists T13 (Diagnostics Dashboard) as a dependency. From an engineering standpoint, the dashboard cannot display historical metrics until the underlying database and tracking repository exist.
   - *Recommendation*: Formalize an administrative clarification permitting T14's data persistence layer to be developed prior to or in lockstep with the visualization components of T13.

4. **Grading Strictness and Subjective Feedback Policy (Task T8)**:
   - *Decision*: Should subjective grading provide only qualitative formative commentary, or assign strict numerical point deductions?
   - *Recommendation*: Use rubric-based numerical ranges with clear qualitative justifications, and display an explicit UI disclaimer: *"AI feedback is advisory. Review model answers for authoritative validation."*