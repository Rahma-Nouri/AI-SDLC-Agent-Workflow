import io
import os
import tempfile
import unittest
from pathlib import Path
from pilot.app.backend.parser import (
    DocumentParser,
    CorruptedDocumentError,
    UnsupportedFormatError,
    benchmark_extraction
)


def create_mock_pdf_bytes(text_lines):
    """Creates a minimal valid PDF-1.4 file byte stream with text operators."""
    stream_content = "BT\n/F1 12 Tf\n"
    y = 750
    for line in text_lines:
        stream_content += f"1 0 0 1 50 {y} Tm\n({line}) Tj\n"
        y -= 20
    stream_content += "ET\n"
    stream_bytes = stream_content.encode("latin-1")
    stream_len = len(stream_bytes)

    pdf_template = (
        b"%PDF-1.4\n"
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 4 0 R >> >> /MediaBox [0 0 612 792] /Contents 5 0 R >>\nendobj\n"
        b"4 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n"
        b"5 0 obj\n<< /Length " + str(stream_len).encode("ascii") + b" >>\nstream\n" +
        stream_bytes +
        b"\nendstream\nendobj\n"
        b"xref\n0 6\n0000000000 65535 f \n0000000009 00000 n \n0000000056 00000 n \n0000000111 00000 n \n0000000233 00000 n \n0000000300 00000 n \n"
        b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n"
        + str(400 + stream_len).encode("ascii") +
        b"\n%%EOF\n"
    )
    return pdf_template


class TestDocumentParser(unittest.TestCase):
    def setUp(self):
        self.parser = DocumentParser()

    def test_plain_text_extraction(self):
        content = "Question 1: What is polymorphism?\nA) Many forms\nB) One form\n"
        result = self.parser.parse(content.encode("utf-8"), filename_hint="exam.txt")
        self.assertIn("Question 1: What is polymorphism?", result.text)
        self.assertEqual(result.layout_detected, "plain_text")
        self.assertGreater(result.char_count, 0)
        self.assertEqual(result.page_count, 1)

    def test_structured_pdf_extraction(self):
        lines = [
            "Midterm Exam 2025: CS101",
            "Question 1: Explain cache coherence.",
            "Question 2: What is virtual memory?"
        ]
        pdf_bytes = create_mock_pdf_bytes(lines)
        result = self.parser.parse(pdf_bytes, filename_hint="midterm.pdf")
        self.assertIn("Midterm Exam 2025: CS101", result.text)
        self.assertIn("Question 1: Explain cache coherence.", result.text)
        self.assertIn("Question 2: What is virtual memory?", result.text)
        self.assertEqual(result.page_count, 1)

    def test_multi_column_text_parsing(self):
        two_column_doc = (
            "Left Column Topic 1              Right Column Topic 2\n"
            "Section A: Fundamentals          Section B: Architecture\n"
            "Item 1: Instruction Set          Item 2: Pipelining Stages\n"
        )
        result = self.parser.parse(two_column_doc.encode("utf-8"), filename_hint="columns.txt")
        self.assertIn("Left Column Topic 1", result.text)
        self.assertIn("Right Column Topic 2", result.text)
        self.assertEqual(result.layout_detected, "plain_text")

    def test_corrupted_empty_document_raises_gracefully(self):
        with self.assertRaises(CorruptedDocumentError):
            self.parser.parse(b"", filename_hint="empty.pdf")

    def test_corrupted_binary_header_raises_gracefully(self):
        corrupted_content = b"%NOT_A_REAL_PDF_12345" + os.urandom(100)
        with self.assertRaises(CorruptedDocumentError):
            self.parser.parse(corrupted_content, filename_hint="corrupt.pdf")

    def test_nonexistent_file_raises_gracefully(self):
        with self.assertRaises(CorruptedDocumentError):
            self.parser.parse("/nonexistent/path/to/missing_exam.pdf")

    def test_benchmark_extraction(self):
        docs = {
            "exam_sample_1.txt": "Question 1: Define process scheduling.\nQuestion 2: Define thread.\n" * 10,
            "exam_sample_2.pdf": create_mock_pdf_bytes(["Exam Paper Part A", "Question 1: OS Kernels"])
        }
        benchmarks = benchmark_extraction(docs)
        self.assertIn("exam_sample_1.txt", benchmarks)
        self.assertIn("exam_sample_2.pdf", benchmarks)
        self.assertGreater(benchmarks["exam_sample_1.txt"]["chars_per_sec"], 0)
        self.assertGreater(benchmarks["exam_sample_2.pdf"]["chars_per_sec"], 0)


if __name__ == "__main__":
    unittest.main()
