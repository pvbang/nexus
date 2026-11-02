<p align="center">
  <picture>
    <img alt="Nexus" src="frontend/atsets/images/nexus-full-logo-and-text.png" width="480">
  </picture>
</p>

<h1 align="center">Nexus</h1>

<p align="center">
  <em>Multi-Agent Orchestration Platform with Layered Memory, Structured Debate &amp; Continual Learning</em>
</p>

<p align="center">
  <a href="#features"><strong>Features</strong></a> ·
  <a href="#architecture"><strong>Architecture</strong></a> ·
  <a href="#quick-start"><strong>Quick Start</strong></a> ·
  <a href="#configuration"><strong>Configuration</strong></a> ·
  <a href="#tech-stack"><strong>Tech Stack</strong></a> ·
  <a href="#api"><strong>API</strong></a> ·
  <a href="#license"><strong>License</strong></a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.12%2B-blue?logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/react-19-61DAFB?logo=react&logoColor=white" alt="React 19">
  <img src="https://img.shields.io/badge/FastAPI-0.115-009688?logo=fastapi&logoColor=white" alt="FastAPI">
  <img src="https://img.shields.io/badge/PostgreSQL-16%2B-4169E1?logo=postgresql&logoColor=white" alt="PostgreSQL">
  <img src="https://img.shields.io/badge/license-MIT-green" alt="License">
</p>

<p align="center">
  An autonomous, self-improving multi-agent orchestration engine that combines structured deliberation, hierarchical memory, continual skill acquisition, and 192 MCP tools into a single unified platform.
</p>

---

## Table of Contents

