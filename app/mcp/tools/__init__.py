"""
MekongAI Nexus - Tool Modules.

Importing this package triggers registration of all built-in tools
into the global :pydata:`app.mcp.tool_registry.registry`.

Each sub-module calls ``registry.register(...)`` at module level.

Hermes-agent pattern: each tool file self-registers via registry.register().
Importing the package triggers all registrations.
"""

import logging

_logger = logging.getLogger(__name__)

# Import order doesn't matter — each module self-registers.
# Wrapped in try/except so a missing optional dependency in one tool
# doesn't prevent the rest from loading (hermes-agent _discover_tools pattern).

_TOOL_MODULES = [
    # Phase 1: Core tools
    "app.mcp.tools.search_engines",     # multi-engine search abstractions (loaded before web.py)
    "app.mcp.tools.search_scoring",     # freshness + credibility scoring
    "app.mcp.tools.search_orchestrator",  # search orchestration logic
    "app.mcp.tools.web",               # web_search, web_search_news, web_search_academic, fetch_url
    "app.mcp.tools.http_request",
    "app.mcp.tools.filesystem",
    "app.mcp.tools.file_edit",      # edit_file, apply_edits (surgical editing) - ưu tiên sử dụng
    "app.mcp.tools.search_patch",
    "app.mcp.tools.terminal",
    "app.mcp.tools.browser",
    "app.mcp.tools.data",
    "app.mcp.tools.vision",
    "app.mcp.tools.video",         # video_info, extract_frames, extract_audio (ffmpeg)
    "app.mcp.tools.voice",
    "app.mcp.tools.communication",
    "app.mcp.tools.external_channels_tools",
    "app.mcp.tools.telegram",
    "app.mcp.tools.email_inbox",
    "app.mcp.tools.email_diagnostics",
    "app.mcp.tools.public_link_setup",
    # Phase 2: Agent Intelligence Tools (hermes-agent inspired)
    "app.mcp.tools.todo",
    "app.mcp.tools.session_search",
    "app.mcp.tools.memory_tool",
    "app.mcp.tools.delegate",
    # Phase 3: Platform Expansion
    "app.mcp.tools.image_gen",
    # Phase 4: New tools (audit gap-fill)
    "app.mcp.tools.utility",       # calculate, current_datetime, hash_encode, system_info
    "app.mcp.tools.email",         # send_email
    "app.mcp.tools.git",           # git operations
    "app.mcp.tools.archive",       # compress, extract
    "app.mcp.tools.database",      # database_query
    "app.mcp.tools.translate",     # translate_text
    "app.mcp.tools.notify",        # notify_user
    "app.mcp.tools.chart",         # generate_chart
    "app.mcp.tools.rag",           # rag_search, rag_index
    "app.mcp.tools.schedule",      # schedule_task, list_scheduled_tasks
    # Phase 5: Network & Security
    "app.mcp.tools.network",       # port_scan, tcp_connect, ssh_exec, dns_lookup, network_probe
    # Phase 5b: Docker Infrastructure (lab creation, container management)
    "app.mcp.tools.docker_tools",  # docker_compose_up/down, docker_build, docker_run, docker_exec, docker_logs, docker_ps, docker_network
    # Phase 6: AI Browser Automation (optional — requires browser-use)
    "app.mcp.tools.browser_ai",    # browser_ai_action, browser_ai_extract, browser_ai_navigate_flow
    # Phase 7+7b: REMOVED — swarm/debate/agent_autonomy deprecated.
    # Multi-agent coordination now handled by Nexus Orchestrator pipeline.
    # Phase 8: Task Working Memory (TWMS — per-task context documents)
    "app.mcp.tools.task_docs",     # task_doc_read, task_doc_write, task_doc_edit, task_doc_search, task_doc_list
    # Phase 8b: Context Recovery (search ACTIVITY.md for truncated tool outputs)
    "app.mcp.tools.activity_search",  # activity_search
    # Phase 9: Project Builder Tools
    "app.mcp.tools.project_tools",     # project_scaffold, install_dependencies
    "app.mcp.tools.dev_server_tools",  # start_dev_server, stop_dev_server, list_dev_servers, get_server_logs
    "app.mcp.tools.visual_tools",      # visual_check, visual_check_responsive
    "app.mcp.tools.test_tools",        # run_build, run_tests, check_project_health
    # Phase 10: Code Navigation (IDE-like intelligence)
    "app.mcp.tools.code_navigation",   # list_code_definitions, codebase_search, find_definitions, find_references, find_imports
    # Phase 11: Tunnel (public URL exposure for services behind NAT)
    "app.mcp.tools.tunnel",            # expose_port, stop_tunnel, list_tunnels
    # Phase 12: Workflow Automation (multi-step pipeline execution)
    "app.mcp.tools.workflow_tools",     # create_workflow, execute_workflow, list_workflows, get_workflow_status
    # Phase 13: System Monitoring (health checks, resource monitoring)
    "app.mcp.tools.monitoring_tools",   # check_health, monitor_process, check_disk_usage, check_port_available, measure_latency, system_resources
    # Phase 14: API Testing (endpoint testing, sequences, mock servers)
    "app.mcp.tools.api_testing_tools",  # api_test_endpoint, api_test_sequence, api_mock_server
    # Phase 15: Security Scanning (dependency audit, secret detection, header analysis)
    "app.mcp.tools.security_scan_tools",  # scan_dependencies, scan_secrets, scan_headers
    # Phase 15b: Intelligence & Security Operations (OSINT, threat hunting, forensics, CVE research)
    "app.mcp.tools.intelligence_tools",   # cve_lookup, network_topology_map, ioc_validate, osint_domain_profile, threat_intel_enrich, evidence_acquire
    # Phase 15c: Blockchain & Cryptocurrency Analysis
    "app.mcp.tools.blockchain_tools",     # blockchain_trace, wallet_cluster, transaction_graph
    # Phase 15d: Malware Analysis & Mobile Forensics
    "app.mcp.tools.malware_tools",        # malware_sandbox_analyze, mobile_forensics_extract
    # Phase 16: Documentation Generation (API docs, README, changelog)
    "app.mcp.tools.documentation_tools",  # generate_api_docs, generate_readme, generate_changelog
    # Phase 17: CI/CD Tools (workflow generation, Dockerfile, docker-compose)
    "app.mcp.tools.cicd_tools",           # generate_github_actions, generate_dockerfile, generate_docker_compose
    # Phase 17b: Audit Trail (immutable action logging for security/intel operations)
    "app.mcp.tools.audit_log",             # audit_log
    # Phase 18: Document Conversion (MarkItDown — agent-initiated)
    "app.mcp.tools.document_converter",    # convert_document
    # Phase 18b: Task Completion Signal
    "app.mcp.tools.complete_task",         # complete_task
    # Phase 19: Skill Loading (Roo-Code lazy-load pattern — agents load skills on-demand)
    "app.mcp.tools.skill_loader",          # load_skill
    "app.mcp.tools.tool_module_loader",    # load_tool_module
    "app.mcp.tools.self_evolve",           # scaffold_custom_tool_module, scaffold_skill, reload_skills
    # Phase 20: GitHub Automation & Money Making
    "app.mcp.tools.github_tools",          # github_fork, github_clone, github_create_pr, github_search_issues, github_browse_bounties, github_api
    "app.mcp.tools.bounty_tools",          # search_bounties, verify_bounty_target, monitor_pr_status, respond_pr_review, earnings_ledger, prepare_bounty_submission
]

