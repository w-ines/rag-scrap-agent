# 🔧 Fix pour retrieve_knowledge() retournant 0 chunks

## 🎯 Problème identifié

La fonction `retrieve_knowledge()` retourne toujours 0 chunks car la fonction RPC Supabase `match_documents` a une **signature incompatible** avec LangChain.

### Diagnostic effectué

```bash
../venv/bin/python debug_supabase.py
```

**Résultat** :
- ✅ Connexion Supabase OK (773 documents)
- ✅ Documents avec doc_id présents
- ❌ Fonction `match_documents` existe mais avec mauvaise signature
  - **Attendu par LangChain** : `match_documents(query_embedding, filter)`
  - **Trouvé** : `match_documents(query_embedding, match_count, filter)`

## 🛠️ Solution

### Étape 1 : Exécuter le SQL de correction

1. Ouvrez votre **Supabase SQL Editor**
2. Copiez-collez le contenu de `fix_match_documents.sql`
3. Exécutez le script

```sql
-- Le script va :
-- 1. Supprimer l'ancienne fonction
-- 2. Créer la nouvelle avec la bonne signature
-- 3. Tester que ça fonctionne
```

### Étape 2 : Vérifier que ça fonctionne

Relancez le diagnostic :

```bash
../venv/bin/python debug_supabase.py
```

Vous devriez voir :
```
✅ Fonction RPC 'match_documents' existe et fonctionne
   Résultats retournés: 5
```

### Étape 3 : Tester avec votre application

Relancez votre serveur et testez l'upload + question :

```bash
../venv/bin/python main.py
```

Les logs devraient maintenant afficher :
```
[retrieve_knowledge] Query: 'document summary' | Requested k=60
[retrieve_knowledge] Retrieved 60 documents from vector store
[retrieve_knowledge] Filtering by doc_id='479c26d1-c564-4e72-b84e-b834c1bcfc58'
[retrieve_knowledge] Found doc_ids in results: {'479c26d1-c564-4e72-b84e-b834c1bcfc58', ...}
[retrieve_knowledge] After filtering: 20 documents match doc_id
```

## 📊 Logs de débogage ajoutés

Le code `vector_store.py` a été modifié pour ajouter des logs détaillés :

```python
# Logs ajoutés dans retrieve_knowledge()
print(f"[retrieve_knowledge] Query: '{query}' | Requested k={top_k * 3 if doc_id else top_k}")
print(f"[retrieve_knowledge] Retrieved {len(docs)} documents from vector store")
print(f"[retrieve_knowledge] Filtering by doc_id='{doc_id}'")
print(f"[retrieve_knowledge] Found doc_ids in results: {found_doc_ids}")
print(f"[retrieve_knowledge] After filtering: {len(filtered_docs)} documents match doc_id")
```

Ces logs vous permettront de diagnostiquer tout problème futur.

## 🔍 Pourquoi ça ne marchait pas ?

1. **LangChain** appelle : `supabase.rpc("match_documents", {query_embedding, filter}).params.set("limit", k)`
2. **Votre fonction** attendait : `match_documents(query_embedding, match_count, filter)`
3. **Résultat** : Erreur 404 ou signature mismatch → 0 résultats

La nouvelle fonction accepte seulement `query_embedding` et `filter`, et laisse PostgREST gérer le `limit` via les paramètres de requête.

## 📝 Fichiers créés

- `debug_supabase.py` : Script de diagnostic
- `fix_match_documents.sql` : SQL de correction
- `supabase_setup.sql` : Setup complet (pour référence)
- `FIX_VECTOR_SEARCH.md` : Ce fichier

## ✅ Checklist

- [ ] Exécuter `fix_match_documents.sql` dans Supabase
- [ ] Vérifier avec `debug_supabase.py`
- [ ] Tester l'upload + question
- [ ] Vérifier les logs `[retrieve_knowledge]`
- [ ] Confirmer que l'agent reçoit des chunks

## 🚀 Prochaines étapes

Une fois corrigé, l'agent devrait pouvoir :
1. ✅ Récupérer les chunks du document uploadé
2. ✅ Filtrer par doc_id correctement
3. ✅ Répondre aux questions sur le contenu
