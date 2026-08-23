import io
from pypdf import PdfReader


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
