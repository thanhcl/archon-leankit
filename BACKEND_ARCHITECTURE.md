# Archon LeanKit Backend Services Architecture

## 1. Backend Service Architecture Overview

The Archon backend is a **FastAPI-based microservices architecture** deployed via Docker Compose with three main services plus a frontend:

### Services Structure:
```
archon-server (8181)      — Main backend API (FastAPI + Socket.IO + Crawling)
archon-mcp (8051)         — MCP server for Claude integration (HTTP-based)
archon-agents (8052)      — Optional AI agents service (reranking/ML)
archon-frontend (3737)    — React UI
```

**Key Architecture Principle**: MCP server uses HTTP calls to the backend API rather than importing dependencies directly, reducing container size from 1.66GB to ~150MB.

### Entry Points:
- **Main Server**: `/Users/thanhcl/Development/TrueAI/archon-leankit/python/src/server/main.py`
  - FastAPI lifespan manager for startup/shutdown
  - CORS middleware configuration
  - Crawler initialization and health monitoring
  - Channel health monitor background service

- **MCP Server**: `/Users/thanhcl/Development/TrueAI/archon-leankit/python/src/mcp_server/mcp_server.py`
  - Lightweight HTTP-only MCP implementation
  - Service client for proxying calls to main backend
  - No heavy dependencies (embeddings, crawling, etc.)

---

## 2. API Routes (37 route modules)

Located in: `/python/src/server/api_routes/`

### Core Knowledge Management Routes:
- **knowledge_api.py** (55.7 KB) — RAG, crawling, document upload, indexing
  - POST `/api/crawl` — Start web crawl with progress tracking
  - GET `/api/crawl-progress/{progress_id}` — HTTP polling for progress
  - POST `/api/upload-documents` — Document upload and processing
  - POST `/api/rag-query` — RAG search with query embedding
  - GET `/api/knowledge-items` — List indexed sources

- **learnings_api.py** (5.4 KB) — Learning lifecycle management
  - GET `/api/learnings` — List learnings with filtering
  - GET `/api/learnings/flagged` — Learnings pending TeamLead review
  - POST `/api/{id}/approve-probation` — Promote to probation tier (3-tier model)
  - POST `/api/{id}/approve-guidance` — Promote to guidance pack
  - POST `/api/{id}/reject` — Demote or reject learning

### Engine/Task Execution Routes:
- **engine_api.py** (43.4 KB) — Task execution, streaming, metrics
- **projects_api.py** (63.9 KB) — Project and task management
- **execution_runs_api.py** — Execution tracking
- **task_generator_service.py** — Dynamic task generation

### Provider/Settings Routes:
- **settings_api.py** — Credentials and RAG configuration management
- **providers_api.py** — LLM provider discovery and management
- **ollama_api.py** (56.0 KB) — Local Ollama model support
- **openrouter_api.py** — OpenRouter provider integration

### Integration Routes:
- **mcp_api.py** — MCP server management
- **telegram_api.py** — Telegram notifications
- **openclaw_api.py** — Mobile approval integration
- **agent_work_orders_proxy.py** — Agent workflow execution

---

## 3. Services Layer Architecture

Located in: `/python/src/server/services/`

### A. Crawling & Document Processing (`/crawling/`)
**Files**: `crawling_service.py`, `code_extraction_service.py`, `discovery_service.py`

**Process Flow**:
1. **URL Discovery** (`discovery_service.py`)
   - Detects sitemap.xml, robots.txt, llms.txt
   - Supports recursive URL discovery
   - Root domain extraction via tldextract

2. **Crawling Strategies** (`/crawling/strategies/`)
   - `single_page.py` — Single page crawl with Crawl4AI
   - `batch.py` — Parallel batch crawling
   - `recursive.py` — Depth-limited recursive crawling
   - `sitemap.py` — Sitemap-based crawling