- [Features](#features)
  - [Core AI & Orchestration](#core-ai--orchestration)
  - [LLM Providers](#llm-providers)
  - [MCP Tools](#mcp-tools)
  - [Security & Intelligence](#security--intelligence)
  - [Frontend & UX](#frontend--ux)
  - [Platform & Infrastructure](#platform--infrastructure)
- [Architecture](#architecture)
- [Quick Start](#quick-start)
  - [Prerequisites](#prerequisites)
  - [Docker Compose (Recommended)](#docker-compose-recommended)
  - [Development Mode](#development-mode)
  - [Docker Services](#docker-services)
- [Configuration](#configuration)
  - [LLM Providers](#llm-providers-1)
  - [Execution Control](#execution-control)
  - [Memory Layers](#memory-layers)
  - [Circuit Breaker & Resilience](#circuit-breaker--resilience)
  - [Auto-Scaling](#auto-scaling)
- [Database Schema](#database-schema)
- [Tech Stack](#tech-stack)
- [Operations & Maintenance](#operations--maintenance)
- [Kilo Gateway for Claude Code](#kilo-gateway-for-claude-code)
- [API Reference](#api-reference)
- [License](#license)

---

## Features

### Core AI & Orchestration

| Feature | Description |
|---------|-------------|
| **Turn-Based Pipeline** | `NexusExecutionPipeline`: Planning → Turn Loop (Think → Discuss → Execute) → Final Report |
| **Structured Discussion Protocol** | Multi-agent debate with Challenge → Rebuttal → Synthesis cycles (agreed approaches + unresolved disagreements + key insights) |
| **Evolution Agent** | Per-turn quality analysis with continuous improvement recommendations |
| **XSkill Continual Learning** | Skills + Experiences + Critique + Trajectory compression + tree search for persistent capability growth |
| **Memory Layers (L0→L3)** | Capture → Atom Extraction → Scene Aggregation → Persona Synthesis with hierarchical recall |
| **Smart Context Aggregation** | Parallel fetch from 7 sources (memory, knowledge graph, RAG, XSkill, history) with relevance scoring, deduplication, and token budget control |
| **Circuit Breaker** | Provider resilience with CLOSED→OPEN→HALF_OPEN state machine across all LLM call paths |
| **Provider Health Metrics** | Per-provider success rate, latency tracking, and failure monitoring |
| **Agent Worker** | Unlimited tool rounds with activity-based timeout and smart output capping (20KB for LLM, full in ACTIVITY.md) |
| **Context Sharing** | Shared observation board — agents see peer thinking (full) + results (5KB cap) + discussion synthesis |
| **Self-Reflection Loop** | Quality checkpoints every N rounds with domain-specific thresholds and stagnation detection |
| **Goal Engine** | Autonomous sub-goal decomposition with result verification and discovery tracking |
| **Per-Model Timeout** | Fast models (GPT-4o-mini: 60s) vs deep-thinking (Opus: 900s) — fail-fast on known-fast providers |
| **Smart Model Routing** | Automatically routes simple tasks to cheap models, complex tasks to capable ones |
| **LLM Retry & Failover** | Error classification with emergency context trimming (preserves first user message), provider fallback chain |
| **Task Checkpoint & Resume** | Crash recovery via PostgreSQL persistence with automatic resumption on restart |
| **Long Message Processing** | Chunked processing up to 500K characters with map_reduce / sequential strategies |
| **Background Observability** | Metrics tracking for fire-and-forget tasks (XSkill, KG indexing, ontology) exposed via `/system/background-metrics` |
| **Cognitive Upgrade** | Self-awareness layer (InfrastructureMap), metacognitive loop, and creative divergence (DivergentThinker) |
| **Sub-Task Decomposition Pipeline** | 3-tier decomposition with dependency tracking and verification gates |
| **Prompt Engineering Suite** | Modular PromptBuilder (stable cache-key sections), prompt cache (~75% cost reduction), reasoning-effort control, compression chain |
| **Token Governor & Context Budget** | Budget-aware context allocation with epoch-based ledger and smart condensation (Tree-sitter AST compression) |
| **Cancellable Execution** | Cooperative cancellation and timeout handling across long-running pipelines |

### LLM Providers

The system uses **LiteLLM + AsyncOpenAI SDK**, supporting **any OpenAI-compatible endpoint**. Set `CORE_LLM_BASE_URL` and `CORE_LLM_API_KEY` to connect any provider.

| Provider | Notes | Timeout |
|----------|-------|---------|
| **AWS Bedrock** | Claude, Llama, Titan (via LiteLLM) | 900s |
| **OpenAI** | GPT-4o, GPT-4o-mini, GPT-4.1 | 60–120s |
| **Google Gemini** | gemini-2.0-flash, gemini-2.5-flash | 90s |
| **OpenRouter** | 100+ models including free options | Varies |
| **Kilo Gateway** | Free anonymous tier (200 req/hour/IP) | 900s |
| **Pollinations** | Free OpenAI-compatible endpoint | 900s |
| **g4f (GPT4Free)** | Real streaming, anonymous fallback | 900s |
| **Local (Ollama/vLLM)** | Self-hosted (Qwen, Llama, Mistral) | 900s |
| **Any OpenAI-Compatible** | Custom `base_url` + `api_key` | 900s |

Any provider exposing a `/v1/chat/completions` endpoint (Together AI, Groq, Mistral, DeepSeek, Fireworks, Cerebras, xAI, etc.) works out of the box.

**Fallback chain:** Core → Agent → Kilo → Pollinations → g4f — the system never stalls due to exhausted credits. Includes credential pool (API-key rotation, round-robin) per provider.

### MCP Tools

**68 modules · 192 registered tools · unlimited runtime-generated**

Tool modules self-register into a dynamic registry at import time (63 auto-loaded modules). Additional modules (cloud_exec, deployment, device_bridge, docker_exec, interactive) load lazily via `load_tool_module`. Agents can scaffold entirely new tool modules at runtime via `self_evolve`.

| Category | Modules |
|----------|---------|
| **File & Code** | filesystem (11), file_edit, code_navigation (5), git, search_patch (3) |
| **Search** | search_engines (15 engine adapters), search_orchestrator, search_scoring, web (6), rag (3) |
| **Terminal & Dev** | terminal (3), cicd_tools (3), test_tools (3), dev_server_tools (4), docker_tools (8), deployment (2), project_tools (3) |
| **Web & Browser** | browser (5), browser_ai (3), http_request, tunnel (3), public_link_setup (2) |
| **Data & Output** | data (10), database, chart, document_converter, archive (2), utility (4) |
| **Communication** | email, email_inbox, email_diagnostics, telegram, communication, external_channels_tools (4), notify |
| **AI & Intelligence** | delegate, self_evolve (5), skill_loader, tool_module_loader, workflow_tools (4), activity_search, session_search (2), interactive (ask_user) |
| **Security & Monitoring** | security_scan_tools (3), monitoring_tools (6), api_testing_tools (3), intelligence_tools (6), malware_tools (2), blockchain_tools (3), network (5), audit_log |
| **Visual & Media** | vision (3), visual_tools (2), image_gen, voice (2), video (3) |
| **GitHub & Bounty** | github_tools (6: fork, clone, create PR, search issues, browse bounties, GitHub API), bounty_tools (6: search, verify, monitor PR, respond review, earnings ledger, submission prep) |
| **Documentation** | documentation_tools (3: API docs, README, changelog) |
| **Utilities** | todo (4), translate, memory_tool (3), schedule (2), task_docs (6) |
| **Cloud & Device (lazy)** | cloud_exec (2), device_bridge (4: ADB/Frida), docker_exec |

The 15 search engine adapters include Google, DuckDuckGo, Brave, Tavily, Serper, Arxiv, PubMed, Semantic Scholar, Google Scholar, GitHub, NVD, ExploitDB, Wikidata, and more — orchestrated with freshness/credibility scoring.

### Security & Intelligence

| Feature | Description |
|---------|-------------|
| **13-Layer Defense-in-Depth** | CommandGuard, SSRF Guard (DNS pinning, private-network blocking), Tool Policy, Tool Guard, Exfiltration Guard, Secret Redaction (20+ patterns), Rate Limiter, Session Mutex, Approval Workflow, Audit Logging, Atomic Writes, Circuit Breaker, Resource Monitor |
| **Human-in-the-Loop Approval** | Dangerous operations queue for explicit user approval via `/doctor-plugins/approvals` |
| **Penetration Testing Framework** | Phased pentest pipeline with ATT&CK integration and ROE (Rules of Engagement) generator |
| **Intelligence Toolkit** | CVE lookup, IOC validation, threat intel enrichment, OSINT domain profiling, network topology mapping, evidence acquisition |
| **Malware & Forensics** | Sandbox-based malware analysis, mobile forensics extraction |
| **Blockchain Analysis** | Transaction tracing, wallet clustering, transaction graph construction |
| **API Testing** | Endpoint testing, request sequences, mock servers |
| **Security Scanning** | Dependency audit, secret detection, HTTP header analysis |
| **Secret Hygiene** | Redaction at every durable write point (logs, DB, files) |
| **Production Hardening** | Fail-fast on default secrets in prod, CORS restrictions, terminal API key enforcement, JWT auth |

### Frontend & UX

| Feature | Description |
|---------|-------------|
| **React 19 + TypeScript 5.7** | Build on Vite 6 with Zustand 5, React Router v7, and Tailwind CSS |
| **20 Screens + Dynamic Routes** | Chat, Dashboard, Agents, Tasks, Graph, XSkill, Artifacts, Skills, Process, Analytics, Terminal, Admin (Cron/Plugins/Batch), Settings, Login + parameterized and plugin-provided routes |
| **Process Transparency** | Inline event cards showing Deep Analysis, Self-Reflection, Discussion, and Tool Calls |
| **Structured Discussion Cards** | Challenges → Rebuttals → Synthesis displayed in real-time during agent debates |
| **Project Builder** | Integrated workflow: Blueprint → Scaffold → Implement → Test → Visual QA (with dev-server management) |
| **Artifact Viewers** | Native support for PDF, DOCX, XLSX, Audio, Video, Image, Code, HTML, Markdown (9 formats) |
| **Terminal Panel** | ANSI-colored command output rendering |
| **Multi-Task Dashboard** | Monitor parallel tasks simultaneously with subtask trees and system health |
| **Interactive Decisions** | Agents request user input mid-execution (decision queue + `/decisions` API) |
| **Analytics Dashboard** | Token usage charts, cost tracking, model breakdown, hourly usage patterns, provider comparison |
| **i18n + Theme** | Vietnamese/English, complete dark/light/system theme support |
| **Plugin System** | Frontend plugin registry with typed slots, error boundaries, and dynamic routes |
| **Real-Time** | SSE token streaming + WebSocket events with resilient reconnection |

### Platform & Infrastructure

| Feature | Description |
|---------|-------------|
| **Multi-Channel (9)** | Telegram, Discord, Slack, WhatsApp, Email, SMS, Signal, Matrix, Webhook (each enabled via its own environment configuration) |
| **Public Gateway** | Separate minimal API surface (port 18800, 300s proxy timeout) with tokenized public inbox |
| **OpenClaw Integration** | Execution layer providing sandboxing, tools, and channel routing |
| **Plugin Architecture** | Manifest-driven extensible plugin system (backend + frontend) with doctor health checks |
| **Docker Sandbox** | Isolated code execution with configurable network/CPU/memory limits |
| **Auto-Scaling** | CPU-aware worker scaling, rate limits, and queue sizing |
| **Redis Cache** | Response caching per category with pub/sub messaging |
| **RAG Pipeline** | Hybrid retrieval combining vector (pgvector), full-text search, and graph with RRF fusion, reranking, and auto-ontology generation |
| **GraphRAG + pgvector** | Knowledge graph with vector embeddings and automatic ontology generation on document index |
| **Workflow Persistence** | Multi-step DAG workflows survive restarts (Kahn topological execution, retries, conditions) |
| **Cron Scheduler** | APScheduler recurring task execution + skill-runner command pipeline |
| **OpenAI-Compatible API** | Drop-in `/v1/chat/completions` and `/v1/models` replacement endpoint |
| **Hermes Agent Bridge** | In-process agent chat, code edit, terminal, and session search tools exposed over MCP |
| **Batch Runner** | Fan out one prompt across many models/sessions with status tracking |
| **Session State API** | Durable session state snapshots and event replay |
| **Observability** | structlog JSON logging, action logger (JSONL timeline), crash-safe session audit logs, metrics exporter, task watchdog (heartbeat), resource monitoring |
| **Document Processing** | MarkItDown-based conversion of 50+ formats with OCR |

---

## Architecture

The platform follows a layered architecture: presentation → API → orchestration → data, with cross-cutting subsystems for memory, tools, and LLM routing.

### System Overview

```mermaid
flowchart TB
    subgraph PRES["🖥️ Presentation Layer"]
        direction LR
        WEB["Web UI<br/>React 19 · TypeScript 5.7<br/>Vite 6 · Zustand 5 · Tailwind CSS"]
        CH["Channels<br/>Telegram · Discord · Slack · WhatsApp<br/>Email · SMS · Signal · Matrix · Webhook"]
    end

    subgraph API["🔌 API Layer"]
        direction LR
        REST["REST API<br/>25 endpoint modules"]
        WS["WebSocket + SSE<br/>Real-time streaming"]
        OA["OpenAI Compatible<br/>/v1/chat/completions"]
    end

    subgraph CORE["⚡ Core Engine (Python 3.12+ / FastAPI)"]
        direction TB
        P["Orchestration Pipeline<br/>Planning → Think → Discuss → Execute → Report"]
        MEM["Memory System<br/>L0 Capture → L1 Atoms → L2 Scenes → L3 Persona"]
        XS["Knowledge & Learning<br/>XSkill · RAG · GraphRAG · Ontology"]
        T["Tool System<br/>68 MCP Modules · 192 Tools · Dynamic Loading"]
        L["LLM Layer<br/>8 Providers · Circuit Breaker · Smart Routing · Fallback Chain"]
    end

    subgraph DATA["🗄️ Data Layer"]
        direction LR
        PG[("PostgreSQL 16 + pgvector<br/>Primary store + vector search")]
        RD[("Redis 7<br/>Cache + Pub/Sub")]
        GW["Public Gateway (:18800)<br/>Minimal API surface"]
    end

    WEB --> API
    CH --> API
    API --> CORE
    CORE --> PG
    CORE --> RD
    GW --> OC[OpenClaw Execution Layer<br/>Sandbox · Channels]

    classDef pres fill:#e3f2fd,stroke:#1565c0,color:#000
    classDef api fill:#f3e5f5,stroke:#6a1b9a,color:#000
    classDef core fill:#fff3e0,stroke:#e65100,color:#000
    classDef data fill:#e8f5e9,stroke:#2e7d32,color:#000

    class WEB,CH pres
    class REST,WS,OA api
    class P,MEM,XS,T,L core
    class PG,RD,GW data
```

### Orchestration Pipeline

```mermaid
flowchart LR
    PLAN["📋 Planning<br/>Goal decomposition"]
    THINK["💭 Think Phase<br/>Parallel agent reasoning<br/>Context aggregation (7 sources)"]
    DISC["🗣️ Discuss Phase<br/>Challenge → Rebuttal → Synthesis"]
    EXEC["⚡ Execute Phase<br/>Unlimited tool rounds<br/>Activity-based timeout"]
    RPT["📝 Final Report<br/>Always generated"]

    PLAN --> THINK --> DISC --> EXEC --> RPT

    subgraph DISC_DETAIL["Discussion Protocol"]
        R1["Round 1<br/>Challenge + Support"]
        R2["Round 2<br/>Rebuttal (defend/accept)"]
        S["Synthesis<br/>Agreed | Disagreed | Insights"]
        R1 --> R2 --> S
    end

    subgraph EXEC_DETAIL["Execution"]
        E1["Agent Tool Calls<br/>Unlimited rounds"]
        E2["Output Capping<br/>20KB LLM · Full in ACTIVITY.md"]
        E3["Context Sharing<br/>Peers see thinking + results"]
        E1 --> E2 --> E3
    end

    DISC -.-> DISC_DETAIL
    EXEC -.-> EXEC_DETAIL

    classDef phase fill:#e8f5e9,stroke:#2e7d32,color:#000
    classDef detail fill:#fff8e1,stroke:#f57f17,color:#000

    class PLAN,THINK,DISC,EXEC,RPT phase
    class R1,R2,S,E1,E2,E3 detail
```

### Memory & Learning Systems

```mermaid
flowchart LR
    subgraph MEM_SYS["Memory Layers (L0 → L3)"]
        direction LR
        L0["L0 Capture<br/>Raw observations"] --> L1["L1 Atoms<br/>Typed · Scored · Deduped"]
        L1 --> L2["L2 Scenes<br/>Multi-keyword recall"]
        L2 --> L3["L3 Persona<br/>User/Agent profile"]
    end

    subgraph KNOWLEDGE["Knowledge & Learning"]
        direction LR
        XS["XSkill<br/>Skills · Experiences<br/>Critique · Trajectory"]
        RAG["RAG Pipeline<br/>Vector + FTS + Graph<br/>RRF Fusion"]
        GRAPH["GraphRAG<br/>Knowledge graph<br/>Auto-ontology"]
    end

    subgraph TASKS["Continuous Processes"]
        DE["Dreaming Engine<br/>6-hour consolidation cycle"]
        AR["Active Recall<br/>Relevance-scored retrieval"]
        CA["Context Aggregator<br/>7 parallel sources · Token budget"]
    end

    MEM_SYS --> KNOWLEDGE
    MEM_SYS --> TASKS
    KNOWLEDGE --> TASKS

    classDef mem fill:#e3f2fd,stroke:#1565c0,color:#000
    classDef know fill:#fce4ec,stroke:#c62828,color:#000
    classDef task fill:#f3e5f5,stroke:#6a1b9a,color:#000

    class L0,L1,L2,L3 mem
    class XS,RAG,GRAPH know
    class DE,AR,CA task
```

---

## Quick Start

### Prerequisites

| Tool | Version | Required |
|------|---------|----------|
| Docker + Docker Compose | Latest | Yes |
| Git | Latest | Yes |
| LLM API Key | Any | No (free tier available: Kilo/Pollinations/g4f) |
| GPU | N/A | No (only needed for local LLMs) |

> No GPU, Python, or Node.js installation required when running via Docker.
> The system includes free fallback providers — it runs **without any API key**.

### Docker Compose (Recommended)

```bash
# 1. Clone the repository
git clone <repo-url> nexus && cd nexus

# 2. Configure environment
cp .env.example .env
# Edit .env with your API keys (or leave blank to use free tier)

# 3. Build & launch
docker compose up -d --build

# 4. Access
# Web UI + API: http://localhost:8899
# API Docs:     http://localhost:8899/api/docs
```

### Docker Services

| Service | Port | Description |
|---------|------|-------------|
| **Nexus** (All-in-One) | 8899 | Backend + Frontend |
| **Public Gateway** | 18800 | Minimal public API surface (300s proxy timeout) |
| **PostgreSQL** | 5433 | Database + pgvector |
| **Redis** | 6380 | Cache + Pub/Sub |
| **OpenClaw** | 18789 | Execution layer (optional) |
| **Security Toolkit** | — | Build-only profile with nmap, jadx, frida-tools, nuclei, sqlmap, mitmproxy (enable with `--profile tools`) |

### Development Mode

```bash
# Start infrastructure services
docker compose up -d postgres redis

# Backend
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
alembic upgrade head
uvicorn app.main:app --reload --port 8000

# Frontend
cd frontend && npm install && npm run dev
```

---

## Configuration

### LLM Providers

```env
# Core LLM (most capable model)
CORE_LLM_PROVIDER=bedrock    # bedrock | openai | gemini | openrouter | local | kilo
CORE_LLM_API_KEY=
CORE_LLM_MODEL=us.anthropic.claude-opus-4-6-v1

# Agent LLM (cost-efficient model for sub-agents)
AGENT_LLM_PROVIDER=openrouter
AGENT_LLM_API_KEY=sk-or-v1-xxxxx
AGENT_LLM_MODEL=nvidia/nemotron-3-super-120b-a12b:free

# Free Tier (no API key required)
KILO_GATEWAY_ENABLED=true
POLLINATION_ENABLED=true
G4F_ENABLED=true
```

### Execution Control

```env
AGENT_MAX_ROUNDS=0                 # 0 = unlimited (agents run until completion)
AGENT_REASONING_EFFORT=xhigh       # off | minimal | low | medium | high | xhigh
PIPELINE_INACTIVITY_TIMEOUT=0      # 0 = unlimited (activity-based timeout only)
EVOLUTION_AGENT_ENABLED=true       # Continuous improvement advisor
ENABLE_TASK_RESUME=true            # Checkpoint & auto-resume on crash
MAX_PARALLEL_LLM_AGENTS=5          # Maximum concurrent agents per turn
```

### Memory Layers

```env
MEMORY_LAYERS_ENABLED=true
MEMORY_L1_EVERY_N_TURNS=5          # Extract atoms every 5 turns
MEMORY_L3_TRIGGER_EVERY_N_ATOMS=50 # Synthesize persona every 50 atoms
MEMORY_RECALL_STRATEGY=hybrid      # hybrid | keyword | embedding
MEMORY_RECALL_TIMEOUT_MS=3000      # Recall timeout with graceful fallback
```

### Circuit Breaker & Resilience

```env
CIRCUIT_BREAKER_FAILURE_THRESHOLD=5    # Failures before circuit trips
CIRCUIT_BREAKER_FAILURE_WINDOW=30.0    # Observation window (seconds)
CIRCUIT_BREAKER_RECOVERY_TIMEOUT=60.0  # OPEN → HALF_OPEN probe delay
```

### Auto-Scaling

```env
AUTO_SCALE=true  # Automatic CPU-aware worker, rate limit, and queue sizing
# Manual overrides:
# UVICORN_WORKERS=4
# RATE_LIMIT_RPM=240
# TASK_QUEUE_MAX_CONCURRENT=4
```

---

## Database Schema

**19 migrations · 22 tables**

| Table | Purpose |
|-------|---------|
| `agents` | AI agent definitions (role, skills, tools) |
| `tasks` | Task tracking (status, result, subtasks) |
| `messages` | Inter-agent message routing |
| `agent_memories` | Long-term memory with pgvector |
| `knowledge_nodes` / `knowledge_edges` | GraphRAG knowledge graph |
| `tools` | MCP tool registry |
| `skills` / `experiences` | XSkill continual learning |
| `conversation_messages` | Chat history with full-text search (tsvector) |
| `task_events` | Execution event persistence |
| `document_chunks` / `indexed_documents` | RAG pipeline storage |
| `approval_requests` / `tool_policies` / `security_audit_results` | Security: approvals, tool authorization, audit trail |
| `memory_atoms` / `memory_scenes` / `memory_personas` | Memory Layers (L1/L2/L3) |
| `memory_pipeline_state` | Layer processing cursors |
| `task_checkpoints` | Execution state for auto-resume |
| `swarms` | Legacy swarm metadata (managed by migrations) |

---

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Backend | Python 3.12+ · FastAPI · Uvicorn · SQLAlchemy 2.0 |
| Database | PostgreSQL 16 + pgvector |
| Cache | Redis 7+ |
| Frontend | React 19 · TypeScript 5.7 · Vite 6 · Zustand 5 · Tailwind CSS |
| LLM | LiteLLM + AsyncOpenAI SDK (any OpenAI-compatible provider) |
| Embeddings | Sentence-Transformers / OpenAI / Bedrock Titan |
| RAG | LangChain (chunking) + pgvector + FTS + GraphRAG + Ontology |
| Sandbox | Docker (configurable isolation) |
| Memory | 4-Layer System (L0→L3) + GraphRAG + pgvector |
| Learning | XSkill (Skills + Experiences + Critique + Trajectory) |
| Protocol | MCP (stdio + HTTP + OAuth 2.1 PKCE) |
| Channels | 9 platforms + WebSocket + SSE |
| Security | 13 protection layers |
| Scheduling | APScheduler + croniter |
| Deploy | Docker Compose (5 running services + tool profiles) |

---

## Operations & Maintenance

```bash
# Update and rebuild
git pull && docker compose up -d --build

# View logs
docker compose logs -f nexus

# Database backup
docker exec nexus-postgres pg_dump -U <db-user> <db-name> > backup.sql

# Full reset (DESTROYS ALL DATA)
docker compose down -v && docker compose up -d --build

# Reset core + tools only (preserves database)
docker compose down -v && docker compose --profile tools up -d --build

# Monitor resource usage
docker stats

# Check provider health metrics
curl http://localhost:8899/api/v1/system/background-metrics
```

---

## Kilo Gateway for Claude Code

```bash
# Start Kilo Gateway LLM proxy
# pip install 'litellm[proxy]'
python -m litellm --config litellm_config.yaml --port 4006

# Ubuntu / Zsh / Bash
export ANTHROPIC_BASE_URL="http://localhost:4006"
export ANTHROPIC_API_KEY="sk-ant-nexus-fake-key-12345"
export ANTHROPIC_MODEL="global.anthropic.claude-opus-4-6-v1[1m]"

# PowerShell
$env:ANTHROPIC_BASE_URL="http://localhost:4006"
$env:ANTHROPIC_API_KEY="sk-ant-nexus-fake-key-12345"
$env:ANTHROPIC_MODEL="deepseek-v4-pro"

# OpenRouter configuration
export OPENROUTER_API_KEY="sk-or-v1-xxxxx"
export OPENROUTER_MODEL="nvidia/nemotron-3-super-120b-a12b:free"

# Launch Claude Code through the gateway
claude
```

---

## API Reference

The platform exposes a REST API organized into **25 endpoint modules** under `/api/v1`, plus OpenAI-compatible `/v1`, WebSocket, and preview endpoints:

| Endpoint Module | Description |
|-----------------|-------------|
| `/api/v1/auth` | Authentication (JWT, admin bootstrap) |
| `/api/v1/system` | System health, metrics, background metrics, maintenance |
| `/api/v1/chat` | Conversation endpoints |
| `/api/v1/chat/completions` | OpenAI-compatible chat endpoint |
| `/v1/chat/completions`, `/v1/models` | Drop-in OpenAI provider surface |
| `/api/v1/tasks` | Task lifecycle management |
| `/api/v1/agents` | Agent configuration and execution |
| `/api/v1/tools` | Tool registry introspection |
| `/api/v1/mcp` | MCP server management |
| `/api/v1/memory` | Memory system read/write operations |
| `/api/v1/skills` | XSkill skill management |
| `/api/v1/xskill` | XSkill engine (experiences, critique) |
| `/api/v1/sessions`, `/api/v1/session-state` | Session management, durable state, event replay |
| `/api/v1/history` | Session history (full-text search) |
| `/api/v1/artifacts` | File and artifact management |
| `/api/v1/attachments` | File upload/processing |
| `/api/v1/analytics` | Token/cost/usage analytics |
| `/api/v1/batch` | Batch execution across models/sessions |
| `/api/v1/decisions` | Interactive decision queue (agent → user) |
| `/api/v1/project-builder` | Project Builder pipeline |
| `/api/v1/terminal` | Terminal access (key-protected) |
| `/api/v1/hermes` | Hermes agent bridge (chat, code-edit, terminal, search) |
| `/api/v1/cron`, `/api/v1/skill-runner` | Cron jobs + skill-runner commands |
| `/api/v1/doctor`, `/api/v1/plugins`, `/api/v1/approvals` | Health checks, plugin management, approval workflow |
| `/api/v1/public-inbox` | Tokenized public inbox (also on Public Gateway) |
| WebSocket `/ws/*` | Real-time event streaming |

Full interactive API documentation is available at `/api/docs` when the server is running.

---

## License

[MIT License](LICENSE)

Copyright (c) 2025–2026 the Nexus authors

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
