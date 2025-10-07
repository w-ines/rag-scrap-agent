import tempfile
from typing import List

from fastapi import UploadFile
from langchain_community.document_loaders import PyPDFLoader, PyMuPDFLoader
import fitz  # PyMuPDF for rendering
import pytesseract
from PIL import Image
from langchain.schema import Document


def _filter_nonempty(docs: List[Document]) -> List[Document]:
    return [d for d in docs if (d.page_content or "").strip()]


def parse_pdf(upload: UploadFile) -> List[Document]:
    # Persist UploadFile to a temporary file path because loaders expect a path
    with tempfile.NamedTemporaryFile(delete=True, suffix=".pdf") as tmp:
        upload.file.seek(0)
        tmp.write(upload.file.read())
        tmp.flush()

        # 1) Try PyPDFLoader (fast, pure-python)
        try:
            pypdf_docs = PyPDFLoader(tmp.name).load()
        except Exception as e:
            print("PyPDFLoader failed, will try PyMuPDFLoader:", e)
            pypdf_docs = []

        pypdf_docs = _filter_nonempty(pypdf_docs)
        if pypdf_docs:
            print("PDF parsed with PyPDFLoader pages:", len(pypdf_docs))
            return pypdf_docs

        # 2) Fallback to PyMuPDFLoader (better on scanned/complex PDFs if OCR text exists)
        try:
            pymupdf_docs = PyMuPDFLoader(tmp.name).load()
        except Exception as e:
            print("PyMuPDFLoader failed:", e)
            pymupdf_docs = []

        pymupdf_docs = _filter_nonempty(pymupdf_docs)
        if pymupdf_docs:
            print("PDF parsed with PyMuPDFLoader pages:", len(pymupdf_docs))
            return pymupdf_docs

        # 3) OCR fallback: render pages and run Tesseract
        print("No text detected; attempting OCR fallback...")
        ocr_docs: List[Document] = []
        try:
            doc = fitz.open(tmp.name)
            for page_index in range(len(doc)):
                page = doc.load_page(page_index)
                pix = page.get_pixmap(dpi=200)
                img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                text = pytesseract.image_to_string(img)
                text = (text or "").strip()
                if text:
                    ocr_docs.append(Document(page_content=text, metadata={"page": page_index}))
        except Exception as e:
            print("OCR fallback failed:", e)

        ocr_docs = _filter_nonempty(ocr_docs)
        print("PDF parsed with OCR pages:", len(ocr_docs))
        return ocr_docs