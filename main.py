from fastapi import FastAPI, UploadFile, File, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import os
from dotenv import load_dotenv
import httpx
import uuid
from huggingsmolagent.agent import app as smolagent_router
from huggingsmolagent.tools.supabase_store import store_pdf
from huggingsmolagent.tools.pdf_loader import parse_pdf
from huggingsmolagent.tools.vector_store import (
    index_documents, 
    retrieve_knowledge, 
    compute_file_hash, 
    check_existing_document
)
from huggingsmolagent.tools.summarizer import summarize
from huggingsmolagent.tools.scraper import web_search
from pydantic import BaseModel

load_dotenv() 
app = FastAPI()
print("[startup] FastAPI app initialized")

# CORS for frontend imports/uploads
cors_origins = os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in cors_origins if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class Query(BaseModel):
    question: str

# Add health check before mounting
@app.get("/health")
async def health_check():
    print("[health] /health called")
    return {"status": "ok"}


# Cache stats endpoint
@app.get("/cache/stats")
async def cache_stats():
    """Returns query cache statistics"""
    try:
        from huggingsmolagent.tools.query_cache import get_cache_stats
        stats = get_cache_stats()
        return stats
    except ImportError:
        return {"error": "Cache not available", "enabled": False}


@app.post("/cache/clear")
async def clear_cache():
    """Clears the query cache"""
    try:
        from huggingsmolagent.tools.query_cache import clear_cache
        clear_cache()
        return {"status": "cache cleared", "success": True}
    except ImportError:
        return {"error": "Cache not available", "success": False}


"""
Mount the smolagent streaming app at /agent (POST /agent/) to keep SSE streaming.
We implement a unified /ask below that returns JSON and orchestrates upload/summarize/RAG/scrape.
"""
app.mount("/agent", smolagent_router)


