"""
MekongAI Nexus - API v1 Router.

Tổng hợp tất cả API endpoints.

Architecture: Protected router requires valid JWT (when AUTH_ENABLED=True).
Public router (auth, system/info) is always accessible.
"""

from fastapi import APIRouter, Depends

from app.api.v1.endpoints import (
    agents, analytics, artifacts, attachments, auth, batch, chat, cron_skills, decisions,
    hermes, history, mcp, project_builder, sessions, session_state, skills, tasks,
    terminal, tools, system, xskill, doctor_plugins, public_inbox,
)
from app.api.v1.endpoints.artifacts import download_router as artifacts_download_router
from app.core.auth import get_current_user

api_router = APIRouter()

# ── Public routes (no auth required) ──
api_router.include_router(auth.router, prefix="/auth", tags=["Auth"])
api_router.include_router(system.router, prefix="/system", tags=["System"])
# Public inbox: token-in-URL entry point for external users (no JWT).
api_router.include_router(public_inbox.router, tags=["Public Inbox"])
# Download endpoints use get_download_user (supports both Bearer header and ?token= query param)
api_router.include_router(artifacts_download_router, prefix="/artifacts", tags=["Artifacts"])

# ── Protected routes (require valid JWT when auth is enabled) ──
# Router-level dependency: get_current_user is called for ALL endpoints below.
# When AUTH_ENABLED=False (dev mode), it returns "default_user" transparently.
protected_router = APIRouter(dependencies=[Depends(get_current_user)])

protected_router.include_router(chat.router, prefix="/chat", tags=["Chat"])
protected_router.include_router(attachments.router, prefix="/attachments", tags=["Attachments"])
protected_router.include_router(tasks.router, prefix="/tasks", tags=["Tasks"])
protected_router.include_router(agents.router, prefix="/agents", tags=["Agents"])
protected_router.include_router(tools.router, prefix="/tools", tags=["Tools"])
protected_router.include_router(xskill.router, prefix="/xskill", tags=["XSkill"])
protected_router.include_router(artifacts.router, prefix="/artifacts", tags=["Artifacts"])
protected_router.include_router(cron_skills.router, tags=["Cron & Skills"])
protected_router.include_router(doctor_plugins.router, tags=["Doctor & Plugins"])
protected_router.include_router(mcp.router, prefix="/mcp", tags=["MCP"])
protected_router.include_router(sessions.router, prefix="/sessions", tags=["Sessions"])
protected_router.include_router(session_state.router, tags=["Session State"])
protected_router.include_router(batch.router, tags=["Batch"])
protected_router.include_router(skills.router, prefix="/skills", tags=["Skills"])
protected_router.include_router(decisions.router, prefix="/decisions", tags=["Decisions"])
protected_router.include_router(project_builder.router, tags=["Project Builder"])
protected_router.include_router(analytics.router, prefix="/analytics", tags=["Analytics"])
protected_router.include_router(terminal.router, tags=["Terminal"])
protected_router.include_router(hermes.router, tags=["Hermes Agent"])
protected_router.include_router(history.router, prefix="/history", tags=["Session History"])

api_router.include_router(protected_router)
