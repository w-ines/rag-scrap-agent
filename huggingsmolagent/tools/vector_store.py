from typing import List, Optional, Dict, Any
import hashlib

from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.documents import Document
from langchain_community.vectorstores import SupabaseVectorStore
from langchain_ollama import OllamaEmbeddings
from huggingsmolagent.tools.supabase_store import supabase
from smolagents import tool
import os
from dotenv import load_dotenv
load_dotenv()

# Monkey patch for Supabase v2.23+ compatibility with LangChain
# The issue: LangChain expects query_builder.params.set() but Supabase v2.23+ uses .limit() directly
import langchain_community.vectorstores.supabase as lc_supabase

_original_similarity_search = lc_supabase.SupabaseVectorStore.similarity_search_by_vector_with_relevance_scores

def _patched_similarity_search(self, query, k=4, filter=None, postgrest_filter=None, score_threshold=None, **kwargs):
    """Patched version that uses .limit() instead of .params.set()"""
    # Build the filter if provided
    if filter:
        postgrest_filter = self._build_postgrest_filter(filter)
    
    # Call the RPC function
    match_documents_params = self.match_args(query, filter)
    query_builder = self._client.rpc(self.query_name, match_documents_params)
    
    # Apply postgrest filter if provided
    if postgrest_filter:
        # For Supabase v2.23+, we can't use .params.set()
        # Instead, we'll retrieve more results and filter in Python
        pass
    
    # Use .limit() instead of .params.set("limit", k)
    query_builder = query_builder.limit(k * 3 if postgrest_filter else k)
    
    res = query_builder.execute()
    
    # Build results
    match_result = [
        (
            Document(
                metadata=search.get("metadata", {}),
                page_content=search.get("content", ""),
            ),
            search.get("similarity", 0.0),
        )
        for search in res.data
        if search.get("content")
    ]
    
    # Apply score threshold if provided
    if score_threshold is not None:
        match_result = [
            (doc, score) for doc, score in match_result if score >= score_threshold
        ]
    
    return match_result[:k]

# Apply the monkey patch
lc_supabase.SupabaseVectorStore.similarity_search_by_vector_with_relevance_scores = _patched_similarity_search 

def chunk_documents(documents: List[Document], *, chunk_size: int = 1000, chunk_overlap: int = 200) -> List[Document]:
    splitter = RecursiveCharacterTextSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
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
    return numbered


def store_embeddings(
    chunks: List[Document],
    *,
    table_name: str = "documents",
    query_name: str = "match_documents",
    embedding_model: Optional[str] = None,
) -> int:
    model_name = (
        embedding_model
        or os.getenv("OLLAMA_EMBED_MODEL")
        or "mxbai-embed-large"
    )
    embeddings = OllamaEmbeddings(model=model_name)

    def _sanitize_text(text: str) -> str:
        if text is None:
            return ""
        # Remove NUL bytes that Postgres text cannot store
        text = text.replace("\x00", " ").replace("\u0000", " ")
        # Fix OCR spacing issues where spaces appear between characters
        import re
        # Pattern 1: Remove spaces within words that have excessive spacing
        text = re.sub(r'(?<=\w)\s+(?=\w(?:\s+\w){2,})', '', text)
        # Pattern 2: Fix remaining single-letter words followed by spaces
        text = re.sub(r'\b(\w)\s+(?=\w\b)', r'\1', text)
        return text.strip()

    # Sanitize chunk contents to avoid Postgres 22P05 (NUL byte) errors
    sanitized_chunks: List[Document] = []
    for doc in chunks:
        cleaned = _sanitize_text(doc.page_content)
        if cleaned:
            sanitized_chunks.append(Document(page_content=cleaned, metadata=doc.metadata))

    # Use from_documents to ensure the table/function are created if missing
    vector_store = SupabaseVectorStore.from_documents(
        documents=sanitized_chunks,
        embedding=embeddings,
        client=supabase,
        table_name=table_name,
        query_name=query_name,
    )

    # When using from_documents above, items are already inserted.
    # Return the number of chunks stored.
    return len(sanitized_chunks)


