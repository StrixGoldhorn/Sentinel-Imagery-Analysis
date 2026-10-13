from datetime import datetime, timedelta, timezone
from flask import Blueprint, jsonify, render_template, request
import uuid

from sentinel_analysis.application.exceptions import AreaOfInterestNotFoundError
from sentinel_analysis.interfaces.web.dependencies import container
from sentinel_analysis.interfaces.web.request_data import (
    RequestValidationError,
    boolean,
    bounding_box,
    json_object,
    optional_datetime,
    optional_string,
    required_string,
)
from sentinel_analysis.interfaces.web.serialization import scan_image_url, serialize_aoi
from sentinel_analysis.application.results import summarize_ingestion_outcome


blueprint = Blueprint("aois", __name__)


@blueprint.get("/aois")
def aois_page():
    return render_template("aois.html")


@blueprint.get("/api/aoi")
def list_aois():
    aois = sorted(container().list_aois.execute(), key=lambda a: (a.name or "").lower())
    return jsonify([serialize_aoi(aoi) for aoi in aois])


@blueprint.post("/api/aoi")
def add_aoi():
    payload = json_object()
    bbox = bounding_box(payload)
    auto_capture = (
        boolean(payload, "auto_capture_enabled")
        if "auto_capture_enabled" in payload else None
    )
    priority = payload.get("priority")
    budget = payload.get("collection_budget_usd")
    revisit = payload.get("desired_revisit_rate_hours")
    retention = payload.get("retention_policy_days")

    aoi_id = container().add_aoi.execute(
        required_string(payload, "name"),
        bbox,
        auto_capture_enabled=auto_capture,
        priority=str(priority) if priority is not None else None,
        collection_budget_usd=float(budget) if budget is not None else None,
        desired_revisit_rate_hours=float(revisit) if revisit is not None else None,
        retention_policy_days=int(retention) if retention is not None else None,
    )
    return jsonify(status="success", id=aoi_id), 201


@blueprint.delete("/api/aoi/<int:aoi_id>")
@blueprint.post("/api/aoi/<int:aoi_id>/delete")
def delete_aoi(aoi_id: int):
    cnt = container()
    pass_monitor = getattr(cnt, "pass_monitor", None)
    if pass_monitor is not None and hasattr(pass_monitor, "stop_for_aoi"):
        pass_monitor.stop_for_aoi(aoi_id)
    cnt.delete_aoi.execute(aoi_id)
    return jsonify(status="success", message=f"Area of interest #{aoi_id} deleted successfully")


@blueprint.get("/api/aoi/<int:aoi_id>/policy")
def get_aoi_policy(aoi_id: int):
    aoi = container().aoi_repository.get(aoi_id)
    if aoi is None:
        return jsonify(status="error", message=f"Area of interest #{aoi_id} not found"), 404
    budget = getattr(aoi, "collection_budget_usd", 0.0)
    spent = getattr(aoi, "budget_spent_usd", 0.0)
    return jsonify({
        "status": "success",
        "policy": {
            "id": aoi.id,
            "name": aoi.name,
            "priority": getattr(aoi, "priority", "MEDIUM"),
            "collection_budget_usd": budget,
            "budget_spent_usd": spent,
            "budget_remaining_usd": max(0.0, budget - spent) if budget > 0 else None,
            "desired_revisit_rate_hours": getattr(aoi, "desired_revisit_rate_hours", None),
            "retention_policy_days": getattr(aoi, "retention_policy_days", None),
        }
    })


@blueprint.put("/api/aoi/<int:aoi_id>/policy")
@blueprint.post("/api/aoi/<int:aoi_id>/policy")
def update_aoi_policy(aoi_id: int):
    payload = json_object()
    priority = payload.get("priority")
    budget = payload.get("collection_budget_usd")
    spent = payload.get("budget_spent_usd")
    revisit = payload.get("desired_revisit_rate_hours")
    retention = payload.get("retention_policy_days")

    try:
        updated = container().update_aoi_policy.execute(
            aoi_id=aoi_id,
            priority=str(priority) if priority is not None else None,
            collection_budget_usd=float(budget) if budget is not None else None,
            budget_spent_usd=float(spent) if spent is not None else None,
            desired_revisit_rate_hours=float(revisit) if revisit is not None else None,
            retention_policy_days=int(retention) if retention is not None else None,
        )
        return jsonify({
            "status": "success",
            "aoi": serialize_aoi(updated),
        })
    except AreaOfInterestNotFoundError as exc:
        return jsonify(status="error", message=str(exc)), 404
    except (ValueError, TypeError) as exc:
        return jsonify(status="error", message=str(exc)), 400


