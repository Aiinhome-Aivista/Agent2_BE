import pandas as pd
from pypdf import PdfReader
from docx import Document


def extract_pdf_text(file_path: str):
    reader = PdfReader(file_path)

    text = ""

    for page in reader.pages:
        text += page.extract_text() or ""

    author = None

    try:
        author = reader.metadata.author
    except:
        pass

    return {
        "content": text,
        "author": author
    }



def extract_docx_text(file_path: str):
    doc = Document(file_path)

    text = "\n".join([
        para.text for para in doc.paragraphs
    ])

    author = None

    try:
        author = doc.core_properties.author
    except:
        pass

    return {
        "content": text,
        "author": author
    }



def extract_csv_text(file_path: str):
    df = pd.read_csv(file_path)

    text = df.to_string()

    return {
        "content": text,
        "author": None
    }



def extract_txt_text(file_path: str):
    with open(file_path, "r", encoding="utf-8") as f:
        text = f.read()

    return {
        "content": text,
        "author": None
    }