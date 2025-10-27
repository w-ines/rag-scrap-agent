import requests
from bs4 import BeautifulSoup
import logging
from smolagents import tool
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.service import Service
import time
import json
import random
from firecrawl import FirecrawlApp
import os
from dotenv import load_dotenv
from .search.endpoints import search_web

load_dotenv()
logger = logging.getLogger(__name__)

# Configuration simplifiée mais efficace
SCRAPING_CONFIG = {
    'firecrawl_timeout': 30,
    'requests_timeout': 20,
    'selenium_timeout': 25,
    'max_retries': 2,
}

@tool
def webscraper(url: str, css_selector: str = None, extraction_prompt: str = None, prefer_method: str = "auto") -> dict:
    """
    Scrapes a web page using multiple methods with intelligent fallback.
    
    Args:
        url (str): The URL of the webpage to scrape
        css_selector (str, optional): CSS selector to extract specific elements
        extraction_prompt (str, optional): Natural language instructions for Firecrawl
        prefer_method (str): "firecrawl", "beautifulsoup", "selenium", or "auto"
        
    Returns:
        dict: Structured data with content, metadata, and scraping info
    """
    logger.info(f"=== STARTING webscraper for URL: {url} ===")
    
    # Déterminer l'ordre de scraping
    methods = determine_scraping_order(url, extraction_prompt, prefer_method)
    
    last_error = None
    
    for method in methods:
        try:
            logger.info(f"Attempting with {method}")
            
            if method == "firecrawl":
                result = use_firecrawl_fixed(url, extraction_prompt)
            elif method == "beautifulsoup":
                result = use_beautifulsoup_enhanced(url, css_selector)
            elif method == "selenium":
                result = use_selenium_optimized(url, css_selector)
            else:
                continue
                
            if result and result.get('full_text'):
                result['scraping_method'] = method
                logger.info(f"✅ Success with {method}")
                return result
                
        except Exception as e:
            logger.warning(f"❌ {method} failed: {str(e)}")
            last_error = e
            continue
    
    # Si toutes les méthodes échouent
    return {
        'title': "",
        'full_text': f"Failed to scrape {url}: {str(last_error)}",
        'selected_elements': [],
        'articles': [],
        'extracted_data': {},
        'summary': f"All scraping methods failed for {url}",
        'scraping_method': 'failed',
        'error': str(last_error)
    }

def determine_scraping_order(url: str, extraction_prompt: str, prefer_method: str) -> list:
    """Détermine l'ordre optimal des méthodes de scraping."""
    
    if prefer_method != "auto":
        if prefer_method == "firecrawl":
            return ["firecrawl", "beautifulsoup", "selenium"]
        elif prefer_method == "beautifulsoup":
            return ["beautifulsoup", "selenium", "firecrawl"]
        elif prefer_method == "selenium":
            return ["selenium", "beautifulsoup", "firecrawl"]
    
    domain = url.split('/')[2].lower()
    
    # Sites nécessitant du JavaScript
    js_heavy_domains = ['twitter', 'facebook', 'instagram', 'spa', 'react', 'angular']
    needs_js = any(keyword in domain for keyword in js_heavy_domains)
    
    # Si on a des instructions spécifiques, préférer Firecrawl
    if extraction_prompt:
        return ["firecrawl", "selenium", "beautifulsoup"]
    
    # Si le site nécessite JS, commencer par Selenium
    if needs_js:
        return ["selenium", "firecrawl", "beautifulsoup"]
    
    # Par défaut: BeautifulSoup puis Firecrawl
    return ["beautifulsoup", "firecrawl", "selenium"]

