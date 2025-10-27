"""
PDF Processing module with validation, retry mechanisms, and chunking.
Follows the architecture pattern from process/pdf_processor.py
"""

import tempfile
import os
import gc
import logging
from typing import List, Dict, Any, Optional, Tuple
from fastapi import UploadFile
from langchain_community.document_loaders import PyPDFLoader, PyMuPDFLoader
from langchain.schema import Document
from langchain.text_splitter import RecursiveCharacterTextSplitter
from tenacity import retry, stop_after_attempt, wait_exponential
from pydantic import BaseModel
import fitz  # PyMuPDF
import pytesseract
from PIL import Image

# Configuration du logging avancé
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s'
)
logger = logging.getLogger(__name__)


class PDFProcessingResult(BaseModel):
    """Result model for PDF processing operations"""
    success: bool
    documents: List[Document] = []
    error: Optional[str] = None
    warnings: List[str] = []
    metadata: Dict[str, Any] = {}


class PDFValidationError(Exception):
    """Custom exception for PDF validation errors"""
    pass


class MemoryManager:
    """Memory management for PDF processing"""
    def __init__(self):
        self.temp_files = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.cleanup()
        gc.collect()

    def cleanup(self):
        """Clean up temporary files"""
        for file_path in self.temp_files:
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
                    logger.debug(f"✅ Cleaned up temporary file: {file_path}")
            except Exception as e:
                logger.warning(f"⚠️  Failed to clean up {file_path}: {str(e)}")
        self.temp_files = []

    def create_temp_file(self, content: bytes, suffix: str = '.pdf') -> str:
        """Create a temporary file and return its path"""
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        temp_file.write(content)
        temp_file.close()
        self.temp_files.append(temp_file.name)
        return temp_file.name


