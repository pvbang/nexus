"""
MekongAI Nexus - GitHub Automation Tools.

Enables agents to interact with GitHub: fork repos, create PRs,
browse issues/bounties, and manage repositories autonomously.
Uses GitHub Personal Access Token from settings.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import structlog

from app.config import get_settings
from app.mcp.tool_registry import registry, tool_result, tool_error
from app.mcp.tools.filesystem import WORKSPACE_DIR

logger = structlog.get_logger(__name__)


def _github_available() -> bool:
    """Check if GitHub token is configured."""
    settings = get_settings()
    return bool(settings.github_token)


def _get_auth_env() -> dict[str, str]:
    """Get environment with GitHub token for authenticated requests."""
    settings = get_settings()
    env = dict(__import__("os").environ)
    if settings.github_token:
        env["GH_TOKEN"] = settings.github_token
        env["GITHUB_TOKEN"] = settings.github_token
    return env


async def _run_gh(args: list[str], cwd: str | None = None, timeout: int = 60) -> tuple[bool, str]:
    """Run a gh CLI command and return (success, output)."""
    import shutil
    gh_path = shutil.which("gh")
    if not gh_path:
        return False, "gh CLI not found. Install: winget install GitHub.cli"

    cmd = [gh_path] + args
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd or str(WORKSPACE_DIR),
            env=_get_auth_env(),
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        output = stdout.decode("utf-8", errors="replace")
        err = stderr.decode("utf-8", errors="replace")
        combined = (output + "\n" + err).strip() if err and proc.returncode != 0 else output.strip()
        return proc.returncode == 0, combined
    except asyncio.TimeoutError:
        return False, f"gh command timed out after {timeout}s"
    except Exception as e:
        return False, f"gh error: {e}"


# ------------------------------------------------------------------
# Handlers
# ------------------------------------------------------------------


async def _handle_github_fork(params: dict[str, Any], **kw: Any) -> str:
    """Fork a repository to owner's account."""
    repo = params.get("repo", "")
    clone = params.get("clone", True)

    if not repo:
        return tool_error("repo is required (format: owner/repo)")

    args = ["repo", "fork", repo, "--clone=" + str(clone).lower()]
    success, output = await _run_gh(args, timeout=120)

    if success:
        return tool_result(
            success=True,
            output=f"🍴 Forked {repo}:\n{output}",
            data={"repo": repo, "cloned": clone},
        )
    return tool_error(f"Fork failed: {output}")


async def _handle_github_clone(params: dict[str, Any], **kw: Any) -> str:
    """Clone a repository."""
    repo = params.get("repo", "")
    directory = params.get("directory", "")

    if not repo:
        return tool_error("repo is required (format: owner/repo or URL)")

    args = ["repo", "clone", repo]
    if directory:
        args.append(directory)

    success, output = await _run_gh(args, timeout=120)
    if success:
        return tool_result(
            success=True,
            output=f"📦 Cloned {repo}:\n{output}",
            data={"repo": repo},
        )
    return tool_error(f"Clone failed: {output}")


async def _handle_github_create_pr(params: dict[str, Any], **kw: Any) -> str:
    """Create a Pull Request."""
    title = params.get("title", "")
    body = params.get("body", "")
    base = params.get("base", "main")
    head = params.get("head", "")
    repo_path = params.get("repo_path", "")

    if not title:
        return tool_error("title is required")

    args = ["pr", "create", "--title", title, "--body", body, "--base", base]
    if head:
        args.extend(["--head", head])

    cwd = str(WORKSPACE_DIR / repo_path) if repo_path else None
    success, output = await _run_gh(args, cwd=cwd, timeout=60)

    if success:
        return tool_result(
            success=True,
            output=f"🔗 PR created:\n{output}",
            data={"title": title, "url": output.strip()},
        )
    return tool_error(f"PR creation failed: {output}")