def use_firecrawl_fixed(url: str, extraction_prompt: str = None) -> dict:
    """✅ Version corrigée de Firecrawl avec API v1."""
    try:
        api_key = os.getenv("FIRECRAWL_API_KEY")
        if not api_key:
            raise Exception("FIRECRAWL_API_KEY not found in environment variables")
        
        app = FirecrawlApp(api_key=api_key)
        
        if extraction_prompt:
            # ✅ Utilisation correcte pour l'extraction JSON
            result = app.scrape_url(
                url=url,
                formats=["json"],
                json_options={
                    "prompt": f"""
                    Extract the following information from this webpage:
                    {extraction_prompt}
                    
                    Please structure the response as JSON with clear field names.
                    """
                },
                only_main_content=True,
                timeout=SCRAPING_CONFIG['firecrawl_timeout'] * 1000,
                waitFor=5000
            )
            
            # Traiter la réponse d'extraction
            if hasattr(result, 'success') and not result.success:
                raise Exception(f"Firecrawl extraction failed: {getattr(result, 'error', 'Unknown error')}")
            
            data = result.data if hasattr(result, 'data') else result
            extracted_data = data.get('json', {}) if isinstance(data, dict) else {}
            content = json.dumps(extracted_data) if extracted_data else str(data)
            
            return {
                'title': extracted_data.get('title', ''),
                'full_text': truncate_content(content, 1500),
                'selected_elements': [],
                'articles': [],
                'extracted_data': extracted_data,
                'summary': f"Firecrawl JSON extraction successful"
            }
        else:
            # ✅ Scraping classique avec markdown
            result = app.scrape_url(
                url=url,
                formats=["markdown"],
                only_main_content=True,
                timeout=SCRAPING_CONFIG['firecrawl_timeout'] * 1000,
                waitFor=5000
            )
            
            if hasattr(result, 'success') and not result.success:
                raise Exception(f"Firecrawl scraping failed: {getattr(result, 'error', 'Unknown error')}")
            
            data = result.data if hasattr(result, 'data') else result
            markdown_content = data.get('markdown', '') if isinstance(data, dict) else str(data)
            metadata = data.get('metadata', {}) if isinstance(data, dict) else {}
            
            return {
                'title': metadata.get('title', ''),
                'full_text': truncate_content(markdown_content, 1500),
                'selected_elements': [],
                'articles': [],
                'extracted_data': data,
                'summary': f"Firecrawl markdown scraping successful"
            }
    
    except Exception as e:
        logger.error(f"Firecrawl error: {str(e)}")
        raise

def use_beautifulsoup_enhanced(url: str, css_selector: str = None) -> dict:
    """Version améliorée de BeautifulSoup avec retry et détection."""
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Connection': 'keep-alive'
    }
    
    # Retry logic simple mais efficace
    for attempt in range(SCRAPING_CONFIG['max_retries']):
        try:
            response = requests.get(url, headers=headers, timeout=SCRAPING_CONFIG['requests_timeout'])
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Détection améliorée de contenu bloqué
            if is_content_blocked_simple(soup):
                raise Exception("Content appears to be blocked or requires JavaScript")
            
            # Extraction intelligente
            title = extract_title_simple(soup)
            full_text = extract_main_content_simple(soup)
            selected_elements = extract_css_elements_simple(soup, css_selector) if css_selector else []
            
            if not full_text or len(full_text.strip()) < 100:
                raise Exception("Insufficient content extracted")
            
            return {
                'title': title,
                'full_text': truncate_content(full_text, 1500),
                'selected_elements': selected_elements,
                'articles': [],
                'extracted_data': {'method': 'beautifulsoup'},
                'summary': f"BeautifulSoup: {len(selected_elements)} selected elements"
            }
            
        except Exception as e:
            if attempt == SCRAPING_CONFIG['max_retries'] - 1:
                raise
            time.sleep(1)

def use_selenium_optimized(url: str, css_selector: str = None) -> dict:
    """Version optimisée de Selenium avec options essentielles."""
    
    driver = None
    
    try:
        options = Options()
        options.add_argument("--headless")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--disable-images")
        options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
        driver.set_page_load_timeout(SCRAPING_CONFIG['selenium_timeout'])
        
        # Anti-détection basique
        driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        
        driver.get(url)
        
        # Attente intelligente
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        time.sleep(2)  # Attente pour le contenu dynamique
        
        # Extraction
        title = driver.title
        full_content = driver.find_element(By.TAG_NAME, "body").text
        selected_elements = []
        
        if css_selector:
            try:
                elements = driver.find_elements(By.CSS_SELECTOR, css_selector)
                selected_elements = [el.text[:200] for el in elements[:5]]
            except:
                pass
        
        return {
            'title': title,
            'full_text': truncate_content(full_content, 1500),
            'selected_elements': selected_elements,
            'articles': [],
            'extracted_data': {'method': 'selenium'},
            'summary': f"Selenium: {len(selected_elements)} selected elements"
        }
        
    finally:
        if driver:
            driver.quit()

# Fonctions utilitaires simplifiées mais efficaces

def is_content_blocked_simple(soup):
    """Détection basique mais efficace de contenu bloqué."""
    text = soup.get_text().lower()
    return (
        len(text) < 200 or
        any(word in text for word in ['javascript', 'enable js', 'blocked', 'captcha']) or
        (soup.find('noscript') and len(soup.find('noscript').get_text()) > len(text) * 0.3)
    )

def extract_title_simple(soup):
    """Extraction de titre avec fallbacks."""
    if soup.title:
        return soup.title.get_text(strip=True)
    
    for selector in ['h1', '.title', '#title']:
        element = soup.select_one(selector)
        if element:
            return element.get_text(strip=True)
    
    return ""

