import os
from typing import List
from dotenv import load_dotenv
from langchain.chains.summarize import load_summarize_chain
from langchain_core.documents import Document
from langchain_openai import ChatOpenAI
from langchain_ollama import ChatOllama
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.prompts import PromptTemplate

load_dotenv() 


def _get_llm():
    provider = os.getenv("LLM_PROVIDER", "ollama").lower()
    if provider == "openai":
        model = os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini")
        return ChatOpenAI(model=model, temperature=0.2)
    # Only use OLLAMA_CHAT_MODEL for chat to avoid embedding model misuse
    model = os.getenv("OLLAMA_CHAT_MODEL", "llama3:latest")
    # Allow larger context for Ollama models via env
    try:
        num_ctx = int(os.getenv("OLLAMA_NUM_CTX", "32768")) or None
    except ValueError:
        num_ctx = None
    return ChatOllama(model=model, temperature=0.2, num_ctx=num_ctx)


def _get_total_chars(docs: List[Document]) -> int:
    """Calculate total character count across all documents"""
    return sum(len(doc.page_content) for doc in docs)


def _summarize_with_refine(llm, docs: List[Document]) -> str:
    """
    Use refine chain for sequential processing - better for local models with limited context.
    Each document is processed one at a time, refining the summary progressively.
    """
    # Custom prompts to minimize token usage
    prompt_template = """Write a concise summary of the following text:

{text}

CONCISE SUMMARY:"""
    
    refine_template = """Your task is to produce a final summary.
We have provided an existing summary up to a certain point: {existing_answer}

Below is additional context:
{text}

Refine the existing summary with the new context. If the context isn't useful, return the original summary.
REFINED SUMMARY:"""
    
    PROMPT = PromptTemplate.from_template(prompt_template)
    REFINE_PROMPT = PromptTemplate.from_template(refine_template)
    
    chain = load_summarize_chain(
        llm,
        chain_type="refine",
        question_prompt=PROMPT,
        refine_prompt=REFINE_PROMPT,
        return_intermediate_steps=False,
        verbose=False
    )
    
    result = chain.invoke({"input_documents": docs})
    if isinstance(result, dict) and "output_text" in result:
        return result["output_text"]
    return str(result)


def _summarize_with_map_reduce(llm, docs: List[Document], batch_size: int = 3) -> str:
    """
    Use map_reduce with very small batches for local models.
    Process in strict batches to avoid context overflow.
    """
    # Custom prompts to minimize token usage
    map_template = """Summarize this text briefly:

{text}

BRIEF SUMMARY:"""
    
    combine_template = """Combine these summaries into one coherent summary:

{text}

FINAL SUMMARY:"""
    
    MAP_PROMPT = PromptTemplate.from_template(map_template)
    COMBINE_PROMPT = PromptTemplate.from_template(combine_template)
    
    # Process in batches to avoid overwhelming the context window
    all_summaries = []
    
    for i in range(0, len(docs), batch_size):
        batch = docs[i:i + batch_size]
        print(f"Processing batch {i//batch_size + 1}/{(len(docs) + batch_size - 1)//batch_size}")
        
        # Map step: summarize each doc in the batch
        batch_summaries = []
        for doc in batch:
            try:
                chain = load_summarize_chain(
                    llm,
                    chain_type="stuff",
                    prompt=MAP_PROMPT,
                    verbose=False
                )
                result = chain.invoke({"input_documents": [doc]})
                summary = result.get("output_text", str(result)) if isinstance(result, dict) else str(result)
                batch_summaries.append(Document(page_content=summary))
            except Exception as e:
                print(f"Warning: Failed to summarize a document chunk: {e}")
                continue
        
        if batch_summaries:
            all_summaries.extend(batch_summaries)
    
    # Reduce step: combine all summaries
    if not all_summaries:
        return "Unable to generate summary."
    
    # If we have too many summaries, reduce them hierarchically
    while len(all_summaries) > batch_size:
        print(f"Reducing {len(all_summaries)} summaries...")
        next_level = []
        for i in range(0, len(all_summaries), batch_size):
            batch = all_summaries[i:i + batch_size]
            try:
                chain = load_summarize_chain(
                    llm,
                    chain_type="stuff",
                    prompt=COMBINE_PROMPT,
                    verbose=False
                )
                result = chain.invoke({"input_documents": batch})
                summary = result.get("output_text", str(result)) if isinstance(result, dict) else str(result)
                next_level.append(Document(page_content=summary))
            except Exception as e:
                print(f"Warning: Failed to combine summaries: {e}")
                continue
        all_summaries = next_level
    
    # Final combine
    try:
        chain = load_summarize_chain(
            llm,
            chain_type="stuff",
            prompt=COMBINE_PROMPT,
            verbose=False
        )
        result = chain.invoke({"input_documents": all_summaries})
        return result.get("output_text", str(result)) if isinstance(result, dict) else str(result)
    except Exception as e:
        print(f"Error in final combine: {e}")
        return all_summaries[0].page_content if all_summaries else "Summary generation failed."


