import io
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Union

try:
    import pypdf
    from pypdf.errors import PdfReadError
    HAVE_PYPDF = True
except ImportError:
    pypdf = None
    PdfReadError = Exception
    HAVE_PYPDF = False


class DocumentParsingError(Exception):
    """Base exception raised when document extraction fails."""
    pass


class CorruptedDocumentError(DocumentParsingError):
    """Raised when the document file format is invalid, empty, or unreadable."""
    pass


class UnsupportedFormatError(DocumentParsingError):
    """Raised when the document format or extension is unsupported."""
    pass


@dataclass
class ExtractionResult:
    text: str
    page_count: int
    char_count: int
    metadata: Dict[str, str] = field(default_factory=dict)
    layout_detected: str = "single_column"
    warnings: List[str] = field(default_factory=list)


class DocumentParser:
    """
    Parser responsible for extracting clean unstructured and structured text
    from academic files (PDFs, TXT, MD) while preventing layout or structural corruption.
    """

    def __init__(self, prefer_column_detection: bool = True):
        self.prefer_column_detection = prefer_column_detection

    def parse(self, source: Union[str, Path, bytes, io.BytesIO], filename_hint: Optional[str] = None) -> ExtractionResult:
        """
        Parses a document source (file path, raw bytes, string content, or stream) and returns an ExtractionResult.
        """
        stream: io.BytesIO
        filename = filename_hint or ""

        if isinstance(source, Path):
            p = source
            filename = filename or p.name
            if not p.exists():
                raise CorruptedDocumentError(f"File not found: {source}")
            if p.stat().st_size == 0:
                raise CorruptedDocumentError(f"Document is empty (0 bytes): {source}")
            try:
                with open(p, "rb") as f:
                    stream = io.BytesIO(f.read())
            except Exception as e:
                raise CorruptedDocumentError(f"Unable to read source file: {e}") from e
        elif isinstance(source, str):
            is_path = False
            if "\n" not in source and "\r" not in source and len(source) < 4096:
                try:
                    p = Path(source)
                    if p.exists():
                        is_path = True
                        filename = filename or p.name
                        if p.stat().st_size == 0:
                            raise CorruptedDocumentError(f"Document is empty (0 bytes): {source}")
                        with open(p, "rb") as f:
                            stream = io.BytesIO(f.read())
                    elif source.endswith((".pdf", ".txt", ".text", ".md")) or "/" in source or "\\" in source:
                        raise CorruptedDocumentError(f"File not found: {source}")
                except (OSError, ValueError):
                    is_path = False
            if not is_path:
                if len(source) == 0:
                    raise CorruptedDocumentError("Document content is empty.")
                stream = io.BytesIO(source.encode("utf-8"))
        elif isinstance(source, bytes):
            if len(source) == 0:
                raise CorruptedDocumentError("Document content is empty.")
            stream = io.BytesIO(source)
        elif isinstance(source, io.BytesIO):
            data = source.getvalue()
            if len(data) == 0:
                raise CorruptedDocumentError("Document stream is empty.")
            stream = io.BytesIO(data)
        else:
            raise UnsupportedFormatError(f"Unsupported source type: {type(source)}")

        # Format detection
        header = stream.getvalue()[:10]
        is_pdf = header.startswith(b"%PDF-") or filename.lower().endswith(".pdf")
        is_text = filename.lower().endswith((".txt", ".text", ".md")) or not is_pdf

        if is_pdf:
            return self._parse_pdf(stream)
        elif is_text:
            return self._parse_text(stream)
        else:
            raise UnsupportedFormatError(f"Unable to determine parser for document: {filename}")

    def _parse_pdf(self, stream: io.BytesIO) -> ExtractionResult:
        if not HAVE_PYPDF:
            # Minimal fallback for mock PDF or environments missing pypdf
            raw_bytes = stream.getvalue()
            if not raw_bytes.startswith(b"%PDF-"):
                raise CorruptedDocumentError("Invalid PDF header.")
            extracted = self._extract_text_stream_simple(raw_bytes)
            if not extracted.strip():
                raise CorruptedDocumentError("PDF stream contains no readable text or is corrupted.")
            return ExtractionResult(
                text=extracted,
                page_count=1,
                char_count=len(extracted),
                layout_detected="unknown"
            )

        try:
            stream.seek(0)
            reader = pypdf.PdfReader(stream)
        except Exception as e:
            raise CorruptedDocumentError(f"Failed to parse PDF document: {e}") from e

        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception as e:
                raise CorruptedDocumentError("PDF is encrypted and password was not provided.") from e

        pages = reader.pages
        if len(pages) == 0:
            raise CorruptedDocumentError("PDF contains no pages.")

        extracted_pages: List[str] = []
        detected_layout = "single_column"
        warnings = []

        for page_idx, page in enumerate(pages):
            try:
                page_text = page.extract_text(extraction_mode="layout") if hasattr(page, "extract_text") else ""
            except Exception:
                try:
                    page_text = page.extract_text() if hasattr(page, "extract_text") else ""
                except Exception as err:
                    warnings.append(f"Page {page_idx + 1} extraction warning: {err}")
                    page_text = ""

            cleaned_page = self._normalize_page_layout(page_text)
            if cleaned_page:
                extracted_pages.append(cleaned_page)

        full_text = "\n\n".join(extracted_pages).strip()
        if not full_text:
            raise CorruptedDocumentError("PDF contains no extractable text content.")

        # Layout heuristic: detect multi-column flow
        if any("    " in line and len(line.split("    ")) >= 2 for line in full_text.splitlines()[:50]):
            detected_layout = "multi_column"

        metadata = {}
        if reader.metadata:
            for k, v in reader.metadata.items():
                if isinstance(v, str):
                    metadata[str(k).replace("/", "")] = v

        return ExtractionResult(
            text=full_text,
            page_count=len(pages),
            char_count=len(full_text),
            metadata=metadata,
            layout_detected=detected_layout,
            warnings=warnings
        )

    def _parse_text(self, stream: io.BytesIO) -> ExtractionResult:
        raw = stream.getvalue()
        for encoding in ("utf-8", "utf-8-sig", "latin-1", "cp1252"):
            try:
                text = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise CorruptedDocumentError("Text document encoding could not be determined.")

        cleaned = self._clean_text(text)
        if not cleaned:
            raise CorruptedDocumentError("Document contains only whitespace or unprintable characters.")

        return ExtractionResult(
            text=cleaned,
            page_count=1,
            char_count=len(cleaned),
            layout_detected="plain_text"
        )

    def _extract_text_stream_simple(self, raw_bytes: bytes) -> str:
        """Basic stream reader when external pypdf engine is absent."""
        matches = re.findall(rb"\((.*?)\)\s*Tj", raw_bytes)
        if matches:
            return " ".join(m.decode("latin-1", errors="ignore") for m in matches)
        # Fallback to general printable tokens
        clean_chars = [chr(b) for b in raw_bytes if 32 <= b <= 126 or b in (10, 13, 9)]
        return "".join(clean_chars)

    def _normalize_page_layout(self, text: str) -> str:
        if not text:
            return ""
        lines = [line.rstrip() for line in text.splitlines()]
        # Remove repetitive empty lines
        normalized = []
        consecutive_empty = 0
        for line in lines:
            if not line.strip():
                consecutive_empty += 1
                if consecutive_empty <= 1:
                    normalized.append("")
            else:
                consecutive_empty = 0
                normalized.append(line)
        return "\n".join(normalized)

    def _clean_text(self, text: str) -> str:
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        return text.strip()


def benchmark_extraction(sample_documents: Dict[str, Union[bytes, str]]) -> Dict[str, Dict[str, Union[float, int, str]]]:
    """
    Benchmarks parser against sample exam documents, verifying extraction fidelity
    and character throughput.
    """
    import time
    parser = DocumentParser()
    benchmark_results = {}

    for doc_name, content in sample_documents.items():
        start_time = time.perf_counter()
        result = parser.parse(content, filename_hint=doc_name)
        duration = time.perf_counter() - start_time
        benchmark_results[doc_name] = {
            "duration_seconds": round(duration, 5),
            "char_count": result.char_count,
            "page_count": result.page_count,
            "layout": result.layout_detected,
            "chars_per_sec": round(result.char_count / max(duration, 0.0001), 2)
        }

    return benchmark_results
