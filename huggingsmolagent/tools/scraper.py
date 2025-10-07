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
import re
import json
import random
from firecrawl import FirecrawlApp
from .search.endpoints import search_web
from .search.generate_query import generate_query
import os
from dotenv import load_dotenv
load_dotenv()
logger = logging.getLogger(__name__)

# Configuration for scraping strategy
SCRAPING_CONFIG = {
    'prefer_firecrawl': True,  # Garder Firecrawl actif
    'firecrawl_timeout': 30,   # Timeout plus court pour Firecrawl (30s au lieu de 45s)
    'firecrawl_wait_for': 5,   # Temps d'attente pour le chargement de la page (5s)
    'requests_timeout': 25,    # Timeout pour BeautifulSoup (augmenté pour les retries)
    'selenium_timeout': 30,    # Timeout pour Selenium
    'max_retries': 2,          # 2 essais par méthode
    'retry_delay': 1,          # Délai court entre retries
    'fallback_enabled': True,  # Fallback vers autres méthodes
    'fast_fallback': True,     # Passer rapidement aux alternatives en cas de timeout
}

@tool
def webscraper(url: str, css_selector: str = None, extraction_prompt: str = None, prefer_method: str = "auto") -> dict:
    """
    Enhanced web scraper with improved error handling and fallback mechanisms.
    
    Args:
        url (str): The URL of the webpage to scrape
        css_selector (str, optional): CSS selector to extract specific elements from the page
        extraction_prompt (str, optional): Natural language instructions for Firecrawl to extract specific information
        prefer_method (str, optional): Preferred scraping method - "firecrawl", "selenium", "beautifulsoup", or "auto" for automatic selection
        
    Returns:
        dict: Structured data containing the scraped content, metadata, and scraping information
    """
    logger.info(f"=== STARTING webscraper for URL: {url} ===")
    
    methods = {
        "firecrawl": lambda: use_firecrawl_optimized(url, extraction_prompt, css_selector),
        "selenium": lambda: use_selenium_optimized(url, css_selector),
        "beautifulsoup": lambda: use_beautifulsoup_optimized(url, css_selector)
    }
    
    if prefer_method != "auto":
        if prefer_method in methods:
            try:
                logger.info(f"Attempting with {prefer_method}")
                result = methods[prefer_method]()
                return {
                    "title": "",
                    "full_text": result if isinstance(result, str) else json.dumps(result),
                    "selected_elements": [],
                    "articles": [],
                    "extracted_data": result if isinstance(result, dict) else {},
                    "summary": f"Successfully scraped using {prefer_method}",
                    "scraping_method": prefer_method
                }
            except Exception as e:
                logger.warning(f"{prefer_method} failed: {str(e)}")
                if prefer_method == "auto":
                    logger.info("Falling back to other methods...")
                else:
                    raise
    
    # Try each method in order
    for method_name, method_func in methods.items():
        try:
            logger.info(f"Attempting with {method_name}")
            result = method_func()
            return {
                "title": "",
                "full_text": result if isinstance(result, str) else json.dumps(result),
                "selected_elements": [],
                "articles": [],
                "extracted_data": result if isinstance(result, dict) else {},
                "summary": f"Successfully scraped using {method_name}",
                "scraping_method": method_name
            }
        except Exception as e:
            logger.warning(f"{method_name} failed: {str(e)}")
            continue
    
    # If all methods fail
    error_msg = "All scraping methods failed"
    logger.error(error_msg)
    return {
        "title": "",
        "full_text": error_msg,
        "selected_elements": [],
        "articles": [],
        "extracted_data": {},
        "summary": error_msg,
        "scraping_method": "failed",
        "error": error_msg
    }

def determine_scraping_strategy(url: str, css_selector: str, extraction_prompt: str, prefer_method: str) -> list:
    """Determines the optimal order of scraping methods based on URL and requirements."""
    
    if prefer_method != "auto":
        if prefer_method == "firecrawl":
            return ["firecrawl", "beautifulsoup", "selenium"]
        elif prefer_method == "beautifulsoup":
            return ["beautifulsoup", "selenium", "firecrawl"]
        elif prefer_method == "selenium":
            return ["selenium", "beautifulsoup", "firecrawl"]
    
    # Intelligent automatic logic
    domain = url.split('/')[2].lower()
    
    # Sites that often require JavaScript
    js_heavy_domains = ['spa', 'react', 'angular', 'vue', 'twitter', 'facebook', 'instagram', 'tiktok','flightstats', 'flightaware', 'flightview']
    needs_js = any(keyword in domain for keyword in js_heavy_domains)
    
    # Flight tracking sites that often have timeout issues
    flight_domains = ['flightstats', 'flightaware', 'flightradar24', 'planefinder']
    is_flight_site = any(keyword in domain for keyword in flight_domains)
    
    # Sites that often block scrapers
    blocking_domains = ['cloudflare', 'bot-protection', 'captcha']
    likely_blocked = any(keyword in domain for keyword in blocking_domains)
    
    # Special handling for flight status sites (prefer Selenium for real-time data)
    if is_flight_site:
        return ["selenium", "firecrawl", "beautifulsoup"]
    
    # If we have specific instructions, prefer Firecrawl
    if extraction_prompt:
        return ["firecrawl", "selenium", "beautifulsoup"]
    
    # If site requires JS, start with Selenium
    if needs_js or likely_blocked:
        return ["selenium", "firecrawl", "beautifulsoup"]
    
    # For static sites, BeautifulSoup first (faster)
    if not css_selector:
        return ["beautifulsoup", "firecrawl", "selenium"]
    
    # Default: Firecrawl first (but with fast fallback)
    return ["firecrawl", "beautifulsoup", "selenium"]

