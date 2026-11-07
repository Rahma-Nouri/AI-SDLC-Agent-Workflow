import json

OPENAPI_SPEC = {
    "openapi": "3.1.0",
    "info": {
        "title": "AI-Powered Adaptive Exam Preparation API",
        "version": "1.0.0",
        "description": "OpenAPI contract drafting endpoints for document processing, exam decomposition, generation, assessment, and diagnostics."
    },
    "paths": {
        "/api/v1/documents": {
            "post": {
                "summary": "Upload study material or exam paper",
                "requestBody": {
                    "required": True,
                    "content": {
                        "multipart/form-data": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "file": {"type": "string", "format": "binary"},
                                    "doc_type": {"type": "string", "enum": ["EXAM_SAMPLE", "LECTURE_NOTE", "SLIDE_DECK"]},
                                    "title": {"type": "string"}
                                },
                                "required": ["file", "doc_type", "title"]
                            }
                        }
                    }
                },
                "responses": {
                    "202": {
                        "description": "File accepted for processing",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "document_id": {"type": "string"},
                                        "status": {"type": "string"},
                                        "message": {"type": "string"}
                                    }
                                }
                            }
                        }
                    }
                }
            }
        },
        "/api/v1/documents/{document_id}": {
            "get": {
                "summary": "Retrieve document metadata and extraction status",
                "parameters": [
                    {
                        "name": "document_id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"}
                    }
                ],
                "responses": {
                    "200": {
                        "description": "Document details",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "document_id": {"type": "string"},
                                        "title": {"type": "string"},
                                        "doc_type": {"type": "string"},
                                        "status": {"type": "string"},
                                        "extracted_char_count": {"type": "integer"},
                                        "created_at": {"type": "string", "format": "date-time"}
                                    }
                                }
                            }
                        }
                    }
                }
            }
        },
        "/api/v1/exams/generate": {
            "post": {
                "summary": "Generate a practice exam",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "source_blueprint_id": {"type": "string"},
                                    "study_document_ids": {"type": "array", "items": {"type": "string"}},
                                    "adaptive_mode": {"type": "boolean"}
                                }
                            }
                        }
                    }
                },
                "responses": {
                    "202": {
                        "description": "Generation task queued",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "job_id": {"type": "string"}
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}

def get_openapi_specification() -> dict:
    return OPENAPI_SPEC
