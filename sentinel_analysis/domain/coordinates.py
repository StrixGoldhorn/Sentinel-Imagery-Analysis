"""Geospatial coordinate projection and transformation utilities for SAR satellite imagery.

Handles conversion between raster pixel coordinates (col, row) / (x, y) and
geographic WGS-84 coordinates (latitude, longitude, EPSG:4326), as well as
oriented bounding box (OBB) polygons, bounding boxes, and GeoJSON features.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable, Optional, Sequence, Union

from sentinel_analysis.domain.entities import BoundingBox, ShipDetection

logger = logging.getLogger(__name__)


def coerce_bounding_box(bbox: Any) -> Optional[BoundingBox]:
    """Coerce various bounding box representations into a BoundingBox domain entity."""
    if bbox is None:
        return None
    if isinstance(bbox, BoundingBox):
        return bbox
    if hasattr(bbox, "min_latitude") and hasattr(bbox, "max_latitude"):
        try:
            return BoundingBox(
                min_longitude=float(bbox.min_longitude),
                min_latitude=float(bbox.min_latitude),
                max_longitude=float(bbox.max_longitude),
                max_latitude=float(bbox.max_latitude),
            )
        except Exception:
            return None
    if isinstance(bbox, dict):
        min_lat = bbox.get("min_latitude", bbox.get("min_lat"))
        max_lat = bbox.get("max_latitude", bbox.get("max_lat"))
        min_lon = bbox.get("min_longitude", bbox.get("min_lon"))
        max_lon = bbox.get("max_longitude", bbox.get("max_lon"))
        if None not in (min_lat, max_lat, min_lon, max_lon):
            try:
                return BoundingBox(
                    min_longitude=float(min_lon),
                    min_latitude=float(min_lat),
                    max_longitude=float(max_lon),
                    max_latitude=float(max_lat),
                )
            except Exception:
                return None
    if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
        try:
            # Common order in BoundingBox is (min_lon, min_lat, max_lon, max_lat)
            return BoundingBox.from_sequence(bbox)
        except Exception:
            return None
    return None


def resolve_image_transform(
    image_path: Path | str | None,
    bbox: BoundingBox | None = None,
    image_width: int = 1,
    image_height: int = 1,
) -> Optional[Callable[[float, float], tuple[float, float]]]:
    """Attempt to resolve a rasterio affine transform mapping (pixel_x, pixel_y) -> (longitude, latitude).
    
    Returns None if rasterio is unavailable or no georeferenced GeoTIFF header is found.
    """
    if image_path is None:
        return None
    img_p = Path(image_path)
    folder_dir = img_p.parent

    candidates: list[Path] = [
        img_p,
        folder_dir / f"{img_p.stem}.tif",
        folder_dir / f"{img_p.stem}.tiff",
    ]
    # Check parent folder name
    if folder_dir.name:
        candidates.append(folder_dir / f"{folder_dir.name}.tif")

    for candidate in candidates:
        if candidate and candidate.is_file() and candidate.suffix.lower() in (".tif", ".tiff"):
            try:
                import rasterio
                import rasterio.warp
                from rasterio.transform import xy

                with rasterio.open(candidate) as src:
                    transform = src.transform
                    crs = src.crs
                    if transform is not None:
                        def _affine(px: float, py: float, t=transform, c=crs) -> tuple[float, float]:
                            x_c, y_c = xy(t, py, px)
                            if c and not c.is_geographic:
                                lons, lats = rasterio.warp.transform(c, "EPSG:4326", [x_c], [y_c])
                                return float(lons[0]), float(lats[0])
                            return float(x_c), float(y_c)
                        return _affine
            except Exception as exc:
                logger.debug("Rasterio transform lookup skipped or failed for %s: %s", candidate, exc)
    return None


def pixel_to_geo(
    px: float,
    py: float,
    bbox: BoundingBox | None = None,
    image_width: int = 1,
    image_height: int = 1,
    transform_fn: Optional[Callable[[float, float], tuple[float, float]]] = None,
) -> tuple[float, float]:
    """Convert raster pixel coordinates (px=col, py=row) to WGS-84 geographic coordinates.
    
    Returns:
        (latitude, longitude) tuple in WGS-84 degrees.
    """
    if transform_fn is not None:
        try:
            lon, lat = transform_fn(float(px), float(py))
            lat = max(-90.0, min(90.0, float(lat)))
            lon = max(-180.0, min(180.0, float(lon)))
            return lat, lon
        except Exception as exc:
            logger.debug("transform_fn failed on (%s, %s): %s", px, py, exc)

    if bbox is None:
        raise ValueError("Either bbox or transform_fn must be provided to convert pixel to geographic coordinates.")

    img_w = max(1.0, float(image_width))
    img_h = max(1.0, float(image_height))

    lat_span = bbox.max_latitude - bbox.min_latitude
    lon_span = bbox.max_longitude - bbox.min_longitude

    lat = bbox.max_latitude - (float(py) / img_h) * lat_span
    lon = bbox.min_longitude + (float(px) / img_w) * lon_span

    lat = max(-90.0, min(90.0, lat))
    lon = max(-180.0, min(180.0, lon))
    return lat, lon


def geo_to_pixel(
    lat: float,
    lon: float,
    bbox: BoundingBox,
    image_width: int,
    image_height: int,
) -> tuple[float, float]:
    """Convert WGS-84 geographic coordinates (lat, lon) to raster pixel coordinates (px, py)."""
    img_w = max(1.0, float(image_width))
    img_h = max(1.0, float(image_height))

    lat_span = bbox.max_latitude - bbox.min_latitude
    lon_span = bbox.max_longitude - bbox.min_longitude

    if lat_span > 0 and lon_span > 0:
        px = ((float(lon) - bbox.min_longitude) / lon_span) * img_w
        py = ((bbox.max_latitude - float(lat)) / lat_span) * img_h
        return px, py
    return 0.0, 0.0


def project_detection_coordinates(
    det: ShipDetection | dict[str, Any],
    bbox: Any = None,
    image_width: int = 1,
    image_height: int = 1,
    transform_fn: Optional[Callable[[float, float], tuple[float, float]]] = None,
    force: bool = False,
) -> dict[str, Any]:
    """Project detection bounding box, centroid, and polygon vertices to WGS-84 geographic coordinates.
    
    Returns a dictionary containing:
        - lat: float (rounded to 6 decimal places)
        - lng: float (rounded to 6 decimal places)
        - latitude: float (rounded to 7 decimal places)
        - longitude: float (rounded to 7 decimal places)
        - geo_bbox: dict with min_lat, max_lat, min_lon, max_lon
        - geo_polygon: tuple of (lat, lon) pairs if polygon_points present, else None
    """
    bbox_obj = coerce_bounding_box(bbox)

    # Extract existing geographic coordinates if present and valid
    if not force:
        existing_lat = getattr(det, "latitude", None) if not isinstance(det, dict) else (det.get("latitude") if det.get("latitude") is not None else det.get("lat"))
        existing_lon = getattr(det, "longitude", None) if not isinstance(det, dict) else (det.get("longitude") if det.get("longitude") is not None else (det.get("lng") if det.get("lng") is not None else det.get("lon")))
        existing_bbox = getattr(det, "geo_bbox", None) if not isinstance(det, dict) else det.get("geo_bbox")
        existing_poly = getattr(det, "geo_polygon", None) if not isinstance(det, dict) else det.get("geo_polygon")

        if existing_lat is not None and existing_lon is not None and existing_bbox is not None:
            norm_bbox = {
                "min_lat": round(float(existing_bbox.get("min_lat", existing_bbox.get("min_latitude"))), 7),
                "max_lat": round(float(existing_bbox.get("max_lat", existing_bbox.get("max_latitude"))), 7),
                "min_lon": round(float(existing_bbox.get("min_lon", existing_bbox.get("min_longitude"))), 7),
                "max_lon": round(float(existing_bbox.get("max_lon", existing_bbox.get("max_longitude"))), 7),
            }
            norm_poly = None
            if existing_poly and len(existing_poly) >= 3:
                norm_poly = tuple((round(float(p[0]), 7), round(float(p[1]), 7)) for p in existing_poly)
            return {
                "lat": round(float(existing_lat), 6),
                "lng": round(float(existing_lon), 6),
                "latitude": round(float(existing_lat), 7),
                "longitude": round(float(existing_lon), 7),
                "geo_bbox": norm_bbox,
                "geo_polygon": norm_poly,
            }

    if bbox_obj is None and transform_fn is None:
        # Cannot project without bounding box or transform function
        return {
            "lat": None,
            "lng": None,
            "latitude": None,
            "longitude": None,
            "geo_bbox": None,
            "geo_polygon": None,
        }

    # Extract pixel coordinates
    if isinstance(det, dict):
        x = float(det.get("x", 0))
        y = float(det.get("y", 0))
        w = float(det.get("width", 1))
        h = float(det.get("height", 1))
        cx = det.get("center_x")
        cy = det.get("center_y")
        polygon_points = det.get("polygon_points")
    else:
        x = float(det.x)
        y = float(det.y)
        w = float(det.width)
        h = float(det.height)
        cx = det.center_x
        cy = det.center_y
        polygon_points = getattr(det, "polygon_points", None)

    center_x = float(cx) if cx is not None else (x + w / 2.0)
    center_y = float(cy) if cy is not None else (y + h / 2.0)

    center_lat, center_lon = pixel_to_geo(center_x, center_y, bbox=bbox_obj, image_width=image_width, image_height=image_height, transform_fn=transform_fn)

    # 4 corners of bounding box
    corners_pixels = [
        (x, y),
        (x + w, y),
        (x + w, y + h),
        (x, y + h),
    ]
    corners_geo = [pixel_to_geo(px, py, bbox=bbox_obj, image_width=image_width, image_height=image_height, transform_fn=transform_fn) for px, py in corners_pixels]
    lats = [pt[0] for pt in corners_geo]
    lons = [pt[1] for pt in corners_geo]

    # If polygon points exist, compute polygon geo coordinates
    geo_polygon = None
    if polygon_points and len(polygon_points) >= 3:
        pts_geo = [
            pixel_to_geo(float(pt[0]), float(pt[1]), bbox=bbox_obj, image_width=image_width, image_height=image_height, transform_fn=transform_fn)
            for pt in polygon_points
        ]
        geo_polygon = tuple((round(p[0], 7), round(p[1], 7)) for p in pts_geo)
        # Bounding box should also incorporate polygon vertices
        lats.extend(p[0] for p in pts_geo)
        lons.extend(p[1] for p in pts_geo)

    det_min_lat = min(lats)
    det_max_lat = max(lats)
    det_min_lon = min(lons)
    det_max_lon = max(lons)

    geo_bbox = {
        "min_lat": round(det_min_lat, 7),
        "max_lat": round(det_max_lat, 7),
        "min_lon": round(det_min_lon, 7),
        "max_lon": round(det_max_lon, 7),
    }

    return {
        "lat": round(center_lat, 6),
        "lng": round(center_lon, 6),
        "latitude": round(center_lat, 7),
        "longitude": round(center_lon, 7),
        "geo_bbox": geo_bbox,
        "geo_polygon": geo_polygon,
    }


def build_geojson_geometry(
    det: dict[str, Any] | ShipDetection,
    bbox: Any = None,
    image_width: int = 1,
    image_height: int = 1,
    transform_fn: Optional[Callable[[float, float], tuple[float, float]]] = None,
) -> Optional[dict[str, Any]]:
    """Generate RFC 7946 GeoJSON geometry for a detection (Polygon or Point).
    
    GeoJSON coordinates format: [longitude, latitude].
    """
    geo_poly = getattr(det, "geo_polygon", None) if not isinstance(det, dict) else det.get("geo_polygon")
    geo_box = getattr(det, "geo_bbox", None) if not isinstance(det, dict) else det.get("geo_bbox")
    lat = getattr(det, "latitude", None) if not isinstance(det, dict) else (det.get("latitude") if det.get("latitude") is not None else det.get("lat"))
    lng = getattr(det, "longitude", None) if not isinstance(det, dict) else (det.get("longitude") if det.get("longitude") is not None else (det.get("lng") if det.get("lng") is not None else det.get("lon")))

    # If coordinates are missing but bbox is available, project them
    if (lat is None or lng is None) and bbox is not None:
        proj = project_detection_coordinates(det, bbox, image_width, image_height, transform_fn)
        lat = proj.get("latitude")
        lng = proj.get("longitude")
        geo_box = proj.get("geo_bbox")
        geo_poly = proj.get("geo_polygon")

    if geo_poly and len(geo_poly) >= 3:
        # geo_poly is list/tuple of (lat, lon) -> GeoJSON requires [lon, lat]
        ring = [[round(float(p[1]), 7), round(float(p[0]), 7)] for p in geo_poly]
        if ring[0] != ring[-1]:
            ring.append(ring[0])
        return {
            "type": "Polygon",
            "coordinates": [ring],
        }

    if geo_box and isinstance(geo_box, dict):
        min_lat = geo_box.get("min_lat", geo_box.get("min_latitude"))
        max_lat = geo_box.get("max_lat", geo_box.get("max_latitude"))
        min_lon = geo_box.get("min_lon", geo_box.get("min_longitude"))
        max_lon = geo_box.get("max_lon", geo_box.get("max_longitude"))
        if None not in (min_lat, max_lat, min_lon, max_lon):
            ring = [
                [round(float(min_lon), 7), round(float(min_lat), 7)],
                [round(float(max_lon), 7), round(float(min_lat), 7)],
                [round(float(max_lon), 7), round(float(max_lat), 7)],
                [round(float(min_lon), 7), round(float(max_lat), 7)],
                [round(float(min_lon), 7), round(float(min_lat), 7)],
            ]
            return {
                "type": "Polygon",
                "coordinates": [ring],
            }

    if lat is not None and lng is not None:
        return {
            "type": "Point",
            "coordinates": [round(float(lng), 7), round(float(lat), 7)],
        }

    return None