def retry_with_backoff(func, *args, max_retries=3, initial_delay=1, **kwargs):
    """
    Retry function with exponential backoff for handling timeouts.
    """
    # Pour Firecrawl, utiliser un retry plus conservateur
    func_name = func.__name__ if hasattr(func, '__name__') else str(func)
    if 'firecrawl' in func_name.lower():
        max_retries = min(max_retries, 2)  # Maximum 2 essais pour Firecrawl
    
    for attempt in range(max_retries):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            error_str = str(e).lower()
            
            # Check if it's a timeout or connection error
            is_timeout = any(keyword in error_str for keyword in [
                'timeout', 'timed out', 'connection timeout', '408', 
                'connection error', 'connection failed', 'read timeout',
                'request timeout', 'failed to scrape'
            ])
            
            # Pour Firecrawl avec fast_fallback, échouer plus rapidement après 1 essai
            if ('firecrawl' in func_name.lower() and is_timeout and 
                SCRAPING_CONFIG.get('fast_fallback', False) and attempt >= 0):
                logger.warning(f"Firecrawl timeout - passage rapide à la méthode suivante: {str(e)}")
                raise  # Passer rapidement à la méthode suivante
            
            if not is_timeout or attempt == max_retries - 1:
                raise  # Re-raise if not timeout or last attempt
            
            delay = initial_delay * (1.2 ** attempt)  # Backoff très modéré
            logger.warning(f"Timeout error on attempt {attempt + 1}/{max_retries}, retrying in {delay:.1f}s: {str(e)}")
            time.sleep(delay)
    
    raise Exception(f"All {max_retries} attempts failed")

