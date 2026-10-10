"""Flask application factory."""

import os
from flask import Flask, request

from sentinel_analysis.bootstrap.config import Settings
from sentinel_analysis.bootstrap.container import ApplicationContainer
from sentinel_analysis.domain.correlation import (
    generate_correlation_id,
    get_current_correlation_id,
    set_current_correlation_id,
)
from sentinel_analysis.interfaces.web import (
    ais,
    alerts,
    aois,
    observability,
    probes,
    review,
    scans,
    schedule,
    scrapers,
    storage,
    tasks,
)
from sentinel_analysis.interfaces.web.errors import register_error_handlers
from sentinel_analysis.interfaces.web.security import setup_security
from sentinel_analysis.interfaces.web.settings import blueprint as settings_blueprint


def create_app(
    settings: Settings | None = None,
    container: ApplicationContainer | None = None,
    start_background_workers: bool = True,
) -> Flask:
    if settings is None:
        settings = container.settings if container is not None else Settings.from_environment()
    elif container is not None and container.settings != settings:
        raise ValueError("Injected container settings do not match application settings")
    container = container or ApplicationContainer(settings)
    app = Flask(
        __name__,
        template_folder=str(settings.project_root / "templates"),
        static_folder=str(settings.project_root / "static"),
        static_url_path="/static",
    )
    app.config.update(
        DEBUG=settings.debug,
        MAX_CONTENT_LENGTH=1024 * 1024,
    )
    app.json.sort_keys = False
    app.extensions["sentinel_container"] = container
    register_error_handlers(app)
    setup_security(app, settings)
    app.register_blueprint(probes.blueprint)
    app.register_blueprint(scans.blueprint)
    app.register_blueprint(aois.blueprint)
    app.register_blueprint(ais.blueprint)
    app.register_blueprint(schedule.blueprint)
    app.register_blueprint(scrapers.blueprint)
    app.register_blueprint(tasks.blueprint)
    app.register_blueprint(alerts.blueprint)
    app.register_blueprint(storage.blueprint)
    app.register_blueprint(review.blueprint)
    app.register_blueprint(observability.blueprint)
    app.register_blueprint(settings_blueprint)

    @app.before_request
    def handle_correlation_id():
        cid = request.headers.get("X-Correlation-ID")
        if not cid:
            cid = generate_correlation_id(prefix="req")
        set_current_correlation_id(cid)

    @app.after_request
    def secure_response(response):
        cid = get_current_correlation_id()
        if cid:
            response.headers["X-Correlation-ID"] = cid
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        if response.content_type.startswith("application/json"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    @app.teardown_request
    def cleanup_correlation(exc=None):
        set_current_correlation_id(None)

    reloader_child = os.environ.get("WERKZEUG_RUN_MAIN") == "true"
    run_scheduler_env = os.environ.get("SENTINEL_RUN_SCHEDULER", "").strip().lower()
    scheduler_enabled = run_scheduler_env not in {"0", "false", "no", "off"}
    should_start_workers = (
        start_background_workers
        and scheduler_enabled
        and (not settings.debug or reloader_child)
    )
    if should_start_workers and getattr(container, "pass_scheduler", None) is not None:
        container.pass_scheduler.start()

    return app
