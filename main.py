"""
Backend FastAPI avec Agent Unifié Agentic
Remplace les graphes séparés par un seul agent intelligent
"""

from fastapi import FastAPI, UploadFile, File, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
import os
from dotenv import load_dotenv
import uuid
import json
import time
from pydantic import BaseModel

# Import RAG processors
from rag import PDFProcessor, EmbeddingProcessor, StorageProcessor, SummarizerProcessor

# Initialize processors
storage_processor = StorageProcessor()
pdf_processor = PDFProcessor()
embedding_processor = EmbeddingProcessor()
summarizer_processor = SummarizerProcessor()

load_dotenv() 
app = FastAPI()
print("[startup] FastAPI app initialized with Unified Agent")

# CORS
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

@app.get("/health")
async def health_check():
    print("[health] /health called")
    return {"status": "ok"}


# ============================================================================
# FONCTION DE STREAMING POUR UPLOAD + RAG
# ============================================================================

async def stream_file_processing_and_rag(query: str, files: list):
    """
    Streaming pour l'upload et l'indexation des fichiers
    Puis délègue au unified_agent pour la réponse
    """
    import asyncio
    
    try:
        uploaded_doc_ids = []
        uploaded_results = []
        
        # Phase 1: Upload et indexation des fichiers
        for file_idx, f in enumerate(files):
            yield json.dumps({
                "step": f"📄 Processing file {file_idx + 1}/{len(files)}: {f.filename}",
                "progress": (file_idx / len(files)) * 70
            }) + "\n"
            await asyncio.sleep(0)
            
            # 1. Store
            yield json.dumps({"step": f"  ⬆️  Uploading..."}) + "\n"
            await asyncio.sleep(0)
            storage_result = storage_processor.store_file(f)
            if not storage_result.success:
                yield json.dumps({"error": f"Storage error: {storage_result.error}"}) + "\n"
                return
            file_url = storage_result.file_url
            
            # 2. Parse PDF
            yield json.dumps({"step": f"  📖 Parsing PDF..."}) + "\n"
            await asyncio.sleep(0)
            pdf_result = pdf_processor.parse_pdf(f)
            if not pdf_result.success:
                yield json.dumps({"error": f"PDF parsing error: {pdf_result.error}"}) + "\n"
                return
            documents = pdf_result.documents
            
            # 3. Chunk
            yield json.dumps({"step": f"  ✂️  Chunking..."}) + "\n"
            await asyncio.sleep(0)
            doc_id = str(uuid.uuid4())
            chunks = pdf_processor.chunk_documents(documents, chunk_size=1000, chunk_overlap=200)
            
            for chunk in chunks:
                chunk.metadata.update({
                    "source": file_url,
                    "filename": f.filename,
                    "doc_id": doc_id
                })
            
            # 4. Index
            yield json.dumps({"step": f"  🔢 Indexing..."}) + "\n"
            await asyncio.sleep(0)
            embedding_result = embedding_processor.store_embeddings(
                chunks,
                base_metadata={"source": file_url, "filename": f.filename, "doc_id": doc_id}
            )
            if not embedding_result.success:
                yield json.dumps({"error": f"Embedding error: {embedding_result.error}"}) + "\n"
                return
            
            # 5. Optional Summary
            summarize_on_upload = (os.getenv("RAG_SUMMARIZE_ON_UPLOAD", "false").lower() == "true")
            if summarize_on_upload:
                yield json.dumps({"step": f"  📝 Summarizing..."}) + "\n"
                await asyncio.sleep(0)
                summary_result = await asyncio.to_thread(summarizer_processor.summarize, documents)
                summary = summary_result.summary if summary_result.success else "Summary failed"
            else:
                summary = "(summary disabled)"
            
            uploaded_results.append({
                "filename": f.filename,
                "doc_id": doc_id,
                "file_url": file_url,
                "chunks_indexed": embedding_result.chunks_stored,
                "summary": summary
            })
            uploaded_doc_ids.append(doc_id)
            
            yield json.dumps({"step": f"  ✅ {f.filename} indexed"}) + "\n"
            await asyncio.sleep(0)
        
        # Phase 2: Agent unifié pour répondre
        yield json.dumps({
            "step": "🤖 Agent unifié analyse la requête...",
            "progress": 80
        }) + "\n"
        await asyncio.sleep(0)
        
        try:
            from unified_agent import run_unified_agent
            
            agent_result = run_unified_agent(
                query=query,
                has_files=True,
                file_ids=uploaded_doc_ids,
                max_iter=1
            )
            
            answer = agent_result.get("answer", "")
            sources = agent_result.get("sources", [])
            steps = agent_result.get("steps", [])
            mode = agent_result.get("mode", "rag")
            
            yield json.dumps({
                "step": f"✅ Agent completed ({agent_result.get('iterations', 0)} iterations)",
                "progress": 100
            }) + "\n"
            await asyncio.sleep(0)
            
        except Exception as e:
            answer = f"⚠️ Agent failed: {e}"
            sources = []
            steps = []
            mode = "error"
        
        # Final result
        yield json.dumps({
            "answer": answer,
            "uploaded_files": uploaded_results,
            "sources": sources,
            "steps": steps,
            "mode": mode,
            "done": True
        }) + "\n"
        
    except Exception as e:
        print(f"[stream] error: {e}")
        import traceback
        traceback.print_exc()
        yield json.dumps({"error": str(e)}) + "\n"