@app.post("/ask")
async def ask(request: Request):
    """
    Unified Ask endpoint:
    - If files are attached: upload+index them first, then pass query to smolagent
    - If no files: directly pass query to smolagent
    
    smolagent decides which tools to use (RAG, summarize, scrape) based on the query context.
    Returns JSON response from smolagent.
    """
    content_type = request.headers.get("content-type", "")
    is_multipart = "multipart/form-data" in content_type
    is_json = "application/json" in content_type
    print(f"[ask] called content_type={content_type} is_multipart={is_multipart} is_json={is_json}")

    try:
        query = ""
        uploaded_context = []  # Track uploaded files for context
        
        # STEP 1: Handle file uploads if present
        if is_multipart:
            form = await request.form()
            query = (form.get("query") or "").strip()
            files = form.getlist("files")
            print(f"[ask] multipart received query='{query[:80] if query else ''}' files_count={len(files) if files else 0}")

            if files:
                # Process uploads: store, parse, index
                print(f"[ask] processing {len(files)} file(s)")
                for f in files:
                    print(f"[ask] processing file name={getattr(f, 'filename', None)}")
                    
                    # Read file content for hashing
                    file_content = await f.read()
                    await f.seek(0)  # Reset file pointer for subsequent reads
                    
                    # Compute file hash for deduplication
                    file_hash = compute_file_hash(file_content)
                    print(f"[ask] computed file_hash={file_hash[:16]}...")
                    
                    # Check if this file already exists
                    existing = check_existing_document(file_hash)
                    
                    if existing:
                        print(f"[ask] ⚠️  File already indexed! doc_id={existing['doc_id']}, chunks={existing['chunk_count']}")
                        uploaded_context.append({
                            "filename": f.filename,
                            "doc_id": existing["doc_id"],
                            "chunks": existing["chunk_count"],
                            "reused": True
                        })
                        continue
                    
                    # New file - proceed with storage and indexing
                    file_url = store_pdf(f)
                    print(f"[ask] stored file_url={file_url}")
                    documents = parse_pdf(f)
                    print(f"[ask] parsed documents_count={len(documents) if isinstance(documents, list) else 'n/a'}")
                    doc_id = str(uuid.uuid4())
                    stored = index_documents(
                        documents,
                        base_metadata={
                            "source": file_url, 
                            "filename": f.filename, 
                            "doc_id": doc_id,
                            "file_hash": file_hash
                        },
                    )
                    print(f"[ask] indexed doc_id={doc_id} stored={stored}")
                    uploaded_context.append({
                        "filename": f.filename,
                        "doc_id": doc_id,
                        "chunks": stored,
                        "reused": False
                    })
                print(f"[ask] upload complete. {len(uploaded_context)} file(s) processed")
        else:
            # JSON body
            body = await request.json() if is_json else {}
            query = (body.get("query") if isinstance(body, dict) else None) or ""
            print(f"[ask] json body parsed query='{query[:120] if query else ''}'")

        if not query:
            return JSONResponse({"answer": "Please provide a query."}, status_code=400)

        # STEP 2: Delegate to smolagent with HYBRID approach
        print(f"[ask] delegating to smolagent with query='{query[:100]}'")
        print(f"[ask] uploaded_files_context={len(uploaded_context)} files")
        
        # Build enhanced context message for smolagent
        context_msg = ""
        
        if uploaded_context:
            # HYBRID APPROACH: Pre-fetch a preview to guide the agent
            # This helps the agent understand that content is available in the vector store
            print("[ask] HYBRID: Pre-fetching document preview to guide agent")
            
            try:
                # Get a small preview (3 chunks) to show the agent what's available
                preview_result = retrieve_knowledge(
                    query="document overview summary",
                    top_k=3  # Just a preview, not the full content
                )
                
                # Extract preview text (limit to 400 chars to keep prompt manageable)
                preview_text = preview_result.get('context', '')[:400]
                has_preview = bool(preview_text.strip())
                
                if has_preview:
                    print(f"[ask] HYBRID: Preview retrieved ({len(preview_text)} chars)")
                else:
                    print("[ask] HYBRID: No preview content found")
                
            except Exception as preview_error:
                print(f"[ask] HYBRID: Preview fetch failed: {preview_error}")
                has_preview = False
                preview_text = ""
            
            # Build the context message with explicit instructions for the agent
            filenames = [ctx["filename"] for ctx in uploaded_context]
            doc_ids = [ctx["doc_id"] for ctx in uploaded_context]
            total_chunks = sum(ctx["chunks"] for ctx in uploaded_context)
            
            # For single file upload, provide the doc_id
            doc_id_instruction = ""
            if len(doc_ids) == 1:
                doc_id_instruction = f'\nIMPORTANT: Use doc_id="{doc_ids[0]}" parameter to retrieve THIS specific document!'
            
            context_msg = f"""
[Context: User just uploaded {len(uploaded_context)} file(s): {', '.join(filenames)}]
[Total chunks indexed: {total_chunks}]{doc_id_instruction}

IMPORTANT INSTRUCTIONS FOR YOU (the agent):
1. The uploaded file(s) have been ALREADY indexed in the Supabase vector database
2. You MUST use the retrieve_knowledge() tool to access the document content
3. DO NOT try to use pdf_reader, file_reader, or any file I/O operations - they don't exist
4. The retrieve_knowledge() tool will return the document chunks with similarity scores

Example usage for THIS uploaded document:
<code>
result = retrieve_knowledge(query="document summary", top_k=20{f', doc_id="{doc_ids[0]}"' if len(doc_ids) == 1 else ''})
print(f"Retrieved {{len(result['results'])}} chunks")
print(result['context'][:500])  # Preview the content
</code>
"""
            
            # Add preview if available to show the agent there's real content
            if has_preview:
                context_msg += f"""
Document preview (first 400 chars from vector store):
---
{preview_text}...
---

Use retrieve_knowledge(query="...", top_k=20) to get the full document content.
"""
            else:
                context_msg += """
Note: Preview unavailable, but content is indexed. Use retrieve_knowledge() to access it.
"""
        
        # Call smolagent with streaming to show steps in real-time
        from huggingsmolagent.agent import generate_streaming_response, ComplexRequest
        from fastapi.responses import StreamingResponse
        
        agent_query = query + context_msg if context_msg else query
        print(f"[ask] calling agent with enhanced query (length={len(agent_query)})")
        print(f"[ask] query preview: '{agent_query[:200]}...'")
        
        # Create request object for streaming agent
        # If files were uploaded, mark RAG tool as selected
        selected_tools = [{"name": "rag"}] if uploaded_context else None
        
        request_data = ComplexRequest(
            toolsQuery=agent_query,
            messages=[{"role": "user", "content": query}],
            selectedTools=selected_tools,
            conversationId=None,
            chatSettings={}
        )
        
        # Return streaming response with steps
        return StreamingResponse(
            generate_streaming_response(request_data),
            media_type="application/x-ndjson",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no"
            }
        )
        
    except Exception as e:
        print("[ask] error:", e)
        import traceback
        traceback.print_exc()
        return JSONResponse({"answer": "Error processing request.", "error": str(e)}, status_code=500)





