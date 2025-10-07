from typing import List, Optional, Dict, Any

from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.schema import Document
from langchain_community.vectorstores import SupabaseVectorStore
from langchain_ollama import OllamaEmbeddings
from huggingsmolagent.tools.supabase_store import supabase
from smolagents import tool
import os
from dotenv import load_dotenv
load_dotenv() 

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
        return text.replace("\x00", " ").replace("\u0000", " ").strip()

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
) -> Dict[str, Any]:
    """
    Retrieve relevant chunks from the Supabase vector store.

    Args:
        query: The search query to match against embeddings
        top_k: Number of results to return
        table_name: Supabase table name that stores vectors
        query_name: Supabase RPC function name for similarity search
        embedding_model: Optional override for the embedding model name

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

        # Perform similarity search with scores when available
        try:
            docs_scores = vector_store.similarity_search_with_score(query, k=top_k)
            docs = [d for d, _ in docs_scores]
            scores = [float(s) for _, s in docs_scores]
        except Exception:
            docs = vector_store.similarity_search(query, k=top_k)
            scores = []

        results: List[Dict[str, Any]] = []
        sources: List[Dict[str, Any]] = []
        context_parts: List[str] = []

        for idx, doc in enumerate(docs):
            meta = doc.metadata or {}
            score_info = f" | score={scores[idx]:.4f}" if idx < len(scores) else ""
            results.append(
                {
                    "content": doc.page_content,
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
                f"Source [{idx + 1}]{score_info}: {meta.get('filename') or meta.get('source') or meta.get('doc_id') or ''}\n{doc.page_content}\n\n----------\n"
            )

        return {
            "results": results,
            "sources": sources,
            "context": "\n".join(context_parts),
            "instructions": "Cite sources inline as [1], [2], etc. for each used passage.",
        }
    except Exception as e:
        return {"error": str(e), "results": [], "sources": [], "context": ""}
