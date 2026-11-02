"""
Nexus - Ledger Verifier.

FIX GAP-4: Spot-check ledger claims against actual workspace artifacts.

Problem: Orchestrator evaluates progress based on ledger summaries that agents
write about themselves. Agents may hallucinate progress ("I found 5 endpoints")
when they actually found nothing, or claim file creation that never happened.

Solution: After each turn evaluation, randomly spot-check 1-3 ledger claims
against verifiable evidence:
- File existence claims → check workspace
- "Found N items" claims → verify count in vault/activity
- URL/endpoint claims → check they appear in tool outputs
- Code creation claims → verify file exists and has content

Verification score feeds into orchestrator's progress assessment.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

_WORKSPACE_BASE = Path(os.environ.get("WORKSPACE_DIR", "workspace_data"))


# A self-attested verdict is the agent asserting its OWN work passed verification
# ("energy closure 0.00%", "FoS 156 PASS", "converged ier=1", "đã xác minh 20 kWe").
# These are exactly the claims a hallucinating/circular agent emits to look done —
# and the ledger verifier has no filesystem fact to check them against. The old code
# never even EXTRACTED them, so they silently inflated trust (default 0.8). We now
# surface them as an explicit skepticism signal: counted as unverifiable (never as
# verified), so trust reflects "this turn produced verdicts nobody independently
# re-ran". Routing them to a critic that re-executes the numbers is ROOT-A/C's job.
_SELF_ATTEST_PATTERNS = (
    r'\b(?:verified|confirmed|validated)\b',
    r'đã\s+xác\s+minh',
    r'\bPASS(?:ED|ED)?\b',
    r'\bconverged\b|hội\s+tụ',
    r'\bclosure\b[^.\n]{0,30}?\d',          # "closure 0.00%"
    r'(?:cân\s+bằng|đóng)[^.\n]{0,30}?\d\s*%',
    r'\bFoS\b|factor\s+of\s+safety|biên\s+an\s+toàn',
    r'\b\d+(?:\.\d+)?\s*kWe\b',             # headline power claim
)


def extract_self_attested_claims(output: str, limit: int = 3) -> list[str]:
    """Pull self-attested verification verdicts from an agent's output.

    Pure function (no I/O) so it can be unit-tested directly, matching the
    module-level-helper convention used elsewhere in orchestration. Returns the
    trimmed sentence/snippet around each verdict, de-duplicated, capped at ``limit``
    so a handful of real filesystem checks are never drowned out.
    """
    if not output:
        return []
    found: list[str] = []
    seen: set[str] = set()
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        for pat in _SELF_ATTEST_PATTERNS:
            if re.search(pat, stripped, re.IGNORECASE):
                key = stripped[:120].lower()
                if key not in seen:
                    seen.add(key)
                    found.append(stripped[:200])
                break
        if len(found) >= limit:
            break
    return found


def verification_blocks_completion(claims_failed: int) -> bool:
    """Whether this turn's verification should block a 'complete' decision.

    The task brief mandates that every 'done' claim be verified. Spot-checking is
    advisory by default; this gate makes a high-confidence FAILED claim (a 'done'
    assertion contradicted by the workspace) block completion so the next turn
    must reconcile it. A failed claim means we caught a likely hallucination.
    """
    return claims_failed > 0


def completion_needs_independent_review(
    is_complete: bool,
    self_attested_recent: int,
    independent_review_recent: bool,
) -> bool:
    """Whether a 'complete' must wait for one independent-review turn.

    A completion that rests on self-attested verdicts ("PASS", "đã xác minh",
    "closure 0.00%") which NO different agent ever re-executed is exactly the
    verification-theater pattern (task d8b78042 / 33ddfc0c): the agent grading its
    own work, then the task closing on it. Block one turn so a critic independently
    re-derives and tries to REFUTE the numbers first.

    Returns True only when ALL hold:
      - the LLM wants to complete,
      - this/recent turns carried at least one self-attested verdict, AND
      - no independent review (critic/QA re-run) has happened recently.
    """
    if not is_complete:
        return False
    if self_attested_recent <= 0:
        return False
    return not independent_review_recent


@dataclass
class VerificationResult:
    """Result of verifying a single claim."""

    claim: str
    claim_type: str  # "file_exists", "count", "data_found", "action_taken"
    verified: bool
    evidence: str = ""
    confidence: float = 1.0  # How confident we are in the verification


@dataclass
class TurnVerification:
    """Aggregate verification for one turn's claims."""

    turn: int
    claims_checked: int = 0
    claims_verified: int = 0
    claims_failed: int = 0
    claims_unverifiable: int = 0
    # Self-attested verdicts ("PASS", "closure 0.00%", "đã xác minh 20 kWe") that no
    # filesystem fact can corroborate. Tracked separately so we can both discount
    # trust AND tell the orchestrator exactly what still needs independent re-run.
    claims_self_attested: int = 0
    self_attested: list[str] = field(default_factory=list)
    results: list[VerificationResult] = field(default_factory=list)

    @property
    def verification_score(self) -> float:
        """0.0 (all claims false) to 1.0 (all verified). 0.5 if nothing checkable.

        Self-attested verdicts are NOT counted as verified: a turn whose only
        "evidence" is the agent declaring its own success scores low, not high —
        the opposite of the old behaviour where unrecognised claims auto-passed.
        """
        if self.claims_checked == 0:
            return 0.5
        return self.claims_verified / self.claims_checked


