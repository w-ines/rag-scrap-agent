from fastapi import FastAPI, UploadFile, File, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import os
from dotenv import load_dotenv
import httpx
import uuid
import json
import time
from pydantic import BaseModel

# Import RAG processors
from rag import PDFProcessor, EmbeddingProcessor, StorageProcessor, SummarizerProcessor, QAProcessor

# Initialize processors
storage_processor = StorageProcessor()
pdf_processor = PDFProcessor()
embedding_processor = EmbeddingProcessor()
summarizer_processor = SummarizerProcessor()
qa_processor = QAProcessor()


try:
    from tools.retrieval_tool import set_embedding_processor  # requires 'smolagents'
    set_embedding_processor(embedding_processor)
except Exception as e:
    print(f"[startup] retrieval_tool not available or failed to initialize: {e}")


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


# """
# Mount the smolagent streaming app at /agent (POST /agent/) to keep SSE streaming.
# We implement a unified /ask below that returns JSON and orchestrates upload/summarize/RAG/scrape.
# """
# app.mount("/agent", smolagent_router)


async def stream_rag_steps(query: str, files: list):
    """Generator for streaming RAG processing steps"""
    import asyncio
    
    try:
        uploaded_results = []
        uploaded_doc_ids = []
        
        # Process each file: store, parse, index, summarize
        for file_idx, f in enumerate(files):
            yield json.dumps({
                "step": f"📄 Processing file {file_idx + 1}/{len(files)}: {f.filename}",
                "progress": (file_idx / len(files)) * 80  # 0-80% for file processing
            }) + "\n"
            await asyncio.sleep(0)  # Force flush
            
            # 1. Store in Supabase
            yield json.dumps({"step": f"  ⬆️  Uploading to storage..."}) + "\n"
            await asyncio.sleep(0)
            storage_result = storage_processor.store_file(f)
            if not storage_result.success:
                yield json.dumps({"error": f"Storage error: {storage_result.error}"}) + "\n"
                return
            file_url = storage_result.file_url
            yield json.dumps({"step": f"  ✅ Uploaded: {file_url}"}) + "\n"
            await asyncio.sleep(0)
            
            # 2. Parse PDF
            yield json.dumps({"step": f"  📖 Parsing PDF..."}) + "\n"
            await asyncio.sleep(0)
            pdf_result = pdf_processor.parse_pdf(f)
            if not pdf_result.success:
                yield json.dumps({"error": f"PDF parsing error: {pdf_result.error}"}) + "\n"
                return
            documents = pdf_result.documents
            yield json.dumps({"step": f"  ✅ Parsed {len(documents)} pages"}) + "\n"
            await asyncio.sleep(0)
            
            # 3. Chunk documents
            yield json.dumps({"step": f"  ✂️  Chunking documents..."}) + "\n"
            await asyncio.sleep(0)
            doc_id = str(uuid.uuid4())
            chunks = pdf_processor.chunk_documents(documents, chunk_size=1000, chunk_overlap=200)
            
            # Enrich chunks with metadata
            for chunk in chunks:
                chunk.metadata.update({
                    "source": file_url,
                    "filename": f.filename,
                    "doc_id": doc_id
                })
            yield json.dumps({"step": f"  ✅ Created {len(chunks)} chunks"}) + "\n"
            await asyncio.sleep(0)
            
            # 4. Index in vector store
            yield json.dumps({"step": f"  🔢 Generating embeddings..."}) + "\n"
            await asyncio.sleep(0)
            embedding_result = embedding_processor.store_embeddings(
                chunks,
                base_metadata={"source": file_url, "filename": f.filename, "doc_id": doc_id}
            )
            if not embedding_result.success:
                yield json.dumps({"error": f"Embedding error: {embedding_result.error}"}) + "\n"
                return
            yield json.dumps({"step": f"  ✅ Indexed {embedding_result.chunks_stored} chunks"}) + "\n"
            await asyncio.sleep(0)
            
            # 5. Optional: Generate summary with periodic heartbeats (can be disabled)
            summarize_on_upload = (os.getenv("RAG_SUMMARIZE_ON_UPLOAD", "true").lower() == "true")
            if summarize_on_upload:
                yield json.dumps({"step": f"  📝 Generating summary..."}) + "\n"
                await asyncio.sleep(0)
                start_ts = time.time()
                summarize_task = asyncio.create_task(asyncio.to_thread(summarizer_processor.summarize, documents))
                # Heartbeat loop every 2s while summarization runs
                while not summarize_task.done():
                    elapsed = int(time.time() - start_ts)
                    yield json.dumps({"step": f"  ⏳ Summarizing... {elapsed}s elapsed"}) + "\n"
                    await asyncio.sleep(2)
                summary_result = await summarize_task
                summary = summary_result.summary if summary_result.success else "Summary generation failed"
                yield json.dumps({"step": f"  ✅ Summary generated ({len(summary)} chars)"}) + "\n"
                await asyncio.sleep(0)
            else:
                summary = "(summary disabled)"
                yield json.dumps({"step": f"  ⏭️  Summary skipped (RAG_SUMMARIZE_ON_UPLOAD=false)"}) + "\n"
                await asyncio.sleep(0)
            
            uploaded_results.append({
                "filename": f.filename,
                "doc_id": doc_id,
                "file_url": file_url,
                "chunks_indexed": embedding_result.chunks_stored,
                "summary": summary
            })
            uploaded_doc_ids.append(doc_id)
        
        # 6. Agentic RAG over the uploaded documents (constrained by file_ids)
        yield json.dumps({"step": "🤖 Running agentic RAG over indexed documents...", "progress": 85}) + "\n"
        await asyncio.sleep(0)

        try:
            from rag.agentic_rag_graph import run_agentic_rag_sync
            rag_result = run_agentic_rag_sync(
                query=query,
                embedding_processor=embedding_processor,
                qa_processor=qa_processor,
                file_ids=uploaded_doc_ids,
                top_k=5,
                max_refine=1,
            )
            response_text = rag_result.get("answer", "") or "No answer produced."
            rag_sources = rag_result.get("sources", [])
            rag_steps = rag_result.get("steps", [])
            yield json.dumps({"step": f"✅ Agentic RAG complete in {rag_result.get('iterations', 0)} iteration(s)", "progress": 95}) + "\n"
            await asyncio.sleep(0)
        except Exception as e:
            response_text = f"⚠️ Agentic RAG failed: {e}"
            rag_sources = []
            rag_steps = []
        
        yield json.dumps({
            "step": f"✅ Answer generated",
            "progress": 100
        }) + "\n"
        await asyncio.sleep(0)
        
        # Send final answer
        yield json.dumps({
            "answer": response_text,
            "uploaded_files": uploaded_results,
            "rag_sources": rag_sources,
            "agentic_steps": rag_steps,
            "mode": "rag-agentic",
            "done": True
        }) + "\n"
        
    except Exception as e:
        print(f"[stream_rag_steps] error: {e}")
        import traceback
        traceback.print_exc()
        yield json.dumps({"error": str(e)}) + "\n"