async def _handle_github_search_issues(params: dict[str, Any], **kw: Any) -> str:
    """Search GitHub issues (bounties, bugs, feature requests)."""
    query = params.get("query", "")
    label = params.get("label", "")
    limit = params.get("limit", 20)
    sort = params.get("sort", "created")

    if not query and not label:
        return tool_error("query or label is required")

    search_query = query
    if label:
        search_query += f" label:{label}"
    search_query += " state:open"

    args = [
        "search", "issues",
        "--json", "title,url,labels,repository,createdAt,body",
        "--limit", str(limit),
        "--sort", sort,
        search_query,
    ]

    success, output = await _run_gh(args, timeout=30)
    if success:
        try:
            issues = json.loads(output) if output else []
            summary = f"Found {len(issues)} issues:\n\n"
            for i, issue in enumerate(issues[:limit], 1):
                labels = ", ".join(l.get("name", "") for l in issue.get("labels", []))
                repo_info = issue.get("repository", {})
                repo_name = repo_info.get("nameWithOwner", "") if isinstance(repo_info, dict) else ""
                summary += (
                    f"{i}. [{repo_name}] {issue.get('title', '')}\n"
                    f"   Labels: {labels}\n"
                    f"   URL: {issue.get('url', '')}\n\n"
                )
            return tool_result(success=True, output=summary, data={"count": len(issues), "issues": issues})
        except json.JSONDecodeError:
            return tool_result(success=True, output=output)
    return tool_error(f"Search failed: {output}")


async def _handle_github_browse_bounties(params: dict[str, Any], **kw: Any) -> str:
    """Search specifically for bounty/paid issues on GitHub."""
    language = params.get("language", "python")
    min_amount = params.get("min_amount", 0)
    limit = params.get("limit", 30)

    bounty_queries = [
        f"label:bounty language:{language} state:open",
        f"label:paid language:{language} state:open",
        f"label:reward language:{language} state:open",
        f"\"bounty\" \"$\" language:{language} state:open",
    ]

    all_issues = []
    for q in bounty_queries:
        args = [
            "search", "issues",
            "--json", "title,url,labels,repository,createdAt,body",
            "--limit", str(limit // len(bounty_queries)),
            q,
        ]
        success, output = await _run_gh(args, timeout=30)
        if success and output:
            try:
                issues = json.loads(output)
                all_issues.extend(issues)
            except json.JSONDecodeError:
                pass

    # Deduplicate by URL
    seen = set()
    unique = []
    for issue in all_issues:
        url = issue.get("url", "")
        if url not in seen:
            seen.add(url)
            unique.append(issue)

    summary = f"🎯 Found {len(unique)} bounty/paid issues ({language}):\n\n"
    for i, issue in enumerate(unique[:limit], 1):
        labels = ", ".join(l.get("name", "") for l in issue.get("labels", []))
        repo_info = issue.get("repository", {})
        repo_name = repo_info.get("nameWithOwner", "") if isinstance(repo_info, dict) else ""
        body_preview = (issue.get("body", "") or "")[:200]
        summary += (
            f"{i}. [{repo_name}] {issue.get('title', '')}\n"
            f"   Labels: {labels}\n"
            f"   URL: {issue.get('url', '')}\n"
            f"   Preview: {body_preview}...\n\n"
        )

    return tool_result(success=True, output=summary, data={"count": len(unique), "issues": unique})


async def _handle_github_api(params: dict[str, Any], **kw: Any) -> str:
    """Make arbitrary GitHub API calls via gh api."""
    endpoint = params.get("endpoint", "")
    method = params.get("method", "GET")

    if not endpoint:
        return tool_error("endpoint is required (e.g., repos/owner/repo/issues)")

    # Strip leading slash to avoid Git Bash path rewriting on Windows
    endpoint = endpoint.lstrip("/")
    args = ["api", endpoint, "--method", method]

    # Add fields if provided
    fields = params.get("fields", {})
    if isinstance(fields, dict):
        for key, value in fields.items():
            args.extend(["-f", f"{key}={value}"])

    success, output = await _run_gh(args, timeout=30)
    if success:
        if len(output) > 15000:
            output = output[:15000] + "\n... (truncated)"
        return tool_result(success=True, output=output)
    return tool_error(f"API call failed: {output}")


# ------------------------------------------------------------------
# Registration
# ------------------------------------------------------------------

registry.register(
    name="github_fork",
    toolset="github",
    schema={
        "description": (
            "Fork a GitHub repository to owner's account. "
            "Optionally clones it locally for development."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "description": "Repository to fork (format: owner/repo)",
                },
                "clone": {
                    "type": "boolean",
                    "description": "Clone locally after forking (default: true)",
                    "default": True,
                },
            },
            "required": ["repo"],
        },
    },
    handler=_handle_github_fork,
    is_async=True,
    check_fn=_github_available,
    emoji="🍴",
)

