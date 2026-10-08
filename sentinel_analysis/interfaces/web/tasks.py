"""Task status and asynchronous scanning HTTP routes."""

from flask import Blueprint, jsonify, request
import uuid

from sentinel_analysis.application.exceptions import TaskNotFoundError
from sentinel_analysis.domain.entities import BoundingBox
from sentinel_analysis.interfaces.web.dependencies import container
from sentinel_analysis.interfaces.web.request_data import (
    RequestValidationError,
    bounding_box,
    json_object,
    optional_datetime,
    optional_string,
)
from sentinel_analysis.interfaces.web.serialization import scan_image_url

blueprint = Blueprint("tasks", __name__)


@blueprint.post("/api/tasks/scan")
def create_async_scan():
    payload = json_object()
    bbox = bounding_box(payload)
    start_date = (
        optional_datetime(payload, "start_datetime", is_end_of_day=False)
        or optional_datetime(payload, "start_date", time_field="start_time", is_end_of_day=False)
        or optional_datetime(payload, "date_from", time_field="time_from", is_end_of_day=False)
    )
    end_date = (
        optional_datetime(payload, "end_datetime", is_end_of_day=True)
        or optional_datetime(payload, "end_date", time_field="end_time", is_end_of_day=True)
        or optional_datetime(payload, "date_to", time_field="time_to", is_end_of_day=True)
    )
    if start_date is not None and end_date is not None and start_date > end_date:
        raise RequestValidationError("Start date/time cannot be after end date/time")

    aoi_name = optional_string(payload, "aoi_name")
    provider = optional_string(payload, "provider") or "copernicus"
    queue = container().task_queue
    cnt = container()
    task_id = str(uuid.uuid4())

    def _run_scan() -> dict[str, object]:
        scan = cnt.create_scan.execute(
            bbox,
            aoi_name=aoi_name,
            start_date=start_date,
            end_date=end_date,
            provider=provider,
            progress_callback=lambda progress, message: queue.update_progress(
                task_id, min(75.0, progress * 0.75), message
            ),
        )
        if hasattr(cnt, "post_acquisition_pipeline") and cnt.post_acquisition_pipeline is not None:
            pipeline_result = cnt.post_acquisition_pipeline.execute(
                scan,
                progress_callback=lambda progress, message: queue.update_progress(
                    task_id, 75.0 + (progress * 0.25), message
                ),
            )
            return pipeline_result

        return {
            "folderName": scan.folder_name,
            "customName": scan.metadata.get("custom_name") or scan.folder_name,
            "imageUrl": scan_image_url(scan, cnt.settings.output_root),
            "bounds": [[bbox.min_latitude, bbox.min_longitude], [bbox.max_latitude, bbox.max_longitude]],
            "datetime": scan.acquisition.acquired_at.isoformat(),
            "provider": scan.metadata.get("provider", provider),
        }

    task = queue.submit("scan", task_id, _run_scan)
    return jsonify({
        "status": "success",
        "task_id": task.task_id,
        "task_status": task.status,
    }), 202


def serialize_task(task) -> dict[str, object]:
    terminal_statuses = {"COMPLETED", "FAILED", "CANCELLED"}
    active_statuses = {"PENDING", "QUEUED", "RUNNING"}
    is_terminal = task.status in terminal_statuses
    return {
        "task_id": task.task_id,
        "task_type": task.task_type,
        "status": task.status,
        "status_group": "ACTIVE" if task.status in active_statuses else "TERMINAL",
        "terminal": is_terminal,
        "progress": task.progress,
        "message": task.message,
        "scan_id": task.scan_id,
        "result": task.result,
        "error": task.error,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
    }


@blueprint.get("/api/tasks")
def list_tasks():
    status = request.args.get("status")
    task_type = request.args.get("task_type")
    limit = int(request.args.get("limit", 50))
    offset = int(request.args.get("offset", 0))
    queue = container().task_queue
    tasks = queue.list_tasks(status=status, task_type=task_type, limit=limit, offset=offset)
    return jsonify({
        "status": "success",
        "count": len(tasks),
        "tasks": [serialize_task(t) for t in tasks],
    })


@blueprint.get("/api/tasks/<task_id>")
def get_task_status(task_id: str):
    task = container().task_queue.get_task(task_id)
    if task is None:
        raise TaskNotFoundError(f"Task not found: {task_id}")
    return jsonify(serialize_task(task))


@blueprint.post("/api/tasks/<task_id>/cancel")
def cancel_task(task_id: str):
    queue = container().task_queue
    task = queue.get_task(task_id)
    if task is None:
        raise TaskNotFoundError(f"Task not found: {task_id}")
    cancelled = queue.cancel_task(task_id)
    if not cancelled:
        return jsonify({
            "status": "error",
            "message": f"Task {task_id} cannot be cancelled (current status: {task.status})",
            "task_id": task_id,
            "task_status": task.status,
        }), 400
    updated = queue.get_task(task_id)
    return jsonify({
        "status": "success",
        "message": "Task cancelled successfully",
        "task": serialize_task(updated) if updated else None,
    })


@blueprint.post("/api/tasks/recover")
def recover_crashed_tasks():
    queue = container().task_queue
    recovered = queue.recover_crashed_tasks()
    return jsonify({
        "status": "success",
        "recovered_count": len(recovered),
        "recovered_tasks": recovered,
    })
