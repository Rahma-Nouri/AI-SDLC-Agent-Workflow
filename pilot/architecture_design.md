# Technical Architecture Document: AI-Powered Adaptive Exam Preparation System

---

## 1. System Overview

The **AI-Powered Adaptive Exam Preparation System** is a modular web application that enables university students to upload course materials and example exams, automatically parse and analyze their structure, and generate synthetic practice exams and study artifacts (summaries, key points, standalone drill questions). Additionally, it provides an interactive execution environment that evaluates student responses, diagnoses topic readiness, and adaptively tailors future exams to address individual knowledge gaps.

### Primary Responsibilities
1. **Document Ingestion & Extraction**: Ingest and extract sanitized plain text from diverse file formats (PDFs, slide decks, plain text notes, past exams).
2. **Pedagogical Structure Analysis**: Parse sample exams to extract a structural "blueprint" (distribution of topics, question formats, cognitive complexity, and difficulty rating).
3. **Synthetic Generation**: Synthesize non-duplicate, pedagogically aligned practice questions and exams matching a reference blueprint, strictly grounded in uploaded source materials.
4. **Execution & Evaluation**: Provide an interface for exam taking, evaluate both objective (e.g., multiple choice) and subjective (e.g., short answer) responses, and return contextual explanations.
5. **Study Material Augmentation**: Extract key definitions, formulas, and topic-level summaries from broader course literature.
6. **Adaptive Analytics & Personalization**: Maintain a longitudinal record of student mastery per topic and calibrate future exam generation toward weaker areas.

---

## 2. Architecture Style

### Recommended Architecture: **Modular Monolith with Pluggable AI Service Layer**

```
                  +----------------------------------------------+
                  |         Client Layer (Single Page App)       |
                  +----------------------------------------------+
                                         |  HTTP / REST
                                         v
+-----------------------------------------------------------------------------------+
|                            FastAPI Modular Monolith                               |
|                                                                                   |
|  +--------------------+  +-----------------------+  +--------------------------+  |
|  |  Ingestion Module  |  |  Analysis Module      |  |  Exam Generation Engine  |  |
|  |  (PDF / Text)      |  |  (Blueprint Extractor)|  |  (Exam & Question Synth) |  |
|  +--------------------+  +-----------------------+  +--------------------------+  |
|  +--------------------+  +-----------------------+  +--------------------------+  |
|  |  Study Synthesis   |  |  Evaluation Engine    |  |  Adaptive Analytics     |  |
|  |  (Summaries, Keys) |  |  (Scoring & Feedback) |  |  (Mastery & Profiling)   |  |
|  +--------------------+  +-----------------------+  +--------------------------+  |
|                                                                                   |
|  +-----------------------------------------------------------------------------+  |
|  |                           Pluggable LLM Gateway                             |  |
|  |         (Prompt Templating, Schema Validation via Pydantic / Instructor)     |  |
|  +-----------------------------------------------------------------------------+  |
+-----------------------------------------------------------------------------------+
           |                                                      |
           v                                                      v
+-----------------------+                              +-----------------------+
|  Persistence Layer    |                              |   AI Inference Host   |
|  - SQLite / Postgres  |                              |  - Ollama (Local) OR  |
|  - Local File Storage |                              |  - OpenAI-Compatible  |
+-----------------------+                              +-----------------------+
```

### Justification for Pilot Project
1. **Low Operational Overhead**: A microservices architecture introduces network latency, distributed state, complex container orchestration, and multi-pipeline maintenance that is disproportionate for a proof of concept (PoC). A modular monolith keeps development fast and deployment simple.
2. **Clear Domain Boundaries**: Business logic is separated into independent modules (Ingestion, Analysis, Generation, Evaluation, Analytics). If individual components (such as Document Processing or AI Generation) need horizontal scaling in the future, they can be extracted into distinct worker services with minimal refactoring.
3. **Pluggable AI Integration**: Isolating the AI interaction inside a dedicated Gateway abstraction allows the system to swap between zero-cost local LLMs (e.g., Ollama running open weights) and hosted commercial APIs without modifying domain business logic.

---

