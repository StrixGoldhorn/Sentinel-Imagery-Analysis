import requests
from datetime import datetime, timedelta, timezone

from utils.get_token import get_token

def main():
    dt = datetime.now(timezone.utc)
    time1 = (dt - timedelta(days=30)).isoformat().replace('+00:00', 'Z')
    time2 = dt.isoformat().replace('+00:00', 'Z')

    token = get_token()

    url = "https://sh.dataspace.copernicus.eu/api/v1/process"
    headers = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {token}"
    }

    data_pasir_panjang = {
    "input": {
        "bounds": {
        "bbox": [
            103.745957,
            1.221451,
            103.8517,
            1.312402
        ]
        },
        "data": [
        {
            "dataFilter": {
            "timeRange": {
                "from": "2026-05-01T00:00:00Z",
                "to": "2026-06-01T23:59:59Z"
            },
            "resolution": "HIGH"
            },
            "type": "sentinel-1-grd"
        }
        ]
    },
    "output": {
        "width": 2500,
        "height": 2150.773,
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

    data_cnb = {
    "input": {
        "bounds": {
        "bbox": [
            103.970834,
            1.254401,
            104.087219,
            1.369383
        ]
        },
        "data": [
        {
            "dataFilter": {
            "timeRange": {
                "from": "2026-05-01T00:00:00Z",
                "to": "2026-06-01T23:59:59Z"
            },
            "resolution": "HIGH"
            },
            "type": "sentinel-1-grd"
        }
        ]
    },
    "output": {
        "width": 2500,
        "height": 2470.455,
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

    data_random = {
    "input": {
        "bounds": {
        "bbox": [
            104.03864,
            1.211152,
            104.149704,
            1.320302
        ]
        },
        "data": [
        {
            "dataFilter": {
            "timeRange": {
                "from": "2026-05-05T00:00:00Z",
                "to": "2026-06-05T23:59:59Z"
            }
            },
            "type": "sentinel-1-grd"
        }
        ]
    },
    "output": {
        "width": 2472.165150936755,
        "height": 2430.1044840171635,
        "responses": [
        {
            "identifier": "default",
            "format": {
            "type": "image/tiff"
            }
        }
        ]
    },
    "evalscript": "//VERSION=3\nreturn [VH*2, dataMask];"
    }

    data_random_with_ground = {
        "input": {
            "bounds": {
            "bbox": [
                103.664932,
                1.156233,
                103.776855,
                1.265042
            ]
            },
            "data": [
            {
                "dataFilter": {
                "timeRange": {
                    "from": "2026-05-05T00:00:00Z",
                    "to": "2026-06-05T23:59:59Z"
                },
                "resolution": "HIGH"
                },
                "processing": {
                "speckleFilter": {
                    "type": "LEE",
                    "windowSizeX": 3,
                    "windowSizeY": 3
                }
                },
                "type": "sentinel-1-grd"
            }
            ]
        },
        "output": {
            "width": 2491.334907554566,
            "height": 2422.512494745063,
            "responses": [
            {
                "identifier": "default",
                "format": {
                "type": "image/png"
                }
            }
            ]
        },
        "evalscript": "//VERSION=3\nreturn [VH*2, dataMask];"
        }
    
    dem_random_with_ground = {
        "input": {
            "bounds": {
            "bbox": [
                103.664932,
                1.156233,
                103.776855,
                1.265042
            ]
            },
            "data": [
            {
                "dataFilter": {
                "timeRange": {
                    "from": "2026-05-05T00:00:00Z",
                    "to": "2026-06-05T23:59:59Z"
                }
                },
                "type": "dem"
            }
            ]
        },
        "output": {
            "width": 2491.334907554566,
            "height": 2422.512494745063,
            "responses": [
            {
                "identifier": "default",
                "format": {
                "type": "image/png"
                }
            }
            ]
        },
        "evalscript": "//VERSION=3\nconst colorRamp = [\n   [-12000, [0.000]],\n   [-9000, [0.098]],\n   [-6000, [0.216]],\n   [-1000, [0.243]],\n   [-500, [0.275]],\n   [-200, [0.294]],\n   [-50, [0.314]],\n   [-20, [0.333]],\n   [-10, [0.353]],\n   [0, [0.392]],\n   [10, [0.431]],\n   [30,[0.510]],\n   [50, [0.549]],\n   [200, [0.627]],\n   [300, [0.706]],\n   [400, [0.784]],\n   [500, [0.843]],\n   [1000, [0.882]],\n   [3000, [0.922]],\n   [5000, [0.961]],\n   [7000, [0.980]],\n   [9000, [1.000]]\n];\n\nconst viz = new ColorRampVisualizer(colorRamp);\n\nfunction setup() {\n  return {\n    input: [\"DEM\", \"dataMask\"],\n    output: {bands: 2},\n  };\n}\n\nfunction evaluatePixel(samples) {\n  return [...viz.process(samples.DEM),samples.dataMask];\n}"
        }

    print(f"Requesting with time from {time1} to {time2}")

    # response = requests.post(url, headers = headers, json = data_cnb, timeout = 300)
    # response = requests.post(url, headers = headers, json = data_pasir_panjang, timeout = 300)
    # response = requests.post(url, headers = headers, json = data_random, timeout = 300)
    
    # response = requests.post(url, headers = headers, json = data_random_with_ground, timeout = 300)
    response = requests.post(url, headers = headers, json = dem_random_with_ground, timeout = 300)

    dt_str = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z').replace(":", "")
    output_file = f"sentinel1_output_{dt_str}.tiff"
    output_file = f"sentinel1_output_{dt_str}.png"

    if response.status_code == 200:
        with open(output_file, "wb") as f:
            f.write(response.content)
        print(f"TIFF image downloaded successfully as '{output_file}'")
    else:
        print(f"Error downloading image. Status code: {response.status_code}")
        print(response.text)

if __name__ == "__main__":
    main()
