"""Development entry point for Sentinel Imagery Analysis."""

from sentinel_analysis.bootstrap.config import Settings
from sentinel_analysis.application.shutdown import shutdown_coordinator
from sentinel_analysis.interfaces.web.application import create_app


settings = Settings.from_environment()
app = create_app(settings)


if __name__ == "__main__":
    shutdown_coordinator.install_signal_handlers()
    container = app.extensions.get("sentinel_container")
    try:
        app.run(debug=settings.debug, port=settings.port)
    except KeyboardInterrupt:
        pass
    finally:
        shutdown_coordinator.request_shutdown(reason="app.py exit", hard_deadline_seconds=3.0)
        if container is not None and hasattr(container, "shutdown"):
            container.shutdown(timeout=2.0)