## 3. System Components

### 3.1 Client Presentation Layer (Frontend)
* **Responsibilities**:
  * Upload management (drag-and-drop of exams and study materials with file-type validation).
  * Exam Taking Interface (form-based layout supporting single-choice, multiple-choice, and open-ended text answers).
  * Results & Feedback Dashboard (score display, per-question model answer comparisons, topic-readiness visualizers).
  * Study Hub (tabbed views for generated summaries, key points, and flash drills).

### 3.2 Ingestion & Document Processing Module
* **Responsibilities**:
  * Ingest raw files (`.pdf`, `.pptx`, `.txt`, `.md`).
  * Extract text and metadata while filtering non-informative content (headers, footers, page numbers, watermarks).
  * Chunk long study materials into semantically coherent text segments for downstream AI processing.

### 3.3 Exam Blueprint & Structure Analysis Engine
* **Responsibilities**:
  * Analyze raw sample exams to extract cognitive parameters: topic distribution, question formats (multiple choice, true/false, short answer, code snippet), point weights, and overall difficulty rating (e.g., Introductory, Intermediate, Advanced).
  * Produce a structured, machine-readable `ExamBlueprint` JSON document.

### 3.4 Synthetic Generation Engine
* **Responsibilities**:
  * Consume an `ExamBlueprint` and (optionally) associated study materials.
  * Formulate prompts enforcing structural equivalence without verbatim duplication of source questions.
  * Generate new exams adhering to the exact question counts, distributions, and difficulty targets.
  * Produce standalone drill questions categorized by topic.

### 3.5 Evaluation & Scoring Engine
* **Responsibilities**:
  * Deterministic grading for objective question types (Multiple Choice, True/False).
  * AI-assisted grading for subjective, short-answer questions against an automatically generated rubric / reference answer.
  * Calculate numerical scores, identify conceptual misconceptions, and generate remedial feedback per question.

### 3.6 Study Material Synthesis Engine
* **Responsibilities**:
  * Summarize large course documents into concise, chapter-level or topic-level overviews.
  * Extract itemized key points: definitions, formulas, rules, and core concepts.

### 3.7 Adaptive Analytics & Mastery Profiler
* **Responsibilities**:
  * Aggregate longitudinal student performance data across attempts.
  * Maintain topic-specific mastery scores (e.g., percentages of correct items per topic).
  * Compute adjustment weights for future exams (e.g., increase question count on topics with $<60\%$ accuracy; decrease count on topics with $>85\%$ accuracy).

### 3.8 Persistence & Storage Layer
* **Responsibilities**:
  * Store uploaded source documents, structured blueprints, generated exams, student submissions, and mastery records.
  * Provide simple relational querying for historical analytics.

---

## 4. Component Interfaces and APIs

All APIs follow RESTful conventions over JSON over HTTP.

### 4.1 Ingestion API

#### `POST /api/v1/documents/upload`
Uploads an example exam or study material file.
* **Content-Type**: `multipart/form-data`
* **Form Parameters**:
  * `file`: Binary file payload (`.pdf`, `.txt`, `.pptx`, `.md`)
  * `document_type`: Enum `["EXAMPLE_EXAM", "LECTURE_NOTES", "SLIDES", "QUESTION_LIST"]`
  * `title`: String (optional)

* **Response (`201 Created`)**:
```json
{
  "document_id": "doc_8f9c1a2e",
  "title": "Computer_Networks_Midterm_2023.pdf",
  "document_type": "EXAMPLE_EXAM",
  "extracted_character_count": 14250,
  "status": "PROCESSED",
  "created_at": "2025-05-10T14:30:00Z"
}
```

---

### 4.2 Blueprint & Generation API

#### `POST /api/v1/exams/analyze`
Generates an architectural blueprint from an uploaded exam document.
* **Request (`application/json`)**:
```json
{
  "document_id": "doc_8f9c1a2e"
}
```

