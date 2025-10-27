"""
Question Answering Processor with LLM.
Generates answers from retrieved context using Ollama/OpenAI.
"""

import os
import logging
from typing import Dict, Any, Optional
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI
from langchain.prompts import PromptTemplate
from langchain.chains import LLMChain
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

# Configuration du logging avancé
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s'
)
logger = logging.getLogger(__name__)


class QAResult(BaseModel):
    """Result model for QA operations"""
    success: bool
    answer: str = ""
    error: Optional[str] = None
    metadata: dict = {}


class QAProcessor:
    """
    Question Answering processor using LLM.
    Takes a question and context, generates a natural language answer.
    """
    
    def __init__(
        self,
        provider: Optional[str] = None,
        model: Optional[str] = None
    ):
        """
        Initialize QA processor
        
        Args:
            provider: LLM provider ('ollama' or 'openai')
            model: Model name override
        """
        self.provider = (provider or os.getenv("LLM_PROVIDER", "ollama")).lower()
        
        # Get model name
        if model:
            self.model_name = model
        elif self.provider == "openai":
            self.model_name = os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini")
        else:
            self.model_name = os.getenv("OLLAMA_CHAT_MODEL", "llama3:latest")
        
        logger.info(f"💬 QAProcessor initialized (provider={self.provider}, model={self.model_name})")

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

    def answer_question(
        self,
        question: str,
        context: str,
        sources: list = None
    ) -> QAResult:
        """
        Generate an answer to a question based on retrieved context
        
        Args:
            question: User's question
            context: Retrieved context from documents
            sources: List of source metadata
            
        Returns:
            QAResult with generated answer
        """
        try:
            logger.info(f"💬 Generating answer for: '{question}'")
            
            if not context or context.strip() == "":
                return QAResult(
                    success=False,
                    error="No context provided for answering"
                )
            
            # Create prompt template
            template = """You are a helpful AI assistant that answers questions based on the provided context.

Context from documents:
{context}

Question: {question}

Instructions:
- Answer the question directly and concisely based ONLY on the information in the context
- If the context contains the answer, provide it clearly
- If the context doesn't contain enough information, say so honestly
- Cite sources using [1], [2], etc. when referencing specific information
- Keep your answer focused and relevant

Answer:"""

            prompt = PromptTemplate(
                template=template,
                input_variables=["context", "question"]
            )
            
            # Get LLM
            llm = self._get_llm()
            
            # Create chain for initial answer
            chain = LLMChain(llm=llm, prompt=prompt, verbose=False)

            # Step 1: Draft answer (Think/Act)
            draft_resp = chain.invoke({
                "context": context,
                "question": question
            })
            draft_answer = draft_resp.get("text", str(draft_resp)) if isinstance(draft_resp, dict) else str(draft_resp)

            # Step 2: Verify and Rethink loop (Observe/Verify/Rethink)
            verify_template = PromptTemplate(
                template=(
                    "You are a meticulous reviewer. Only use the provided context to verify the answer.\n"
                    "Context:\n{context}\n\n"
                    "Question: {question}\n\n"
                    "Answer Draft:\n{answer}\n\n"
                    "Instructions:\n"
                    "- Check if the answer strictly follows the context (no hallucinations).\n"
                    "- Check if claims are supported and if citations [1], [2], ... are used when referencing context.\n"
                    "- If anything is unsupported or missing, propose a revised answer strictly grounded in the context, with inline citations.\n"
                    "- Keep a concise style; if the context is insufficient, say so clearly.\n\n"
                    "Return a compact JSON object with keys: verdict ('pass'|'revise'), reasons (string), revised_answer (string|null)."
                ),
                input_variables=["context", "question", "answer"],
            )

            verify_chain = LLMChain(llm=llm, prompt=verify_template, verbose=False)
            verify_resp = verify_chain.invoke({
                "context": context,
                "question": question,
                "answer": draft_answer,
            })

            verify_text = verify_resp.get("text", str(verify_resp)) if isinstance(verify_resp, dict) else str(verify_resp)

            # Robust JSON parsing with graceful fallback
            decision = {"verdict": "pass", "reasons": "", "revised_answer": None}
            try:
                import json as _json
                # Try to extract JSON if the model wrapped it in text
                start = verify_text.find("{")
                end = verify_text.rfind("}")
                if start != -1 and end != -1 and end > start:
                    decision = _json.loads(verify_text[start:end+1])
                else:
                    decision = _json.loads(verify_text)
            except Exception:
                logger.debug("Verification JSON parsing failed; using draft answer.")

            final_answer = draft_answer.strip()
            if isinstance(decision, dict) and decision.get("verdict") == "revise":
                revised = decision.get("revised_answer")
                if revised and isinstance(revised, str) and revised.strip():
                    final_answer = revised.strip()

            logger.info(f"✅ Answer generated ({len(final_answer)} chars) [verify={decision.get('verdict','pass')}]")

            return QAResult(
                success=True,
                answer=final_answer,
                metadata={
                    "provider": self.provider,
                    "model": self.model_name,
                    "question": question,
                    "context_length": len(context),
                    "sources_count": len(sources) if sources else 0,
                    "verification": decision if isinstance(decision, dict) else {"verdict": "pass"}
                }
            )
            
        except Exception as e:
            logger.error(f"❌ Error generating answer: {str(e)}")
            return QAResult(
                success=False,
                error=f"Failed to generate answer: {str(e)}"
            )
