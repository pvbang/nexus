"""
MekongAI Nexus - Legal Compliance Framework.

Implements legal compliance gates for intelligence operations
following Vietnamese legal framework:

- Bộ luật Tố tụng Hình sự 2015 (Criminal Procedure Code)
- Luật An ninh Mạng 2018 (Cybersecurity Law)
- Luật Phòng, chống rửa tiền 2022 (Anti-Money Laundering Law)
- Luật Bảo vệ Bí mật Nhà nước 2018 (State Secrets Protection Law)
- Luật Công nghệ Thông tin 2006 (IT Law)

Ensures operations have proper legal authority before execution.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

import structlog

logger = structlog.get_logger(__name__)


class LegalBasis(str, Enum):
    """Legal basis for intelligence/security operations."""
    # Bộ luật Tố tụng Hình sự 2015
    CRIMINAL_INVESTIGATION = "criminal_investigation"  # Điều 34-36: Cơ quan điều tra
    SEARCH_WARRANT = "search_warrant"                  # Điều 192: Khám xét
    SEIZURE_ORDER = "seizure_order"                    # Điều 196: Thu giữ
    SURVEILLANCE_ORDER = "surveillance_order"          # Điều 223: Biện pháp điều tra đặc biệt
    WIRETAP_ORDER = "wiretap_order"                    # Điều 224: Nghe điện thoại bí mật
    DATA_INTERCEPTION = "data_interception"            # Điều 225: Thu thập dữ liệu điện tử

    # Luật An ninh Mạng 2018
    CYBER_SECURITY_RESPONSE = "cyber_security_response"  # Điều 10: Phòng ngừa
    CYBER_EMERGENCY = "cyber_emergency"                  # Điều 21: Ứng phó sự cố
    CYBER_INVESTIGATION = "cyber_investigation"          # Điều 26: Điều tra tội phạm mạng

    # Luật Phòng, chống rửa tiền 2022
    AML_INVESTIGATION = "aml_investigation"            # Điều 34: Điều tra rửa tiền
    STR_ANALYSIS = "str_analysis"                      # Điều 25: Phân tích STR
    ASSET_FREEZE = "asset_freeze"                      # Điều 37: Phong tỏa tài sản

    # Administrative
    SECURITY_ASSESSMENT = "security_assessment"         # Đánh giá bảo mật (authorized)
    PENETRATION_TEST = "penetration_test"              # Kiểm thử xâm nhập (authorized)
    TRAINING_EXERCISE = "training_exercise"            # Diễn tập, huấn luyện
    OPEN_SOURCE = "open_source"                        # Thu thập nguồn mở (không cần lệnh)


class ApprovalAuthority(str, Enum):
    """Authority levels for approval chain."""
    TEAM_LEAD = "team_lead"                 # Trưởng nhóm
    DEPARTMENT_HEAD = "department_head"      # Trưởng phòng
    DEPUTY_DIRECTOR = "deputy_director"      # Phó Giám đốc/Phó Cục trưởng
    DIRECTOR = "director"                    # Giám đốc/Cục trưởng
    PROCURATOR = "procurator"               # Viện Kiểm sát (prosecution oversight)
    COURT = "court"                         # Tòa án (judicial warrant)
    MINISTER = "minister"                   # Bộ trưởng (ministerial level)


# Required approval level per operation type
APPROVAL_REQUIREMENTS: Dict[str, Dict[str, Any]] = {
    "osint_collection": {
        "legal_basis": [LegalBasis.OPEN_SOURCE],
        "min_authority": ApprovalAuthority.TEAM_LEAD,
        "requires_warrant": False,
        "requires_procurator": False,
        "notes": "Thu thập nguồn mở không yêu cầu lệnh tòa",
    },
    "security_assessment": {
        "legal_basis": [LegalBasis.SECURITY_ASSESSMENT, LegalBasis.PENETRATION_TEST],
        "min_authority": ApprovalAuthority.DEPARTMENT_HEAD,
        "requires_warrant": False,
        "requires_procurator": False,
        "notes": "Yêu cầu văn bản ủy quyền từ chủ sở hữu hệ thống",
    },
    "network_surveillance": {
        "legal_basis": [LegalBasis.SURVEILLANCE_ORDER, LegalBasis.CYBER_INVESTIGATION],
        "min_authority": ApprovalAuthority.DEPUTY_DIRECTOR,
        "requires_warrant": True,
        "requires_procurator": True,
        "notes": "Điều 223 BLTTHS: Cần phê chuẩn của Viện Kiểm sát",
        "max_duration_days": 60,
        "renewal_authority": ApprovalAuthority.DIRECTOR,
    },
    "communications_interception": {
        "legal_basis": [LegalBasis.WIRETAP_ORDER, LegalBasis.DATA_INTERCEPTION],
        "min_authority": ApprovalAuthority.DIRECTOR,
        "requires_warrant": True,
        "requires_procurator": True,
        "notes": "Điều 224-225 BLTTHS: Biện pháp điều tra đặc biệt, cần Viện trưởng VKS phê chuẩn",
        "max_duration_days": 60,
        "renewal_authority": ApprovalAuthority.DIRECTOR,
    },
    "financial_investigation": {
        "legal_basis": [LegalBasis.AML_INVESTIGATION, LegalBasis.STR_ANALYSIS],
        "min_authority": ApprovalAuthority.DEPARTMENT_HEAD,
        "requires_warrant": False,
        "requires_procurator": False,
        "notes": "Luật PCRT 2022: AMLD có quyền yêu cầu thông tin từ tổ chức tài chính",
    },
    "asset_freeze": {
        "legal_basis": [LegalBasis.ASSET_FREEZE],
        "min_authority": ApprovalAuthority.DIRECTOR,
        "requires_warrant": True,
        "requires_procurator": True,
        "notes": "Điều 37 Luật PCRT: Phong tỏa tạm thời, tối đa 3 ngày làm việc",
        "max_duration_days": 3,
    },
    "search_and_seizure": {
        "legal_basis": [LegalBasis.SEARCH_WARRANT, LegalBasis.SEIZURE_ORDER],
        "min_authority": ApprovalAuthority.COURT,
        "requires_warrant": True,
        "requires_procurator": True,
        "notes": "Điều 192-196 BLTTHS: Lệnh khám xét từ Cơ quan điều tra, phê chuẩn VKS",
    },
    "cyber_emergency_response": {
        "legal_basis": [LegalBasis.CYBER_EMERGENCY, LegalBasis.CYBER_SECURITY_RESPONSE],
        "min_authority": ApprovalAuthority.DEPARTMENT_HEAD,
        "requires_warrant": False,
        "requires_procurator": False,
        "notes": "Điều 21 Luật ANMN: Ứng phó khẩn cấp không cần lệnh trước, báo cáo sau",
        "report_within_hours": 24,
    },
}


@dataclass
class LegalApproval:
    """A legal approval in the authorization chain."""
    authority: ApprovalAuthority
    approver_name: str = ""
    approved_at: str = ""
    approval_reference: str = ""  # Document/decision number
    scope: str = ""
    conditions: List[str] = field(default_factory=list)
    expires_at: str = ""
    status: str = "pending"  # pending, approved, denied, expired


@dataclass
class OperationAuthorization:
    """Complete authorization record for an operation."""
    operation_id: str = ""
    operation_type: str = ""
    legal_basis: List[LegalBasis] = field(default_factory=list)
    approvals: List[LegalApproval] = field(default_factory=list)
    targets: List[str] = field(default_factory=list)
    scope_description: str = ""
    constraints: List[str] = field(default_factory=list)
    start_date: str = ""
    end_date: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    status: str = "pending"  # pending, authorized, denied, expired, completed


class LegalComplianceEngine:
    """
    Enforces legal compliance for intelligence operations.

    Validates that operations have proper legal authority,
    required approvals at correct levels, and maintains
    audit trail for legal proceedings.
    """

    def __init__(self) -> None:
        self._authorizations: Dict[str, OperationAuthorization] = {}
        self._compliance_log: List[Dict[str, Any]] = []

    def get_requirements(self, operation_type: str) -> Dict[str, Any]:
        """Get legal requirements for an operation type."""
        return APPROVAL_REQUIREMENTS.get(operation_type, {})

    def create_authorization(
        self,
        operation_type: str,
        targets: List[str],
        scope: str,
        legal_basis: Optional[List[LegalBasis]] = None,
    ) -> OperationAuthorization:
        """Create an authorization request for an operation."""
        import uuid
        requirements = self.get_requirements(operation_type)

        auth = OperationAuthorization(
            operation_id=f"AUTH-{str(uuid.uuid4())[:8].upper()}",
            operation_type=operation_type,
            legal_basis=legal_basis or requirements.get("legal_basis", []),
            targets=targets,
            scope_description=scope,
            constraints=[requirements.get("notes", "")],
        )

        max_days = requirements.get("max_duration_days")
        if max_days:
            auth.constraints.append(f"Thời hạn tối đa: {max_days} ngày")

        self._authorizations[auth.operation_id] = auth
        self._log("authorization_created", auth.operation_id, operation_type)
        return auth

    def add_approval(
        self,
        operation_id: str,
        approval: LegalApproval,
    ) -> bool:
        """Add an approval to an authorization."""
        auth = self._authorizations.get(operation_id)
        if not auth:
            return False

        approval.approved_at = datetime.now(timezone.utc).isoformat()
        auth.approvals.append(approval)
        self._log(
            "approval_added", operation_id,
            f"{approval.authority.value}: {approval.status}",
        )
        return True

    def validate_authorization(self, operation_id: str) -> Dict[str, Any]:
        """
        Validate if operation has all required approvals.

        Returns validation result with pass/fail and missing items.
        """
        auth = self._authorizations.get(operation_id)
        if not auth:
            return {"valid": False, "reason": "authorization_not_found"}

        requirements = self.get_requirements(auth.operation_type)
        if not requirements:
            return {"valid": True, "reason": "no_specific_requirements", "warnings": ["Operation type has no defined requirements"]}

        issues: List[str] = []

        # Check minimum authority level
        min_authority = requirements.get("min_authority")
        if min_authority:
            approved_levels = [
                a.authority for a in auth.approvals if a.status == "approved"
            ]
            if not approved_levels:
                issues.append(f"Chưa có phê duyệt (yêu cầu: {min_authority.value})")
            elif max(approved_levels, key=lambda x: list(ApprovalAuthority).index(x)) < min_authority:
                issues.append(f"Cấp phê duyệt chưa đủ (yêu cầu: {min_authority.value})")

        # Check warrant requirement
        if requirements.get("requires_warrant"):
            warrant_approvals = [
                a for a in auth.approvals
                if a.authority in (ApprovalAuthority.COURT, ApprovalAuthority.DIRECTOR)
                and a.status == "approved"
            ]
            if not warrant_approvals:
                issues.append("Chưa có lệnh/quyết định từ cơ quan có thẩm quyền")

        # Check procurator oversight
        if requirements.get("requires_procurator"):
            procurator_approvals = [
                a for a in auth.approvals
                if a.authority == ApprovalAuthority.PROCURATOR
                and a.status == "approved"
            ]
            if not procurator_approvals:
                issues.append("Chưa có phê chuẩn của Viện Kiểm sát")

        # Check expiration
        if auth.end_date:
            try:
                end = datetime.fromisoformat(auth.end_date)
                if end < datetime.now(timezone.utc):
                    issues.append("Ủy quyền đã hết hạn")
            except ValueError:
                pass

        valid = len(issues) == 0
        if valid:
            auth.status = "authorized"
        else:
            auth.status = "pending"

        self._log(
            "validation_result", operation_id,
            "PASS" if valid else f"FAIL: {'; '.join(issues)}",
        )

        return {
            "valid": valid,
            "operation_id": operation_id,
            "operation_type": auth.operation_type,
            "issues": issues,
            "approvals_count": len([a for a in auth.approvals if a.status == "approved"]),
            "requirements": requirements,
        }

    def check_operation_permitted(
        self,
        operation_type: str,
        targets: List[str],
    ) -> Dict[str, Any]:
        """
        Quick check if an operation type is permitted given requirements.

        Returns guidance on what approvals are needed.
        """
        requirements = self.get_requirements(operation_type)
        if not requirements:
            return {
                "permitted": True,
                "guidance": "Loại hoạt động không có yêu cầu pháp lý đặc biệt",
                "recommendations": ["Document all actions for audit trail"],
            }

        guidance_parts: List[str] = []
        guidance_parts.append(f"Cơ sở pháp lý: {', '.join(lb.value for lb in requirements.get('legal_basis', []))}")
        guidance_parts.append(f"Cấp phê duyệt tối thiểu: {requirements['min_authority'].value}")

        if requirements.get("requires_warrant"):
            guidance_parts.append("⚠️ YÊU CẦU LỆNH/QUYẾT ĐỊNH từ cơ quan có thẩm quyền")
        if requirements.get("requires_procurator"):
            guidance_parts.append("⚠️ YÊU CẦU PHÊ CHUẨN của Viện Kiểm sát")

        max_days = requirements.get("max_duration_days")
        if max_days:
            guidance_parts.append(f"Thời hạn tối đa: {max_days} ngày")

        if requirements.get("notes"):
            guidance_parts.append(f"Lưu ý: {requirements['notes']}")

        return {
            "permitted": not requirements.get("requires_warrant", False),
            "requires_authorization": requirements.get("requires_warrant", False),
            "guidance": "\n".join(guidance_parts),
            "min_authority": requirements["min_authority"].value,
        }

    def _log(self, event_type: str, operation_id: str, detail: str = "") -> None:
        """Log compliance event."""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event_type,
            "operation_id": operation_id,
            "detail": detail,
        }
        self._compliance_log.append(entry)
        logger.info("legal_compliance", **entry)

    def get_compliance_log(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get recent compliance log."""
        return self._compliance_log[-limit:]
