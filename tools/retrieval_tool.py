"""
Retrieval tool for smolagent - wrapper around EmbeddingProcessor
"""

from smolagents import tool
from typing import Dict, Any

# Import the global embedding processor from main
# This will be set by main.py after initialization
_embedding_processor = None

def set_embedding_processor(processor):
    """Set the global embedding processor instance"""
    global _embedding_processor
    _embedding_processor = processor


@tool
def retrieve_knowledge(query: str, top_k: int = 5) -> Dict[str, Any]:
    """
    Retrieve relevant chunks from the knowledge base.
    
    Args:
        query: The search query to match against stored documents
        top_k: Number of results to return (default: 5)
        
    Returns:
        Dictionary with results, sources, and context from the knowledge base
    """
    if _embedding_processor is None:
        return {
            "error": "Knowledge base not initialized",
            "results": [],
            "sources": [],
            "context": ""
        }
    
    return _embedding_processor.retrieve_knowledge(query, top_k)
