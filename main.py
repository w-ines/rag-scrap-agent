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
from huggingsmolagent.tools.vector_store import index_documents, retrieve_knowledge
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
                    file_url = store_pdf(f)
                    print(f"[ask] stored file_url={file_url}")
                    documents = parse_pdf(f)
                    print(f"[ask] parsed documents_count={len(documents) if isinstance(documents, list) else 'n/a'}")
                    doc_id = str(uuid.uuid4())
                    stored = index_documents(
                        documents,
                        base_metadata={"source": file_url, "filename": f.filename, "doc_id": doc_id},
                    )
                    print(f"[ask] indexed doc_id={doc_id} stored={stored}")
                    uploaded_context.append({
                        "filename": f.filename,
                        "doc_id": doc_id,
                        "chunks": stored
                    })
                print(f"[ask] upload complete. {len(uploaded_context)} file(s) indexed")
        else:
            # JSON body
            body = await request.json() if is_json else {}
            query = (body.get("query") if isinstance(body, dict) else None) or ""
            print(f"[ask] json body parsed query='{query[:120] if query else ''}'")

        if not query:
            return JSONResponse({"answer": "Please provide a query."}, status_code=400)

        # STEP 2: Delegate all reasoning to smolagent
        print(f"[ask] delegating to smolagent with query='{query[:100]}'")
        print(f"[ask] uploaded_files_context={len(uploaded_context)} files")
        
        # Build context message for smolagent
        context_msg = ""
        if uploaded_context:
            filenames = [ctx["filename"] for ctx in uploaded_context]
            context_msg = f"\n[Context: User just uploaded {len(uploaded_context)} file(s): {', '.join(filenames)}]"
        
        # Call smolagent via internal HTTP (you could also import and call directly)
        # For now, we'll use a simple approach: import the agent and call it
        from huggingsmolagent.agent import run_agent_sync
        
        agent_query = query + context_msg if context_msg else query
        print(f"[ask] calling agent with: '{agent_query[:150]}'")
        
        result = run_agent_sync(agent_query)
        print(f"[ask] smolagent response received length={len(str(result)) if result else 0}")
        
        return JSONResponse({"answer": result or "No response from agent"})
        
    except Exception as e:
        print("[ask] error:", e)
        import traceback
        traceback.print_exc()
        return JSONResponse({"answer": "Error processing request.", "error": str(e)}, status_code=500)





@app.post("/upload")
async def upload_pdf(file: UploadFile = File(...)):
    print("[upload] /upload called")
    print ("filename", file.filename, "content_type" ,file.content_type)

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
        base_metadata={"source": file_url, "filename": file.filename, "doc_id": doc_id},
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