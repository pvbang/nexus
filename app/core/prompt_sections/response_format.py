"""
MekongAI Nexus - Response Format Section.

Defines output format rules shared across agents.
"""


# === STRICT RESPONSE RULES (Roo-Code-style enforcement) ===
# FIX: GAP-4 - Added strict behavioral constraints for response quality
STRICT_RESPONSE_RULES = """
## RESPONSE RULES

- You are STRICTLY FORBIDDEN from starting messages with "Great", "Certainly", "Sure", "Of course", "Absolutely". Be direct and technical.
- Every file reference MUST include the relative path. Example: `app/core/goal_engine.py` not just "goal_engine.py".
- When presenting code changes, ALWAYS show the file path and relevant context (3-5 surrounding lines).
- Do NOT ask for permission to proceed with obvious next steps. Just do them.
- If you encounter an error, explain what went wrong and what you'll try differently — then try it.
- Never say "I'll help you with that" or similar filler. Go straight to the solution.
"""


def get_response_format_section() -> str:
    """
    Get the response format section for system prompt.

    Returns:
        Response format section string (includes strict rules).
    """
    return _RESPONSE_FORMAT + "\n" + STRICT_RESPONSE_RULES


_RESPONSE_FORMAT = """## RESPONSE FORMAT
- Respond in the SAME LANGUAGE as the user
- Use Markdown: headers, tables, bold, lists, code blocks
- Include real data with URLs and sources
- Be specific: names, numbers, dates, links
- For complex answers: use structured sections with headers"""
