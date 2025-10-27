"""
RAG module - Provides a clean interface for document processing and retrieval.
Architecture follows the processor pattern from process/ modules.
"""

from .pdf_processor import PDFProcessor
from .embedding_processor import EmbeddingProcessor
from .storage_processor import StorageProcessor
from .summarizer_processor import SummarizerProcessor
from .qa_processor import QAProcessor

__all__ = [
    "PDFProcessor",
    "EmbeddingProcessor",
    "StorageProcessor",
    "SummarizerProcessor",
    "QAProcessor"
]
