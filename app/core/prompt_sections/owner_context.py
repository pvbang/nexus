"""
MekongAI Nexus - Owner Context Section.

Two modes:
- minimal (default): short hint telling agent that owner info exists in
  developer_profile.json — agent reads the file only when it actually needs
  wallet/payment/identity details. Saves ~1500 tokens per prompt.
- full: complete section with all data inlined (for the rare case where
  caller explicitly needs everything upfront).
"""

import json
from pathlib import Path
from typing import Optional

from app.config import get_settings

_FULL_CACHE: Optional[str] = None
_MINIMAL_CACHE: Optional[str] = None


def _load_developer_profile() -> dict:
    """Load developer_profile.json from project root."""
    candidates = [
        Path("developer_profile.json"),
        Path(__file__).resolve().parents[3] / "developer_profile.json",
    ]
    for path in candidates:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    return {}


def get_owner_context_section(full: bool = False) -> str:
    """Build owner context section for system prompts.

    Args:
        full: If True, inline all owner data (wallets, platforms, rules).
              If False (default), return a minimal hint (~100 tokens) that
              tells the agent to read developer_profile.json when needed.
    """
    if full:
        return _build_full_section()
    return _build_minimal_section()


def _build_minimal_section() -> str:
    """Lightweight hint — tells agent WHERE to find owner data, not the data itself."""
    global _MINIMAL_CACHE
    if _MINIMAL_CACHE is not None:
        return _MINIMAL_CACHE

    settings = get_settings()
    profile = _load_developer_profile()

    owner_name = profile.get("developer_name", "")
    github_username = getattr(settings, "github_username", "") or profile.get("github_username", "")

    section = "## OWNER CONTEXT\n\n"
    if owner_name:
        section += f"Owner: **{owner_name}**"
        if github_username:
            section += f" (@{github_username})"
        section += "\n\n"

    section += (
        "Full owner profile (wallets, payment platforms, bank, email) is in "
        "`developer_profile.json` at the project root.\n"
        "- Use `read_file(\"developer_profile.json\")` when you need wallet addresses, "
        "platform mappings, or payment details.\n"
        "- NEVER fabricate/guess wallet addresses — always read the file first.\n"
        "- NEVER create a new developer_profile.json with made-up data.\n"
    )

    _MINIMAL_CACHE = section
    return section


def _build_full_section() -> str:
    """Full inline owner data — use sparingly (high token cost)."""
    global _FULL_CACHE
    if _FULL_CACHE is not None:
        return _FULL_CACHE

    settings = get_settings()
    profile = _load_developer_profile()

    owner_name = profile.get("developer_name", "")
    github_username = getattr(settings, "github_username", "") or profile.get("github_username", "")
    github_token_available = bool(getattr(settings, "github_token", ""))
    email = getattr(settings, "personal_email", "") or profile.get("email", "")

    eth_addr = getattr(settings, "ethereum_address", "") or profile.get("wallets", {}).get("ethereum", "")
    sol_addr = getattr(settings, "solana_address", "") or profile.get("wallets", {}).get("solana", "")
    tron_addr = getattr(settings, "tron_address", "") or profile.get("wallets", {}).get("tron", "")

    wallets = profile.get("wallets", {})
    platforms = profile.get("bounty_platforms", {})

    section = "## OWNER CONTEXT & PAYMENT CONFIGURATION\n\n"
    section += (
        "⚠️ **CRITICAL: This section contains the ONLY AUTHORITATIVE owner data.**\n"
        "- DO NOT fabricate/generate/guess wallet addresses — use ONLY what's listed here\n"
        "- If a wallet address is missing below, it means the owner hasn't set it up yet\n"
        "- Using wrong wallet addresses means MONEY GOES TO STRANGERS. This is irreversible.\n\n"
    )

    if owner_name:
        section += f"- **Owner**: {owner_name}\n"
    if github_username:
        section += f"- **GitHub**: @{github_username}\n"
    if email:
        section += f"- **Email**: {email}\n"
    if github_token_available:
        section += "- **GitHub Auth**: ✅ Authenticated\n"

    section += "\n### Wallet Addresses\n"
    if eth_addr:
        section += f"- **Ethereum/EVM**: `{eth_addr}`\n"
    if sol_addr:
        section += f"- **Solana**: `{sol_addr}`\n"
    if tron_addr:
        section += f"- **Tron**: `{tron_addr}`\n"

    bank_name = getattr(settings, "payment_bank_name", "")
    bank_account = getattr(settings, "payment_bank_account_number", "")
    bank_holder = getattr(settings, "payment_bank_account_holder", "")
    if bank_name and bank_account:
        section += (
            f"\n### Bank Transfer (VND)\n"
            f"- **Bank**: {bank_name}\n"
            f"- **Account**: {bank_account}\n"
            f"- **Holder**: {bank_holder}\n"
        )

    if platforms:
        section += "\n### Platform → Wallet Mapping\n"
        for platform, info in platforms.items():
            wallet_field = info.get("wallet_field", "ethereum")
            addr = wallets.get(wallet_field, eth_addr)
            section += f"- **{platform}**: `{addr}`\n"

    reg_email = getattr(settings, "email_username", "") or "phanvanbang.dev@gmail.com"
    section += (
        f"\n### Registration Email: `{reg_email}`\n"
        "Use `fetch_emails` tool for OTP/verification codes.\n"
    )

    _FULL_CACHE = section
    return section
