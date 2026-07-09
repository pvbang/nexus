"""
Public Inbox Endpoint (no JWT).

Allows external users to post a message to a tokenized URL. The token is the
shared secret embedded in the URL path.
"""

from __future__ import annotations

from typing import Any, Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db
from app.core.public_inbox import get_public_inbox
from app.services.orchestrator import CoreOrchestrator

logger = structlog.get_logger(__name__)
router = APIRouter()


class PublicInboxMessage(BaseModel):
    message: str = Field(..., min_length=1, max_length=8000, description="Message content")
    sender_id: str = Field(default="", max_length=200, description="Optional external sender identifier")


@router.get("/public/inbox/{token}", response_class=HTMLResponse)
async def public_inbox_ui(token: str) -> str:
    """Simple public HTML form to send a message without needing an API client."""
    inbox = get_public_inbox(token)
    if inbox is None or not inbox.enabled:
        raise HTTPException(status_code=404, detail="Inbox not found")

    # Minimal HTML (no external assets) so it works behind a tunnel easily.
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Public Inbox</title>
    <style>
      body {{ font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Arial; margin: 24px; max-width: 840px; }}
      .card {{ border: 1px solid #ddd; border-radius: 12px; padding: 16px; }}
      label {{ display: block; font-weight: 600; margin-top: 12px; }}
      input, textarea {{ width: 100%; padding: 10px; border: 1px solid #ccc; border-radius: 10px; font-size: 14px; }}
      textarea {{ min-height: 140px; }}
      button {{ margin-top: 14px; padding: 10px 14px; border-radius: 10px; border: 1px solid #222; background: #111; color: #fff; cursor: pointer; }}
      .hint {{ color: #555; font-size: 13px; margin-top: 8px; }}
      pre {{ background: #f6f6f6; padding: 12px; border-radius: 10px; overflow: auto; }}
    </style>
  </head>
  <body>
    <h1>Public Inbox</h1>
    <div class="card">
      <div class="hint">This link is tokenized. Treat it as a secret.</div>
      <form id="f">
        <label>Sender ID (optional)</label>
        <input id="sender_id" placeholder="e.g. alice@example.com" />
        <label>Message</label>
        <textarea id="message" placeholder="Type your message..."></textarea>
        <button type="submit">Send</button>
      </form>
      <div class="hint" id="status"></div>
      <pre id="resp" style="display:none;"></pre>
    </div>
    <script>
      const form = document.getElementById('f');
      const status = document.getElementById('status');
      const resp = document.getElementById('resp');
      form.addEventListener('submit', async (e) => {{
        e.preventDefault();
        status.textContent = 'Sending...';
        resp.style.display = 'none';
        resp.textContent = '';
        const payload = {{
          sender_id: document.getElementById('sender_id').value || '',
          message: document.getElementById('message').value || '',
        }};
        try {{
          const r = await fetch(window.location.pathname, {{
            method: 'POST',
            headers: {{ 'Content-Type': 'application/json' }},
            body: JSON.stringify(payload),
          }});
          const txt = await r.text();
          status.textContent = r.ok ? 'Sent.' : ('Error: HTTP ' + r.status);
          resp.style.display = 'block';
          resp.textContent = txt;
        }} catch (err) {{
          status.textContent = 'Failed: ' + (err && err.message ? err.message : String(err));
        }}
      }});
    </script>
  </body>
</html>"""


@router.post("/public/inbox/{token}")
async def post_public_inbox_message(
    token: str,
    request: Request,
    body: Optional[PublicInboxMessage] = None,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    inbox = get_public_inbox(token)
    if inbox is None or not inbox.enabled:
        raise HTTPException(status_code=404, detail="Inbox not found")

    # Accept both JSON and form submissions (UI form uses JSON by default).
    if body is None:
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        try:
            body = PublicInboxMessage(**(payload or {}))
        except Exception:
            # Try form as fallback
            try:
                form = await request.form()
                body = PublicInboxMessage(
                    message=str(form.get("message") or ""),
                    sender_id=str(form.get("sender_id") or ""),
                )
            except Exception:
                raise HTTPException(status_code=400, detail="Invalid request body")

    # Basic sender identity (best-effort). Avoid trusting external headers too much.
    sender = (body.sender_id or "").strip()
    if not sender:
        client = request.client.host if request.client else "public_user"
        sender = f"public:{client}"

    orchestrator = CoreOrchestrator(db)
    result = await orchestrator.process_user_request(
        message=body.message,
        user_id=sender,
        conversation_id=inbox.conversation_id,
    )
    return result
