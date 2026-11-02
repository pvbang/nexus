"""
Nexus - Case Management System.

Provides structured lifecycle management for intelligence/security cases
following Vietnamese law enforcement procedures (A05/Cục B).

Case lifecycle: OPEN → ACTIVE → REVIEW → CLOSED/ESCALATED/ARCHIVED
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

import structlog

logger = structlog.get_logger(__name__)


class CaseStatus(str, Enum):
    """Case lifecycle states."""
    DRAFT = "draft"
    OPEN = "open"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    REVIEW = "review"
    ESCALATED = "escalated"
    CLOSED_RESOLVED = "closed_resolved"
    CLOSED_INSUFFICIENT = "closed_insufficient"
    ARCHIVED = "archived"


class CasePriority(str, Enum):
    """Case priority levels aligned with Vietnamese law enforcement."""
    CRITICAL = "critical"       # Khẩn cấp — requires immediate action
    HIGH = "high"               # Quan trọng — 24-hour response
    MEDIUM = "medium"           # Bình thường — standard processing
    LOW = "low"                 # Theo dõi — monitoring only
    INFORMATIONAL = "info"      # Tham khảo — reference only


class CaseType(str, Enum):
    """Case categories for A05/Cục B operations."""
    CYBER_INTRUSION = "cyber_intrusion"
    DATA_BREACH = "data_breach"
    MALWARE_INCIDENT = "malware_incident"
    INSIDER_THREAT = "insider_threat"
    ESPIONAGE = "espionage"
    FINANCIAL_CRIME = "financial_crime"
    MONEY_LAUNDERING = "money_laundering"
    TERRORISM_FINANCING = "terrorism_financing"
    DISINFORMATION = "disinformation"
    SUPPLY_CHAIN_COMPROMISE = "supply_chain_compromise"
    VULNERABILITY_ASSESSMENT = "vulnerability_assessment"
    THREAT_HUNTING = "threat_hunting"
    OSINT_COLLECTION = "osint_collection"
    COUNTER_INTELLIGENCE = "counter_intelligence"
    NETWORK_SURVEILLANCE = "network_surveillance"
    CRYPTOCURRENCY_TRACING = "cryptocurrency_tracing"


VALID_TRANSITIONS: Dict[CaseStatus, set] = {
    CaseStatus.DRAFT: {CaseStatus.OPEN},
    CaseStatus.OPEN: {CaseStatus.ACTIVE, CaseStatus.CLOSED_INSUFFICIENT},
    CaseStatus.ACTIVE: {CaseStatus.SUSPENDED, CaseStatus.REVIEW, CaseStatus.ESCALATED},
    CaseStatus.SUSPENDED: {CaseStatus.ACTIVE, CaseStatus.CLOSED_INSUFFICIENT},
    CaseStatus.REVIEW: {CaseStatus.ACTIVE, CaseStatus.CLOSED_RESOLVED, CaseStatus.ESCALATED},
    CaseStatus.ESCALATED: {CaseStatus.ACTIVE, CaseStatus.CLOSED_RESOLVED},
    CaseStatus.CLOSED_RESOLVED: {CaseStatus.ARCHIVED},
    CaseStatus.CLOSED_INSUFFICIENT: {CaseStatus.ARCHIVED, CaseStatus.OPEN},
    CaseStatus.ARCHIVED: set(),
}


@dataclass
class CaseEvent:
    """Immutable event in case history."""
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    event_type: str = ""
    actor: str = ""
    description: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CaseEvidence:
    """Evidence item linked to a case."""
    evidence_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    description: str = ""
    source: str = ""
    file_path: str = ""
    sha256: str = ""
    acquired_at: str = ""
    acquired_by: str = ""
    classification: str = "INTERNAL"
    chain_of_custody: List[Dict[str, str]] = field(default_factory=list)


@dataclass
class Case:
    """Intelligence/security case."""
    case_id: str = field(default_factory=lambda: f"CASE-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{str(uuid.uuid4())[:6].upper()}")
    title: str = ""
    description: str = ""
    case_type: CaseType = CaseType.CYBER_INTRUSION
    status: CaseStatus = CaseStatus.DRAFT
    priority: CasePriority = CasePriority.MEDIUM
    classification: str = "MẬT"
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    created_by: str = ""
    assigned_to: List[str] = field(default_factory=list)
    targets: List[str] = field(default_factory=list)
    related_cases: List[str] = field(default_factory=list)
    events: List[CaseEvent] = field(default_factory=list)
    evidence: List[CaseEvidence] = field(default_factory=list)
    findings: List[Dict[str, Any]] = field(default_factory=list)
    legal_authority: str = ""
    approval_chain: List[Dict[str, str]] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)


class CaseManager:
    """
    Manages case lifecycle for intelligence operations.

    Enforces valid transitions, maintains audit trail,
    and ensures legal compliance requirements.
    """

    def __init__(self) -> None:
        self._cases: Dict[str, Case] = {}

    def create_case(
        self,
        title: str,
        description: str,
        case_type: CaseType,
        priority: CasePriority = CasePriority.MEDIUM,
        classification: str = "MẬT",
        created_by: str = "system",
        legal_authority: str = "",
        targets: Optional[List[str]] = None,
    ) -> Case:
        """Create a new case in DRAFT status."""
        case = Case(
            title=title,
            description=description,
            case_type=case_type,
            priority=priority,
            classification=classification,
            created_by=created_by,
            legal_authority=legal_authority,
            targets=targets or [],
        )
        case.events.append(CaseEvent(
            event_type="created",
            actor=created_by,
            description=f"Case created: {title}",
        ))
        self._cases[case.case_id] = case
        logger.info("case_created", case_id=case.case_id, type=case_type.value)
        return case

    def transition(
        self,
        case_id: str,
        new_status: CaseStatus,
        actor: str,
        reason: str = "",
    ) -> bool:
        """Transition case to new status with validation."""
        case = self._cases.get(case_id)
        if not case:
            logger.warning("case_not_found", case_id=case_id)
            return False

        valid = VALID_TRANSITIONS.get(case.status, set())
        if new_status not in valid:
            logger.warning(
                "invalid_case_transition",
                case_id=case_id,
                from_status=case.status.value,
                to_status=new_status.value,
            )
            return False

        old_status = case.status
        case.status = new_status
        case.events.append(CaseEvent(
            event_type="status_change",
            actor=actor,
            description=f"{old_status.value} → {new_status.value}: {reason}",
        ))

        logger.info(
            "case_transition",
            case_id=case_id,
            from_status=old_status.value,
            to_status=new_status.value,
            actor=actor,
        )
        return True

    def add_evidence(self, case_id: str, evidence: CaseEvidence) -> bool:
        """Add evidence to a case with chain-of-custody entry."""
        case = self._cases.get(case_id)
        if not case:
            return False

        evidence.chain_of_custody.append({
            "action": "added_to_case",
            "case_id": case_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "actor": evidence.acquired_by,
        })
        case.evidence.append(evidence)
        case.events.append(CaseEvent(
            event_type="evidence_added",
            actor=evidence.acquired_by,
            description=f"Evidence added: {evidence.description}",
            metadata={"evidence_id": evidence.evidence_id, "sha256": evidence.sha256},
        ))
        return True

    def add_finding(
        self,
        case_id: str,
        finding: Dict[str, Any],
        actor: str = "system",
    ) -> bool:
        """Add an intelligence finding to a case."""
        case = self._cases.get(case_id)
        if not case:
            return False

        finding["added_at"] = datetime.now(timezone.utc).isoformat()
        finding["added_by"] = actor
        case.findings.append(finding)
        case.events.append(CaseEvent(
            event_type="finding_added",
            actor=actor,
            description=f"Finding: {finding.get('title', 'untitled')}",
        ))
        return True

    def escalate(
        self,
        case_id: str,
        actor: str,
        reason: str,
        escalate_to: str,
    ) -> bool:
        """Escalate case to higher authority."""
        success = self.transition(case_id, CaseStatus.ESCALATED, actor, reason)
        if success:
            case = self._cases[case_id]
            case.events.append(CaseEvent(
                event_type="escalated",
                actor=actor,
                description=f"Escalated to: {escalate_to}. Reason: {reason}",
                metadata={"escalate_to": escalate_to},
            ))
        return success

    def get_case(self, case_id: str) -> Optional[Case]:
        """Retrieve a case by ID."""
        return self._cases.get(case_id)

    def search_cases(
        self,
        status: Optional[CaseStatus] = None,
        case_type: Optional[CaseType] = None,
        priority: Optional[CasePriority] = None,
    ) -> List[Case]:
        """Search cases by criteria."""
        results = list(self._cases.values())
        if status:
            results = [c for c in results if c.status == status]
        if case_type:
            results = [c for c in results if c.case_type == case_type]
        if priority:
            results = [c for c in results if c.priority == priority]
        return results

    def get_case_summary(self, case_id: str) -> Dict[str, Any]:
        """Get structured case summary."""
        case = self._cases.get(case_id)
        if not case:
            return {}
        return {
            "case_id": case.case_id,
            "title": case.title,
            "status": case.status.value,
            "priority": case.priority.value,
            "type": case.case_type.value,
            "classification": case.classification,
            "created_at": case.created_at,
            "evidence_count": len(case.evidence),
            "findings_count": len(case.findings),
            "events_count": len(case.events),
            "assigned_to": case.assigned_to,
            "targets": case.targets,
        }
