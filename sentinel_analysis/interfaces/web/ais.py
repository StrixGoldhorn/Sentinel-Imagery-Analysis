import csv
import io
import json
from datetime import datetime, timedelta, timezone

from flask import Blueprint, Response, jsonify, render_template, request

from sentinel_analysis.application.use_cases.scrape_aoi_ais import calculate_pass_window
from sentinel_analysis.application.results import summarize_ingestion_outcome
from sentinel_analysis.domain.entities import BoundingBox
from sentinel_analysis.interfaces.web.dependencies import container
from sentinel_analysis.interfaces.web.request_data import (
    RequestValidationError,
    bounding_box,
    json_object,
    optional_string,
)


blueprint = Blueprint("ais", __name__)


def _parse_bool(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "on"}
    return bool(value)


def _parse_bbox_value(raw: object) -> BoundingBox | None:
    if raw is None or raw == "":
        return None
    try:
        if isinstance(raw, dict):
            min_lon = raw.get("min_longitude", raw.get("min_lon"))
            min_lat = raw.get("min_latitude", raw.get("min_lat"))
            max_lon = raw.get("max_longitude", raw.get("max_lon"))
            max_lat = raw.get("max_latitude", raw.get("max_lat"))
            return BoundingBox(float(min_lon), float(min_lat), float(max_lon), float(max_lat))
        if isinstance(raw, str):
            values = [float(value.strip()) for value in raw.split(",")]
        elif isinstance(raw, (list, tuple)):
            values = [float(value) for value in raw]
        else:
            raise ValueError("unsupported bbox format")
        if len(values) != 4:
            raise ValueError("bbox must contain four coordinates")
        return BoundingBox.from_sequence(values)
    except Exception as exc:
        raise RequestValidationError(
            "Invalid bbox, expected min_lon,min_lat,max_lon,max_lat"
        ) from exc


