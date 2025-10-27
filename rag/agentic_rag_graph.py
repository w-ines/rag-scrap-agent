from __future__ import annotations

from typing import TypedDict, List, Dict, Any, Optional
import logging

from langgraph.graph import StateGraph, END

logger = logging.getLogger(__name__)


class RAGState(TypedDict, total=False):
    query: str
    working_query: str
    file_ids: List[str]
    results: List[Dict[str, Any]]
    sources: List[Dict[str, Any]]
    context: str
    draft_answer: str
    verification: Dict[str, Any]
    final_answer: str
    iter: int
    steps: List[str]


def build_rag_graph(
    embedding_processor,
    qa_processor,
    top_k: int = 5,
    max_refine: int = 1,
):
    """
    Build a small agentic RAG graph over your indexed documents.

    Nodes:
    - plan: optionally reformulate/clarify the query (can be a no-op for now)
    - retrieve: call embedding_processor.retrieve_knowledge with file_ids
    - synthesize: draft an answer with qa_processor using retrieved context
    - verify: check groundedness; may suggest revision
    - refine: optionally refine and/or expand query, then re-retrieve
    - finalize: pick final answer
    """

    graph = StateGraph(RAGState)

    def node_plan(state: RAGState) -> RAGState:
        # Minimal planning: keep the query as-is, but set working_query to allow future expansion
        steps = list(state.get("steps", []))
        steps.append("💭 Plan: initialized working query")
        return {"working_query": state["query"], "iter": state.get("iter", 0), "steps": steps}

    def node_retrieve(state: RAGState) -> RAGState:
        q = state.get("working_query", state["query"]) or state["query"]
        file_ids = state.get("file_ids")
        steps = list(state.get("steps", []))
        try:
            r = embedding_processor.retrieve_knowledge(query=q, top_k=top_k, file_ids=file_ids)
            ctx = r.get("context", "")
            results = r.get("results", [])
            sources = r.get("sources", [])
            steps.append(f"🔎 Retrieve: found {len(results)} chunk(s)")
            # Keep context reasonably bounded for LLMs
            context = ctx[:16000]
            return {"results": results, "sources": sources, "context": context, "steps": steps}
        except Exception as e:
            steps.append(f"⚠️ Retrieve failed: {e}")
            return {"results": [], "sources": [], "context": "", "steps": steps}

    def node_synthesize(state: RAGState) -> RAGState:
        steps = list(state.get("steps", []))
        ctx = state.get("context", "")
        if not ctx.strip():
            steps.append("🧠 Synthesize: no context, empty draft")
            return {"draft_answer": "No relevant information found in the uploaded documents.", "steps": steps}
        qa = qa_processor.answer_question(question=state["query"], context=ctx, sources=state.get("sources", []))
        draft = qa.answer if getattr(qa, "success", False) else (qa.error or "Failed to generate answer")
        steps.append("🧠 Synthesize: drafted answer from retrieved context")
        # Store verifier decision from qa.metadata if present
        verification = {}
        try:
            md = getattr(qa, "metadata", {}) or {}
            verification = md.get("verification", {}) or {}
        except Exception:
            pass
        return {"draft_answer": draft, "verification": verification, "steps": steps}

    def node_verify(state: RAGState) -> RAGState:
        # If qa_processor already produced a verification dict, reuse it
        steps = list(state.get("steps", []))
        decision = state.get("verification", {}) or {}
        verdict = decision.get("verdict", "pass") if isinstance(decision, dict) else "pass"
        steps.append(f"✔️ Verify: verdict={verdict}")
        return {"verification": decision, "steps": steps}

    def node_refine(state: RAGState) -> RAGState:
        # Simple refinement: if revise, slightly expand the working_query using reasons
        steps = list(state.get("steps", []))
        decision = state.get("verification", {}) or {}
        reasons = decision.get("reasons", "") if isinstance(decision, dict) else ""
        wq = state.get("working_query", state["query"]) or state["query"]
        if reasons:
            expanded = f"{wq} (focus: {reasons[:120]})"
        else:
            expanded = wq
        steps.append("♻️ Refine: expanded query and will re-retrieve")
        # Increment iter and reset context/results to trigger new retrieval
        return {
            "working_query": expanded,
            "iter": int(state.get("iter", 0)) + 1,
            "context": "",
            "results": [],
            "sources": [],
            "steps": steps,
        }

    def node_finalize(state: RAGState) -> RAGState:
        decision = state.get("verification", {}) or {}
        final_answer = state.get("draft_answer", "")
        if isinstance(decision, dict) and decision.get("verdict") == "revise":
            revised = decision.get("revised_answer")
            if revised:
                final_answer = str(revised).strip()
        steps = list(state.get("steps", []))
        steps.append("🏁 Finalize: finalized answer")
        return {"final_answer": final_answer, "steps": steps}

    graph.add_node("plan", node_plan)
    graph.add_node("retrieve", node_retrieve)
    graph.add_node("synthesize", node_synthesize)
    graph.add_node("verify", node_verify)
    graph.add_node("refine", node_refine)
    graph.add_node("finalize", node_finalize)

    graph.set_entry_point("plan")
    graph.add_edge("plan", "retrieve")
    graph.add_edge("retrieve", "synthesize")
    graph.add_edge("synthesize", "verify")

    def should_refine(state: RAGState):
        decision = state.get("verification", {}) or {}
        it = int(state.get("iter", 0))
        if isinstance(decision, dict) and decision.get("verdict") == "revise" and it < max_refine:
            return "refine"
        return "finalize"

    graph.add_conditional_edges("verify", should_refine, {"refine": "refine", "finalize": "finalize"})
    graph.add_edge("refine", "retrieve")
    graph.add_edge("finalize", END)

    return graph.compile()


def run_agentic_rag_sync(
    query: str,
    embedding_processor,
    qa_processor,
    file_ids: Optional[List[str]] = None,
    top_k: int = 5,
    max_refine: int = 1,
) -> Dict[str, Any]:
    """Convenience function to run the RAG graph synchronously and return final state."""
    app = build_rag_graph(embedding_processor=embedding_processor, qa_processor=qa_processor, top_k=top_k, max_refine=max_refine)
    initial: RAGState = {"query": query, "file_ids": file_ids or [], "iter": 0, "steps": []}
    final_state = app.invoke(initial)
    return {
        "answer": final_state.get("final_answer", ""),
        "sources": final_state.get("sources", []),
        "context_length": len(final_state.get("context", "")),
        "iterations": final_state.get("iter", 0),
        "verification": final_state.get("verification", {}),
        "steps": final_state.get("steps", []),
    }
