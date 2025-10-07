# huggingsmolagent/tools/search/generate_query.py

import logging
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

async def generate_query(messages: List[Dict[str, Any]], llm_model=None) -> str:
    """
    Génère une requête de recherche à partir des messages de conversation.
    
    Args:
        messages: Liste des messages de la conversation
        llm_model: Modèle LLM optionnel pour générer la requête
        
    Returns:
        Requête de recherche générée
    """
    # Si aucun message, retourne une chaîne vide
    if not messages:
        return ""
    
    # Extrait le dernier message (supposé être la requête de l'utilisateur)
    last_message = messages[-1]
    
    if isinstance(last_message, dict) and "content" in last_message:
        content = last_message["content"]
    elif isinstance(last_message, str):
        content = last_message
    else:
        content = str(last_message)
    
    # Option simple: utiliser directement le dernier message comme requête
    if not llm_model:
        return content
    
    # Option avancée: utiliser un LLM pour générer une meilleure requête
    try:
        prompt = f"""
        Based on the conversation history, generate a search query that would help find relevant information to answer the user's request. 
        The query should be concise, use relevant keywords, and exclude conversational language.
        
        Last user message: {content}
        
        Search query:
        """
        
        response = await llm_model.generate(prompt)
        query = response.strip()
        
        logger.info(f"Generated search query: {query}")
        return query
    except Exception as e:
        logger.error(f"Error generating search query: {str(e)}")
        # En cas d'erreur, retourne le contenu du dernier message
        return content