def use_firecrawl_optimized(url: str, extraction_prompt: str = None, css_selector: str = None) -> dict:
    """Optimized Firecrawl implementation with correct v1 API usage and enhanced error handling."""
    try:
        api_key = os.getenv("FIRECRAWL_API_KEY")
        if not api_key:
            raise Exception("FIRECRAWL_API_KEY not found in environment variables. Please set it in your .env file or environment.")
        
        app = FirecrawlApp(api_key=api_key)
        
        logger.debug(f"🔧 Firecrawl extraction_prompt: {extraction_prompt}")
        
        if extraction_prompt:
            # ✅ Use scrape_url with json format for extraction in v1 API
            logger.info(f"🔥 Calling Firecrawl scrape_url with JSON extraction...")
            
            # Format the prompt properly for jsonOptions
            optimized_prompt_text = f"""
            Extract the following information from this webpage:
            {extraction_prompt}

            Please structure the response as JSON with clear field names.
            Focus on extracting only the most relevant and accurate information.
            If the page contains prices, include currency information.
            If the page contains links, include relevant URLs.
            """

            # ✅ Correct v1 API format using scrape_url with json format
            # Fix the waitFor parameter issue - use numeric value
            result = app.scrape_url(
                url=url,
                formats=["json"],
                json_options={
                    "prompt": optimized_prompt_text.strip()
                },
                only_main_content=True,
                timeout=SCRAPING_CONFIG.get('firecrawl_timeout', 30) * 1000,  # Convert to milliseconds
                waitFor=SCRAPING_CONFIG.get('firecrawl_wait_for', 5) * 1000,  # Use configurable wait time in milliseconds
                skip_tls_verification=True  # Ignorer les problèmes SSL qui peuvent causer des timeouts
            )

            # Enhanced response processing with better error handling
            if hasattr(result, 'success') and not result.success:
                error_message = getattr(result, 'error', 'Unknown Firecrawl extraction error')
                logger.error(f"Firecrawl scrape_url with JSON failed: {error_message}")
                raise Exception(f"Firecrawl extraction failed: {error_message}")
            
            # Validate result structure
            if not result:
                raise Exception("Firecrawl returned empty result")
            
            # Extract data from the result with better error handling
            data = None
            if hasattr(result, 'data'):
                data = result.data
            elif isinstance(result, dict) and 'data' in result:
                data = result['data']
            elif isinstance(result, dict):
                data = result
            else:
                # Handle Firecrawl ScrapeResponse object
                logger.debug(f"Handling Firecrawl response object: {type(result)}")
                if hasattr(result, '__dict__'):
                    # Convert object to dict
                    data = result.__dict__
                else:
                    data = str(result)

            if not data:
                raise Exception("No data found in Firecrawl response")

            # Get the extracted JSON data with fallbacks
            extracted_data = {}
            if isinstance(data, dict):
                extracted_data = data.get('json', data.get('extract', data.get('content', {})))
            
            metadata = data.get('metadata', {}) if isinstance(data, dict) else {}
            
            # Better title extraction
            title = (metadata.get('title') or 
                    extracted_data.get('title') or 
                    extracted_data.get('name') or 
                    extracted_data.get('heading') or '')
            
            content = str(extracted_data) if extracted_data else str(data)
            
            if not content or content == "{}":
                raise Exception("Firecrawl extraction returned empty content")
            
            logger.info(f"✅ Firecrawl JSON extraction successful: {len(content)} characters")

            return {
                'title': title,
                'full_text': truncate_content(content, 1500),
                'selected_elements': [],  
                'articles': [],
                'extracted_data': extracted_data,
                'summary': f"Firecrawl JSON extraction successful - {len(content)} characters extracted"
            }
        else:
            # ✅ Standard scraping without extraction using v1 API format
            logger.info(f"🔥 Calling Firecrawl scrape_url for standard scraping...")
            
            result = app.scrape_url(
                url=url,
                formats=["markdown"],
                only_main_content=True,
                timeout=SCRAPING_CONFIG.get('firecrawl_timeout', 30) * 1000,  # Convert to milliseconds
                waitFor=SCRAPING_CONFIG.get('firecrawl_wait_for', 5) * 1000,  # Use configurable wait time in milliseconds
                skip_tls_verification=True  # Ignorer les problèmes SSL
            )
            
            # Process the response
            if hasattr(result, 'success') and not result.success:
                error_message = getattr(result, 'error', 'Unknown Firecrawl scraping error')
                logger.error(f"Firecrawl scrape_url failed: {error_message}")
                raise Exception(f"Firecrawl scraping failed: {error_message}")
            
            # Extract data from the result
            if hasattr(result, 'data'):
                data = result.data
            elif isinstance(result, dict) and 'data' in result:
                data = result['data']
            elif isinstance(result, dict):
                data = result
            else:
                # Handle Firecrawl ScrapeResponse object
                logger.debug(f"Handling Firecrawl response object: {type(result)}")
                if hasattr(result, '__dict__'):
                    # Convert object to dict
                    data = result.__dict__
                else:
                    data = str(result)

            markdown_content = data.get('markdown', '') if isinstance(data, dict) else str(data)
            metadata = data.get('metadata', {}) if isinstance(data, dict) else {}
            title_from_scrape = metadata.get('title', '')

            logger.info(f"✅ Firecrawl standard scraping successful: {len(markdown_content)} characters")

            return {
                'title': title_from_scrape,
                'full_text': truncate_content(markdown_content, 1500),
                'selected_elements': [],
                'articles': [],
                'extracted_data': data,
                'summary': f"Firecrawl standard scraping successful - {len(markdown_content)} characters"
            }
    
    except Exception as e:
        error_msg = str(e)
        
        # Enhanced error categorization and logging
        error_type = "unknown"
        if "timeout" in error_msg.lower() or "timed out" in error_msg.lower():
            error_type = "timeout"
        elif "400" in error_msg or "bad request" in error_msg.lower():
            error_type = "bad_request"
        elif "401" in error_msg or "unauthorized" in error_msg.lower():
            error_type = "auth_error"
        elif "403" in error_msg or "forbidden" in error_msg.lower():
            error_type = "forbidden"
        elif "404" in error_msg or "not found" in error_msg.lower():
            error_type = "not_found"
        elif "500" in error_msg or "internal server error" in error_msg.lower():
            error_type = "server_error"
        elif "rate limit" in error_msg.lower() or "429" in error_msg:
            error_type = "rate_limit"
        elif "waitfor" in error_msg.lower() or "invalid_type" in error_msg.lower():
            error_type = "parameter_error"
        
        logger.error(f"❌ Firecrawl {error_type} error for URL '{url}': {error_msg}")
        
        # Log the URL and parameters for debugging
        current_params = {
            'extraction_prompt': extraction_prompt if extraction_prompt else None,
            'css_selector': css_selector if css_selector else None,
            'error_type': error_type
        }
        logger.warning(f"💥 Firecrawl failure for URL '{url}' with params {current_params}: {error_msg}")
        
        # Provide more specific error messages for common issues
        if error_type == "parameter_error":
            raise Exception(f"Firecrawl parameter error (likely waitFor format): {error_msg}")
        elif error_type == "timeout":
            raise Exception(f"Firecrawl timeout after {SCRAPING_CONFIG.get('firecrawl_timeout', 45)}s: {error_msg}")
        elif error_type == "auth_error":
            raise Exception(f"Firecrawl authentication error - check API key: {error_msg}")
        elif error_type == "rate_limit":
            raise Exception(f"Firecrawl rate limit exceeded: {error_msg}")
        else:
            raise Exception(f"Firecrawl {error_type} error: {error_msg}")

