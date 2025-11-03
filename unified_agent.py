"""
Agent unifié qui suit les principes agentic AI (Think → Act → Observe → Verify → Rethink)
Fonctionne pour TOUS les cas : RAG avec fichiers ET recherche web pure

Architecture basée sur le whitepaper Agent Companion :
- Reasoning avec ReAct framework
- Tool use dynamique (web_search, document_search)
- Verification loop avant la réponse finale
"""

from __future__ import annotations
from typing import TypedDict, List, Dict, Any, Optional, Literal
import logging
from langgraph.graph import StateGraph, END

logger = logging.getLogger(__name__)


# ============================================================================
# GLOBAL CALLBACK FOR STREAMING
# ============================================================================

_step_callback = None

def set_step_callback(callback):
    """Set global callback for streaming steps"""
    global _step_callback
    _step_callback = callback

def clear_step_callback():
    """Clear global callback"""
    global _step_callback
    _step_callback = None

def emit_step(step: str):
    """Emit a step to the callback if set"""
    global _step_callback
    if _step_callback is not None:
        _step_callback(step)


# ============================================================================
# ÉTAT UNIFIÉ de l'agent
# ============================================================================

class UnifiedAgentState(TypedDict, total=False):
    """État partagé pour tous les types de requêtes"""
    # Input
    query: str
    has_files: bool
    file_ids: List[str]
    
    # Planning
    working_query: str
    search_strategy: Literal["web", "documents", "hybrid"]
    
    # Tool results
    web_results: List[Dict[str, Any]]
    doc_results: List[Dict[str, Any]]
    all_sources: List[Dict[str, Any]]
    context: str
    
    # Answer generation
    draft_answer: str
    verification: Dict[str, Any]
    final_answer: str
    
    # Control flow
    iter: int
    max_iter: int
    steps: List[str]
    consecutive_failures: int  # Track failed attempts
    last_context_length: int   # Track if we're getting new info


# ============================================================================
# NŒUDS du graph unifié
# ============================================================================

def node_think(state: UnifiedAgentState) -> UnifiedAgentState:
    """
    THINK: Analyse la requête et décide de la stratégie
    
    Décisions:
    - Si fichiers présents → "documents" ou "hybrid"
    - Si pas de fichiers → "web"
    - Reformule la query si c'est une itération de refinement
    """
    steps = list(state.get("steps", []))
    query = state["query"]
    has_files = state.get("has_files", False)
    iter_count = state.get("iter", 0)
    
    # Première itération : choisir la stratégie
    if iter_count == 0:
        if has_files:
            # Analyser si la question nécessite aussi du web
            if any(word in query.lower() for word in ["actuel", "récent", "aujourd'hui", "latest", "news", "current"]):
                strategy = "hybrid"
                step = "💭 THINK: HYBRID strategy (docs + recent web)"
                steps.append(step)
                emit_step(step)
            else:
                strategy = "documents"
                step = "💭 THINK: DOCUMENTS strategy (uploaded files)"
                steps.append(step)
                emit_step(step)
        else:
            strategy = "web"
            step = "💭 THINK: WEB strategy (internet search)"
            steps.append(step)
            emit_step(step)
        
        return {
            "working_query": query,
            "search_strategy": strategy,
            "iter": 0,
            "steps": steps
        }
    
    # Itérations suivantes : RETHINK avec reformulation
    else:
        verification = state.get("verification", {})
        reasons = verification.get("reasons", "")
        
        # Reformuler la query en fonction des raisons du verifier
        if reasons:
            expanded_query = f"{query} (focus: {reasons[:120]})"
        else:
            expanded_query = query
        
        step = f"🔄 RETHINK (iter {iter_count}): Query reformulation based on feedback"
        steps.append(step)
        emit_step(step)
        
        return {
            "working_query": expanded_query,
            "iter": iter_count,
            "steps": steps
        }


