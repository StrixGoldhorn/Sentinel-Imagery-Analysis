"""CRS Inspector & Geospatial Advisor Utility.

Comprehensive utility for diagnosing coordinate reference systems (CRS),
analyzing raw coordinates/bounding boxes, inspecting geospatial metadata,
recommending optimal projections, and generating conversion code snippets.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class CRSDiagnosisResult:
    """Result of CRS diagnosis for coordinates, bbox, or dataset."""
    likely_crs: str
    epsg_code: Optional[int]
    confidence: str  # 'HIGH', 'MEDIUM', 'LOW'
    coordinate_type: str  # 'GEOGRAPHIC' or 'PROJECTED'
    units: str  # 'degrees', 'meters', 'feet', 'unknown'
    description: str
    warnings: List[str] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)
    approx_wgs84_coords: Optional[Tuple[float, float]] = None
    utm_zone_candidate: Optional[int] = None
    utm_epsg_candidate: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "likely_crs": self.likely_crs,
            "epsg_code": self.epsg_code,
            "confidence": self.confidence,
            "coordinate_type": self.coordinate_type,
            "units": self.units,
            "description": self.description,
            "warnings": self.warnings,
            "recommendations": self.recommendations,
            "approx_wgs84_coords": self.approx_wgs84_coords,
            "utm_zone_candidate": self.utm_zone_candidate,
            "utm_epsg_candidate": self.utm_epsg_candidate,
        }


def calculate_utm_zone(lon: float, lat: float) -> Tuple[int, int]:
    """Calculate UTM zone and EPSG code for a given WGS84 (lon, lat).

    Returns:
        Tuple of (zone_number, epsg_code).
        EPSG is 32600 + zone for North, 32700 + zone for South.
    """
    # Normalize longitude to [-180, 180)
    norm_lon = ((lon + 180.0) % 360.0) - 180.0
    zone = int((norm_lon + 180.0) / 6.0) + 1
    if zone > 60:
        zone = 60
    elif zone < 1:
        zone = 1

    # Northern hemisphere vs Southern hemisphere
    epsg = 32600 + zone if lat >= 0 else 32700 + zone
    return zone, epsg


def web_mercator_to_wgs84(x: float, y: float) -> Tuple[float, float]:
    """Inverse project Web Mercator (EPSG:3857) meters to WGS84 (lon, lat) degrees."""
    lon = (x / 20037508.342789244) * 180.0
    y_clamped = max(-20037508.34, min(20037508.34, y))
    lat = (y_clamped / 20037508.342789244) * 180.0
    lat = 180.0 / math.pi * (2.0 * math.atan(math.exp(lat * math.pi / 180.0)) - math.pi / 2.0)
    return lon, lat


def wgs84_to_web_mercator(lon: float, lat: float) -> Tuple[float, float]:
    """Project WGS84 (lon, lat) degrees to Web Mercator (EPSG:3857) meters."""
    x = lon * 20037508.342789244 / 180.0
    clamped_lat = max(-85.0511287798, min(85.0511287798, lat))
    y = math.log(math.tan((90.0 + clamped_lat) * math.pi / 360.0)) / (math.pi / 180.0)
    y = y * 20037508.342789244 / 180.0
    return x, y


def diagnose_coordinates(x: float, y: float) -> CRSDiagnosisResult:
    """Diagnose the coordinate system of a single (X, Y) point."""
    warnings: List[str] = []
    recs: List[str] = []

    # Case 1: Inverted geographic coordinates check: X in [-90, 90] and |Y| > 90 and |Y| <= 180
    if -90.0 <= x <= 90.0 and abs(y) > 90.0 and abs(y) <= 180.0:
        warnings.append(
            f"CRITICAL: Axis inversion detected! X={x} looks like Latitude and Y={y} looks like Longitude. "
            f"Cartesian / GeoJSON standard expects (X=longitude, Y=latitude)."
        )
        corrected_lon, corrected_lat = y, x
        zone, epsg = calculate_utm_zone(corrected_lon, corrected_lat)
        recs.append(f"Swap coordinates to (lon={corrected_lon}, lat={corrected_lat}).")
        recs.append(f"Matching UTM zone for this location is UTM Zone {zone} (EPSG:{epsg}).")
        return CRSDiagnosisResult(
            likely_crs="WGS 84 (Inverted: Latitude, Longitude)",
            epsg_code=4326,
            confidence="HIGH",
            coordinate_type="GEOGRAPHIC",
            units="degrees",
            description="Coordinates appear to be WGS84 decimal degrees with latitude and longitude swapped.",
            warnings=warnings,
            recommendations=recs,
            approx_wgs84_coords=(corrected_lon, corrected_lat),
            utm_zone_candidate=zone,
            utm_epsg_candidate=epsg,
        )

    # Case 2: Standard Geographic Coordinates: -180 <= x <= 180 and -90 <= y <= 90
    if -180.0 <= x <= 180.0 and -90.0 <= y <= 90.0:
        # Check if coordinates might still be swapped within the [-90, 90] overlapping box
        if abs(x) <= 90.0 and abs(y) <= 90.0:
            # Common US/Europe ambiguity check: if X is positive and Y is negative, e.g. (37.7, -122.4)
            if x > 0 and y < -30:
                warnings.append(
                    f"POSSIBLE INVERSION: X={x} is positive and Y={y} is strongly negative. "
                    f"In North America, Longitude is negative (~ -125 to -65) and Latitude is positive (~ 25 to 50). "
                    f"Check if (X, Y) should actually be ({y}, {x})."
                )
        zone, epsg = calculate_utm_zone(x, y)
        recs.append(f"For distance/area calculations, reproject to local UTM Zone {zone} (EPSG:{epsg}).")
        recs.append("When using pyproj, always initialize Transformer with always_xy=True.")
        return CRSDiagnosisResult(
            likely_crs="WGS 84 / Geographic 2D",
            epsg_code=4326,
            confidence="HIGH" if not warnings else "MEDIUM",
            coordinate_type="GEOGRAPHIC",
            units="degrees",
            description="Angular coordinates in decimal degrees, matching WGS 84 (EPSG:4326) or NAD83 (EPSG:4269).",
            warnings=warnings,
            recommendations=recs,
            approx_wgs84_coords=(x, y),
            utm_zone_candidate=zone,
            utm_epsg_candidate=epsg,
        )

    # Case 3: Web Mercator (EPSG:3857)
    # Bounds: |X| <= 20037508.34 and |Y| <= 20037508.34, typically > 180
    if abs(x) <= 20037509.0 and abs(y) <= 20037509.0 and (abs(x) > 180.0 or abs(y) > 90.0):
        # Additional check: could this be UTM? UTM Northing is at most 10,000,000m, Easting 100k-900k
        is_utm_candidate = (100000.0 <= x <= 900000.0) and (0.0 <= y <= 10000000.0)
        
        # Test Web Mercator inversion
        w_lon, w_lat = web_mercator_to_wgs84(x, y)
        if not is_utm_candidate:
            zone, epsg = calculate_utm_zone(w_lon, w_lat)
            recs.append("EPSG:3857 is for web map tile display only! Do NOT calculate distances or areas in this CRS.")
            recs.append(f"Approximate geographic location: {w_lon:.5f}° lon, {w_lat:.5f}° lat (UTM Zone {zone}, EPSG:{epsg}).")
            return CRSDiagnosisResult(
                likely_crs="WGS 84 / Pseudo-Mercator (Web Mercator)",
                epsg_code=3857,
                confidence="HIGH",
                coordinate_type="PROJECTED",
                units="meters",
                description="Projected planar coordinates matching global Web Mercator meters.",
                warnings=warnings,
                recommendations=recs,
                approx_wgs84_coords=(w_lon, w_lat),
                utm_zone_candidate=zone,
                utm_epsg_candidate=epsg,
            )

    # Case 4: British National Grid (EPSG:27700) or Equatorial UTM
    # Bounds: 0 <= X <= 700000 and 0 <= Y <= 1300000
    if (0.0 <= x <= 700000.0) and (0.0 <= y <= 1300000.0):
        recs.append("If this dataset covers the United Kingdom, it is British National Grid (OSGB36, EPSG:27700).")
        recs.append("If near the equator (within ~11° latitude, e.g. Singapore, Kenya, Colombia), this could also be an equatorial UTM zone.")
        return CRSDiagnosisResult(
            likely_crs="OSGB36 / British National Grid",
            epsg_code=27700,
            confidence="MEDIUM",
            coordinate_type="PROJECTED",
            units="meters",
            description="Coordinates fit the official envelope of the British National Grid (England, Scotland, Wales) or an equatorial UTM zone.",
            warnings=warnings,
            recommendations=recs,
        )

    # Case 5: UTM Zone Coordinates (Mid/High latitudes: Northing > 1,300,000 m up to 10,000,000 m)
    # Easting is around 500,000 m (100,000 to 900,000) and Northing is 0 to 10,000,000 m
    if (100000.0 <= x <= 900000.0) and (0.0 <= y <= 10000000.0):
        recs.append("UTM coordinates provide accurate local planar measurements (scale distortion < 0.1%).")
        recs.append("UTM zone number cannot be derived from Easting/Northing alone; check file metadata or associated longitude.")
        return CRSDiagnosisResult(
            likely_crs="Universal Transverse Mercator (UTM Zone)",
            epsg_code=None,
            confidence="MEDIUM",
            coordinate_type="PROJECTED",
            units="meters",
            description=f"Coordinates (Easting={x:.1f} m, Northing={y:.1f} m) strongly characteristic of a UTM Zone projection.",
            warnings=warnings,
            recommendations=recs,
        )

    # Case 6: US State Plane (Meters or Feet)
    # Often 6 to 8 digits (1,000,000 to 30,000,000)
    if (1000000.0 <= x <= 30000000.0) and (100000.0 <= y <= 10000000.0):
        recs.append("Examine dataset state/county to identify the specific SPCS zone (NAD83 or NAD27).")
        return CRSDiagnosisResult(
            likely_crs="US State Plane Coordinate System (SPCS)",
            epsg_code=None,
            confidence="LOW",
            coordinate_type="PROJECTED",
            units="feet or meters",
            description="Large projected coordinates matching typical US State Plane coordinate envelopes.",
            warnings=warnings,
            recommendations=recs,
        )

    # Default fallback
    return CRSDiagnosisResult(
        likely_crs="Unknown Projected / Custom CRS",
        epsg_code=None,
        confidence="LOW",
        coordinate_type="PROJECTED",
        units="unknown",
        description=f"Coordinate values ({x}, {y}) do not match standard geographic degrees or common global projections.",
        warnings=["Unrecognized coordinate magnitude. Check file metadata, projection header, or datum documentation."],
        recommendations=["Inspect source file with GDAL / rasterio / crs_inspector."],
    )


def diagnose_bbox(min_x: float, min_y: float, max_x: float, max_y: float) -> CRSDiagnosisResult:
    """Diagnose the coordinate system of a bounding box [min_x, min_y, max_x, max_y]."""
    center_x = (min_x + max_x) / 2.0
    center_y = (min_y + max_y) / 2.0
    res = diagnose_coordinates(center_x, center_y)

    # Validate min < max
    if min_x > max_x:
        res.warnings.append(f"Bounding box min_x ({min_x}) > max_x ({max_x}). Note: crosses anti-meridian (180°)?")
    if min_y > max_y:
        res.warnings.append(f"Bounding box min_y ({min_y}) > max_y ({max_y}). Check latitude ordering.")

    return res


def inspect_prj_file(prj_path: str | Path) -> Dict[str, Any]:
    """Parse an ESRI / OGC .prj WKT file to extract CRS information."""
    path = Path(prj_path)
    if not path.is_file():
        raise FileNotFoundError(f"PRJ file not found: {prj_path}")

    content = path.read_text(encoding="utf-8", errors="ignore").strip()
    return parse_wkt_crs(content)


def parse_wkt_crs(wkt: str) -> Dict[str, Any]:
    """Parse WKT string for CRS properties."""
    is_projected = wkt.startswith("PROJCS") or "PROJCRS" in wkt
    is_geographic = wkt.startswith("GEOGCS") or "GEOGCRS" in wkt

    # Extract EPSG code if present: AUTHORITY["EPSG","32610"]
    epsg_matches = re.findall(r'AUTHORITY\s*\[\s*"EPSG"\s*,\s*"(\d+)"\s*\]', wkt, re.IGNORECASE)
    epsg_code = int(epsg_matches[-1]) if epsg_matches else None

    # Extract CRS Name
    name_match = re.search(r'^(?:PROJCS|GEOGCS|PROJCRS|GEOGCRS)\s*\[\s*"([^"]+)"', wkt, re.IGNORECASE)
    crs_name = name_match.group(1) if name_match else "Unknown CRS"

    # Extract Datum
    datum_match = re.search(r'DATUM\s*\[\s*"([^"]+)"', wkt, re.IGNORECASE)
    datum = datum_match.group(1) if datum_match else "Unknown"

    # Extract Projection algorithm
    proj_match = re.search(r'PROJECTION\s*\[\s*"([^"]+)"', wkt, re.IGNORECASE)
    projection = proj_match.group(1) if proj_match else None

    # Extract Unit
    unit_match = re.search(r'UNIT\s*\[\s*"([^"]+)"', wkt, re.IGNORECASE)
    unit = unit_match.group(1) if unit_match else ("degrees" if is_geographic else "meters")

    return {
        "crs_name": crs_name,
        "is_projected": is_projected,
        "is_geographic": is_geographic,
        "epsg_code": epsg_code,
        "datum": datum,
        "projection": projection,
        "unit": unit,
        "raw_wkt": wkt,
    }


def inspect_geotiff_header(tif_path: str | Path) -> Dict[str, Any]:
    """Inspect a GeoTIFF header using rasterio if available, or fallback to pure Python TIFF tag parsing."""
    path = Path(tif_path)
    if not path.is_file():
        raise FileNotFoundError(f"TIFF file not found: {tif_path}")

    # Method 1: Try rasterio
    try:
        import rasterio
        with rasterio.open(str(path)) as src:
            return {
                "source": "rasterio",
                "crs": str(src.crs),
                "epsg": src.crs.to_epsg() if src.crs else None,
                "is_projected": src.crs.is_projected if src.crs else None,
                "bounds": {
                    "left": src.bounds.left,
                    "bottom": src.bounds.bottom,
                    "right": src.bounds.right,
                    "top": src.bounds.top,
                },
                "width": src.width,
                "height": src.height,
                "count": src.count,
                "transform": [src.transform[i] for i in range(9)],
            }
    except ImportError:
        pass
    except Exception as e:
        # Fall back to pure Python reader on failure
        pass

    # Method 2: Pure Python basic TIFF IFD reader
    return _inspect_tiff_pure_python(path)


def _inspect_tiff_pure_python(path: Path) -> Dict[str, Any]:
    """Parse TIFF tags without external dependencies."""
    with open(path, "rb") as f:
        header = f.read(4)
        if len(header) < 4:
            raise ValueError("File too small to be a valid TIFF.")

        if header[:2] == b"II":
            endian = "<"
        elif header[:2] == b"MM":
            endian = ">"
        else:
            raise ValueError(f"Not a TIFF file (magic bytes: {header[:2]!r})")

        magic_num = struct.unpack(endian + "H", header[2:4])[0]
        if magic_num != 42:
            raise ValueError(f"Invalid TIFF magic number: {magic_num}")

        ifd_offset = struct.unpack(endian + "I", f.read(4))[0]
        f.seek(ifd_offset)
        num_entries = struct.unpack(endian + "H", f.read(2))[0]

        tags: Dict[int, Any] = {}
        # GeoTIFF specific tag constants
        GEOTIFF_TAGS = {
            34735: "GeoKeyDirectoryTag",
            34736: "GeoDoubleParamsTag",
            34737: "GeoAsciiParamsTag",
            33550: "ModelPixelScaleTag",
            33922: "ModelTiepointTag",
            34264: "ModelTransformationTag",
            256: "ImageWidth",
            257: "ImageLength",
        }

        for _ in range(num_entries):
            entry = f.read(12)
            if len(entry) < 12:
                break
            tag, tag_type, count, val_or_offset = struct.unpack(endian + "HHII", entry)
            if tag in GEOTIFF_TAGS:
                tags[GEOTIFF_TAGS[tag]] = {"type": tag_type, "count": count, "offset": val_or_offset}

        has_geotiff_keys = "GeoKeyDirectoryTag" in tags or "ModelPixelScaleTag" in tags or "ModelTiepointTag" in tags
        return {
            "source": "pure_python_tiff_parser",
            "is_geotiff": has_geotiff_keys,
            "detected_tags": list(tags.keys()),
            "endian": "little (Intel)" if endian == "<" else "big (Motorola)",
            "note": "GeoTIFF tags found in TIFF header." if has_geotiff_keys else "Standard TIFF without GeoTIFF tags detected.",
        }


def inspect_geojson_file(geojson_path: str | Path) -> Dict[str, Any]:
    """Inspect a GeoJSON file to check for RFC 7946 compliance and coordinate validity."""
    path = Path(geojson_path)
    if not path.is_file():
        raise FileNotFoundError(f"GeoJSON file not found: {geojson_path}")

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Extract sample coordinate
    coords_sample: List[float] = []

    def _extract_first_coord(obj: Any) -> Optional[List[float]]:
        if isinstance(obj, (list, tuple)):
            if len(obj) >= 2 and isinstance(obj[0], (int, float)) and isinstance(obj[1], (int, float)):
                return [float(obj[0]), float(obj[1])]
            for item in obj:
                res = _extract_first_coord(item)
                if res:
                    return res
        elif isinstance(obj, dict):
            if "coordinates" in obj:
                return _extract_first_coord(obj["coordinates"])
            if "geometry" in obj and obj["geometry"]:
                return _extract_first_coord(obj["geometry"])
            if "features" in obj and obj["features"]:
                return _extract_first_coord(obj["features"])
        return None

    sample = _extract_first_coord(data)
    crs_declared = data.get("crs")

    diagnosis = None
    if sample:
        diagnosis = diagnose_coordinates(sample[0], sample[1])

    return {
        "type": data.get("type", "Unknown"),
        "has_declared_crs": crs_declared is not None,
        "declared_crs": crs_declared,
        "sample_coordinate": sample,
        "rfc7946_compliant": (diagnosis.likely_crs.startswith("WGS 84") and "Inverted" not in diagnosis.likely_crs) if diagnosis else None,
        "diagnosis": diagnosis.to_dict() if diagnosis else None,
    }


def recommend_crs(use_case: str, lon: Optional[float] = None, lat: Optional[float] = None, region: Optional[str] = None) -> Dict[str, Any]:
    """Recommend the optimal coordinate system for a given task and location."""
    normalized_use = use_case.lower().replace("-", "_").strip()
    norm_region = region.lower().strip() if region else ""

    recommendations: Dict[str, Any] = {
        "use_case": use_case,
        "recommended_crs": "",
        "epsg_code": None,
        "units": "",
        "rationale": "",
        "warning": None,
    }

    if "web" in normalized_use or "tile" in normalized_use or "display" in normalized_use:
        recommendations["recommended_crs"] = "WGS 84 / Pseudo-Mercator (Web Mercator)"
        recommendations["epsg_code"] = 3857
        recommendations["units"] = "meters"
        recommendations["rationale"] = "Industry standard for web tile services (Leaflet, Mapbox, Google Maps, OpenStreetMap)."
        recommendations["warning"] = "EPSG:3857 is for visual display ONLY. Never calculate distance or area directly in Web Mercator."

    elif "storage" in normalized_use or "api" in normalized_use or "gps" in normalized_use or "ais" in normalized_use:
        recommendations["recommended_crs"] = "WGS 84 (Geographic 2D)"
        recommendations["epsg_code"] = 4326
        recommendations["units"] = "decimal degrees"
        recommendations["rationale"] = "Universal standard for GPS receivers, AIS telemetry, and GeoJSON (RFC 7946). Axis order: (longitude, latitude)."

    elif "area" in normalized_use or "acre" in normalized_use or "biomass" in normalized_use:
        if "us" in norm_region or "conus" in norm_region or "america" in norm_region:
            recommendations["recommended_crs"] = "NAD83 / Conus Albers"
            recommendations["epsg_code"] = 5070
            recommendations["units"] = "meters"
            recommendations["rationale"] = "Equal-area conic projection preserving true surface areas across the continental United States."
        elif "eu" in norm_region or "europe" in norm_region:
            recommendations["recommended_crs"] = "ETRS89-extended / LAEA Europe"
            recommendations["epsg_code"] = 3035
            recommendations["units"] = "meters"
            recommendations["rationale"] = "Official European Commission standard for equal-area statistical and environmental mapping."
        else:
            recommendations["recommended_crs"] = "Equal Earth / Geodesic Area"
            recommendations["epsg_code"] = 8857
            recommendations["units"] = "meters"
            recommendations["rationale"] = "Equal Earth (EPSG:8857) or direct ellipsoidal geodesic area computation (pyproj.Geod)."

    elif "radar" in normalized_use or "circle" in normalized_use or "bearing" in normalized_use:
        center_lon = lon if lon is not None else 0.0
        center_lat = lat if lat is not None else 0.0
        recommendations["recommended_crs"] = f"Azimuthal Equidistant (centered at {center_lon}, {center_lat})"
        recommendations["epsg_code"] = None
        recommendations["units"] = "meters"
        recommendations["rationale"] = f"Preserves exact distances and bearings from the center point (+proj=aeqd +lat_0={center_lat} +lon_0={center_lon})."

    elif "sentinel" in normalized_use or "sar" in normalized_use or "satellite" in normalized_use or "local" in normalized_use or "distance" in normalized_use:
        if lon is not None and lat is not None:
            zone, epsg = calculate_utm_zone(lon, lat)
            recommendations["recommended_crs"] = f"WGS 84 / UTM Zone {zone}{'N' if lat >= 0 else 'S'}"
            recommendations["epsg_code"] = epsg
            recommendations["units"] = "meters"
            recommendations["rationale"] = (
                f"Sentinel-2 tiles and geocoded Sentinel-1 SAR products use UTM Zone {zone} "
                f"with minimal scale distortion (<0.1%) and conformal shape preservation."
            )
        else:
            recommendations["recommended_crs"] = "Universal Transverse Mercator (UTM Local Zone)"
            recommendations["epsg_code"] = 32601  # Example
            recommendations["units"] = "meters"
            recommendations["rationale"] = "Provide longitude and latitude to obtain the exact UTM EPSG code (EPSG:326xx North, EPSG:327xx South)."

    else:
        recommendations["recommended_crs"] = "WGS 84 (EPSG:4326) or local UTM"
        recommendations["epsg_code"] = 4326
        recommendations["units"] = "degrees or meters"
        recommendations["rationale"] = "Specify whether your primary goal is web display, area computation, distance buffering, or satellite analysis."

    return recommendations


def generate_python_snippet(from_crs: str | int, to_crs: str | int) -> str:
    """Generate a clean, modern Python snippet for reprojecting coordinates or geometries."""
    return f'''# Python CRS Transformation Snippet
import pyproj

# Note: always_xy=True is essential to maintain standard (lon, lat) order!
transformer = pyproj.Transformer.from_crs("{from_crs}", "{to_crs}", always_xy=True)

# Example: Forward transformation
x_target, y_target = transformer.transform(x_source, y_source)

# Example: Reprojecting a GeoPandas GeoDataFrame
# import geopandas as gpd
# gdf = gpd.read_file("data.geojson")
# gdf_reprojected = gdf.to_crs("{to_crs}")
'''


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point for CRS Inspector."""
    parser = argparse.ArgumentParser(
        description="CRS Inspector & Geospatial Advisor - Detect and diagnose coordinate reference systems."
    )
    parser.add_argument("coords_or_file", nargs="*", help="File path (TIF, PRJ, GeoJSON) OR coordinate X Y (e.g. -122.4 37.7)")
    parser.add_argument("--bbox", nargs=4, type=float, metavar=("MIN_X", "MIN_Y", "MAX_X", "MAX_Y"), help="Bounding box coordinates")
    parser.add_argument("--recommend", choices=["web", "storage", "area", "distance", "radar", "satellite"], help="Recommend CRS for a use-case")
    parser.add_argument("--lon", type=float, help="Longitude for recommendation/UTM calculation")
    parser.add_argument("--lat", type=float, help="Latitude for recommendation/UTM calculation")
    parser.add_argument("--region", type=str, help="Region for recommendation (e.g. us, europe, global)")
    parser.add_argument("--snippet", action="store_true", help="Print Python transformation snippet")
    parser.add_argument("--json", action="store_true", help="Output results in JSON format")

    args = parser.parse_args(argv)

    # Mode 1: Recommend CRS
    if args.recommend:
        rec = recommend_crs(args.recommend, lon=args.lon, lat=args.lat, region=args.region)
        if args.json:
            print(json.dumps(rec, indent=2))
        else:
            print("=" * 60)
            print(f"GEOSPATIAL CRS RECOMMENDATION: {args.recommend.upper()}")
            print("=" * 60)
            print(f"Recommended CRS : {rec['recommended_crs']}")
            if rec["epsg_code"]:
                print(f"EPSG Code       : {rec['epsg_code']}")
            print(f"Units           : {rec['units']}")
            print(f"Rationale       : {rec['rationale']}")
            if rec["warning"]:
                print(f"WARNING         : {rec['warning']}")
            if args.snippet and rec["epsg_code"]:
                print("-" * 60)
                print(generate_python_snippet(4326, rec["epsg_code"]))
        return 0

    # Mode 2: Diagnose Bounding Box
    if args.bbox:
        min_x, min_y, max_x, max_y = args.bbox
        diag = diagnose_bbox(min_x, min_y, max_x, max_y)
        if args.json:
            print(json.dumps(diag.to_dict(), indent=2))
        else:
            _print_diagnosis(diag, f"BBOX [{min_x}, {min_y}, {max_x}, {max_y}]")
            if args.snippet and diag.epsg_code:
                print("-" * 60)
                target = diag.utm_epsg_candidate if diag.coordinate_type == "GEOGRAPHIC" else 4326
                print(generate_python_snippet(diag.epsg_code, target or 3857))
        return 0

    # Mode 3: Coordinates or File
    if args.coords_or_file:
        first_arg = args.coords_or_file[0]
        # Check if it's a file
        if Path(first_arg).is_file():
            path = Path(first_arg)
            ext = path.suffix.lower()
            if ext in [".tif", ".tiff"]:
                res = inspect_geotiff_header(path)
            elif ext in [".prj"]:
                res = inspect_prj_file(path)
            elif ext in [".geojson", ".json"]:
                res = inspect_geojson_file(path)
            else:
                res = {"error": f"Unsupported file extension: {ext}. Supported: .tif, .tiff, .prj, .geojson"}

            if args.json:
                print(json.dumps(res, indent=2))
            else:
                print("=" * 60)
                print(f"CRS FILE INSPECTION: {path.name}")
                print("=" * 60)
                print(json.dumps(res, indent=2))
            return 0

        # Check if coordinates were passed: X Y
        if len(args.coords_or_file) >= 2:
            try:
                x = float(args.coords_or_file[0])
                y = float(args.coords_or_file[1])
                diag = diagnose_coordinates(x, y)
                if args.json:
                    print(json.dumps(diag.to_dict(), indent=2))
                else:
                    _print_diagnosis(diag, f"Coordinates ({x}, {y})")
                    if args.snippet and diag.epsg_code:
                        print("-" * 60)
                        target = diag.utm_epsg_candidate if diag.coordinate_type == "GEOGRAPHIC" else 4326
                        print(generate_python_snippet(diag.epsg_code, target or 3857))
                return 0
            except ValueError:
                pass

    parser.print_help()
    return 1


def _print_diagnosis(res: CRSDiagnosisResult, title: str) -> None:
    print("=" * 60)
    print(f"CRS DIAGNOSIS FOR: {title}")
    print("=" * 60)
    print(f"Likely CRS      : {res.likely_crs}")
    print(f"EPSG Code       : {res.epsg_code if res.epsg_code else 'N/A'}")
    print(f"Confidence      : {res.confidence}")
    print(f"Coordinate Type : {res.coordinate_type} ({res.units})")
    print(f"Description     : {res.description}")
    if res.approx_wgs84_coords:
        lon, lat = res.approx_wgs84_coords
        print(f"Approx WGS84    : Longitude {lon:.5f}°, Latitude {lat:.5f}°")
    if res.utm_zone_candidate:
        print(f"Matching UTM    : Zone {res.utm_zone_candidate} (EPSG:{res.utm_epsg_candidate})")
    if res.warnings:
        print("-" * 60)
        print("WARNINGS:")
        for w in res.warnings:
            print(f" [!] {w}")
    if res.recommendations:
        print("-" * 60)
        print("RECOMMENDATIONS:")
        for r in res.recommendations:
            print(f" [*] {r}")


if __name__ == "__main__":
    sys.exit(main())