def compute_file_hash(content: bytes) -> str:
    """Compute SHA256 hash of file content for deduplication."""
    return hashlib.sha256(content).hexdigest()


def check_existing_document(file_hash: str, table_name: str = "documents") -> Optional[Dict[str, Any]]:
    """
    Check if a document with the given file hash already exists in the database.
    
    Args:
        file_hash: SHA256 hash of the file content
        table_name: Supabase table name
        
    Returns:
        Dict with doc_id and chunk count if exists, None otherwise
    """
    try:
        # Query for documents with this file_hash in metadata
        response = supabase.table(table_name).select("metadata").eq("metadata->>file_hash", file_hash).limit(1).execute()
        
        if response.data and len(response.data) > 0:
            metadata = response.data[0].get("metadata", {})
            doc_id = metadata.get("doc_id")
            
            if doc_id:
                # Count total chunks for this doc_id
                count_response = supabase.table(table_name).select("id", count="exact").eq("metadata->>doc_id", doc_id).execute()
                
                return {
                    "doc_id": doc_id,
                    "chunk_count": count_response.count or 0,
                    "filename": metadata.get("filename"),
                    "source": metadata.get("source")
                }
        
        return None
    except Exception as e:
        print(f"[check_existing_document] Error: {e}")
        return None


def delete_document_by_doc_id(doc_id: str, table_name: str = "documents") -> int:
    """
    Delete all chunks associated with a doc_id.
    
    Args:
        doc_id: Document ID to delete
        table_name: Supabase table name
        
    Returns:
        Number of chunks deleted
    """
    try:
        # Delete all rows with this doc_id
        response = supabase.table(table_name).delete().eq("metadata->>doc_id", doc_id).execute()
        deleted_count = len(response.data) if response.data else 0
        print(f"[delete_document_by_doc_id] Deleted {deleted_count} chunks for doc_id={doc_id}")
        return deleted_count
    except Exception as e:
        print(f"[delete_document_by_doc_id] Error: {e}")
        return 0