def node_act(state: UnifiedAgentState) -> UnifiedAgentState:
    """
    ACT: Exécute les outils appropriés selon la stratégie
    
    Tools disponibles:
    - web_search (via tools.scraper.web_search)
    - document_search (via embedding_processor)
    """
    steps = list(state.get("steps", []))
    strategy = state.get("search_strategy", "web")
    working_query = state.get("working_query", state["query"])
    
    web_results = []
    doc_results = []
    all_sources = []
    contexts = []
    
    try:
        # Tool 1: Web Search
        if strategy in ["web", "hybrid"]:
            step = "🔧 ACT: Calling web_search() tool"
            steps.append(step)
            emit_step(step)
            from tools.scraper import web_search
            
            try:
                web_response = web_search(query=working_query, max_results=5)
                web_results = web_response.get("results", [])
                all_sources.extend(web_response.get("sources", []))
                if web_response.get("context"):
                    contexts.append(f"[WEB SEARCH RESULTS]\n{web_response['context']}")
                step = f"✅ web_search: {len(web_results)} results"
                steps.append(step)
                emit_step(step)
            except Exception as e:
                steps.append(f"⚠️ web_search failed: {e}")
        
        # Tool 2: Document Search (RAG)
        if strategy in ["documents", "hybrid"]:
            step = "🔧 ACT: Calling document_search() tool"
            steps.append(step)
            emit_step(step)
            # Import depuis main
            from rag.embedding_processor import EmbeddingProcessor
            embedding_processor = EmbeddingProcessor()
            
            try:
                doc_response = embedding_processor.retrieve_knowledge(
                    query=working_query,
                    top_k=5,
                    file_ids=state.get("file_ids")
                )
                doc_results = doc_response.get("results", [])
                all_sources.extend(doc_response.get("sources", []))
                if doc_response.get("context"):
                    contexts.append(f"[DOCUMENTS]\n{doc_response['context']}")
                step = f"✅ document_search: {len(doc_results)} chunks"
                steps.append(step)
                emit_step(step)
            except Exception as e:
                steps.append(f"⚠️ document_search failed: {e}")
        
        # Merge contexts
        merged_context = "\n\n==========\n\n".join(contexts)[:16000]
        
        return {
            "web_results": web_results,
            "doc_results": doc_results,
            "all_sources": all_sources,
            "context": merged_context,
            "steps": steps
        }
        
    except Exception as e:
        logger.error(f"node_act error: {e}")
        steps.append(f"❌ ACT failed: {e}")
        return {
            "web_results": [],
            "doc_results": [],
            "all_sources": [],
            "context": "",
            "steps": steps
        }


def node_observe(state: UnifiedAgentState) -> UnifiedAgentState:
    """
    OBSERVE: Analyse les résultats des tools
    
    Vérifie:
    - Y a-t-il assez d'information ?
    - Les sources sont-elles pertinentes ?
    - Faut-il chercher ailleurs ?
    """
    steps = list(state.get("steps", []))
    context = state.get("context", "")
    web_count = len(state.get("web_results", []))
    doc_count = len(state.get("doc_results", []))
    
    consecutive_failures = state.get("consecutive_failures", 0)
    last_context_length = state.get("last_context_length", 0)
    current_context_length = len(context.strip())
    
    # Track consecutive failures
    if not context.strip() or len(context) < 200:
        consecutive_failures += 1
        if not context.strip():
            step = "👁️ OBSERVE: ⚠️ No context found"
        else:
            step = "👁️ OBSERVE: ⚠️ Insufficient context"
        steps.append(step)
        emit_step(step)
    else:
        # Reset failure counter if we got good results
        if current_context_length > last_context_length:
            consecutive_failures = 0
        step = f"👁️ OBSERVE: ✅ Valid context ({web_count} web, {doc_count} docs)"
        steps.append(step)
        emit_step(step)
    
    return {
        "steps": steps,
        "consecutive_failures": consecutive_failures,
        "last_context_length": current_context_length
    }