def use_beautifulsoup_optimized(url: str, css_selector: str = None) -> dict:
    """Optimized BeautifulSoup implementation with enhanced error handling and session management."""
    
    session = None
    
    try:
        # Create a session for better connection handling
        session = requests.Session()
        
        # Enhanced headers with more realistic browser simulation
        headers = get_enhanced_headers()
        session.headers.update(headers)
        
        # Configure session settings
        timeout = SCRAPING_CONFIG.get('requests_timeout', 20)
        
        # Retry logic with different strategies
        max_attempts = 3
        last_error = None
        
        for attempt in range(max_attempts):
            try:
                logger.debug(f"BeautifulSoup attempt {attempt + 1}/{max_attempts} for {url}")
                
                # Try different request strategies
                if attempt == 0:
                    # Standard request
                    response = session.get(
                        url, 
                        timeout=timeout,
                        allow_redirects=True,
                        stream=False,
                        verify=True  # Verify SSL by default
                    )
                elif attempt == 1:
                    # Retry with different headers and no SSL verification
                    session.headers.update(get_enhanced_headers())
                    response = session.get(
                        url, 
                        timeout=timeout + 5,  # Slightly longer timeout
                        allow_redirects=True,
                        stream=False,
                        verify=False  # Skip SSL verification
                    )
                else:
                    # Final attempt with minimal headers
                    session.headers.clear()
                    session.headers.update({
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
                    })
                    response = session.get(
                        url, 
                        timeout=timeout + 10,
                        allow_redirects=True,
                        stream=False,
                        verify=False
                    )
                
                response.raise_for_status()
                break  # Success, exit retry loop
                
            except requests.exceptions.RequestException as e:
                last_error = e
                error_msg = str(e).lower()
                
                if attempt == max_attempts - 1:
                    # Last attempt failed
                    break
                
                # Determine if we should retry
                should_retry = any(keyword in error_msg for keyword in [
                    'timeout', 'connection', 'ssl', 'certificate', 'handshake'
                ])
                
                if not should_retry:
                    break  # Don't retry for non-recoverable errors
                
                logger.warning(f"BeautifulSoup attempt {attempt + 1} failed, retrying: {str(e)}")
                time.sleep(1)  # Brief delay before retry
        
        if not response or response.status_code != 200:
            raise Exception(f"Failed to fetch content after {max_attempts} attempts: {str(last_error)}")
        
        # Enhanced content validation
        if not response.text or len(response.text) < 100:
            raise Exception("Response content is too short or empty")
        
        # Check content type
        content_type = response.headers.get('content-type', '').lower()
        if 'text/html' not in content_type and 'text/plain' not in content_type:
            raise Exception(f"Unexpected content type: {content_type}")
        
        # Parse with BeautifulSoup
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Enhanced content validation
        if not soup or not soup.find():
            raise Exception("Failed to parse HTML content")
        
        # Intelligent detection of blocked content with enhanced checks
        if is_content_blocked_enhanced(soup, response):
            raise Exception("Content appears to be blocked or requires JavaScript")
        
        # Enhanced extraction with error handling
        title = ""
        full_text = ""
        articles = []
        selected_elements = []
        
        try:
            title = extract_title(soup)
        except Exception as e:
            logger.warning(f"Error extracting title: {str(e)}")
        
        try:
            full_text = extract_main_content(soup)
        except Exception as e:
            logger.warning(f"Error extracting main content: {str(e)}")
        
        try:
            articles = extract_articles_bs(soup, url)
        except Exception as e:
            logger.warning(f"Error extracting articles: {str(e)}")
        
        try:
            selected_elements = extract_css_elements(soup, css_selector) if css_selector else []
        except Exception as e:
            logger.warning(f"Error extracting CSS elements: {str(e)}")
        
        # Validate that we extracted meaningful content
        if not full_text and not articles and not selected_elements:
            raise Exception("No meaningful content extracted from page")
        
        if full_text and len(full_text.strip()) < 50:
            raise Exception("Extracted content is too short, likely blocked or empty page")
        
        return {
            'title': title,
            'full_text': truncate_content(full_text, 1500),
            'selected_elements': selected_elements,
            'articles': articles,
            'extracted_data': {
                'method': 'beautifulsoup',
                'content_length': len(full_text),
                'response_size': len(response.text),
                'status_code': response.status_code
            },
            'summary': f"BeautifulSoup: {len(articles)} articles, {len(selected_elements)} selected elements"
        }
    
    except Exception as e:
        error_msg = str(e)
        
        # Enhanced error categorization for BeautifulSoup
        error_type = "unknown"
        if "timeout" in error_msg.lower() or "timed out" in error_msg.lower():
            error_type = "timeout"
        elif "connection" in error_msg.lower():
            error_type = "connection_error"
        elif "ssl" in error_msg.lower() or "certificate" in error_msg.lower():
            error_type = "ssl_error"
        elif "blocked" in error_msg.lower() or "javascript" in error_msg.lower():
            error_type = "content_blocked"
        elif "404" in error_msg or "not found" in error_msg.lower():
            error_type = "not_found"
        elif "403" in error_msg or "forbidden" in error_msg.lower():
            error_type = "forbidden"
        elif "500" in error_msg or "server error" in error_msg.lower():
            error_type = "server_error"
        elif "content type" in error_msg.lower():
            error_type = "content_type_error"
        elif "too short" in error_msg.lower() or "empty" in error_msg.lower():
            error_type = "empty_content"
        
        logger.error(f"❌ BeautifulSoup {error_type} error for URL '{url}': {error_msg}")
        
        # Provide specific error messages
        if error_type == "content_blocked":
            raise Exception(f"BeautifulSoup content blocked - site requires JavaScript: {error_msg}")
        elif error_type == "timeout":
            raise Exception(f"BeautifulSoup timeout after {SCRAPING_CONFIG.get('requests_timeout', 20)}s: {error_msg}")
        elif error_type == "ssl_error":
            raise Exception(f"BeautifulSoup SSL/Certificate error: {error_msg}")
        elif error_type == "connection_error":
            raise Exception(f"BeautifulSoup connection error: {error_msg}")
        elif error_type == "empty_content":
            raise Exception(f"BeautifulSoup extracted empty or insufficient content: {error_msg}")
        else:
            raise Exception(f"BeautifulSoup {error_type} error: {error_msg}")
    
    finally:
        if session:
            try:
                session.close()
            except Exception as cleanup_error:
                logger.warning(f"Error closing BeautifulSoup session: {str(cleanup_error)}")

