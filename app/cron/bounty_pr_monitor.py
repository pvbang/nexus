"""
MekongAI Nexus - Bounty PR Monitor (Cron).

Automatically polls GitHub notifications + PR status for all bounties in
SUBMITTED/MONITORING state. When action is needed (changes requested, CI fail,
comments, merged, closed), sends a Telegram alert and optionally triggers
an agent session to respond.

Integrates with:
- bounty_ledger.json (reads PR URLs)
- GitHub Notifications API (lightweight signal detection)
- gh CLI (full PR status checks)
- Telegram (alert delivery)
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

WORKSPACE_DIR = Path(os.environ.get("WORKSPACE_DIR", "workspace_data"))
LEDGER_PATH = WORKSPACE_DIR / "bounty_ledger.json"
MONITOR_STATE_PATH = WORKSPACE_DIR / "bounty_monitor_state.json"

POLL_INTERVAL_SECONDS = 900  # 15 minutes


def _load_ledger() -> dict[str, Any]:
    if LEDGER_PATH.exists():
        try:
            return json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"bounties": [], "total_earned": 0, "total_pending": 0}


def _load_monitor_state() -> dict[str, Any]:
    if MONITOR_STATE_PATH.exists():
        try:
            return json.loads(MONITOR_STATE_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"last_notification_check": "", "known_events": {}}


def _save_monitor_state(state: dict[str, Any]) -> None:
    MONITOR_STATE_PATH.write_text(
        json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _get_gh_env() -> dict[str, str]:
    env = dict(os.environ)
    try:
        from app.config import get_settings
        settings = get_settings()
        if settings.github_token:
            env["GH_TOKEN"] = settings.github_token
            env["GITHUB_TOKEN"] = settings.github_token
    except Exception:
        pass
    return env


async def _run_gh(args: list[str], timeout: int = 30) -> tuple[bool, str]:
    gh_path = shutil.which("gh")
    if not gh_path:
        return False, "gh CLI not found"
    try:
        proc = await asyncio.create_subprocess_exec(
            gh_path, *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_get_gh_env(),
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        output = stdout.decode("utf-8", errors="replace").strip()
        if proc.returncode != 0:
            err = stderr.decode("utf-8", errors="replace").strip()
            return False, err or output
        return True, output
    except Exception as e:
        return False, str(e)


async def _send_telegram_alert(text: str) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_DEFAULT_CHAT_ID", "").strip()
    if not token or not chat_id:
        logger.warning("bounty_monitor_telegram_not_configured")
        return False
    try:
        import httpx
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json={
                "chat_id": chat_id,
                "text": text,
                "disable_web_page_preview": True,
            })
            return resp.status_code == 200
    except Exception as e:
        logger.error("bounty_monitor_telegram_failed", error=str(e))
        return False


async def _check_github_notifications() -> list[dict[str, Any]]:
    """Poll GitHub notifications for PR-related activity."""
    success, raw = await _run_gh([
        "api", "/notifications",
        "--jq", '.[].subject | select(.type == "PullRequest") | {title, url}',
    ])
    if not success:
        # Fallback: fetch without jq filter
        success, raw = await _run_gh(["api", "/notifications"])
        if not success:
            return []
        try:
            notifications = json.loads(raw)
            return [
                {
                    "title": n.get("subject", {}).get("title", ""),
                    "url": n.get("subject", {}).get("url", ""),
                    "reason": n.get("reason", ""),
                    "updated_at": n.get("updated_at", ""),
                    "repo": n.get("repository", {}).get("full_name", ""),
                }
                for n in notifications
                if n.get("subject", {}).get("type") == "PullRequest"
            ]
        except (json.JSONDecodeError, TypeError):
            return []

    # If jq worked, parse the filtered output
    results = []
    for line in raw.strip().split("\n"):
        if line.strip():
            try:
                results.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return results


async def _check_pr_status(pr_url: str) -> dict[str, Any]:
    """Full PR status check via gh API."""
    parts = pr_url.rstrip("/").split("/")
    try:
        number = parts[-1]
        repo = f"{parts[-4]}/{parts[-3]}"
    except (IndexError, ValueError):
        return {"error": f"Cannot parse: {pr_url}"}

    success, raw = await _run_gh(["api", f"repos/{repo}/pulls/{number}"])
    if not success:
        return {"error": raw}

    try:
        pr = json.loads(raw)
    except json.JSONDecodeError:
        return {"error": "Invalid JSON"}

    state = pr.get("state", "unknown")
    merged = pr.get("merged", False)
    title = pr.get("title", "")
    head_sha = pr.get("head", {}).get("sha", "")

    # Check CI
    ci_status = "unknown"
    if head_sha:
        ok, ci_raw = await _run_gh(["api", f"repos/{repo}/commits/{head_sha}/status"])
        if ok:
            try:
                ci_status = json.loads(ci_raw).get("state", "unknown")
            except json.JSONDecodeError:
                pass

    # Check reviews
    ok, reviews_raw = await _run_gh(["api", f"repos/{repo}/pulls/{number}/reviews"])
    reviews = []
    if ok:
        try:
            reviews = json.loads(reviews_raw)
        except json.JSONDecodeError:
            pass

    # Check review comments
    ok, comments_raw = await _run_gh(["api", f"repos/{repo}/pulls/{number}/comments"])
    review_comments = []
    if ok:
        try:
            review_comments = json.loads(comments_raw)
        except json.JSONDecodeError:
            pass

    changes_requested = [r for r in reviews if r.get("state") == "CHANGES_REQUESTED"]
    approved = [r for r in reviews if r.get("state") == "APPROVED"]

    return {
        "repo": repo,
        "number": number,
        "title": title,
        "state": state,
        "merged": merged,
        "ci_status": ci_status,
        "changes_requested": len(changes_requested),
        "approved": len(approved),
        "review_comments": len(review_comments),
        "latest_comments": [
            {"author": c.get("user", {}).get("login", "?"), "body": (c.get("body", "") or "")[:200]}
            for c in review_comments[-3:]
        ],
    }


def _determine_action(status: dict[str, Any]) -> tuple[str, str]:
    """Determine what action is needed and its urgency level.

    Returns (action_type, description).
    """
    if status.get("error"):
        return "error", f"API error: {status['error']}"

    if status["merged"]:
        return "merged", "PR MERGED! Track payment."

    if status["state"] == "closed":
        return "closed", "PR closed without merge. Bounty lost."

    if status["ci_status"] == "failure":
        return "ci_fail", "CI FAILING — fix needed."

    if status["changes_requested"] > 0:
        return "changes_requested", "Reviewer requested changes — respond ASAP."

    if status["review_comments"] > 0:
        return "comments", f"{status['review_comments']} review comments to address."

    if status["approved"] > 0:
        return "approved", "PR approved — awaiting merge."

    return "waiting", "Awaiting review."


async def run_bounty_pr_monitor() -> str:
    """Main monitor loop iteration. Check all active PRs and alert on changes."""
    ledger = _load_ledger()
    state = _load_monitor_state()

    # Get all PRs that need monitoring
    active_bounties = [
        b for b in ledger.get("bounties", [])
        if b.get("status") in ("SUBMITTED", "MONITORING") and b.get("pr_url")
    ]

    if not active_bounties:
        return "No active PRs to monitor."

    # Step 1: Check GitHub notifications for early signal
    notifications = await _check_github_notifications()
    notification_repos = {n.get("repo", "") for n in notifications}

    # Step 2: Full PR status check for each active bounty
    alerts = []
    for bounty in active_bounties:
        pr_url = bounty["pr_url"]
        bounty_id = bounty.get("id", bounty.get("url", ""))
        prev_event_key = f"{pr_url}:last_action"
        prev_action = state.get("known_events", {}).get(prev_event_key, "")

        status = await _check_pr_status(pr_url)
        action_type, description = _determine_action(status)

        # Only alert if action changed (avoid spam)
        if action_type != prev_action and action_type not in ("waiting", "error"):
            reward_str = f"${bounty.get('reward_usd', 0)}" if bounty.get('reward_usd') else "TBD"
            emoji_map = {
                "merged": "🟢",
                "closed": "🔴",
                "ci_fail": "🔴",
                "changes_requested": "🟡",
                "comments": "🟡",
                "approved": "🟢",
            }
            emoji = emoji_map.get(action_type, "🔔")

            alert_text = (
                f"{emoji} **PR Update** — {status.get('title', bounty.get('title', ''))}\n"
                f"Repo: {status.get('repo', '')}\n"
                f"Reward: {reward_str}\n"
                f"Action: {description}\n"
                f"PR: {pr_url}"
            )

            if status.get("latest_comments"):
                alert_text += "\n\nLatest feedback:"
                for c in status["latest_comments"]:
                    alert_text += f"\n  @{c['author']}: {c['body'][:100]}"

            alerts.append(alert_text)

            # Update ledger status if terminal
            if action_type == "merged":
                bounty["status"] = "MERGED"
            elif action_type == "closed":
                bounty["status"] = "REJECTED"

        # Save current action
        state.setdefault("known_events", {})[prev_event_key] = action_type

    # Save updated state
    state["last_notification_check"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    _save_monitor_state(state)

    # Save ledger if any status changed
    if any(a for a in alerts if "MERGED" in a or "closed" in a):
        from app.mcp.tools.bounty_tools import _save_ledger as save_ledger
        save_ledger(ledger)

    # Send alerts
    if alerts:
        combined = "🔔 BOUNTY PR MONITOR\n\n" + "\n\n---\n\n".join(alerts)
        await _send_telegram_alert(combined)
        logger.info("bounty_monitor_alerts_sent", count=len(alerts))
        return combined
    else:
        logger.debug("bounty_monitor_no_changes", active_prs=len(active_bounties))
        return f"Checked {len(active_bounties)} PRs — no new actions needed."


class BountyPRMonitor:
    """Background task that runs the PR monitor on a schedule."""

    def __init__(self, interval: float = POLL_INTERVAL_SECONDS) -> None:
        self._interval = interval
        self._task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("bounty_pr_monitor_started", interval=self._interval)

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("bounty_pr_monitor_stopped")

    async def _loop(self) -> None:
        try:
            while self._running:
                try:
                    result = await run_bounty_pr_monitor()
                    logger.debug("bounty_monitor_tick", result_preview=result[:100])
                except Exception as exc:
                    logger.error("bounty_monitor_tick_error", error=str(exc))
                await asyncio.sleep(self._interval)
        except asyncio.CancelledError:
            pass


_monitor: BountyPRMonitor | None = None


def get_bounty_pr_monitor(interval: float = POLL_INTERVAL_SECONDS) -> BountyPRMonitor:
    global _monitor
    if _monitor is None:
        _monitor = BountyPRMonitor(interval=interval)
    return _monitor
