# huggingsmolagent/tools/search/endpoints.py

import os
import logging
from enum import Enum
from typing import List, Dict, Any, Optional
from duckduckgo_search import DDGS
import requests

logger = logging.getLogger(__name__)

class SearchProvider(Enum):
    DUCKDUCKGO = "duckduckgo"
    GOOGLE = "google"
    BING = "bing"
    CUSTOM = "custom"

def get_search_provider() -> str:
    """Retourne le fournisseur de recherche configuré dans les variables d'environnement."""
    provider = os.environ.get("SEARCH_PROVIDER", "duckduckgo").lower()
    return provider

def search_web(query: str, max_results: int = 8) -> List[Dict[str, Any]]:
    """
    Recherche sur le web en utilisant le fournisseur de recherche configuré.
    
    Args:
        query: La requête de recherche
        max_results: Nombre maximum de résultats à retourner
        
    Returns:
        Liste de résultats de recherche au format standardisé
    """
    provider = get_search_provider()
    
    if provider == SearchProvider.DUCKDUCKGO.value:
        return search_duckduckgo(query, max_results)
    elif provider == SearchProvider.GOOGLE.value:
        return search_google(query, max_results)
    elif provider == SearchProvider.BING.value:
        return search_bing(query, max_results)
    elif provider == SearchProvider.CUSTOM.value:
        return search_custom(query, max_results)
    else:
        logger.warning(f"Unsupported search provider: {provider}, falling back to DuckDuckGo")
        return search_duckduckgo(query, max_results)

def search_duckduckgo(query: str, max_results: int = 8) -> List[Dict[str, Any]]:
    """Recherche en utilisant DuckDuckGo."""
    try:
        results = []
        with DDGS() as ddgs:
            ddgs_results = ddgs.text(query, max_results=max_results)
            for r in ddgs_results:
                results.append({
                    "link": r.get("href", ""),
                    "title": r.get("title", ""),
                    "text": [r.get("body", "")],
                })
        return results
    except Exception as e:
        logger.error(f"DuckDuckGo search error: {str(e)}")
        return []

def search_google(query: str, max_results: int = 8) -> List[Dict[str, Any]]:
    """Recherche en utilisant l'API Google Custom Search."""
    try:
        api_key = os.environ.get("GOOGLE_API_KEY")
        cx = os.environ.get("GOOGLE_CSE_ID")
        
        if not api_key or not cx:
            logger.error("GOOGLE_API_KEY or GOOGLE_CSE_ID not set")
            return []
            
        url = f"https://www.googleapis.com/customsearch/v1?key={api_key}&cx={cx}&q={query}"
        response = requests.get(url)
        response.raise_for_status()
        
        data = response.json()
        results = []
        
        if "items" in data:
            for item in data["items"][:max_results]:
                results.append({
                    "link": item.get("link", ""),
                    "title": item.get("title", ""),
                    "text": [item.get("snippet", "")],
                })
                
        return results
    except Exception as e:
        logger.error(f"Google search error: {str(e)}")
        return []

def search_bing(query: str, max_results: int = 8) -> List[Dict[str, Any]]:
    """Recherche en utilisant l'API Bing Search."""
    try:
        api_key = os.environ.get("BING_API_KEY")
        
        if not api_key:
            logger.error("BING_API_KEY not set")
            return []
            
        url = "https://api.bing.microsoft.com/v7.0/search"
        headers = {"Ocp-Apim-Subscription-Key": api_key}
        params = {"q": query, "count": max_results, "responseFilter": "Webpages"}
        
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        
        data = response.json()
        results = []
        
        if "webPages" in data and "value" in data["webPages"]:
            for item in data["webPages"]["value"]:
                results.append({
                    "link": item.get("url", ""),
                    "title": item.get("name", ""),
                    "text": [item.get("snippet", "")],
                })
                
        return results
    except Exception as e:
        logger.error(f"Bing search error: {str(e)}")
        return []

def search_custom(query: str, max_results: int = 8) -> List[Dict[str, Any]]:
    """
    Implémentation d'un moteur de recherche personnalisé.
    Modifiez cette fonction pour intégrer votre propre API de recherche.
    """
    # Exemple d'implémentation à personnaliser
    try:
        custom_url = os.environ.get("CUSTOM_SEARCH_URL")
        custom_key = os.environ.get("CUSTOM_SEARCH_KEY")
        
        if not custom_url:
            logger.error("CUSTOM_SEARCH_URL not set")
            return []
            
        headers = {}
        if custom_key:
            headers["Authorization"] = f"Bearer {custom_key}"
            
        response = requests.get(
            custom_url,
            headers=headers,
            params={"query": query, "limit": max_results}
        )
        response.raise_for_status()
        
        data = response.json()
        # Adaptez cette partie à la structure de réponse de votre API
        results = []
        for item in data.get("results", [])[:max_results]:
            results.append({
                "link": item.get("url", ""),
                "title": item.get("title", ""),
                "text": [item.get("snippet", "")],
            })
            
        return results
    except Exception as e:
        logger.error(f"Custom search error: {str(e)}")
        return []