def use_selenium_optimized(url: str, css_selector: str = None) -> dict:
    """Optimized Selenium implementation with enhanced error handling and resource management."""
    
    driver = None
    
    try:
        options = get_optimized_chrome_options()
        service = Service(ChromeDriverManager().install())
        
        # Enhanced driver initialization with retry logic
        max_init_attempts = 3
        for attempt in range(max_init_attempts):
            try:
                driver = webdriver.Chrome(service=service, options=options)
                break
            except Exception as init_error:
                logger.warning(f"Selenium driver init attempt {attempt + 1}/{max_init_attempts} failed: {str(init_error)}")
                if attempt == max_init_attempts - 1:
                    raise Exception(f"Failed to initialize Chrome driver after {max_init_attempts} attempts: {str(init_error)}")
                time.sleep(1)  # Wait before retry
        
        # Set timeouts
        driver.set_page_load_timeout(SCRAPING_CONFIG.get('selenium_timeout', 30))
        driver.implicitly_wait(10)
        
        # Anti-detection setup
        stealth_setup(driver)
        
        logger.info(f"Selenium navigating to {url}")
        
        # Enhanced navigation with timeout handling
        try:
            driver.get(url)
        except Exception as nav_error:
            if "timeout" in str(nav_error).lower():
                logger.warning(f"Page load timeout, but continuing: {str(nav_error)}")
                # Continue with partial page load
            else:
                raise
        
        # Intelligent waiting
        wait_for_content_load(driver)
        
        # Handle popups
        handle_consent_popups_optimized(driver)
        
        # Content extraction with error handling
        title = ""
        full_content = ""
        articles = []
        selected_elements = []
        
        try:
            title = driver.title or ""
        except Exception as e:
            logger.warning(f"Error getting title: {str(e)}")
        
        try:
            full_content = extract_body_content(driver)
        except Exception as e:
            logger.warning(f"Error extracting content: {str(e)}")
        
        try:
            articles = extract_articles_selenium(driver)
        except Exception as e:
            logger.warning(f"Error extracting articles: {str(e)}")
        
        try:
            selected_elements = extract_css_elements_selenium(driver, css_selector) if css_selector else []
        except Exception as e:
            logger.warning(f"Error extracting CSS elements: {str(e)}")
        
        # Validate that we got some content
        if not full_content and not articles and not selected_elements:
            raise Exception("No content extracted from page")
        
        return {
            'title': title,
            'full_text': truncate_content(full_content, 1500),
            'selected_elements': selected_elements,
            'articles': articles,
            'extracted_data': {
                'method': 'selenium',
                'page_loaded': True,
                'content_length': len(full_content)
            },
            'summary': f"Selenium: {len(articles)} articles, {len(selected_elements)} selected elements"
        }
        
    except Exception as e:
        error_msg = str(e)
        
        # Enhanced error categorization for Selenium
        error_type = "unknown"
        if "session not created" in error_msg.lower():
            error_type = "session_creation"
        elif "unable to discover open pages" in error_msg.lower():
            error_type = "page_discovery"
        elif "timeout" in error_msg.lower() or "timed out" in error_msg.lower():
            error_type = "timeout"
        elif "chromedriver" in error_msg.lower():
            error_type = "driver_issue"
        elif "no such element" in error_msg.lower():
            error_type = "element_not_found"
        elif "connection refused" in error_msg.lower():
            error_type = "connection_error"
        
        logger.error(f"❌ Selenium {error_type} error for URL '{url}': {error_msg}")
        
        # Provide specific error messages
        if error_type == "session_creation" or error_type == "page_discovery":
            raise Exception(f"Selenium Chrome session creation failed - possible Chrome/driver compatibility issue: {error_msg}")
        elif error_type == "timeout":
            raise Exception(f"Selenium timeout after {SCRAPING_CONFIG.get('selenium_timeout', 30)}s: {error_msg}")
        elif error_type == "driver_issue":
            raise Exception(f"Selenium ChromeDriver issue - may need driver update: {error_msg}")
        else:
            raise Exception(f"Selenium {error_type} error: {error_msg}")
    finally:
        if driver:
            try:
                driver.quit()
            except Exception as cleanup_error:
                logger.warning(f"Error during Selenium cleanup: {str(cleanup_error)}")

