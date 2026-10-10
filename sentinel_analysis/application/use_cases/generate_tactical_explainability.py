"""Use case to generate tactical explainability rationales for maritime contacts."""

from __future__ import annotations

import logging
from typing import Any, Optional, Sequence

from sentinel_analysis.application.ports.scan_repository import ScanRepository
from sentinel_analysis.domain.explainability import (
    EvidenceItem,
    ExplainabilityReport,
    TargetClassification,
    TargetRationale,
    ThreatLevel,
)

logger = logging.getLogger(__name__)


class GenerateTacticalExplainability:
    """Produces explainability reports detailing why contacts were classified as dark, spoofed, SOLAS-suspect, or transshipping."""

    def __init__(self, scan_repository: Optional[ScanRepository] = None) -> None:
        self._scan_repository = scan_repository

    def execute(
        self,
        scan_id: Optional[str] = None,
        detections: Optional[list[dict[str, Any]]] = None,
        transshipment_events: Optional[list[dict[str, Any]]] = None,
        identity_anomalies: Optional[list[dict[str, Any]]] = None,
        environmental_context: Optional[dict[str, Any]] = None,
    ) -> ExplainabilityReport:
        """Evaluate contacts and generate tactical explainability rationales."""
        target_detections = list(detections or [])
        events = list(transshipment_events or [])
        anomalies = list(identity_anomalies or [])

        # If scan_id is provided and repository available, load data from scan
        if scan_id and self._scan_repository is not None and not target_detections:
            try:
                scan_obj = self._scan_repository.get_scan(scan_id)
                if scan_obj is not None:
                    if hasattr(scan_obj, "detections") and scan_obj.detections:
                        target_detections = [
                            d if isinstance(d, dict) else (d.to_dict() if hasattr(d, "to_dict") else vars(d))
                            for d in scan_obj.detections
                        ]
                    if hasattr(scan_obj, "metadata") and scan_obj.metadata:
                        if not target_detections and "detections" in scan_obj.metadata:
                            target_detections = list(scan_obj.metadata["detections"])
                        if not events and "transshipment_events" in scan_obj.metadata:
                            events = list(scan_obj.metadata["transshipment_events"])
            except Exception as exc:
                logger.warning("Could not load scan %s for explainability: %s", scan_id, exc)

        rationales: list[TargetRationale] = []

        dark_count = 0
        spoofed_count = 0
        solas_count = 0
        transshipment_count = 0
        cooperative_count = 0
        infra_count = 0

        for idx, det in enumerate(target_detections):
            rationale = self._evaluate_single_target(
                target_idx=idx,
                det=det,
                all_detections=target_detections,
                transshipment_events=events,
                identity_anomalies=anomalies,
                env_context=environmental_context,
            )
            rationales.append(rationale)

            primary = rationale.primary_classification
            if primary == TargetClassification.DARK_VESSEL:
                dark_count += 1
            elif primary == TargetClassification.SPOOFED_AIS:
                spoofed_count += 1
            elif primary == TargetClassification.SOLAS_SUSPECT:
                solas_count += 1
            elif primary == TargetClassification.TRANSSHIPMENT_SUSPECT:
                transshipment_count += 1
            elif primary == TargetClassification.OFFSHORE_INFRASTRUCTURE:
                infra_count += 1
            else:
                cooperative_count += 1

        return ExplainabilityReport(
            scan_id=scan_id,
            total_targets=len(rationales),
            dark_vessel_count=dark_count,
            spoofed_count=spoofed_count,
            solas_suspect_count=solas_count,
            transshipment_count=transshipment_count,
            cooperative_count=cooperative_count,
            infrastructure_count=infra_count,
            rationales=rationales,
        )

    def _evaluate_single_target(
        self,
        target_idx: int,
        det: dict[str, Any],
        all_detections: list[dict[str, Any]],
        transshipment_events: list[dict[str, Any]],
        identity_anomalies: list[dict[str, Any]],
        env_context: Optional[dict[str, Any]],
    ) -> TargetRationale:
        target_id = f"CONTACT-{target_idx + 1:03d}"
        lat = det.get("latitude") if det.get("latitude") is not None else det.get("lat")
        lon = det.get("longitude") if det.get("longitude") is not None else (det.get("lon") if det.get("lon") is not None else det.get("lng"))
        is_correlated = bool(det.get("is_correlated", False))
        confidence = float(det.get("confidence", 0.75)) if det.get("confidence") is not None else 0.75
        length = float(det.get("length")) if det.get("length") is not None else None
        beam = float(det.get("beam")) if det.get("beam") is not None else None

        # Flags from upstream detectors
        is_speed_spoofed = bool(det.get("is_speed_spoofed", False))
        is_course_spoofed = bool(det.get("is_course_spoofed", False))
        is_dark_flag = bool(det.get("is_dark_vessel", False)) or bool(det.get("is_dark", False))
        is_infra = (
            bool(det.get("is_offshore_infrastructure", False))
            or bool(det.get("is_infrastructure", False))
            or (det.get("detection_category") == "OFFSHORE_INFRASTRUCTURE")
            or bool(det.get("offshore_infrastructure"))
        )
        matched_infra = det.get("matched_infrastructure") or det.get("offshore_infrastructure")
        in_shipping_lane = bool(det.get("is_in_shipping_lane", False))
        shipping_lane_name = det.get("shipping_lane_name")
        in_anchorage = bool(det.get("is_in_anchorage", False))
        anchorage_name = det.get("anchorage_name")

        wake_detected = bool(det.get("wake_detected", False))
        wake_speed = det.get("wake_speed_knots")
        wake_heading = det.get("wake_heading") if det.get("wake_heading") is not None else det.get("wake_heading_deg")

        speed_diff = det.get("speed_discrepancy_knots")
        heading_diff = det.get("heading_discrepancy_deg")

        contributing: list[EvidenceItem] = []
        mitigating: list[EvidenceItem] = []
        rules_triggered: list[str] = []
        secondaries: list[TargetClassification] = []

        # =====================================================================
        # 1. FIXED OFFSHORE INFRASTRUCTURE
        # =====================================================================
        if is_infra:
            infra_name = matched_infra.get("name") if (matched_infra and isinstance(matched_infra, dict)) else "Charted Offshore Asset"
            infra_type = (matched_infra.get("type") or matched_infra.get("feature_type")) if (matched_infra and isinstance(matched_infra, dict)) else "Platform/Turbine"
            contributing.append(
                EvidenceItem(
                    factor="OFFSHORE_INFRASTRUCTURE_CO_LOCATION",
                    description=f"Detection coordinates match charted fixed offshore infrastructure: {infra_name} ({infra_type}).",
                    weight=0.95,
                )
            )
            mitigating.append(
                EvidenceItem(
                    factor="STATIONARY_STRUCTURE_EXONERATION",
                    description="Strong radar backscatter originated from fixed metallic jacket/tower, not an underway vessel.",
                    weight=0.90,
                    is_mitigating=True,
                )
            )
            rules_triggered.append("RULE_SUPPRESS_FIXED_INFRASTRUCTURE")
            return TargetRationale(
                target_id=target_id,
                detection_index=target_idx,
                latitude=lat,
                longitude=lon,
                primary_classification=TargetClassification.OFFSHORE_INFRASTRUCTURE,
                secondary_classifications=[],
                threat_level=ThreatLevel.LOW,
                overall_suspicion_score=0.05,
                tactical_summary=f"Contact confirmed as fixed offshore asset ({infra_name}). False alarm suppressed.",
                contributing_evidence=contributing,
                mitigating_evidence=mitigating,
                rules_triggered=rules_triggered,
                recommended_action="Maintain false-alarm suppression mask. No tactical interdiction required.",
                metadata={"matched_infrastructure": matched_infra},
            )

        # =====================================================================
        # 2. TRANSSHIPMENT RENDEZVOUS CHECK
        # =====================================================================
        is_transshipment = False
        partner_target_id = None
        for ev in transshipment_events:
            v1_idx = ev.get("vessel1_index")
            v2_idx = ev.get("vessel2_index")
            if target_idx in (v1_idx, v2_idx):
                is_transshipment = True
                other_idx = v2_idx if target_idx == v1_idx else v1_idx
                partner_target_id = f"CONTACT-{other_idx + 1:03d}" if other_idx is not None else "Unknown Contact"
                dist_m = ev.get("distance_meters", 350.0)
                contributing.append(
                    EvidenceItem(
                        factor="STS_PROXIMITY_ENCOUNTER",
                        description=f"Close-quarters Ship-to-Ship encounter observed: within {dist_m:.0f}m of partner {partner_target_id}.",
                        weight=0.88,
                    )
                )
                if ev.get("is_partner_dark"):
                    contributing.append(
                        EvidenceItem(
                            factor="DARK_PARTNER_RENDEZVOUS",
                            description=f"Partner vessel {partner_target_id} has disabled AIS transponder.",
                            weight=0.92,
                        )
                    )
                rules_triggered.append("RULE_STS_TRANSSHIPMENT_PROXIMITY")
                break

        # Check proximity with other detections if not explicit in events
        if not is_transshipment and (det.get("transshipment_suspect") or det.get("is_transshipment_suspect")):
            is_transshipment = True
            contributing.append(
                EvidenceItem(
                    factor="STS_PROXIMITY_ENCOUNTER",
                    description="Vessel is engaged in close-quarters rendezvous signature.",
                    weight=0.88,
                )
            )
            rules_triggered.append("RULE_STS_TRANSSHIPMENT_PROXIMITY")

        if not is_transshipment and lat is not None and lon is not None:
            for other_idx, other_d in enumerate(all_detections):
                if other_idx == target_idx:
                    continue
                o_lat = other_d.get("latitude") if other_d.get("latitude") is not None else other_d.get("lat")
                o_lon = other_d.get("longitude") if other_d.get("longitude") is not None else (other_d.get("lon") if other_d.get("lon") is not None else other_d.get("lng"))
                if o_lat is not None and o_lon is not None:
                    dy = (o_lat - lat) * 111320.0
                    dx = (o_lon - lon) * 111320.0 * 0.8
                    dist = (dx * dx + dy * dy) ** 0.5
                    if dist <= 500.0:
                        is_transshipment = True
                        partner_target_id = f"CONTACT-{other_idx + 1:03d}"
                        contributing.append(
                            EvidenceItem(
                                factor="RADAR_PROXIMITY_CLUSTER",
                                description=f"Vessel is separated by only {dist:.0f}m from {partner_target_id} in open water.",
                                weight=0.85,
                            )
                        )
                        rules_triggered.append("RULE_DEEPWATER_PROXIMITY")
                        break

        # Anchorage mitigation for STS
        if is_transshipment and in_anchorage:
            mitigating.append(
                EvidenceItem(
                    factor="DESIGNATED_ANCHORAGE_LOCATION",
                    description=f"Encounter is situated inside authorized anchorage ({anchorage_name}); lightering or bunkering likely legitimate.",
                    weight=0.65,
                    is_mitigating=True,
                )
            )

        # =====================================================================
        # 3. SPOOFED AIS CHECK
        # =====================================================================
        is_spoofed = False
        if is_course_spoofed or is_speed_spoofed or det.get("spoofing_warning"):
            is_spoofed = True
            if is_course_spoofed or (wake_heading is not None and heading_diff is not None):
                if wake_heading is not None and heading_diff is not None:
                    h_desc = f"Observed SAR wake heading ({wake_heading:.1f}°) diverges by {heading_diff:.1f}° from reported AIS heading."
                elif det.get("spoofing_warning"):
                    h_desc = str(det["spoofing_warning"])
                else:
                    h_desc = "Kinematic wake heading diverges from reported AIS heading."
                contributing.append(EvidenceItem(factor="WAKE_COURSE_DIVERGENCE", description=h_desc, weight=0.85))
                rules_triggered.append("RULE_KINEMATIC_HEADING_MISMATCH")
            if is_speed_spoofed or (wake_speed is not None and speed_diff is not None):
                if wake_speed is not None and speed_diff is not None:
                    s_desc = f"Observed SAR wake velocity ({wake_speed:.1f} kn) deviates by {speed_diff:.1f} kn from broadcast AIS speed."
                elif det.get("spoofing_warning"):
                    s_desc = str(det["spoofing_warning"])
                else:
                    s_desc = "Observed SAR wake velocity deviates from broadcast AIS speed."
                contributing.append(EvidenceItem(factor="WAKE_SPEED_DIVERGENCE", description=s_desc, weight=0.80))
                rules_triggered.append("RULE_KINEMATIC_SPEED_MISMATCH")
            if not is_course_spoofed and not is_speed_spoofed and det.get("spoofing_warning"):
                contributing.append(EvidenceItem(factor="AIS_SPOOFING_ALERT", description=str(det["spoofing_warning"]), weight=0.85))
                rules_triggered.append("RULE_SPOOFING_WARNING")

        # Identity anomaly check
        mmsi = None
        if det.get("correlated_ais"):
            mmsi = det["correlated_ais"].get("mmsi")
        elif det.get("mmsi"):
            mmsi = det.get("mmsi")

        if mmsi:
            for anom in identity_anomalies:
                if str(anom.get("mmsi")) == str(mmsi):
                    is_spoofed = True
                    anom_type = anom.get("anomaly_type", "IDENTITY_ANOMALY")
                    desc = anom.get("description") or f"AIS broadcast exhibits {anom_type}"
                    contributing.append(EvidenceItem(factor="AIS_IDENTITY_ANOMALY", description=desc, weight=0.90))
                    rules_triggered.append(f"RULE_IDENTITY_{anom_type}")

        # =====================================================================
        # 4. SOLAS SUSPECT CHECK (MANDATORY AIS CARRIAGE VIOLATION)
        # =====================================================================
        is_solas_suspect = False
        effective_len = length if length is not None and length > 0 else 0.0

        if not is_correlated and effective_len >= 50.0:
            is_solas_suspect = True
            contributing.append(
                EvidenceItem(
                    factor="SOLAS_MANDATORY_AIS_VIOLATION",
                    description=(
                        f"Vessel estimated length ({effective_len:.0f}m) exceeds 50m SOLAS Chapter V Regulation 19 "
                        f"mandatory AIS carriage threshold with no active broadcast detected."
                    ),
                    weight=0.92,
                )
            )
            rules_triggered.append("RULE_SOLAS_CARRIAGE_NONCOMPLIANCE")

            if in_shipping_lane:
                contributing.append(
                    EvidenceItem(
                        factor="TSS_NAVIGATION_WITHOUT_AIS",
                        description=f"Operating without broadcast inside designated Traffic Separation Scheme ({shipping_lane_name}).",
                        weight=0.88,
                    )
                )
                rules_triggered.append("RULE_TSS_UNBROADCAST_NAVIGATION")

        # =====================================================================
        # 5. DARK VESSEL EVALUATION
        # =====================================================================
        is_dark = False
        if not is_correlated and not is_infra:
            is_dark = is_dark_flag or (confidence >= 0.45)
            if is_dark:
                contributing.append(
                    EvidenceItem(
                        factor="RADAR_PRESENCE_WITHOUT_AIS",
                        description="Target exhibits prominent SAR radar backscatter but no matching AIS broadcast within search window.",
                        weight=0.82,
                    )
                )
                rules_triggered.append("RULE_DARK_VESSEL_RADAR_SIGNATURE")

                if wake_detected:
                    contributing.append(
                        EvidenceItem(
                            factor="POWERED_TRANSIT_CONFIRMED",
                            description=f"Active hydrodynamic wake observed ({wake_speed or 'est'} kn, {wake_heading or 'est'}°), confirming covert underway navigation.",
                            weight=0.86,
                        )
                    )
                    rules_triggered.append("RULE_ACTIVE_WAKE_CONFIRMATION")

                competing = det.get("competing_candidates") or []
                if competing:
                    nearest_dist = competing[0].get("distance_to_box_meters", 5000.0)
                    if nearest_dist > 2500.0:
                        contributing.append(
                            EvidenceItem(
                                factor="ISOLATED_TRANSPONDER_ABSENCE",
                                description=f"Nearest active transponder candidate is {nearest_dist:.0f}m away, exhausting temporal propagation.",
                                weight=0.75,
                            )
                        )
                else:
                    contributing.append(
                        EvidenceItem(
                            factor="COMPLETE_TRANSPONDER_SILENCE",
                            description="Zero AIS transponders detected within regional acquisition radius.",
                            weight=0.78,
                        )
                    )

                if confidence < 0.60:
                    mitigating.append(
                        EvidenceItem(
                            factor="MARGINAL_RADAR_CONFIDENCE",
                            description=f"Moderate radar confidence ({confidence:.2f}) leaves possibility of sea clutter or wave interference.",
                            weight=0.40,
                            is_mitigating=True,
                        )
                    )

        # =====================================================================
        # 6. COOPERATIVE VESSEL EVALUATION
        # =====================================================================
        if is_correlated and not is_spoofed and not is_transshipment:
            contributing.append(
                EvidenceItem(
                    factor="VERIFIED_AIS_CORRELATION",
                    description="Radar detection matches active AIS transponder with consistent positional and kinematic telemetry.",
                    weight=0.10,
                )
            )
            mitigating.append(
                EvidenceItem(
                    factor="COOPERATIVE_TELEMETRY",
                    description="Vessel is compliant with international tracking requirements.",
                    weight=0.90,
                    is_mitigating=True,
                )
            )
            rules_triggered.append("RULE_COOPERATIVE_TRACKING")

        # =====================================================================
        # CLASSIFICATION ASSIGNMENT & PRIORITY
        # =====================================================================
        primary_cls: TargetClassification
        if is_spoofed:
            primary_cls = TargetClassification.SPOOFED_AIS
            if is_transshipment:
                secondaries.append(TargetClassification.TRANSSHIPMENT_SUSPECT)
            if is_solas_suspect:
                secondaries.append(TargetClassification.SOLAS_SUSPECT)
        elif is_transshipment:
            primary_cls = TargetClassification.TRANSSHIPMENT_SUSPECT
            if is_solas_suspect:
                secondaries.append(TargetClassification.SOLAS_SUSPECT)
            if is_dark:
                secondaries.append(TargetClassification.DARK_VESSEL)
        elif is_solas_suspect:
            primary_cls = TargetClassification.SOLAS_SUSPECT
            if is_dark:
                secondaries.append(TargetClassification.DARK_VESSEL)
        elif is_dark:
            primary_cls = TargetClassification.DARK_VESSEL
        else:
            primary_cls = TargetClassification.COOPERATIVE_VESSEL

        # Calculate Threat Level & Suspicion Score
        contrib_score = sum(e.weight for e in contributing) / max(1, len(contributing))
        mitig_score = sum(e.weight for e in mitigating) / max(1, len(mitigating)) if mitigating else 0.0

        if primary_cls == TargetClassification.COOPERATIVE_VESSEL:
            suspicion = 0.05
            threat = ThreatLevel.LOW
            action = "Routine monitoring; track progress through maritime zone."
            summary = "Compliant cooperative vessel broadcasting valid AIS transponder data matching radar returns."
        elif primary_cls == TargetClassification.SPOOFED_AIS:
            suspicion = min(0.98, max(0.65, 0.70 + 0.25 * contrib_score - 0.2 * mitig_score))
            threat = ThreatLevel.CRITICAL if suspicion >= 0.85 else ThreatLevel.HIGH
            action = "Escalate to VTS watch officer; cross-examine coastal radar and dispatch patrol for AIS spoofing audit."
            summary = (
                f"Kinematic/identity discrepancy detected. Contact reported kinematics deviate significantly from "
                f"hydrodynamic radar wake, indicating deliberate telemetry spoofing or identity cloning."
            )
        elif primary_cls == TargetClassification.TRANSSHIPMENT_SUSPECT:
            suspicion = min(0.95, max(0.60, 0.65 + 0.30 * contrib_score - 0.3 * mitig_score))
            threat = ThreatLevel.HIGH if not in_anchorage else ThreatLevel.GUARD
            action = "Flag for customs and fisheries maritime interdiction. Review historical track for illicit STS rendezvous."
            summary = (
                f"Unusual Ship-to-Ship rendezvous observed with partner {partner_target_id or 'unknown'} "
                f"outside authorized transfer anchorages."
            )
        elif primary_cls == TargetClassification.SOLAS_SUSPECT:
            suspicion = min(0.95, max(0.70, 0.75 + 0.20 * contrib_score - 0.2 * mitig_score))
            threat = ThreatLevel.CRITICAL if effective_len >= 100.0 else ThreatLevel.HIGH
            action = "Issue maritime non-compliance report. Dispatch aerial or naval asset for visual hull identification."
            summary = (
                f"Large vessel ({effective_len:.0f}m) operating covertly without mandatory Class-A AIS broadcast, "
                f"violating IMO SOLAS Chapter V carriage requirements."
            )
        elif primary_cls == TargetClassification.DARK_VESSEL:
            suspicion = min(0.90, max(0.40, 0.50 + 0.35 * contrib_score - 0.3 * mitig_score))
            threat = ThreatLevel.HIGH if wake_detected or suspicion >= 0.70 else ThreatLevel.ELEVATED
            action = "Monitor for transponder reappearance; correlate with optical satellite or secondary radar sensors."
            summary = "Unidentified radar contact with confirmed hull signature operating without active transponder broadcast."
        else:
            suspicion = 0.10
            threat = ThreatLevel.LOW
            action = "Maintain monitoring."
            summary = "Standard contact."

        return TargetRationale(
            target_id=target_id,
            detection_index=target_idx,
            latitude=lat,
            longitude=lon,
            primary_classification=primary_cls,
            secondary_classifications=secondaries,
            threat_level=threat,
            overall_suspicion_score=suspicion,
            tactical_summary=summary,
            contributing_evidence=contributing,
            mitigating_evidence=mitigating,
            rules_triggered=rules_triggered,
            recommended_action=action,
            metadata={
                "is_correlated": is_correlated,
                "length_meters": effective_len,
                "confidence": confidence,
                "wake_detected": wake_detected,
                "is_in_shipping_lane": in_shipping_lane,
                "is_in_anchorage": in_anchorage,
            },
        )