3. **Content Processing** (`page_storage_operations.py`)
   - HTML → Markdown conversion (using BeautifulSoup4)
   - Metadata extraction (title, description, author)
   - Code block extraction with language detection
   - Document chunking with overlap handling

4. **Progress Tracking**
   - In-memory progress tracker with HTTP polling support
   - Graceful cancellation for long-running operations
   - Semaphore limiting (max 3 concurrent crawl operations)

### B. Embeddings Service (`/embeddings/`)
**Files**: `embedding_service.py`, `contextual_embedding_service.py`, `provider_error_adapters.py`

**Features**:
- **Multi-Provider Support**:
  - OpenAI (default) — `text-embedding-3-small` (1536 dims)
  - Google Generative AI — Custom endpoint
  - Anthropic
  - Ollama (local)
  - OpenRouter proxy

- **Dimension Support** (via Supabase pgvector):
  - 384, 768, 1024, 1536 (default), 3072 dimensions
  - Multi-embedding storage for flexible search

- **Batch Processing**:
  - `create_embeddings_batch()` — Parallel embedding with rate limiting
  - Threading service integration for concurrent operations
  - Error recovery with adaptive batch sizing

- **Contextual Embeddings** (optional):
  - `generate_contextual_embeddings_batch()` — Embed with surrounding context
  - Configurable context window size
  - Improved semantic search accuracy

**Configuration**:
```python
# From config.py
RAGStrategyConfig:
  use_contextual_embeddings: bool = False
  use_hybrid_search: bool = True
  use_agentic_rag: bool = True
  use_reranking: bool = True
```

### C. Storage Service (`/storage/`)
**Files**: `document_storage_service.py`, `code_storage_service.py`, `base_storage_service.py`

**Document Storage Pipeline**:
1. Accept documents with URLs, chunks, content, metadata
2. Delete existing records for same URLs (batched)
3. Generate embeddings (parallel batches)
4. Insert into Supabase with metadata JSONB
5. Progress callback for UI updates

**Key Features**:
- Parallel batch processing with configurable batch size (default: 50)
- Rate limiting to prevent DB saturation
- Cancellation token support
- Metadata enrichment (source_id, knowledge_type, tags, author)

**Code Storage**:
- Extract code examples from crawled pages
- Store in `archon_code_examples` table
- Generate code summaries via LLM
- Category-based indexing (functions, classes, algorithms, etc.)

### D. Search/RAG Service (`/search/`)
**Files**: `rag_service.py`, `base_search_strategy.py`, `hybrid_search_strategy.py`, `agentic_rag_strategy.py`, `reranking_strategy.py`

**Layered RAG Architecture**:

1. **Base Vector Search** (`base_search_strategy.py`)
   - SQL RPC call to `match_archon_crawled_pages(query_embedding, match_count, filter, source_filter)`
   - pgvector-based semantic similarity
   - Similarity threshold filtering (0.05 by default)
   - Returns: `id, url, chunk_number, content, metadata, source_id, similarity`

2. **Hybrid Search** (optional, configurable)
   - Combines vector + keyword/full-text search (tsvector in PostgreSQL)
   - Reciprocal rank fusion (RRF) to merge results
   - Better for long-tail and domain-specific terms

3. **Agentic RAG** (optional)
   - Enhanced code example search
   - Cross-encoder style ranking
   - Integrates with base strategy

4. **Reranking** (optional)
   - CrossEncoder model for result reranking
   - Requires `archon-agents` service
   - Improves top-k result quality

5. **Keyword Extraction** (`keyword_extractor.py`)
   - TF-IDF based keyword extraction for hybrid search
   - Supports custom stop words and thresholds

### E. Knowledge Management (`/knowledge/`)
**Files**: `knowledge_item_service.py`, `knowledge_summary_service.py`, `database_metrics_service.py`

- Track source metadata, summaries, update frequencies
- Database metrics (document count, page count, last updated)
- Knowledge item versioning