def node_synthesize(state: UnifiedAgentState) -> UnifiedAgentState:
    """
    SYNTHESIZE: Génère un brouillon de réponse depuis le contexte
    
    Utilise le QAProcessor pour générer une réponse groundée
    """
    steps = list(state.get("steps", []))
    context = state.get("context", "")
    
    if not context.strip():
        step = "🧠 SYNTHESIZE: No context → empty response"
        steps.append(step)
        emit_step(step)
        return {
            "draft_answer": "Je n'ai pas trouvé d'information pertinente pour répondre à cette question.",
            "steps": steps
        }
    
    try:
        from rag.qa_processor import QAProcessor
        qa_processor = QAProcessor()
        
        qa_result = qa_processor.answer_question(
            question=state["query"],
            context=context,
            sources=state.get("all_sources", [])
        )
        
        draft = qa_result.answer if qa_result.success else "Erreur de génération"
        verification = qa_result.metadata.get("verification", {})
        
        step = "🧠 SYNTHESIZE: Response generated via QAProcessor"
        steps.append(step)
        emit_step(step)
        
        return {
            "draft_answer": draft,
            "verification": verification,
            "steps": steps
        }
        
    except Exception as e:
        logger.error(f"synthesize error: {e}")
        steps.append(f"❌ SYNTHESIZE failed: {e}")
        return {
            "draft_answer": "Erreur lors de la génération de la réponse.",
            "steps": steps
        }


def node_verify(state: UnifiedAgentState) -> UnifiedAgentState:
    """
    VERIFY: Vérifie la qualité de la réponse
    
    Checks:
    - Grounding dans le contexte
    - Présence de citations
    - Hallucinations potentielles
    - Complétude de la réponse
    
    Retourne: verdict = "pass" | "revise"
    """
    steps = list(state.get("steps", []))
    
    # Le verification est déjà fait par QAProcessor
    verification = state.get("verification", {})
    verdict = verification.get("verdict", "pass")
    
    step = f"✔️ VERIFY: verdict = {verdict}"
    steps.append(step)
    emit_step(step)
    
    return {"steps": steps}


def node_finalize(state: UnifiedAgentState) -> UnifiedAgentState:
    """
    FINALIZE: Prépare la réponse finale
    
    Applique une révision si le verifier l'a demandée
    """
    steps = list(state.get("steps", []))
    
    verification = state.get("verification", {})
    final_answer = state.get("draft_answer", "")
    
    # Si révision proposée, l'utiliser
    if isinstance(verification, dict) and verification.get("verdict") == "revise":
        revised = verification.get("revised_answer")
        if revised and str(revised).strip():
            final_answer = str(revised).strip()
            step = "🏁 FINALIZE: Revision applied"
            steps.append(step)
            emit_step(step)
        else:
            step = "🏁 FINALIZE: Keeping draft (no valid revision)"
            steps.append(step)
            emit_step(step)
    else:
        step = "🏁 FINALIZE: Draft validated"
        steps.append(step)
        emit_step(step)
    
    return {
        "final_answer": final_answer,
        "steps": steps
    }


# ============================================================================
# CONSTRUCTION DU GRAPH
# ============================================================================

def should_refine(state: UnifiedAgentState) -> str:
    """
    Décision: Refine (RETHINK) ou Finalize ?
    
    Refine SI:
    - verdict = "revise"
    - iter < max_iter
    - consecutive_failures < 3 (safety limit)
    """
    verification = state.get("verification", {})
    iter_count = state.get("iter", 0)
    max_iter = state.get("max_iter", 1)
    consecutive_failures = state.get("consecutive_failures", 0)
    
    # Safety: Force finalize after 3 consecutive failures
    if consecutive_failures >= 3:
        logger.warning(f"Forcing finalization after {consecutive_failures} consecutive failures")
        return "finalize"
    
    if isinstance(verification, dict) and verification.get("verdict") == "revise":
        if iter_count < max_iter:
            return "rethink"
    
    return "finalize"


