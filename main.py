import requests
from config import Settings

url = "https://sh.dataspace.copernicus.eu/api/v1/process"
headers = {
  "Content-Type": "application/json",
  "Authorization": f"Bearer {Settings.API_KEY}"
}
data = {
  "input": {
    "bounds": {
      "bbox": [
        103.563272,
        1.117788,
        104.137371,
        1.491226
      ]
    },
    "data": [
      {
        "dataFilter": {
          "timeRange": {
            "from": "2026-04-30T00:00:00Z",
            "to": "2026-05-31T23:59:59Z"
          }
        },
        "type": "sentinel-1-grd"
      }
    ]
  },
  "output": {
    "width": 2500,
    "height": 1626.501,
    "responses": [
      {
        "identifier": "default",
        "format": {
          "type": "image/tiff"
        }
      }
    ]
  },
  "evalscript": "return [VH*2]"
}

response = requests.post(url, headers=headers, json=data)

if response.status_code == 200:
    output_file = "sentinel2_output.tif"
    with open(output_file, "wb") as f:
        f.write(response.content)
    print(f"TIFF image downloaded successfully as '{output_file}'")
else:
    print(f"Error downloading image. Status code: {response.status_code}")
    print(response.text)