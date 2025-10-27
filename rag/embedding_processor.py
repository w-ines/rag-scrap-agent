"""
Embedding Processing module with Supabase storage + FAISS retrieval.
Follows the architecture pattern from process/retrieval_processor.py
Stores chunks in Supabase 'file_items' table, retrieves with FAISS in-memory.
"""

import os
import logging
from typing import List, Dict, Any, Optional
from langchain.schema import Document
from langchain_community.vectorstores import FAISS
from langchain_ollama import OllamaEmbeddings
from pydantic import BaseModel
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

# Configuration du logging avancé
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s'
)
logger = logging.getLogger(__name__)


class EmbeddingResult(BaseModel):
    """Result model for embedding operations"""
    success: bool
    chunks_stored: int = 0
    error: Optional[str] = None
    metadata: dict[str, Any] = {}


class RetrievalResult(BaseModel):
    """Result model for retrieval operations"""
    results: List[Dict[str, Any]] = []
    sources: List[Dict[str, Any]] = []
    context: str = ""
    instructions: str = ""
    error: Optional[str] = None


class EmbeddingProcessor:
    """
    Embedding processor with Supabase storage + FAISS retrieval.
    Stores chunks in Supabase 'file_items', retrieves with FAISS in-memory.
    """
    
    def __init__(
        self,
        embedding_model: Optional[str] = None
    ):
        """
        Initialize embedding processor
        
        Args:
            embedding_model: Ollama embedding model name
        """
        self.embedding_model = embedding_model or os.getenv("OLLAMA_EMBED_MODEL", "mxbai-embed-large")
        self.embeddings = OllamaEmbeddings(model=self.embedding_model)
        
        # Supabase connection
        supabase_url = os.getenv("SUPABASE_URL")
        supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY")
        
        if not supabase_url or not supabase_key:
            raise ValueError("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY in .env")
        
        self.supabase: Client = create_client(supabase_url, supabase_key)
        
        logger.info(f"🔢 EmbeddingProcessor initialized (model={self.embedding_model}, storage=Supabase)")

    def _sanitize_text(self, text: str) -> str:
        """Remove problematic characters for Postgres"""
        if text is None:
            return ""
        return text.replace("\x00", " ").replace("\u0000", " ").strip()

    def _load_index(self) -> bool:
        """
        Load FAISS index from disk if available
        
        Returns:
            True if loaded successfully, False otherwise
        """
        if self._faiss_index is None and os.path.exists(self.index_path):
            try:
                embeddings = self._get_embeddings()
                self._faiss_index = FAISS.load_local(
                    self.index_path,
                    embeddings,
                    allow_dangerous_deserialization=True
                )
                logger.info(f"✅ Loaded FAISS index from {self.index_path}")
                return True
            except Exception as e:
                logger.error(f"❌ Failed to load FAISS index: {str(e)}")
                return False
        return self._faiss_index is not None

    def _save_index(self) -> bool:
        """
        Save FAISS index to disk
        
        Returns:
            True if saved successfully, False otherwise
        """
        if self._faiss_index is not None:
            try:
                self._faiss_index.save_local(self.index_path)
                logger.info(f"💾 FAISS index saved to {self.index_path}")
                return True
            except Exception as e:
                logger.error(f"❌ Failed to save FAISS index: {str(e)}")
                return False
        return False

    def store_embeddings(
        self,
        chunks: List[Document],
        base_metadata: Optional[Dict[str, Any]] = None
    ) -> EmbeddingResult:
        """
        Store document chunks in Supabase 'file_items' table
        
        Args:
            chunks: List of Document chunks to store
            base_metadata: Base metadata (file_id, filename, doc_id, source)
            
        Returns:
            EmbeddingResult with success status and metadata
        """
        try:
            if not chunks:
                return EmbeddingResult(
                    success=False,
                    error="No chunks provided"
                )
            
            file_id = base_metadata.get("doc_id") if base_metadata else None
            if not file_id:
                return EmbeddingResult(
                    success=False,
                    error="Missing file_id (doc_id) in base_metadata"
                )
            
            logger.info(f"📥 Storing {len(chunks)} chunks in Supabase for file_id={file_id}")
            
            # Prepare chunks for Supabase insertion
            items_to_insert = []
            for idx, chunk in enumerate(chunks):
                sanitized_content = self._sanitize_text(chunk.page_content)
                
                # Estimate tokens (rough approximation: 1 token ≈ 4 chars)
                tokens = len(sanitized_content) // 4
                
                item = {
                    "file_id": file_id,
                    "content": sanitized_content,
                    "tokens": tokens,
                    "metadata": {
                        **chunk.metadata,
                        **(base_metadata or {}),
                        "chunk_index": idx
                    }
                }
                items_to_insert.append(item)
            
            # Insert into Supabase
            response = self.supabase.table("file_items").insert(items_to_insert).execute()
            
            if not response.data:
                raise Exception("Supabase insert returned no data")
            
            logger.info(f"✅ Successfully stored {len(items_to_insert)} chunks in Supabase")
            
            return EmbeddingResult(
                success=True,
                chunks_stored=len(items_to_insert),
                metadata={
                    "file_id": file_id,
                    "storage": "supabase",
                    "table": "file_items",
                    "total_chunks": len(items_to_insert)
                }
            )
            
        except Exception as e:
            logger.error(f"❌ Error storing chunks in Supabase: {str(e)}")
            return EmbeddingResult(
                success=False,
                error=f"Failed to store chunks: {str(e)}"
            )

    def retrieve_knowledge(
        self,
        query: str,
        top_k: int = 5,
        file_ids: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Retrieve relevant chunks from Supabase using FAISS similarity search
        
        Args:
            query: Search query
            top_k: Number of results to return
            file_ids: Optional list of file_ids to filter by
            
        Returns:
            Dictionary with results, sources, and formatted context
        """
        try:
            logger.info(f"🔍 Searching for: '{query}' (top_k={top_k})")
            
            # Fetch chunks from Supabase
            query_builder = self.supabase.table("file_items").select("*")
            
            if file_ids:
                query_builder = query_builder.in_("file_id", file_ids)
                logger.info(f"📂 Filtering by file_ids: {file_ids}")
            
            response = query_builder.execute()
            file_items = response.data
            
            if not file_items:
                logger.warning("⚠️  No chunks found in Supabase")
                return {
                    "error": "No documents indexed yet",
                    "results": [],
                    "sources": [],
                    "context": ""
                }
            
            logger.info(f"📦 Loaded {len(file_items)} chunks from Supabase")
            
            # Convert to Langchain Documents
            documents = []
            for item in file_items:
                doc = Document(
                    page_content=item.get('content', ''),
                    metadata={
                        'file_id': item.get('file_id'),
                        'chunk_id': item.get('id'),
                        'tokens': item.get('tokens'),
                        **item.get('metadata', {})
                    }
                )
                documents.append(doc)
            
            # Create FAISS index in-memory
            logger.info("🔢 Creating FAISS index from Supabase chunks...")
            vectorstore = FAISS.from_documents(documents, self.embeddings)
            
            # Perform similarity search
            docs_with_scores = vectorstore.similarity_search_with_score(
                query,
                k=min(top_k, len(documents))
            )
            
            if not docs_with_scores:
                logger.info("ℹ️  No results found")
                return {
                    "results": [],
                    "sources": [],
                    "context": "No relevant information found."
                }
            
            # Format results
            results = []
            sources = []
            context_parts = []
            
            for idx, (doc, score) in enumerate(docs_with_scores, 1):
                result = {
                    "content": doc.page_content,
                    "metadata": doc.metadata,
                    "score": float(score)
                }
                results.append(result)
                
                # Extract source info
                source_info = {
                    "id": doc.metadata.get("file_id", "unknown"),
                    "filename": doc.metadata.get("filename", "unknown"),
                    "source": doc.metadata.get("source", "unknown"),
                    "chunk_index": doc.metadata.get("chunk_index", idx - 1),
                    "score": float(score)
                }
                sources.append(source_info)
                
                # Build context string
                context_parts.append(
                    f"Source [{idx}] | score={score:.4f}: {doc.metadata.get('filename', 'unknown')}\n"
                    f"{doc.page_content}\n"
                )
            
            context = "\n----------\n\n".join(context_parts) + "\n----------\n"
            
            logger.info(f"✅ Found {len(results)} relevant chunks")
            
            return {
                "results": results,
                "sources": sources,
                "context": context,
                "instructions": "Cite sources inline as [1], [2], etc. for each used passage."
            }
            
        except Exception as e:
            logger.error(f"❌ Error retrieving knowledge: {str(e)}")
            return {
                "error": str(e),
                "results": [],
                "sources": [],
                "context": ""
            }

    def clear_index(self) -> bool:
        """
        Clear the FAISS index (both memory and disk)
        
        Returns:
            True if cleared successfully
        """
        try:
            self._faiss_index = None
            
            if os.path.exists(self.index_path):
                import shutil
                shutil.rmtree(self.index_path)
                logger.info(f"🗑️  Cleared FAISS index at {self.index_path}")
            
            return True
        except Exception as e:
            logger.error(f"❌ Failed to clear index: {str(e)}")
            return False

    def get_index_stats(self) -> Dict[str, Any]:
        """
        Get statistics about the current index
        
        Returns:
            Dictionary with index statistics
        """
        try:
            if self._faiss_index is None:
                self._load_index()
            
            if self._faiss_index is None:
                return {
                    "exists": False,
                    "total_vectors": 0
                }
            
            return {
                "exists": True,
                "total_vectors": self._faiss_index.index.ntotal,
                "model": self.model_name,
                "index_path": self.index_path
            }
        except Exception as e:
            logger.error(f"❌ Error getting index stats: {str(e)}")
            return {"error": str(e)}