@blueprint.post("/api/aoi/<int:aoi_id>/spend")
def record_aoi_spend(aoi_id: int):
    payload = json_object()
    amount = payload.get("amount_usd")
    if amount is None or not isinstance(amount, (int, float)) or amount < 0:
        return jsonify(status="error", message="amount_usd must be a non-negative number"), 400
    try:
        updated = container().record_aoi_expenditure.execute(aoi_id, float(amount))
        return jsonify({
            "status": "success",
            "aoi": serialize_aoi(updated),
        })
    except AreaOfInterestNotFoundError as exc:
        return jsonify(status="error", message=str(exc)), 404
    except (ValueError, TypeError) as exc:
        return jsonify(status="error", message=str(exc)), 400


@blueprint.post("/api/aoi/<int:aoi_id>/enforce-retention")
def enforce_aoi_retention(aoi_id: int):
    try:
        outcome = container().enforce_aoi_retention.execute(aoi_id)
        return jsonify({"status": "success", "result": outcome})
    except AreaOfInterestNotFoundError as exc:
        return jsonify(status="error", message=str(exc)), 404


@blueprint.post("/api/aoi/<int:aoi_id>/predict")
def predict_aoi(aoi_id: int):
    force_refresh = request.args.get("refresh", "").lower() in ("true", "1", "yes")
    custom_ttl = None

    if "cache_ttl" in request.args:
        try:
            custom_ttl = int(request.args["cache_ttl"])
        except ValueError:
            pass
    elif "ttl_hours" in request.args:
        try:
            custom_ttl = int(float(request.args["ttl_hours"]) * 3600)
        except ValueError:
            pass

    if request.is_json:
        try:
            body = request.get_json(silent=True) or {}
            if body.get("refresh") or body.get("force_refresh"):
                force_refresh = True
            if "cache_ttl_seconds" in body:
                custom_ttl = int(body["cache_ttl_seconds"])
            elif "ttl_hours" in body:
                custom_ttl = int(float(body["ttl_hours"]) * 3600)
        except Exception:
            pass

    api_key = container().settings.n2yo_api_key or ""
    use_case = container().predict_aoi
    if hasattr(use_case, "execute_with_analysis"):
        result = use_case.execute_with_analysis(
            aoi_id,
            api_key,
            force_refresh=force_refresh,
            cache_ttl_seconds=custom_ttl,
        )

        predictions = result.get("predictions", [])
        n2yo_predictions = result.get("n2yo_predictions", [])
        historical_predictions = result.get("historical_predictions", [])
        next_scan = result.get("next_scan")
        mission_analysis = result.get("mission_analysis")
        cached = result.get("cached", False)
        fetched_at = result.get("fetched_at")
        expires_at = result.get("expires_at")
    else:
        predictions = use_case.execute(aoi_id, api_key)
        n2yo_predictions = [p for p in predictions if p.get("source") in ("N2YO", "COMBINED")]
        historical_predictions = [p for p in predictions if p.get("source") in ("HISTORICAL_MISSION", "COMBINED")]
        next_scan = predictions[0]["time"] if predictions else None
        mission_analysis = None
        cached = False
        fetched_at = None
        expires_at = None

    if not predictions and not n2yo_predictions and not historical_predictions:
        return jsonify(error="No upcoming scans found"), 404
    return jsonify(
        status="success",
        next_scan=next_scan,
        predictions=predictions,
        n2yo_predictions=n2yo_predictions,
        historical_predictions=historical_predictions,
        mission_analysis=mission_analysis,
        cached=cached,
        fetched_at=fetched_at,
        expires_at=expires_at,
    )




@blueprint.get("/api/aoi/<int:aoi_id>/mission_history")
def aoi_mission_history(aoi_id: int):
    result = container().analyze_mission_passes.execute(aoi_id)
    return jsonify(status="success", **result)


@blueprint.post("/api/aoi/<int:aoi_id>/auto_capture")
def toggle_auto_capture(aoi_id: int):
    payload = json_object()
    enabled = boolean(payload, "enabled", True)
    repo = container().aoi_repository
    if hasattr(repo, "update_auto_capture"):
        repo.update_auto_capture(aoi_id, enabled)
    if not enabled:
        monitor = getattr(container(), "pass_monitor", None)
        if monitor is not None and hasattr(monitor, "stop_for_aoi"):
            monitor.stop_for_aoi(aoi_id)
    return jsonify(status="success", auto_capture_enabled=enabled)


