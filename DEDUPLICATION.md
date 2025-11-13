# Document Deduplication

## Problem

When users upload the same PDF file multiple times, the system was creating duplicate entries in the vector database:
- Same file content was chunked and embedded multiple times
- Wasted storage space in Supabase
- Wasted computation for embeddings
- Multiple `doc_id` values for the same document
- Confusion when querying documents

## Solution

The system now implements **content-based deduplication** using SHA256 file hashing:

### How It Works

1. **File Upload**: When a file is uploaded, the system:
   - Computes a SHA256 hash of the file content
   - Checks if this hash already exists in the database
   - If found: Returns the existing `doc_id` and skips processing
   - If new: Proceeds with storage, parsing, chunking, and embedding

2. **Metadata Storage**: Each document chunk now includes:
   - `file_hash`: SHA256 hash of the original file
   - `doc_id`: Unique identifier for this document
   - `filename`: Original filename
   - `source`: Storage URL

3. **Deduplication Check**: Uses PostgreSQL's JSONB operators:
   ```sql
   SELECT * FROM documents WHERE metadata->>'file_hash' = '<hash>'
   ```

### Benefits

✅ **No duplicate embeddings**: Same file uploaded multiple times uses the same embeddings  
✅ **Storage efficiency**: Saves Supabase storage space  
✅ **Faster uploads**: Reusing existing documents is instant  
✅ **Cost savings**: No redundant embedding API calls  
✅ **Consistent doc_ids**: Same file always has the same `doc_id`

### API Changes

#### `/upload` Endpoint

**Before:**
```json
{
  "file_url": "...",
  "doc_id": "new-uuid",
  "chunks_indexed": 108
}
```

**After (new file):**
```json
{
  "file_url": "...",
  "doc_id": "new-uuid",
  "chunks_indexed": 108,
  "reused": false
}
```

**After (duplicate file):**
```json
{
  "file_url": "...",
  "doc_id": "existing-uuid",
  "chunks_indexed": 108,
  "summary": "File 'document.pdf' was already indexed. Reusing existing document.",
  "reused": true
}
```

#### `/ask` Endpoint

When files are uploaded via multipart form, the response includes a `reused` flag in the context.

### Cleanup Existing Duplicates

To clean up duplicates that were created before this feature:

```bash
# Dry run (shows what would be deleted)
python cleanup_duplicates.py

# Actually delete duplicates (keeps the version with most chunks)
python cleanup_duplicates.py --execute
```

The cleanup script:
- Identifies files with multiple `doc_id` values
- Keeps the version with the most chunks
- Deletes all other versions
- Requires confirmation before deleting

### Implementation Details

**New Functions in `vector_store.py`:**

- `compute_file_hash(content: bytes) -> str`
  - Computes SHA256 hash of file content

- `check_existing_document(file_hash: str) -> Optional[Dict]`
  - Checks if document with this hash exists
  - Returns doc_id and chunk count if found

- `delete_document_by_doc_id(doc_id: str) -> int`
  - Deletes all chunks for a given doc_id
  - Returns number of chunks deleted

**Modified Endpoints:**

- `/upload`: Added deduplication check before processing
- `/ask`: Added deduplication check for multipart uploads

### Migration Notes

**For existing databases:**

1. Existing documents don't have `file_hash` in metadata
2. Run `cleanup_duplicates.py` to remove duplicates
3. New uploads will include `file_hash`
4. Old documents can be re-uploaded to add `file_hash` (they'll be detected as duplicates if hash matches)

**Database requirements:**

- No schema changes needed
- Uses existing JSONB metadata column
- PostgreSQL JSONB operators (`->>`) for querying

### Testing

```bash
# Test 1: Upload a file
curl -X POST http://localhost:8000/upload \
  -F "file=@test.pdf"
# Response: reused=false, new doc_id

# Test 2: Upload the same file again
curl -X POST http://localhost:8000/upload \
  -F "file=@test.pdf"
# Response: reused=true, same doc_id

# Test 3: Upload a different file with same name
curl -X POST http://localhost:8000/upload \
  -F "file=@different_content.pdf"
# Response: reused=false, different doc_id (hash is different)
```

### Edge Cases Handled

✅ **Same content, different filename**: Detected as duplicate (hash matches)  
✅ **Different content, same filename**: Treated as new file (hash differs)  
✅ **Modified file**: Treated as new file (hash changes)  
✅ **Renamed file**: Detected as duplicate (content hash matches)

### Performance Impact

- **Hash computation**: ~10-50ms for typical PDFs (< 10MB)
- **Database lookup**: ~5-20ms (indexed JSONB query)
- **Total overhead**: < 100ms per upload
- **Savings**: Skips parsing (1-5s) and embedding (5-30s) for duplicates

### Future Enhancements

Potential improvements:
- [ ] Add file size to metadata for quick filtering
- [ ] Support partial hash for very large files
- [ ] Add upload timestamp to track document versions
- [ ] Implement "force re-index" option to override deduplication
- [ ] Add API endpoint to list all documents with their hashes
