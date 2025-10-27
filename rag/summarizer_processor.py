"""
Summarization Processing module with adaptive strategies.
Follows the architecture pattern from process/ modules.
"""

import os
import logging
from typing import List, Optional
from langchain.chains.summarize import load_summarize_chain
from langchain_core.documents import Document
from langchain_openai import ChatOpenAI
from langchain_ollama import ChatOllama
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.prompts import PromptTemplate
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

# Configuration du logging avancé
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s'
)
logger = logging.getLogger(__name__)


class SummaryResult(BaseModel):
    """Result model for summarization operations"""
    success: bool
    summary: str = ""
    error: Optional[str] = None
    metadata: dict = {}


class SummarizerProcessor:
    """
    Summarization processor with adaptive strategies.
    Automatically chooses the best approach based on document size and provider.
    """
    
    def __init__(
        self,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        strategy: Optional[str] = None
    ):
        """
        Initialize summarizer processor
        
        Args:
            provider: LLM provider ('ollama' or 'openai')
            model: Model name override
            strategy: Strategy override ('refine' or 'map_reduce')
        """
        self.provider = (provider or os.getenv("LLM_PROVIDER", "ollama")).lower()
        self.strategy_override = strategy
        
        # Get model name
        if model:
            self.model_name = model
        elif self.provider == "openai":
            self.model_name = os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini")
        else:
            self.model_name = os.getenv("OLLAMA_CHAT_MODEL", "llama3:latest")
        
        logger.info(f"📝 SummarizerProcessor initialized (provider={self.provider}, model={self.model_name})")

    def _get_llm(self):
        """Get LLM instance based on provider"""
        if self.provider == "openai":
            return ChatOpenAI(model=self.model_name, temperature=0.2)
        
        # Ollama
        try:
            num_ctx = int(os.getenv("OLLAMA_NUM_CTX", "32768")) or None
        except ValueError:
            num_ctx = None
        
        return ChatOllama(model=self.model_name, temperature=0.2, num_ctx=num_ctx)

    def _get_total_chars(self, docs: List[Document]) -> int:
        """Calculate total character count across all documents"""
        return sum(len(doc.page_content) for doc in docs)

    def _summarize_with_refine(self, llm, docs: List[Document]) -> str:
        """
        Use refine chain for sequential processing.
        Better for local models with limited context.
        """
        logger.info("🔄 Using REFINE strategy")
        
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

    def _summarize_with_map_reduce(self, llm, docs: List[Document], batch_size: int = 3) -> str:
        """
        Use map_reduce with batches.
        Better for OpenAI with larger context.
        """
        logger.info(f"🗺️  Using MAP_REDUCE strategy (batch_size={batch_size})")
        
        map_template = """Summarize this text briefly:

{text}

BRIEF SUMMARY:"""
        
        combine_template = """Combine these summaries into one coherent summary:

{text}

FINAL SUMMARY:"""
        
        MAP_PROMPT = PromptTemplate.from_template(map_template)
        COMBINE_PROMPT = PromptTemplate.from_template(combine_template)
        
        # Process in batches
        all_summaries = []
        
        for i in range(0, len(docs), batch_size):
            batch = docs[i:i + batch_size]
            logger.debug(f"  Processing batch {i//batch_size + 1}/{(len(docs) + batch_size - 1)//batch_size}")
            
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
                    logger.warning(f"⚠️  Failed to summarize a chunk: {e}")
                    continue
            
            if batch_summaries:
                all_summaries.extend(batch_summaries)
        
        if not all_summaries:
            return "Unable to generate summary."
        
        # Hierarchical reduction if too many summaries
        while len(all_summaries) > batch_size:
            logger.debug(f"  Reducing {len(all_summaries)} summaries...")
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
                    logger.warning(f"⚠️  Failed to combine summaries: {e}")
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
            logger.error(f"❌ Error in final combine: {e}")
            return all_summaries[0].page_content if all_summaries else "Summary generation failed."

    def summarize(self, documents: List[Document]) -> SummaryResult:
        """
        Adaptive summarization that chooses the best strategy.
        
        Args:
            documents: List of documents to summarize
            
        Returns:
            SummaryResult with summary and metadata
        """
        try:
            logger.info(f"📝 Starting summarization of {len(documents)} documents")
            
            if not documents:
                return SummaryResult(
                    success=False,
                    error="No documents to summarize"
                )
            
            # Calculate total content size
            total_chars = self._get_total_chars(documents)
            logger.info(f"📏 Total characters: {total_chars}")
            
            # Adaptive chunk sizing based on provider
            if self.provider == "ollama":
                max_context_chars = 2500
                chunk_size = 2000
                chunk_overlap = 200
                use_refine = True
            else:
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
            
            logger.info(f"⚙️  Chunk config: size={chunk_size}, overlap={chunk_overlap}")
            
            # Split documents into manageable chunks
            splitter = RecursiveCharacterTextSplitter(
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap
            )
            split_docs = splitter.split_documents(documents)
            logger.info(f"✂️  Split into {len(split_docs)} chunks")
            
            # Get LLM
            llm = self._get_llm()
            
            # Choose strategy
            if total_chars <= max_context_chars and len(split_docs) <= 3:
                # Small document - direct summarization
                logger.info("📄 Strategy: Direct (fits in context)")
                try:
                    chain = load_summarize_chain(llm, chain_type="stuff", verbose=False)
                    result = chain.invoke({"input_documents": split_docs})
                    summary = result.get("output_text", str(result)) if isinstance(result, dict) else str(result)
                    
                    return SummaryResult(
                        success=True,
                        summary=summary,
                        metadata={
                            "strategy": "direct",
                            "chunks": len(split_docs),
                            "total_chars": total_chars,
                            "provider": self.provider,
                            "model": self.model_name
                        }
                    )
                except Exception as e:
                    logger.warning(f"⚠️  Direct summarization failed: {e}, falling back to refine")
                    use_refine = True
            
            # Check for strategy override
            strategy_override = self.strategy_override or os.getenv("SUMMARY_STRATEGY", "").lower()
            
            if strategy_override == "refine":
                summary = self._summarize_with_refine(llm, split_docs)
                strategy = "refine"
            elif strategy_override == "map_reduce":
                batch_size = 3 if self.provider == "ollama" else 5
                try:
                    batch_size = int(os.getenv("SUMMARY_BATCH_DOCS", str(batch_size)))
                except ValueError:
                    pass
                summary = self._summarize_with_map_reduce(llm, split_docs, batch_size)
                strategy = "map_reduce"
            elif use_refine:
                summary = self._summarize_with_refine(llm, split_docs)
                strategy = "refine"
            else:
                batch_size = 3 if self.provider == "ollama" else 5
                try:
                    batch_size = int(os.getenv("SUMMARY_BATCH_DOCS", str(batch_size)))
                except ValueError:
                    pass
                summary = self._summarize_with_map_reduce(llm, split_docs, batch_size)
                strategy = "map_reduce"
            
            logger.info("✅ Summarization complete")
            
            return SummaryResult(
                success=True,
                summary=summary,
                metadata={
                    "strategy": strategy,
                    "chunks": len(split_docs),
                    "total_chars": total_chars,
                    "provider": self.provider,
                    "model": self.model_name
                }
            )
            
        except Exception as e:
            logger.error(f"❌ Summarization error: {str(e)}")
            return SummaryResult(
                success=False,
                error=f"Summarization failed: {str(e)}"
            )
