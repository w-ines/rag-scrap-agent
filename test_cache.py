#!/usr/bin/env python3
"""
Script de test pour le cache des queries
Démontre l'amélioration de performance
"""

import time
import requests
import json
from typing import Dict, Any

BASE_URL = "http://localhost:8000"


def test_query(query: str, description: str = "") -> Dict[str, Any]:
    """Teste une query et mesure le temps"""
    print(f"\n{'='*60}")
    print(f"🧪 Test: {description or query}")
    print(f"{'='*60}")
    
    start_time = time.time()
    
    try:
        response = requests.post(
            f"{BASE_URL}/ask",
            json={"query": query},
            timeout=60
        )
        
        elapsed = time.time() - start_time
        
        if response.status_code == 200:
            print(f"✅ Success in {elapsed:.3f}s")
            return {
                "success": True,
                "time": elapsed,
                "query": query
            }
        else:
            print(f"❌ Error: {response.status_code}")
            return {
                "success": False,
                "time": elapsed,
                "query": query
            }
    
    except Exception as e:
        elapsed = time.time() - start_time
        print(f"❌ Exception: {e}")
        return {
            "success": False,
            "time": elapsed,
            "query": query,
            "error": str(e)
        }


def get_cache_stats() -> Dict[str, Any]:
    """Récupère les statistiques du cache"""
    try:
        response = requests.get(f"{BASE_URL}/cache/stats")
        if response.status_code == 200:
            return response.json()
        return {}
    except:
        return {}


def clear_cache():
    """Vide le cache"""
    try:
        response = requests.post(f"{BASE_URL}/cache/clear")
        if response.status_code == 200:
            print("🧹 Cache cleared")
            return True
    except:
        pass
    return False


def print_cache_stats(stats: Dict[str, Any]):
    """Affiche les stats du cache de manière lisible"""
    if not stats:
        print("⚠️  Cache stats not available")
        return
    
    print(f"\n{'='*60}")
    print("📊 CACHE STATISTICS")
    print(f"{'='*60}")
    print(f"Enabled:           {stats.get('enabled', False)}")
    print(f"Total Requests:    {stats.get('total_requests', 0)}")
    print(f"Cache Hits:        {stats.get('hits', 0)}")
    print(f"Cache Misses:      {stats.get('misses', 0)}")
    print(f"Hit Rate:          {stats.get('hit_rate_percent', 0):.2f}%")
    print(f"Time Saved:        {stats.get('total_time_saved_seconds', 0):.2f}s")
    print(f"Cache Size:        {stats.get('cache_size', 0)}/{stats.get('max_size', 0)}")
    print(f"TTL:               {stats.get('ttl_seconds', 0)}s")
    print(f"{'='*60}")


def run_cache_demo():
    """Démo complète du cache"""
    print("\n" + "="*60)
    print("🚀 CACHE PERFORMANCE DEMO")
    print("="*60)
    
    # Vider le cache pour commencer propre
    clear_cache()
    
    # Test 1: Première query (cache miss)
    print("\n📍 Phase 1: Cache Miss (première fois)")
    result1 = test_query(
        "Résume ce document",
        "Première query - devrait être lente"
    )
    
    # Attendre un peu
    time.sleep(1)
    
    # Test 2: Même query (cache hit)
    print("\n📍 Phase 2: Cache Hit (deuxième fois)")
    result2 = test_query(
        "Résume ce document",
        "Même query - devrait être instantanée"
    )
    
    # Calculer le gain
    if result1["success"] and result2["success"]:
        speedup = result1["time"] / result2["time"] if result2["time"] > 0 else 0
        time_saved = result1["time"] - result2["time"]
        
        print(f"\n{'='*60}")
        print("📈 PERFORMANCE COMPARISON")
        print(f"{'='*60}")
        print(f"First query (cache miss):  {result1['time']:.3f}s")
        print(f"Second query (cache hit):  {result2['time']:.3f}s")
        print(f"Time saved:                {time_saved:.3f}s")
        print(f"Speedup:                   {speedup:.0f}x faster ⚡")
        print(f"{'='*60}")
    
    # Afficher les stats du cache
    stats = get_cache_stats()
    print_cache_stats(stats)
    
    # Test 3: Query différente (cache miss)
    print("\n📍 Phase 3: Nouvelle query (cache miss)")
    result3 = test_query(
        "Quels sont les points principaux?",
        "Nouvelle query - devrait être lente"
    )
    
    # Test 4: Répéter la nouvelle query (cache hit)
    print("\n📍 Phase 4: Répéter nouvelle query (cache hit)")
    result4 = test_query(
        "Quels sont les points principaux?",
        "Répétition - devrait être instantanée"
    )
    
    # Stats finales
    final_stats = get_cache_stats()
    print_cache_stats(final_stats)
    
    # Résumé
    print(f"\n{'='*60}")
    print("✅ DEMO COMPLETE")
    print(f"{'='*60}")
    print(f"Total queries:     4")
    print(f"Cache hits:        {final_stats.get('hits', 0)}")
    print(f"Cache misses:      {final_stats.get('misses', 0)}")
    print(f"Hit rate:          {final_stats.get('hit_rate_percent', 0):.2f}%")
    print(f"Total time saved:  {final_stats.get('total_time_saved_seconds', 0):.2f}s")
    print(f"{'='*60}\n")


def run_stress_test(query: str = "Test query", iterations: int = 10):
    """Test de stress avec queries répétées"""
    print(f"\n{'='*60}")
    print(f"🔥 STRESS TEST: {iterations} iterations")
    print(f"{'='*60}")
    
    clear_cache()
    
    times = []
    
    for i in range(iterations):
        print(f"\nIteration {i+1}/{iterations}...")
        result = test_query(query, f"Iteration {i+1}")
        if result["success"]:
            times.append(result["time"])
    
    if times:
        print(f"\n{'='*60}")
        print("📊 STRESS TEST RESULTS")
        print(f"{'='*60}")
        print(f"First query:       {times[0]:.3f}s (cache miss)")
        print(f"Average (2-{iterations}):   {sum(times[1:])/len(times[1:]):.3f}s (cache hits)")
        print(f"Total time:        {sum(times):.3f}s")
        print(f"Time saved:        {times[0] * (iterations-1) - sum(times[1:]):.3f}s")
        print(f"{'='*60}\n")
    
    # Stats finales
    final_stats = get_cache_stats()
    print_cache_stats(final_stats)


if __name__ == "__main__":
    import sys
    
    print("\n🎯 Cache Performance Test Suite")
    print("Make sure the server is running on http://localhost:8000\n")
    
    # Vérifier que le serveur est accessible
    try:
        response = requests.get(f"{BASE_URL}/health", timeout=5)
        if response.status_code != 200:
            print("❌ Server not responding. Start with: python main.py")
            sys.exit(1)
    except:
        print("❌ Cannot connect to server. Start with: python main.py")
        sys.exit(1)
    
    print("✅ Server is running\n")
    
    # Menu
    print("Select test:")
    print("1. Full demo (recommended)")
    print("2. Stress test (10 iterations)")
    print("3. View cache stats")
    print("4. Clear cache")
    
    choice = input("\nChoice (1-4): ").strip()
    
    if choice == "1":
        run_cache_demo()
    elif choice == "2":
        run_stress_test()
    elif choice == "3":
        stats = get_cache_stats()
        print_cache_stats(stats)
    elif choice == "4":
        clear_cache()
        stats = get_cache_stats()
        print_cache_stats(stats)
    else:
        print("Invalid choice")
