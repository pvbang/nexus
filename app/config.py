"""
Nexus - Configuration Management.

Sử dụng pydantic-settings để quản lý cấu hình từ env vars.
"""

from functools import lru_cache
from typing import Optional

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Application ---
    app_name: str = "Nexus"
    app_env: str = "development"
    app_debug: bool = True
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    app_secret_key: str = "change-me-in-production"

    # --- Database ---
    database_url: str = "postgresql+asyncpg://nexus:nexus_password@localhost:5432/nexus_db"
    database_pool_size: int = 20
    database_max_overflow: int = 10

    # --- Redis ---
    redis_url: str = "redis://localhost:6379/0"

    # --- Core LLM (model mạnh nhất cho Core Agent) ---
    # bedrock | openai | gemini | openrouter | deepseek | groq | xai | mistral |
    # together | fireworks | cerebras | moonshot | deepinfra | dashscope | local | kilo
    core_llm_provider: str = "bedrock"
    core_llm_api_key: str = ""
    core_llm_base_url: str = ""
    core_llm_model: str = "us.anthropic.claude-opus-4-6-v1"

    # --- Agent LLM (model tiết kiệm cho sub-agents) ---
    agent_llm_provider: str = "openrouter"
    agent_llm_api_key: str = ""
    agent_llm_base_url: str = "https://openrouter.ai/api/v1"
    agent_llm_model: str = "nvidia/nemotron-3-super-120b-a12b:free"

    # --- Kilo Gateway (free models, anonymous access) ---
    # Supports anonymous access via IP-based rate limiting (200 req/hour/IP)
    # Available free models: kilo-auto/free, nvidia/nemotron-3-super-120b-a12b:free, poolside/laguna-m.1:free
    kilo_gateway_base_url: str = "https://api.kilo.ai/api/gateway/v1"
    kilo_gateway_enabled: bool = True

    # --- Pollinations AI (free anonymous models) ---
    # No API key required - OpenAI-compatible endpoint
    # Models: openai, mistral, llama, qwen, gemini, etc.
    pollination_base_url: str = "https://text.pollinations.ai/openai"
    pollination_enabled: bool = True

    # --- GPT4Free (anonymous access to multiple providers) ---
    # pip install g4f
    g4f_enabled: bool = True

    # --- AWS Bedrock ---
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_session_token: str = ""  # Support for temporary credentials (STS)
    aws_region: str = "us-east-1"
    # Whether to run the background AWS session-token refresher.
    #   None  → AUTO: on only when a bedrock provider is in use AND a rotating STS
    #           session token is present (static IAM keys are not rotated).
    #   True  → always on (operator opt-in).
    #   False → always off (e.g. static/long-lived key, or bedrock no longer used).
    aws_token_refresh_enabled: Optional[bool] = None
    # URL to fetch refreshed AWS session token (e.g. from Grafana Infinity / STS bridge)
    aws_token_refresh_url: str = "https://agentic-backend-grafana.capitol.ai:443"
    aws_token_refresh_interval: int = 14400  # Default 4 hours in seconds
    # Grafana auth for token refresh (Basic Auth or Bearer token)
    aws_token_refresh_auth_user: str = ""
    aws_token_refresh_auth_pass: str = ""
    aws_token_refresh_bearer_token: str = ""

    # --- OpenAI ---
    openai_api_key: str = ""

    # --- OpenRouter ---
    openrouter_api_key: str = ""

    # --- Local LLM (optional) ---
    local_llm_enabled: bool = False
    local_llm_base_url: str = "http://localhost:11434/v1"
    local_llm_model: str = "qwen2.5:32b"

    # --- Embedding (optional — best-effort, falls back to keyword search) ---
    # Override provider/model/key specifically for embeddings.
    # If empty, inherits from core_llm_provider/core_llm_api_key.
    embedding_provider: str = ""  # bedrock | openai | openrouter | gemini | local
    embedding_model: str = ""  # e.g. text-embedding-3-small, amazon.titan-embed-text-v2:0
    embedding_api_key: str = ""  # Dedicated key (falls back to core/openai key)
    embedding_base_url: str = ""  # Custom endpoint URL
    embedding_dimensions: int = 0  # 0 = auto-detect from model defaults (1024 for titan/qwen, 1536 for openai)

    # --- Memory ---
    memory_provider: str = "local"  # local | mem0
    mem0_api_key: str = ""
    zep_api_key: Optional[str] = None

    # --- RAG (Retrieval-Augmented Generation) ---
    rag_chunk_size: int = 1000          # chars per chunk
    rag_chunk_overlap: int = 200        # overlap chars between chunks
    rag_auto_index_threshold: int = 10000  # auto-index docs larger than this (chars)
    rag_top_k: int = 5                  # default retrieval count
    rag_vector_weight: float = 0.5      # weight for vector search in hybrid
    rag_graph_weight: float = 0.3       # weight for graph search in hybrid
    rag_fts_weight: float = 0.2         # weight for FTS in hybrid
    rag_collection: str = "documents"   # default collection name
    # ── Advanced RAG (Anthropic Contextual Retrieval + reranking) ──
    rag_contextual_enabled: bool = True       # prepend LLM-generated context to chunks
    rag_contextual_threshold: int = 6000      # only contextualize docs larger than this (chars)
    rag_contextual_max_chunks: int = 200      # safety cap on per-doc LLM context calls
    rag_rerank_enabled: bool = True           # cross-encoder rerank after hybrid fusion
    rag_rerank_candidates: int = 30           # how many fused hits to feed the reranker
    rag_rerank_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"  # local CE model

    # --- Sandbox ---
    ironclaw_binary_path: str = "./src/open-source/ironclaw/target/release/ironclaw"
    ironclaw_enabled: bool = False
    docker_sandbox_enabled: bool = True
    docker_sandbox_image: str = "python:3.12-slim"
    docker_sandbox_memory: str = "1g"  # Upgraded: agents need RAM for builds
    docker_sandbox_cpu: float = 2.0  # Upgraded: 2 cores for parallel tasks
    docker_sandbox_timeout: int = 600  # Upgraded: 10 min for complex operations
    docker_sandbox_network: bool = True  # Upgraded: agents need network access

    # --- External Access ---
    # Public URL base cho dev servers. User bên ngoài truy cập qua link này.
    # Để trống = tự detect IP public. Ví dụ: "http://103.45.67.89"
    external_base_url: str = ""
    # Public hostname/IP for dev server preview URLs (used when running in Docker)
    # Env: NEXUS_PUBLIC_HOST
    public_host: str = ""
    # Tự động mở port trên firewall (ufw/iptables) khi start dev server
    auto_open_firewall_port: bool = True

    # --- Project Builder ---
    project_builder_enabled: bool = True
    project_builder_max_iterations: int = 20
    project_builder_quality_threshold: float = 0.85
    project_builder_stall_limit: int = 3
    project_builder_dev_server_timeout: int = 60
    project_builder_frontend_port_start: int = 3100
    project_builder_frontend_port_end: int = 3199
    project_builder_backend_port_start: int = 8100
    project_builder_backend_port_end: int = 8199
    project_builder_projects_dir: str = "workspace_data/projects"

    # --- MCP ---
    mcp_servers_dir: str = "./mcp_servers"
    mcp_auto_discover: bool = True
    # JSON array of MCPServerConfig objects for external MCP server connections
    # Example: [{"name":"filesystem","command":"npx","args":["-y","@modelcontextprotocol/server-filesystem","/tmp"]}]
    mcp_servers: str = ""
    mcp_oauth_redirect_uri: str = "http://localhost:8000/api/v1/mcp/oauth/callback"

    # --- Channels ---
    telegram_bot_token: Optional[str] = None
    telegram_default_chat_id: str = ""  # optional default chat_id for tool-driven messaging
    # Gate for the background getUpdates poll loop (inbound Telegram → router).
    # OFF by default: Telegram's getUpdates is single-consumer, so a background
    # poller competes with the agent-facing telegram_get_updates tool and causes
    # 409 Conflict — and silently swallows owner replies before the resume
    # detector can see them. The token still powers send_external_message and the
    # telegram_get_updates tool. Set TELEGRAM_POLLING_ENABLED=true only if you
    # want "message the bot to assign work" (and are NOT also polling via tools).
    telegram_polling_enabled: bool = False
    discord_bot_token: str = ""
    discord_guild_ids: str = ""  # comma-separated guild IDs
    discord_command_prefix: str = "!"
    discord_default_channel_id: str = ""  # optional default channel_id for tool-driven messaging
    slack_bot_token: Optional[str] = None
    slack_app_token: str = ""  # for Socket Mode (xapp-...)
    slack_signing_secret: Optional[str] = None
    slack_default_channel: str = ""  # optional default channel ID for tool-driven messaging

    # --- Channels: WhatsApp ---
    whatsapp_enabled: bool = False      # Explicit opt-in to avoid poll spam
    whatsapp_api_url: str = ""          # Baileys bridge URL or Cloud API base
    whatsapp_api_token: str = ""        # Meta Cloud API access token
    whatsapp_phone_number_id: str = ""  # Meta Cloud API phone number ID
    whatsapp_mode: str = "bridge"       # "bridge" (Baileys) or "cloud" (Meta)
    whatsapp_default_to: str = ""       # optional default recipient for tool-driven messaging

    # --- Channels: Email ---
    # IMAP/SMTP credentials below are ALSO used by the agent-facing tools
    # (fetch_emails / send_email). They do NOT, by themselves, enable the
    # auto-polling channel that turns inbound mail into tasks.
    email_imap_host: str = ""
    email_imap_port: int = 993
    email_smtp_host: str = ""
    email_smtp_port: int = 587
    email_username: str = ""
    email_password: str = ""
    email_from_address: str = ""
    # Gate for the auto-polling EmailChannel (inbound mail → auto-creates a task).
    # OFF by default: receiving e.g. GitHub notification mail must not spawn tasks.
    # Set EMAIL_CHANNEL_ENABLED=true only if you want "email the agent to assign work".
    email_channel_enabled: bool = False

    # --- Channels: SMS (Twilio) ---
    sms_account_sid: str = ""
    sms_auth_token: str = ""
    sms_phone_number: str = ""

    # --- Channels: Signal ---
    signal_api_url: str = ""            # signal-cli-rest-api URL
    signal_phone_number: str = ""
    signal_default_recipient: str = ""  # optional default recipient for tool-driven messaging

    # --- Channels: Matrix ---
    matrix_homeserver: str = ""         # e.g. https://matrix.org
    matrix_user_id: str = ""            # e.g. @bot:matrix.org
    matrix_access_token: str = ""

    # --- Channels: Webhook ---
    webhook_secret: str = ""            # Shared secret for signature validation

    # --- Security ---
    terminal_api_key: str = ""  # Empty = no auth (dev mode). Set to protect terminal in production.
    jwt_secret_key: str = "change-me-jwt-secret"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60
    jwt_token_version: int = 0  # Bump to revoke all previously-issued tokens.

    # --- Admin Login Credentials ---
    admin_username: str = "admin"
    admin_password: str = ""  # Empty = auth disabled. Set to enable login protection.
    admin_password_hash: str = ""  # bcrypt hash ($mkv2$…). Preferred over admin_password when set.

    # --- Security: Tool Policy (OpenClaw-inspired) ---
    tool_policy_enabled: bool = True
    tool_policy_default_action: str = "ask"  # allow, deny, ask, sandbox
    tool_policy_workspace_only: bool = True  # enforce workspace boundaries for file tools

    # --- Security: SSRF Guard (OpenClaw-inspired) ---
    ssrf_guard_enabled: bool = True
    ssrf_allow_localhost: bool = False   # True only for development
    ssrf_allow_private: bool = False     # DANGEROUS: allow private IPs

    # --- Security: Approval Workflow (OpenClaw-inspired) ---
    approval_timeout_seconds: int = 120
    approval_max_pending: int = 50

    security_audit_on_startup: bool = False
    security_audit_cron: str = ""

    # --- Engagement Authorization (deployment-level standing authorization) ---
    # Authorization for security/intel engagements is a property of the DEPLOYMENT,
    # set once by the operator — NOT something each chat request must re-declare.
    # When roe_standing_authorization is True, generated ROEs state that the operating
    # organization holds standing authorization for in-scope targets, so individual
    # requests don't need to write "I own this domain" / "Company X hired me".
    # The fields below are rendered verbatim into the ROE's Authorization Reference.
    # This records WHO is accountable — it does not, and cannot, remove the operator's
    # real-world responsibility to actually hold that authorization.
    roe_standing_authorization: bool = False
    roe_authorizing_org: str = ""          # e.g. "National Cyber Security Operations"
    roe_authorization_basis: str = ""      # e.g. "Standing government engagement mandate"

    # --- Smart Model Routing (hermes-agent inspired) ---
    smart_routing_enabled: bool = True
    smart_routing_max_chars: int = 160
    smart_routing_max_words: int = 28

    # --- Provider Fallback Chain (hermes-agent inspired) ---
    # Comma-separated list: "provider:model,provider:model,..."
    # Example: "openrouter:anthropic/claude-sonnet-4-20250514,openai:gpt-4o,gemini:gemini-2.0-flash"
    llm_fallback_providers: str = ""
    llm_fallback_enabled: bool = True
    llm_fallback_max_failures: int = 3
    llm_fallback_cooldown: int = 120

    # --- Smart Wait Strategy (rate limit patience) ---
    # When preferred provider hits rate limit, wait for reset instead of fallback
    # (for hard tasks — trivial tasks always fallback immediately)
    smart_wait_enabled: bool = True
    smart_wait_max_seconds: int = 1200      # max wait 20 minutes for any provider
    smart_wait_check_interval: int = 30     # check provider health every 30s during wait

    # --- Credential Pool Rotation (hermes-agent inspired) ---
    # JSON mapping provider → list of API keys for rotation
    # Example: {"openrouter": ["key1", "key2"], "anthropic": ["sk-ant-1", "sk-ant-2"]}
    llm_credential_pool: str = ""
    # Cooldown (seconds) for a rate-limited key before reuse
    llm_credential_cooldown: int = 600

    # --- Credential Leak Detection ---
    credential_scan_tool_outputs: bool = True

    # --- LLM Concurrency Control ---
    max_parallel_llm_agents: int = 5      # max agents executing LLM calls simultaneously per turn
    max_concurrent_llm_calls: int = 10    # global max concurrent LLM API calls system-wide

    # --- Message Bus ---
    message_bus_buffer_size: int = 500    # max messages buffered during Redis outage (L5)

    # --- Parallel Tool Execution (hermes-agent inspired) ---
    tool_max_concurrent: int = 8          # max concurrent tool executions (per-agent)
    max_parallel_tools: int = 8
    tool_iteration_budget: int = 99999    # max tool calls per agent turn (legacy, use agent_max_tool_calls)
    tool_timeout: int = 8000              # per-tool timeout in seconds (generous for complex multi-engine search)
    # Agent execution tunables (M3: single source of truth; were hardcoded in agent_worker).
    agent_tool_output_cap_chars: int = 20000        # cap tool output injected into LLM messages
    context_compress_threshold_chars: int = 60000   # compress older tool results past this size
    # ── Token Governor (A budget + B estimator + C loop monitor) ──
    # Budgets are RUNAWAY CEILINGS (cost guard), not administrative limits —
    # set wide so legitimate long tasks are never cut short.
    agent_token_budget: int = 400_000            # cumulative tokens / one agent react-loop
    task_token_budget: int = 2_000_000           # cumulative tokens / whole task
    token_budget_warn_ratio: float = 0.80        # warn → ask to wrap up
    token_budget_hard_stop_ratio: float = 0.95   # hard-stop → force conclusion
    loop_monitor_warn_count: int = 3             # same tool call repeated → warn
    loop_monitor_hard_stop_count: int = 5        # same tool call repeated → hard-stop
    token_estimator_use_tiktoken: bool = True    # disable if env lacks tiktoken
    pipeline_timeout: int = 0             # DISABLED (was 300). Use activity-based timeout instead. 0 = unlimited (hermes-agent style).
    pipeline_max_retries: int = 3         # Auto-retry pipeline on timeout (total attempts = 1 + retries)
    max_stream_duration: int = 3600       # SSE stale timeout (seconds): if no event for this long, move task to background. NOT an absolute limit — active tasks run forever. Default 1h for long debates.

    # --- Activity-Based Timeout (hermes-agent inspired) ---
    # Pipeline runs FOREVER as long as events/tool-calls keep flowing.
    # Only triggers timeout when agent is IDLE (no activity) for this many seconds.
    # This prevents killing complex multi-agent tasks that are actively working.
    pipeline_inactivity_timeout: int = 0  # seconds of INACTIVITY before timeout (0=unlimited). Active tasks never timeout.

    # --- Agent Execution Limits (0 = unlimited) ---
    # All limits default to 0 = unlimited (agent runs until task complete)
    agent_max_rounds: int = Field(default=0, description="Core agent max tool-calling rounds (0=unlimited)")
    agent_max_tool_calls: int = Field(default=0, description="Max total tool calls per turn (0=unlimited, overrides tool_iteration_budget)")
    sub_agent_max_rounds: int = Field(default=0, description="Sub-agent max tool rounds, overrides per-role (0=unlimited)")
    agent_reasoning_effort: str = Field(default="xhigh", description="Default reasoning depth: off|minimal|low|medium|high|xhigh")
    agent_max_retries: int = Field(default=10, description="Reflection max retries for weak agent results (0=unlimited)")

    auto_scale: bool = True

    rate_limit_enabled: bool = True
    rate_limit_rpm: int = 0               # 0 = auto (60 * cpu_cores)
    rate_limit_burst: int = 0             # 0 = auto (10 * cpu_cores)

    task_queue_max_concurrent: int = 0    # 0 = auto (cpu_cores)
    task_queue_max_size: int = 0          # 0 = auto (cpu_cores * 20)

    uvicorn_workers: int = 0              # 0 = auto (cpu_cores)

    # --- Data Retention ---
    task_events_retention_days: int = Field(
        default=90,
        description="Days to retain task_events records. A cleanup cron job should purge older entries.",
    )

    tool_gates: str = Field(default="", description="Comma-separated tool gates: 'docker_deploy:false,send_email:false'")

    # --- Evolution Agent ---
    evolution_agent_enabled: bool = True
    evolution_model: str = ""  # Empty = use default cheap model
    evolution_min_turn: int = 1
    evolution_inject_threshold: float = 0.6
    evolution_max_tokens: int = 4096

    # --- Memory Layers (TencentDB Agent Memory inspired) ---
    memory_layers_enabled: bool = True
    memory_l1_every_n_turns: int = 5
    memory_l1_idle_timeout_seconds: int = 600
    memory_l2_min_interval_seconds: int = 900
    memory_l2_max_interval_seconds: int = 3600
    memory_l3_trigger_every_n_atoms: int = 50
    memory_recall_max_results: int = 5
    memory_recall_strategy: str = "hybrid"
    memory_recall_timeout_ms: int = 3000
    memory_recall_score_threshold: float = 0.3
    memory_budget_l3_tokens: int = 800
    memory_budget_l2_tokens: int = 1200
    memory_budget_l1_tokens: int = 1000
    memory_dedup_threshold: float = 0.85

    # --- Smart Context Aggregation ---
    context_budget_simple: int = Field(
        default=8000,
        description="Character budget for context in simple chat interactions",
    )
    context_budget_complex: int = Field(
        default=12000,
        description="Character budget for context in complex task interactions",
    )
    context_source_timeout: float = Field(
        default=3.0,
        description="Timeout in seconds for each context source fetch",
    )

    # --- Circuit Breaker (LLM provider resilience) ---
    circuit_breaker_failure_threshold: int = Field(
        default=5,
        description="Number of failures within window to trip circuit breaker",
    )
    circuit_breaker_failure_window: float = Field(
        default=30.0,
        description="Time window (seconds) for failure counting",
    )
    circuit_breaker_recovery_timeout: float = Field(
        default=60.0,
        description="Seconds to wait in OPEN state before probing recovery",
    )

    # --- Task Checkpoint & Auto-Resume ---
    enable_task_resume: bool = Field(
        default=True,
        description="Enable task checkpoint persistence and auto-resume on restart",
    )
    enable_task_auto_resume: bool = Field(
        default=False,
        description=(
            "Auto-resume interrupted tasks on server startup. Default False — "
            "crashed/suspended tasks instead surface a manual 'resume' button. "
            "Checkpoint persistence is controlled separately by enable_task_resume."
        ),
    )
    task_checkpoint_max_per_task: int = Field(
        default=3,
        description="Maximum checkpoints to keep per task (older ones pruned)",
    )

    # --- Long Message Processing ---
    long_message_char_threshold: int = 80_000  # chars threshold to trigger chunked processing
    long_message_max_chars: int = 500_000  # absolute max (matches schema validation)
    long_message_chunk_size_tokens: int = 30_000  # tokens per chunk
    long_message_chunk_overlap_tokens: int = 500  # overlap between chunks
    long_message_max_chunks: int = 20  # max number of chunks
    long_message_strategy: str = "auto"  # "auto", "map_reduce", "sequential"

    @model_validator(mode="after")
    def _validate_production_secrets(self) -> "Settings":
        """Fail fast if production mode uses default secrets."""
        if self.app_env == "production":
            _INSECURE_DEFAULTS = {
                "change-me-in-production",
                "change-me-jwt-secret",
            }
            if self.jwt_secret_key in _INSECURE_DEFAULTS:
                raise ValueError(
                    "CRITICAL: JWT_SECRET_KEY must be changed in production! "
                    "Set a strong random secret via environment variable."
                )
            if self.app_secret_key in _INSECURE_DEFAULTS:
                raise ValueError(
                    "CRITICAL: APP_SECRET_KEY must be changed in production! "
                    "Set a strong random secret via environment variable."
                )
        return self

    @property
    def effective_workers(self) -> int:
        """Get effective worker count (auto-detected or manual)."""
        if self.uvicorn_workers > 0:
            return self.uvicorn_workers
        if self.auto_scale:
            import os
            return max(2, min(os.cpu_count() or 2, 8))
        return 2

    @property
    def effective_rate_limit_rpm(self) -> int:
        if self.rate_limit_rpm > 0:
            return self.rate_limit_rpm
        if self.auto_scale:
            import os
            cores = os.cpu_count() or 2
            return 60 * cores  # 60 RPM per core
        return 120

    @property
    def effective_rate_limit_burst(self) -> int:
        if self.rate_limit_burst > 0:
            return self.rate_limit_burst
        if self.auto_scale:
            import os
            cores = os.cpu_count() or 2
            return 10 * cores
        return 20

    @property
    def effective_queue_concurrent(self) -> int:
        if self.task_queue_max_concurrent > 0:
            return self.task_queue_max_concurrent
        if self.auto_scale:
            import os
            return max(2, min(os.cpu_count() or 2, 6))
        return 3

    @property
    def effective_queue_size(self) -> int:
        if self.task_queue_max_size > 0:
            return self.task_queue_max_size
        if self.auto_scale:
            import os
            cores = os.cpu_count() or 2
            return cores * 20
        return 50

    # --- Owner Identity & Payment (public info, safe to expose) ---
    github_username: str = ""
    github_token: str = ""
    personal_email: str = ""
    ethereum_address: str = ""
    tron_address: str = ""
    bitcoin_address: str = ""
    solana_address: str = ""
    payment_bank_name: str = ""
    payment_bank_account_number: str = ""
    payment_bank_account_holder: str = ""

    # --- Agent Intelligence: Profiles, Reasoning, Model Switching ---
    # JSON array of available models: [{"name":"gpt-4o","provider":"openai","description":"..."}]
    available_models: str = ""
    default_reasoning_effort: str = "medium"  # Legacy: overridden by agent_reasoning_effort if set
    default_profile: str = "default"

    # ------------------------------------------------------------------
    # Effective limit properties (0 = unlimited → safety cap 500)
    # ------------------------------------------------------------------
    # MAXIMUM-POWER-MODE: Increased from 100 to 500. Agents never give up —
    # with Progressive Compaction, context is managed efficiently so agents
    # can run hundreds of rounds without overflow. Cost is managed by
    _SAFETY_CAP: int = 99999

    @property
    def effective_agent_max_rounds(self) -> int:
        """Core agent max tool-calling rounds. 0 = unlimited."""
        return self.agent_max_rounds if self.agent_max_rounds > 0 else self._SAFETY_CAP

    @property
    def effective_agent_max_tool_calls(self) -> int:
        """Max total tool calls per turn. AGENT_MAX_TOOL_CALLS > TOOL_ITERATION_BUDGET."""
        if self.agent_max_tool_calls > 0:
            return self.agent_max_tool_calls
        # Backward compat: if legacy TOOL_ITERATION_BUDGET is set and != default
        if self.tool_iteration_budget > 0 and self.tool_iteration_budget != 40:
            return self.tool_iteration_budget
        return self._SAFETY_CAP  # unlimited

    @property
    def effective_sub_agent_max_rounds(self) -> int:
        """Sub-agent max tool rounds. 0 = unlimited (overrides per-role policy)."""
        return self.sub_agent_max_rounds if self.sub_agent_max_rounds > 0 else self._SAFETY_CAP

    @property
    def effective_agent_max_retries(self) -> int:
        """Reflection max retries for weak agents. 0 = unlimited."""
        return self.agent_max_retries if self.agent_max_retries > 0 else self._SAFETY_CAP

    @property
    def effective_reasoning_effort(self) -> str:
        """Effective default reasoning effort — agent_reasoning_effort takes priority."""
        return self.agent_reasoning_effort or self.default_reasoning_effort or "xhigh"

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def sync_database_url(self) -> str:
        """Sync version of database URL for Alembic migrations."""
        return self.database_url.replace("+asyncpg", "")


