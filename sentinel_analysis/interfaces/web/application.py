"""Flask application factory."""

from flask import Flask

from sentinel_analysis.bootstrap.config import Settings
from sentinel_analysis.bootstrap.container import ApplicationContainer
from sentinel_analysis.interfaces.web import ais, aois, scans, schedule, scrapers, tasks
from sentinel_analysis.interfaces.web.errors import register_error_handlers


def create_app(
    settings: Settings | None = None,
    container: ApplicationContainer | None = None,
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
    app.register_blueprint(scans.blueprint)
    app.register_blueprint(aois.blueprint)
    app.register_blueprint(ais.blueprint)
    app.register_blueprint(schedule.blueprint)
    app.register_blueprint(scrapers.blueprint)
    app.register_blueprint(tasks.blueprint)



    @app.after_request
    def secure_response(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        if response.content_type.startswith("application/json"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    if getattr(container, "pass_scheduler", None) is not None:
        container.pass_scheduler.start()

    return app