@app.post("/ask")
async def ask(request: Request):
    """
    Unified Ask endpoint:
    - If files are attached: execute upload logic (store, parse, index, summarize) and return RAG result
    - If no files: delegate to smolagent for web search/scraping
    
    Returns streaming response for RAG, JSON for web.
    """
    content_type = request.headers.get("content-type", "")
    is_multipart = "multipart/form-data" in content_type
    is_json = "application/json" in content_type
    print(f"[ask] called content_type={content_type} is_multipart={is_multipart} is_json={is_json}")

    try:
        query = ""
        files = []
        
        # Parse request
        if is_multipart:
            form = await request.form()
            query = (form.get("query") or "").strip()
            files = form.getlist("files")
            print(f"[ask] multipart received query='{query[:80] if query else ''}' files_count={len(files) if files else 0}")
        else:
            # JSON body
            body = await request.json() if is_json else {}
            query = (body.get("query") if isinstance(body, dict) else None) or ""
            print(f"[ask] json body parsed query='{query[:120] if query else ''}'")

        if not query:
            return JSONResponse({"answer": "Please provide a query."}, status_code=400)

        # DECISION: If files present → RAG workflow (streaming), else → smolagent
        if files:
            print(f"[ask] FILES DETECTED → Executing RAG workflow with streaming")
            from fastapi.responses import StreamingResponse
            
            return StreamingResponse(
                stream_rag_steps(query, files),
                media_type="application/x-ndjson",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no"
                }
            )
        
        else:
            # NO FILES → Always use LangGraph web graph
            print(f"[ask] NO FILES → Delegating to LangGraph web graph")
            try:
                from web_graph.web_search_graph import run_web_graph_sync
                graph_result = run_web_graph_sync(query)
                print(f"[ask] langgraph response ok len={len(graph_result.get('answer',''))}")
                return JSONResponse({
                    "answer": graph_result.get("answer") or "",
                    "sources": graph_result.get("sources", []),
                    "verification": graph_result.get("verification", {}),
                    "iterations": graph_result.get("iterations", 0),
                    "steps": graph_result.get("steps", []),
                    "mode": "web-graph"
                })
            except Exception as eg:
                print(f"[ask] langgraph failed: {eg}")
                return JSONResponse({
                    "answer": "Web graph failed to process the query.",
                    "error": str(eg),
                    "mode": "web-graph"
                }, status_code=500)
        
    except Exception as e:
        print("[ask] error:", e)
        import traceback
        traceback.print_exc()
        return JSONResponse({"answer": "Error processing request.", "error": str(e)}, status_code=500)



if __name__ == "__main__":
    import uvicorn
    print("[startup] Starting uvicorn server on 0.0.0.0:8000")
    uvicorn.run(app, host="0.0.0.0", port=8000)