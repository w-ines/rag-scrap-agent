# smolagent/tools/search/endpoints.py

import os
import re
import logging
import requests
from enum import Enum
from typing import List, Dict, Any, Optional
from functools import lru_cache
from duckduckgo_search import DDGS
from langchain_ollama import ChatOllama
from langdetect import detect, LangDetectException

logger = logging.getLogger("search.endpoints")


# ============================================================================
# ENUM ET CONFIG
# ============================================================================

class SearchProvider(Enum):
    DUCKDUCKGO = "duckduckgo"
    GOOGLE = "google"
    BING = "bing"
    CUSTOM = "custom"


def get_search_provider() -> str:
    """Retourne le fournisseur de recherche configuré dans les variables d'environnement."""
    provider = os.environ.get("SEARCH_PROVIDER", "duckduckgo").lower()
    return provider


# ============================================================================
# OPTIMISATION DE REQUÊTE
# ============================================================================

def optimize_search_query(query: str) -> str:
    """
    Optimize search query using local LLM, with fallback and strict output control.
    """
    try:
        llm = ChatOllama(
            model="llama3:8b",
            base_url="http://127.0.0.1:11434",
            temperature=0
        )
        
        prompt = f"""Transform this into a search query. Output ONLY the query, nothing else.

Rules:
- English only
- Max 10 words
- Remove: what, is, the, a, an, were
- Keep: numbers, dates, names
- Fix typos

Examples:
Q: "whats the weather in tunisia" → weather Tunisia
Q: "last 5 football matches america" → last 5 football matches America
Q: "quelle météo paris" → weather Paris

Q: "{query}" →"""
        
        response = llm.invoke(prompt)
        optimized = response.content.strip().split('\n')[0].strip('"\' ')
        
        if ':' in optimized or '→' in optimized:
            optimized = re.split(r'[:→]', optimized)[-1].strip()

        # Validate
        words = optimized.split()
        if not optimized or len(words) < 2 or len(words) > 15:
            logger.warning(f"LLM output invalid ('{optimized}'), using fallback")
            raise ValueError("Invalid LLM output")

        logger.info(f"[LLM] Query optimized: '{query}' → '{optimized}'")
        return optimized

    except Exception as e:
        # Fallback minimal si LLM indisponible
        logger.warning(f"Optimization failed ({e}), applying basic fallback.")
        fallback = re.sub(r"\b(what|is|the|a|an|were)\b", "", query, flags=re.I)
        return fallback.strip()


# ============================================================================
# FILTRAGE DE DOMAINES NON ANGLAIS
# ============================================================================

def is_non_english_domain(url: str) -> bool:
    """Filter out non-English or irrelevant domains."""
    blocked_patterns = [
        r'\.(cn|ru|jp|kr)($|/)',                   # Country TLDs
        r'(baidu|weibo|qq|bilibili|zhihu|yandex|naver)',  # Sites
        r'[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]',     # Asian characters
    ]
    return any(re.search(pattern, url, re.IGNORECASE) for pattern in blocked_patterns)


# ============================================================================
# RECHERCHE GÉNÉRALE
# ============================================================================

def search_web(query: str, max_results: int = 8) -> List[Dict[str, Any]]:
    """Recherche sur le web en utilisant le fournisseur configuré."""
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
        logger.warning(f"Unsupported search provider: {provider}, fallback to DuckDuckGo")
        return search_duckduckgo(query, max_results)


# ============================================================================
# DUCKDUCKGO AVEC DETECTION DE LANGUE ET CACHE
# ============================================================================

