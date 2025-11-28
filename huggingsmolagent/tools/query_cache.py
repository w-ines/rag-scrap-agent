"""
Query and embedding cache system
Drastically reduces response time on similar queries
"""

import hashlib
import json
import time
from typing import Optional, Dict, Any, List
from functools import wraps
from cachetools import TTLCache, LRUCache
import os
from dotenv import load_dotenv

load_dotenv()

# Cache configuration
CACHE_ENABLED = os.getenv("CACHE_ENABLED", "true").lower() == "true"
CACHE_TTL = int(os.getenv("CACHE_TTL_SECONDS", "3600"))  # 1 hour by default
CACHE_MAX_SIZE = int(os.getenv("CACHE_MAX_SIZE", "1000"))  # Max 1000 entries

# In-memory cache with TTL (Time To Live)
# TTLCache: automatically expires after CACHE_TTL seconds
query_cache = TTLCache(maxsize=CACHE_MAX_SIZE, ttl=CACHE_TTL)

# Cache for embeddings (longer TTL as they're more expensive to compute)
embedding_cache = TTLCache(maxsize=500, ttl=7200)  # 2 hours

# Cache statistics
cache_stats = {
    "hits": 0,
    "misses": 0,
    "total_time_saved": 0.0,
}


def compute_query_hash(query: str, **kwargs) -> str:
    """
    Computes a unique hash for a query and its parameters.
    Similar queries will have the same hash.
    """
    # Normalize the query (lowercase, strip whitespace)
    normalized_query = query.lower().strip()
    
    # Include important parameters in the hash
    params_str = json.dumps(kwargs, sort_keys=True)
    
    # Create a SHA256 hash
    hash_input = f"{normalized_query}:{params_str}"
    return hashlib.sha256(hash_input.encode()).hexdigest()[:16]


def cache_query_result(ttl: Optional[int] = None):
    """
    Decorator to cache function results.
    
    Usage:
        @cache_query_result(ttl=3600)
        def my_expensive_function(query: str):
            # ... expensive processing
            return result
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            if not CACHE_ENABLED:
                return func(*args, **kwargs)
            
            # Extract query from first argument or kwargs
            query = args[0] if args else kwargs.get('query', '')
            
            # Compute query hash
            cache_key = compute_query_hash(query, **kwargs)
            
            # Check if result is in cache
            if cache_key in query_cache:
                cache_stats["hits"] += 1
                cached_result = query_cache[cache_key]
                print(f"🎯 CACHE HIT: Query '{query[:50]}...' (saved {cached_result['time_saved']:.2f}s)")
                cache_stats["total_time_saved"] += cached_result["time_saved"]
                return cached_result["result"]
            
            # Cache miss - execute the function
            cache_stats["misses"] += 1
            print(f"❌ CACHE MISS: Query '{query[:50]}...'")
            
            start_time = time.time()
            result = func(*args, **kwargs)
            execution_time = time.time() - start_time
            
            # Store result in cache
            query_cache[cache_key] = {
                "result": result,
                "time_saved": execution_time,
                "timestamp": time.time(),
            }
            
            print(f"💾 Cached result for '{query[:50]}...' (execution: {execution_time:.2f}s)")
            
            return result
        
        return wrapper
    return decorator


def cache_embedding(func):
    """
    Specific decorator for caching embeddings.
    Embeddings are expensive to compute, so longer cache TTL.
    """
    @wraps(func)
    def wrapper(text: str, *args, **kwargs):
        if not CACHE_ENABLED:
            return func(text, *args, **kwargs)
        
        # Hash the text for cache key
        text_hash = hashlib.sha256(text.encode()).hexdigest()[:16]
        
        if text_hash in embedding_cache:
            print(f"🎯 EMBEDDING CACHE HIT: '{text[:30]}...'")
            return embedding_cache[text_hash]
        
        print(f"❌ EMBEDDING CACHE MISS: '{text[:30]}...'")
        result = func(text, *args, **kwargs)
        embedding_cache[text_hash] = result
        
        return result
    
    return wrapper


def get_cache_stats() -> Dict[str, Any]:
    """Returns cache statistics"""
    total_requests = cache_stats["hits"] + cache_stats["misses"]
    hit_rate = (cache_stats["hits"] / total_requests * 100) if total_requests > 0 else 0
    
    return {
        "enabled": CACHE_ENABLED,
        "hits": cache_stats["hits"],
        "misses": cache_stats["misses"],
        "total_requests": total_requests,
        "hit_rate_percent": round(hit_rate, 2),
        "total_time_saved_seconds": round(cache_stats["total_time_saved"], 2),
        "cache_size": len(query_cache),
        "embedding_cache_size": len(embedding_cache),
        "max_size": CACHE_MAX_SIZE,
        "ttl_seconds": CACHE_TTL,
    }


def clear_cache():
    """Clears all caches"""
    query_cache.clear()
    embedding_cache.clear()
    cache_stats["hits"] = 0
    cache_stats["misses"] = 0
    cache_stats["total_time_saved"] = 0.0
    print("🧹 Cache cleared")


def warm_cache(common_queries: List[str], retrieval_function):
    """
    Pre-loads the cache with common queries.
    Useful at startup to improve initial performance.
    """
    print(f"🔥 Warming cache with {len(common_queries)} common queries...")
    
    for query in common_queries:
        try:
            retrieval_function(query)
            print(f"  ✅ Cached: '{query[:50]}...'")
        except Exception as e:
            print(f"  ❌ Failed to cache '{query[:50]}...': {e}")
    print(f"🔥 Cache warmed! {len(query_cache)} entries cached")


# Redis cache (optional, for distributed cache)
class RedisCache:
    """
    Redis cache for distributed environments.
    Allows sharing cache between multiple instances.
    """
    def __init__(self):
        self.enabled = os.getenv("REDIS_ENABLED", "false").lower() == "true"
        self.client = None
        
        if self.enabled:
            try:
                import redis
                redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
                self.client = redis.from_url(redis_url)
                print(f"✅ Redis cache connected: {redis_url}")
            except Exception as e:
                print(f"⚠️  Redis cache disabled: {e}")
                self.enabled = False
    
    def get(self, key: str) -> Optional[Any]:
        if not self.enabled or not self.client:
            return None
        
        try:
            data = self.client.get(key)
            if data:
                return json.loads(data)
        except Exception as e:
            print(f"Redis get error: {e}")
        
        return None
    
    def set(self, key: str, value: Any, ttl: int = 3600):
        if not self.enabled or not self.client:
            return
        
        try:
            self.client.setex(key, ttl, json.dumps(value))
        except Exception as e:
            print(f"Redis set error: {e}")
    
    def clear(self):
        if self.enabled and self.client:
            self.client.flushdb()
            print("🧹 Redis cache cleared")


# Instance globale du cache Redis (optionnel)
redis_cache = RedisCache()


if __name__ == "__main__":
    # Test du cache
    @cache_query_result(ttl=60)
    def slow_function(query: str):
        import time
        time.sleep(2)  # Simule une opération lente
        return f"Result for: {query}"
    
    # Premier appel - cache miss
    result1 = slow_function("test query")
    print(result1)
    
    # Deuxième appel - cache hit (instantané)
    result2 = slow_function("test query")
    print(result2)
    
    # Stats
    print("\nCache Stats:")
    print(json.dumps(get_cache_stats(), indent=2))