# Optimized utility functions

def get_random_headers():
    """Returns random headers to avoid detection."""
    user_agents = [
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15',
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    ]
    
    return {
        'User-Agent': random.choice(user_agents),
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Accept-Encoding': 'gzip, deflate',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1'
    }

def get_enhanced_headers():
    """Returns enhanced headers with more realistic browser simulation."""
    user_agents = [
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    ]
    
    return {
        'User-Agent': random.choice(user_agents),
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
        'Accept-Language': 'en-US,en;q=0.9,fr;q=0.8',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Sec-Fetch-User': '?1',
        'Cache-Control': 'max-age=0',
        'DNT': '1',  # Do Not Track
        'Sec-CH-UA': '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
        'Sec-CH-UA-Mobile': '?0',
        'Sec-CH-UA-Platform': '"Windows"'
    }

def is_content_blocked(soup):
    """Detects if content is blocked or requires JavaScript."""
    indicators = [
        lambda: len(soup.get_text()) < 500,  # Content too short
        lambda: any(word in soup.get_text().lower() for word in ['javascript', 'enable js', 'blocked']),
        lambda: soup.find('noscript') and len(soup.find('noscript').get_text()) > 100,
        lambda: any(word in soup.get_text().lower() for word in ['consent', 'cookie', 'gdpr']) and len(soup.get_text()) < 2000
    ]
    
    return any(check() for check in indicators)

def is_content_blocked_enhanced(soup, response):
    """Enhanced detection of blocked content with response analysis."""
    if not soup or not response:
        return True
    
    text_content = soup.get_text().strip()
    text_lower = text_content.lower()
    
    # Basic content length check
    if len(text_content) < 100:
        return True
    
    # JavaScript requirement indicators
    js_indicators = [
        'javascript is disabled',
        'enable javascript',
        'please enable javascript',
        'javascript required',
        'js is disabled',
        'turn on javascript',
        'javascript must be enabled',
        'this site requires javascript'
    ]
    
    if any(indicator in text_lower for indicator in js_indicators):
        return True
    
    # Bot detection indicators
    bot_indicators = [
        'access denied',
        'blocked',
        'bot detected',
        'automated requests',
        'unusual traffic',
        'captcha',
        'verify you are human',
        'security check',
        'cloudflare',
        'ddos protection'
    ]
    
    if any(indicator in text_lower for indicator in bot_indicators):
        return True
    
    # Check for redirect pages or loading pages
    redirect_indicators = [
        'redirecting',
        'please wait',
        'loading',
        'you will be redirected',
        'if you are not redirected'
    ]
    
    if any(indicator in text_lower for indicator in redirect_indicators) and len(text_content) < 1000:
        return True
    
    # Check for consent/cookie walls that block content
    consent_indicators = ['consent', 'cookie', 'gdpr', 'privacy policy', 'accept cookies']
    if (any(indicator in text_lower for indicator in consent_indicators) and 
        len(text_content) < 2000 and
        not any(word in text_lower for word in ['article', 'content', 'news', 'blog', 'post'])):
        return True
    
    # Check response headers for additional clues
    content_type = response.headers.get('content-type', '').lower()
    if 'application/json' in content_type:
        # Might be an API response instead of HTML
        return True
    
    # Check for minimal HTML structure
    if not soup.find('body') or not soup.find('head'):
        return True
    
    # Check for single-page applications with minimal server-side content
    script_tags = soup.find_all('script')
    if (len(script_tags) > 10 and  # Lots of scripts
        len(text_content) < 1000 and  # But little text content
        any('react' in str(script).lower() or 'vue' in str(script).lower() or 'angular' in str(script).lower() 
            for script in script_tags)):
        return True
    
    # Check for noscript content that's longer than main content
    noscript = soup.find('noscript')
    if noscript and len(noscript.get_text()) > len(text_content) * 0.5:
        return True
    
    return False

def extract_title(soup):
    """Extracts page title with fallbacks."""
    if soup.title:
        return soup.title.get_text(strip=True)
    
    # Fallback to h1 or other title elements
    for selector in ['h1', '.title', '#title', '[data-title]']:
        element = soup.select_one(selector)
        if element:
            return element.get_text(strip=True)
    
    return ""

def extract_main_content(soup):
    """Intelligently extracts main content."""
    # Priority order for main content
    content_selectors = [
        'main',
        'article',
        '[role="main"]',
        '.content',
        '.main-content',
        '#content',
        '.post-content',
        '.entry-content'
    ]
    
    for selector in content_selectors:
        elements = soup.select(selector)
        if elements:
            return ' '.join(el.get_text(separator=' ', strip=True) for el in elements)
    
    # Fallback: entire body minus header/footer/nav
    for tag in soup(['header', 'footer', 'nav', 'script', 'style']):
        tag.decompose()
    
    return soup.get_text(separator=' ', strip=True)

