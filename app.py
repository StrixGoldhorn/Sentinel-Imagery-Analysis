"""Development entry point for Sentinel Imagery Analysis."""

from sentinel_analysis.bootstrap.config import Settings
from sentinel_analysis.interfaces.web.application import create_app


settings = Settings.from_environment()
app = create_app(settings)


if __name__ == "__main__":
    app.run(debug=settings.debug, port=settings.port)

