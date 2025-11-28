#!/usr/bin/env python3
"""
Script de test pour vérifier le streaming des steps de l'agent.
Envoie une requête simple et affiche les steps en temps réel.
"""

import httpx
import json
import time

API_URL = "http://localhost:8000/ask"

def test_streaming():
    """Test le streaming avec une requête simple"""
    
    print("=" * 60)
    print("TEST: Streaming des steps de l'agent")
    print("=" * 60)
    
    query = "What is the weather in Paris?"
    
    print(f"\n📤 Envoi de la requête: '{query}'")
    print(f"⏱️  Timestamp: {time.strftime('%H:%M:%S')}\n")
    
    start_time = time.time()
    step_count = 0
    
    try:
        with httpx.Client(timeout=120.0) as client:
            with client.stream(
                "POST",
                API_URL,
                json={"query": query},
                headers={"Content-Type": "application/json"}
            ) as response:
                
                if response.status_code != 200:
                    print(f"❌ Erreur: Status {response.status_code}")
                    print(response.text)
                    return
                
                print("✅ Connexion établie, lecture du stream...\n")
                print("-" * 60)
                
                for line in response.iter_lines():
                    if not line.strip():
                        continue
                    
                    # Parse SSE format
                    if line.startswith("data: "):
                        json_str = line[6:]  # Remove "data: " prefix
                        
                        try:
                            data = json.loads(json_str)
                            elapsed = time.time() - start_time
                            
                            # Afficher les steps
                            if data.get("steps"):
                                for step in data["steps"]:
                                    step_count += 1
                                    print(f"[{elapsed:6.1f}s] Step {step_count}: {step}")
                            
                            # Afficher la réponse finale
                            if data.get("response"):
                                print(f"\n{'=' * 60}")
                                print(f"[{elapsed:6.1f}s] 📝 RÉPONSE FINALE:")
                                print(f"{'=' * 60}")
                                print(data["response"])
                                print(f"{'=' * 60}\n")
                            
                            # Afficher les erreurs
                            if data.get("error"):
                                print(f"\n❌ ERREUR: {data['error']}\n")
                        
                        except json.JSONDecodeError as e:
                            print(f"⚠️  Erreur de parsing JSON: {e}")
                            print(f"   Ligne: {json_str[:100]}...")
    
    except httpx.TimeoutException:
        print("\n⏱️  Timeout - La requête a pris trop de temps")
    except Exception as e:
        print(f"\n❌ Erreur: {e}")
        import traceback
        traceback.print_exc()
    
    finally:
        total_time = time.time() - start_time
        print(f"\n{'=' * 60}")
        print(f"📊 STATISTIQUES:")
        print(f"   - Temps total: {total_time:.1f}s")
        print(f"   - Steps reçus: {step_count}")
        print(f"   - Timestamp fin: {time.strftime('%H:%M:%S')}")
        print(f"{'=' * 60}\n")

if __name__ == "__main__":
    test_streaming()
