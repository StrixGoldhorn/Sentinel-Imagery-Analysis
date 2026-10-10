"""Web routes for the analyst review queue, contact dispositions, and dataset exports."""

import json
from flask import Blueprint, jsonify, render_template, request, Response

from sentinel_analysis.interfaces.web.dependencies import container

blueprint = Blueprint("review", __name__)


@blueprint.route("/review", methods=["GET"])
def review_page():
    """Render the Analyst Review Queue and verification cockpit."""
    return render_template("review.html")


@blueprint.route("/api/review/queue", methods=["GET"])
def get_review_queue():
    """List review queue contacts with disposition filters and summary statistics."""
    app_container = container()
    disposition = request.args.get("disposition") or "all"
    scan_id = request.args.get("scan_id")
    limit = max(1, min(500, int(request.args.get("limit", 100))))
    offset = max(0, int(request.args.get("offset", 0)))
    sync = request.args.get("sync", "true").lower() in ("1", "true", "yes")

    result = app_container.list_review_queue.execute(
        disposition=disposition,
        scan_id=scan_id,
        limit=limit,
        offset=offset,
        sync_pending=sync,
    )
    return jsonify({
        "success": True,
        "reviews": result["reviews"],
        "stats": result["stats"],
        "total": result["total"],
        "limit": result["limit"],
        "offset": result["offset"],
    })


@blueprint.route("/api/review/<review_id>", methods=["GET"])
def get_review_by_id(review_id: str):
    """Retrieve a single review record and its complete immutable audit history trail."""
    app_container = container()
    record = app_container.get_review_details.execute(review_id=review_id)
    if record is None:
        return jsonify({"success": False, "error": f"Review not found: {review_id}"}), 404
    return jsonify({
        "success": True,
        "review": record.to_dict(),
    })


@blueprint.route("/api/review/<review_id>/crop", methods=["GET"])
def get_review_crop(review_id: str):
    """Retrieve cropped radar imagery for a review record with surroundings context."""
    app_container = container()
    record = app_container.get_review_details.execute(review_id=review_id)
    if record is None:
        return jsonify({"success": False, "error": f"Review not found: {review_id}"}), 404
    from sentinel_analysis.interfaces.web.scans import get_detection_crop
    return get_detection_crop(record.scan_id, requested_idx=record.detection_idx)


@blueprint.route("/api/review/scan/<scan_id>/detection/<int:detection_idx>", methods=["GET"])
def get_review_by_scan_and_index(scan_id: str, detection_idx: int):
    """Retrieve review record for a specific contact detection in a scan."""
    app_container = container()
    record = app_container.get_review_details.execute(scan_id=scan_id, detection_idx=detection_idx)
    if record is None:
        return jsonify({"success": False, "error": f"Review not found for {scan_id} #{detection_idx}"}), 404
    return jsonify({
        "success": True,
        "review": record.to_dict(),
    })


@blueprint.route("/api/review/submit", methods=["POST"])
def submit_review():
    """Submit an analyst review decision (accept/reject/uncertain) with optional corrected box and comments."""
    app_container = container()
    payload = request.get_json(silent=True) or {}

    scan_id = payload.get("scan_id")
    if not scan_id:
        return jsonify({"success": False, "error": "Missing required 'scan_id' parameter"}), 400

    detection_idx = payload.get("detection_idx")
    if detection_idx is None:
        return jsonify({"success": False, "error": "Missing required 'detection_idx' parameter"}), 400
    try:
        detection_idx = int(detection_idx)
    except (ValueError, TypeError):
        return jsonify({"success": False, "error": "'detection_idx' must be an integer"}), 400

    disposition = payload.get("disposition")
    if not disposition:
        return jsonify({"success": False, "error": "Missing required 'disposition' parameter"}), 400

    # Reviewer identity from header, payload, or fallback
    reviewer_id = (
        request.headers.get("X-Analyst-ID")
        or request.headers.get("X-User-ID")
        or payload.get("reviewer_id")
        or "analyst"
    )

    corrected_bbox = payload.get("corrected_bbox")
    comments = payload.get("comments")
    confidence = payload.get("confidence")
    if confidence is not None:
        try:
            confidence = float(confidence)
        except (ValueError, TypeError):
            confidence = None

    original_bbox = payload.get("original_bbox")
    vessel_class = payload.get("vessel_class")
    reason_codes = payload.get("reason_codes")

    record = app_container.submit_review.execute(
        scan_id=str(scan_id),
        detection_idx=detection_idx,
        disposition=str(disposition),
        reviewer_id=str(reviewer_id),
        corrected_bbox=corrected_bbox,
        comments=comments,
        confidence=confidence,
        original_bbox=original_bbox,
        vessel_class=vessel_class,
        reason_codes=reason_codes,
    )

    return jsonify({
        "success": True,
        "review": record.to_dict(),
    })


@blueprint.route("/api/review/datasets/benchmark", methods=["GET"])
def get_benchmark_dataset():
    """Calculate and retrieve benchmark dataset evaluation comparing detector vs analyst ground truth."""
    app_container = container()
    iou_thresh = 0.5
    thresh_arg = request.args.get("iou_threshold")
    if thresh_arg is not None:
        try:
            iou_thresh = float(thresh_arg)
        except ValueError:
            pass

    benchmark_data = app_container.export_reviewed_dataset.build_benchmark_dataset(iou_threshold=iou_thresh)
    return jsonify({
        "success": True,
        "benchmark": benchmark_data,
    })


@blueprint.route("/api/review/datasets/retraining", methods=["GET"])
def get_retraining_dataset():
    """Retrieve ML retraining dataset manifest containing positive and negative sample annotations."""
    app_container = container()
    fmt = request.args.get("format") or "yolo"
    retraining_data = app_container.export_reviewed_dataset.build_retraining_dataset(format_type=fmt)
    return jsonify({
        "success": True,
        "dataset": retraining_data,
    })


@blueprint.route("/api/review/datasets/build", methods=["POST"])
def build_datasets():
    """Compile and write benchmark and retraining datasets to disk."""
    app_container = container()
    retraining_data = app_container.export_reviewed_dataset.build_retraining_dataset(format_type="yolo")
    benchmark_data = app_container.export_reviewed_dataset.build_benchmark_dataset(iou_threshold=0.5)

    return jsonify({
        "success": True,
        "message": "Benchmark and retraining datasets compiled successfully",
        "retraining_summary": retraining_data.get("summary", {}),
        "benchmark_metrics": benchmark_data.get("metrics", {}),
    })


@blueprint.route("/api/review/datasets/export", methods=["GET"])
def export_dataset_bundle():
    """Download the complete reviewed dataset manifest as a JSON file."""
    app_container = container()
    retraining_data = app_container.export_reviewed_dataset.build_retraining_dataset(format_type="yolo")
    benchmark_data = app_container.export_reviewed_dataset.build_benchmark_dataset(iou_threshold=0.5)

    bundle = {
        "version": "1.0.0",
        "retraining_dataset": retraining_data,
        "benchmark_dataset": benchmark_data,
    }
    json_bytes = json.dumps(bundle, indent=2).encode("utf-8")
    return Response(
        json_bytes,
        mimetype="application/json",
        headers={"Content-Disposition": "attachment; filename=maritime_analyst_review_dataset.json"},
    )