class PDFProcessor:
    """
    PDF processing with multiple fallback strategies and validation.
    Handles PDF parsing, validation, chunking, and error recovery.
    """
    
    def __init__(self, chunk_size: int = 1000, chunk_overlap: int = 200):
        """
        Initialize PDF processor
        
        Args:
            chunk_size: Size of text chunks
            chunk_overlap: Overlap between chunks
        """
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        logger.info(f"📄 PDFProcessor initialized (chunk_size={chunk_size}, overlap={chunk_overlap})")

    def _filter_nonempty(self, docs: List[Document]) -> List[Document]:
        """Filter out empty documents"""
        return [d for d in docs if (d.page_content or "").strip()]

    def validate_pdf_structure(self, pdf_content: bytes) -> Tuple[bool, Optional[str]]:
        """
        Validate PDF structure and integrity
        
        Args:
            pdf_content: Raw PDF bytes
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        try:
            import io
            import PyPDF2
            from PyPDF2.errors import DependencyError
            
            with io.BytesIO(pdf_content) as pdf_stream:
                # Check PDF signature
                signature = pdf_stream.read(5).decode('utf-8', errors='ignore')
                if not signature.startswith('%PDF-'):
                    return False, "Invalid PDF signature"

                # Validate with PyPDF2
                pdf_stream.seek(0)
                pdf_reader = PyPDF2.PdfReader(pdf_stream, strict=False)

                if len(pdf_reader.pages) == 0:
                    return False, "PDF contains no pages"

                try:
                    if pdf_reader.is_encrypted:
                        return False, "PDF is encrypted and requires decryption"
                except DependencyError:
                    return False, "PDF requires PyCryptodome library"

                return True, None

        except Exception as e:
            logger.error(f"❌ PDF validation error: {str(e)}")
            return False, f"PDF validation error: {str(e)}"

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        reraise=True
    )
    def _parse_with_pypdf(self, file_path: str) -> List[Document]:
        """Parse PDF with PyPDFLoader (with retry)"""
        try:
            docs = PyPDFLoader(file_path).load()
            return self._filter_nonempty(docs)
        except Exception as e:
            logger.error(f"❌ PyPDFLoader failed: {str(e)}")
            raise

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        reraise=True
    )
    def _parse_with_pymupdf(self, file_path: str) -> List[Document]:
        """Parse PDF with PyMuPDFLoader (with retry)"""
        try:
            docs = PyMuPDFLoader(file_path).load()
            return self._filter_nonempty(docs)
        except Exception as e:
            logger.error(f"❌ PyMuPDFLoader failed: {str(e)}")
            raise

    def _parse_with_ocr(self, file_path: str) -> List[Document]:
        """Parse PDF with OCR fallback (Tesseract)"""
        logger.info("🔍 Attempting OCR fallback...")
        ocr_docs: List[Document] = []
        
        try:
            doc = fitz.open(file_path)
            for page_index in range(len(doc)):
                page = doc.load_page(page_index)
                pix = page.get_pixmap(dpi=200)
                img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                text = pytesseract.image_to_string(img)
                text = (text or "").strip()
                
                if text:
                    ocr_docs.append(
                        Document(
                            page_content=text,
                            metadata={"page": page_index, "source": "ocr"}
                        )
                    )
        except Exception as e:
            logger.error(f"❌ OCR fallback failed: {str(e)}")
        
        return self._filter_nonempty(ocr_docs)

    def parse_pdf(self, upload: UploadFile) -> PDFProcessingResult:
        """
        Parse PDF with multiple fallback strategies:
        1. PyPDFLoader (fast, pure-python)
        2. PyMuPDFLoader (better on complex PDFs)
        3. OCR with Tesseract (for scanned documents)
        
        Args:
            upload: FastAPI UploadFile object
            
        Returns:
            PDFProcessingResult with documents and metadata
        """
        with MemoryManager() as memory_manager:
            try:
                # Read file content
                upload.file.seek(0)
                file_content = upload.file.read()
                
                logger.info(f"📄 Processing PDF: {upload.filename} ({len(file_content)} bytes)")
                
                # Validate PDF structure
                is_valid, validation_error = self.validate_pdf_structure(file_content)
                if not is_valid:
                    return PDFProcessingResult(
                        success=False,
                        error=f"PDF validation failed: {validation_error}"
                    )
                
                # Create temporary file
                pdf_path = memory_manager.create_temp_file(file_content)
                
                # Strategy 1: PyPDFLoader
                try:
                    pypdf_docs = self._parse_with_pypdf(pdf_path)
                    if pypdf_docs:
                        logger.info(f"✅ PDF parsed with PyPDFLoader: {len(pypdf_docs)} pages")
                        return PDFProcessingResult(
                            success=True,
                            documents=pypdf_docs,
                            metadata={
                                "parser": "PyPDFLoader",
                                "pages": len(pypdf_docs),
                                "filename": upload.filename
                            }
                        )
                except Exception as e:
                    logger.warning(f"⚠️  PyPDFLoader failed, trying PyMuPDFLoader: {e}")
                
                # Strategy 2: PyMuPDFLoader
                try:
                    pymupdf_docs = self._parse_with_pymupdf(pdf_path)
                    if pymupdf_docs:
                        logger.info(f"✅ PDF parsed with PyMuPDFLoader: {len(pymupdf_docs)} pages")
                        return PDFProcessingResult(
                            success=True,
                            documents=pymupdf_docs,
                            metadata={
                                "parser": "PyMuPDFLoader",
                                "pages": len(pymupdf_docs),
                                "filename": upload.filename
                            }
                        )
                except Exception as e:
                    logger.warning(f"⚠️  PyMuPDFLoader failed, trying OCR: {e}")
                
                # Strategy 3: OCR fallback
                ocr_docs = self._parse_with_ocr(pdf_path)
                if ocr_docs:
                    logger.info(f"✅ PDF parsed with OCR: {len(ocr_docs)} pages")
                    return PDFProcessingResult(
                        success=True,
                        documents=ocr_docs,
                        metadata={
                            "parser": "OCR",
                            "pages": len(ocr_docs),
                            "filename": upload.filename
                        },
                        warnings=["Document was parsed using OCR (slower, may have errors)"]
                    )
                
                # All strategies failed
                return PDFProcessingResult(
                    success=False,
                    error="Failed to extract text from PDF with all available methods"
                )
                
            except Exception as e:
                logger.error(f"❌ PDF processing error: {str(e)}")
                return PDFProcessingResult(
                    success=False,
                    error=f"PDF processing failed: {str(e)}"
                )

    def chunk_documents(
        self,
        documents: List[Document],
        chunk_size: Optional[int] = None,
        chunk_overlap: Optional[int] = None
    ) -> List[Document]:
        """
        Chunk documents with overlap and metadata tracking
        
        Args:
            documents: List of documents to chunk
            chunk_size: Override default chunk size
            chunk_overlap: Override default chunk overlap
            
        Returns:
            List of chunked documents with metadata
        """
        chunk_size = chunk_size or self.chunk_size
        chunk_overlap = chunk_overlap or self.chunk_overlap
        
        logger.info(f"✂️  Chunking {len(documents)} documents (size={chunk_size}, overlap={chunk_overlap})")
        
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap
        )
        chunks = splitter.split_documents(documents)
        
        # Add chunk_index metadata for traceability
        numbered: List[Document] = []
        counter_by_doc: Dict[str, int] = {}
        
        for ch in chunks:
            base_id = (ch.metadata or {}).get("doc_id", "")
            idx = counter_by_doc.get(base_id, 0)
            meta = dict(ch.metadata or {})
            meta["chunk_index"] = idx
            numbered.append(Document(page_content=ch.page_content, metadata=meta))
            counter_by_doc[base_id] = idx + 1
        
        logger.info(f"✅ Created {len(numbered)} chunks")
        return numbered