@lru_cache(maxsize=128)
def search_duckduckgo(query: str, max_results: int = 8) -> List[Dict[str, Any]]:
    """Recherche DuckDuckGo avec optimisation, filtrage et détection de langue."""
    try:
        optimized_query = optimize_search_query(query)
        logger.info(f"[DuckDuckGo] Original: '{query}' → Optimized: '{optimized_query}'")

        # Détection automatique de langue pour ajuster la région
        try:
            lang = detect(query)
        except LangDetectException:
            lang = "en"

        region_map = {
            "en": "us-en",
            "fr": "fr-fr",
            "es": "es-es",
            "de": "de-de",
            "ar": "ar-xa",
        }
        region = region_map.get(lang, "us-en")
        logger.info(f"[DuckDuckGo] Region: {region} (detected lang='{lang}')")

        results = []
        with DDGS() as ddgs:
            ddgs_results = ddgs.text(
                optimized_query,
                max_results=max_results * 3,  # Fetch extra for filtering
                region=region,
                safesearch="moderate",
                backend="api",
            )

            filtered = 0
            for r in ddgs_results:
                if len(results) >= max_results:
                    break

                link = r.get("href", "")
                title = r.get("title", "")
                snippet = r.get("body", "")

                # Filtrage non-anglais
                if is_non_english_domain(link) or is_non_english_domain(title):
                    filtered += 1
                    continue

                if link and title:
                    results.append({
                        "link": link,
                        "title": title,
                        "text": [snippet],
                    })

            if filtered:
                logger.info(f"[DuckDuckGo] Filtered {filtered} non-English results")

        logger.info(f"[DuckDuckGo] {len(results)} clean results returned")
        return results

    except Exception as e:
        logger.error(f"[DuckDuckGo] Search error: {e}", exc_info=True)
        return []


# ============================================================================
# GOOGLE SEARCH
# ============================================================================

def search_google(query: str, max_results: int = 8) -> List[Dict[str, Any]]:
    """Recherche en utilisant Google Custom Search API."""
    try:
        api_key = os.environ.get("GOOGLE_API_KEY")
        cx = os.environ.get("GOOGLE_CSE_ID")
        
        if not api_key or not cx:
            logger.error("GOOGLE_API_KEY or GOOGLE_CSE_ID not set")
            return []
            
        url = f"https://www.googleapis.com/customsearch/v1"
        params = {"key": api_key, "cx": cx, "q": query}
        response = requests.get(url, params=params)
        response.raise_for_status()
        
        data = response.json()
        return [
            {
                "link": i.get("link", ""),
                "title": i.get("title", ""),
                "text": [i.get("snippet", "")]
            }
            for i in data.get("items", [])[:max_results]
        ]

    except Exception as e:
        logger.error(f"[Google] Search error: {e}")
        return []


# ============================================================================
# BING SEARCH
# ============================================================================

def search_bing(query: str, max_results: int = 8) -> List[Dict[str, Any]]:
    """Recherche en utilisant Bing Web Search API."""
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

        return [
            {
                "link": i.get("url", ""),
                "title": i.get("name", ""),
                "text": [i.get("snippet", "")]
            }
            for i in data.get("webPages", {}).get("value", [])
        ]

    except Exception as e:
        logger.error(f"[Bing] Search error: {e}")
        return []


# ============================================================================
# CUSTOM SEARCH API
# ============================================================================

def search_custom(query: str, max_results: int = 8) -> List[Dict[str, Any]]:
    """Recherche via moteur custom défini dans les variables d'environnement."""
    try:
        custom_url = os.environ.get("CUSTOM_SEARCH_URL")
        custom_key = os.environ.get("CUSTOM_SEARCH_KEY")
        if not custom_url:
            logger.error("CUSTOM_SEARCH_URL not set")
            return []
            
        headers = {}
        if custom_key:
            headers["Authorization"] = f"Bearer {custom_key}"
            
        response = requests.get(custom_url, headers=headers, params={"query": query, "limit": max_results})
        response.raise_for_status()
        
        data = response.json()
        return [
            {
                "link": i.get("url", ""),
                "title": i.get("title", ""),
                "text": [i.get("snippet", "")]
            }
            for i in data.get("results", [])[:max_results]
        ]

    except Exception as e:
        logger.error(f"[Custom] Search error: {e}")
        return []
