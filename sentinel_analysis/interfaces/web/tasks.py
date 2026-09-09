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
    queue = container().task_queue
    cnt = container()
    task_id = str(uuid.uuid4())

    def _run_scan() -> dict[str, object]:
        scan = cnt.create_scan.execute(
            bbox,
            aoi_name=aoi_name,
            start_date=start_date,
            end_date=end_date,
            progress_callback=lambda progress, message: queue.update_progress(task_id, progress, message),
        )
        return {
            "folderName": scan.folder_name,
            "customName": scan.metadata.get("custom_name") or scan.folder_name,
            "imageUrl": scan_image_url(scan, cnt.settings.output_root),
            "bounds": [[bbox.min_latitude, bbox.min_longitude], [bbox.max_latitude, bbox.max_longitude]],
            "datetime": scan.acquisition.acquired_at.isoformat(),
        }

    task = queue.submit("scan", task_id, _run_scan)
    return jsonify({
        "status": "success",
        "task_id": task.task_id,
        "task_status": task.status,
    }), 202


@blueprint.get("/api/tasks/<task_id>")
def get_task_status(task_id: str):
    task = container().task_queue.get_task(task_id)
    if task is None:
        raise TaskNotFoundError(f"Task not found: {task_id}")

    return jsonify({
        "task_id": task.task_id,
        "task_type": task.task_type,
        "status": task.status,
        "progress": task.progress,
        "message": task.message,
        "result": task.result,
        "error": task.error,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
    })
