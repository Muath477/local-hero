"""Local file reading tool — text, markdown, PDF and docx, scoped to the
project's data/uploads directory so an agent can never read arbitrary
paths on the host just because a prompt asked it to.

The frontend upload button accepts literally any file (no extension
gatekeeping — that's a UI decision, not this module's), so this has to
tell a genuinely unreadable file (an image, a zip, some binary blob) apart
from real text instead of silently feeding garbage bytes to the model.
"""
from pathlib import Path

from docx import Document
from pypdf import PdfReader

from .registry import tool

UPLOADS_DIR = Path(__file__).parent.parent / "data" / "uploads"


@tool("Read the text content of a file the user uploaded (txt, md, pdf, or docx). Pass just the filename.")
def read_uploaded_file(filename: str) -> str:
    path = (UPLOADS_DIR / filename).resolve()
    if UPLOADS_DIR.resolve() not in path.parents and path != UPLOADS_DIR.resolve():
        return "Error: access outside the uploads directory is not allowed."
    if not path.exists():
        return f"Error: '{filename}' not found in uploads."

    suffix = path.suffix.lower()
    try:
        if suffix == ".pdf":
            reader = PdfReader(str(path))
            return "\n".join(page.extract_text() or "" for page in reader.pages)
        if suffix == ".docx":
            doc = Document(str(path))
            return "\n".join(p.text for p in doc.paragraphs)
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return (
            f"Error: '{filename}' doesn't look like a readable text format "
            "(binary content) — supported types are txt/md/pdf/docx."
        )
    except Exception as e:
        return f"Error reading '{filename}': {e}"