def build_unified_agent(max_iter: int = 1):
    """
    Construit le graph unifié avec pattern agentic complet
    
    Flow:
    THINK → ACT → OBSERVE → SYNTHESIZE → VERIFY
      ↑                                      ↓
      └────────── RETHINK ←──────────────── (if revise)
                               ↓
                           FINALIZE (if pass)
    """
    graph = StateGraph(UnifiedAgentState)
    
    # Ajouter les nœuds
    graph.add_node("think", node_think)
    graph.add_node("act", node_act)
    graph.add_node("observe", node_observe)
    graph.add_node("synthesize", node_synthesize)
    graph.add_node("verify", node_verify)
    graph.add_node("finalize", node_finalize)
    
    # Flow principal
    graph.set_entry_point("think")
    graph.add_edge("think", "act")
    graph.add_edge("act", "observe")
    graph.add_edge("observe", "synthesize")
    graph.add_edge("synthesize", "verify")
    
    # Branchement conditionnel après VERIFY
    graph.add_conditional_edges(
        "verify",
        should_refine,
        {
            "rethink": "think",  # Refaire un cycle
            "finalize": "finalize"
        }
    )
    
    graph.add_edge("finalize", END)
    
    # Compile with increased recursion limit and better config
    return graph.compile(
        checkpointer=None,
        interrupt_before=None,
        interrupt_after=None,
        debug=False
    )


# ============================================================================
# FONCTION D'INTERFACE
# ============================================================================

def run_unified_agent(
    query: str,
    has_files: bool = False,
    file_ids: Optional[List[str]] = None,
    max_iter: int = 1
) -> Dict[str, Any]:
    """
    Point d'entrée unique pour tous les types de requêtes
    
    Args:
        query: Question de l'utilisateur
        has_files: Si des fichiers ont été uploadés
        file_ids: Liste des doc_ids si has_files=True
        max_iter: Nombre max d'itérations RETHINK
    
    Returns:
        {
            "answer": str,
            "sources": List[Dict],
            "verification": Dict,
            "iterations": int,
            "steps": List[str],
            "mode": "rag" | "web" | "hybrid"
        }
    """
    logger.info(f"🚀 Running unified agent: query='{query[:60]}...', has_files={has_files}")
    
    agent = build_unified_agent(max_iter=max_iter)
    
    initial_state: UnifiedAgentState = {
        "query": query,
        "has_files": has_files,
        "file_ids": file_ids or [],
        "iter": 0,
        "max_iter": max_iter,
        "steps": [],
        "consecutive_failures": 0,
        "last_context_length": 0
    }
    
    # Invoke with increased recursion limit
    final_state = agent.invoke(
        initial_state,
        config={"recursion_limit": 50}  # Increase from default 25
    )
    
    # Déterminer le mode effectif
    strategy = final_state.get("search_strategy", "web")
    mode_map = {
        "web": "web",
        "documents": "rag",
        "hybrid": "hybrid"
    }
    
    return {
        "answer": final_state.get("final_answer", ""),
        "sources": final_state.get("all_sources", []),
        "verification": final_state.get("verification", {}),
        "iterations": final_state.get("iter", 0),
        "steps": final_state.get("steps", []),
        "mode": mode_map.get(strategy, "unknown")
    }


def run_unified_agent_streaming(query: str, has_files: bool = False, file_ids: list = None, max_iter: int = 1, step_callback=None):
    """
    Version streaming de run_unified_agent qui appelle un callback pour chaque step
    
    Args:
        query: Question de l'utilisateur
        has_files: Si des fichiers ont été uploadés
        file_ids: Liste des doc_ids si has_files=True
        max_iter: Nombre max d'itérations RETHINK
        step_callback: Fonction appelée avec chaque nouveau step (signature: callback(step: str))
    
    Returns:
        Même format que run_unified_agent
    """
    # Set callback
    set_step_callback(step_callback)
    
    try:
        result = run_unified_agent(query, has_files, file_ids, max_iter)
        return result
    finally:
        # Always clear callback
        clear_step_callback()