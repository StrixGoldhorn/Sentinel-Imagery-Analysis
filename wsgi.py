"""Production WSGI entry point for Sentinel Imagery Analysis."""

from sentinel_analysis.bootstrap.config import Settings
from sentinel_analysis.bootstrap.container import ApplicationContainer
from sentinel_analysis.interfaces.web.application import create_app

settings = Settings.from_environment()
container = ApplicationContainer(settings)
app = create_app(settings, container, start_background_workers=True)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=settings.port)
