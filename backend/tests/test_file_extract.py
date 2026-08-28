import pytest

from services.file_extract import validate_resume_file


def test_resume_file_validation_accepts_real_pdf_header():
    assert validate_resume_file("resume.pdf", b"notes\n%PDF-1.7\ncontent") == "application/pdf"


@pytest.mark.parametrize(
    ("filename", "content", "message"),
    [
        ("resume.pdf", b"not a pdf", "invalid PDF file"),
        ("resume.txt", b"hello\x00world", "invalid text file"),
        ("resume.exe", b"MZ", "unsupported file type"),
        ("resume.md", b"\xff\xfe", "text file must use UTF-8"),
    ],
)
def test_resume_file_validation_rejects_disguised_or_invalid_files(filename, content, message):
    with pytest.raises(ValueError, match=message):
        validate_resume_file(filename, content)
