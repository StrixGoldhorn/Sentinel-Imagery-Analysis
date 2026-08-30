"""Task status and asynchronous scanning HTTP routes."""

from flask import Blueprint, jsonify, request

from sentinel_analysis.domain.entities import BoundingBox
from sentinel_analysis.interfaces.web.dependencies import container
from sentinel_analysis.interfaces.web.request_data import (
    RequestValidationError,
    bounding_box,
    json_object,
)
from sentinel_analysis.interfaces.web.serialization import scan_image_url

blueprint = Blueprint("tasks", __name__)


@blueprint.post("/api/tasks/scan")
def create_async_scan():
    payload = json_object()
    bbox = bounding_box(payload)
    queue = container().task_queue
    cnt = container()

    def _run_scan() -> dict[str, object]:
        scan = cnt.create_scan.execute(bbox)
        return {
            "folderName": scan.folder_name,
            "imageUrl": scan_image_url(scan, cnt.settings.output_root),
            "bounds": [[bbox.min_latitude, bbox.min_longitude], [bbox.max_latitude, bbox.max_longitude]],
            "datetime": scan.acquisition.acquired_at.isoformat(),
        }

    task = queue.submit("scan", None, _run_scan)
    return jsonify({
        "status": "success",
        "task_id": task.task_id,
        "task_status": task.status,
    }), 202


@blueprint.get("/api/tasks/<task_id>")
def get_task_status(task_id: str):
    task = container().task_queue.get_task(task_id)
    if task is None:
        raise RequestValidationError(f"Task not found: {task_id}")

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