* **Response (`200 OK`)**:
```json
{
  "blueprint_id": "bp_3d4e5f6a",
  "source_document_id": "doc_8f9c1a2e",
  "title": "Analysis of Midterm 2023",
  "estimated_difficulty": "INTERMEDIATE",
  "topics": [
    {"name": "OSI Model", "weight_percentage": 30},
    {"name": "TCP/UDP Mechanics", "weight_percentage": 40},
    {"name": "Subnetting", "weight_percentage": 30}
  ],
  "question_distribution": [
    {"type": "MULTIPLE_CHOICE", "count": 5, "points_per_question": 2},
    {"type": "SHORT_ANSWER", "count": 3, "points_per_question": 10}
  ],
  "total_estimated_duration_minutes": 60
}
```

#### `POST /api/v1/exams/generate`
Generates a new, non-identical practice exam based on a blueprint.
* **Request (`application/json`)**:
```json
{
  "blueprint_id": "bp_3d4e5f6a",
  "study_material_ids": ["doc_12ab34cd"],
  "is_adaptive": false
}
```

* **Response (`201 Created`)**:
```json
{
  "exam_id": "exam_7a8b9c0d",
  "blueprint_id": "bp_3d4e5f6a",
  "title": "Practice Exam: Computer Networks",
  "questions": [
    {
      "question_id": "q_001",
      "order": 1,
      "type": "MULTIPLE_CHOICE",
      "topic": "TCP/UDP Mechanics",
      "prompt": "Which of the following flags is used to initiate a TCP 3-way handshake?",
      "options": ["ACK", "SYN", "FIN", "RST"],
      "points": 2
    },
    {
      "question_id": "q_002",
      "order": 2,
      "type": "SHORT_ANSWER",
      "topic": "Subnetting",
      "prompt": "Explain the difference between a /24 and /25 CIDR block in terms of usable host addresses.",
      "options": null,
      "points": 10
    }
  ]
}
```
*(Note: Correct answers and grading rubrics are stored on the server and omitted from the client response to prevent cheating).*

---

### 4.3 Exam Execution & Evaluation API

#### `POST /api/v1/exams/{exam_id}/submit`
Submits student answers for evaluation.
* **Request (`application/json`)**:
```json
{
  "submission_id": "sub_55667788",
  "answers": [
    {
      "question_id": "q_001",
      "student_answer": "SYN"
    },
    {
      "question_id": "q_002",
      "student_answer": "A /24 network provides 254 usable hosts, whereas /25 splits that into two subnets of 126 usable hosts each."
    }
  ]
}
```

* **Response (`200 OK`)**:
```json
{
  "submission_id": "sub_55667788",
  "exam_id": "exam_7a8b9c0d",
  "score_awarded": 12.0,
  "total_score_possible": 12.0,
  "percentage": 100.0,
  "evaluations": [
    {
      "question_id": "q_001",
      "is_correct": true,
      "score": 2.0,
      "feedback": "Correct. SYN is the synchronization packet used to initiate connection.",
      "model_answer": "SYN"
    },
    {
      "question_id": "q_002",
      "is_correct": true,
      "score": 10.0,
      "feedback": "Accurate explanation of host capacity calculations for both prefixes.",
      "model_answer": "A /24 network has 254 usable hosts (2^8 - 2). A /25 network has 126 usable hosts (2^7 - 2)."
    }
  ],
  "topic_readiness": [
    {"topic": "TCP/UDP Mechanics", "mastery_percentage": 100.0, "status": "MASTERED"},
    {"topic": "Subnetting", "mastery_percentage": 100.0, "status": "MASTERED"}
  ]
}
```

---

### 4.4 Study Material Synthesis API

#### `POST /api/v1/study-materials/{document_id}/synthesize`
Generates summaries and key points from an uploaded lecture document.
* **Request (`application/json`)**:
```json
{
  "include_summary": true,
  "include_key_points": true
}
```

* **Response (`200 OK`)**:
```json
{
  "document_id": "doc_12ab34cd",
  "summary": "This lecture covers the Transport Layer, contrasting reliable connection-oriented TCP against best-effort connectionless UDP...",
  "key_points": [
    {
      "category": "Definition",
      "term": "Three-Way Handshake",
      "description": "Process used by TCP to establish a connection using SYN, SYN-ACK, and ACK packets."
    },
    {
      "category": "Formula",
      "term": "Usable IPv4 Hosts",
      "description": "2^(32 - prefix_length) - 2"
    }
  ]
}
```

