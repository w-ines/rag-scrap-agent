from __future__ import annotations

from typing import TypedDict, List, Dict, Any, Optional
import os
import logging

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from langchain_openai import ChatOpenAI
from langchain_ollama import ChatOllama

# Reuse existing tools
from tools.scraper import web_search as tool_web_search

logger = logging.getLogger(__name__)


class WebState(TypedDict, total=False):
    query: str
    subqueries: List[str]
    results: List[Dict[str, Any]]
    sources: List[Dict[str, Any]]
    context: str
    draft_answer: str
    verification: Dict[str, Any]
    final_answer: str
    iter: int
    steps: List[str]


def _get_llm():
    provider = (os.getenv("LLM_PROVIDER", "ollama")).lower()
    if provider == "openai":
        model = os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini")
        return ChatOpenAI(model=model, temperature=0.2)
    # Ollama local
    model = os.getenv("OLLAMA_CHAT_MODEL", "llama3:latest")
    try:
        num_ctx = int(os.getenv("OLLAMA_NUM_CTX", "32768")) or None
    except ValueError:
        num_ctx = None
    return ChatOllama(model=model, temperature=0.2, num_ctx=num_ctx)


def node_plan(state: WebState) -> WebState:
    """Plan: decompose the user's query into 1-3 web subqueries."""
    llm = _get_llm()
    prompt = (
        "You will decompose the user's information need into up to 3 simple web search queries.\n"
        "User query: {query}\n\n"
        "Return a JSON list of strings only."
    )
    text = llm.invoke(prompt.format(query=state["query"]))
    # Robust parsing
    subqueries: List[str] = []
    try:
        import json
        content = getattr(text, "content", str(text))
        start, end = content.find("["), content.rfind("]")
        payload = content[start : end + 1] if start != -1 and end != -1 else content
        data = json.loads(payload)
        if isinstance(data, list):
            subqueries = [str(x).strip() for x in data if str(x).strip()]
    except Exception:
        # Fallback: use original query
        subqueries = [state["query"]]
    if not subqueries:
        subqueries = [state["query"]]
    steps = list(state.get("steps", []))
    steps.append(f"💭 Plan: derived {len(subqueries[:3])} subquerie(s)")
    return {"subqueries": subqueries[:3], "iter": state.get("iter", 0), "steps": steps}


def node_search(state: WebState) -> WebState:
    """Search + scrape using existing tool_web_search for each subquery, then merge."""
    merged_results: List[Dict[str, Any]] = []
    merged_sources: List[Dict[str, Any]] = []
    contexts: List[str] = []

    for i, sq in enumerate(state.get("subqueries", []) or [state["query"]]):
        try:
            r = tool_web_search(query=sq, max_results=8)
            res = r.get("results", [])
            src = r.get("sources", [])
            ctx = r.get("context", "")
            if res:
                merged_results.extend(res)
            if src:
                merged_sources.extend(src)
            if ctx:
                contexts.append(ctx)
        except Exception as e:
            logger.warning(f"search failed for subquery {i}: {e}")
            continue

    context = ("\n\n".join(contexts))[:12000]
    steps = list(state.get("steps", []))
    steps.append(f"🔎 Search: aggregated {len(merged_results)} result(s) from {len(state.get('subqueries', []) or [state['query']])} subquery(ies)")
    return {"results": merged_results, "sources": merged_sources, "context": context, "steps": steps}


def node_synthesize(state: WebState) -> WebState:
    """Draft an answer from the combined context."""
    llm = _get_llm()
    template = (
        "You are a precise assistant. Answer the user's query using ONLY the provided context.\n\n"
        "Context:\n{context}\n\n"
        "User query: {query}\n\n"
        "Instructions:\n"
        "- Be concise and complete.\n"
        "- If you cite, use inline references like [1][2] matching the sources order in context blocks.\n"
        "- If insufficient information, say so clearly.\n\n"
        "Answer:"
    )
    resp = llm.invoke(template.format(context=state.get("context", ""), query=state["query"]))
    content = getattr(resp, "content", str(resp))
    steps = list(state.get("steps", []))
    steps.append("🧠 Synthesize: drafted an answer from merged context")
    return {"draft_answer": content.strip(), "steps": steps}


