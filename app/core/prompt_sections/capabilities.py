"""
Nexus - Capabilities Section.

High-level capabilities summary. Details live in tool descriptions.
"""


def get_capabilities_section(agent_type: str = "core") -> str:
    """
    Get capabilities section.

    Args:
        agent_type: Agent role key.

    Returns:
        Capabilities section string, or empty string for judge.
    """
    if agent_type == "judge":
        return ""

    return _CAPABILITIES


_CAPABILITIES = """## CAPABILITIES

You have access to real tools that execute in the real world:
- **Web & Research**: Search engines, URL fetching, browser automation, HTTP requests.
- **Code & Files**: Execute Python/shell, read/write/edit files, navigate codebases.
- **Task Memory**: Persistent task documents (`task_doc_*`) for saving long outputs and reading prior context.
- **Collaboration**: Delegate to specialists, activate multi-agent swarms, initiate debates.
- **Output**: Generate reports, charts, documents, translations, notifications.

OUTPUT DISCIPLINE: If output exceeds 2000 chars, write to a task document and return a short summary.
CODE SANDBOX: `execute_code` has no internet; use `http_request` for API calls. Pre-installed: fpdf2, matplotlib, pandas, numpy, pillow, openpyxl."""
