import requests
from utils.config import Settings

def get_token() -> str:
    auth_server_url = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"

    data = {
        "client_id": "cdse-public",
        "username": Settings.USERNAME,
        "password": Settings.PASSWORD,
        "grant_type": "password",
    }

    response = requests.post(auth_server_url, data=data).json()
    access_token = response.get("access_token")
    return access_token