class LedgerVerifier:
    """Spot-checks ledger/agent claims against workspace reality.

    Runs after each turn to catch hallucinated progress early.
    Lightweight — only checks verifiable claims (file existence, data presence).
    Does NOT use LLM calls — pure filesystem/data verification.
    """

    def __init__(self, task_id: str) -> None:
        from app.core import task_paths
        self._task_id = task_id
        self._workspace = task_paths.work_dir(task_id)
        self._context_dir = task_paths.context_dir(task_id)
        self._history: list[TurnVerification] = []

    def verify_turn_claims(
        self,
        turn: int,
        agent_outputs: list[dict[str, Any]],
        ledger_entries: list[dict[str, Any]] | None = None,
    ) -> TurnVerification:
        """Verify claims from a turn's agent outputs and ledger entries.

        Args:
            turn: Turn number
            agent_outputs: List of {agent_role, output, tool_calls} dicts
            ledger_entries: Optional ledger entries for this turn

        Returns:
            TurnVerification with spot-check results
        """
        verification = TurnVerification(turn=turn)
        claims = self._extract_claims(agent_outputs, ledger_entries)

        # Spot-check up to 5 claims per turn (keep it fast)
        for claim in claims[:5]:
            result = self._verify_claim(claim)
            verification.results.append(result)
            verification.claims_checked += 1
            if result.verified:
                verification.claims_verified += 1
            elif result.confidence >= 0.7:
                verification.claims_failed += 1
            else:
                verification.claims_unverifiable += 1

        # Surface self-attested verdicts that have NO filesystem corroboration.
        # These don't fail the turn (we can't disprove them here), but they must
        # not pass silently either — they're flagged so the orchestrator knows the
        # turn rests on claims nobody independently re-ran.
        for output_info in agent_outputs:
            for snippet in extract_self_attested_claims(output_info.get("output", "")):
                verification.self_attested.append(snippet)
                verification.claims_self_attested += 1
            # An audit_log transaction that recorded a material finding with NO
            # evidence (A6) is self-attestation too: the audit tool flagged it
            # UNSUBSTANTIATED in its own output. Route that into the same channel so
            # the A3 completion gate sees it — not just the agent that called the tool.
            for tc in output_info.get("tool_calls", []):
                if tc.get("tool_name") == "audit_log" and "unsubstantiated" in str(
                    tc.get("output", "")
                ).lower():
                    verification.self_attested.append(
                        f"UNSUBSTANTIATED audit finding (no evidence): {str(tc.get('output', ''))[:160]}"
                    )
                    verification.claims_self_attested += 1

        self._history.append(verification)
        return verification

    def _extract_claims(
        self,
        agent_outputs: list[dict[str, Any]],
        ledger_entries: list[dict[str, Any]] | None,
    ) -> list[dict[str, str]]:
        """Extract verifiable claims from outputs and ledger."""
        claims: list[dict[str, str]] = []

        for output_info in agent_outputs:
            output = output_info.get("output", "")
            if not output:
                continue

            # File creation/modification claims
            file_patterns = re.findall(
                r'(?:created|wrote|saved|generated|modified)\s+(?:file\s+)?[`"\']?([^\s`"\']+\.\w+)',
                output, re.IGNORECASE,
            )
            for fp in file_patterns[:3]:
                claims.append({"type": "file_exists", "value": fp, "source": output[:100]})

            # Count claims ("found N", "discovered N", "identified N")
            count_patterns = re.findall(
                r'(?:found|discovered|identified|detected|enumerated)\s+(\d+)\s+(\w+)',
                output, re.IGNORECASE,
            )
            for count, thing in count_patterns[:2]:
                claims.append({
                    "type": "count",
                    "value": f"{count} {thing}",
                    "source": output[:100],
                    "expected_count": count,
                })

            # Tool success claims (verify tool actually returned success)
            tool_calls = output_info.get("tool_calls", [])
            if "successfully" in output.lower() and tool_calls:
                last_tool = tool_calls[-1] if tool_calls else None
                if last_tool and not last_tool.get("success", True):
                    claims.append({
                        "type": "tool_success_mismatch",
                        "value": f"Claims success but last tool failed: {last_tool.get('tool_name', 'unknown')}",
                        "source": output[:100],
                    })

        # Ledger entry claims
        if ledger_entries:
            for entry in ledger_entries:
                key_data = entry.get("key_data", [])
                for kd in key_data[:2]:
                    # Check if key_data mentions file paths
                    file_match = re.search(r'([^\s]+\.\w{1,5})\b', kd)
                    if file_match:
                        claims.append({
                            "type": "file_exists",
                            "value": file_match.group(1),
                            "source": f"ledger: {entry.get('did_what', '')[:80]}",
                        })

        return claims

    def _verify_claim(self, claim: dict[str, str]) -> VerificationResult:
        """Verify a single extracted claim."""
        claim_type = claim["type"]
        value = claim["value"]

        if claim_type == "file_exists":
            return self._verify_file_exists(value, claim.get("source", ""))

        elif claim_type == "count":
            return self._verify_count(value, claim.get("expected_count", "0"), claim.get("source", ""))

        elif claim_type == "tool_success_mismatch":
            return VerificationResult(
                claim=value,
                claim_type="tool_success_mismatch",
                verified=False,
                evidence="Agent claimed success but tool returned failure",
                confidence=0.9,
            )

        # Unrecognised claim type: we have no evidence either way. Do NOT mark it
        # verified — "can't disprove → assume true" is exactly what let tautological
        # and back-solved verdicts sail through. Low confidence keeps it out of
        # claims_failed (we didn't catch a contradiction), but counting it as
        # unverified stops it from inflating the trust score.
        return VerificationResult(
            claim=value,
            claim_type=claim_type,
            verified=False,
            evidence="No independent evidence — not checked, not assumed true",
            confidence=0.3,
        )

    def _verify_file_exists(self, filepath: str, source: str) -> VerificationResult:
        """Check if a claimed file actually exists in workspace."""
        # Try multiple possible locations
        candidates = [
            self._workspace / filepath,
            self._workspace / Path(filepath).name,
            self._context_dir / filepath,
            Path(filepath),  # Absolute path
        ]

        for candidate in candidates:
            if candidate.exists() and candidate.is_file():
                size = candidate.stat().st_size
                # CC-1: an EMPTY file is not a delivered artifact. "It exists" is
                # not proof — a 0-byte report used to PASS at 0.95. Treat empty as
                # a FAILED claim (confidence ≥0.7 so it reaches claims_failed and
                # blocks completion).
                if size == 0:
                    return VerificationResult(
                        claim=f"File exists: {filepath}",
                        claim_type="file_exists",
                        verified=False,
                        evidence=f"File exists at {candidate} but is EMPTY (0 bytes)",
                        confidence=0.8,
                    )
                return VerificationResult(
                    claim=f"File exists: {filepath}",
                    claim_type="file_exists",
                    verified=True,
                    evidence=f"Found at {candidate} ({size} bytes)",
                    confidence=0.95,
                )

        return VerificationResult(
            claim=f"File exists: {filepath}",
            claim_type="file_exists",
            verified=False,
            evidence=f"Not found in workspace or context directory",
            confidence=0.8,
        )

    def _verify_count(self, value: str, expected_count: str, source: str) -> VerificationResult:
        """Verify a count claim against available evidence.

        Checks vault and activity log for corroborating data.
        """
        try:
            expected = int(expected_count)
        except (ValueError, TypeError):
            # CC-1: unparseable expected count → cannot verify; do not assume true.
            return VerificationResult(
                claim=f"Count: {value}",
                claim_type="count",
                verified=False,
                evidence="Count unparseable — cannot verify (not assumed true)",
                confidence=0.2,
            )

        # Check vault for matching data
        vault_path = self._context_dir / "VAULT.jsonl"
        if vault_path.exists():
            try:
                vault_content = vault_path.read_text(encoding="utf-8")
                # Look for arrays or lists that match the claimed count
                # This is a rough heuristic — counts items in JSON arrays
                import json
                for line in vault_content.split("\n"):
                    if not line.strip():
                        continue
                    try:
                        entry = json.loads(line)
                        content = entry.get("content", "")
                        # Count items if content looks like a list
                        items = content.count("\n- ") + content.count("\n* ")
                        if items > 0 and abs(items - expected) <= 2:
                            return VerificationResult(
                                claim=f"Count: {value}",
                                claim_type="count",
                                verified=True,
                                evidence=f"Vault contains ~{items} items (claimed {expected})",
                                confidence=0.7,
                            )
                    except json.JSONDecodeError:
                        continue
            except Exception:
                pass

        # If expected count is very high (>20) and we can't verify, flag as suspicious
        if expected > 20:
            return VerificationResult(
                claim=f"Count: {value}",
                claim_type="count",
                verified=False,
                evidence=f"High count ({expected}) unverifiable — no corroborating data in vault",
                confidence=0.5,
            )

        # CC-1: no corroborating data → do NOT auto-pass. "Can't disprove → assume
        # true" is exactly the theater the audit flagged. Mark unverified at low
        # confidence (stays out of claims_failed — we didn't catch a contradiction —
        # but no longer inflates the trust score as a verified claim).
        return VerificationResult(
            claim=f"Count: {value}",
            claim_type="count",
            verified=False,
            evidence="Count unverifiable — no corroborating data in vault (not assumed true)",
            confidence=0.3,
        )

    def get_trust_score(self, recent_turns: int = 5) -> float:
        """Get aggregate trust score for recent turns.

        Returns 0.0 (all claims false) to 1.0 (all verified).
        Used by orchestrator to discount progress when trust is low.
        """
        recent = self._history[-recent_turns:] if self._history else []
        if not recent:
            return 0.8  # Default: trust until proven otherwise

        total_checked = sum(v.claims_checked for v in recent)
        total_verified = sum(v.claims_verified for v in recent)

        if total_checked == 0:
            return 0.8

        return total_verified / total_checked

    def build_verification_context(self) -> str:
        """Build context string for orchestrator about verification state."""
        if not self._history:
            return ""

        recent = self._history[-3:]
        trust = self.get_trust_score()

        # Self-attested verdicts are worth surfacing even at high trust: a turn can
        # have zero failed filesystem checks yet rest entirely on "PASS"/"đã xác minh"
        # claims nobody re-ran. That's the false-positive spiral, so we flag it.
        recent_self_attested = [s for v in recent for s in v.self_attested]

        if trust >= 0.8 and not recent_self_attested:
            return ""  # High trust and no unchecked verdicts — no need to inject doubt

        parts = [f"## Ledger Verification (Trust Score: {trust:.0%})"]
        if trust < 0.5:
            parts.append("⚠️ LOW TRUST: Multiple agent claims failed verification. Discount reported progress.")

        for v in recent:
            if v.claims_failed > 0:
                failed_claims = [r for r in v.results if not r.verified and r.confidence >= 0.7]
                for fc in failed_claims[:2]:
                    parts.append(f"- Turn {v.turn} FAILED: {fc.claim} ({fc.evidence})")

        if recent_self_attested:
            parts.append(
                "⚠️ SELF-ATTESTED, NOT INDEPENDENTLY VERIFIED — the following verdicts "
                "are the agent grading its own work; no tool/critic re-ran them. Treat "
                "as unproven until a DIFFERENT agent re-executes the numbers (watch for "
                "tautologies and back-solved targets):"
            )
            for snippet in recent_self_attested[:4]:
                parts.append(f"- {snippet}")

        return "\n".join(parts)
