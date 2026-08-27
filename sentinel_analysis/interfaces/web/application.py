"""Flask application factory."""

from flask import Flask

from sentinel_analysis.bootstrap.config import Settings
from sentinel_analysis.bootstrap.container import ApplicationContainer
from sentinel_analysis.interfaces.web import ais, aois, scans


def create_app(
    settings: Settings | None = None,
    container: ApplicationContainer | None = None,
) -> Flask:
    settings = settings or Settings.from_environment()
    container = container or ApplicationContainer(settings)
    app = Flask(
        __name__,
        template_folder=str(settings.project_root / "templates"),
        static_folder=str(settings.project_root / "static"),
        static_url_path="/static",
    )
    app.extensions["sentinel_container"] = container
    app.register_blueprint(scans.blueprint)
    app.register_blueprint(aois.blueprint)
    app.register_blueprint(ais.blueprint)
    return app

