# 📝 Changes Log - Vector Search Fix

## Date: 2025-11-13

## Problem
`retrieve_knowledge()` was returning 0 chunks even with correct `doc_id` parameter.

## Root Cause
Supabase RPC function `match_documents` had incompatible signature with LangChain:
- **LangChain expects**: `match_documents(query_embedding, filter)`
- **Supabase had**: `match_documents(query_embedding, match_count, filter)`

## Changes Made

### 1. Code Changes

#### `huggingsmolagent/tools/vector_store.py`

**Added debug logging** (lines 151-179):
```python
# Log exceptions in similarity search
except Exception as e:
    print(f"[retrieve_knowledge] similarity_search_with_score failed: {e}")
    print(f"[retrieve_knowledge] Falling back to similarity_search without scores")

# Log search results
print(f"[retrieve_knowledge] Query: '{query}' | Requested k={top_k * 3 if doc_id else top_k}")
print(f"[retrieve_knowledge] Retrieved {len(docs)} documents from vector store")

# Log filtering process
if doc_id:
    print(f"[retrieve_knowledge] Filtering by doc_id='{doc_id}'")
    # Show all doc_ids found
    found_doc_ids = set()
    for doc in docs:
        if doc.metadata:
            found_doc_ids.add(doc.metadata.get("doc_id", "NO_DOC_ID"))
    print(f"[retrieve_knowledge] Found doc_ids in results: {found_doc_ids}")
    # ... filtering logic ...
    print(f"[retrieve_knowledge] After filtering: {len(filtered_docs)} documents match doc_id")
```

### 2. New Files Created

#### `debug_supabase.py`
Diagnostic script to check:
- Supabase connection
- Documents count and structure
- RPC function signature
- doc_id filtering

**Usage**: `../venv/bin/python debug_supabase.py`

#### `fix_match_documents.sql` ⭐ **MUST EXECUTE**
SQL script to fix the RPC function signature:
```sql
CREATE OR REPLACE FUNCTION match_documents(
    query_embedding VECTOR(1024),
    filter JSONB DEFAULT '{}'::jsonb
)
RETURNS TABLE (...)
```

**Action Required**: Execute this in Supabase SQL Editor

#### `supabase_setup.sql`
Complete setup reference including:
- Table creation
- Index creation
- RPC function
- Permissions

#### `FIX_VECTOR_SEARCH.md`
Detailed documentation with:
- Problem description
- Step-by-step solution
- Verification steps
- Troubleshooting

#### `quick_fix.sh`
Quick reference guide displayed in terminal

#### `CHANGES.md`
This file - change log

## Impact

### Before Fix
```
[retrieve_knowledge] Retrieved 0 documents from vector store
Retrieved 0 chunks
```

### After Fix (Expected)
```
[retrieve_knowledge] Query: 'document summary' | Requested k=60
[retrieve_knowledge] Retrieved 60 documents from vector store
[retrieve_knowledge] Filtering by doc_id='479c26d1-c564-4e72-b84e-b834c1bcfc58'
[retrieve_knowledge] Found doc_ids in results: {'479c26d1-c564-4e72-b84e-b834c1bcfc58', ...}
[retrieve_knowledge] After filtering: 20 documents match doc_id
Retrieved 20 chunks
```

## Testing

### 1. Run Diagnostic
```bash
../venv/bin/python debug_supabase.py
```

Expected output:
```
✅ Connexion OK - Table 'documents' existe
   Nombre total de documents: 773
✅ Fonction RPC 'match_documents' existe et fonctionne
   Résultats retournés: 5
```

### 2. Test with Application
```bash
# Start server
../venv/bin/python main.py

# Upload PDF and ask question
# Check logs for [retrieve_knowledge] messages
```

## Rollback

If needed, restore old function:
```sql
CREATE OR REPLACE FUNCTION match_documents(
    query_embedding VECTOR(1024),
    match_count INT DEFAULT 5,
    filter JSONB DEFAULT '{}'::jsonb
)
-- ... old implementation
```

## Related Files Modified
- `huggingsmolagent/tools/vector_store.py` (debug logs added)

## Related Files Created
- `debug_supabase.py`
- `fix_match_documents.sql` ⭐
- `supabase_setup.sql`
- `FIX_VECTOR_SEARCH.md`
- `quick_fix.sh`
- `CHANGES.md`

## Next Steps
1. ✅ Execute `fix_match_documents.sql` in Supabase
2. ✅ Run `debug_supabase.py` to verify
3. ✅ Test with real PDF upload
4. ✅ Monitor `[retrieve_knowledge]` logs
5. ✅ Confirm agent receives chunks and answers questions

## Notes
- Debug logs can be removed once issue is confirmed fixed
- Keep diagnostic scripts for future troubleshooting
- The fix aligns with LangChain's expected RPC signature