@app.post("/upload")
async def upload_pdf(file: UploadFile = File(...)):
    print("[upload] /upload called")
    print ("filename", file.filename, "content_type" ,file.content_type)

    # Read file content for hashing
    file_content = await file.read()
    await file.seek(0)  # Reset file pointer for subsequent reads
    
    # Compute file hash for deduplication
    file_hash = compute_file_hash(file_content)
    print(f"[upload] computed file_hash={file_hash[:16]}...")
    
    # Check if this file already exists
    existing = check_existing_document(file_hash)
    
    if existing:
        print(f"[upload] ⚠️  File already indexed! doc_id={existing['doc_id']}, chunks={existing['chunk_count']}")
        return {
            "file_url": existing.get("source", ""),
            "doc_id": existing["doc_id"],
            "chunks_indexed": existing["chunk_count"],
            "summary": f"File '{file.filename}' was already indexed. Reusing existing document.",
            "reused": True
        }

    # New file - proceed with storage and indexing
    # 1. Save in supabase storage
    file_url = store_pdf(file)
    print("[upload] stored file_url", file_url)
    
    # 2. Text Extraction
    documents = parse_pdf(file)
    try:
        print("[upload] documents_count", len(documents))
    except Exception:
        print("[upload] documents_count unavailable")

    # 3. Vector Supabase Indexation
    doc_id = str(uuid.uuid4())
    print("[upload] doc_id", doc_id)
    stored = index_documents(
        documents,
        base_metadata={
            "source": file_url, 
            "filename": file.filename, 
            "doc_id": doc_id,
            "file_hash": file_hash
        },
    )
    print("[upload] indexed stored=", stored)
    
    # 4. Summarization
    summary = summarize(documents)
    print("[upload] summary generated length=", (len(summary) if isinstance(summary, str) else "n/a"))
    # 5. notify n8n webhook 
    webhook_url = os.getenv("N8N_WEBHOOK_URL")
    if webhook_url:
        print("webhook_url", webhook_url)
        try:
            payload = {
                "doc_id": doc_id,
                "file_url": file_url,
                "filename": file.filename,
                "chunks_indexed": stored,
                "summary": summary,
            }
            print("payload", payload)
            
            # non-blocking fire-and-forget
            verify = os.getenv("N8N_WEBHOOK_VERIFY", "true").lower() != "false"
            with httpx.Client(timeout=5.0, verify=verify) as client:
                client.post(webhook_url, json=payload)
        except Exception as e:
            print("n8n webhook notify failed:", e)

    return {"file_url": file_url, "doc_id": doc_id, "chunks_indexed": stored, "summary": summary}


 
if __name__ == "__main__":
    import uvicorn
    print("[startup] Starting uvicorn server on 0.0.0.0:8000")
    uvicorn.run(app, host="0.0.0.0", port=8000)