# 🏗️ Architecture RAG-Scrap-Agent

## 📋 Vue d'ensemble

Le système suit une architecture en 2 étapes:
1. **`/ask` endpoint** : Gère uniquement l'upload des fichiers
2. **smolagent** : Prend toutes les décisions intelligentes (RAG/summarize/scrape)

---

## 🔄 Flux de traitement

```
┌─────────────────────────────────────────────────────────────┐
│                    FRONTEND (Next.js)                        │
│  User clicks "Ask" with query + optional files              │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ↓
┌─────────────────────────────────────────────────────────────┐
│              Next.js API Route (/api/ask)                    │
│  Proxies request to backend                                 │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ↓
┌─────────────────────────────────────────────────────────────┐
│              FastAPI Backend (/ask)                          │
│                                                              │
│  STEP 1: File Upload Processing (if files present)          │
│  ┌────────────────────────────────────────────┐             │
│  │ For each file:                             │             │
│  │  1. store_pdf()    → Supabase Storage      │             │
│  │  2. parse_pdf()    → Extract text          │             │
│  │  3. index_documents() → Vector DB          │             │
│  └────────────────────────────────────────────┘             │
│                                                              │
│  STEP 2: Delegate to smolagent                              │
│  ┌────────────────────────────────────────────┐             │
│  │ Query + context → run_agent_sync()         │             │
│  └────────────────────────────────────────────┘             │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ↓
┌─────────────────────────────────────────────────────────────┐
│                     SMOLAGENT                                │
│  🧠 Intelligent decision making                             │
│                                                              │
│  Available Tools:                                            │
│  ┌────────────────────────────────────────────┐             │
│  │ 🔍 retrieve_knowledge (RAG)                │             │
│  │    - Searches vector DB for relevant docs  │             │
│  │    - Returns: chunks + metadata            │             │
│  │                                            │             │
│  │ 🌐 web_search (Scraping)                   │             │
│  │    - Searches the web via Tavily API       │             │
│  │    - Returns: URLs + snippets              │             │
│  │                                            │             │
│  │ 🕷️ webscraper (Deep scraping)              │             │
│  │    - Scrapes specific URLs                 │             │
│  │    - Returns: full page content            │             │
│  └────────────────────────────────────────────┘             │
│                                                              │
│  Agent reasoning:                                            │
│  1. Analyzes the query                                       │
│  2. Decides which tool(s) to use                            │
│  3. Executes tools in sequence                              │
│  4. Synthesizes final answer                                │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ↓
┌─────────────────────────────────────────────────────────────┐
│                  RESPONSE TO USER                            │
│  { "answer": "..." }                                        │
└─────────────────────────────────────────────────────────────┘
```

---

## 🎯 Exemples de décisions de smolagent

### Scenario 1: Question simple sans contexte
```
Query: "what s the fortilla sumud"
Files: None

Agent reasoning:
1. No files uploaded
2. Query doesn't contain URL or scraping keywords
3. Try retrieve_knowledge() first
4. If no results → Could try web_search()
```

### Scenario 2: Upload + Question
```
Query: "summarize this document"
Files: [recipe.pdf]

Agent reasoning:
1. File already uploaded and indexed by /ask
2. Context indicates file just uploaded
3. Use retrieve_knowledge() to get all chunks
4. Synthesize summary from chunks
```

### Scenario 3: Web scraping request
```
Query: "what's on https://example.com/news"
Files: None

Agent reasoning:
1. URL detected in query
2. Use webscraper() to fetch content
3. Summarize the scraped content
```

### Scenario 4: General web search
```
Query: "search the web for latest AI news"
Files: None

Agent reasoning:
1. Keywords "search the web" detected
2. Use web_search() via Tavily API
3. Return top results with sources
```

---

## 🛠️ Composants principaux

### 1. `/ask` Endpoint (main.py)
**Responsabilités:**
- ✅ Gérer les uploads multipart
- ✅ Parser et indexer les PDFs
- ✅ Transmettre la query à smolagent
- ❌ **PAS** de logique de décision (intent detection supprimée)

### 2. smolagent (agent.py)
**Responsabilités:**
- ✅ Analyser la query
- ✅ Choisir les outils appropriés
- ✅ Exécuter les outils
- ✅ Synthétiser la réponse finale

**Outils disponibles:**
1. `retrieve_knowledge(query)` - RAG search
2. `web_search(query)` - Web search via Tavily
3. `webscraper(url)` - Scrape specific URLs

### 3. Vector Store (vector_store.py)
**Responsabilités:**
- Chunking des documents
- Génération d'embeddings (Ollama)
- Stockage dans Supabase pgvector
- Recherche de similarité

---

## 🔧 Configuration requise

### Supabase
1. **Table `documents`** avec colonnes:
   - `id` (BIGSERIAL)
   - `content` (TEXT)
   - `metadata` (JSONB)
   - `embedding` (VECTOR(1024))

2. **Fonction `match_documents`**:
```sql
CREATE OR REPLACE FUNCTION match_documents(
  query_embedding VECTOR(1024),
  match_threshold FLOAT DEFAULT 0.5,
  match_count INT DEFAULT 5
)
RETURNS TABLE (...)
```

### Environment Variables
```bash
# Supabase
SUPABASE_URL=https://xxx.supabase.co
SUPABASE_KEY=xxx

# Ollama (local)
OLLAMA_EMBED_MODEL=mxbai-embed-large
OLLAMA_BASE_URL=http://localhost:11434

# Tavily (web search)
TAVILY_API_KEY=xxx
```

---

## 📊 Avantages de cette architecture

1. **Séparation des responsabilités**
   - `/ask` = Infrastructure (upload/index)
   - `smolagent` = Intelligence (décisions)

2. **Flexibilité**
   - smolagent peut combiner plusieurs outils
   - Facile d'ajouter de nouveaux outils

3. **Simplicité**
   - Pas de logique if/else complexe dans `/ask`
   - L'agent raisonne de façon autonome

4. **Traçabilité**
   - Logs à chaque étape
   - Facile de debugger

---

## 🚀 Prochaines étapes

1. ✅ Créer la fonction `match_documents` dans Supabase
2. ✅ Tester avec une question simple
3. ✅ Tester avec upload + question
4. ✅ Tester avec web scraping
5. ⬜ Ajouter un outil de summarization dédié
6. ⬜ Améliorer les prompts de l'agent

