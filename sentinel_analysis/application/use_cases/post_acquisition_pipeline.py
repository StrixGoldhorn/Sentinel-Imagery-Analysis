"""Automated post-acquisition intelligence pipeline.

Executes ship detection, live AIS track correlation, visual/GIS artifact generation,
intelligence briefing (PDF) compilation, and geospatial product exports automatically
upon SAR image acquisition.
"""

from datetime import datetime, timezone
import json
import logging
import math
from collections.abc import Callable
from pathlib import Path
from typing import Any, Optional
import uuid

from sentinel_analysis.application.ports.scan_repository import ScanRepository
from sentinel_analysis.application.use_cases.correlate_ais_detections import CorrelateDetectionsWithAIS
from sentinel_analysis.application.use_cases.cross_validate_optical import CrossValidateOptical
from sentinel_analysis.application.use_cases.detect_sar_changes import ComputeSARChangeDetection
from sentinel_analysis.application.use_cases.detect_ships import DetectShips
from sentinel_analysis.application.use_cases.detect_transshipment import DetectTransshipmentAnomalies
from sentinel_analysis.application.use_cases.export_geospatial import ExportGeospatial
from sentinel_analysis.application.use_cases.generate_briefing import GenerateIntelligenceBrief
from sentinel_analysis.application.use_cases.generate_dem import GenerateDEM
from sentinel_analysis.application.use_cases.geofence_monitor import GeofenceMonitor
from sentinel_analysis.application.use_cases.manage_alerts import DispatchMaritimeAlert
from sentinel_analysis.domain.entities import MaritimeAlert, Scan
from sentinel_analysis.domain.provenance import build_preprocessing_provenance

logger = logging.getLogger(__name__)