# ============================================================================
# FONCTION DE STREAMING POUR WEB QUERIES
# ============================================================================

async def stream_web_query(query: str, max_iter: int = 1):
    """
    Streaming pour les requêtes web sans fichiers
    Stream chaque step de l'agent au fur et à mesure
    """
    import asyncio
    from unified_agent import run_unified_agent_streaming
    
    try:
        # Initial state
        yield json.dumps({
            "step": "🚀 Initializing agent...",
            "progress": 5
        }) + "\n"
        await asyncio.sleep(0)
        
        # Run agent with streaming callback
        import threading
        from queue import Queue
        
        step_queue = Queue()
        
        def step_callback(step: str):
            """Called by agent when a new step is added"""
            step_queue.put({"type": "step", "data": step})
        
        def run_agent():
            try:
                result = run_unified_agent_streaming(
                    query=query,
                    has_files=False,
                    file_ids=[],
                    max_iter=max_iter,
                    step_callback=step_callback
                )
                step_queue.put({"type": "done", "data": result})
            except Exception as e:
                print(f"[stream] agent error: {e}")
                import traceback
                traceback.print_exc()
                step_queue.put({"type": "error", "data": str(e)})
        
        # Start agent in thread
        thread = threading.Thread(target=run_agent, daemon=True)
        thread.start()
        
        # Stream steps as they come
        progress = 10
        while True:
            try:
                import queue
                try:
                    item = step_queue.get(timeout=0.5)
                except queue.Empty:
                    # Keep connection alive with heartbeat
                    await asyncio.sleep(0.1)
                    continue
                
                if item["type"] == "step":
                    progress = min(progress + 8, 90)
                    yield json.dumps({
                        "step": item["data"],
                        "progress": progress
                    }) + "\n"
                    await asyncio.sleep(0)
                    
                elif item["type"] == "done":
                    result = item["data"]
                    
                    yield json.dumps({
                        "step": "✅ Agent completed",
                        "progress": 100
                    }) + "\n"
                    await asyncio.sleep(0)
                    
                    # Send final result
                    yield json.dumps({
                        "answer": result.get("answer", ""),
                        "sources": result.get("sources", []),
                        "verification": result.get("verification", {}),
                        "iterations": result.get("iterations", 0),
                        "steps": result.get("steps", []),
                        "mode": result.get("mode", "web"),
                        "done": True
                    }) + "\n"
                    break
                    
                elif item["type"] == "error":
                    yield json.dumps({
                        "error": item["data"],
                        "done": True
                    }) + "\n"
                    break
                    
            except Exception as e:
                print(f"[stream] error processing item: {e}")
                break
        
    except Exception as e:
        print(f"[stream] error: {e}")
        import traceback
        traceback.print_exc()
        yield json.dumps({"error": str(e), "done": True}) + "\n"


# ============================================================================
# ENDPOINT /ASK UNIFIÉ
# ============================================================================

@app.post("/ask")
async def ask(request: Request):
    """
    Endpoint unifié qui utilise l'agent intelligent pour TOUS les cas
    
    - Si fichiers → Upload + Index + Agent en mode "documents/hybrid"
    - Si pas de fichiers → Agent en mode "web"
    
    L'agent décide automatiquement de la stratégie optimale
    """
    content_type = request.headers.get("content-type", "")
    is_multipart = "multipart/form-data" in content_type
    is_json = "application/json" in content_type
    
    print(f"[ask] content_type={content_type}")
    
    try:
        query = ""
        files = []
        
        # Parse request
        if is_multipart:
            form = await request.form()
            query = (form.get("query") or "").strip()
            files = form.getlist("files")
            print(f"[ask] multipart: query='{query[:60]}...', files={len(files)}")
        else:
            body = await request.json() if is_json else {}
            query = (body.get("query") if isinstance(body, dict) else None) or ""
            print(f"[ask] json: query='{query[:60]}...'")
        
        if not query:
            return JSONResponse({"answer": "Please provide a query."}, status_code=400)
        
        # DÉCISION: Files présents ?
        if files:
            print(f"[ask] FILES DETECTED → Streaming upload + unified agent (RAG mode)")
            return StreamingResponse(
                stream_file_processing_and_rag(query, files),
                media_type="application/x-ndjson",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no"
                }
            )
        
        else:
            print(f"[ask] NO FILES → Unified agent (WEB mode) with streaming")
            return StreamingResponse(
                stream_web_query(query, max_iter=int(os.getenv("AGENT_MAX_ITER", "1"))),
                media_type="application/x-ndjson",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no"
                }
            )
        
    except Exception as e:
        print(f"[ask] error: {e}")
        import traceback
        traceback.print_exc()
        return JSONResponse({
            "answer": "Error processing request.",
            "error": str(e)
        }, status_code=500)


if __name__ == "__main__":
    import uvicorn
    print("[startup] Starting server with Unified Agentic AI")
    uvicorn.run(app, host="0.0.0.0", port=8000)