from sentinel_analysis.bootstrap.config import Settings
from sentinel_analysis.infrastructure.imagery.copernicus import CopernicusTokenProvider

def get_token() -> str:
    settings = Settings.from_environment()
    return CopernicusTokenProvider(
        settings.copernicus_username,
        settings.copernicus_password,
    ).get()
