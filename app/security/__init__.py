"""
MekongAI Nexus - Security Module (OpenClaw-inspired).

Comprehensive security framework providing:
- SSRF Guard: Block private IPs, metadata endpoints, DNS rebinding protection
- Safe Regex: ReDoS-safe regex validation
- Secret Scanner: Detect secrets in tool outputs
- Security Audit: Multi-module security scanning
- Credential Sanitization: Redact secrets from tool outputs
- Pentest Phases: PTES-based penetration testing lifecycle
- Exploitation Chain: Chain findings for maximum impact
- Curiosity Driver: Drive deeper investigation on findings
- Case Management: Intelligence case lifecycle (A05/Cục B)
- Classification: Vietnamese state secret classification levels
- Legal Compliance: Vietnamese legal framework gates
"""

from app.security.ssrf_guard import SSRFGuard, SSRFResult, get_ssrf_guard
from app.security.safe_regex import SafeRegex, RegexValidation
from app.security.secret_scanner import SecretScanner, get_secret_scanner

# Re-export from legacy location for canonical access
# (app.core.security_audit is deprecated; prefer app.security.sanitize_credentials)
from app.core.security_audit import sanitize_credentials

# New: Pentest system
from app.security.pentest_phases import PentestPhase, PentestPhaseMachine
from app.security.exploitation_chain import (
    CuriosityDriver,
    ExploitationChainEngine,
    SecurityFinding,
)

# Intelligence operations framework
from app.security.case_management import (
    Case,
    CaseManager,
    CasePriority,
    CaseStatus,
    CaseType,
)
from app.security.classification import (
    ClassificationEngine,
    ClassificationLevel,
    ClassificationMarking,
)
from app.security.legal_compliance import (
    LegalBasis,
    LegalComplianceEngine,
    OperationAuthorization,
)

__all__ = [
    "SSRFGuard",
    "SSRFResult",
    "get_ssrf_guard",
    "SafeRegex",
    "RegexValidation",
    "SecretScanner",
    "get_secret_scanner",
    "sanitize_credentials",
    # Pentest system
    "PentestPhase",
    "PentestPhaseMachine",
    "CuriosityDriver",
    "ExploitationChainEngine",
    "SecurityFinding",
    # Intelligence operations
    "Case",
    "CaseManager",
    "CasePriority",
    "CaseStatus",
    "CaseType",
    "ClassificationEngine",
    "ClassificationLevel",
    "ClassificationMarking",
    "LegalBasis",
    "LegalComplianceEngine",
    "OperationAuthorization",
]