def extract_articles_bs(soup, base_url):
    """Extracts article links using BeautifulSoup."""
    articles = []
    
    # Look for article links in common patterns
    article_elements = (
        soup.find_all('article') or 
        soup.find_all('div', class_=lambda x: x and ('post' in x.lower() or 'article' in x.lower())) or
        soup.find_all('a', href=lambda x: x and ('/20' in x or '/article' in x or '/post' in x))
    )
    
    for element in article_elements[:10]:  # Limit to 10 articles
        article = {}
        
        # Try to find title and link in various ways
        if element.name == 'article':
            link_elem = element.find('a')
            title_elem = element.find(['h1', 'h2', 'h3']) or link_elem
        else:
            link_elem = element if element.name == 'a' else element.find('a')
            title_elem = element.find(['h1', 'h2', 'h3']) or link_elem
            
        if link_elem and link_elem.get('href'):
            href = link_elem['href']
            # Make relative URLs absolute
            if href.startswith('/'):
                href = '/'.join(base_url.split('/')[:3]) + href
            article['link'] = href
            
        if title_elem:
            title = title_elem.get_text(strip=True)
            if title and len(title) > 5:  # Ignore very short titles
                article['title'] = title
                
        if article.get('title') and article.get('link'):
            articles.append(article)
    
    return articles

def extract_css_elements(soup, css_selector):
    """Extracts elements matching CSS selector."""
    if not css_selector:
        return []
    
    try:
        elements = soup.select(css_selector)
        if elements:
            # Limit to 5 elements max, 200 chars each
            max_elements = 5
            selected_elements = [
                el.get_text(strip=True)[:200] + "..." if len(el.get_text(strip=True)) > 200 
                else el.get_text(strip=True)
                for el in elements[:max_elements]
            ]
            if len(elements) > max_elements:
                selected_elements.append(f"...and {len(elements) - max_elements} more elements")
            return selected_elements
        
        # Try alternative selector if no match
        try:
            elements = soup.select(f".{css_selector}")
            if elements:
                return [el.get_text(strip=True)[:200] for el in elements[:5]]
        except:
            pass
            
    except Exception as e:
        logger.warning(f"CSS selector error: {str(e)}")
    
    return []

def extract_articles_from_structured(structured_data):
    """Extracts articles from Firecrawl structured data."""
    articles = []
    
    # Different ways Firecrawl might structure articles
    if 'links' in structured_data:
        for link in structured_data['links'][:10]:
            if isinstance(link, dict) and 'text' in link and 'url' in link:
                articles.append({
                    'title': link['text'],
                    'link': link['url']
                })
    
    if 'articles' in structured_data:
        articles.extend(structured_data['articles'][:10])
    
    return articles

def truncate_content(content, max_length=1000):
    """Intelligently truncates content."""
    if len(content) <= max_length:
        return content
    
    # Cut at last space to avoid cutting in middle of word
    truncated = content[:max_length]
    last_space = truncated.rfind(' ')
    if last_space > max_length * 0.8:  # If we find space in last 20%
        truncated = truncated[:last_space]
    
    return truncated + "..."

def get_optimized_chrome_options():
    """Chrome options optimized for performance, stealth, and stability."""
    options = Options()
    
    # Core headless configuration
    options.add_argument("--headless=new")  # Use new headless mode for better stability
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    
    # GPU and rendering optimizations
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-software-rasterizer")
    options.add_argument("--disable-background-timer-throttling")
    options.add_argument("--disable-backgrounding-occluded-windows")
    options.add_argument("--disable-renderer-backgrounding")
    
    # Performance optimizations
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-images")  # Faster loading
    options.add_argument("--disable-plugins")
    options.add_argument("--disable-default-apps")
    
    # Memory and process optimizations
    options.add_argument("--memory-pressure-off")
    options.add_argument("--max_old_space_size=4096")
    options.add_argument("--single-process")  # Can help with session creation issues
    
    # Network and security
    options.add_argument("--disable-web-security")
    options.add_argument("--allow-running-insecure-content")
    options.add_argument("--ignore-certificate-errors")
    options.add_argument("--ignore-ssl-errors")
    options.add_argument("--ignore-certificate-errors-spki-list")
    
    # Anti-detection measures
    options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    
    # Stability improvements for session creation
    options.add_argument("--disable-features=VizDisplayCompositor")
    options.add_argument("--disable-ipc-flooding-protection")
    options.add_argument("--disable-hang-monitor")
    options.add_argument("--disable-client-side-phishing-detection")
    options.add_argument("--disable-popup-blocking")
    options.add_argument("--disable-prompt-on-repost")
    
    # Logging and debugging (can help identify issues)
    options.add_argument("--enable-logging")
    options.add_argument("--log-level=3")  # Only fatal errors
    options.add_argument("--silent")
    
    # Additional prefs for stability
    prefs = {
        "profile.default_content_setting_values": {
            "notifications": 2,  # Block notifications
            "geolocation": 2,    # Block location sharing
        },
        "profile.managed_default_content_settings": {
            "images": 2  # Block images for faster loading
        }
    }
    options.add_experimental_option("prefs", prefs)
    
    return options