---

### 4.5 Assumptions Regarding Interfaces
* *Assumption 4.5.1*: For the pilot PoC, requests are processed synchronously. If document parsing or exam generation exceeds 20 seconds on low-resource environments, the API should transition to a background task model (`202 Accepted` with polling at `/api/v1/tasks/{task_id}`).
* *Assumption 4.5.2*: Standard student session identity can be maintained via a simple `X-Student-Id` header during the initial pilot phase before a complete multi-user authentication mechanism is mandated.

---

## 5. Data Design

The persistence tier relies on a clean relational model to guarantee strong data integrity for exam structure, answers, scoring, and analytics.

### 5.1 Entity Relationship Diagram

```
+--------------------+            1:N            +---------------------+
|      Document      |-------------------------->|    ExamBlueprint    |
|--------------------|                           |---------------------|
| id (PK)            |                           | id (PK)             |
| title              |                           | source_doc_id (FK)  |
| document_type      |                           | topics_json         |
| raw_content_path   |                           | distribution_json   |
| cleaned_text       |                           | difficulty_rating   |
+--------------------+                           +---------------------+
          |                                                 |
          | 0..N:N                                          | 1:N
          v                                                 v
+--------------------+                           +---------------------+
| DocumentChunk      |                           |    GeneratedExam    |
|--------------------|                           |---------------------|
| id (PK)            |                           | id (PK)             |
| document_id (FK)   |                           | blueprint_id (FK)   |
| chunk_text         |                           | title               |
| token_count        |                           | is_adaptive_seed    |
+--------------------+                           +---------------------+
                                                            |
                                                            | 1:N
                                                            v
+--------------------+            1:N            +---------------------+
|   ExamSubmission   |<--------------------------|      Question       |
|--------------------|                           |---------------------|
| id (PK)            |                           | id (PK)             |
| exam_id (FK)       |                           | exam_id (FK)        |
| student_id         |                           | order_index         |
| total_score        |                           | type (MCQ, Open)    |
| submitted_at       |                           | prompt              |
+--------------------+                           | options_json        |
          |                                      | answer_key          |
          | 1:N                                  | rubric_notes        |
          v                                      | topic               |
+--------------------+                           | points              |
|  AnswerEvaluation  |                           +---------------------+
|--------------------|                                      |
| id (PK)            |                                      |
| submission_id (FK) |                                      |
| question_id (FK)   |--------------------------------------+
| student_answer     |
| score_awarded      |
| feedback_text      |
| is_correct         |
+--------------------+

+--------------------------+
|  StudentTopicMastery     |
|--------------------------|
| id (PK)                  |
| student_id               |
| topic                    |
| total_questions_attempted|
| total_questions_correct  |
| current_mastery_ratio    |
| updated_at               |
+--------------------------+
```

### 5.2 Persistence Justification
* **Relational Storage (SQLite / PostgreSQL)**: Structured relations are strictly required for calculating student metrics, tying specific question options to submissions, and generating topic-level report cards. No complex schema-less structures are needed.
* **Blob / File Storage**: Raw uploaded files (`.pdf`, `.pptx`) are kept on the local file system using deterministic file path references stored in the `Document` entity, avoiding database bloat.

### 5.3 Unresolved Data Requirements
* **Data Retention Policy**: What is the lifecycle of uploaded student lecture materials? (Assumed: materials persist until explicitly cleared by the student or the PoC environment resets).
* **Multi-tenancy Scope**: Does the pilot require total data isolation across universities/courses, or is it a single-tenant local prototype? (Assumed: single-tenant, student-centric local tracking for PoC).

---

## 6. Technology Recommendations

To meet the constraint of a beginner-friendly proof of concept while keeping the stack strictly free and open source, the following technologies are selected:

