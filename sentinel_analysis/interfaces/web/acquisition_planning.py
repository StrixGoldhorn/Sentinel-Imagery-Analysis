"""HTTP routes for Area of Interest revisit calendar, multi-provider acquisition suitability scoring, and opportunity recommendations."""

from datetime import datetime, timezone
import math
from typing import Any, List, Optional
from flask import Blueprint, jsonify, render_template, request

from sentinel_analysis.application.exceptions import AreaOfInterestNotFoundError
from sentinel_analysis.domain.acquisition_suitability import MetoceanCondition
from sentinel_analysis.domain.entities import BoundingBox
from sentinel_analysis.domain.provider_catalog import (
    ModalityType,
    ProviderCatalog,
)
from sentinel_analysis.interfaces.web.dependencies import container
from sentinel_analysis.interfaces.web.request_data import (
    RequestValidationError,
    boolean,
    json_object,
    optional_datetime,
    optional_string,
)

blueprint = Blueprint("acquisition_planning", __name__)


@blueprint.get("/acquisition-planner")
@blueprint.get("/planning")
def acquisition_planner_page():
    """Render the mission planning and satellite acquisition suitability dashboard."""
    return render_template("acquisition_planner.html")


@blueprint.get("/api/providers/catalog")
def list_providers():
    """Return catalog of registered satellite providers and capabilities."""
    providers = ProviderCatalog.list_all()
    return jsonify({
        "status": "success",
        "total_providers": len(providers),
        "providers": [p.to_dict() for p in providers],
    })


@blueprint.get("/api/revisit-calendar")
@blueprint.get("/api/aoi/<int:aoi_id>/revisit-calendar")
def get_revisit_calendar(aoi_id: Optional[int] = None):
    """Return revisit calendar and footprint coverage timeline for an AOI or all AOIs."""
    days_ahead_raw = request.args.get("days_ahead", "14")
    try:
        days_ahead = max(1, min(int(days_ahead_raw), 60))
    except ValueError:
        days_ahead = 14

    providers_raw = request.args.get("providers")
    provider_ids = [p.strip().lower() for p in providers_raw.split(",") if p.strip()] if providers_raw else None

    api_key = container().settings.n2yo_api_key or ""

    use_case = container().get_aoi_revisit_calendar
    calendar = use_case.execute(
        aoi_id=aoi_id if (aoi_id and aoi_id > 0) else None,
        days_ahead=days_ahead,
        api_key=api_key,
        providers=provider_ids,
    )
    return jsonify(calendar)


@blueprint.get("/api/aoi/<int:aoi_id>/opportunities/best")
def get_best_opportunities(aoi_id: int):
    """Return ranked 'best upcoming opportunity' recommendations for an AOI."""
    if aoi_id <= 0:
        raise RequestValidationError("aoi_id must be a positive integer")

    horizon_hours_raw = request.args.get("horizon_hours", "72")
    try:
        horizon_hours = max(6, min(int(horizon_hours_raw), 336))
    except ValueError:
        horizon_hours = 72

    target_vessel_size_raw = request.args.get("target_vessel_size_m", "30.0")
    try:
        target_vessel_size_m = max(2.0, min(1000.0, float(target_vessel_size_raw)))
    except ValueError:
        target_vessel_size_m = 30.0

    cost_pref_raw = request.args.get("cost_preference", "BALANCED").strip().upper()
    if cost_pref_raw in {"FREE_ONLY", "FREE", "OPEN"}:
        cost_preference = "FREE_ONLY"
    elif cost_pref_raw in {"TACTICAL", "TACTICAL_PRIORITY", "MISSION_CRITICAL", "COMMERCIAL"}:
        cost_preference = "TACTICAL_PRIORITY"
    else:
        cost_preference = "BALANCED"

    api_key = container().settings.n2yo_api_key or ""
    use_case = container().recommend_acquisition_opportunities

    try:
        recommendations = use_case.execute(
            aoi_id=aoi_id,
            horizon_hours=horizon_hours,
            target_vessel_size_m=target_vessel_size_m,
            cost_preference=cost_preference,
            api_key=api_key,
        )
        if "primary_opportunity" in recommendations and "primary_recommendation" not in recommendations:
            recommendations["primary_recommendation"] = recommendations["primary_opportunity"]
        if "backup_opportunity" in recommendations and "backup_recommendation" not in recommendations:
            recommendations["backup_recommendation"] = recommendations["backup_opportunity"]
        if "contingency_opportunity" in recommendations and "contingency_recommendation" not in recommendations:
            recommendations["contingency_recommendation"] = recommendations["contingency_opportunity"]
        if "tactical_briefing" in recommendations and "dispatch_guidance" not in recommendations:
            recommendations["dispatch_guidance"] = recommendations["tactical_briefing"]
        return jsonify(recommendations)
    except ValueError as exc:
        raise AreaOfInterestNotFoundError(str(exc)) from exc