# Discovery: import each module, tolerating failures
import importlib as _importlib

for _mod_name in _TOOL_MODULES:
    try:
        _importlib.import_module(_mod_name)
    except Exception as _e:
        _logger.warning("Could not import tool module %s: %s", _mod_name, _e)

# For backward-compat star imports
from app.mcp.tools import (  # noqa: F401
    web,
    filesystem,
    terminal,
    data,
)

__all__ = [
    "activity_search",
    "api_testing_tools",
    "archive",
    "audit_log",
    "browser",
    "browser_ai",
    "chart",
    "cicd_tools",
    "code_navigation",
    "communication",
    "telegram",
    "email_inbox",
    "complete_task",
    "data",
    "database",
    "delegate",
    "dev_server_tools",
    "email",
    "file_edit",
    "filesystem",
    "git",
    "http_request",
    "image_gen",
    "memory_tool",
    "monitoring_tools",
    "network",
    "notify",
    "project_tools",
    "rag",
    "schedule",
    "search_engines",
    "search_orchestrator",
    "search_patch",
    "search_scoring",
    "session_search",
    "task_docs",
    "terminal",
    "test_tools",
    "todo",
    "translate",
    "tunnel",
    "utility",
    "vision",
    "video",
    "visual_tools",
    "voice",
    "web",
    "workflow_tools",
    "document_converter",
    "security_scan_tools",
    "intelligence_tools",
    "documentation_tools",
    "github_tools",
    "tool_module_loader",
]