def summarize(documents: List[Document]) -> str:
    """
    Adaptive summarization strategy that works efficiently with local models.
    Automatically chooses the best approach based on document size.
    """
    print("Starting adaptive summarization...")
    if not documents:
        return ""
    
    # Calculate total content size
    total_chars = _get_total_chars(documents)
    print(f"Total characters to summarize: {total_chars}")
    
    # Get configuration
    provider = os.getenv("LLM_PROVIDER", "ollama").lower()
    
    # Adaptive chunk sizing based on total content and provider
    if provider == "ollama":
        # For local models, use smaller chunks and refine strategy
        # Estimate ~3-4 chars per token, model context is typically 2048-4096 for llama3
        max_context_chars = 2500  # Safe limit for local models (~600-800 tokens)
        chunk_size = 2000
        chunk_overlap = 200
        use_refine = True  # Refine is more reliable for local models
    else:
        # For OpenAI, we can use larger chunks
        max_context_chars = 12000
        chunk_size = 4000
        chunk_overlap = 400
        use_refine = False
    
    # Override with env vars if provided
    try:
        chunk_size = int(os.getenv("SUMMARY_CHUNK_CHARS", str(chunk_size)))
        chunk_overlap = int(os.getenv("SUMMARY_CHUNK_OVERLAP", str(chunk_overlap)))
    except ValueError:
        pass
    
    print(f"Using chunk_size={chunk_size}, chunk_overlap={chunk_overlap}")
    
    # Split documents into manageable chunks
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap
    )
    split_docs = splitter.split_documents(documents)
    print(f"Split into {len(split_docs)} chunks")
    
    # Get LLM
    llm = _get_llm()
    
    # Choose strategy based on content size and number of chunks
    if total_chars <= max_context_chars and len(split_docs) <= 3:
        # Small document - use simple stuff chain
        print("Strategy: Direct summarization (document fits in context)")
        try:
            chain = load_summarize_chain(llm, chain_type="stuff", verbose=False)
            result = chain.invoke({"input_documents": split_docs})
            summary = result.get("output_text", str(result)) if isinstance(result, dict) else str(result)
            print("Summarization complete")
            return summary
        except Exception as e:
            print(f"Direct summarization failed: {e}, falling back to refine")
            use_refine = True
    
    # For larger documents, choose between refine and map_reduce
    # Check if user explicitly set a strategy in .env
    strategy_override = os.getenv("SUMMARY_STRATEGY", "").lower()
    
    if strategy_override == "refine":
        # User explicitly wants refine
        print(f"Strategy: Refine (processing {len(split_docs)} chunks sequentially)")
        summary = _summarize_with_refine(llm, split_docs)
    elif strategy_override == "map_reduce":
        # User explicitly wants map_reduce
        print(f"Strategy: Map-Reduce (processing {len(split_docs)} chunks in batches)")
        batch_size = 3 if provider == "ollama" else 5
        try:
            batch_size = int(os.getenv("SUMMARY_BATCH_DOCS", str(batch_size)))
        except ValueError:
            pass
        summary = _summarize_with_map_reduce(llm, split_docs, batch_size)
    elif use_refine:
        # No override, use default based on provider (refine for ollama)
        print(f"Strategy: Refine (default for {provider}, processing {len(split_docs)} chunks sequentially)")
        summary = _summarize_with_refine(llm, split_docs)
    else:
        # No override, use default (map_reduce for openai)
        print(f"Strategy: Map-Reduce (default for {provider}, processing {len(split_docs)} chunks in batches)")
        batch_size = 3 if provider == "ollama" else 5
        try:
            batch_size = int(os.getenv("SUMMARY_BATCH_DOCS", str(batch_size)))
        except ValueError:
            pass
        summary = _summarize_with_map_reduce(llm, split_docs, batch_size)
    
    print("Summarization complete")
    return summary