def index_documents(
    documents: List[Document],
    *,
    base_metadata: Optional[Dict[str, Any]] = None,
    chunk_size: int = 1000,
    chunk_overlap: int = 200,
    table_name: str = "documents",
    query_name: str = "match_documents",
    embedding_model: Optional[str] = None,
) -> int:
    base_metadata = base_metadata or {}

    # Attach base metadata to each page-level document
    enriched_docs: List[Document] = []
    for doc in documents:
        md = dict(base_metadata)
        md.update(doc.metadata or {})
        enriched_docs.append(Document(page_content=doc.page_content, metadata=md))

    chunks = chunk_documents(enriched_docs, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    stored = store_embeddings(
        chunks,
        table_name=table_name,
        query_name=query_name,
        embedding_model=embedding_model,
    )
    print("stored in vector store=", stored)
    return stored


@tool
def retrieve_knowledge(
    query: str,
    *,
    top_k: int = 5,
    table_name: str = "documents",
    query_name: str = "match_documents",
    embedding_model: Optional[str] = None,
    doc_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Retrieve relevant chunks from the Supabase vector store.

    Args:
        query: The search query to match against embeddings
        top_k: Number of results to return
        table_name: Supabase table name that stores vectors
        query_name: Supabase RPC function name for similarity search
        embedding_model: Optional override for the embedding model name
        doc_id: Optional document ID to filter results by specific document

    Returns:
        dict with keys: results (list), sources (list), context (str)
    """
    try:
        model_name = (
            embedding_model
            or os.getenv("OLLAMA_EMBED_MODEL")
            or "mxbai-embed-large"
        )
        embeddings = OllamaEmbeddings(model=model_name)

        # Try to initialize the vector store robustly across versions
        try:
            vector_store = SupabaseVectorStore(
                embedding=embeddings,
                client=supabase,
                table_name=table_name,
                query_name=query_name,
            )
        except Exception:
            # Fallback constructor signature order in some versions
            vector_store = SupabaseVectorStore(
                supabase, embeddings, table_name=table_name, query_name=query_name
            )

        # Perform similarity search
        # Note: similarity_search_with_score is not implemented in LangChain's SupabaseVectorStore
        # We use the patched similarity_search_by_vector_with_relevance_scores instead
        try:
            # Generate embedding for the query
            query_embedding = embeddings.embed_query(query)
            
            # Use the patched method directly to get scores
            docs_scores = vector_store.similarity_search_by_vector_with_relevance_scores(
                query_embedding, 
                k=top_k * 3 if doc_id else top_k
            )
            docs = [d for d, _ in docs_scores]
            scores = [float(s) for _, s in docs_scores]
            print(f"[retrieve_knowledge] Got {len(docs)} results with scores")
        except Exception as e:
            print(f"[retrieve_knowledge] similarity_search_with_relevance_scores failed: {e}")
            print(f"[retrieve_knowledge] Falling back to similarity_search without scores")
            docs = vector_store.similarity_search(query, k=top_k * 3 if doc_id else top_k)
            scores = []

        # DEBUG: Log search results
        print(f"[retrieve_knowledge] Query: '{query}' | Requested k={top_k * 3 if doc_id else top_k}")
        print(f"[retrieve_knowledge] Retrieved {len(docs)} documents from vector store")
        
        # Filter by doc_id if provided
        if doc_id:
            print(f"[retrieve_knowledge] Filtering by doc_id='{doc_id}'")
            # DEBUG: Show all doc_ids in results
            found_doc_ids = set()
            for doc in docs:
                if doc.metadata:
                    found_doc_ids.add(doc.metadata.get("doc_id", "NO_DOC_ID"))
            print(f"[retrieve_knowledge] Found doc_ids in results: {found_doc_ids}")
            
            filtered_docs = []
            filtered_scores = []
            for idx, doc in enumerate(docs):
                if doc.metadata and doc.metadata.get("doc_id") == doc_id:
                    filtered_docs.append(doc)
                    if idx < len(scores):
                        filtered_scores.append(scores[idx])
            
            print(f"[retrieve_knowledge] After filtering: {len(filtered_docs)} documents match doc_id")
            docs = filtered_docs[:top_k]
            scores = filtered_scores[:top_k]

        # Helper to normalize retrieved text (fix OCR spacing issues)
        def normalize_text(text: str) -> str:
            if not text:
                return ""
            import re
            # Fix OCR spacing issues where spaces appear between characters
            # Pattern 1: Remove spaces within words that have excessive spacing
            # e.g., "a g e n t" -> "agent", "c o n v e r s a t i o n" -> "conversation"
            text = re.sub(r'(?<=\w)\s+(?=\w(?:\s+\w){2,})', '', text)
            # Pattern 2: Fix remaining single-letter words followed by spaces
            text = re.sub(r'\b(\w)\s+(?=\w\b)', r'\1', text)
            return text.strip()

        results: List[Dict[str, Any]] = []
        sources: List[Dict[str, Any]] = []
        context_parts: List[str] = []

        for idx, doc in enumerate(docs):
            meta = doc.metadata or {}
            score_info = f" | score={scores[idx]:.4f}" if idx < len(scores) else ""
            
            # Normalize the content before returning
            normalized_content = normalize_text(doc.page_content)
            
            results.append(
                {
                    "content": normalized_content,
                    "metadata": meta,
                    "score": scores[idx] if idx < len(scores) else None,
                }
            )
            sources.append(
                {
                    "id": meta.get("doc_id") or meta.get("source") or meta.get("filename") or f"chunk-{idx}",
                    "filename": meta.get("filename"),
                    "source": meta.get("source"),
                    "chunk_index": meta.get("chunk_index"),
                    "score": scores[idx] if idx < len(scores) else None,
                }
            )
            context_parts.append(
                f"Source [{idx + 1}]{score_info}: {meta.get('filename') or meta.get('source') or meta.get('doc_id') or ''}\n{normalized_content}\n\n----------\n"
            )

        return {
            "results": results,
            "sources": sources,
            "context": "\n".join(context_parts),
            "instructions": "Cite sources inline as [1], [2], etc. for each used passage.",
        }
    except Exception as e:
        return {"error": str(e), "results": [], "sources": [], "context": ""}