@blueprint.post("/api/aoi/<int:aoi_id>/scrape_ais")
def scrape_aoi_ais(aoi_id: int):
    payload = request.get_json(silent=True) or {}
    plugin = optional_string(payload, "plugin") if isinstance(payload, dict) else None
    pass_time_str = optional_string(payload, "pass_time") if isinstance(payload, dict) else None
    force_now = False
    if isinstance(payload, dict):
        force_now = bool(payload.get("force") or payload.get("force_now"))

    pass_time = None
    if pass_time_str and not force_now:
        try:
            pass_time = datetime.fromisoformat(pass_time_str.replace("Z", "+00:00"))
        except ValueError as exc:
            raise RequestValidationError("Invalid pass_time format, must be ISO datetime") from exc

    results = container().scrape_aoi_ais.execute(
        aoi_id,
        plugin_name=plugin,
        pass_time=pass_time,
        force_now=force_now,
    )
    return jsonify(
        status="success",
        ingestion_outcome=summarize_ingestion_outcome(results),
        results=results,
    )


@blueprint.post("/api/aoi/<int:aoi_id>/force_ais_scan")
def force_ais_scan(aoi_id: int):
    payload = request.get_json(silent=True) or {}
    plugin = optional_string(payload, "plugin") if isinstance(payload, dict) else None
    results = container().scrape_aoi_ais.execute(
        aoi_id,
        plugin_name=plugin,
        force_now=True,
    )
    return jsonify(
        status="success",
        ingestion_outcome=summarize_ingestion_outcome(results),
        results=results,
        forced=True,
    )


@blueprint.post("/api/aoi/<int:aoi_id>/scan")
def scan_aoi(aoi_id: int):
    cnt = container()
    repo = getattr(cnt, "aoi_repository", None)
    aoi = repo.get(aoi_id) if repo and hasattr(repo, "get") else None
    if aoi is None:
        list_use_case = getattr(cnt, "list_aois", None)
        if list_use_case and hasattr(list_use_case, "execute"):
            for candidate in list_use_case.execute():
                if getattr(candidate, "id", None) == aoi_id:
                    aoi = candidate
                    break
    if aoi is None:
        return jsonify(error=f"Area of Interest {aoi_id} not found"), 404

    payload = request.get_json(silent=True) or {} if request.is_json else {}
    if not isinstance(payload, dict):
        payload = {}

    is_async = request.args.get("async", "").lower() in ("true", "1", "yes")
    if payload.get("async") or payload.get("is_async"):
        is_async = True

    now = datetime.now(timezone.utc)
    default_start = now - timedelta(days=15)
    default_end = now

    start_raw = (
        request.args.get("start_datetime")
        or request.args.get("start_date")
        or request.args.get("date_from")
        or payload.get("start_datetime")
        or payload.get("start_date")
        or payload.get("date_from")
    )
    start_time_raw = (
        request.args.get("start_time")
        or request.args.get("time_from")
        or payload.get("start_time")
        or payload.get("time_from")
    )

    end_raw = (
        request.args.get("end_datetime")
        or request.args.get("end_date")
        or request.args.get("date_to")
        or payload.get("end_datetime")
        or payload.get("end_date")
        or payload.get("date_to")
    )
    end_time_raw = (
        request.args.get("end_time")
        or request.args.get("time_to")
        or payload.get("end_time")
        or payload.get("time_to")
    )

    days_ago = None
    if "days_ago" in payload or "days_ago" in request.args:
        try:
            days_ago = int(payload.get("days_ago") or request.args.get("days_ago"))
        except (TypeError, ValueError):
            pass

    if start_raw is not None:
        start_date = optional_datetime(
            {"start_date": start_raw, "start_time": start_time_raw},
            "start_date",
            time_field="start_time",
            is_end_of_day=False,
        )
    elif days_ago is not None:
        start_date = now - timedelta(days=days_ago)
    else:
        start_date = default_start

    if end_raw is not None:
        end_date = optional_datetime(
            {"end_date": end_raw, "end_time": end_time_raw},
            "end_date",
            time_field="end_time",
            is_end_of_day=True,
        )
    else:
        end_date = default_end

    if start_date > end_date:
        raise RequestValidationError("Start date/time cannot be after end date/time")

    if is_async:
        queue = cnt.task_queue
        bbox = aoi.bbox
        aoi_name = aoi.name
        task_id = str(uuid.uuid4())

        def _run_scan() -> dict[str, object]:
            scan = cnt.create_scan.execute(
                bbox,
                aoi_name=aoi_name,
                start_date=start_date,
                end_date=end_date,
                progress_callback=lambda progress, message: queue.update_progress(
                    task_id, min(75.0, progress * 0.75), message
                ),
            )
            post_pipe = getattr(cnt, "post_acquisition_pipeline", None)
            if post_pipe is not None:
                res = post_pipe.execute(
                    scan,
                    progress_callback=lambda progress, message: queue.update_progress(
                        task_id, 75.0 + (progress * 0.25), message
                    ),
                )
            else:
                res = {
                    "status": "success",
                    "folderName": scan.folder_name,
                    "customName": scan.metadata.get("custom_name") or scan.folder_name,
                    "imageUrl": scan_image_url(scan, cnt.settings.output_root),
                    "bounds": [[bbox.min_latitude, bbox.min_longitude], [bbox.max_latitude, bbox.max_longitude]],
                    "datetime": scan.acquisition.acquired_at.isoformat(),
                }
            res["aoi_id"] = aoi_id
            res["aoi_name"] = aoi_name
            res["start_date"] = start_date.isoformat()
            res["end_date"] = end_date.isoformat()
            return res

        task = queue.submit("scan", task_id, _run_scan)
        return jsonify({
            "status": "success",
            "task_id": task.task_id,
            "task_status": task.status,
            "aoi_id": aoi.id,
            "aoi_name": aoi.name,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
        }), 202

    scan = cnt.create_scan.execute(
        aoi.bbox,
        aoi_name=aoi.name,
        start_date=start_date,
        end_date=end_date,
    )
    post_pipe = getattr(cnt, "post_acquisition_pipeline", None)
    if post_pipe is not None:
        res = post_pipe.execute(scan)
    else:
        res = {
            "status": "success",
            "folderName": scan.folder_name,
            "customName": scan.metadata.get("custom_name") or scan.folder_name,
            "imageUrl": scan_image_url(scan, cnt.settings.output_root),
            "bounds": [[aoi.bbox.min_latitude, aoi.bbox.min_longitude], [aoi.bbox.max_latitude, aoi.bbox.max_longitude]],
            "datetime": scan.acquisition.acquired_at.isoformat(),
        }
    res["aoi_id"] = aoi.id
    res["aoi_name"] = aoi.name
    res["start_date"] = start_date.isoformat()
    res["end_date"] = end_date.isoformat()
    return jsonify(res), 201