@lru_cache()
def get_settings() -> Settings:
    """Cached settings singleton.

    DESIGN-2 NOTE: This is an ``@lru_cache`` singleton — the Settings object
    is created ONCE at first call and reused forever. Environment variables
    are read only at that point. If you change env vars after import (e.g. in
    tests), call ``get_settings.cache_clear()`` first.

    Many modules capture this at module level:
        ``settings = get_settings()``
    which means their ``settings`` reference is frozen at import time.
    This is intentional for production but may surprise during testing.

    To pick up an edited ``.env`` at runtime without a restart, call
    ``reload_settings()`` (a deliberate, operator-triggered reload).
    """
    return Settings()


def reload_settings() -> "Settings":
    """Re-read ``.env`` and rebuild the cached settings singleton.

    For a DELIBERATE, operator-triggered config change (switch LLM
    provider/model, rotate a feature API key, etc.) without restarting the app.
    Steps:
      1. ``load_dotenv(override=True)`` — re-load the .env file into
         ``os.environ``. ``override=True`` (unlike the boot-time
         ``override=False``) means the freshly-edited file WINS — this is the
         explicit "I changed it, apply it" path.
      2. clear the ``get_settings`` lru_cache so the next ``get_settings()``
         rebuilds from the new environment.
      3. return the fresh ``Settings``.

    SCOPE/limitation: this updates everything that reads ``get_settings()`` per
    use. It does NOT retroactively mutate modules that captured
    ``settings = get_settings()`` at import time — those keep their snapshot
    until they re-read. So this is a bounded reload, not a global hot-swap.

    AWS credentials are intentionally NOT the target here: a session token
    sourced from refresh-aws.sh / the environment still outranks .env (the
    boot-time ``override=False`` and the refresher both win), so reloading does
    not clobber a live STS token with a stale one from .env.
    """
    try:
        from dotenv import load_dotenv
        load_dotenv(override=True)
    except Exception:
        pass
    get_settings.cache_clear()
    return get_settings()