def extract_main_content_simple(soup):
    """Extraction de contenu principal intelligent."""
    # Supprimer les éléments non-content
    for tag in soup(['script', 'style', 'nav', 'header', 'footer']):
        tag.decompose()
    
    # Essayer les sélecteurs de contenu principal
    for selector in ['main', 'article', '.content', '#content']:
        elements = soup.select(selector)
        if elements:
            return ' '.join(el.get_text(separator=' ', strip=True) for el in elements)
    
    # Fallback: body entier
    return soup.get_text(separator=' ', strip=True)

def extract_css_elements_simple(soup, css_selector):
    """Extraction d'éléments CSS simplifiée."""
    try:
        elements = soup.select(css_selector)
        return [el.get_text(strip=True)[:200] for el in elements[:5]]
    except:
        return []

def truncate_content(content, max_length=1000):
    """Troncature intelligente du contenu."""
    if len(content) <= max_length:
        return content
    
    truncated = content[:max_length]
    last_space = truncated.rfind(' ')
    if last_space > max_length * 0.8:
        truncated = truncated[:last_space]
    
    return truncated + "..." 

    # Keep your existing web_search function but make it synchronous for smolagents
@tool
def web_search(query: str = None, messages: list = None, allowed_domains: list = None, blocked_domains: list = None, max_results: int = 8) -> dict:
    """
    Searches for information on the web and extracts it to provide relevant results.
    
    Args:
        query (str, optional): The search query. If None, will be generated from messages.
        messages (list, optional): Message history to generate query if needed.
        allowed_domains (list, optional): List of allowed domains to filter results.
        blocked_domains (list, optional): List of domains to block in results.
        max_results (int, optional): Maximum number of results to return. Default 8.
        
    Returns:
        dict: A dictionary with the following structure:
            {
                'search_query': str,
                'results': [{'title': str, 'content': str, 'url': str}, ...],
                'sources': [{'title': str, 'url': str, 'snippet': str}, ...],
                'context': str,
                'instructions': str
            }
            
    Example usage:
        search_result = web_search('news in Paris yesterday')
        # Access the results list
        for result in search_result['results']:
            print(f"Title: {result['title']}")
            print(f"URL: {result['url']}")
            print(f"Content: {result['content'][:200]}")
    """
    logger.info("=== STARTING web_search ===")

    try:
        # Generate or use provided query
        search_query = query
        if not search_query and messages:
            # Derive a simple query from the last user message synchronously
            try:
                last_message = messages[-1]
                if isinstance(last_message, dict) and "content" in last_message:
                    search_query = last_message["content"]
                elif isinstance(last_message, str):
                    search_query = last_message
                else:
                    search_query = str(last_message)
            except Exception:
                search_query = None
        
        if not search_query:
            return {
                "error": "No query provided and couldn't generate one from messages",
                "results": [],
                "sources": []
            }
        
        # Build domain filters if needed
        domain_filters = ""
        if allowed_domains:
            domain_filters += " ".join([f"site:{domain}" for domain in allowed_domains])
        
        if blocked_domains:
            if domain_filters:
                domain_filters += " "
            domain_filters += " ".join([f"-site:{domain}" for domain in blocked_domains])
        
        # Combine filters with query
        full_query = f"{domain_filters} {search_query}".strip()
        
        # Execute web search
        search_results = search_web(full_query, max_results)
        
        # Extract content from found pages (using existing webscraper function)
        scraped_results = []
        sources = []
        
        for idx, result in enumerate(search_results):
            if idx < 5:  # Limit number of pages to scrape
                try:
                    # Use the optimized webscraper
                    scraped_data = webscraper(result["link"])
                    
                    # Add to results and sources
                    if scraped_data and scraped_data.get("full_text"):
                        scraped_results.append({
                            "title": scraped_data.get("title", result["title"]),
                            "content": scraped_data.get("full_text", ""),
                            "url": result["link"]
                        })
                        
                        sources.append({
                            "title": scraped_data.get("title", result["title"]),
                            "url": result["link"],
                            "snippet": scraped_data.get("full_text", "")[:200] + "..."
                        })
                except Exception as e:
                    logger.warning(f"Failed to scrape {result['link']}: {str(e)}")
        
        # Build context from scraped results
        context = ""
        for idx, result in enumerate(scraped_results):
            context += f"Source [{idx + 1}]: {result['title']}\n{result['content']}\n\n----------\n\n"
        
        return {
            "search_query": search_query,
            "results": scraped_results,
            "sources": sources,
            "context": context,
            "instructions": "When answering the question, reference the sources inline by wrapping the index in brackets like this: [1]. If multiple sources are used, reference each without commas like this: [1][2][3]."
        }
    except Exception as e:
        logger.error(f"Web search error: {str(e)}")
        return {
            "error": str(e),
            "results": [],
            "sources": []
        }