class PostAcquisitionPipeline:
    """Orchestrates the automated end-to-end maritime analysis pipeline on newly acquired SAR imagery."""

    def __init__(
        self,
        scan_repository: ScanRepository,
        detect_ships: Optional[DetectShips] = None,
        correlate_ais: Optional[CorrelateDetectionsWithAIS] = None,
        generate_dem: Optional[GenerateDEM] = None,
        generate_briefing: Optional[GenerateIntelligenceBrief] = None,
        export_geospatial: Optional[ExportGeospatial] = None,
        settings_repository: Optional[Any] = None,
        output_root: Optional[Path | str] = None,
        detection_saver: Optional[Any] = None,
        detect_transshipment: Optional[DetectTransshipmentAnomalies] = None,
        geofence_monitor: Optional[GeofenceMonitor] = None,
        dispatch_alert: Optional[DispatchMaritimeAlert] = None,
        cross_validate_optical: Optional[CrossValidateOptical] = None,
        detect_sar_changes: Optional[ComputeSARChangeDetection] = None,
    ) -> None:
        self._scan_repository = scan_repository
        self._detect_ships = detect_ships
        self._correlate_ais = correlate_ais
        self._generate_dem = generate_dem
        self._generate_briefing = generate_briefing
        self._export_geospatial = export_geospatial
        self._settings_repository = settings_repository
        self._output_root = Path(output_root).resolve() if output_root else None
        self._detection_saver = detection_saver
        self._detect_transshipment = detect_transshipment
        self._geofence_monitor = geofence_monitor
        self._dispatch_alert = dispatch_alert
        self._cross_validate_optical = cross_validate_optical
        self._detect_sar_changes = detect_sar_changes

    def _get_setting(self, key: str, default: Any) -> Any:
        if self._settings_repository is not None and hasattr(self._settings_repository, "get"):
            try:
                val = self._settings_repository.get(key)
                if val is not None:
                    return val
            except Exception:
                pass
        return default

    def _resolve_image_url(self, scan: Scan) -> str:
        root = self._output_root
        if root is None and hasattr(self._scan_repository, "root"):
            root = getattr(self._scan_repository, "root")
        if root is not None:
            try:
                rel = Path(scan.image_path).resolve().relative_to(root.resolve())
                return f"/media/scans/{rel.as_posix()}"
            except Exception:
                pass
        return f"/media/scans/{scan.folder_name}/images/{Path(scan.image_path).name}"

    def execute(
        self,
        scan: Scan,
        threshold: Optional[int] = None,
        coastal_buffer: Optional[int] = None,
        ais_distance: Optional[float] = None,
        dem_enabled: Optional[bool] = None,
        enable_kinematics: Optional[bool] = None,
        optical_validation_enabled: Optional[bool] = None,
        sar_change_detection_enabled: Optional[bool] = None,
        progress_callback: Optional[Callable[[float, str], None]] = None,
    ) -> dict[str, Any]:
        """Execute the post-acquisition analysis pipeline end-to-end."""
        def report(pct: float, msg: str) -> None:
            if progress_callback is not None:
                try:
                    progress_callback(pct, msg)
                except Exception:
                    pass

        # 1. Resolve parameters
        report(5, "Initializing post-acquisition analysis")
        if threshold is None:
            threshold = int(self._get_setting("threshold", 40))
        if coastal_buffer is None:
            coastal_buffer = int(self._get_setting("coastal_buffer_pixels", 81))
        if ais_distance is None:
            ais_distance = float(self._get_setting("ais_correlation_distance_meters", 100.0))
        if dem_enabled is None:
            dem_enabled = bool(self._get_setting("dem_land_mask_enabled", True))
        if optical_validation_enabled is None:
            optical_validation_enabled = bool(self._get_setting("optical_validation_enabled", True))
        if sar_change_detection_enabled is None:
            sar_change_detection_enabled = bool(self._get_setting("sar_change_detection_enabled", True))

        image_path = Path(scan.image_path)
        folder_dir = image_path.parent

        # 2. DEM land masking check and on-demand generation
        dem_candidates = list(folder_dir.glob("*_stitched_dem.png")) or list(folder_dir.glob("*_dem.png"))
        if dem_enabled and not dem_candidates and self._generate_dem is not None:
            report(15, "Generating digital elevation model (DEM) for coastal land masking")
            dem_target = folder_dir / f"{scan.folder_name}_stitched_dem.png"
            try:
                if self._generate_dem.execute(scan.bbox, dem_target):
                    if dem_target.is_file():
                        dem_candidates = [dem_target]
            except Exception as exc:
                logger.warning("DEM land mask generation skipped or failed for %s: %s", scan.folder_name, exc)

        dem_path = dem_candidates[0] if (dem_enabled and dem_candidates) else None

        # 3. Ship detection model with dual-polarization and wake hydrodynamic processing
        report(30, "Running ship detection model")
        det_result = None
        vh_candidates = (
            list(folder_dir.glob("*_vh.png"))
            + list(folder_dir.glob("*_vh.tif"))
            + list(folder_dir.glob("*_VH.png"))
            + list(folder_dir.glob("*_VH.tif"))
        )
        vh_path = vh_candidates[0] if vh_candidates else None
        enable_wake = bool(self._get_setting("enable_wake_detection", True))

        if self._detect_ships is not None:
            try:
                try:
                    det_result = self._detect_ships.execute(
                        image_path,
                        dem_path,
                        threshold,
                        coastal_buffer=coastal_buffer,
                        enable_wake_detection=enable_wake,
                        vh_path=vh_path,
                    )
                except TypeError:
                    det_result = self._detect_ships.execute(
                        image_path,
                        dem_path,
                        threshold,
                        coastal_buffer=coastal_buffer,
                    )
            except Exception as exc:
                logger.warning("Ship detection model execution failed for %s: %s", scan.folder_name, exc, exc_info=True)

        img_width = det_result.image_width if det_result else 1
        img_height = det_result.image_height if det_result else 1
        raw_detections = det_result.detections if det_result else []

        # 4. Live AIS track correlation & kinematic interpolation
        report(50, "Correlating live AIS tracks & kinematics")
        enriched_detections: list[dict[str, Any]] = []
        ghost_vessels: list[dict[str, Any]] = []

        if self._correlate_ais is not None and raw_detections:
            try:
                enriched = self._correlate_ais.execute(
                    raw_detections,
                    scan,
                    img_width,
                    img_height,
                    tolerance_meters=ais_distance,
                    enable_kinematics=enable_kinematics,
                )
                if isinstance(enriched, list) and len(enriched) == len(raw_detections):
                    enriched_detections = enriched
                ghost_vessels = getattr(self._correlate_ais, "last_ghost_vessels", [])
            except Exception as exc:
                logger.warning("AIS correlation failed for scan %s: %s", scan.folder_name, exc, exc_info=True)

        if not enriched_detections and raw_detections:
            for idx, item in enumerate(raw_detections):
                enriched_detections.append({
                    "index": idx,
                    "x": item.x,
                    "y": item.y,
                    "width": item.width,
                    "height": item.height,
                    "confidence": item.confidence,
                    "angle": item.angle,
                    "length": item.length,
                    "beam": item.beam,
                    "center_x": item.center_x,
                    "center_y": item.center_y,
                    "polygon_points": getattr(item, "polygon_points", None),
                    "wake_detected": getattr(item, "wake_detected", None),
                    "wake_heading": getattr(item, "wake_heading", None),
                    "wake_speed_knots": getattr(item, "wake_speed_knots", None),
                    "wake_confidence": getattr(item, "wake_confidence", None),
                    "is_speed_spoofed": getattr(item, "is_speed_spoofed", None),
                    "is_course_spoofed": getattr(item, "is_course_spoofed", None),
                    "vessel_class": getattr(item, "vessel_class", None),
                    "classification_confidence": getattr(item, "classification_confidence", None),
                    "provenance": getattr(item, "provenance", None),
                    "correlation_status": "uncorrelated",
                    "is_correlated": False,
                    "is_dark_vessel": False,
                    "dark_vessel_risk": "NOMINAL",
                    "dark_vessel_score": 0.0,
                    "correlated_ais": None,
                })

        # Enrich all detections with sensor, orbit, product ID, and preprocessing provenance
        for det_dict in enriched_detections:
            existing_prov = det_dict.get("provenance") or {}
            det_prov = build_preprocessing_provenance(
                scan=scan,
                image_path=scan.image_path,
                dem_path=dem_path,
                vh_path=vh_path,
                metadata=scan.metadata,
                thresholds={
                    "threshold": threshold,
                    "coastal_buffer_pixels": coastal_buffer,
                },
                **existing_prov,
            )
            det_dict["provenance"] = det_prov
            for k in (
                "orbit",
                "product_id",
                "polarization",
                "processing_baseline",
                "calibration_method",
                "terrain_correction",
                "dem",
                "speckle_filtering",
                "pixel_spacing",
                "model_version",
                "thresholds",
                "source_checksum",
                "source_checksums",
            ):
                det_dict[k] = det_prov[k]

        # 5. Operational Intelligence: EEZ & MPA Geofencing, STS Transshipment & Anomaly Analysis
        report(65, "Evaluating EEZ & MPA geofencing rules and STS transshipment patterns")
        geofence_engine = self._geofence_monitor or GeofenceMonitor()
        geofence_res: dict[str, Any] = {}
        try:
            geofence_res = geofence_engine.evaluate_detections(enriched_detections, scan_bbox=scan.bbox)
            geofence_file = folder_dir / f"{scan.folder_name}_geofence.json"
            geofence_file.write_text(json.dumps(geofence_res, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.warning("Geofence evaluation failed for %s: %s", scan.folder_name, exc, exc_info=True)

        transshipment_engine = self._detect_transshipment or DetectTransshipmentAnomalies(self._scan_repository)
        transshipment_res: dict[str, Any] = {}
        try:
            transshipment_res = transshipment_engine.execute_from_detections(
                enriched_detections,
                scan_bbox=scan.bbox,
                image_width=img_width,
                image_height=img_height,
            )
            trans_file = folder_dir / f"{scan.folder_name}_transshipment.json"
            trans_file.write_text(json.dumps(transshipment_res, indent=2), encoding="utf-8")
            if "geojson" in transshipment_res:
                trans_geojson = folder_dir / f"{scan.folder_name}_transshipment.geojson"
                trans_geojson.write_text(json.dumps(transshipment_res["geojson"], indent=2), encoding="utf-8")

            # Tag detections with transshipment rendezvous status
            for r_event in transshipment_res.get("rendezvous_events", []):
                idx_a = r_event.get("vessel_a_index")
                idx_b = r_event.get("vessel_b_index")
                for t_idx, p_id, p_dark in [
                    (idx_a, r_event.get("vessel_b_identifier"), r_event.get("vessel_b_is_dark")),
                    (idx_b, r_event.get("vessel_a_identifier"), r_event.get("vessel_a_is_dark")),
                ]:
                    if t_idx is not None and 0 <= t_idx < len(enriched_detections):
                        det = enriched_detections[t_idx]
                        det["is_transshipment_suspect"] = True
                        if "transshipment_events" not in det:
                            det["transshipment_events"] = []
                        det["transshipment_events"].append({
                            "partner": p_id,
                            "partner_is_dark": p_dark,
                            "distance_meters": r_event.get("distance_meters"),
                            "risk_level": r_event.get("risk_level"),
                            "narrative": r_event.get("narrative"),
                        })
        except Exception as exc:
            logger.warning("Transshipment detection failed for %s: %s", scan.folder_name, exc, exc_info=True)

        # Automated Webhook Alert Dispatch
        if self._dispatch_alert is not None:
            try:
                critical_dark = sum(
                    1 for d in enriched_detections
                    if d.get("is_dark_vessel", False) and (d.get("dark_vessel_risk") == "HIGH" or d.get("is_solas_suspect", False))
                )
                crit_sts = int(transshipment_res.get("critical_rendezvous_count", 0))
                crit_geo = int(geofence_res.get("critical_breaches", 0))
                if critical_dark > 0 or crit_sts > 0 or crit_geo > 0:
                    summary_parts = []
                    if critical_dark > 0:
                        summary_parts.append(f"{critical_dark} critical dark vessel(s)")
                    if crit_sts > 0:
                        summary_parts.append(f"{crit_sts} STS rendezvous event(s)")
                    if crit_geo > 0:
                        summary_parts.append(f"{crit_geo} MPA/geofence breach(es)")

                    alert = MaritimeAlert(
                        alert_id=f"alt-{uuid.uuid4().hex[:10]}",
                        event_type="OPERATIONAL_INTELLIGENCE_EVENT",
                        severity="CRITICAL",
                        title=f"Critical Maritime Intelligence Alert: {scan.folder_name}",
                        summary=f"Automated threat detection in {scan.folder_name}: {', '.join(summary_parts)}.",
                        details={
                            "scan_folder": scan.folder_name,
                            "critical_dark_vessels": critical_dark,
                            "critical_sts_rendezvous": crit_sts,
                            "critical_geofence_breaches": crit_geo,
                            "total_detections": len(enriched_detections),
                        },
                        timestamp=datetime.now(timezone.utc),
                    )
                    self._dispatch_alert.execute(alert)
            except Exception as exc:
                logger.warning("Automated webhook alert dispatch failed for %s: %s", scan.folder_name, exc)

        # 6. Visual overlays, structured JSON, GeoJSON, and World files (.pgw, .prj)
        report(75, "Generating visual overlays and GIS metadata")
        saved_info: dict[str, Any] = {}
        if self._detection_saver is not None:
            try:
                saved_info = self._detection_saver(
                    image_path=image_path,
                    detections=enriched_detections,
                    image_width=img_width,
                    image_height=img_height,
                    metadata={
                        "threshold": threshold,
                        "coastal_buffer": coastal_buffer,
                        "land_masked": bool(dem_path is not None),
                        "ais_correlation_distance": ais_distance,
                    },
                    bbox=scan.bbox,
                    ghost_vessels=ghost_vessels,
                )
            except Exception as exc:
                logger.warning("Saving detection results failed for scan %s: %s", scan.folder_name, exc, exc_info=True)

        # 6.2 Multi-Sensor Sentinel-2 Optical Cross-Validation & NDWI
        optical_res: dict[str, Any] = {}
        if self._cross_validate_optical is not None and optical_validation_enabled:
            report(78, "Cross-validating detections with Sentinel-2 optical scenes")
            try:
                optical_res = self._cross_validate_optical.execute(scan.folder_name)
                opt_results = optical_res.get("results", [])
                for d, v in zip(enriched_detections, opt_results):
                    d["optical_status"] = v.get("status")
                    d["optical_confirmed"] = v.get("optical_confirmed")
                    d["optical_confidence"] = v.get("optical_confidence")
                    d["optical_scene_id"] = v.get("scene_id")
                    d["optical_cloud_cover"] = v.get("cloud_cover")
                    d["optical_time_delta_hours"] = v.get("time_delta_hours")
                    d["optical_details"] = v.get("details")
            except Exception as exc:
                logger.warning("Optical cross-validation failed for %s: %s", scan.folder_name, exc, exc_info=True)

        # 6.4 Multi-Temporal Repeat-Pass SAR Coherence & Change Detection
        change_res: dict[str, Any] = {}
        if self._detect_sar_changes is not None and sar_change_detection_enabled:
            report(81, "Analyzing multi-temporal SAR coherence and change detection")
            try:
                all_scans = list(self._scan_repository.list())
                curr_time = scan.acquisition.acquired_at
                prior_scans = [
                    s for s in all_scans
                    if s.folder_name != scan.folder_name
                    and s.acquisition.acquired_at < curr_time
                    and Path(s.image_path).is_file()
                ]
                if prior_scans:
                    def _center(b: Any) -> tuple[float, float]:
                        return ((b.min_latitude + b.max_latitude) / 2.0, (b.min_longitude + b.max_longitude) / 2.0)
                    c_curr = _center(scan.bbox)
                    best_ref = None
                    min_dist = float("inf")
                    for ps in prior_scans:
                        c_p = _center(ps.bbox)
                        d_sq = (c_p[0] - c_curr[0]) ** 2 + (c_p[1] - c_curr[1]) ** 2
                        if d_sq < 0.25 and d_sq < min_dist:
                            min_dist = d_sq
                            best_ref = ps

                    if best_ref is not None:
                        change_res = self._detect_sar_changes.execute(
                            target_folder=scan.folder_name,
                            reference_folder=best_ref.folder_name,
                        )
                        cps = change_res.get("change_points", [])
                        for d in enriched_detections:
                            det_px = float(d.get("center_x") or d.get("x") or 0.0)
                            det_py = float(d.get("center_y") or d.get("y") or 0.0)
                            closest_cp = None
                            min_cp_dist = 40.0
                            for cp in cps:
                                dist = math.hypot(float(cp.get("x", 0.0)) - det_px, float(cp.get("y", 0.0)) - det_py)
                                if dist < min_cp_dist:
                                    min_cp_dist = dist
                                    closest_cp = cp
                            if closest_cp is not None:
                                d["temporal_change_type"] = closest_cp.get("change_type")
                                d["temporal_magnitude_db"] = closest_cp.get("magnitude_db")

                        if cps:
                            try:
                                det_json_path = folder_dir / "detection_results.json"
                                if det_json_path.is_file():
                                    jdata = json.loads(det_json_path.read_text(encoding="utf-8"))
                                    jdata["detections"] = enriched_detections
                                    jdata["sar_change_summary"] = {
                                        "reference_scan": change_res.get("reference_scan"),
                                        "arrived_count": change_res.get("arrived_count", 0),
                                        "departed_count": change_res.get("departed_count", 0),
                                        "persistent_structures_count": change_res.get("persistent_structures_count", 0),
                                        "change_map_filename": change_res.get("change_map_filename"),
                                    }
                                    det_json_path.write_text(json.dumps(jdata, indent=2), encoding="utf-8")
                                det_gj_path = folder_dir / "detections.geojson"
                                if det_gj_path.is_file():
                                    gjdata = json.loads(det_gj_path.read_text(encoding="utf-8"))
                                    for feat, ed in zip(gjdata.get("features", []), enriched_detections):
                                        if "properties" in feat and isinstance(feat["properties"], dict):
                                            feat["properties"]["temporal_change_type"] = ed.get("temporal_change_type")
                                    det_gj_path.write_text(json.dumps(gjdata, indent=2), encoding="utf-8")
                            except Exception as exc:
                                logger.debug("Updating detection files with SAR change data failed: %s", exc)
            except Exception as exc:
                logger.warning("SAR change detection failed for %s: %s", scan.folder_name, exc, exc_info=True)

        # 7. PDF Maritime Intelligence Briefing
        report(85, "Generating Maritime Intelligence Briefing (PDF)")
        briefing_pdf_path: Optional[Path] = None
        if self._generate_briefing is not None:
            try:
                briefing_pdf_path = self._generate_briefing.execute(scan.folder_name)
            except Exception as exc:
                logger.warning("Automated PDF briefing generation failed for %s: %s", scan.folder_name, exc, exc_info=True)

        # 8. Geospatial exports (GeoTIFF & STAC Item)
        report(92, "Exporting GeoTIFF and STAC metadata")
        geotiff_path: Optional[Path] = None
        stac_path: Optional[Path] = None
        if self._export_geospatial is not None:
            try:
                geotiff_path = self._export_geospatial.export_geotiff(scan.folder_name)
            except Exception as exc:
                logger.warning("GeoTIFF export failed for %s: %s", scan.folder_name, exc)

            try:
                stac_item = self._export_geospatial.export_stac_item(scan.folder_name)
                stac_path = folder_dir / f"{scan.folder_name}_stac.json"
                stac_path.write_text(json.dumps(stac_item, indent=2), encoding="utf-8")
            except Exception as exc:
                logger.warning("STAC export failed for %s: %s", scan.folder_name, exc)

        # 9. Update scan metadata and persist
        report(96, "Finalizing scan intelligence catalog")
        inside_box_count = sum(1 for d in enriched_detections if d.get("correlation_status") == "inside_box")
        outside_box_count = sum(1 for d in enriched_detections if d.get("correlation_status") == "outside_box")
        uncorrelated_count = sum(1 for d in enriched_detections if d.get("correlation_status") == "uncorrelated")
        correlated_count = inside_box_count + outside_box_count

        scan_meta = dict(scan.metadata or {})
        scan_meta["latest_cv_results"] = {
            "detected_at": saved_info.get("timestamp"),
            "ship_count": saved_info.get("ship_count", len(enriched_detections)),
            "correlated_count": saved_info.get("correlated_count", correlated_count),
            "inside_box_count": saved_info.get("inside_box_count", inside_box_count),
            "outside_box_count": saved_info.get("outside_box_count", outside_box_count),
            "uncorrelated_count": saved_info.get("uncorrelated_count", uncorrelated_count),
            "dark_vessel_count": saved_info.get("dark_vessel_count", 0),
            "critical_dark_count": saved_info.get("critical_dark_count", 0),
            "ghost_vessel_count": saved_info.get("ghost_vessel_count", len(ghost_vessels)),
            "threshold": threshold,
            "coastal_buffer": coastal_buffer,
            "land_masked": bool(dem_path is not None),
            "detected_image": saved_info.get("detected_image_name"),
            "detections_json": saved_info.get("detections_json_name"),
            "transshipment_rendezvous_count": transshipment_res.get("rendezvous_count", 0),
            "critical_rendezvous_count": transshipment_res.get("critical_rendezvous_count", 0),
            "loitering_count": transshipment_res.get("loitering_count", 0),
            "geofence_breach_count": geofence_res.get("total_breaches", 0),
            "critical_geofence_breach_count": geofence_res.get("critical_breaches", 0),
            "transshipment_threat_level": transshipment_res.get("overall_threat_level", "LOW"),
            "optical_validation": {
                "status": optical_res.get("status"),
                "scenes_found": optical_res.get("scenes_found", 0),
                "confirmed_count": optical_res.get("confirmed_count", 0),
                "land_false_alarm_count": optical_res.get("land_false_alarm_count", 0),
                "best_scene_id": optical_res.get("best_scene", {}).get("scene_id") if isinstance(optical_res.get("best_scene"), dict) else None,
            },
            "sar_change_detection": {
                "status": change_res.get("status"),
                "reference_scan": change_res.get("reference_scan"),
                "arrived_count": change_res.get("arrived_count", 0),
                "departed_count": change_res.get("departed_count", 0),
                "persistent_structures_count": change_res.get("persistent_structures_count", 0),
                "change_map_filename": change_res.get("change_map_filename"),
            },
        }
        if briefing_pdf_path:
            scan_meta["briefing_pdf"] = Path(briefing_pdf_path).name
        if geotiff_path:
            scan_meta["geotiff"] = Path(geotiff_path).name
        if stac_path:
            scan_meta["stac"] = Path(stac_path).name
        route_img = folder_dir / f"{scan.folder_name}_route_correlation.png"
        if route_img.is_file():
            scan_meta["route_correlation_image"] = route_img.name

        try:
            self._scan_repository.save(
                Scan(
                    folder_name=scan.folder_name,
                    bbox=scan.bbox,
                    acquisition=scan.acquisition,
                    image_path=scan.image_path,
                    metadata=scan_meta,
                )
            )
        except Exception as exc:
            logger.warning("Failed updating scan repository metadata for %s: %s", scan.folder_name, exc)

        meta_file = folder_dir.parent / "metadata.json"
        if meta_file.is_file():
            try:
                curr_data = json.loads(meta_file.read_text(encoding="utf-8"))
                curr_data["latest_cv_results"] = scan_meta["latest_cv_results"]
                if briefing_pdf_path:
                    curr_data["briefing_pdf"] = Path(briefing_pdf_path).name
                if geotiff_path:
                    curr_data["geotiff"] = Path(geotiff_path).name
                meta_file.write_text(json.dumps(curr_data, indent=2), encoding="utf-8")
            except Exception:
                pass

        report(100, "Automated intelligence analysis complete")

        return {
            "status": "success",
            "folderName": scan.folder_name,
            "customName": scan_meta.get("custom_name") or scan.folder_name,
            "imageUrl": self._resolve_image_url(scan),
            "bounds": [
                [scan.bbox.min_latitude, scan.bbox.min_longitude],
                [scan.bbox.max_latitude, scan.bbox.max_longitude],
            ],
            "datetime": scan.acquisition.acquired_at.isoformat(),
            "provider": scan_meta.get("provider", "copernicus"),
            "land_masked": bool(dem_path is not None),
            "coastal_buffer": coastal_buffer,
            "ais_correlation_distance": ais_distance,
            "boxes": [(d.get("x", 0), d.get("y", 0), d.get("width", 0), d.get("height", 0)) for d in enriched_detections],
            "detections": enriched_detections,
            "ship_count": saved_info.get("ship_count", len(enriched_detections)),
            "correlated_count": saved_info.get("correlated_count", correlated_count),
            "inside_box_count": saved_info.get("inside_box_count", inside_box_count),
            "outside_box_count": saved_info.get("outside_box_count", outside_box_count),
            "uncorrelated_count": saved_info.get("uncorrelated_count", uncorrelated_count),
            "dark_vessel_count": saved_info.get("dark_vessel_count", 0),
            "critical_dark_count": saved_info.get("critical_dark_count", 0),
            "ghost_vessel_count": saved_info.get("ghost_vessel_count", len(ghost_vessels)),
            "ghost_vessels": ghost_vessels,
            "width": img_width,
            "height": img_height,
            "saved_image": saved_info.get("detected_image_name"),
            "saved_json": saved_info.get("detections_json_name"),
            "detection_image_url": f"/api/scan/{scan.folder_name}/detection_image",
            "detections_url": f"/api/scan/{scan.folder_name}/detections",
            "geojson_url": f"/api/scan/{scan.folder_name}/geojson",
            "gis_bundle_url": f"/api/scan/{scan.folder_name}/gis_bundle",
            "briefing_pdf_url": f"/api/scan/{scan.folder_name}/briefing/pdf",
            "route_correlation_image_url": f"/api/scan/{scan.folder_name}/route_correlation_image",
            "transshipment_url": f"/api/scan/{scan.folder_name}/transshipment",
            "transshipment": transshipment_res,
            "geofence": geofence_res,
            "optical_validation": optical_res,
            "sar_change_detection": change_res,
            "optical_validation_url": f"/api/scan/{scan.folder_name}/optical_validation",
            "sar_change_url": f"/api/scan/{scan.folder_name}/change_detection",
            "geotiff_url": f"/api/scan/{scan.folder_name}/geotiff",
            "stac_url": f"/api/scan/{scan.folder_name}/stac",
        }