def stealth_setup(driver):
    """Sets up anti-detection measures for Selenium."""
    driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

def wait_for_content_load(driver):
    """Intelligently waits for content to load with enhanced error handling."""
    try:
        selenium_timeout = SCRAPING_CONFIG.get('selenium_timeout', 30)
        
        # Wait for basic page structure
        try:
            WebDriverWait(driver, min(selenium_timeout, 15)).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
            logger.debug("✅ Body element found")
        except Exception as e:
            logger.warning(f"Body element not found within timeout: {str(e)}")
            return  # Continue without body if needed
        
        # Wait for document ready state
        try:
            WebDriverWait(driver, 10).until(
                lambda d: d.execute_script("return document.readyState") == "complete"
            )
            logger.debug("✅ Document ready state complete")
        except Exception as e:
            logger.warning(f"Document ready state timeout: {str(e)}")
        
        # Additional wait for dynamic content
        time.sleep(2)  # Reduced from 3 to 2 seconds
        
        # Try to wait for meaningful content
        try:
            WebDriverWait(driver, 5).until(
                lambda d: len(d.find_element(By.TAG_NAME, "body").text.strip()) > 50
            )
            logger.debug("✅ Meaningful content detected")
        except Exception as e:
            logger.debug(f"Meaningful content timeout (continuing anyway): {str(e)}")
        
        # Wait for any JavaScript to finish (if present)
        try:
            WebDriverWait(driver, 3).until(
                lambda d: d.execute_script("return jQuery.active == 0") if d.execute_script("return typeof jQuery !== 'undefined'") else True
            )
        except:
            pass  # jQuery might not be present
            
    except Exception as e:
        logger.warning(f"Content load error: {str(e)}")

def extract_body_content(driver):
    """Extracts body content using Selenium."""
    try:
        return driver.find_element(By.TAG_NAME, "body").text
    except Exception as e:
        logger.warning(f"Error extracting body content: {str(e)}")
        return ""

def extract_articles_selenium(driver):
    """Extracts articles using Selenium."""
    articles = []
    try:
        # Try different strategies to find articles
        article_elements = (
            driver.find_elements(By.TAG_NAME, "article") or 
            driver.find_elements(By.XPATH, "//div[contains(@class, 'post') or contains(@class, 'article')]") or
            driver.find_elements(By.XPATH, "//a[contains(@href, '/20') or contains(@href, '/article') or contains(@href, '/post')]")
        )
        
        for element in article_elements[:10]:  # Limit to 10 articles
            try:
                if element.tag_name == "article":
                    link_elem = element.find_element(By.TAG_NAME, "a")
                    try:
                        title_elem = element.find_element(By.XPATH, ".//h1 | .//h2 | .//h3")
                    except:
                        title_elem = link_elem
                else:
                    link_elem = element if element.tag_name == "a" else element.find_element(By.TAG_NAME, "a")
                    try:
                        title_elem = element.find_element(By.XPATH, ".//h1 | .//h2 | .//h3")
                    except:
                        title_elem = link_elem
                        
                href = link_elem.get_attribute("href") if link_elem else None
                title = title_elem.text if title_elem else None
                
                if href and title and len(title) > 5:  # Ignore very short titles
                    articles.append({
                        "title": title,
                        "link": href
                    })
            except Exception as e:
                continue  # Skip this article on error
    except Exception as e:
        logger.warning(f"Error extracting articles: {str(e)}")
    
    return articles

def extract_css_elements_selenium(driver, css_selector):
    """Extracts elements matching CSS selector using Selenium."""
    if not css_selector:
        return []
    
    try:
        elements = driver.find_elements(By.CSS_SELECTOR, css_selector)
        if elements:
            # Limit to 5 elements max, 200 chars each
            max_elements = 5
            selected_elements = [
                el.text[:200] + "..." if len(el.text) > 200 else el.text 
                for el in elements[:max_elements]
            ]
            if len(elements) > max_elements:
                selected_elements.append(f"...and {len(elements) - max_elements} more elements")
            return selected_elements
    except Exception as e:
        logger.warning(f"Error with CSS selector '{css_selector}': {str(e)}")
    
    return []

def handle_consent_popups_optimized(driver):
    """Handles common consent popups more efficiently."""
    try:
        # Common consent button XPath patterns
        consent_patterns = [
            "//button[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'accept')]",
            "//button[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'agree')]",
            "//button[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'allow')]",
            "//button[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'ok')]",
            "//a[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'accept')]"
        ]
        
        for xpath in consent_patterns:
            try:
                # Short timeout for each attempt
                button = WebDriverWait(driver, 2).until(
                    EC.element_to_be_clickable((By.XPATH, xpath))
                )
                # Scroll to button and click
                driver.execute_script("arguments[0].scrollIntoView();", button)
                time.sleep(0.5)
                button.click()
                logger.info(f"Clicked consent button with XPath: {xpath}")
                time.sleep(1)  # Wait after click
                return True
            except:
                continue
        
        return False
    except Exception as e:
        logger.warning(f"Error handling consent popups: {str(e)}")
        return False

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
        dict: Structured results with context and sources
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