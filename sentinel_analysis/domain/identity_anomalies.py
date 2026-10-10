"""Domain models and algorithms for Maritime AIS Identity Anomaly Detection.

Covers:
- MMSI_REUSE: Multiple distinct vessels or hull metadata sharing the same MMSI.
- IMPOSSIBLE_JUMP: Kinematically impossible position displacement (> physical speed limit).
- FLAG_CALLSIGN_CHANGE: Inconsistent or suddenly mutated Maritime Identification Digits (MID), flag states, or callsigns.
- DUPLICATE_IDENTITY: Simultaneous or near-simultaneous broadcasts from geographically disparate locations.
- AIS_GAP: Extended unannounced blackout / dark period during active voyage.
- LOITERING: Prolonged stationary or slow-drift operation (< 2-3 kn) outside designated ports/anchorages.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from sentinel_analysis.domain.exceptions import DomainValidationError


class IdentityAnomalyType(str, Enum):
    MMSI_REUSE = "MMSI_REUSE"
    IMPOSSIBLE_JUMP = "IMPOSSIBLE_JUMP"
    FLAG_CALLSIGN_CHANGE = "FLAG_CALLSIGN_CHANGE"
    DUPLICATE_IDENTITY = "DUPLICATE_IDENTITY"
    AIS_GAP = "AIS_GAP"
    LOITERING = "LOITERING"


class IdentityAnomalySeverity(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


# ITU Maritime Identification Digits (MID) reference table: leading 3 digits of MMSI
# Reference: ITU-R M.585-8
MID_TO_COUNTRY: dict[int, tuple[str, str]] = {
    # Americas
    304: ("Antigua and Barbuda", "AG"),
    305: ("Antigua and Barbuda", "AG"),
    308: ("Bahamas", "BS"),
    309: ("Bahamas", "BS"),
    311: ("Bahamas", "BS"),
    312: ("Belize", "BZ"),
    314: ("Barbados", "BB"),
    316: ("Canada", "CA"),
    319: ("Cayman Islands", "KY"),
    325: ("Dominica", "DM"),
    338: ("United States of America", "US"),
    351: ("Panama", "PA"),
    352: ("Panama", "PA"),
    353: ("Panama", "PA"),
    354: ("Panama", "PA"),
    355: ("Panama", "PA"),
    356: ("Panama", "PA"),
    357: ("Panama", "PA"),
    366: ("United States of America", "US"),
    367: ("United States of America", "US"),
    368: ("United States of America", "US"),
    369: ("United States of America", "US"),
    370: ("Panama", "PA"),
    371: ("Panama", "PA"),
    372: ("Panama", "PA"),
    373: ("Panama", "PA"),
    # Europe
    205: ("Belgium", "BE"),
    209: ("Cyprus", "CY"),
    210: ("Cyprus", "CY"),
    211: ("Germany", "DE"),
    212: ("Cyprus", "CY"),
    215: ("Malta", "MT"),
    218: ("Germany", "DE"),
    219: ("Denmark", "DK"),
    220: ("Denmark", "DK"),
    224: ("Spain", "ES"),
    225: ("Spain", "ES"),
    226: ("France", "FR"),
    227: ("France", "FR"),
    228: ("France", "FR"),
    230: ("Finland", "FI"),
    231: ("Faroe Islands", "FO"),
    232: ("United Kingdom", "GB"),
    233: ("United Kingdom", "GB"),
    234: ("United Kingdom", "GB"),
    235: ("United Kingdom", "GB"),
    236: ("Gibraltar", "GI"),
    237: ("Greece", "GR"),
    238: ("Croatia", "HR"),
    239: ("Greece", "GR"),
    240: ("Greece", "GR"),
    241: ("Greece", "GR"),
    244: ("Netherlands", "NL"),
    245: ("Netherlands", "NL"),
    246: ("Netherlands", "NL"),
    247: ("Italy", "IT"),
    248: ("Malta", "MT"),
    249: ("Malta", "MT"),
    250: ("Ireland", "IE"),
    256: ("Malta", "MT"),
    257: ("Norway", "NO"),
    258: ("Norway", "NO"),
    259: ("Norway", "NO"),
    261: ("Poland", "PL"),
    263: ("Portugal", "PT"),
    265: ("Sweden", "SE"),
    266: ("Sweden", "SE"),
    271: ("Turkey", "TR"),
    273: ("Russian Federation", "RU"),
    # Asia & Oceania
    412: ("China", "CN"),
    413: ("China", "CN"),
    414: ("China", "CN"),
    416: ("Taiwan", "TW"),
    419: ("India", "IN"),
    431: ("Japan", "JP"),
    432: ("Japan", "JP"),
    440: ("Republic of Korea", "KR"),
    441: ("Republic of Korea", "KR"),
    477: ("Hong Kong", "HK"),
    503: ("Australia", "AU"),
    512: ("New Zealand", "NZ"),
    518: ("Cook Islands", "CK"),
    525: ("Indonesia", "ID"),
    533: ("Malaysia", "MY"),
    538: ("Marshall Islands", "MH"),
    548: ("Philippines", "PH"),
    563: ("Singapore", "SG"),
    564: ("Singapore", "SG"),
    565: ("Singapore", "SG"),
    566: ("Singapore", "SG"),
    567: ("Thailand", "TH"),
    574: ("Vietnam", "VN"),
    # Africa
    636: ("Liberia", "LR"),
    637: ("Liberia", "LR"),
    667: ("Sierra Leone", "SL"),
    671: ("Togolese Republic", "TG"),
}


def haversine_distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two geographic points in kilometers."""
    radius_km = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)

    a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * (math.sin(dlam / 2.0) ** 2)
    a = min(1.0, max(0.0, a))
    return radius_km * (2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a)))


