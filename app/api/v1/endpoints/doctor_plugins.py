"""
MekongAI Nexus - Doctor & Plugin API Endpoints.
"""

from fastapi import APIRouter, HTTPException

from app.core.doctor import DoctorService
from app.core.approval import get_approval_manager
from app.plugins.registry import get_plugin_registry

router = APIRouter()


# ── Doctor ──────────────────────────────────────────────────────────
@router.get("/doctor", summary="Run system health check")
async def run_doctor():
    doctor = DoctorService()
    results = await doctor.run_all()
    summary = {
        "total": len(results),
        "ok": sum(1 for r in results if r.status == "ok"),
        "warnings": sum(1 for r in results if r.status == "warning"),
        "errors": sum(1 for r in results if r.status == "error"),
    }
    return {
        "summary": summary,
        "results": [
            {"check": r.check, "status": r.status, "message": r.message,
             "fix_hint": r.fix_hint}
            for r in results
        ],
    }


# ── Plugins ─────────────────────────────────────────────────────────
@router.get("/plugins", summary="List all plugins")
async def list_plugins():
    registry = get_plugin_registry()
    return {"plugins": registry.list_all()}


@router.get("/plugins/discover", summary="Discover available plugins")
async def discover_plugins():
    registry = get_plugin_registry()
    manifests = registry.discover()
    return {
        "discovered": [
            {"id": m.id, "name": m.name, "version": m.version,
             "capabilities": [c.value for c in m.capabilities]}
            for m in manifests
        ],
    }


@router.post("/plugins/{plugin_id}/activate", summary="Activate a plugin")
async def activate_plugin(plugin_id: str):
    registry = get_plugin_registry()
    try:
        await registry.activate(plugin_id)
        return {"status": "activated", "plugin_id": plugin_id}
    except FileNotFoundError:
        raise HTTPException(404, f"Plugin not found: {plugin_id}")
    except Exception as e:
        raise HTTPException(500, f"Plugin activation failed: {e}")


@router.post("/plugins/{plugin_id}/deactivate", summary="Deactivate a plugin")
async def deactivate_plugin(plugin_id: str):
    registry = get_plugin_registry()
    await registry.deactivate(plugin_id)
    return {"status": "deactivated", "plugin_id": plugin_id}



# ── Tool Approvals ──────────────────────────────────────────────────
@router.get("/approvals/pending", summary="List pending tool approvals")
async def list_pending_approvals():
    mgr = get_approval_manager()
    return {"pending": mgr.list_pending()}


@router.post("/approvals/{request_id}/approve", summary="Approve a tool execution")
async def approve_tool(request_id: str):
    mgr = get_approval_manager()
    ok = mgr.approve(request_id)
    if not ok:
        raise HTTPException(404, "Approval request not found")
    return {"approved": True}


@router.post("/approvals/{request_id}/deny", summary="Deny a tool execution")
async def deny_tool(request_id: str, reason: str = ""):
    mgr = get_approval_manager()
    ok = mgr.deny(request_id, reason)
    if not ok:
        raise HTTPException(404, "Approval request not found")
    return {"denied": True, "reason": reason}