@blueprint.get("/api/aoi/<int:aoi_id>/baseline")
def get_aoi_baseline(aoi_id: int):
    cnt = container()
    try:
        baseline = cnt.get_aoi_baseline.execute(aoi_id)
        if baseline is None:
            return jsonify({"status": "not_found", "message": f"No baseline built yet for AOI #{aoi_id}", "baseline": None}), 404
        return jsonify({"status": "success", "baseline": baseline})
    except Exception as exc:
        if "not found" in str(exc).lower():
            return jsonify({"status": "error", "message": str(exc)}), 404
        return jsonify({"status": "error", "message": str(exc)}), 500


@blueprint.post("/api/aoi/<int:aoi_id>/baseline/backfill")
def backfill_aoi_baseline(aoi_id: int):
    cnt = container()
    payload = request.get_json(silent=True) or {} if request.is_json else {}

    days_back = 90
    if "days_back" in payload:
        try:
            days_back = int(payload["days_back"])
        except (ValueError, TypeError):
            pass
    elif "days_back" in request.args:
        try:
            days_back = int(request.args["days_back"])
        except (ValueError, TypeError):
            pass

    max_scenes = 25
    if "max_scenes" in payload:
        try:
            max_scenes = int(payload["max_scenes"])
        except (ValueError, TypeError):
            pass

    min_persistence = 0.40
    if "min_persistence_pct" in payload:
        try:
            min_persistence = float(payload["min_persistence_pct"])
        except (ValueError, TypeError):
            pass

    cluster_radius = 150.0
    if "cluster_radius_m" in payload:
        try:
            cluster_radius = float(payload["cluster_radius_m"])
        except (ValueError, TypeError):
            pass

    start_date = None
    if "start_date" in payload and payload["start_date"]:
        try:
            start_date = datetime.fromisoformat(str(payload["start_date"]).replace("Z", "+00:00"))
        except Exception:
            pass

    end_date = None
    if "end_date" in payload and payload["end_date"]:
        try:
            end_date = datetime.fromisoformat(str(payload["end_date"]).replace("Z", "+00:00"))
        except Exception:
            pass

    try:
        result = cnt.backfill_aoi_baseline.execute(
            aoi_id=aoi_id,
            days_back=days_back,
            start_date=start_date,
            end_date=end_date,
            min_persistence_pct=min_persistence,
            cluster_radius_m=cluster_radius,
            max_scenes=max_scenes,
        )
        return jsonify({"status": "success", "baseline": result}), 201
    except Exception as exc:
        if "not found" in str(exc).lower():
            return jsonify({"status": "error", "message": str(exc)}), 404
        return jsonify({"status": "error", "message": str(exc)}), 500

