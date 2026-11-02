"""
Nexus - Bounty Pipeline Tools.

Complete toolset for autonomous bounty hunting with strict verification gates:
- Scout: search_bounties, github_browse_bounties
- Qualify: verify_bounty_target (check open, competition, feasibility)
- Execute: (uses existing github_fork, git tools, terminal)
- Submit: prepare_bounty_submission
- Monitor: monitor_pr_status, respond_pr_review
- Track: earnings_ledger (persistent state across sessions)

Pipeline: FOUND → QUALIFIED → CLAIMED → EXECUTING → SUBMITTED → MONITORING → MERGED/REJECTED → PAID
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import structlog

from app.mcp.tool_registry import registry, tool_result, tool_error
from app.mcp.tools.filesystem import WORKSPACE_DIR

logger = structlog.get_logger(__name__)

LEDGER_PATH = WORKSPACE_DIR / "bounty_ledger.json"


def _load_ledger() -> dict[str, Any]:
    if LEDGER_PATH.exists():
        try:
            return json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"bounties": [], "total_earned": 0, "total_pending": 0}


def _save_ledger(ledger: dict[str, Any]) -> None:
    LEDGER_PATH.write_text(json.dumps(ledger, indent=2, ensure_ascii=False), encoding="utf-8")


async def _fetch_json(url: str, headers: dict | None = None, timeout: int = 15) -> tuple[bool, Any]:
    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers or {}, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                if resp.status == 200:
                    return True, await resp.json()
                return False, f"HTTP {resp.status}"
    except ImportError:
        try:
            import httpx
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.get(url, headers=headers or {})
                if resp.status_code == 200:
                    return True, resp.json()
                return False, f"HTTP {resp.status_code}"
        except ImportError:
            return False, "Neither aiohttp nor httpx available"
    except Exception as e:
        return False, str(e)


def _get_gh_env() -> dict[str, str]:
    """Get environment with GitHub token for authenticated gh CLI requests."""
    import os
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
    """Run gh CLI command with authentication."""
    import asyncio
    import shutil
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


# ==================================================================
# TOOL 1: search_bounties — Scout phase
# ==================================================================

async def _handle_search_bounties(params: dict[str, Any], **kw: Any) -> str:
    """Search for bounties across platforms with real API calls."""
    platform = params.get("platform", "all")
    skills = params.get("skills", ["python", "typescript"])
    min_reward = params.get("min_reward", 0)
    limit = params.get("limit", 20)

    if isinstance(skills, str):
        skills = [s.strip() for s in skills.split(",")]

    results = []

    # Superteam Earn API
    if platform in ("all", "superteam"):
        success, data = await _fetch_json(
            "https://earn.superteam.fun/api/listings/?type=bounty&status=open&take=20"
        )
        if success and isinstance(data, list):
            for item in data:
                reward = item.get("rewardAmount", 0) or item.get("maxRewardAsk", 0)
                if reward >= min_reward:
                    results.append({
                        "platform": "superteam",
                        "title": item.get("title", ""),
                        "reward_usd": reward,
                        "token": item.get("token", "USDC"),
                        "url": f"https://earn.superteam.fun/listings/{item.get('slug', '')}",
                        "skills": item.get("skills", []),
                        "deadline": item.get("deadline", ""),
                        "submissions_count": item.get("totalSubmissions", 0),
                    })

    # GitHub bounty labels via gh CLI
    if platform in ("all", "github"):
        for lang in skills[:2]:
            success, output = await _run_gh([
                "search", "issues",
                "--json", "title,url,labels,repository,createdAt,comments",
                "--limit", "10",
                "--sort", "updated",
                f"label:bounty language:{lang} state:open",
            ])
            if success and output:
                try:
                    issues = json.loads(output)
                    for issue in issues:
                        results.append({
                            "platform": "github",
                            "title": issue.get("title", ""),
                            "reward_usd": 0,
                            "url": issue.get("url", ""),
                            "skills": [lang],
                            "comments_count": issue.get("comments", 0),
                            "repo": issue.get("repository", {}).get("nameWithOwner", ""),
                        })
                except json.JSONDecodeError:
                    pass

    # Algora bounties via GitHub search
    if platform in ("all", "algora"):
        success, output = await _run_gh([
            "search", "issues",
            "--json", "title,url,labels,repository,comments",
            "--limit", "10",
            '--sort', 'updated',
            'label:"💎 Bounty" state:open',
        ])
        if success and output:
            try:
                issues = json.loads(output)
                for issue in issues:
                    labels = [l.get("name", "") for l in issue.get("labels", [])]
                    reward = 0
                    for lbl in labels:
                        if "$" in lbl:
                            try:
                                reward = int("".join(c for c in lbl if c.isdigit()))
                            except ValueError:
                                pass
                    results.append({
                        "platform": "algora",
                        "title": issue.get("title", ""),
                        "reward_usd": reward,
                        "url": issue.get("url", ""),
                        "labels": labels,
                        "comments_count": issue.get("comments", 0),
                        "repo": issue.get("repository", {}).get("nameWithOwner", ""),
                    })
            except json.JSONDecodeError:
                pass

    # Huntr — AI/ML vulnerability bounties
    if platform in ("all", "huntr"):
        success, data = await _fetch_json(
            "https://huntr.com/api/bounties?status=open&limit=15",
            timeout=20,
        )
        if success and isinstance(data, (list, dict)):
            items = data if isinstance(data, list) else data.get("data", data.get("bounties", []))
            for item in items[:15]:
                reward = item.get("reward", item.get("amount", 0))
                if isinstance(reward, dict):
                    reward = reward.get("amount", 0)
                if reward >= min_reward:
                    results.append({
                        "platform": "huntr",
                        "title": item.get("title", item.get("name", "")),
                        "reward_usd": reward,
                        "url": item.get("url", f"https://huntr.com/bounties/{item.get('id', '')}"),
                        "skills": ["python", "ai", "ml"],
                        "severity": item.get("severity", ""),
                    })

    # Immunefi — Web3 security bounties (public explore page)
    if platform in ("all", "immunefi"):
        success, data = await _fetch_json(
            "https://immunefi.com/explore/",
            timeout=20,
        )
        # Immunefi may not have a public JSON API; try their bounty list endpoint
        if not success:
            success, data = await _fetch_json(
                "https://immunefi.com/v2/bounties",
                timeout=20,
            )
        if success and isinstance(data, (list, dict)):
            items = data if isinstance(data, list) else data.get("data", data.get("bounties", []))
            for item in items[:10]:
                max_bounty = item.get("maxBounty", item.get("maximum_reward", 0))
                if isinstance(max_bounty, str):
                    try:
                        max_bounty = int(max_bounty.replace(",", "").replace("$", ""))
                    except ValueError:
                        max_bounty = 0
                results.append({
                    "platform": "immunefi",
                    "title": item.get("name", item.get("title", "")),
                    "reward_usd": max_bounty,
                    "url": item.get("url", f"https://immunefi.com/bug-bounty/{item.get('slug', '')}"),
                    "skills": item.get("languages", ["solidity"]),
                })

    # CodeHawks — Audit competitions (Cyfrin)
    if platform in ("all", "codehawks"):
        success, data = await _fetch_json(
            "https://codehawks.cyfrin.io/api/v1/contests?status=active",
            timeout=20,
        )
        if success and isinstance(data, (list, dict)):
            items = data if isinstance(data, list) else data.get("data", data.get("contests", []))
            for item in items[:10]:
                prize = item.get("prize_pool", item.get("prize", 0))
                if isinstance(prize, str):
                    try:
                        prize = int(prize.replace(",", "").replace("$", ""))
                    except ValueError:
                        prize = 0
                results.append({
                    "platform": "codehawks",
                    "title": item.get("title", item.get("name", "")),
                    "reward_usd": prize,
                    "url": item.get("url", f"https://codehawks.cyfrin.io/c/{item.get('slug', item.get('id', ''))}"),
                    "skills": ["solidity", "rust"],
                    "deadline": item.get("end_date", item.get("deadline", "")),
                })

    # HackenProof — Multi-language bug bounties
    if platform in ("all", "hackenproof"):
        success, data = await _fetch_json(
            "https://hackenproof.com/api/v1/programs?status=active&limit=10",
            timeout=20,
        )
        if success and isinstance(data, (list, dict)):
            items = data if isinstance(data, list) else data.get("data", data.get("programs", []))
            for item in items[:10]:
                max_reward = item.get("max_reward", item.get("maximum_bounty", 0))
                results.append({
                    "platform": "hackenproof",
                    "title": item.get("name", item.get("title", "")),
                    "reward_usd": max_reward,
                    "url": item.get("url", f"https://hackenproof.com/programs/{item.get('slug', '')}"),
                    "skills": item.get("languages", item.get("technologies", [])),
                })

    # BOSS.dev — GitHub OSS bounties (low competition)
    if platform in ("all", "boss_dev"):
        success, output = await _run_gh([
            "search", "issues",
            "--json", "title,url,labels,repository,comments",
            "--limit", "10",
            "--sort", "updated",
            'label:"boss" state:open',
        ])
        if success and output:
            try:
                issues = json.loads(output)
                for issue in issues:
                    labels = [l.get("name", "") for l in issue.get("labels", [])]
                    reward = 0
                    for lbl in labels:
                        if "$" in lbl or "bounty" in lbl.lower():
                            try:
                                reward = int("".join(c for c in lbl if c.isdigit()))
                            except ValueError:
                                pass
                    results.append({
                        "platform": "boss_dev",
                        "title": issue.get("title", ""),
                        "reward_usd": reward,
                        "url": issue.get("url", ""),
                        "labels": labels,
                        "comments_count": issue.get("comments", 0),
                        "repo": issue.get("repository", {}).get("nameWithOwner", ""),
                    })
            except json.JSONDecodeError:
                pass

    results.sort(key=lambda x: x.get("reward_usd", 0), reverse=True)
    results = results[:limit]

    output = f"Found {len(results)} bounties:\n\n"
    for i, r in enumerate(results[:limit], 1):
        reward_str = f"${r['reward_usd']}" if r.get('reward_usd') else "TBD"
        output += (
            f"{i}. [{r['platform']}] {r['title']}\n"
            f"   Reward: {reward_str} | URL: {r['url']}\n"
        )
        if r.get("comments_count"):
            output += f"   Comments: {r['comments_count']} (competition indicator)\n"
        output += "\n"

    return tool_result(success=True, output=output, data={"count": len(results), "bounties": results})


# ==================================================================
# TOOL 2: verify_bounty_target — Qualification gate (CRITICAL)
# ==================================================================

async def _handle_verify_bounty_target(params: dict[str, Any], **kw: Any) -> str:
    """
    STRICT verification gate before any work begins.
    Checks: issue still open, not locked, competition level, no existing winning PR.
    Returns PASS/FAIL with reasons.
    """
    issue_url = params.get("issue_url", "")
    if not issue_url:
        return tool_error("issue_url is required")

    # Parse owner/repo/number from URL
    # Format: https://github.com/owner/repo/issues/123
    parts = issue_url.rstrip("/").split("/")
    try:
        number = parts[-1]
        repo = f"{parts[-4]}/{parts[-3]}"
    except (IndexError, ValueError):
        return tool_error(f"Cannot parse issue URL: {issue_url}")

    # Fetch full issue data via gh API
    success, raw_json = await _run_gh([
        "api", f"repos/{repo}/issues/{number}",
    ])

    if not success:
        return tool_result(
            success=True,
            output=f"⚠️ CANNOT VERIFY (API error): {raw_json}\nDecision: SKIP — cannot confirm target is viable.",
            data={"verdict": "SKIP", "reason": "api_error"},
        )

    try:
        issue_data = json.loads(raw_json)
    except json.JSONDecodeError:
        return tool_result(
            success=True,
            output="⚠️ CANNOT VERIFY: invalid JSON from API",
            data={"verdict": "SKIP", "reason": "parse_error"},
        )

    checks = []
    verdict = "PASS"

    # Check 1: Issue still open?
    state = issue_data.get("state", "unknown")
    if state != "open":
        checks.append(f"❌ CLOSED (state={state})")
        verdict = "FAIL"
    else:
        checks.append("✅ Issue is OPEN")

    # Check 2: Issue locked?
    locked = issue_data.get("locked", False)
    if locked:
        checks.append("❌ LOCKED — cannot comment or claim")
        verdict = "FAIL"
    else:
        checks.append("✅ Not locked")

    # Check 3: Already assigned?
    assignee = issue_data.get("assignee")
    if assignee:
        login = assignee.get("login", "unknown")
        checks.append(f"⚠️ Assigned to @{login} — may be claimed")
        if verdict == "PASS":
            verdict = "CAUTION"
    else:
        checks.append("✅ Unassigned")

    # Check 4: Competition level (comments count)
    comments = issue_data.get("comments", 0)
    if comments > 15:
        checks.append(f"❌ HIGH competition ({comments} comments) — likely saturated")
        verdict = "FAIL"
    elif comments > 5:
        checks.append(f"⚠️ Moderate competition ({comments} comments)")
        if verdict == "PASS":
            verdict = "CAUTION"
    else:
        checks.append(f"✅ Low competition ({comments} comments)")

    # Check 5: Existing PRs referencing this issue (search by number mention in body/title)
    existing_prs = []
    success3, pr_output = await _run_gh([
        "search", "prs",
        "--json", "title,url,state,author",
        "--limit", "5",
        f"repo:{repo} is:open {number} in:body,title",
    ])
    if success3 and pr_output:
        try:
            existing_prs = json.loads(pr_output)
        except json.JSONDecodeError:
            pass

    if existing_prs:
        pr_authors = [pr.get("author", {}).get("login", "?") for pr in existing_prs]
        checks.append(f"⚠️ {len(existing_prs)} existing PRs by: {', '.join(pr_authors)}")
        if len(existing_prs) >= 3:
            checks.append("❌ Too many competing PRs — likely won't be reviewed")
            verdict = "FAIL"
        elif verdict == "PASS":
            verdict = "CAUTION"
    else:
        checks.append("✅ No competing PRs found")

    result_output = (
        f"{'🟢' if verdict == 'PASS' else '🟡' if verdict == 'CAUTION' else '🔴'} "
        f"VERDICT: {verdict}\n\n"
        f"Target: {issue_data.get('title', '')}\n"
        f"URL: {issue_url}\n\n"
        "Checks:\n" + "\n".join(f"  {c}" for c in checks)
    )

    if verdict == "FAIL":
        result_output += "\n\n⛔ DO NOT PROCEED. Find another target."

    return tool_result(
        success=True,
        output=result_output,
        data={
            "verdict": verdict,
            "checks": checks,
            "issue_state": state,
            "locked": locked,
            "comments": comments,
            "existing_prs": len(existing_prs),
            "title": issue_data.get("title", ""),
        },
    )


# ==================================================================
# TOOL 3: monitor_pr_status — Post-submission monitoring
# ==================================================================

async def _handle_monitor_pr_status(params: dict[str, Any], **kw: Any) -> str:
    """
    Check PR status: CI, reviews, merge state.
    Returns actionable next steps.
    """
    pr_url = params.get("pr_url", "")
    if not pr_url:
        return tool_error("pr_url is required")

    # Parse owner/repo/number
    parts = pr_url.rstrip("/").split("/")
    try:
        number = parts[-1]
        repo = f"{parts[-4]}/{parts[-3]}"
    except (IndexError, ValueError):
        return tool_error(f"Cannot parse PR URL: {pr_url}")

    # Fetch PR data
    success, raw = await _run_gh(["api", f"repos/{repo}/pulls/{number}"])
    if not success:
        return tool_error(f"Cannot fetch PR: {raw}")

    try:
        pr = json.loads(raw)
    except json.JSONDecodeError:
        return tool_error("Invalid JSON from PR API")

    state = pr.get("state", "unknown")
    merged = pr.get("merged", False)
    mergeable = pr.get("mergeable_state", "unknown")
    draft = pr.get("draft", False)
    title = pr.get("title", "")

    # Fetch reviews
    success2, reviews_raw = await _run_gh(["api", f"repos/{repo}/pulls/{number}/reviews"])
    reviews = []
    if success2:
        try:
            reviews = json.loads(reviews_raw)
        except json.JSONDecodeError:
            pass

    # Fetch CI status
    head_sha = pr.get("head", {}).get("sha", "")
    ci_status = "unknown"
    if head_sha:
        success3, ci_raw = await _run_gh(["api", f"repos/{repo}/commits/{head_sha}/status"])
        if success3:
            try:
                ci_data = json.loads(ci_raw)
                ci_status = ci_data.get("state", "unknown")
            except json.JSONDecodeError:
                pass

    # Fetch review comments (actionable feedback)
    success4, comments_raw = await _run_gh(["api", f"repos/{repo}/pulls/{number}/comments"])
    review_comments = []
    if success4:
        try:
            review_comments = json.loads(comments_raw)
        except json.JSONDecodeError:
            pass

    # Determine action needed
    actions = []
    status_emoji = "🟡"

    if state == "closed" and not merged:
        status_emoji = "🔴"
        actions.append("PR was CLOSED without merge. Bounty lost. Move to next target.")
    elif merged:
        status_emoji = "🟢"
        actions.append("PR MERGED! Update ledger and track payment.")
    elif ci_status == "failure":
        status_emoji = "🔴"
        actions.append("CI FAILING — fix immediately and push.")
    elif reviews:
        changes_requested = [r for r in reviews if r.get("state") == "CHANGES_REQUESTED"]
        approved = [r for r in reviews if r.get("state") == "APPROVED"]
        if changes_requested:
            status_emoji = "🟡"
            actions.append(f"CHANGES REQUESTED by {len(changes_requested)} reviewer(s). Address feedback and push.")
        elif approved:
            status_emoji = "🟢"
            actions.append("APPROVED — waiting for maintainer merge.")
    elif review_comments:
        status_emoji = "🟡"
        actions.append(f"{len(review_comments)} review comments to address.")
    else:
        actions.append("Awaiting review. No action needed yet.")

    output = (
        f"{status_emoji} PR Status: {title}\n"
        f"State: {state} | Merged: {merged} | CI: {ci_status} | Mergeable: {mergeable}\n"
        f"Reviews: {len(reviews)} | Comments: {len(review_comments)}\n\n"
        f"Actions needed:\n" + "\n".join(f"  → {a}" for a in actions)
    )

    # Include review comment bodies for response
    if review_comments:
        output += "\n\nReview comments to address:\n"
        for rc in review_comments[:5]:
            body = (rc.get("body", "") or "")[:200]
            author = rc.get("user", {}).get("login", "?")
            output += f"  @{author}: {body}\n"

    return tool_result(
        success=True,
        output=output,
        data={
            "state": state,
            "merged": merged,
            "ci_status": ci_status,
            "reviews_count": len(reviews),
            "comments_count": len(review_comments),
            "actions": actions,
            "needs_work": bool(ci_status == "failure" or any(r.get("state") == "CHANGES_REQUESTED" for r in reviews) or review_comments),
        },
    )


# ==================================================================
# TOOL 4: respond_pr_review — Address reviewer feedback
# ==================================================================

async def _handle_respond_pr_review(params: dict[str, Any], **kw: Any) -> str:
    """Post a response comment on a PR review or comment."""
    pr_url = params.get("pr_url", "")
    comment_id = params.get("comment_id", "")
    body = params.get("body", "")

    if not pr_url or not body:
        return tool_error("pr_url and body are required")

    parts = pr_url.rstrip("/").split("/")
    try:
        number = parts[-1]
        repo = f"{parts[-4]}/{parts[-3]}"
    except (IndexError, ValueError):
        return tool_error(f"Cannot parse PR URL: {pr_url}")

    if comment_id:
        # Reply to specific review comment
        success, output = await _run_gh([
            "api", f"repos/{repo}/pulls/comments/{comment_id}/replies",
            "--method", "POST",
            "-f", f"body={body}",
        ])
    else:
        # Post general comment on PR
        success, output = await _run_gh([
            "api", f"repos/{repo}/issues/{number}/comments",
            "--method", "POST",
            "-f", f"body={body}",
        ])

    if success:
        return tool_result(
            success=True,
            output=f"✅ Comment posted on PR #{number}",
            data={"pr": number, "repo": repo},
        )
    return tool_error(f"Failed to post comment: {output}")


# ==================================================================
# TOOL 5: earnings_ledger — Persistent pipeline state
# ==================================================================

async def _handle_earnings_ledger(params: dict[str, Any], **kw: Any) -> str:
    """
    Manage bounty pipeline state. Actions:
    - add: Register new bounty target
    - update: Update status of existing bounty
    - list: Show all bounties by status
    - summary: Financial summary
    """
    action = params.get("action", "list")
    ledger = _load_ledger()

    if action == "add":
        entry = {
            "id": f"b_{int(time.time())}",
            "title": params.get("title", ""),
            "url": params.get("url", ""),
            "platform": params.get("platform", ""),
            "reward_usd": params.get("reward_usd", 0),
            "status": "FOUND",
            "pr_url": "",
            "created_at": time.strftime("%Y-%m-%d %H:%M"),
            "updated_at": time.strftime("%Y-%m-%d %H:%M"),
            "notes": params.get("notes", ""),
        }
        ledger["bounties"].append(entry)
        _save_ledger(ledger)
        return tool_result(
            success=True,
            output=f"✅ Added to ledger: {entry['title']} (${entry['reward_usd']})\nID: {entry['id']}",
            data=entry,
        )

    elif action == "update":
        bounty_id = params.get("bounty_id", "")
        new_status = params.get("status", "")
        pr_url = params.get("pr_url", "")
        notes = params.get("notes", "")

        valid_statuses = ["FOUND", "QUALIFIED", "CLAIMED", "EXECUTING", "SUBMITTED", "MONITORING", "MERGED", "REJECTED", "PAID"]

        if new_status and new_status not in valid_statuses:
            return tool_error(f"Invalid status. Valid: {valid_statuses}")

        for b in ledger["bounties"]:
            if b["id"] == bounty_id or b["url"] == bounty_id:
                old_status = b["status"]
                if new_status:
                    b["status"] = new_status
                if pr_url:
                    b["pr_url"] = pr_url
                if notes:
                    b["notes"] = notes
                b["updated_at"] = time.strftime("%Y-%m-%d %H:%M")

                # Update totals (only on first transition to avoid double-counting)
                reward = b.get("reward_usd", 0)
                if new_status == "PAID" and old_status != "PAID":
                    ledger["total_earned"] += reward
                    if old_status == "MERGED":
                        ledger["total_pending"] -= reward
                elif new_status == "MERGED" and old_status != "MERGED":
                    ledger["total_pending"] += reward

                _save_ledger(ledger)
                return tool_result(
                    success=True,
                    output=f"✅ Updated: {b['title']} → {b['status']}",
                    data=b,
                )

        return tool_error(f"Bounty not found: {bounty_id}")

    elif action == "list":
        status_filter = params.get("status", "")
        items = ledger["bounties"]
        if status_filter:
            items = [b for b in items if b["status"] == status_filter]

        if not items:
            return tool_result(success=True, output="No bounties in ledger.", data={"bounties": []})

        output = f"📋 Bounty Pipeline ({len(items)} items):\n\n"
        by_status: dict[str, list] = {}
        for b in items:
            by_status.setdefault(b["status"], []).append(b)

        status_order = ["FOUND", "QUALIFIED", "CLAIMED", "EXECUTING", "SUBMITTED", "MONITORING", "MERGED", "REJECTED", "PAID"]
        for status in status_order:
            group = by_status.get(status, [])
            if group:
                output += f"[{status}] ({len(group)}):\n"
                for b in group:
                    reward_str = f"${b.get('reward_usd', 0)}" if b.get('reward_usd') else "TBD"
                    output += f"  • {b['title']} — {reward_str}\n"
                    if b.get("pr_url"):
                        output += f"    PR: {b['pr_url']}\n"
                output += "\n"

        return tool_result(success=True, output=output, data={"bounties": items})

    elif action == "summary":
        total_earned = ledger.get("total_earned", 0)
        total_pending = ledger.get("total_pending", 0)
        active = [b for b in ledger["bounties"] if b["status"] not in ("PAID", "REJECTED")]
        output = (
            f"💰 Earnings Summary:\n"
            f"  Total Earned: ${total_earned}\n"
            f"  Pending Payment: ${total_pending}\n"
            f"  Active Pipeline: {len(active)} bounties\n"
            f"  Pipeline Value: ${sum(b.get('reward_usd', 0) for b in active)}\n"
        )
        return tool_result(success=True, output=output, data=ledger)

    return tool_error(f"Unknown action: {action}")


# ==================================================================
# TOOL 6: prepare_bounty_submission
# ==================================================================

async def _handle_submit_bounty(params: dict[str, Any], **kw: Any) -> str:
    """Prepare bounty submission with correct wallet and update ledger."""
    platform = params.get("platform", "")
    bounty_url = params.get("bounty_url", "")
    pr_url = params.get("pr_url", "")
    wallet_type = params.get("wallet_type", "ethereum")

    from app.config import get_settings
    settings = get_settings()

    wallet_map = {
        "ethereum": settings.ethereum_address,
        "solana": settings.solana_address,
        "tron": settings.tron_address,
    }
    wallet_address = wallet_map.get(wallet_type, settings.ethereum_address)

    # Auto-update ledger
    ledger = _load_ledger()
    for b in ledger["bounties"]:
        if b["url"] == bounty_url:
            b["status"] = "SUBMITTED"
            b["pr_url"] = pr_url
            b["updated_at"] = time.strftime("%Y-%m-%d %H:%M")
            _save_ledger(ledger)
            break

    output = (
        f"📋 Submission Ready:\n"
        f"  Platform: {platform}\n"
        f"  Bounty: {bounty_url}\n"
        f"  PR: {pr_url}\n"
        f"  Wallet ({wallet_type}): {wallet_address}\n"
        f"  Developer: @{settings.github_username or 'pvbang'}\n"
    )
    return tool_result(success=True, output=output, data={
        "platform": platform, "bounty_url": bounty_url,
        "pr_url": pr_url, "wallet_address": wallet_address,
    })


# ==================================================================
# Registration
# ==================================================================

registry.register(
    name="search_bounties",
    toolset="money",
    schema={
        "description": (
            "Search for paid bounties across 8+ platforms: Superteam Earn, GitHub labels, Algora, "
            "Huntr (AI/ML), Immunefi (Web3 security), CodeHawks (audit contests), "
            "HackenProof (multi-lang), BOSS.dev (low competition OSS). "
            "Returns opportunities sorted by reward. Use as first step in bounty pipeline."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "platform": {
                    "type": "string",
                    "enum": ["all", "superteam", "github", "algora", "huntr", "immunefi", "codehawks", "hackenproof", "boss_dev"],
                    "default": "all",
                },
                "skills": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Skills filter (e.g., ['python', 'typescript'])",
                    "default": ["python", "typescript"],
                },
                "min_reward": {"type": "integer", "default": 0},
                "limit": {"type": "integer", "default": 20},
            },
        },
    },
    handler=_handle_search_bounties,
    is_async=True,
    emoji="💰",
)

registry.register(
    name="verify_bounty_target",
    toolset="money",
    schema={
        "description": (
            "STRICT verification gate — MUST call before working on any bounty. "
            "Checks: issue open, not locked, competition level, existing PRs. "
            "Returns PASS/FAIL/CAUTION verdict. If FAIL → skip target immediately."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "issue_url": {
                    "type": "string",
                    "description": "GitHub issue URL to verify",
                },
            },
            "required": ["issue_url"],
        },
    },
    handler=_handle_verify_bounty_target,
    is_async=True,
    emoji="🔍",
)

registry.register(
    name="monitor_pr_status",
    toolset="money",
    schema={
        "description": (
            "Monitor a submitted PR: check CI status, review state, merge status. "
            "Returns actionable next steps (fix CI, respond to review, etc.). "
            "Call periodically for all SUBMITTED/MONITORING bounties."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "pr_url": {
                    "type": "string",
                    "description": "Pull Request URL to monitor",
                },
            },
            "required": ["pr_url"],
        },
    },
    handler=_handle_monitor_pr_status,
    is_async=True,
    emoji="📊",
)

registry.register(
    name="respond_pr_review",
    toolset="money",
    schema={
        "description": (
            "Post a response on a PR (reply to review comment or general comment). "
            "Use to address reviewer feedback, explain changes, or ask questions."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "pr_url": {"type": "string", "description": "PR URL"},
                "comment_id": {"type": "string", "description": "Specific comment ID to reply to (optional)"},
                "body": {"type": "string", "description": "Response text (markdown)"},
            },
            "required": ["pr_url", "body"],
        },
    },
    handler=_handle_respond_pr_review,
    is_async=True,
    emoji="💬",
)

registry.register(
    name="earnings_ledger",
    toolset="money",
    schema={
        "description": (
            "Persistent bounty pipeline tracker. Actions: "
            "'add' (register target), 'update' (change status/PR), "
            "'list' (show pipeline), 'summary' (financial overview). "
            "Statuses: FOUND→QUALIFIED→CLAIMED→EXECUTING→SUBMITTED→MONITORING→MERGED→PAID"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["add", "update", "list", "summary"],
                    "default": "list",
                },
                "bounty_id": {"type": "string", "description": "Bounty ID or URL (for update)"},
                "title": {"type": "string"},
                "url": {"type": "string"},
                "platform": {"type": "string"},
                "reward_usd": {"type": "integer"},
                "status": {"type": "string"},
                "pr_url": {"type": "string"},
                "notes": {"type": "string"},
            },
        },
    },
    handler=_handle_earnings_ledger,
    is_async=True,
    emoji="📋",
)

registry.register(
    name="prepare_bounty_submission",
    toolset="money",
    schema={
        "description": "Prepare submission with correct wallet. Auto-updates ledger status to SUBMITTED.",
        "parameters": {
            "type": "object",
            "properties": {
                "platform": {"type": "string"},
                "bounty_url": {"type": "string"},
                "pr_url": {"type": "string"},
                "wallet_type": {
                    "type": "string",
                    "enum": ["ethereum", "solana", "tron"],
                    "default": "ethereum",
                },
            },
            "required": ["platform", "bounty_url"],
        },
    },
    handler=_handle_submit_bounty,
    is_async=True,
    emoji="📋",
)