### F. Source Management (`source_management_service.py`)

**Functions**:
- `extract_source_summary()` — LLM-based source summarization (max 500 chars)
- `generate_source_title_and_metadata()` — Auto-generate user-friendly titles
- Source type detection (llms.txt, sitemap, documentation, website)
- Source validation and enrichment

### G. Learning Processor (`/engine/learning_processor.py`)

**3-Tier Learning Lifecycle (ML-1)**:

```
Candidate (auto-created)
    ↓ [recurrence >= 3] flagged_for_review=True
    ↓ [TeamLead approval]
Probation (30-day window, "[project pattern]" prefix)
    ↓ [probation expires OR teamlead rejects]
    ↓ [recurrence_count increases]
Promoted (guidance pack)
```

**Key Concepts**:
- **Confidence Decay**: Observed/inferred learnings lose 1 confidence point per 30 days
- **Recurrence Tracking**: Links tasks and execution runs
- **Pattern Key**: `type:area:description_prefix` for deduplication
- **Keyword Overlap**: 0.6+ threshold for similarity detection

**Tables**:
- `archon_learnings` — Main learnings table
- `archon_code_patterns` — Code pattern extraction (separate lifecycle)
- `archon_promotion_log` — Audit trail of tier transitions

---

## 4. Supabase Integration

### Connection & Client Management
**File**: `services/client_manager.py`

```python
def get_supabase_client() -> Client:
    # Initialize from env: SUPABASE_URL, SUPABASE_SERVICE_KEY
    # Uses service_role key (NOT anon key) for write permissions
    # Connection pooling handled by Supabase SDK
```

**Critical Config**:
- **SUPABASE_SERVICE_KEY** must be the service_role key (full access)
- Using anon key → all writes fail with "permission denied"
- Validation in `config/config.py` with clear error messages

### Vector Database Setup
**File**: `migration/complete_setup.sql`

**Tables**:
- `archon_crawled_pages` — Main document storage
  - Columns: id, url, chunk_number, content, metadata (JSONB), source_id, embedding_1536 (pgvector)
  - Supports: 384, 768, 1024, 1536, 3072-dim embeddings
  - Full-text search: content_tsvector (tsvector for hybrid search)

- `archon_code_examples` — Code snippet storage
  - Columns: language, category, pattern_name, code, summary
  - Similar vector support

- `archon_learnings` — Learning extraction
  - Columns: type, area, description, confidence, source, tier, recurrence_count, related_tasks, source_run_ids, flagged_for_review, promotion_log

- `archon_promotion_log` — Audit trail

**RPC Functions** (SQL):
```sql
match_archon_crawled_pages(
  query_embedding: vector,
  match_count: int,
  filter: jsonb,
  source_filter: text
) → RETURNS (id, url, chunk_number, content, metadata, source_id, similarity)
```

Multi-dimensional support via `match_archon_crawled_pages_multi()` with dimension parameter.

---

## 5. Configuration Management

**File**: `/python/src/server/config/config.py`

### Required Environment Variables:
```bash
SUPABASE_URL=https://xxx.supabase.co
SUPABASE_SERVICE_KEY=eyJ...  # service_role key (NOT anon)
ARCHON_SERVER_PORT=8181
ARCHON_MCP_PORT=8051
OPENAI_API_KEY=sk-...  # Optional, can be set via Settings API
```

### Optional Environment Variables:
```bash
USE_CONTEXTUAL_EMBEDDINGS=true/false
USE_HYBRID_SEARCH=true/false
USE_AGENTIC_RAG=true/false
USE_RERANKING=true/false
LOG_LEVEL=INFO
LOGFIRE_TOKEN=...  # Observability
```

### Settings Management:
- Credentials stored encrypted in Supabase `archon_credentials` table
- Category-based storage: "rag_strategy", "llm_providers", etc.
- Can be updated via Settings API without restart

---

## 6. Document Processing Pipeline (Complete Flow)