| Layer | Recommended Choice | Rationale |
| :--- | :--- | :--- |
| **Backend Framework** | **Python (FastAPI)** | High performance, native asynchronous handling, automatic OpenAPI/Swagger documentation, and mature integration with document extraction and AI tooling. |
| **Frontend Framework** | **React + Vite (TypeScript)** | Industry-standard, fast hot-reloading for development, zero licensing fees, and rich component availability for interactive forms and visual dashboards. |
| **Relational Database** | **SQLite (via SQLAlchemy / SQLModel)** | Zero setup, zero infrastructure cost, embedded file-based storage. Perfect for local prototyping. Using SQLAlchemy/SQLModel enables seamless migration to PostgreSQL later with no code changes. |
| **Document Parsers** | **`pdfplumber` / `pypdf`** (PDFs), **`python-pptx`** (Slides) | Open-source, local execution, no external API costs, reliable extraction of text and basic tables. |
| **AI Inference Engine** | **Ollama** (Local inference with `Llama 3` or `Mistral 7B`) | Free, open-source, runs fully offline without subscription costs or API tokens. |
| **LLM Interface Layer** | **Instructor** or **LangChain Core** | Enables strict Pydantic JSON schema generation from open-weight models, ensuring zero malformed output when generating exam blueprints and questions. |

---

## 7. Mapping to Approved Tasks

| Task ID | Task Title | Architectural Component Responsible | Implementation Strategy & Notes |
| :--- | :--- | :--- | :--- |
| **TASK-001** | Technical Foundation & Baseline Architecture Setup | Client Presentation Layer, API Controller Layer, Pluggable LLM Gateway | Establish FastAPI repository structure, React client scaffold, SQLite database schema migrations, and Ollama connection health check. |
| **TASK-002** | Exam Ingestion & Document Processing Module | Ingestion & Document Processing Module | Implement `/api/v1/documents/upload` using `pdfplumber` and `pypdf` to extract sanitized text. |
| **TASK-003** | Exam Structure & Difficulty Analysis Engine | Exam Blueprint & Structure Analysis Engine | Define Pydantic schema for `ExamBlueprint`; implement structured prompting via LLM Gateway to output blueprint JSON. |
| **TASK-004** | Synthetic Practice Exam Generation Engine | Synthetic Generation Engine | Implement generative prompt taking the `ExamBlueprint` and producing non-verbatim questions; save to `GeneratedExam` and `Question` tables. |
| **TASK-005** | Exam Execution Interface | Client Presentation Layer (Frontend) | Develop interactive exam views in React allowing answer input for MCQ and free text, with state management and payload submission. |
| **TASK-006** | Answer Evaluation & Feedback Engine | Evaluation & Scoring Engine | Implement deterministic matching for MCQs and structured LLM evaluation prompts for short-text items; calculate point tallies. |
| **TASK-007** | Multi-Format Study Material Ingestion | Ingestion & Document Processing Module | Extend ingestion module to handle `.pptx` (via `python-pptx`) and multi-page reading materials with document chunking. |
| **TASK-008** | Summary & Key-Point Extraction Service | Study Material Synthesis Engine | Implement `/api/v1/study-materials/{id}/synthesize` using map-reduce or chunk-based extraction prompts for definitions and formulas. |
| **TASK-009** | Standalone Practice Question Generator | Synthetic Generation Engine | Expose an endpoint to request ad-hoc questions on specific topics directly from uploaded study notes without a full blueprint. |
| **TASK-010** | Topic Readiness Assessment | Adaptive Analytics & Mastery Profiler | Calculate topic mastery ratios based on `AnswerEvaluation` results; present colored visual tags (e.g., Mastered, Needs Revision) in the UI. |
| **TASK-011** | Student Performance & History Tracking | Adaptive Analytics & Mastery Profiler, Persistence Layer | Implement `StudentTopicMastery` table updates whenever an exam is submitted; persist longitudinal records over multiple attempts. |
| **TASK-012** | Adaptive Exam Generation & Recommendation Engine | Synthetic Generation Engine, Adaptive Analytics | Adjust question generation prompt weights: dynamically skew question counts toward topics where `current_mastery_ratio < 0.6`. |

