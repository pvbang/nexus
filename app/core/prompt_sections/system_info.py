"""
MekongAI Nexus - System Info Section.

Provides current date/time and environment context for agents.
"""

import datetime
import os
import platform


def _get_external_hostname() -> str:
    """Resolve external hostname/IP for this server.

    Used to inform agents what URL to provide users.
    """
    from app.config import get_settings
    settings = get_settings()

    if settings.external_base_url:
        return settings.external_base_url.rstrip("/")

    # Auto-detect from DevServerManager utility
    try:
        from app.project_builder.dev_server import DevServerManager
        ip = DevServerManager._detect_external_ip()
        if ip and ip != "localhost":
            return f"http://{ip}"
    except ImportError:
        pass

    return "localhost (external URL not configured)"


# FIX: GAP-11 - Add shell and command guidance
_shell = os.environ.get("SHELL", "")
_is_windows = platform.system() == "Windows"

SHELL_INFO = f"""- Default Shell: {_shell or ('cmd.exe' if _is_windows else '/bin/bash')}
- Command Chaining: {'Use && for conditional execution' if not _is_windows else 'Use && in cmd.exe, ; in PowerShell'}
"""

if _is_windows:
    SHELL_INFO += """- IMPORTANT: On Windows, avoid Unix-specific utils (sed, grep, awk, cat, rm). Use: type (for cat), del (for rm), copy (for cp), findstr (for grep).
"""


def get_system_info_section() -> str:
    """
    Build system context with current date/time and environment.

    Uses explicit human-readable format so agents can use it in
    search queries to find the most current information.

    Returns:
        System info section string.
    """
    tz_vn = datetime.timezone(datetime.timedelta(hours=7))
    now = datetime.datetime.now(tz_vn)
    external_host = _get_external_hostname()
    return (
        f"## CURRENT DATE & TIME\n"
        f"- **Today**: {now.strftime('%A, %B %d, %Y')} ({now.strftime('%Y-%m-%d')})\n"
        f"- **Time**: {now.strftime('%H:%M:%S')} (UTC+7, Vietnam/Bangkok)\n"
        f"- **Year**: {now.year}\n"
        f"- **ISO**: {now.isoformat()}\n"
        f"- Working Directory: {os.getcwd()}\n"
        f"- OS: {platform.system()} ({os.name})\n"
        f"{SHELL_INFO}"
        f"- External hostname: {external_host}\n\n"
        f"## EXTERNAL ACCESS RULE\n"
        f"When providing server URLs to users, ALWAYS use the external hostname "
        f"'{external_host}' instead of 'localhost'. Users CANNOT access localhost "
        f"from their browser — they need the external IP/domain.\n\n"
        f"IMPORTANT: When searching for information, use this date as reference. "
        f"Always search for the LATEST data available as of {now.strftime('%B %Y')}. "
        f"When the user says 'now', 'current', 'latest', they mean as of {now.strftime('%Y-%m-%d')}."
    )