def get_country_from_mmsi(mmsi: str | int) -> tuple[Optional[str], Optional[str]]:
    """Look up flag state country name and ISO 2-letter code from MMSI MID digits."""
    s = str(mmsi).strip()
    if len(s) < 3:
        return None, None
    try:
        mid = int(s[:3])
        return MID_TO_COUNTRY.get(mid, (None, None))
    except ValueError:
        return None, None


@dataclass(frozen=True)
class IdentityAnomaly:
    """Represents a validated maritime identity or kinematic anomaly."""

    anomaly_id: str
    anomaly_type: IdentityAnomalyType
    mmsi: str
    vessel_name: Optional[str] = None
    severity: IdentityAnomalySeverity = IdentityAnomalySeverity.MEDIUM
    confidence: float = 0.90
    description: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    detected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    latitude: Optional[float] = None
    longitude: Optional[float] = None

    def __post_init__(self) -> None:
        if not self.anomaly_id:
            raise DomainValidationError("anomaly_id is required")
        if not self.mmsi:
            raise DomainValidationError("mmsi is required")
        if not (0.0 <= self.confidence <= 1.0):
            raise DomainValidationError("confidence must be between 0.0 and 1.0")

    def to_dict(self) -> dict[str, Any]:
        return {
            "anomaly_id": self.anomaly_id,
            "anomaly_type": self.anomaly_type.value if isinstance(self.anomaly_type, IdentityAnomalyType) else str(self.anomaly_type),
            "mmsi": str(self.mmsi),
            "vessel_name": self.vessel_name,
            "severity": self.severity.value if isinstance(self.severity, IdentityAnomalySeverity) else str(self.severity),
            "confidence": round(self.confidence, 3),
            "description": self.description,
            "evidence": self.evidence,
            "detected_at": self.detected_at.isoformat() if hasattr(self.detected_at, "isoformat") else str(self.detected_at),
            "latitude": round(self.latitude, 6) if self.latitude is not None else None,
            "longitude": round(self.longitude, 6) if self.longitude is not None else None,
        }
