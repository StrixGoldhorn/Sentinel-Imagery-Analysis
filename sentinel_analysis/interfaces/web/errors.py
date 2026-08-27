"""HTTP error translation for expected application failures."""

from flask import Flask, current_app, jsonify
from werkzeug.exceptions import HTTPException

from sentinel_analysis.application.exceptions import (
    AreaOfInterestNotFoundError,
    AuthenticationError,
    ExternalServiceError,
    InvalidPredictionError,
    NoImageryFoundError,
    PluginNotFoundError,
    ScanNotFoundError,
)
from sentinel_analysis.domain.exceptions import DomainValidationError
from sentinel_analysis.interfaces.web.request_data import RequestValidationError


def _error(message: str, status: int):
    return jsonify(error=message), status


def register_error_handlers(app: Flask) -> None:
    @app.errorhandler(RequestValidationError)
    @app.errorhandler(DomainValidationError)
    @app.errorhandler(PluginNotFoundError)
    def invalid_request(exc: Exception):
        return _error(str(exc), 400)

    @app.errorhandler(ScanNotFoundError)
    @app.errorhandler(AreaOfInterestNotFoundError)
    @app.errorhandler(NoImageryFoundError)
    def not_found(exc: Exception):
        return _error(str(exc), 404)

    @app.errorhandler(ExternalServiceError)
    @app.errorhandler(AuthenticationError)
    @app.errorhandler(InvalidPredictionError)
    def dependency_failure(exc: Exception):
        return _error(str(exc), 502)

    @app.errorhandler(HTTPException)
    def http_error(exc: HTTPException):
        return _error(exc.description, exc.code or 500)

    @app.errorhandler(Exception)
    def unexpected_error(exc: Exception):
        current_app.logger.exception("Unhandled web request failure", exc_info=exc)
        return _error("Internal server error", 500)
