from sentinel_analysis.bootstrap.config import Settings as ApplicationSettings

class Settings:
    _settings = ApplicationSettings.from_environment()
    USERNAME = _settings.copernicus_username
    PASSWORD = _settings.copernicus_password