def node_verify(state: WebState) -> WebState:
    """Verify the draft answer strictly against context and propose revision if needed."""
    llm = _get_llm()
    template = (
        "You are a strict verifier. Use ONLY the context to check the answer.\n\n"
        "Context:\n{context}\n\n"
        "Question: {question}\n\n"
        "Draft answer:\n{answer}\n\n"
        "Return compact JSON with keys: verdict ('pass'|'revise'), reasons (string), revised_answer (string|null)."
    )
    resp = llm.invoke(
        template.format(
            context=state.get("context", ""), question=state["query"], answer=state.get("draft_answer", "")
        )
    )
    text = getattr(resp, "content", str(resp))
    decision = {"verdict": "pass", "reasons": "", "revised_answer": None}
    try:
        import json
        start, end = text.find("{"), text.rfind("}")
        payload = text[start : end + 1] if start != -1 and end != -1 else text
        decision = json.loads(payload)
    except Exception:
        pass
    steps = list(state.get("steps", []))
    verdict = decision.get("verdict", "pass") if isinstance(decision, dict) else "pass"
    steps.append(f"✔️ Verify: verdict={verdict}")
    return {"verification": decision, "steps": steps}


def node_refine(state: WebState) -> WebState:
    """Refine the draft using verifier reasons."""
    llm = _get_llm()
    template = (
        "Revise the answer to strictly align with the context and address these issues: {reasons}.\n\n"
        "Context:\n{context}\n\n"
        "Question: {question}\n\n"
        "Keep it concise with inline citations [1][2] if applicable.\n\n"
        "Revised answer:"
    )
    reasons = (state.get("verification", {}) or {}).get("reasons", "")
    resp = llm.invoke(
        template.format(
            reasons=reasons,
            context=state.get("context", ""),
            question=state["query"],
        )
    )
    content = getattr(resp, "content", str(resp))
    steps = list(state.get("steps", []))
    steps.append("♻️ Refine: produced a revised draft")
    return {"draft_answer": content.strip(), "iter": state.get("iter", 0) + 1, "steps": steps}


def node_finalize(state: WebState) -> WebState:
    decision = state.get("verification", {}) or {}
    final_answer = state.get("draft_answer", "")
    if isinstance(decision, dict) and decision.get("verdict") == "revise":
        revised = decision.get("revised_answer")
        if revised:
            final_answer = str(revised).strip()
    steps = list(state.get("steps", []))
    steps.append("🏁 Finalize: finalized the answer")
    return {"final_answer": final_answer, "steps": steps}


def build_graph(max_refine: int = 1):
    graph = StateGraph(WebState)
    memory = MemorySaver()

    graph.add_node("plan", node_plan)
    graph.add_node("search", node_search)
    graph.add_node("synthesize", node_synthesize)
    graph.add_node("verify", node_verify)
    graph.add_node("refine", node_refine)
    graph.add_node("finalize", node_finalize)

    graph.set_entry_point("plan")
    graph.add_edge("plan", "search")
    graph.add_edge("search", "synthesize")
    graph.add_edge("synthesize", "verify")

    def should_refine(state: WebState):
        decision = state.get("verification", {}) or {}
        it = int(state.get("iter", 0))
        if isinstance(decision, dict) and decision.get("verdict") == "revise" and it < max_refine:
            return "refine"
        return "finalize"

    graph.add_conditional_edges("verify", should_refine, {"refine": "refine", "finalize": "finalize"})
    graph.add_edge("refine", "verify")
    graph.add_edge("finalize", END)

    return graph.compile(checkpointer=memory)


def run_web_graph_sync(query: str) -> Dict[str, Any]:
    """Convenience function to run the graph synchronously and get a final answer and sources."""
    app = build_graph(max_refine=int(os.getenv("WEB_GRAPH_MAX_REFINE", "1")))
    initial: WebState = {"query": query, "iter": 0, "steps": []}
    final_state = app.invoke(initial)
    return {
        "answer": final_state.get("final_answer", ""),
        "sources": final_state.get("sources", []),
        "context_length": len(final_state.get("context", "")),
        "iterations": final_state.get("iter", 0),
        "verification": final_state.get("verification", {}),
        "steps": final_state.get("steps", []),
    }
