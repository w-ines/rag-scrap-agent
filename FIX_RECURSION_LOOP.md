# Fix: Recursion Loop Issue

## Problem
The agent was hitting the recursion limit (25 iterations) due to:
1. DuckDuckGo returning irrelevant Chinese search results (Baidu links)
2. Verifier continuously returning "revise" verdict
3. No safeguards to break the loop

## Root Causes
1. **Bad Search Results**: DuckDuckGo with `region='wt-wt'` was returning Chinese results for English queries
2. **No Failure Tracking**: Agent didn't track consecutive failed attempts
3. **No Break Condition**: Loop continued indefinitely until hitting hard recursion limit

## Solutions Applied

### 1. Improved DuckDuckGo Search (`tools/search/endpoints.py`)
```python
# Changed from:
region='wt-wt'  # Worldwide, English

# To:
region='us-en',  # US English for better results
safesearch='moderate',
backend='api'  # Use API backend for more reliable results
```

### 2. Added Failure Tracking (`unified_agent.py`)
Added to `UnifiedAgentState`:
```python
consecutive_failures: int  # Track failed attempts
last_context_length: int   # Track if we're getting new info
```

### 3. Enhanced Observation Logic
`node_observe()` now:
- Increments `consecutive_failures` when no/insufficient context found
- Resets counter to 0 when good results are obtained
- Tracks context length to detect progress

### 4. Safety Break Condition
`should_refine()` now:
```python
if consecutive_failures >= 3:
    logger.warning(f"Forcing finalization after {consecutive_failures} consecutive failures")
    return "finalize"
```

### 5. Increased Recursion Limit
Changed from default 25 to 50 as additional safety:
```python
agent.invoke(initial_state, config={"recursion_limit": 50})
```

## Expected Behavior
- Agent will try maximum 3 times if getting bad results
- After 3 consecutive failures, forces finalization
- Better English search results from DuckDuckGo
- Prevents infinite loops while still allowing legitimate retries