### Architectural Observations & Task Dependencies
1. **Implicit Dependency in TASK-011**: TASK-011 requires a `student_id` or user identity concept. For the PoC, the architect recommends using a local student profile session identifier so that multi-tenant user authentication does not block delivery.
2. **Overlap Between TASK-004 and TASK-009**: Both tasks generate questions. The generation logic must be modularized into a shared question generation service where TASK-004 invokes it in batch mode (full blueprint) and TASK-009 invokes it in ad-hoc mode (single topic/chunk).

---

## 8. Risks and Open Questions

### 8.1 Technical and Architectural Risks
1. **LLM Output Non-Determinism & Schema Violations**:
   * *Risk*: Open-source local models may occasionally return malformed JSON, breaking the generation pipeline.
   * *Mitigation*: Use structured output frameworks (`Instructor` with Pydantic) that enforce strict JSON validation and automatic retries on validation failure.
2. **Context Window Saturation with Large Lecture Decks**:
   * *Risk*: Uploading 100+ slide decks or 50-page textbooks will exceed LLM context windows or degrade parsing performance.
   * *Mitigation*: Introduce chunking and text-cleaning pipelines in TASK-007 to process documents in focused segments.
3. **Subjective Grading Fairness**:
   * *Risk*: AI evaluation of free-text student answers can be inconsistent or overly lenient/harsh.
   * *Mitigation*: The evaluation engine must output an explicit reference answer alongside its critique so the student can verify the grade.
4. **Hardware Constraints of Local Inference**:
   * *Risk*: Running an 8B parameter model locally requires sufficient GPU VRAM or RAM, which may slow down development on low-spec developer machines.
   * *Mitigation*: Ensure the LLM Gateway can switch to an external API (e.g., Groq free tier, OpenAI-compatible endpoint) via an environment variable (`LLM_PROVIDER=local|remote`).

### 8.2 Confirmed Requirements vs. Working Assumptions

| Item | Status | Description |
| :--- | :--- | :--- |
| **Synthetic Exam Novelty** | **Confirmed** | Generated exams must not simply copy original questions; they must follow the structure and difficulty while generating fresh content. |
| **Free / Open Source Bias** | **Confirmed** | The system must not mandate paid proprietary APIs for baseline demonstration. |
| **Multi-Turn Chat Dialog** | **Assumption** | The system operates on discrete batch actions (upload $\rightarrow$ analyze $\rightarrow$ generate $\rightarrow$ take $\rightarrow$ evaluate). An interactive conversational chatbot is out of scope for the pilot. |
| **User Authentication System**| **Assumption** | Complex user registration/login (OAuth/JWT) is deferred; a lightweight profile identifier is sufficient for TASK-011 historical tracking. |
| **Non-Text Content (Diagrams)**| **Assumption** | Charts, diagrams, and mathematical figures are treated as plain text or OCR descriptions for the PoC; direct image generation/vision parsing is omitted from the initial scope. |

---

## 9. Human Review Decisions

The following architectural decisions require human approval before developer agents begin implementation:

1. **AI Hosting Mode for Development**:
   * *Option A*: Enforce 100% local inference using Ollama (`llama3:8b` or `mistral:7b`). Zero monetary cost, but requires a development machine with at least 16GB RAM.
   * *Option B*: Standardize on an OpenAI-compatible interface with a configurable endpoint and API key, allowing the developer to choose between local Ollama or a remote API.
   * *Architect Recommendation*: **Option B** (provides maximum flexibility without forcing paid accounts).

2. **PoC User Isolation Strategy**:
   * *Option A*: Single-user mode (all data belongs to one local default student).
   * *Option B*: Lightweight profile selector (student enters a name or selects from a dropdown) to demonstrate TASK-011 history tracking without full password/auth overhead.
   * *Architect Recommendation*: **Option B**.

3. **Handling Formula & Complex Diagram Content in Lecture Slides**:
   * *Option A*: Pure text-based extraction (discard complex diagrams, focus on text notes and standard questions).
   * *Option B*: Include OCR / multimodal vision parsing libraries.
   * *Architect Recommendation*: **Option A** for the pilot to maintain simplicity and prevent dependency bloat. Vision processing can be scheduled as an enhancement.