import re
import unicodedata


def preprocess_text(text: str) -> str:
    if not text:
        return ""

    # Normalize Unicode and line endings
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # Strip HTML tags
    text = re.sub(r"<[^>]+>", "", text)

    # Remove control characters except newline and tab
    text = "".join(
        char for char in text
        if char in "\n\t" or unicodedata.category(char) != "Cc"
    )

    # Normalize horizontal whitespace
    text = re.sub(r"[ \t]+", " ", text)

    # Collapse excessive blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)

    # Trim each line while preserving structure
    text = "\n".join(line.strip() for line in text.splitlines())

    return text.strip()

