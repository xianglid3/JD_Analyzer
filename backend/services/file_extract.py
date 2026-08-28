import io
from os.path import splitext

from pypdf import PdfReader


RESUME_MIME_TYPES = {
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".html": "text/html",
}


def validate_resume_file(filename, file_bytes):
    suffix = splitext(filename or "")[1].lower()

    if suffix not in RESUME_MIME_TYPES:
        raise ValueError("unsupported file type")
    if not file_bytes:
        raise ValueError("file is empty")

    if suffix == ".pdf":
        if b"%PDF-" not in file_bytes[:1024]:
            raise ValueError("invalid PDF file")
    else:
        try:
            text = file_bytes.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("text file must use UTF-8") from exc
        if "\x00" in text:
            raise ValueError("invalid text file")

    return RESUME_MIME_TYPES[suffix]


def extract_text_from_file(filename, file_bytes):
    """Pull plain text out of an uploaded resume file.

    PDFs go through pypdf; everything else (txt, md, html) is decoded as text,
    and any HTML tags get stripped later by preprocess_text.
    """
    name = (filename or "").lower()

    if name.endswith(".pdf"):
        reader = PdfReader(io.BytesIO(file_bytes))
        return "\n".join(page.extract_text() or "" for page in reader.pages[:20])  # cap pages (bomb guard)

    # txt / md / html / anything else → decode as UTF-8, ignore bad bytes
    return file_bytes.decode("utf-8", errors="ignore")
