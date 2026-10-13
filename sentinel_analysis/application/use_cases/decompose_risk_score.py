"""Application use case for maritime tactical risk score decomposition."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

from sentinel_analysis.application.ports.scan_repository import ScanRepository
from sentinel_analysis.domain.risk_scoring import (
    RiskCategory,
    RiskComponentScore,
    RiskFactor,
    RiskScoreDecomposition,
    RiskThreatLevel,
    threat_level_from_score,
)

logger = logging.getLogger(__name__)

# Standard tactical category weights summing to 1.0
CATEGORY_WEIGHTS: dict[RiskCategory, float] = {
    RiskCategory.DARK_UNCORRELATED: 0.30,
    RiskCategory.KINEMATIC_SPOOFING: 0.25,
    RiskCategory.IDENTITY_INTEGRITY: 0.20,
    RiskCategory.BEHAVIORAL_TACTICAL: 0.15,
    RiskCategory.ENVIRONMENTAL: 0.10,
}

HIGH_RISK_FLAGS = {
    "TOGO", "TG",
    "SIERRA LEONE", "SL",
    "COOK ISLANDS", "CK",
    "CAMEROON", "CM",
    "COMOROS", "KM",
    "GABON", "GA",
    "TANZANIA", "TZ",
    "PALAU", "PW",
}


class DecomposeRiskScore:
    """Computes transparent, explainable risk score decompositions across all tactical dimensions."""

    def __init__(self, scan_repository: Optional[ScanRepository] = None) -> None:
        self._scan_repository = scan_repository

    def execute(
        self,
        scan_id: Optional[str] = None,
        detection_index: Optional[int] = None,
        detection: Optional[dict[str, Any]] = None,
        correlated_ais: Optional[dict[str, Any]] = None,
        identity_anomalies: Optional[list[dict[str, Any]]] = None,
        transshipment_events: Optional[list[dict[str, Any]]] = None,
        environmental_context: Optional[dict[str, Any]] = None,
        target_idx: Optional[int] = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Compute risk score decomposition for a scan or single detection target."""
        eff_idx = target_idx if target_idx is not None else detection_index
        # Load scan data if scan_id is provided
        detections: list[dict[str, Any]] = []
        anomalies: list[dict[str, Any]] = list(identity_anomalies or [])
        events: list[dict[str, Any]] = list(transshipment_events or [])
        env_ctx: dict[str, Any] = dict(environmental_context or {})

        if scan_id and self._scan_repository is not None:
            detections, anomalies, events, env_ctx = self._load_scan_context(
                scan_id, anomalies, events, env_ctx
            )

        # If a single detection was passed directly
        if detection is not None:
            if correlated_ais is not None and "correlated_ais" not in detection:
                det_copy = dict(detection)
                det_copy["correlated_ais"] = correlated_ais
                detections = [det_copy]
            else:
                detections = [detection]

        if not detections:
            return {
                "status": "success",
                "scan_id": scan_id,
                "total_targets": 0,
                "decompositions": [],
            }

        # If a specific detection_index is requested
        if eff_idx is not None:
            resolved_idx = None
            if 0 <= eff_idx < len(detections):
                resolved_idx = eff_idx
            elif 1 <= eff_idx <= len(detections):
                resolved_idx = eff_idx - 1

            if resolved_idx is not None:
                dec = self._decompose_single_target(
                    idx=resolved_idx,
                    det=detections[resolved_idx],
                    scan_id=scan_id or "scan",
                    all_anomalies=anomalies,
                    all_events=events,
                    env_ctx=env_ctx,
                )
                return {
                    "status": "success",
                    "scan_id": scan_id,
                    "target_index": resolved_idx,
                    "decomposition": dec.to_dict(),
                }

        # Otherwise decompose all detections in scan
        decompositions: list[RiskScoreDecomposition] = []
        for idx, det in enumerate(detections):
            dec = self._decompose_single_target(
                idx=idx,
                det=det,
                scan_id=scan_id or "scan",
                all_anomalies=anomalies,
                all_events=events,
                env_ctx=env_ctx,
            )
            decompositions.append(dec)

        # Threat counts
        threat_counts = {t.value: 0 for t in RiskThreatLevel}
        for d in decompositions:
            threat_counts[d.threat_level.value] += 1

        avg_score = (
            sum(d.total_risk_score for d in decompositions) / len(decompositions)
            if decompositions
            else 0.0
        )

        return {
            "status": "success",
            "scan_id": scan_id,
            "total_targets": len(decompositions),
            "average_risk_score": round(avg_score, 1),
            "threat_distribution": threat_counts,
            "decompositions": [d.to_dict() for d in decompositions],
        }

    def _decompose_single_target(
        self,
        idx: int,
        det: dict[str, Any],
        scan_id: str,
        all_anomalies: list[dict[str, Any]],
        all_events: list[dict[str, Any]],
        env_ctx: dict[str, Any],
    ) -> RiskScoreDecomposition:
        target_id = str(det.get("detection_id") or f"{scan_id}_target_{idx}")
        lat = det.get("lat") or det.get("latitude")
        lon = det.get("lng") or det.get("lon") or det.get("longitude")

        # Correlated AIS info
        ais_info = det.get("correlated_ais")
        has_ais = bool(ais_info and isinstance(ais_info, dict) and (ais_info.get("mmsi") or ais_info.get("vessel_id")))
        mmsi = str(ais_info.get("mmsi") or ais_info.get("vessel_id") or "") if has_ais else ""

        # Vessel dimensions and kinematic observables
        length_m = det.get("length_m") or det.get("estimated_length_m") or det.get("length")
        width_m = det.get("width_m") or det.get("estimated_beam_m") or det.get("width")
        flength = float(length_m) if length_m is not None else None
        fwidth = float(width_m) if width_m is not None else None
        wake_speed = det.get("wake_speed_knots")
        wake_heading = det.get("heading_deg") or det.get("estimated_heading_deg") or det.get("orientation")

        # Find anomalies related to this MMSI or location
        target_anomalies = []
        for a in all_anomalies:
            a_mmsi = str(a.get("mmsi") or "").strip()
            if mmsi and a_mmsi and mmsi == a_mmsi:
                target_anomalies.append(a)

        # Find rendezvous/transshipment events involving this detection
        in_transshipment = False
        partner_id = None
        for ev in all_events:
            d1 = ev.get("detection_idx_1")
            d2 = ev.get("detection_idx_2")
            if idx in (d1, d2):
                in_transshipment = True
                partner_id = d2 if idx == d1 else d1
                break

        contributing_factors: list[RiskFactor] = []
        mitigating_factors: list[RiskFactor] = []

        # -------------------------------------------------------------
        # 1. DARK / UNCORRELATED SCORE (Weight: 0.30)
        # -------------------------------------------------------------
        dark_factors: list[RiskFactor] = []
        dark_raw = 0.0

        if not has_ais:
            dark_raw += 45.0
            f1 = RiskFactor(
                name="UNREPORTED_RADAR_CONTACT",
                category=RiskCategory.DARK_UNCORRELATED,
                score_contribution=45.0,
                weight=0.50,
                description="Radar RCS contact present with total absence of correlated AIS broadcast.",
            )
            dark_factors.append(f1)
            contributing_factors.append(f1)

            # Check SOLAS carriage violation (commercial length >= 50m)
            if flength is not None and flength >= 50.0:
                dark_raw += 35.0
                f2 = RiskFactor(
                    name="SOLAS_CARRIAGE_NON_COMPLIANCE",
                    category=RiskCategory.DARK_UNCORRELATED,
                    score_contribution=35.0,
                    weight=0.35,
                    description=f"Estimated length ({flength:.1f}m) exceeds 50m SOLAS Class-A mandatory AIS threshold.",
                )
                dark_factors.append(f2)
                contributing_factors.append(f2)
            elif flength is not None and flength < 18.0:
                # Small boat mitigation
                dark_raw -= 25.0
                f_mit = RiskFactor(
                    name="SMALL_CRAFT_EXEMPTION",
                    category=RiskCategory.DARK_UNCORRELATED,
                    score_contribution=-25.0,
                    weight=0.25,
                    description=f"Estimated hull length ({flength:.1f}m) is consistent with small craft exempt from Class-A AIS.",
                    is_mitigating=True,
                )
                dark_factors.append(f_mit)
                mitigating_factors.append(f_mit)
        else:
            # Verified AIS broadcast mitigates dark risk
            f_mit = RiskFactor(
                name="VERIFIED_AIS_CARRIAGE",
                category=RiskCategory.DARK_UNCORRELATED,
                score_contribution=-30.0,
                weight=0.40,
                description=f"Active, verified AIS telemetry broadcast (MMSI: {mmsi}).",
                is_mitigating=True,
            )
            dark_factors.append(f_mit)
            mitigating_factors.append(f_mit)

        dark_raw = max(0.0, min(100.0, dark_raw))

        # -------------------------------------------------------------
        # 2. KINEMATIC SPOOFING SCORE (Weight: 0.25)
        # -------------------------------------------------------------
        kin_factors: list[RiskFactor] = []
        kin_raw = 0.0

        # Wake heading vs AIS heading divergence
        if has_ais and wake_heading is not None and ais_info.get("heading") is not None:
            try:
                w_h = float(wake_heading) % 360.0
                a_h = float(ais_info["heading"]) % 360.0
                diff_h = abs(w_h - a_h)
                ang_diff = min(diff_h, 360.0 - diff_h)
                if ang_diff >= 45.0:
                    pts = 35.0 if ang_diff < 90.0 else 55.0
                    kin_raw += pts
                    f = RiskFactor(
                        name="WAKE_HEADING_DIVERGENCE",
                        category=RiskCategory.KINEMATIC_SPOOFING,
                        score_contribution=pts,
                        weight=0.45,
                        description=f"Physical radar wake vector ({w_h:.1f}°) diverges by {ang_diff:.1f}° from broadcast AIS heading ({a_h:.1f}°).",
                    )
                    kin_factors.append(f)
                    contributing_factors.append(f)
            except (ValueError, TypeError):
                pass

        # Teleportation anomaly
        teleport_anoms = [a for a in target_anomalies if a.get("anomaly_type") == "TELEPORTATION"]
        if teleport_anoms:
            kin_raw += 60.0
            f = RiskFactor(
                name="TELEPORTATION_VIOLATION",
                category=RiskCategory.KINEMATIC_SPOOFING,
                score_contribution=60.0,
                weight=0.50,
                description=teleport_anoms[0].get("description", "Extreme kinematic jump exceeding 140+ knots."),
            )
            kin_factors.append(f)
            contributing_factors.append(f)

        # Impossible speed / jump
        speed_anoms = [a for a in target_anomalies if a.get("anomaly_type") in ("IMPOSSIBLE_SPEED", "IMPOSSIBLE_JUMP")]
        if speed_anoms and not teleport_anoms:
            kin_raw += 45.0
            f = RiskFactor(
                name="IMPOSSIBLE_SPEED_VIOLATION",
                category=RiskCategory.KINEMATIC_SPOOFING,
                score_contribution=45.0,
                weight=0.40,
                description=speed_anoms[0].get("description", "Implied vessel speed exceeds realistic hull velocity."),
            )
            kin_factors.append(f)
            contributing_factors.append(f)

        # Course discontinuity
        course_anoms = [a for a in target_anomalies if a.get("anomaly_type") == "COURSE_DISCONTINUITY"]
        if course_anoms:
            kin_raw += 30.0
            f = RiskFactor(
                name="COURSE_DISCONTINUITY",
                category=RiskCategory.KINEMATIC_SPOOFING,
                score_contribution=30.0,
                weight=0.30,
                description=course_anoms[0].get("description", "Abrupt course heading shift at speed."),
            )
            kin_factors.append(f)
            contributing_factors.append(f)

        kin_raw = max(0.0, min(100.0, kin_raw))

        # -------------------------------------------------------------
        # 3. IDENTITY INTEGRITY SCORE (Weight: 0.20)
        # -------------------------------------------------------------
        ident_factors: list[RiskFactor] = []
        ident_raw = 0.0

        if has_ais:
            # MMSI Reuse
            reuse_anoms = [a for a in target_anomalies if a.get("anomaly_type") == "MMSI_REUSE"]
            if reuse_anoms:
                ident_raw += 45.0
                f = RiskFactor(
                    name="MMSI_REUSE_DETECTED",
                    category=RiskCategory.IDENTITY_INTEGRITY,
                    score_contribution=45.0,
                    weight=0.45,
                    description=reuse_anoms[0].get("description", "Multiple distinct vessel identities broadcasting on identical MMSI."),
                )
                ident_factors.append(f)
                contributing_factors.append(f)

            # Duplicate identity
            dup_anoms = [a for a in target_anomalies if a.get("anomaly_type") == "DUPLICATE_IDENTITY"]
            if dup_anoms:
                ident_raw += 50.0
                f = RiskFactor(
                    name="DUPLICATE_IDENTITY_TRANSMISSION",
                    category=RiskCategory.IDENTITY_INTEGRITY,
                    score_contribution=50.0,
                    weight=0.50,
                    description=dup_anoms[0].get("description", "Simultaneous transmission from geographically disparate coordinates."),
                )
                ident_factors.append(f)
                contributing_factors.append(f)

            # Flag / callsign mutation
            flag_anoms = [a for a in target_anomalies if a.get("anomaly_type") == "FLAG_CALLSIGN_CHANGE"]
            if flag_anoms:
                ident_raw += 30.0
                f = RiskFactor(
                    name="FLAG_CALLSIGN_INCONSISTENCY",
                    category=RiskCategory.IDENTITY_INTEGRITY,
                    score_contribution=30.0,
                    weight=0.30,
                    description=flag_anoms[0].get("description", "Inconsistent MID country code or mutated radio callsigns."),
                )
                ident_factors.append(f)
                contributing_factors.append(f)

            # AIS Blackout Gap
            gap_anoms = [a for a in target_anomalies if a.get("anomaly_type") == "AIS_GAP"]
            if gap_anoms:
                ident_raw += 35.0
                f = RiskFactor(
                    name="AIS_BLACKOUT_GAP",
                    category=RiskCategory.IDENTITY_INTEGRITY,
                    score_contribution=35.0,
                    weight=0.35,
                    description=gap_anoms[0].get("description", "Extended unannounced AIS silence during transit."),
                )
                ident_factors.append(f)
                contributing_factors.append(f)

            # Flag of Convenience / High Risk Registry
            flag_str = (ais_info.get("flag") or ais_info.get("flag_country") or "").strip().upper()
            if flag_str in HIGH_RISK_FLAGS:
                ident_raw += 25.0
                f = RiskFactor(
                    name="HIGH_RISK_FLAG_REGISTRY",
                    category=RiskCategory.IDENTITY_INTEGRITY,
                    score_contribution=25.0,
                    weight=0.25,
                    description=f"Vessel registered under high-risk flag state ({flag_str}).",
                )
                ident_factors.append(f)
                contributing_factors.append(f)

        ident_raw = max(0.0, min(100.0, ident_raw))

        # -------------------------------------------------------------
        # 4. BEHAVIORAL & TACTICAL SCORE (Weight: 0.15)
        # -------------------------------------------------------------
        behav_factors: list[RiskFactor] = []
        behav_raw = 0.0

        if in_transshipment:
            behav_raw += 55.0
            f = RiskFactor(
                name="TRANSSHIPMENT_RENDEZVOUS",
                category=RiskCategory.BEHAVIORAL_TACTICAL,
                score_contribution=55.0,
                weight=0.55,
                description=f"Ship-to-Ship close-proximity rendezvous with target #{partner_id} in open sea.",
            )
            behav_factors.append(f)
            contributing_factors.append(f)

        loiter_anoms = [a for a in target_anomalies if a.get("anomaly_type") == "LOITERING"]
        if loiter_anoms:
            behav_raw += 35.0
            f = RiskFactor(
                name="ANOMALOUS_LOITERING",
                category=RiskCategory.BEHAVIORAL_TACTICAL,
                score_contribution=35.0,
                weight=0.35,
                description=loiter_anoms[0].get("description", "Prolonged slow drifting outside designated anchorages."),
            )
            behav_factors.append(f)
            contributing_factors.append(f)

        convoy_anoms = [a for a in target_anomalies if a.get("anomaly_type") == "CONVOY"]
        if convoy_anoms:
            behav_raw += 25.0
            f = RiskFactor(
                name="CONVOY_FORMATION",
                category=RiskCategory.BEHAVIORAL_TACTICAL,
                score_contribution=25.0,
                weight=0.25,
                description=convoy_anoms[0].get("description", "Vessel transiting in close coordinated formation."),
            )
            behav_factors.append(f)
            contributing_factors.append(f)

        route_anoms = [a for a in target_anomalies if a.get("anomaly_type") == "UNUSUAL_ROUTE"]
        if route_anoms:
            behav_raw += 30.0
            f = RiskFactor(
                name="UNUSUAL_ROUTE_SINUOSITY",
                category=RiskCategory.BEHAVIORAL_TACTICAL,
                score_contribution=30.0,
                weight=0.30,
                description=route_anoms[0].get("description", "High track sinuosity indicating search or evasive maneuvering."),
            )
            behav_factors.append(f)
            contributing_factors.append(f)

        behav_raw = max(0.0, min(100.0, behav_raw))

        # -------------------------------------------------------------
        # 5. ENVIRONMENTAL CONTEXT SCORE (Weight: 0.10)
        # -------------------------------------------------------------
        env_factors: list[RiskFactor] = []
        env_raw = 0.0

        # Check environmental layers
        lanes = env_ctx.get("shipping_lanes", [])
        if lanes and isinstance(lanes, list):
            in_lane = False
            for lane in lanes:
                if lane.get("intersects_target"):
                    in_lane = True
                    break
            if in_lane:
                f_mit = RiskFactor(
                    name="AUTHORIZED_SHIPPING_LANE",
                    category=RiskCategory.ENVIRONMENTAL,
                    score_contribution=-20.0,
                    weight=0.30,
                    description="Vessel navigating directly within recognized commercial shipping corridor.",
                    is_mitigating=True,
                )
                env_factors.append(f_mit)
                mitigating_factors.append(f_mit)
                env_raw -= 20.0
            else:
                env_raw += 20.0
                f = RiskFactor(
                    name="OFF_CORRIDOR_NAVIGATION",
                    category=RiskCategory.ENVIRONMENTAL,
                    score_contribution=20.0,
                    weight=0.25,
                    description="Vessel transiting outside established commercial shipping corridors.",
                )
                env_factors.append(f)
                contributing_factors.append(f)

        # Marine Protected Areas
        mpas = env_ctx.get("marine_protected_areas", [])
        if mpas:
            for mpa in mpas:
                if mpa.get("intersects_target"):
                    env_raw += 40.0
                    f = RiskFactor(
                        name="MPA_INCURSION",
                        category=RiskCategory.ENVIRONMENTAL,
                        score_contribution=40.0,
                        weight=0.40,
                        description=f"Operating inside protected marine sanctuary: {mpa.get('name', 'Marine Sanctuary')}.",
                    )
                    env_factors.append(f)
                    contributing_factors.append(f)
                    break

        # Critical Infrastructure (Platforms/Wind farms)
        infra = env_ctx.get("infrastructure_proximity", [])
        if infra:
            for item in infra:
                dist = item.get("distance_m", 9999)
                if dist < 800:
                    env_raw += 30.0
                    f = RiskFactor(
                        name="INFRASTRUCTURE_PROXIMITY_ALERT",
                        category=RiskCategory.ENVIRONMENTAL,
                        score_contribution=30.0,
                        weight=0.35,
                        description=f"Critical offshore infrastructure proximity ({dist:.0f}m from {item.get('name', 'platform')}).",
                    )
                    env_factors.append(f)
                    contributing_factors.append(f)
                    break

        env_raw = max(0.0, min(100.0, env_raw))

        # -------------------------------------------------------------
        # COMPOSITE WEIGHTED SCORE
        # -------------------------------------------------------------
        component_scores: dict[str, RiskComponentScore] = {
            RiskCategory.DARK_UNCORRELATED.value: RiskComponentScore(
                category=RiskCategory.DARK_UNCORRELATED,
                raw_score=dark_raw,
                weight=CATEGORY_WEIGHTS[RiskCategory.DARK_UNCORRELATED],
                weighted_score=dark_raw * CATEGORY_WEIGHTS[RiskCategory.DARK_UNCORRELATED],
                factors=dark_factors,
            ),
            RiskCategory.KINEMATIC_SPOOFING.value: RiskComponentScore(
                category=RiskCategory.KINEMATIC_SPOOFING,
                raw_score=kin_raw,
                weight=CATEGORY_WEIGHTS[RiskCategory.KINEMATIC_SPOOFING],
                weighted_score=kin_raw * CATEGORY_WEIGHTS[RiskCategory.KINEMATIC_SPOOFING],
                factors=kin_factors,
            ),
            RiskCategory.IDENTITY_INTEGRITY.value: RiskComponentScore(
                category=RiskCategory.IDENTITY_INTEGRITY,
                raw_score=ident_raw,
                weight=CATEGORY_WEIGHTS[RiskCategory.IDENTITY_INTEGRITY],
                weighted_score=ident_raw * CATEGORY_WEIGHTS[RiskCategory.IDENTITY_INTEGRITY],
                factors=ident_factors,
            ),
            RiskCategory.BEHAVIORAL_TACTICAL.value: RiskComponentScore(
                category=RiskCategory.BEHAVIORAL_TACTICAL,
                raw_score=behav_raw,
                weight=CATEGORY_WEIGHTS[RiskCategory.BEHAVIORAL_TACTICAL],
                weighted_score=behav_raw * CATEGORY_WEIGHTS[RiskCategory.BEHAVIORAL_TACTICAL],
                factors=behav_factors,
            ),
            RiskCategory.ENVIRONMENTAL.value: RiskComponentScore(
                category=RiskCategory.ENVIRONMENTAL,
                raw_score=env_raw,
                weight=CATEGORY_WEIGHTS[RiskCategory.ENVIRONMENTAL],
                weighted_score=env_raw * CATEGORY_WEIGHTS[RiskCategory.ENVIRONMENTAL],
                factors=env_factors,
            ),
        }

        total_score = sum(c.weighted_score for c in component_scores.values())
        # Floor adjustment: if an individual category has severe tactical threat (raw >= 70),
        # prevent the isolated critical risk from being masked as low threat.
        max_raw = max((c.raw_score for c in component_scores.values()), default=0.0)
        if max_raw >= 70.0:
            total_score = max(total_score, max_raw * 0.40)

        total_score = max(0.0, min(100.0, total_score))
        threat_lvl = threat_level_from_score(total_score)

        # Identify primary driver
        best_comp = max(component_scores.values(), key=lambda c: c.weighted_score)
        primary_driver = best_comp.category.value if best_comp.weighted_score > 5.0 else "NOMINAL_TRANSIT"

        # Formulate analyst summary and actions
        analyst_summary, rec_actions = self._build_tactical_assessment(
            threat_lvl=threat_lvl,
            primary_driver=primary_driver,
            has_ais=has_ais,
            in_transshipment=in_transshipment,
            flength=flength,
            contributing=contributing_factors,
            mitigating=mitigating_factors,
        )

        return RiskScoreDecomposition(
            target_id=target_id,
            detection_index=idx,
            latitude=float(lat) if lat is not None else None,
            longitude=float(lon) if lon is not None else None,
            total_risk_score=total_score,
            threat_level=threat_lvl,
            primary_driver=primary_driver,
            component_scores=component_scores,
            contributing_factors=contributing_factors,
            mitigating_factors=mitigating_factors,
            analyst_summary=analyst_summary,
            recommended_actions=rec_actions,
            metadata=det,
        )

    def _build_tactical_assessment(
        self,
        threat_lvl: RiskThreatLevel,
        primary_driver: str,
        has_ais: bool,
        in_transshipment: bool,
        flength: Optional[float],
        contributing: list[RiskFactor],
        mitigating: list[RiskFactor],
    ) -> tuple[str, list[str]]:
        actions: list[str] = []

        if threat_lvl in (RiskThreatLevel.CRITICAL, RiskThreatLevel.HIGH):
            if not has_ais:
                summary = f"High-confidence dark target without AIS carriage ({f'{flength:.0f}m' if flength else 'vessel'})."
                actions.append("Queue high-resolution optical / UAV tasking")
                actions.append("Issue dark vessel intercept alert to regional maritime authority")
            elif in_transshipment:
                summary = "Active Ship-to-Ship transshipment rendezvous detected outside authorized anchorages."
                actions.append("Dispatch maritime patrol craft for on-scene verification")
                actions.append("Correlate bunker transfer manifest and cargo declarations")
            else:
                summary = f"Severe tactical threat driven by {primary_driver.replace('_', ' ').lower()}."
                actions.append("Flag vessel MMSI for enhanced surveillance")
                actions.append("Review multi-pass re-identification history")
        elif threat_lvl == RiskThreatLevel.ELEVATED:
            summary = f"Elevated anomaly score primarily driven by {primary_driver.replace('_', ' ').lower()}."
            actions.append("Maintain automated AIS track monitoring")
            actions.append("Inspect upcoming satellite passes for re-identification")
        else:
            summary = "Nominal commercial maritime target with verified telemetry or legitimate exemptions."
            actions.append("Continue standard passive monitoring")

        return summary, actions

    def _load_scan_context(
        self,
        scan_id: str,
        anomalies: list[dict[str, Any]],
        events: list[dict[str, Any]],
        env_ctx: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
        detections: list[dict[str, Any]] = []
        try:
            scan = self._scan_repository.get_scan(scan_id)
            if scan and scan.image_path:
                scan_dir = Path(scan.image_path).parent

                # 1. Detections
                det_path = scan_dir / "detection_results.json"
                if det_path.is_file():
                    try:
                        d_data = json.loads(det_path.read_text(encoding="utf-8"))
                        detections = d_data.get("detections", [])
                    except Exception:
                        pass

                # 2. Anomalies
                if not anomalies:
                    anom_path = scan_dir / "identity_anomalies.json"
                    if anom_path.is_file():
                        try:
                            a_data = json.loads(anom_path.read_text(encoding="utf-8"))
                            anomalies = a_data.get("anomalies", [])
                        except Exception:
                            pass

                # 3. Transshipment
                if not events:
                    trans_path = scan_dir / "transshipment.json"
                    if trans_path.is_file():
                        try:
                            t_data = json.loads(trans_path.read_text(encoding="utf-8"))
                            events = t_data.get("rendezvous_pairs", [])
                        except Exception:
                            pass

                # 4. Environmental
                if not env_ctx:
                    env_path = scan_dir / "environmental_context.json"
                    if env_path.is_file():
                        try:
                            env_ctx = json.loads(env_path.read_text(encoding="utf-8"))
                        except Exception:
                            pass
        except Exception as exc:
            logger.warning("Could not load scan context for risk decomposition: %s", exc)

        return detections, anomalies, events, env_ctx
