#!/usr/bin/env python3
"""
Script de diagnostic pour vérifier l'état de la base Supabase
"""
import os
from dotenv import load_dotenv
from huggingsmolagent.tools.supabase_store import supabase

load_dotenv()

def check_supabase_connection():
    """Vérifie la connexion à Supabase"""
    print("=" * 60)
    print("DIAGNOSTIC SUPABASE")
    print("=" * 60)
    
    # 1. Vérifier la connexion
    print("\n1. Test de connexion...")
    try:
        # Essayer de lire la table documents
        response = supabase.table("documents").select("id", count="exact").limit(1).execute()
        print(f"✅ Connexion OK - Table 'documents' existe")
        print(f"   Nombre total de documents: {response.count}")
    except Exception as e:
        print(f"❌ Erreur de connexion: {e}")
        return
    
    # 2. Vérifier les documents récents
    print("\n2. Documents récents...")
    try:
        response = supabase.table("documents").select("id, metadata").order("id", desc=True).limit(5).execute()
        print(f"   Trouvé {len(response.data)} documents récents:")
        for doc in response.data:
            metadata = doc.get("metadata", {})
            doc_id = metadata.get("doc_id", "N/A")
            filename = metadata.get("filename", "N/A")
            print(f"   - ID: {doc['id'][:8]}... | doc_id: {doc_id[:8] if doc_id != 'N/A' else 'N/A'}... | filename: {filename}")
    except Exception as e:
        print(f"❌ Erreur lecture documents: {e}")
    
    # 3. Vérifier la fonction RPC match_documents
    print("\n3. Test de la fonction RPC match_documents...")
    try:
        # Créer un embedding de test (vecteur de 1024 dimensions rempli de 0.1)
        test_embedding = [0.1] * 1024
        
        # Test avec la nouvelle signature (compatible LangChain)
        response = supabase.rpc(
            "match_documents",
            {
                "query_embedding": test_embedding,
                "filter": {}  # Nouvelle signature
            }
        ).limit(5).execute()
        
        print(f"✅ Fonction RPC 'match_documents' existe et fonctionne (nouvelle signature)")
        print(f"   Résultats retournés: {len(response.data)}")
        
        if response.data:
            print(f"   Premier résultat:")
            first = response.data[0]
            print(f"   - ID: {first.get('id', 'N/A')[:8]}...")
            print(f"   - Similarity: {first.get('similarity', 'N/A')}")
            metadata = first.get('metadata', {})
            print(f"   - doc_id: {metadata.get('doc_id', 'N/A')[:8] if metadata.get('doc_id') else 'N/A'}...")
            print(f"   - filename: {metadata.get('filename', 'N/A')}")
        else:
            print("   ⚠️  Aucun résultat retourné (base vide ou seuil trop élevé)")
            
    except Exception as e:
        print(f"❌ Erreur fonction RPC: {e}")
        print(f"   Type d'erreur: {type(e).__name__}")
        print("\n   💡 Solution: Vous devez créer la fonction match_documents dans Supabase")
        print("   Voir ARCHITECTURE.md pour le SQL à exécuter")
    
    # 4. Vérifier un doc_id spécifique si fourni
    print("\n4. Test de recherche par doc_id...")
    try:
        # Récupérer un doc_id existant
        response = supabase.table("documents").select("metadata").limit(1).execute()
        if response.data:
            test_doc_id = response.data[0].get("metadata", {}).get("doc_id")
            if test_doc_id:
                print(f"   Test avec doc_id: {test_doc_id[:8]}...")
                
                # Compter les chunks avec ce doc_id
                response = supabase.table("documents").select("id", count="exact").eq("metadata->>doc_id", test_doc_id).execute()
                print(f"   ✅ Trouvé {response.count} chunks avec ce doc_id")
            else:
                print("   ⚠️  Aucun doc_id trouvé dans les métadonnées")
        else:
            print("   ⚠️  Aucun document dans la base")
    except Exception as e:
        print(f"   ❌ Erreur: {e}")
    
    print("\n" + "=" * 60)
    print("FIN DU DIAGNOSTIC")
    print("=" * 60)

if __name__ == "__main__":
    check_supabase_connection()