@blueprint.get("/api/providers/compare")
def compare_providers():
    """Compare multiple satellite providers (Sentinel-1, Sentinel-2, Umbra, etc.) before dispatch."""
    aoi_id_raw = request.args.get("aoi_id")
    aoi_id: Optional[int] = None
    if aoi_id_raw:
        try:
            aoi_id = int(aoi_id_raw)
        except ValueError as exc:
            raise RequestValidationError("Query parameter 'aoi_id' must be an integer") from exc

    bbox: Optional[list[float]] = None
    bbox_raw = request.args.get("bbox")
    if bbox_raw:
        try:
            parts = [float(p.strip()) for p in bbox_raw.split(",")]
            if len(parts) == 4:
                bbox = parts
        except ValueError:
            pass

    if aoi_id is None and bbox is None:
        raise RequestValidationError("Either 'aoi_id' or 'bbox' must be provided")

    pass_time_raw = request.args.get("pass_time")
    pass_time: Optional[datetime] = None
    if pass_time_raw:
        try:
            cleaned = pass_time_raw.replace("Z", "+00:00")
            pass_time = datetime.fromisoformat(cleaned)
        except ValueError:
            pass_time = None

    providers_raw = request.args.get("providers")
    provider_ids = [p.strip().lower() for p in providers_raw.split(",") if p.strip()] if providers_raw else None

    target_vessel_size_raw = request.args.get("target_vessel_size_m", "30.0")
    try:
        target_vessel_size_m = max(2.0, min(1000.0, float(target_vessel_size_raw)))
    except ValueError:
        target_vessel_size_m = 30.0

    is_daylight_raw = request.args.get("is_daylight")
    is_daylight: Optional[bool] = None
    if is_daylight_raw is not None:
        is_daylight = is_daylight_raw.strip().lower() in ("true", "1", "yes")

    use_case = container().compare_satellite_providers
    try:
        result = use_case.execute(
            aoi_id=aoi_id,
            bbox=bbox,
            target_vessel_size_m=target_vessel_size_m,
            target_time=pass_time,
            is_daylight=is_daylight,
            filter_providers=provider_ids,
        )
        return jsonify(result)
    except ValueError as exc:
        raise AreaOfInterestNotFoundError(str(exc)) from exc


@blueprint.post("/api/aoi/<int:aoi_id>/evaluate-suitability")
def evaluate_custom_suitability(aoi_id: int):
    """Evaluate custom what-if acquisition suitability scenario for an AOI."""
    cnt = container()
    repo = getattr(cnt, "aoi_repository", None)
    aoi = repo.get(aoi_id) if repo and hasattr(repo, "get") else None
    if aoi is None and hasattr(cnt, "list_aois"):
        for candidate in cnt.list_aois.execute():
            if getattr(candidate, "id", None) == aoi_id:
                aoi = candidate
                break
    if aoi is None:
        raise AreaOfInterestNotFoundError(f"Area of Interest with id {aoi_id} not found")

    payload = json_object()
    pass_time = optional_datetime(payload, "pass_time")
    resolution_m = float(payload.get("resolution_m", 10.0))
    aoi_coverage_pct = float(payload.get("aoi_coverage_pct", 100.0))
    incidence_angle_deg = float(payload.get("incidence_angle_deg", 35.0))
    orbit_direction = str(payload.get("orbit_direction", "descending"))
    modality_str = optional_string(payload, "modality") or "SAR"
    is_daylight = boolean(payload, "is_daylight") if "is_daylight" in payload else None

    custom_metocean = None
    if "metocean" in payload and isinstance(payload["metocean"], dict):
        m_dict = payload["metocean"]
        custom_metocean = MetoceanCondition(
            wind_speed_mps=float(m_dict.get("wind_speed_mps", 5.0)),
            wave_height_m=float(m_dict.get("wave_height_m", 1.0)),
            cloud_cover_pct=float(m_dict.get("cloud_cover_pct", 10.0)),
            sea_surface_temp_c=float(m_dict.get("sea_surface_temp_c", m_dict.get("surface_temperature_c", 20.0))),
        )

    use_case = container().evaluate_acquisition_suitability
    assessment = use_case.execute(
        bbox=aoi.bbox,
        resolution_m=resolution_m,
        aoi_coverage_pct=aoi_coverage_pct,
        incidence_angle_deg=incidence_angle_deg,
        orbit_direction=orbit_direction,
        modality=modality_str,
        pass_time=pass_time,
        is_daylight=is_daylight,
        custom_metocean=custom_metocean,
    )
    return jsonify(assessment.to_dict())


@blueprint.post("/api/aoi/<int:aoi_id>/dispatch-opportunity")
def dispatch_acquisition_opportunity(aoi_id: int):
    """Dispatch or schedule an acquisition opportunity before satellite pass."""
    cnt = container()
    repo = getattr(cnt, "aoi_repository", None)
    aoi = repo.get(aoi_id) if repo and hasattr(repo, "get") else None
    if aoi is None and hasattr(cnt, "list_aois"):
        for candidate in cnt.list_aois.execute():
            if getattr(candidate, "id", None) == aoi_id:
                aoi = candidate
                break
    if aoi is None:
        raise AreaOfInterestNotFoundError(f"Area of Interest with id {aoi_id} not found")

    payload = json_object()
    provider_id = str(payload.get("provider_id", "sentinel1")).lower().replace("-", "")
    pass_time_str = str(payload.get("pass_time", ""))
    notes = str(payload.get("notes", ""))

    # If Sentinel-1, automatically ensure auto_capture is active
    auto_enabled = False
    if "sentinel1" in provider_id and repo and hasattr(repo, "update_auto_capture"):
        repo.update_auto_capture(aoi_id, True)
        auto_enabled = True

    return jsonify({
        "status": "success",
        "dispatched": True,
        "aoi_id": aoi_id,
        "aoi_name": aoi.name,
        "provider_id": provider_id,
        "pass_time": pass_time_str,
        "auto_capture_enabled": auto_enabled,
        "notes": notes,
        "message": f"Acquisition opportunity with provider '{provider_id.upper()}' dispatched successfully for AOI '{aoi.name}'",
    })
