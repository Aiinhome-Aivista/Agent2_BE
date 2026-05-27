import fitz
from docx import Document

def extract_text(file_path: str) -> str:
    if file_path.endswith(".pdf"):
        text = ""
        doc = fitz.open(file_path)

        for page in doc:
            text += page.get_text()

        return text

    elif file_path.endswith(".docx"):
        doc = Document(file_path)

        return "\n".join([p.text for p in doc.paragraphs])

    else:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()