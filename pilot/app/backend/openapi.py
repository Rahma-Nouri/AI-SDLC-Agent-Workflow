"""OpenAPI specification contract definitions for the adaptive exam system."""

OPENAPI_SPEC = {
    "openapi": "3.0.3",
    "info": {
        "title": "Adaptive Exam Preparation System API",
        "version": "1.0.0",
        "description": "API specifications defining service contracts for AI integration, document processing, and exam workflows."
    },
    "paths": {
        "/api/v1/documents": {
            "post": {
                "summary": "Upload study material or exam paper",
                "responses": {
                    "202": {"description": "File accepted for extraction."}
                }
            }
        },
        "/api/v1/documents/{document_id}": {
            "get": {
                "summary": "Get document metadata and status",
                "parameters": [
                    {"name": "document_id", "in": "path", "required": True, "schema": {"type": "string"}}
                ],
                "responses": {
                    "200": {"description": "Document details returned."},
                    "404": {"description": "Document not found."}
                }
            }
        },
        "/api/v1/documents/{document_id}/decompose": {
            "post": {
                "summary": "Trigger exam decomposition into pedagogical blueprint",
                "parameters": [
                    {"name": "document_id", "in": "path", "required": True, "schema": {"type": "string"}}
                ],
                "responses": {
                    "202": {"description": "Job created for decomposition."}
                }
            }
        },
        "/api/v1/jobs/{job_id}": {
            "get": {
                "summary": "Check status of long-running job",
                "parameters": [
                    {"name": "job_id", "in": "path", "required": True, "schema": {"type": "string"}}
                ],
                "responses": {
                    "200": {"description": "Job status retrieved."}
                }
            }
        },
        "/api/v1/exams/generate": {
            "post": {
                "summary": "Request practice exam generation",
                "responses": {
                    "202": {"description": "Job created for exam generation."}
                }
            }
        },
        "/api/v1/exams/{exam_id}": {
            "get": {
                "summary": "Get generated exam content",
                "parameters": [
                    {"name": "exam_id", "in": "path", "required": True, "schema": {"type": "string"}}
                ],
                "responses": {
                    "200": {"description": "Exam details returned."}
                }
            }
        }
    }
}
