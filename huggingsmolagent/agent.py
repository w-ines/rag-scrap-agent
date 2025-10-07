# Modifiez /home/ines/cnrs-work/api-python/smolagents/agent.py
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
import asyncio
import json
# Importer les classes factices au lieu de smolagents
from smolagents import CodeAgent, Tool,OpenAIServerModel
try:
    from .streaming_handler import streaming_manager
except Exception:
    streaming_manager = None
# from huggingsmolagent.tools.retrieval_tool import RetrieverTool
from huggingsmolagent.tools.scraper import webscraper, web_search
from huggingsmolagent.tools.vector_store import retrieve_knowledge
import os.path
import logging
import os
import time
import yaml 
import re
import queue
import threading
from typing import AsyncGenerator
import traceback

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("agent_debug.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("smolagents.agent")

app = FastAPI()

# Custom Log Handler to capture steps
class ListLogHandler(logging.Handler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.log_records = []

    def emit(self, record):
        self.log_records.append(self.format(record))


def extract_final_answer(llm_response: str) -> str:
    """
    Extracts the final user response from structured LLM output (Thought/Code/Observation/final_answer).
    """
    if llm_response is None:
        return ""
    
    # Convert to string if it's not already
    if not isinstance(llm_response, str):
        llm_response = str(llm_response)

    # Look for final_answer pattern with better regex handling
    match = re.search(r'final_answer\("([^"]*)"\)', llm_response, re.DOTALL)
    if not match:
        match = re.search(r"final_answer\('([^']*)'\)", llm_response, re.DOTALL)
    
    if match:
        answer = match.group(1).strip()
        return format_json_response(answer)
    
    # Try to find the Out - Final answer: line
    match = re.search(r"Out - Final answer: (.*?)$", llm_response, re.MULTILINE)
    if match:
        answer = match.group(1).strip()
        return format_json_response(answer)
    
    # Look for "Final answer:" pattern 
    match = re.search(r"Final answer:\s*(.*?)$", llm_response, re.MULTILINE | re.IGNORECASE)
    if match:
        answer = match.group(1).strip()
        return format_json_response(answer)
        
    # Fallback: return the last non-empty line
    lines = [line for line in llm_response.strip().split("\n") if line.strip()]
    if lines:
        # Try to avoid returning object representations
        for line in reversed(lines):
            if not line.startswith(("ActionStep(", "MessageRole.", "<")):
                cleaned_line = line.strip()
                return format_json_response(cleaned_line)
    return "Unable to extract final answer"

def format_json_response(response: str) -> str:
    """
    Formats JSON responses into human-readable text using a universal approach.
    """
    if not response:
        return response
    
    # Check if response looks like JSON
    response = response.strip()
    if response.startswith('{') and response.endswith('}'):
        try:
            import json
            data = json.loads(response)
            return format_any_json(data, "Information")
        except (json.JSONDecodeError, ValueError):
            pass
    elif response.startswith('[') and response.endswith(']'):
        try:
            import json
            data = json.loads(response)
            return format_any_json(data, "Results")
        except (json.JSONDecodeError, ValueError):
            pass
    
    return response

def format_any_json(data, title="Data", level=0) -> str:
    """
    Universal JSON formatter that works with any structure.
    Converts any JSON into beautiful, readable text.
    """
    if data is None:
        return "No data available."
    
    # Indent for nested structures
    indent = "  " * level
    
    # Handle different data types
    if isinstance(data, dict):
        if not data:
            return f"**{title}:** Empty"
        
        formatted = f"**{title}:**\n\n" if level == 0 else f"{indent}**{title}:**\n"
        
        for key, value in data.items():
            # Clean up key name
            clean_key = key.replace("_", " ").replace("-", " ").title()
            
            if isinstance(value, dict):
                if value:  # Non-empty dict
                    formatted += f"{indent}🔹 **{clean_key}:**\n"
                    formatted += format_any_json(value, "", level + 1)
                else:
                    formatted += f"{indent}🔹 **{clean_key}:** Empty\n"
                    
            elif isinstance(value, list):
                if value:  # Non-empty list
                    formatted += f"{indent}🔹 **{clean_key}:** {len(value)} item(s)\n"
                    formatted += format_any_json(value, "", level + 1)
                else:
                    formatted += f"{indent}🔹 **{clean_key}:** Empty list\n"
                    
            else:
                # Simple value (string, number, boolean)
                value_str = str(value)
                if len(value_str) > 100:
                    value_str = value_str[:100] + "..."
                formatted += f"{indent}🔹 **{clean_key}:** {value_str}\n"
        
        return formatted
    
    elif isinstance(data, list):
        if not data:
            return f"{indent}No items found.\n"
        
        formatted = ""
        
        # Handle list of items
        for i, item in enumerate(data):
            if i >= 15:  # Limit to first 15 items
                remaining = len(data) - i
                formatted += f"{indent}... and {remaining} more item(s)\n"
                break
                
            if isinstance(item, dict):
                # For dict items, show key-value pairs nicely
                if item:
                    formatted += f"{indent}**{i+1}.** "
                    
                    # Get the most important fields first
                    important_keys = []
                    for key in item.keys():
                        if any(word in key.lower() for word in ['name', 'title', 'team', 'home', 'away']):
                            important_keys.append(key)
                    
                    # Add remaining keys
                    other_keys = [k for k in item.keys() if k not in important_keys]
                    all_keys = important_keys + other_keys
                    
                    # Show first few key-value pairs
                    key_values = []
                    for key in all_keys[:3]:  # Show first 3 fields
                        clean_key = key.replace("_", " ").replace("-", " ").title()
                        value = item[key]
                        value_str = str(value)
                        if len(value_str) > 50:
                            value_str = value_str[:50] + "..."
                        key_values.append(f"{clean_key}: {value_str}")
                    
                    formatted += " | ".join(key_values)
                    
                    if len(item) > 3:
                        formatted += f" | (+{len(item)-3} more fields)"
                    
                    formatted += "\n"
                else:
                    formatted += f"{indent}**{i+1}.** Empty item\n"
                    
            elif isinstance(item, list):
                formatted += f"{indent}**{i+1}.** List with {len(item)} items\n"
                
            else:
                # Simple item (string, number, etc.)
                item_str = str(item)
                if len(item_str) > 100:
                    item_str = item_str[:100] + "..."
                formatted += f"{indent}**{i+1}.** {item_str}\n"
        
        return formatted
    
    else:
        # Simple value
        return f"{indent}{str(data)}\n"

class ComplexRequest(BaseModel):
    chatSettings: Optional[Dict[str, Any]] = None
    messages: Optional[List[Dict[str, Any]]] = None
    selectedTools: Optional[List[Dict[str, Any]]] = None
    toolsQuery: Optional[str] = None
    conversationId: Optional[str] = None

# Keep the original model for backward compatibility
class QueryRequest(BaseModel):
    query: str

class AgentResponse(BaseModel):
    response: str
    steps: Optional[List[str]] = None
    paper: Optional[Dict[str, Any]] = None
    canHandle: bool = False


# In-memory per-conversation memory store
conversation_memory: Dict[str, Dict[str, Any]] = {}

def update_conversation_memory(conversation_id: Optional[str], final_text: str):
    """
    Store simple, useful facts extracted from the final answer into memory.
    This is intentionally lightweight and domain-agnostic.
    """
    if not conversation_id or not final_text:
        return

    mem = conversation_memory.setdefault(conversation_id, {"facts": []})

    facts = mem.get("facts", [])

    # Example: extract GitHub star facts like "React GitHub stars: 153k" or "React has around 153k GitHub stars"
    star_patterns = [
        r"\b([A-Z][A-Za-z0-9_.+-]+)\s+GitHub\s+stars\s*:\s*([^\n]+)",
        r"\b([A-Z][A-Za-z0-9_.+-]+)\s+has\s+(?:around\s+)?([0-9.,kKmM]+)\s+GitHub\s+stars\b",
    ]

    new_facts = []
    for pat in star_patterns:
        for m in re.finditer(pat, final_text):
            try:
                subject = m.group(1).strip()
                value = m.group(2).strip()
                new_facts.append({"type": "github_stars", "subject": subject, "value": value})
            except Exception:
                continue

    # De-duplicate by (type, subject)
    existing_keys = {(f.get("type"), f.get("subject")) for f in facts}
    for f in new_facts:
        key = (f.get("type"), f.get("subject"))
        if key not in existing_keys:
            facts.append(f)
            existing_keys.add(key)

    # Keep memory bounded
    if len(facts) > 50:
        facts = facts[-50:]

    mem["facts"] = facts
    conversation_memory[conversation_id] = mem

def build_memory_context(conversation_id: Optional[str]) -> str:
    """
    Create a short, general-purpose memory summary for the model.
    """
    if not conversation_id:
        return ""
    mem = conversation_memory.get(conversation_id)
    if not mem:
        return ""

    lines: List[str] = []
    facts = mem.get("facts", [])
    if facts:
        lines.append("Known facts from this conversation (may be approximate):")
        # show a few highest-signal facts first
        for f in facts[-10:]:
            if f.get("type") == "github_stars":
                lines.append(f"- {f.get('subject')} GitHub stars: {f.get('value')}")

    return "\n".join(lines)

def build_history_context(
    messages: Optional[List[Dict[str, Any]]],
    current_query: Optional[str] = None,
    max_turns: int = 8,
    max_chars_per_turn: int = 300,
    max_total_chars: int = 2000,
) -> str:
    """
    Construct a compact, model-friendly conversation context from past turns.

    - Uses only user/assistant roles
    - Excludes the current user message if duplicated in history
    - Truncates each turn and caps total context length
    """
    if not messages:
        return ""

    # Keep only user/assistant roles and strip empty content
    filtered: List[Dict[str, Any]] = []
    for m in messages:
        role = m.get("role")
        content = (m.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            filtered.append({"role": role, "content": content})

    if not filtered:
        return ""

    # Drop the last user message if it's exactly the current query (to avoid duplication)
    if current_query:
        for i in range(len(filtered) - 1, -1, -1):
            if filtered[i]["role"] == "user" and filtered[i]["content"] == current_query:
                filtered.pop(i)
                break

    # Take last N relevant turns
    recent = filtered[-(max_turns * 2) :]

    # Format chronologically
    lines: List[str] = []
    total_chars = 0
    for turn in recent:
        prefix = "User" if turn["role"] == "user" else "Assistant"
        content = turn["content"]
        # Trim overly long turns
        if len(content) > max_chars_per_turn:
            content = content[: max_chars_per_turn - 1] + "…"

        candidate_line = f"- {prefix}: {content}"
        if total_chars + len(candidate_line) > max_total_chars:
            break
        lines.append(candidate_line)
        total_chars += len(candidate_line)

    if not lines:
        return ""

    return "\n".join(lines)

def is_simple_query(query: str) -> bool:
    """
    Detects if a query is simple enough to bypass the agent workflow.
    
    Simple queries include:
    - Greetings (hi, hello, hey)
    - Basic questions (how are you, what's up)
    - Thanks (thank you, thanks)
    """
    simple_patterns = [
        r"^(hi|hello|hey|greetings)(\s+there)?(!|\.|)?$",
        r"^(how are you|what'?s up|how'?s it going|how do you do)(\?|\.|!)?$",
        r"^(thanks|thank you|ty)(\s+so much)?(!|\.|)?$"
    ]
    
    query = query.lower().strip()
    return any(re.match(pattern, query) for pattern in simple_patterns)

# Add simple responses for direct handling
SIMPLE_RESPONSES = {
    "greeting": "Hello! I'm your AI assistant. How can I help you today?",
    "how_are_you": "I'm doing well, thank you for asking! How can I assist you?",
    "thanks": "You're welcome! Is there anything else I can help you with?"
}

def get_simple_response(query: str) -> str:
    """Returns an appropriate simple response based on query type"""
    query = query.lower().strip()
    logger.debug(f"query: {query}")

    if re.match(r"^(hi|hello|hey|greetings)(\s+there)?(!|\.|)?$", query):
        return SIMPLE_RESPONSES["greeting"]
    elif re.match(r"^(how are you|what'?s up|how'?s it going|how do you do)(\?|\.|!)?$", query):
        return SIMPLE_RESPONSES["how_are_you"]
    elif re.match(r"^(thanks|thank you|ty)(\s+so much)?(!|\.|)?$", query):
        return SIMPLE_RESPONSES["thanks"]
    
    return None 


def parse_agent_steps(agent_output: str) -> List[str]:
    """
    Parses the agent's raw output string to extract human-readable steps.
    """    
    if not agent_output:
        return []
        
    steps = []
    current_step = []
    
    for line in agent_output.strip().split('\n'):
        if line.startswith(("Thought:", "Action:", "Observation:")):
            if current_step:
                formatted_step = format_step(" ".join(current_step))
                steps.append(formatted_step)
            current_step = [line]
        elif line.strip():
            current_step.append(line)
            
    if current_step:
        formatted_step = format_step(" ".join(current_step))
        steps.append(formatted_step)
        
    return steps
def format_step(step: str) -> str:
    """
    Formats and cleans a step for display by removing unwanted patterns and Python objects.
    """
    if not step or not isinstance(step, str):
        return ""
        
    # Convert to string if it's not already
    step_str = str(step)
    
    # Remove common unwanted patterns
    patterns_to_remove = [
        r"ActionStep\([^)]*\)",
        r"<MessageRole\.[^>]*>",
        r"MessageRole\.[A-Z_]+",
        r"tool_calls=\[[^\]]*\]",
        r"model_input_messages=\[[^\]]*\]",
        r"start_time=[\d.]+",
        r"end_time=[\d.]+",
        r"step_number=\d+",
        r"duration=[\d.]+",
        r"observations_images=None",
        r"action_output=None",
        r"error=[^,)]*",
        r"'role': <[^>]*>",
        r"'content': \[.*?\]",
        r"ToolCall\([^)]*\)",
        r"ChatMessage\([^)]*\)",
        r"ChatCompletion\([^)]*\)",
        r"CompletionUsage\([^)]*\)",
    ]
    
    # Apply all removal patterns
    for pattern in patterns_to_remove:
        step_str = re.sub(pattern, "", step_str, flags=re.DOTALL)
    
    # Extract useful information
    if "Thought:" in step_str and "Code:" in step_str:
        # Extract Thought and Code sections
        thought_match = re.search(r"Thought:\s*([^C]*?)(?=Code:|$)", step_str, re.DOTALL)
        code_match = re.search(r"Code:\s*```(?:python|py)?\s*(.*?)```", step_str, re.DOTALL)
        
        if thought_match:
            thought = thought_match.group(1).strip()
        else:
            thought = ""
            
        if code_match:
            code = code_match.group(1).strip()
        else:
            code = ""
        
        if thought or code:
            formatted = ""
            if thought:
                formatted += f"💭 **Thought:** {thought}\n"
            if code:
                formatted += f"💻 **Code:**\n```python\n{code}\n```"
            return formatted
    
    # Try to extract just the meaningful text
    if "model_output=" in step_str:
        output_match = re.search(r"model_output='([^']*)'", step_str)
        if output_match:
            return f"🤖 **Agent Output:** {output_match.group(1)}"
    
    # If it's just a simple string, clean and return it
    step_str = re.sub(r"\s+", " ", step_str)  # Normalize whitespace
    step_str = step_str.strip()
    
    # Remove very technical/debug info
    if any(term in step_str.lower() for term in ["actionstep", "messagero", "toolcall", "chatcompletion"]):
        return ""
    
    # If the step is too short or empty after cleaning, return empty
    if len(step_str) < 10:
        return ""
    
    return f"📝 **Step:** {step_str}"

class StepTracker:
    def __init__(self):
        self.steps = []
        self.current_thought = ""
        self.current_code = ""
        self.current_observation = ""
        
    def __call__(self, step: str):
        """This method is called by the agent at each step"""
        print(f"🔍 StepTracker called with: {step}")
        formatted_step = self.format_step_realtime(step)
        if formatted_step:
            self.steps.append(formatted_step)
            print(f"🔍 Step added: {formatted_step}")
            
    def format_step_realtime(self, step_content) -> List[str]:
        """Format steps in real-time for readable display, returning multiple formatted steps"""
        if not step_content:
            return []
        
        # Convert to string if it's an ActionStep object or other complex type
        if hasattr(step_content, 'model_output'):
            # Extract the actual LLM output from ActionStep object
            step_str = step_content.model_output
        elif hasattr(step_content, 'content'):
            step_str = step_content.content
        else:
            step_str = str(step_content).strip()
        
        if not isinstance(step_str, str):
            step_str = str(step_str)
            
        print(f"🔍 Processing step_str: {step_str[:200]}...")
        
        formatted_steps = []
        
        # Extract Thought
        if "Thought:" in step_str:
            thought_match = re.search(r"Thought:\s*(.+?)(?=\nCode:|\n\nCode:|\nAction:|\nObservation:|$)", step_str, re.DOTALL)
            if thought_match:
                thought = thought_match.group(1).strip()
                # Clean up thought text
                thought = re.sub(r'\s+', ' ', thought)
                formatted_steps.append(f"💭 **Thought:** {thought}")
        
        # Extract Code (simplified - only show short code or action summary)
        if "Code:" in step_str:
            # Instead of showing full code, show a summary of what's being executed
            code_match = re.search(r"Code:\s*```(?:python|py)?\s*(.*?)```", step_str, re.DOTALL)
            if code_match:
                code = code_match.group(1).strip()
                # Only show code if it's short and meaningful
                if len(code) < 150 and not any(keyword in code.lower() for keyword in ['visit_webpage', 'webscraper', 'print(']):
                    formatted_steps.append(f"💻 **Code Execution:**\n```python\n{code}\n```")
                else:
                    # Show a summary instead of full code
                    if 'visit_webpage' in code:
                        formatted_steps.append(f"🌐 **Action:** Visiting webpage...")
                    elif 'webscraper' in code:
                        formatted_steps.append(f"🔍 **Action:** Scraping webpage content...")
                    elif 'get_weather' in code:
                        formatted_steps.append(f"🌤️ **Action:** Getting weather information...")
                    elif 'web_search' in code:
                        formatted_steps.append(f"🔎 **Action:** Searching the web...")
                    elif 'final_answer' in code:
                        formatted_steps.append(f"✅ **Action:** Generating final response...")
                    else:
                        formatted_steps.append(f"⚙️ **Action:** Executing code...")
        
        # Extract Action
        if "Action:" in step_str:
            action_match = re.search(r"Action:\s*(.+?)(?=\nThought:|\nCode:|\nObservation:|$)", step_str, re.DOTALL)
            if action_match:
                action = action_match.group(1).strip()
                # Simplify action display
                if len(action) > 200:
                    action = action[:200] + "..."
                formatted_steps.append(f"⚡ **Action:** {action}")
        
        # Extract Observation
        if "Observation:" in step_str:
            obs_match = re.search(r"Observation:\s*(.+?)(?=\nThought:|\nCode:|\nAction:|$)", step_str, re.DOTALL)
            if obs_match:
                observation = obs_match.group(1).strip()
                # Clean observation and limit length significantly
                observation = re.sub(r'\s+', ' ', observation)
                
                # For webpage content, show only summary
                if len(observation) > 150:
                    # Try to extract meaningful summary
                    if 'webpage' in observation.lower() or 'html' in observation.lower():
                        formatted_steps.append(f"👁️ **Observation:** Webpage content retrieved successfully")
                    elif 'error' in observation.lower():
                        # Show errors in full but limit length
                        observation = observation[:200] + "..." if len(observation) > 200 else observation
                        formatted_steps.append(f"❌ **Observation:** {observation}")
                    else:
                        # General case - show first part
                        observation = observation[:150] + "..."
                        formatted_steps.append(f"👁️ **Observation:** {observation}")
                else:
                    formatted_steps.append(f"👁️ **Observation:** {observation}")
        
        # Check for execution results (lines starting with special markers)
        execution_lines = []
        for line in step_str.split('\n'):
            line = line.strip()
            if line.startswith('─ Executing parsed code:') or line.startswith('Out - '):
                if 'Final answer:' in line:
                    formatted_steps.append("✅ **Generating final response...**")
                elif line.startswith('Out - '):
                    result = line.replace('Out - ', '').strip()
                    if result and not result.startswith('Final answer:'):
                        formatted_steps.append(f"📤 **Output:** {result}")
        
        # Check for final answer generation
        if "final_answer" in step_str and ("Final Answer:" in step_str or "final_answer(" in step_str):
            if not any("Generating final response" in step for step in formatted_steps):
                formatted_steps.append("✅ **Generating final response...**")
        
        return formatted_steps
            
    def get_steps(self) -> List[str]:
        return self.steps



async def generate_streaming_response(request_data: ComplexRequest):
    """
    Generator function for streaming steps and final response in real-time.
    """
    # Initialize step communication system
    step_queue = queue.Queue()
    agent_finished = threading.Event()
    
    # Create step tracker with queue communication
    class QueueStepTracker(StepTracker):
        def __init__(self, step_queue):
            super().__init__()
            self.step_queue = step_queue
            self.step_counter = 0
            
        def __call__(self, step):
            """This method is called by the agent at each step"""
            print(f"🔍 QueueStepTracker called with: {step}")
            formatted_steps = self.format_step_realtime(step)
            
            for formatted_step in formatted_steps:
                if formatted_step:
                    self.steps.append(formatted_step)
                    self.step_counter += 1
                    print(f"🔍 Step {self.step_counter} added: {formatted_step}")
                    
                    # Send to queue immediately
                    self.step_queue.put(formatted_step)
                    print(f"🔍 Step {self.step_counter} sent to queue")
    
    step_tracker = QueueStepTracker(step_queue)
    
    # Configure logging
    agent_logger = logging.getLogger("smolagents") 
    list_handler = ListLogHandler()
    agent_logger.addHandler(list_handler)
    agent_logger.setLevel(logging.DEBUG) 
    agent_logger.propagate = False

    try:
        # Extract query from different possible formats in the request
        query = None
        if request_data.toolsQuery:
            query = request_data.toolsQuery
        elif request_data.messages and len(request_data.messages) > 0:
            # Get the last user message if available
            for message in reversed(request_data.messages):
                if message.get("role") == "user":
                    query = message.get("content")
                    break
        elif request_data.chatSettings and "query" in request_data.chatSettings:
            query = request_data.chatSettings["query"]
            
        if not query:
            raise HTTPException(status_code=400, detail="No query found in request")
            
        logger.info(f"Processing query: {query}")

        # Check if this is a simple query that can bypass the agent workflow
        if is_simple_query(query):
            logger.info(f"Detected simple query, bypassing agent workflow")
            simple_response = get_simple_response(query)
            # Send simple response via streaming
            simple_data = {
                "steps": ["Simple query handled directly."],
                "response": simple_response,
                "canHandle": True
            }
            try:
                json_str = json.dumps(simple_data, ensure_ascii=False)
                yield f"data: {json_str}\n\n"
            except Exception as e:
                logger.error(f"Error encoding simple response to JSON: {e}")
                # Fallback
                fallback_data = {
                    "steps": ["Simple query handled."],
                    "response": "Hello!",
                    "canHandle": True
                }
                yield f"data: {json.dumps(fallback_data)}\n\n"
            return

        # Build conversation context from history
        history_context = build_history_context(
            request_data.messages if request_data and hasattr(request_data, "messages") else None,
            current_query=query,
        )

        # Enhance the query with history and memory to enable continuity
        memory_context = build_memory_context(getattr(request_data, "conversationId", None))
        enhanced_query = query

        # Intent classification to hint tool choice
        def classify_query_intent(user_query: str, selected_tools: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
            intent = "rag"
            reason = []
            detected_url = None
            q = (user_query or "").lower()
            m = re.search(r"https?://\S+", user_query or "")
            if m:
                detected_url = m.group(0)
                intent = "scrape"
                reason.append("url detected")
            scrape_keywords = [
                "scrape", "crawl", "website", "webpage", "from site", "from web", "google", "search the web",
            ]
            if any(k in q for k in scrape_keywords):
                intent = "scrape"
                reason.append("scrape keywords")
            rag_keywords = [
                "pdf", "document", "knowledge base", "kb", "vector", "embedding", "retrieve", "chunks", "supabase", "stored docs", "my documents"
            ]
            if any(k in q for k in rag_keywords) and not detected_url:
                intent = "rag"
                reason.append("rag keywords")
            if selected_tools:
                names = [t.get("name", "").lower() for t in selected_tools if isinstance(t, dict)]
                if any(n in names for n in ["webscraper", "web_search"]):
                    intent = "scrape"
                    reason.append("selectedTools web")
                if any(n in names for n in ["retrieve_knowledge", "retriever", "rag"]):
                    intent = "rag"
                    reason.append("selectedTools rag")
            return {"intent": intent, "url": detected_url, "reason": ", ".join(reason) or "heuristics"}

        intent_info = classify_query_intent(query, getattr(request_data, "selectedTools", None))
        intent_hint = (
            f"Detected intent: {intent_info['intent']}. "
            + (f"URL: {intent_info['url']}. " if intent_info.get("url") else "")
            + ("Preferred tools: retrieve_knowledge (RAG). " if intent_info['intent'] == "rag" else "Preferred tools: web_search then webscraper (web). ")
            + f"Reason: {intent_info['reason']}."
        )

        if history_context:
            intro = (
                "You are in a multi-turn conversation. Maintain continuity and carry implied parameters "
                "(topic, location, filters) from prior turns unless the user changes them."
            )
            enhanced_query = intro + "\n" + intent_hint + "\n"
            if memory_context:
                enhanced_query += "Known conversation memory:\n" + memory_context + "\n"
            enhanced_query += "Conversation so far:\n" + history_context + "\n\nCurrent user message:\n" + query
        else:
            enhanced_query = intent_hint + "\n\n" + query

        # Start timing the agent execution
        start_time = time.time()
        logger.debug("Initializing OpenAIServerModel")

        # Configure the language model (Ollama/OpenAI-compatible server)
        llm_model = OpenAIServerModel(
            model_id=os.getenv("OLLAMA_CHAT_MODEL", "qwen2.5:7b-instruct"),
            api_base=os.getenv("BASE_URL", "http://localhost:11434/v1"),
            api_key="ollama"  # Ollama doesn't need real API key
        )
        
            
        # Load prompt templates from YAML file
        current_dir = os.path.dirname(os.path.abspath(__file__))
        prompt_path = os.path.join(current_dir, "tools", "prompt.yaml")
        prompt_templates = {}
        try:
            if os.path.exists(prompt_path):
                with open(prompt_path, 'r') as stream:
                    prompt_templates = yaml.safe_load(stream) or {}
            else:
                logger.info(f"prompt.yaml not found at {prompt_path}, continuing without it")
        except Exception as e:
            logger.warning(f"Failed to load prompt.yaml: {e}")
        
        # Configure the agent with tools and settings
        logger.debug("Setting up tools for CodeAgent")
        agent = CodeAgent(
            tools=[
                webscraper,
                web_search,
                retrieve_knowledge,
            ],
            model=llm_model,
            add_base_tools=True,
            max_steps=8,
            verbosity_level=2,  # Ensures thoughts/actions are in the output string
            step_callbacks=[step_tracker], 
            additional_authorized_imports=[
                'requests', 
                'bs4',
                're',
                'queue',
                # 'random',
                'statistics',
                'unicodedata',
                'itertools',
                'math',
                'stat',
                'time',
                'datetime',
                'collections',
                'numpy',
                'duckduckgo_search',
                'selenium',
                'httpx'
            ],
        )
        
        # Run agent in thread and stream steps
        agent_result = None
        agent_error = None
        
        def run_agent():
            nonlocal agent_result, agent_error
            try:
                logger.info("Running agent with query")
                agent_result = agent.run(enhanced_query)
                agent_finished.set()  # Signal completion
                print("🔍 Agent execution completed")
            except Exception as e:
                agent_error = e
                agent_finished.set()  # Signal completion even on error
                print(f"🔍 Agent execution failed: {e}")
        
        # Start agent in background thread
        agent_thread = threading.Thread(target=run_agent)
        agent_thread.start()
        
        # Stream steps as they come from the queue
        while not agent_finished.is_set() or not step_queue.empty():
            try:
                # Try to get step from queue with timeout
                step = step_queue.get(timeout=0.1)
                
                # Send step immediately
                steps_data = {
                    "steps": [step],
                    "response": None
                }
                json_str = json.dumps(steps_data, ensure_ascii=False)
                yield f"data: {json_str}\n\n"
                print(f"🔍 Streamed step to frontend: {step[:50]}...")
                
            except queue.Empty:
                # No step available, continue waiting
                await asyncio.sleep(0.1)
                continue
        
        # Wait for agent to complete
        agent_thread.join()
        
        # Check for errors
        if agent_error:
            raise agent_error

        # Process final response
        # Convert output to string for processing
        if not isinstance(agent_result, str):
            agent_result_str = str(agent_result)
        else:
            agent_result_str = agent_result

        logger.debug(f"Raw agent output string from agent.run():\n{agent_result_str}")

        # Get steps directly from the step tracker
        parsed_display_steps = step_tracker.get_steps()
        
        # If no steps were captured, try traditional parsing
        if not parsed_display_steps:
            parsed_display_steps = parse_agent_steps(agent_result_str)
        
        # Extract the final answer - handle different return types
        final_answer_text = ""
        
        # Try to get action_output from the agent result if it's an object
        if hasattr(agent_result, 'action_output') and agent_result.action_output:
            final_answer_text = str(agent_result.action_output)
        else:
            # Fallback to string parsing
            final_answer_text = extract_final_answer(agent_result_str)

        # Process tool results
        tool_results = []
        for step in parsed_display_steps:
            if any(marker in step.lower() for marker in ["result:", "results:", "weather forecast"]):
                tool_results.append(step)
        
        # Determine the final response
        if not final_answer_text or final_answer_text == "Unable to extract final answer":
            if tool_results:
                final_answer_text = "\n\n".join(tool_results)
            elif parsed_display_steps:
                final_answer_text = parsed_display_steps[-1]
            else:
                final_answer_text = "I couldn't process this request with the available tools."

        # Update conversation memory with extracted facts
        try:
            update_conversation_memory(getattr(request_data, "conversationId", None), final_answer_text)
        except Exception:
            pass

        # Check if the agent can handle the query
        result_str_lower = final_answer_text.lower()
        cannot_handle_patterns = [
            "cannot complete this task with the available tools",
            "unable to fulfill this request",
            "don't have the tools",
            "cannot access",
            "cannot retrieve",
            "cannot get information about",
            "not possible to get",
            "i'm unable to",
            "beyond my capabilities",
            "i don't have access to",
            "i don't have the ability to",
            "i can't perform this action",
            "sorry, i cannot",
            "i am unable to"
        ]
        
        # Determine if the query was handled successfully
        is_unhandled = any(pattern in result_str_lower for pattern in cannot_handle_patterns)
        
        # Send final response
        final_data = {
            "steps": [],
            "response": final_answer_text,
            "canHandle": not is_unhandled
        }
        # Ensure JSON is properly encoded
        try:
            json_str = json.dumps(final_data, ensure_ascii=False)
            yield f"data: {json_str}\n\n"
        except Exception as e:
            logger.error(f"Error encoding final response to JSON: {e}")
            # Fallback with error response
            error_data = {
                "steps": [],
                "response": "Error encoding response",
                "canHandle": False,
                "error": str(e)
            }
            yield f"data: {json.dumps(error_data)}\n\n"

        # Log execution time
        end_time = time.time()
        logger.info(f"Agent processing time: {end_time - start_time:.2f} seconds")

    except HTTPException as http_exc:
        # Re-raise HTTP exceptions
        error_data = {
            "steps": [],
            "response": None,
            "error": str(http_exc.detail)
        }
        yield f"data: {json.dumps(error_data)}\n\n"
    except Exception as e:
        error_str = str(e)
        
        # Handle specific error cases
        if "401 Client Error: Unauthorized" in error_str:
            error_detail = "Authentication error with Hugging Face API. Please check your token validity."
        elif "403 Forbidden" in error_str and "does not have sufficient permissions" in error_str:
            error_detail = "Insufficient permissions to use the model. Please check your HF token permissions."
        else:
            error_detail = f"Error during agent execution: {error_str}"
        
        # Handle general errors
        logger.error(f"Error in run_agent: {error_str}", exc_info=True)
        error_data = {
            "steps": [],
            "response": None,
            "error": error_detail
        }
        yield f"data: {json.dumps(error_data)}\n\n"

@app.post("/")
async def run_agent_streaming(request_data: ComplexRequest):
    """
    Point d'entrée principal qui retourne un StreamingResponse.
    """
    return StreamingResponse(
        generate_streaming_response(request_data),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive"
        }
        )


def run_agent_sync(query: str) -> str:
    """
    Synchronous agent execution for /ask endpoint.
    The agent will decide which tools to use (RAG, summarize, scrape) based on the query.
    
    Args:
        query: User question/request
        
    Returns:
        str: Agent's final answer
    """
    print(f"[agent_sync] Starting with query: '{query[:100]}'")
    
    try:
        # Initialize the agent with all tools
        model = OpenAIServerModel(
            model_id=os.getenv("OLLAMA_CHAT_MODEL", "qwen2.5:7b-instruct"),
            api_base=os.getenv("BASE_URL", "http://localhost:11434/v1"),
            api_key="ollama"
        )
        
        # All available tools - agent will choose which to use
        tools = [web_search, webscraper, retrieve_knowledge]
        
        agent = CodeAgent(
            tools=tools,
            model=model,
            max_steps=10,
            verbosity_level=2
        )
        
        print(f"[agent_sync] Agent initialized with {len(tools)} tools")
        
        # Run the agent
        result = agent.run(query)
        
        # Extract final answer
        if hasattr(result, 'action_output') and result.action_output:
            final_answer = str(result.action_output)
        else:
            final_answer = str(result)
        
        print(f"[agent_sync] Agent completed. Answer length: {len(final_answer)}")
        return final_answer
        
    except Exception as e:
        print(f"[agent_sync] Error: {e}")
        import traceback
        traceback.print_exc()
        return f"Error running agent: {str(e)}"