```
1. CRAWLING PHASE
   └─ User clicks "Crawl URL"
   └─ discovery_service detects sitemaps, robots.txt
   └─ Crawling strategy selected (single, batch, recursive, sitemap)
   └─ Crawl4AI fetches pages asynchronously
   └─ HTML → Markdown conversion
   └─ Code extraction if enabled
   └─ Progress tracked & reported via HTTP polling

2. CHUNKING PHASE
   └─ Content split into overlapping chunks (default strategy)
   └─ Chunk size configurable
   └─ Metadata enriched (source_id, url, chunk_number)

3. EMBEDDING PHASE
   └─ Batch embeddings generated via provider
   └─ Default: OpenAI text-embedding-3-small (1536 dims)
   └─ Contextual embeddings optional (slower, more accurate)
   └─ Parallel batch processing with rate limiting

4. STORAGE PHASE
   └─ Delete old records for same URL
   └─ Insert new documents + embeddings into archon_crawled_pages
   └─ Store code examples separately in archon_code_examples
   └─ Metadata JSONB stored (tags, knowledge_type, author, etc.)

5. INDEXING PHASE
   └─ tsvector auto-updated for full-text search
   └─ pgvector indexed for semantic search
   └─ Hybrid search ready (if enabled)

6. RETRIEVAL PHASE (RAG)
   └─ User query → embedding
   └─ Base vector search: pgvector similarity
   └─ Optional: Hybrid search + keyword expansion
   └─ Optional: Agentic RAG for code examples
   └─ Optional: Reranking for top-k results
   └─ Results returned with similarity scores
```

---

## 7. Learning Extraction & Auto-Promotion Pipeline

**File**: `/python/src/server/services/engine/learning_processor.py`

```
TASK EXECUTION COMPLETES
└─ learnings list extracted by CC task
└─ code_patterns extracted from generated code

LEARNING PROCESSING
└─ Validate learning (type, area, description)
└─ Search for similar existing learnings (keyword overlap >= 0.6)
└─ If similar:
   └─ Increment recurrence_count
   └─ Add task_id to related_tasks
   └─ Add execution_run_id to source_run_ids
   └─ IF recurrence_count >= 3:
      └─ Flag for TeamLead review (don't auto-promote)
      └─ Set flagged_for_review=True
└─ If new:
   └─ Create with tier="candidate", recurrence_count=1
   └─ Optional: Notify TeamLead

CODE PATTERN PROCESSING (separate)
└─ Dedup by category.language.name
└─ Track usage_count and confidence
└─ Auto-promote if confidence >= 0.9 AND usage_count >= 3

HUMAN REVIEW (3-Tier Model)
└─ TeamLead reviews flagged learnings
└─ POST /api/learnings/{id}/approve-probation
   └─ tier="probation", probation_started_at=now
   └─ Injected with higher priority
   └─ Expires in 30 days if not promoted
└─ POST /api/learnings/{id}/approve-guidance
   └─ tier="promoted", promoted_to="guidance_pack"
   └─ Permanent integration
└─ POST /api/learnings/{id}/reject
   └─ demote_to_candidate(reason="rejected")
```

---

## 8. Scheduled/Automated Processes

**File**: `/python/src/server/services/channels/notification_scheduler_service.py`

### Learnings Expiry:
- **Endpoint**: POST `/api/learnings/expire-probations`
- **Trigger**: Can be called by external scheduler (cron)
- **Action**: Demote probation learnings > 30 days old back to candidate

### Channel Health Monitoring:
- **File**: `services/channels/channel_health_monitor.py`
- **Background**: Runs on server startup
- **Monitors**: Telegram, OpenClaw channels
- **Recovers**: Failed notification channels

### Notification Scheduling:
- **File**: `api_routes/telegram_api.py`
- **Feature**: Schedule-aware Telegram digests
- **Endpoints**:
  - `POST /api/telegram/digest` — Send schedule-aware digest
  - `POST /api/telegram/replay-digest` — Collect replay events and digest