registry.register(
    name="github_clone",
    toolset="github",
    schema={
        "description": "Clone a GitHub repository to workspace.",
        "parameters": {
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "description": "Repository (format: owner/repo or full URL)",
                },
                "directory": {
                    "type": "string",
                    "description": "Target directory name (optional)",
                },
            },
            "required": ["repo"],
        },
    },
    handler=_handle_github_clone,
    is_async=True,
    check_fn=_github_available,
    emoji="📦",
)

registry.register(
    name="github_create_pr",
    toolset="github",
    schema={
        "description": (
            "Create a Pull Request on GitHub. Use after committing and pushing changes."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "PR title",
                },
                "body": {
                    "type": "string",
                    "description": "PR description/body (markdown)",
                },
                "base": {
                    "type": "string",
                    "description": "Base branch (default: main)",
                    "default": "main",
                },
                "head": {
                    "type": "string",
                    "description": "Head branch (default: current branch)",
                },
                "repo_path": {
                    "type": "string",
                    "description": "Repo directory relative to workspace",
                },
            },
            "required": ["title"],
        },
    },
    handler=_handle_github_create_pr,
    is_async=True,
    check_fn=_github_available,
    emoji="🔗",
)

registry.register(
    name="github_search_issues",
    toolset="github",
    schema={
        "description": (
            "Search GitHub issues across all repos. "
            "Use to find bounties, bugs, feature requests, and paid tasks."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query (GitHub search syntax)",
                },
                "label": {
                    "type": "string",
                    "description": "Filter by label (e.g., 'bounty', 'good first issue')",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results (default: 20)",
                    "default": 20,
                },
                "sort": {
                    "type": "string",
                    "enum": ["created", "updated", "comments"],
                    "description": "Sort order",
                    "default": "created",
                },
            },
        },
    },
    handler=_handle_github_search_issues,
    is_async=True,
    check_fn=_github_available,
    emoji="🔍",
)

registry.register(
    name="github_browse_bounties",
    toolset="github",
    schema={
        "description": (
            "Search for bounty/paid issues on GitHub. "
            "Finds issues labeled 'bounty', 'paid', 'reward' in specified language."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "language": {
                    "type": "string",
                    "description": "Programming language filter (default: python)",
                    "default": "python",
                },
                "min_amount": {
                    "type": "integer",
                    "description": "Minimum bounty amount in USD (best-effort filter)",
                    "default": 0,
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results (default: 30)",
                    "default": 30,
                },
            },
        },
    },
    handler=_handle_github_browse_bounties,
    is_async=True,
    check_fn=_github_available,
    emoji="🎯",
)

registry.register(
    name="github_api",
    toolset="github",
    schema={
        "description": (
            "Make arbitrary GitHub REST API calls. "
            "Use for any GitHub operation not covered by other tools."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "endpoint": {
                    "type": "string",
                    "description": "API endpoint (e.g., /repos/owner/repo/issues, /user)",
                },
                "method": {
                    "type": "string",
                    "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"],
                    "description": "HTTP method (default: GET)",
                    "default": "GET",
                },
                "fields": {
                    "type": "object",
                    "description": "Request body fields (key-value pairs)",
                },
            },
            "required": ["endpoint"],
        },
    },
    handler=_handle_github_api,
    is_async=True,
    check_fn=_github_available,
    emoji="🐙",
)
