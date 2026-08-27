"""Access dependencies attached by the Flask application factory."""

from flask import current_app

from sentinel_analysis.bootstrap.container import ApplicationContainer


def container() -> ApplicationContainer:
    """Return the request-scoped view of the application container."""

    return current_app.extensions["sentinel_container"]