---

## 9. Threading & Concurrency

**File**: `services/threading_service.py`

**Purpose**: Optimize concurrent operations without blocking main async loop

**Features**:
- Thread pool for CPU-bound work (embeddings, encoding)
- Async-safe task submission
- Rate limiting per provider
- Configurable worker counts

**Usage**:
```python
threading_service = get_threading_service()
result = await threading_service.run_in_executor(
    processor_func,
    document_batch,
    timeout=300
)
```

---

## 10. Key Tables & Database Schema

### Core Tables (from migrations):

| Table | Purpose | Key Columns |
|-------|---------|------------|
| `archon_crawled_pages` | Document storage | id, url, chunk_number, content, source_id, embedding_1536, metadata (JSONB) |
| `archon_code_examples` | Code snippets | language, category, pattern_name, code, summary, embedding_1536 |
| `archon_learnings` | Learning extraction | type, area, description, confidence, source, tier, recurrence_count, related_tasks, source_run_ids, flagged_for_review, promotion_log |
| `archon_code_patterns` | Code pattern tracking | pattern_key, confidence, usage_count, language, category |
| `archon_promotion_log` | Audit trail | learning_id, from_tier, to_tier, actor, reason, timestamp |
| `archon_sources` | Source metadata | source_id, url, title, summary, knowledge_type, tags |
| `archon_crawled_page_metadata` | Metadata index | page_id, key, value |
| `archon_credentials` | Encrypted settings | key, value (encrypted), category |

---

## 11. Important Files & Paths

### Configuration:
- `/python/src/server/config/config.py` — Config loading & validation
- `/python/src/server/config/env_aliases.py` — Platform-specific env aliases
- `/deploy/{local,staging,production}/.env.example` — Template configs
- `docker-compose.yml` — Service orchestration

### API Routes (37 modules):
- `/python/src/server/api_routes/knowledge_api.py` — RAG & crawling
- `/python/src/server/api_routes/learnings_api.py` — Learning management
- `/python/src/server/api_routes/engine_api.py` — Task execution
- `/python/src/server/api_routes/projects_api.py` — Project management

### Core Services:
- `/python/src/server/services/crawling/` — Web crawling
- `/python/src/server/services/embeddings/` — Vector embeddings
- `/python/src/server/services/storage/` — Document persistence
- `/python/src/server/services/search/` — RAG & vector search
- `/python/src/server/services/engine/` — Task execution engine & learning processor

### Database:
- `/migration/complete_setup.sql` — Full schema setup
- `/migration/0.1.0-leankit/` — Version migrations
- `/migration/RESET_DB.sql` — Full reset script

---

## 12. Security & Best Practices

1. **Supabase Service Key** — Must be service_role, NOT anon key
2. **API Key Validation** — All crawling operations validate embedding provider keys first
3. **Concurrent Crawl Limiting** — Max 3 simultaneous crawl operations (server protection)
4. **Sensitive Data in Logs** — Provider errors sanitized before logging
5. **Docker Socket Removed** — Uses HTTP health checks instead (CVE-2025-9074)
6. **Environment Validation** — Startup checks for required variables

---

## Summary

The Archon backend is a **production-grade, scalable RAG system** built on:
- **FastAPI** for async request handling
- **Supabase/PostgreSQL** with pgvector for semantic search
- **Crawl4AI** for web content extraction
- **Modular architecture** separating concerns (crawling, embedding, storage, search)
- **3-tier learning lifecycle** with human-in-the-loop promotion
- **Flexible RAG pipeline** supporting multiple strategies (vector, hybrid, agentic, reranking)
- **HTTP-based MCP** for lightweight Claude integration

The system handles the complete knowledge management cycle: discovery → crawling → processing → embedding → storage → retrieval, plus learning extraction for continuous improvement.
