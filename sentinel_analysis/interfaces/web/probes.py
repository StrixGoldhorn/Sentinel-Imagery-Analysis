"""Health, readiness, and liveness probes for production orchestration."""

from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3
import time
from typing import Any

from flask import Blueprint, jsonify

from sentinel_analysis.interfaces.web.dependencies import container

blueprint = Blueprint("probes", __name__)
START_TIME = time.time()


@blueprint.get("/healthz")
@blueprint.get("/livez")
def liveness_probe():
    """Liveness probe: verifies process is alive and responsive."""
    uptime = round(time.time() - START_TIME, 2)
    return jsonify({
        "status": "healthy",
        "uptime_seconds": uptime,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "version": "1.0.0",
    }), 200


@blueprint.get("/readyz")
def readiness_probe():
    """Readiness probe: validates connectivity to SQLite database, storage paths, and queues."""
    checks: dict[str, Any] = {}
    is_ready = True
    c = container()

    # 1. Database check
    db_path = getattr(c.settings, "database_path", None)
    if db_path:
        try:
            db_file = Path(db_path)
            db_file.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(str(db_file), timeout=2.0) as conn:
                conn.execute("SELECT 1;").fetchone()
            checks["database"] = {"status": "ok", "path": str(db_file)}
        except Exception as exc:
            checks["database"] = {"status": "error", "error": str(exc)}
            is_ready = False
    else:
        checks["database"] = {"status": "ok", "note": "in-memory / mock"}

    # 2. Storage / Output root check
    output_root = getattr(c.settings, "output_root", None)
    if output_root:
        try:
            out_path = Path(output_root)
            out_path.mkdir(parents=True, exist_ok=True)
            test_file = out_path / ".probe_test"
            test_file.touch(exist_ok=True)
            test_file.unlink(missing_ok=True)
            checks["output_storage"] = {"status": "ok", "path": str(out_path)}
        except Exception as exc:
            checks["output_storage"] = {"status": "error", "error": str(exc)}
            is_ready = False
    else:
        checks["output_storage"] = {"status": "ok", "note": "not configured"}

    # 3. Cache root check
    cache_root = getattr(c.settings, "cache_root", None)
    if cache_root:
        try:
            c_path = Path(cache_root)
            c_path.mkdir(parents=True, exist_ok=True)
            checks["cache_storage"] = {"status": "ok", "path": str(c_path)}
        except Exception as exc:
            checks["cache_storage"] = {"status": "error", "error": str(exc)}
            is_ready = False
    else:
        checks["cache_storage"] = {"status": "ok", "note": "not configured"}

    # 4. Background task queue check
    task_queue = getattr(c, "task_queue", None)
    if task_queue is not None:
        is_active = not getattr(task_queue, "_is_shutdown", False)
        checks["task_queue"] = {
            "status": "ok" if is_active else "shutdown",
            "active": is_active,
        }
        if not is_active:
            is_ready = False
    else:
        checks["task_queue"] = {"status": "ok", "note": "uninitialized"}

    uptime = round(time.time() - START_TIME, 2)
    response_payload = {
        "status": "ready" if is_ready else "not_ready",
        "ready": is_ready,
        "uptime_seconds": uptime,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "checks": checks,
    }
    status_code = 200 if is_ready else 503
    return jsonify(response_payload), status_code
