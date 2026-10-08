"""Web routes for storage monitoring, scan archival, and disk cleanup."""

import logging
from pathlib import Path
from flask import Blueprint, jsonify, request, send_file

from sentinel_analysis.interfaces.web.dependencies import container

logger = logging.getLogger(__name__)
blueprint = Blueprint("storage", __name__)


@blueprint.route("/api/storage/quota", methods=["GET"])
def get_quota():
    """Retrieve disk quota and storage breakdown across scans, cache, and database."""
    app_container = container()
    quota_bytes_param = request.args.get("quota_bytes", type=int)
    report = app_container.get_storage_quota.execute(quota_bytes=quota_bytes_param)
    return jsonify({
        "success": True,
        "quota": {
            "total_bytes_used": report.total_bytes_used,
            "quota_bytes": report.quota_bytes,
            "usage_percent": report.usage_percent,
            "scans_bytes": report.scans_bytes,
            "cache_bytes": report.cache_bytes,
            "database_bytes": report.database_bytes,
            "scan_count": report.scan_count,
            "oldest_scan_date": report.oldest_scan_date.isoformat() if report.oldest_scan_date else None,
            "quota_exceeded": report.quota_exceeded,
        },
    })


@blueprint.route("/api/storage/cleanup", methods=["POST"])
def cleanup_storage():
    """Execute retention policies to compress expired scans and prune stale tile cache."""
    app_container = container()
    payload = request.get_json(silent=True) or {}
    scan_days = payload.get("scan_retention_days")
    cache_days = payload.get("cache_retention_days")
    if scan_days is not None:
        try:
            scan_days = int(scan_days)
        except (ValueError, TypeError):
            return jsonify({"error": "scan_retention_days must be an integer"}), 400
    if cache_days is not None:
        try:
            cache_days = int(cache_days)
        except (ValueError, TypeError):
            return jsonify({"error": "cache_retention_days must be an integer"}), 400

    outcome = app_container.execute_storage_retention.execute(
        scan_retention_days=scan_days,
        cache_retention_days=cache_days,
    )
    return jsonify({
        "success": True,
        "outcome": {
            "archived_scans": list(outcome.archived_scans),
            "pruned_cache_files": outcome.pruned_cache_files,
            "bytes_freed": outcome.bytes_freed,
            "archive_paths": list(outcome.archive_paths),
            "timestamp": outcome.timestamp.isoformat() if outcome.timestamp else None,
        },
    })


@blueprint.route("/api/storage/scans/<folder_name>/archive", methods=["POST"])
def archive_scan_route(folder_name: str):
    """Compress a specific scan folder into an archived .tar.gz bundle."""
    app_container = container()
    payload = request.get_json(silent=True) or {}
    remove_orig = bool(payload.get("remove_original", False))
    try:
        archive_path = app_container.archive_scan.execute(folder_name, remove_original=remove_orig)
        return jsonify({
            "success": True,
            "folder_name": folder_name,
            "archive_path": str(archive_path),
            "archive_size_bytes": archive_path.stat().st_size,
        })
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@blueprint.route("/api/storage/scans/<folder_name>/export", methods=["GET"])
def export_scan_archive(folder_name: str):
    """Download an archived scan as a .tar.gz file."""
    app_container = container()
    try:
        archive_path = app_container.storage_manager.get_archive_path(folder_name)
        if archive_path is None or not archive_path.is_file():
            archive_path = app_container.archive_scan.execute(folder_name, remove_original=False)
        return send_file(
            str(archive_path),
            as_attachment=True,
            download_name=f"{Path(folder_name).name}.tar.gz",
            mimetype="application/gzip",
        )
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
