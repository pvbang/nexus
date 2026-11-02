"""
Nexus - Information Classification System.

Implements Vietnamese state secret classification levels per:
- Luật Bảo vệ Bí mật Nhà nước 2018 (Law on Protection of State Secrets)
- Nghị định 26/2020/NĐ-CP (implementing regulations)

Classification levels:
- TUYỆT MẬT (Top Secret) — Đặc biệt quan trọng
- TỐI MẬT (Secret) — Rất quan trọng
- MẬT (Confidential) — Quan trọng
- NỘI BỘ (Internal) — Hạn chế phổ biến
- CÔNG KHAI (Unclassified) — Không hạn chế
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import IntEnum
from typing import Any, Dict, List, Optional, Set

import structlog

logger = structlog.get_logger(__name__)


class ClassificationLevel(IntEnum):
    """
    Vietnamese classification levels per Luật Bảo vệ BMNN 2018.

    Higher number = more restricted. Used for access control comparisons:
    user.clearance >= document.classification → access granted.
    """
    CONG_KHAI = 0       # Công khai — Unclassified
    NOI_BO = 1          # Nội bộ — Internal/Restricted
    MAT = 2             # Mật — Confidential
    TOI_MAT = 3         # Tối Mật — Secret
    TUYET_MAT = 4       # Tuyệt Mật — Top Secret


CLASSIFICATION_LABELS: Dict[ClassificationLevel, Dict[str, str]] = {
    ClassificationLevel.CONG_KHAI: {
        "vi": "CÔNG KHAI",
        "en": "UNCLASSIFIED",
        "description": "Thông tin không hạn chế phổ biến",
        "marking": "",
        "handling": "Không yêu cầu đặc biệt",
    },
    ClassificationLevel.NOI_BO: {
        "vi": "NỘI BỘ",
        "en": "INTERNAL",
        "description": "Hạn chế phổ biến trong phạm vi cơ quan",
        "marking": "NỘI BỘ",
        "handling": "Chỉ phổ biến trong cơ quan, không công khai",
    },
    ClassificationLevel.MAT: {
        "vi": "MẬT",
        "en": "CONFIDENTIAL",
        "description": "Bí mật nhà nước mức Mật (Điều 9, Luật BVBMNN 2018)",
        "marking": "MẬT",
        "handling": (
            "Bảo quản tại nơi an toàn. Chỉ người có thẩm quyền tiếp cận. "
            "Ghi nhật ký truy cập. Mã hóa khi truyền tải điện tử."
        ),
    },
    ClassificationLevel.TOI_MAT: {
        "vi": "TỐI MẬT",
        "en": "SECRET",
        "description": "Bí mật nhà nước mức Tối Mật (Điều 8, Luật BVBMNN 2018)",
        "marking": "TỐI MẬT",
        "handling": (
            "Bảo quản trong két/tủ có khóa. Truy cập theo danh sách được phê duyệt. "
            "Ghi nhật ký chi tiết mọi truy cập. Không sao chép khi chưa được phê duyệt. "
            "Mã hóa với khóa riêng khi truyền tải."
        ),
    },
    ClassificationLevel.TUYET_MAT: {
        "vi": "TUYỆT MẬT",
        "en": "TOP SECRET",
        "description": "Bí mật nhà nước mức Tuyệt Mật (Điều 7, Luật BVBMNN 2018)",
        "marking": "TUYỆT MẬT",
        "handling": (
            "Bảo quản đặc biệt, phòng riêng có kiểm soát ra vào. "
            "Chỉ người đứng đầu cơ quan phê duyệt truy cập. "
            "Không được mang ra khỏi cơ quan. Ghi nhật ký với xác thực 2 người. "
            "Mã hóa cấp quốc gia khi truyền tải. Tiêu hủy theo quy trình đặc biệt."
        ),
    },
}


# Declassification timeline per Vietnamese law
DECLASSIFICATION_PERIODS: Dict[ClassificationLevel, int] = {
    ClassificationLevel.MAT: 10,          # 10 năm (có thể gia hạn)
    ClassificationLevel.TOI_MAT: 20,      # 20 năm (có thể gia hạn)
    ClassificationLevel.TUYET_MAT: 30,    # 30 năm (có thể gia hạn)
}


@dataclass
class ClassificationMarking:
    """Classification marking for a document/artifact."""
    level: ClassificationLevel
    caveats: List[str] = field(default_factory=list)
    releasable_to: List[str] = field(default_factory=list)
    originator: str = ""
    classified_by: str = ""
    classified_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    declassify_on: str = ""
    reason: str = ""


@dataclass
class AccessClearance:
    """User/agent access clearance."""
    entity_id: str = ""
    entity_type: str = "agent"  # agent, user, system
    clearance_level: ClassificationLevel = ClassificationLevel.NOI_BO
    compartments: Set[str] = field(default_factory=set)
    granted_by: str = ""
    granted_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    expires_at: str = ""
    restrictions: List[str] = field(default_factory=list)


class ClassificationEngine:
    """
    Enforces information classification and access control.

    Per Luật Bảo vệ Bí mật Nhà nước 2018:
    - Điều 7: Tuyệt Mật — information whose disclosure would cause
      exceptionally grave damage to national interests
    - Điều 8: Tối Mật — information whose disclosure would cause
      serious damage to national interests
    - Điều 9: Mật — information whose disclosure would cause
      damage to national interests
    """

    def __init__(self) -> None:
        self._clearances: Dict[str, AccessClearance] = {}
        self._access_log: List[Dict[str, Any]] = []

    def grant_clearance(self, clearance: AccessClearance) -> None:
        """Grant clearance to an entity."""
        self._clearances[clearance.entity_id] = clearance
        logger.info(
            "clearance_granted",
            entity=clearance.entity_id,
            level=clearance.clearance_level.name,
        )

    def check_access(
        self,
        entity_id: str,
        marking: ClassificationMarking,
    ) -> bool:
        """
        Check if entity has sufficient clearance for classified material.

        Access requires:
        1. Clearance level >= classification level
        2. Compartment access (if caveats specified)
        3. Need-to-know (enforced by caller, not this engine)
        """
        clearance = self._clearances.get(entity_id)
        if not clearance:
            self._log_access(entity_id, marking, granted=False, reason="no_clearance")
            return False

        # Level check
        if clearance.clearance_level < marking.level:
            self._log_access(
                entity_id, marking, granted=False,
                reason=f"insufficient_level: has {clearance.clearance_level.name}, needs {marking.level.name}",
            )
            return False

        # Compartment check
        if marking.caveats:
            missing = set(marking.caveats) - clearance.compartments
            if missing:
                self._log_access(
                    entity_id, marking, granted=False,
                    reason=f"missing_compartments: {missing}",
                )
                return False

        # Releasable-to check
        if marking.releasable_to and entity_id not in marking.releasable_to:
            if clearance.entity_type not in marking.releasable_to:
                self._log_access(
                    entity_id, marking, granted=False,
                    reason="not_in_releasable_list",
                )
                return False

        self._log_access(entity_id, marking, granted=True)
        return True

    def classify(
        self,
        level: ClassificationLevel,
        classified_by: str,
        reason: str = "",
        caveats: Optional[List[str]] = None,
        releasable_to: Optional[List[str]] = None,
    ) -> ClassificationMarking:
        """Create a classification marking."""
        declassify_years = DECLASSIFICATION_PERIODS.get(level)
        declassify_on = ""
        if declassify_years:
            from datetime import timedelta
            declassify_on = (
                datetime.now(timezone.utc) + timedelta(days=declassify_years * 365)
            ).isoformat()

        return ClassificationMarking(
            level=level,
            caveats=caveats or [],
            releasable_to=releasable_to or [],
            classified_by=classified_by,
            reason=reason,
            declassify_on=declassify_on,
        )

    def get_marking_text(self, marking: ClassificationMarking) -> str:
        """Get display text for classification marking."""
        label = CLASSIFICATION_LABELS[marking.level]
        text = label["vi"]
        if marking.caveats:
            text += f" // {'/'.join(marking.caveats)}"
        if marking.releasable_to:
            text += f" // REL TO: {', '.join(marking.releasable_to)}"
        return text

    def get_handling_instructions(self, level: ClassificationLevel) -> str:
        """Get handling instructions for a classification level."""
        return CLASSIFICATION_LABELS[level]["handling"]

    def _log_access(
        self,
        entity_id: str,
        marking: ClassificationMarking,
        granted: bool,
        reason: str = "",
    ) -> None:
        """Log access attempt for audit trail."""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "entity_id": entity_id,
            "classification": marking.level.name,
            "granted": granted,
            "reason": reason,
        }
        self._access_log.append(entry)
        if not granted:
            logger.warning("access_denied", **entry)

    def get_audit_log(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get recent access log entries."""
        return self._access_log[-limit:]
