"""
MekongAI Nexus - Security CLI Commands.

Provides CLI subcommands for security operations:
- nexus security audit [--deep]
- nexus security fix
- nexus policy list
- nexus policy add <tool> <action> [reason]
- nexus policy remove <tool>
- nexus memory show [agent_id]
- nexus memory search <query> [agent_id]
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any


def cmd_security(args: Any) -> int:
    """Handle 'security' subcommand."""
    action = getattr(args, "security_action", None)
    if not action:
        print("Usage: nexus security {audit|fix}")
        print("  audit [--deep]  Run security audit")
        print("  fix             Auto-fix fixable findings")
        return 1

    if action == "audit":
        return _run_security_audit(args)
    elif action == "fix":
        return _run_security_fix(args)
    else:
        print(f"Unknown security action: {action}")
        return 1


def cmd_policy(args: Any) -> int:
    """Handle 'policy' subcommand."""
    action = getattr(args, "policy_action", None)
    if not action:
        print("Usage: nexus policy {list|add|remove}")
        print("  list              Show all policy rules")
        print("  add <tool> <action> [reason]  Add a rule")
        print("  remove <tool>     Remove a rule")
        return 1

    if action == "list":
        return _run_policy_list()
    elif action == "add":
        return _run_policy_add(args)
    elif action == "remove":
        return _run_policy_remove(args)
    else:
        print(f"Unknown policy action: {action}")
        return 1


def cmd_memory(args: Any) -> int:
    """Handle 'memory' subcommand for active memory management."""
    action = getattr(args, "memory_action", None)
    if not action:
        print("Usage: nexus memory {show|search|stats|clear}")
        print("  show [agent_id]          Show agent's memory")
        print("  search <query> [agent_id] Search memories")
        print("  stats [agent_id]         Memory statistics")
        print("  clear <agent_id>         Clear agent memory")
        return 1

    if action == "show":
        return _run_memory_show(args)
    elif action == "search":
        return _run_memory_search(args)
    elif action == "stats":
        return _run_memory_stats(args)
    elif action == "clear":
        return _run_memory_clear(args)
    else:
        print(f"Unknown memory action: {action}")
        return 1


# ─────────────────────────────────────────────────────────────────
# Security commands implementation
# ─────────────────────────────────────────────────────────────────


def _run_security_audit(args: Any) -> int:
    """Run security audit."""
    deep = getattr(args, "deep", False)

    print(f"🔍 Running security audit {'(deep mode)' if deep else ''}...")
    print()

    async def _run():
        from app.security.audit import get_security_audit_runner
        runner = get_security_audit_runner()
        report = await runner.run_audit(deep=deep)
        return report

    try:
        report = asyncio.run(_run())
    except Exception as e:
        print(f"❌ Audit failed: {e}", file=sys.stderr)
        return 1

    # Print results
    print(report.format_summary())
    print()

    if not report.passed:
        print("⚠️  Security audit FAILED. Fix critical/high findings.")
        return 2
    else:
        print("✅ Security audit passed.")
        return 0


def _run_security_fix(args: Any) -> int:
    """Run auto-fix for fixable findings."""
    print("🔧 Running security fix...")

    async def _run():
        from app.security.audit import get_security_audit_runner
        runner = get_security_audit_runner()
        # Run audit first if no previous report
        if runner.last_report is None:
            await runner.run_audit(deep=False)
        return await runner.run_fix()

    try:
        result = asyncio.run(_run())
    except Exception as e:
        print(f"❌ Fix failed: {e}", file=sys.stderr)
        return 1

    fixed = result.get("fixed", 0)
    failed = result.get("failed", 0)

    if fixed > 0:
        print(f"✅ Fixed {fixed} issue(s)")
    if failed > 0:
        print(f"⚠️  {failed} issue(s) could not be auto-fixed")
    if fixed == 0 and failed == 0:
        print("ℹ️  No auto-fixable issues found")

    return 0


# ─────────────────────────────────────────────────────────────────
# Policy commands implementation
# ─────────────────────────────────────────────────────────────────


def _run_policy_list() -> int:
    """List all tool policy rules."""
    from app.core.tool_policy import get_tool_policy_engine

    engine = get_tool_policy_engine()
    rules = engine.list_rules()

    if not rules:
        print("No policy rules configured.")
        return 0

    print(f"{'Pattern':<20} {'Action':<10} {'Priority':<8} {'Conditions':<25} {'Reason'}")
    print("─" * 90)
    for rule in rules:
        conditions = ", ".join(rule["conditions"]) if rule["conditions"] else "-"
        enabled = "" if rule["enabled"] else " (disabled)"
        print(
            f"{rule['pattern']:<20} {rule['action']:<10} {rule['priority']:<8} "
            f"{conditions:<25} {rule['reason'][:30]}{enabled}"
        )

    print(f"\nTotal: {len(rules)} rules")
    stats = engine.stats
    print(f"Stats: {stats['total_evaluations']} evaluations | "
          f"{stats['allowed']} allowed | {stats['denied']} denied | {stats['asked']} asked")
    return 0


def _run_policy_add(args: Any) -> int:
    """Add a tool policy rule."""
    from app.core.tool_policy import ToolPolicyRule, ToolAction, get_tool_policy_engine

    tool = getattr(args, "tool_pattern", None)
    action = getattr(args, "action", None)
    reason = getattr(args, "reason", "Added via CLI")

    if not tool or not action:
        print("Usage: nexus policy add <tool_pattern> <action> [reason]")
        print(f"  Actions: {', '.join(a.value for a in ToolAction)}")
        return 1

    try:
        tool_action = ToolAction(action.lower())
    except ValueError:
        print(f"Invalid action: {action}")
        print(f"  Valid: {', '.join(a.value for a in ToolAction)}")
        return 1

    engine = get_tool_policy_engine()
    rule = ToolPolicyRule(
        tool_pattern=tool,
        action=tool_action,
        reason=reason,
        priority=50,
        created_by="cli",
    )
    engine.add_rule(rule)

    print(f"✅ Added rule: {tool} → {action} (reason: {reason})")
    return 0


def _run_policy_remove(args: Any) -> int:
    """Remove a tool policy rule."""
    from app.core.tool_policy import get_tool_policy_engine

    tool = getattr(args, "tool_pattern", None)
    if not tool:
        print("Usage: nexus policy remove <tool_pattern>")
        return 1

    engine = get_tool_policy_engine()
    removed = engine.remove_rule(tool)

    if removed:
        print(f"✅ Removed rule for pattern: {tool}")
    else:
        print(f"⚠️  No rule found for pattern: {tool}")
    return 0


# ─────────────────────────────────────────────────────────────────
# Memory commands implementation
# ─────────────────────────────────────────────────────────────────


def _run_memory_show(args: Any) -> int:
    """Show agent memory contents."""
    agent_id = getattr(args, "agent_id", "default") or "default"

    async def _run():
        from app.memory.active_memory import get_active_memory
        mgr = get_active_memory(agent_id)
        return await mgr.get_all()

    try:
        content = asyncio.run(_run())
    except Exception as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        return 1

    if not content:
        print(f"ℹ️  No memory file for agent '{agent_id}'")
        return 0

    print(content)
    return 0


def _run_memory_search(args: Any) -> int:
    """Search agent memories."""
    query = getattr(args, "query", None)
    agent_id = getattr(args, "agent_id", "default") or "default"

    if not query:
        print("Usage: nexus memory search <query> [agent_id]")
        return 1

    async def _run():
        from app.memory.active_memory import get_active_memory
        mgr = get_active_memory(agent_id)
        return await mgr.search(query, limit=20)

    try:
        entries = asyncio.run(_run())
    except Exception as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        return 1

    if not entries:
        print(f"No matches for '{query}' in agent '{agent_id}'")
        return 0

    print(f"Found {len(entries)} matches:")
    for entry in entries:
        print(f"  [{entry.category.value}] {entry.content[:80]}")
    return 0


def _run_memory_stats(args: Any) -> int:
    """Show memory statistics."""
    agent_id = getattr(args, "agent_id", None)

    async def _run():
        if agent_id:
            from app.memory.active_memory import get_active_memory
            mgr = get_active_memory(agent_id)
            return [await mgr.get_stats()]
        else:
            from app.memory.active_memory import list_memory_agents, get_active_memory
            agents = list_memory_agents()
            stats = []
            for aid in agents:
                mgr = get_active_memory(aid)
                stats.append(await mgr.get_stats())
            return stats

    try:
        all_stats = asyncio.run(_run())
    except Exception as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        return 1

    if not all_stats:
        print("ℹ️  No memory agents found")
        return 0

    for stats in all_stats:
        print(f"\nAgent: {stats['agent_id']}")
        print(f"  Entries: {stats['total_entries']}")
        print(f"  File size: {stats['file_size_bytes']} bytes")
        if stats.get("category_counts"):
            for cat, count in stats["category_counts"].items():
                print(f"    {cat}: {count}")
    return 0


def _run_memory_clear(args: Any) -> int:
    """Clear agent memory."""
    agent_id = getattr(args, "agent_id", None)
    if not agent_id:
        print("Usage: nexus memory clear <agent_id>")
        return 1

    confirm = input(f"⚠️  Clear all memory for agent '{agent_id}'? [y/N]: ")
    if confirm.lower() != "y":
        print("Cancelled.")
        return 0

    async def _run():
        from app.memory.active_memory import get_active_memory
        mgr = get_active_memory(agent_id)
        await mgr.clear()

    try:
        asyncio.run(_run())
    except Exception as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        return 1

    print(f"✅ Memory cleared for agent '{agent_id}'")
    return 0