def _parse_datetime(value: object, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise RequestValidationError(f"Invalid datetime format for {field}")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise RequestValidationError(f"Invalid datetime format for {field}") from exc
    if parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_ais_filters(max_limit: int = 2000) -> dict:
    is_json_request = request.method == "POST" or (
        request.is_json and request.get_json(silent=True) is not None
    )
    payload = request.get_json(silent=True) or {} if is_json_request else request.args
    if not isinstance(payload, dict):
        raise RequestValidationError("JSON request body must be an object")

    bbox = _parse_bbox_value(payload.get("bbox"))
    start_raw = payload.get("start")
    end_raw = payload.get("end")
    within_hours_raw = payload.get("within_hours", payload.get("hours"))
    all_time = _parse_bool(payload.get("all_time"), False)
    if start_raw or end_raw:
        time_range = (
            _parse_datetime(start_raw, "start") if start_raw else None,
            _parse_datetime(end_raw, "end") if end_raw else None,
        )
    elif within_hours_raw is not None and str(within_hours_raw).strip():
        try:
            within_hours = float(within_hours_raw)
        except (TypeError, ValueError) as exc:
            raise RequestValidationError("within_hours must be a number") from exc
        if within_hours < 0:
            raise RequestValidationError("within_hours must not be negative")
        time_range = (datetime.now(timezone.utc) - timedelta(hours=within_hours), None)
    elif all_time:
        time_range = None
    else:
        time_range = (datetime.now(timezone.utc) - timedelta(hours=12), None)

    try:
        limit = int(payload.get("limit", payload.get("page_size", 500)))
        offset = int(payload.get("offset", 0))
        page = int(payload.get("page", 1))
    except (TypeError, ValueError) as exc:
        raise RequestValidationError("limit, page, and offset must be integers") from exc
    if page < 1:
        raise RequestValidationError("page must be a positive integer")
    limit = max(1, min(limit, max_limit))
    offset = max(0, offset)
    if "page" in payload and "offset" not in payload:
        offset = (page - 1) * limit

    return {
        "bbox": bbox,
        "time_range": time_range,
        "latest_only": _parse_bool(payload.get("latest_only"), True),
        "randomize": _parse_bool(payload.get("randomize"), False),
        "limit": limit,
        "offset": offset,
        "page": page,
        "search": (str(payload.get("q")).strip() if payload.get("q") else None),
        "vessel_type": (str(payload.get("vessel_type")).strip() if payload.get("vessel_type") else None),
        "source_plugin": (str(payload.get("source_plugin")).strip() if payload.get("source_plugin") else None),
    }


@blueprint.post("/api/ingest_ais")
def ingest_ais():
    payload = json_object()
    bbox = bounding_box(payload)
    plugin = optional_string(payload, "plugin")

    pass_time_str = optional_string(payload, "pass_time")
    if pass_time_str:
        try:
            pass_time = datetime.fromisoformat(pass_time_str.replace("Z", "+00:00"))
            time_range = calculate_pass_window(pass_time, window_minutes=5)
        except ValueError as exc:
            raise RequestValidationError("Invalid pass_time format, must be ISO datetime") from exc
    else:
        time_range = (None, None)

    results = container().ingest_ais.execute(
        bbox,
        time_range,
        plugin,
        trigger_reason=f"Direct API Ingest Request{f' ({plugin})' if plugin else ''}",
    )
    return jsonify(
        status="success",
        ingestion_outcome=summarize_ingestion_outcome(results),
        results=results,
    )


@blueprint.route("/api/ais/vessels", methods=["GET", "POST"])
def list_vessels():
    filters = _parse_ais_filters()
    query_filters = {key: value for key, value in filters.items() if key != "page"}
    vessels = container().get_vessels.execute(**query_filters)
    repo = getattr(container(), "ais_repository", None)
    total_count = len(vessels)
    if repo is not None and hasattr(repo, "count_vessel_positions"):
        total_count = repo.count_vessel_positions(
            bbox=filters["bbox"],
            time_range=filters["time_range"],
            latest_only=filters["latest_only"],
            search=filters["search"],
            vessel_type=filters["vessel_type"],
            source_plugin=filters["source_plugin"],
        )
    return jsonify(
        status="success",
        count=len(vessels),
        total_count=total_count,
        vessels=vessels,
        generated_at=datetime.now(timezone.utc).isoformat(),
        view_semantics="LATEST_STORED" if filters["latest_only"] else "HISTORICAL_REPORTS",
        default_lookback_hours=12,
        page=filters["page"],
        page_size=filters["limit"],
        has_more=filters["offset"] + len(vessels) < total_count,
    )


@blueprint.get("/api/ais/timeline")
def get_ais_timeline():
    repo = getattr(container(), "ais_repository", None)
    if repo is not None and hasattr(repo, "get_timeline_bounds"):
        bounds = repo.get_timeline_bounds()
        return jsonify(status="success", **bounds)
    return jsonify(status="success", min_timestamp=None, max_timestamp=None, total_records=0, count=0)


@blueprint.get("/api/ais/vessels/<int:vessel_id>/history")
def get_vessel_history(vessel_id: int):
    filters = _parse_ais_filters(max_limit=10000)
    repo = getattr(container(), "ais_repository", None)
    if repo is None or not hasattr(repo, "get_vessel_history"):
        raise RequestValidationError("AIS history repository is not configured")
    vessel = container().get_vessel_details.execute(vessel_id)
    locations = repo.get_vessel_history(
        vessel_id=vessel_id,
        time_range=filters["time_range"],
        limit=filters["limit"],
    )
    return jsonify(
        status="success",
        vessel=vessel,
        locations=locations,
        count=len(locations),
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


def _freshness_band(freshness_seconds: int | float | None) -> str:
    if freshness_seconds is None:
        return "NO_DATA"
    if freshness_seconds < 30 * 60:
        return "FRESH"
    if freshness_seconds <= 120 * 60:
        return "AGING"
    return "STALE"


@blueprint.get("/api/ais/summary")
def get_ais_summary():
    filters = _parse_ais_filters()
    repo = getattr(container(), "ais_repository", None)
    if repo is None or not hasattr(repo, "get_ais_summary"):
        summary = {
            "vessel_count": 0,
            "record_count": 0,
            "latest_report_at": None,
            "freshness_seconds": None,
            "by_type": {},
            "by_source": {},
            "time_buckets": [],
        }
    else:
        summary = repo.get_ais_summary(
            bbox=filters["bbox"],
            time_range=filters["time_range"],
            search=filters["search"],
            vessel_type=filters["vessel_type"],
            source_plugin=filters["source_plugin"],
        )
    summary["freshness_band"] = _freshness_band(summary.get("freshness_seconds"))
    return jsonify(status="success", **summary)


@blueprint.get("/api/ais/activity")
def get_ais_activity():
    app_container = container()
    filters = _parse_ais_filters()
    repo = getattr(app_container, "ais_repository", None)
    if repo is not None and hasattr(repo, "get_ais_summary"):
        coverage = repo.get_ais_summary(time_range=filters["time_range"])
    else:
        coverage = {
            "vessel_count": 0,
            "record_count": 0,
            "latest_report_at": None,
            "freshness_seconds": None,
            "by_type": {},
            "by_source": {},
            "time_buckets": [],
        }
    scraper_result = app_container.list_scrapers.execute()
    coverage["freshness_band"] = _freshness_band(coverage.get("freshness_seconds"))
    return jsonify(
        status="success",
        scrapers=scraper_result.get("scrapers", []),
        metrics=scraper_result.get("metrics", {}),
        coverage=coverage,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


@blueprint.get("/api/ais/export")
def export_ais():
    export_format = request.args.get("format", "csv").strip().lower()
    if export_format not in {"csv", "geojson"}:
        raise RequestValidationError("format must be csv or geojson")
    filters = _parse_ais_filters(max_limit=10000)
    filters["limit"] = 10000
    filters["offset"] = 0
    query_filters = {key: value for key, value in filters.items() if key != "page"}
    vessels = container().get_vessels.execute(**query_filters)

    if export_format == "geojson":
        features = []
        for vessel in vessels:
            if vessel.get("latitude") is None or vessel.get("longitude") is None:
                continue
            properties = {
                key: value for key, value in vessel.items()
                if key not in {"latitude", "longitude"}
            }
            features.append({
                "type": "Feature",
                "id": vessel.get("vessel_id"),
                "geometry": {
                    "type": "Point",
                    "coordinates": [vessel["longitude"], vessel["latitude"]],
                },
                "properties": properties,
            })
        payload = json.dumps({
            "type": "FeatureCollection",
            "features": features,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        })
        response = Response(payload, content_type="application/geo+json; charset=utf-8")
        response.headers["Content-Disposition"] = "attachment; filename=ais_export.geojson"
        return response

    fields = [
        "vessel_id", "name", "mmsi", "imo", "type", "callsign",
        "latitude", "longitude", "speed", "heading", "timestamp", "source_plugin",
    ]
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(vessels)
    response = Response(buffer.getvalue(), content_type="text/csv; charset=utf-8")
    response.headers["Content-Disposition"] = "attachment; filename=ais_export.csv"
    return response


@blueprint.get("/ais")
def ais_dashboard():
    return render_template("ais.html")


@blueprint.get("/ais/vessels/<int:vessel_id>")
def vessel_dashboard(vessel_id: int):
    return render_template("ais_vessel.html", vessel_id=vessel_id)


@blueprint.get("/ais/activity")
def ais_activity_dashboard():
    return render_template("ais_activity.html")


@blueprint.get("/api/ais/vessels/<int:vessel_id>")
def get_vessel(vessel_id: int):
    vessel = container().get_vessel_details.execute(vessel_id)
    return jsonify(status="success", vessel=vessel)


@blueprint.route("/api/ais/vessels/<int:vessel_id>", methods=["PUT", "PATCH", "POST"])
def update_vessel(vessel_id: int):
    payload = request.get_json(silent=True) or {}
    name = payload.get("name") if "name" in payload else payload.get("vessel_name")
    vessel_type = payload.get("type") if "type" in payload else payload.get("vessel_type")
    callsign = payload.get("callsign")
    imo = payload.get("imo")

    updated = container().update_vessel_details.execute(
        vessel_id=vessel_id,
        name=name,
        vessel_type=vessel_type,
        callsign=callsign,
        imo=imo,
    )
    return jsonify(status="success", vessel=updated)
