"""Nautical chart overlay generation for Maritime Intelligence Briefings.

Integrates OpenSeaMap seamarks (navigation buoys, fairways, lights, depth soundings)
and OpenStreetMap maritime basemap tiles with graceful offline DEM / hydrographic
bathymetric fallback.
"""

from __future__ import annotations

import logging
import math
import os
import urllib.request
from pathlib import Path
from typing import Any, Optional, Sequence

import matplotlib
matplotlib.use("Agg")
import matplotlib.patches as patches
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import numpy as np
from PIL import Image

from sentinel_analysis.domain.entities import BoundingBox, Scan

logger = logging.getLogger(__name__)

USER_AGENT = "SentinelImageryAnalysis/1.0 (contact: info@sentinel-analysis.org)"


def deg2num(lat_deg: float, lon_deg: float, zoom: int) -> tuple[int, int]:
    """Convert WGS-84 latitude and longitude to Slippy Map tile indices (x, y)."""
    lat_rad = math.radians(lat_deg)
    n = 2.0 ** zoom
    xtile = int((lon_deg + 180.0) / 360.0 * n)
    lat_rad_clamped = max(-1.4844, min(1.4844, lat_rad))  # Clamp to ~85 degrees
    ytile = int((1.0 - math.asinh(math.tan(lat_rad_clamped)) / math.pi) / 2.0 * n)
    return xtile, ytile


def num2deg(xtile: int, ytile: int, zoom: int) -> tuple[float, float]:
    """Convert Slippy Map tile index (x, y) to Northwest WGS-84 coordinate (lat, lon)."""
    n = 2.0 ** zoom
    lon_deg = xtile / n * 360.0 - 180.0
    lat_rad = math.atan(math.sinh(math.pi * (1.0 - 2.0 * ytile / n)))
    lat_deg = math.degrees(lat_rad)
    return lat_deg, lon_deg


def fetch_tile_image(url: str, cache_file: Path, timeout: float = 2.5) -> Optional[Image.Image]:
    """Fetch a tile image with local disk cache and resilient timeout."""
    try:
        if cache_file.is_file() and cache_file.stat().st_size > 100:
            return Image.open(cache_file).convert("RGBA")
    except Exception:
        pass

    cache_file.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                data = resp.read()
                cache_file.write_bytes(data)
                im = Image.open(cache_file)
                return im.convert("RGBA")
    except Exception as exc:
        logger.debug("Tile fetch failed for %s: %s", url, exc)
    return None


def select_optimal_zoom(bbox: BoundingBox, max_tiles: int = 16) -> int:
    """Select Slippy Map zoom level keeping total tiles within max_tiles."""
    lon_span = max(0.005, abs(bbox.max_longitude - bbox.min_longitude))
    lat_span = max(0.005, abs(bbox.max_latitude - bbox.min_latitude))
    
    # Target resolution: ~1000-1500 pixels across bounding box
    for zoom in range(13, 7, -1):
        x0, y1 = deg2num(bbox.min_latitude, bbox.min_longitude, zoom)
        x1, y0 = deg2num(bbox.max_latitude, bbox.max_longitude, zoom)
        tiles_x = abs(x1 - x0) + 1
        tiles_y = abs(y1 - y0) + 1
        if tiles_x * tiles_y <= max_tiles:
            return zoom
    return 9


def build_nautical_basemap(
    bbox: BoundingBox,
    cache_dir: Optional[Path] = None,
    dem_path: Optional[Path] = None,
    timeout: float = 2.5,
) -> tuple[Optional[np.ndarray], tuple[float, float, float, float]]:
    """Build a composited nautical basemap (OpenStreetMap + OpenSeaMap seamarks).

    Returns:
        tuple[Optional[np.ndarray], tuple[float, float, float, float]]:
            (image_array_rgb, (min_lon, max_lon, min_lat, max_lat))
    """
    if cache_dir is None:
        cache_dir = Path(".cache/nautical_tiles")

    zoom = select_optimal_zoom(bbox)
    min_x, max_y = deg2num(bbox.min_latitude, bbox.min_longitude, zoom)
    max_x, min_y = deg2num(bbox.max_latitude, bbox.max_longitude, zoom)

    if min_x > max_x:
        min_x, max_x = max_x, min_x
    if min_y > max_y:
        min_y, max_y = max_y, min_y

    tiles_x = max_x - min_x + 1
    tiles_y = max_y - min_y + 1

    tile_size = 256
    canvas_w = tiles_x * tile_size
    canvas_h = tiles_y * tile_size

    # Geographic extent of the stitched tile canvas
    canvas_max_lat, canvas_min_lon = num2deg(min_x, min_y, zoom)
    canvas_min_lat, canvas_max_lon = num2deg(max_x + 1, max_y + 1, zoom)
    extent = (canvas_min_lon, canvas_max_lon, canvas_min_lat, canvas_max_lat)

    # Attempt to fetch and stitch online OSM + OpenSeaMap tiles
    composite = Image.new("RGBA", (canvas_w, canvas_h), (220, 235, 245, 255))
    online_success = False
    tiles_fetched = 0

    for ix, x in enumerate(range(min_x, max_x + 1)):
        for iy, y in enumerate(range(min_y, max_y + 1)):
            px = ix * tile_size
            py = iy * tile_size

            osm_url = f"https://tile.openstreetmap.org/{zoom}/{x}/{y}.png"
            osm_cache = cache_dir / "osm" / str(zoom) / f"{x}_{y}.png"
            osm_tile = fetch_tile_image(osm_url, osm_cache, timeout=timeout)

            seamark_url = f"https://tiles.openseamap.org/seamark/{zoom}/{x}/{y}.png"
            seamark_cache = cache_dir / "seamark" / str(zoom) / f"{x}_{y}.png"
            seamark_tile = fetch_tile_image(seamark_url, seamark_cache, timeout=timeout)

            if osm_tile is not None:
                composite.paste(osm_tile, (px, py))
                tiles_fetched += 1
                online_success = True

            if seamark_tile is not None:
                composite.alpha_composite(seamark_tile, (px, py))

    if online_success and tiles_fetched > 0:
        return np.array(composite.convert("RGB")), extent

    # Graceful Fallback: Generate hydrographic chart from DEM or bathymetric shading
    logger.info("Online nautical tiles unavailable; generating hydrographic chart fallback.")
    fallback_arr = generate_hydrographic_chart_fallback(bbox, dem_path=dem_path)
    return fallback_arr, (bbox.min_longitude, bbox.max_longitude, bbox.min_latitude, bbox.max_latitude)


def generate_hydrographic_chart_fallback(
    bbox: BoundingBox,
    dem_path: Optional[Path] = None,
    width: int = 800,
    height: int = 600,
) -> np.ndarray:
    """Generate a high-legibility hydrographic maritime chart fallback.

    Uses DEM elevation data to distinguish marine bathymetry from terrain, or generates
    a stylized navigational chart with nautical graticule and depth gradations.
    """
    chart = np.full((height, width, 3), (219, 234, 254), dtype=np.uint8)  # Soft oceanic blue

    if dem_path and Path(dem_path).is_file():
        try:
            with Image.open(dem_path) as im:
                dem_im = im.convert("L").resize((width, height), Image.Resampling.BILINEAR)
                dem_arr = np.array(dem_im)

                # In DEM imagery, water is typically lowest intensity (0 or near 0)
                water_mask = dem_arr < 15
                land_mask = dem_arr >= 15

                # Bathymetry shading for water (deep blue to light coastal blue)
                chart[water_mask] = (202, 225, 245)

                # Coastal shallow water tint near land
                from scipy.ndimage import binary_dilation  # type: ignore
                coastal_water = binary_dilation(land_mask, iterations=6) & water_mask
                chart[coastal_water] = (225, 240, 252)

                # Land tint (warm buff / hydrographic chart terrain color)
                chart[land_mask] = (244, 241, 232)

                # Shoreline border stroke
                shoreline = binary_dilation(water_mask, iterations=2) & land_mask
                chart[shoreline] = (90, 110, 130)
                return chart
        except Exception as exc:
            logger.debug("DEM hydrographic chart processing failed: %s", exc)

    # Stylized marine background with subtle depth contours
    y_coords, x_coords = np.ogrid[:height, :width]
    depth_gradient = ((np.sin(x_coords / 80.0) + np.cos(y_coords / 60.0)) * 10).astype(np.int16)
    r = np.clip(215 + depth_gradient, 195, 235).astype(np.uint8)
    g = np.clip(230 + depth_gradient, 210, 245).astype(np.uint8)
    b = np.clip(248 + depth_gradient // 2, 235, 255).astype(np.uint8)
    chart[:, :, 0] = r
    chart[:, :, 1] = g
    chart[:, :, 2] = b
    return chart


def render_nautical_chart_overlay(
    ax: plt.Axes,
    scan: Scan,
    detections: list[dict[str, Any]],
    ghost_vessels: list[dict[str, Any]],
    basemap_img: Optional[np.ndarray],
    extent: tuple[float, float, float, float],
) -> None:
    """Render comprehensive nautical chart with OpenSeaMap features, scale bar, and ship overlays."""
    bbox = scan.bbox
    ax.set_facecolor("#edf2f7")

    # 1. Render basemap / hydrographic chart backdrop
    if basemap_img is not None and basemap_img.size > 0:
        ax.imshow(
            basemap_img,
            extent=extent,
            origin="upper",
            aspect="auto",
            zorder=1,
            alpha=0.92,
        )

    # Coordinate limits matching bounding box with slight margin
    lon_pad = max((bbox.max_longitude - bbox.min_longitude) * 0.04, 0.01)
    lat_pad = max((bbox.max_latitude - bbox.min_latitude) * 0.04, 0.01)
    ax.set_xlim(bbox.min_longitude - lon_pad, bbox.max_longitude + lon_pad)
    ax.set_ylim(bbox.min_latitude - lat_pad, bbox.max_latitude + lat_pad)

    # Graticule formatting
    ax.grid(True, linestyle="--", alpha=0.45, color="#718096", zorder=2)
    ax.xaxis.set_major_formatter(ticker.FuncFormatter(lambda x, _: f"{abs(x):.2f}°{'E' if x >= 0 else 'W'}"))
    ax.yaxis.set_major_formatter(ticker.FuncFormatter(lambda y, _: f"{abs(y):.2f}°{'N' if y >= 0 else 'S'}"))
    ax.tick_params(labelsize=8, colors="#2d3748")

    # Bounding Box of SAR pass
    aoi_rect = patches.Rectangle(
        (bbox.min_longitude, bbox.min_latitude),
        bbox.max_longitude - bbox.min_longitude,
        bbox.max_latitude - bbox.min_latitude,
        linewidth=1.4,
        edgecolor="#2b6cb0",
        facecolor="none",
        linestyle="--",
        zorder=3,
    )
    ax.add_patch(aoi_rect)

    # 2. Separate detections by classification
    solas_targets = [d for d in detections if d.get("is_solas_suspect")]
    dark_targets = [d for d in detections if d.get("is_dark") and not d.get("is_solas_suspect")]
    ais_targets = [d for d in detections if not d.get("is_dark")]

    # Plot AIS correlated vessels
    if ais_targets:
        ais_lons = [d["longitude"] for d in ais_targets if d.get("longitude") is not None]
        ais_lats = [d["latitude"] for d in ais_targets if d.get("latitude") is not None]
        ax.scatter(
            ais_lons, ais_lats,
            c="#2b6cb0", edgecolors="white", linewidths=0.8,
            marker="o", s=55,
            label=f"AIS Correlated ({len(ais_targets)})",
            zorder=5,
        )

    # Plot Non-SOLAS Dark targets
    if dark_targets:
        dark_lons = [d["longitude"] for d in dark_targets if d.get("longitude") is not None]
        dark_lats = [d["latitude"] for d in dark_targets if d.get("latitude") is not None]
        ax.scatter(
            dark_lons, dark_lats,
            c="#e53e3e", edgecolors="white", linewidths=0.9,
            marker="^", s=75,
            label=f"Dark Vessel ({len(dark_targets)})",
            zorder=6,
        )

    # Plot SOLAS violations with prominent warning marker
    if solas_targets:
        solas_lons = [d["longitude"] for d in solas_targets if d.get("longitude") is not None]
        solas_lats = [d["latitude"] for d in solas_targets if d.get("latitude") is not None]
        ax.scatter(
            solas_lons, solas_lats,
            c="#dd6b20", edgecolors="#9b2c2c", linewidths=1.5,
            marker="*", s=160,
            label=f"SOLAS Non-Compliant ({len(solas_targets)})",
            zorder=7,
        )
        # Callout annotations for SOLAS targets
        for s_tgt in solas_targets:
            s_lat = s_tgt.get("latitude")
            s_lon = s_tgt.get("longitude")
            s_id = s_tgt.get("id", "?")
            s_len = s_tgt.get("length_m", 0)
            if s_lat is not None and s_lon is not None:
                ax.annotate(
                    f"#{s_id}: SOLAS SUSPECT\nL: {s_len:.0f}m (No AIS)",
                    xy=(s_lon, s_lat),
                    xytext=(15, 12),
                    textcoords="offset points",
                    fontsize=7,
                    fontweight="bold",
                    color="#9b2c2c",
                    bbox=dict(boxstyle="round,pad=0.25", facecolor="#fff5f5", edgecolor="#e53e3e", alpha=0.9),
                    arrowprops=dict(arrowstyle="->", color="#e53e3e", lw=1.0),
                    zorder=8,
                )

    # Plot Ghost Vessels (AIS signal without SAR radar contact)
    if ghost_vessels:
        g_lons = [
            float(g.get("longitude") or g.get("lon") or g.get("lng"))
            for g in ghost_vessels
            if (g.get("longitude") or g.get("lon") or g.get("lng")) is not None
        ]
        g_lats = [
            float(g.get("latitude") or g.get("lat"))
            for g in ghost_vessels
            if (g.get("latitude") or g.get("lat")) is not None
        ]
        if g_lons:
            ax.scatter(
                g_lons, g_lats,
                c="#805ad5", edgecolors="white", linewidths=0.8,
                marker="D", s=55,
                label=f"Ghost AIS ({len(g_lons)})",
                zorder=6,
            )

    # 3. Nautical Scale Bar (Calculated in Nautical Miles: 1 NM = 1852m)
    mid_lat = (bbox.min_latitude + bbox.max_latitude) / 2.0
    meters_per_deg_lon = 111320.0 * math.cos(math.radians(mid_lat))
    span_meters = (bbox.max_longitude - bbox.min_longitude) * meters_per_deg_lon

    # Pick scale length: 2, 5, 10, or 20 Nautical Miles
    nm_options = [1, 2, 5, 10, 20, 50]
    target_nm = span_meters / 1852.0 * 0.20
    chosen_nm = min(nm_options, key=lambda x: abs(x - target_nm))
    scale_bar_deg = (chosen_nm * 1852.0) / meters_per_deg_lon

    sb_x0 = bbox.min_longitude + (bbox.max_longitude - bbox.min_longitude) * 0.05
    sb_y0 = bbox.min_latitude + (bbox.max_latitude - bbox.min_latitude) * 0.05

    ax.plot([sb_x0, sb_x0 + scale_bar_deg], [sb_y0, sb_y0], color="#1a202c", linewidth=3.0, zorder=9)
    ax.plot([sb_x0, sb_x0], [sb_y0 - lat_pad * 0.15, sb_y0 + lat_pad * 0.15], color="#1a202c", linewidth=1.5, zorder=9)
    ax.plot([sb_x0 + scale_bar_deg, sb_x0 + scale_bar_deg], [sb_y0 - lat_pad * 0.15, sb_y0 + lat_pad * 0.15], color="#1a202c", linewidth=1.5, zorder=9)
    ax.text(
        sb_x0 + scale_bar_deg / 2.0,
        sb_y0 + lat_pad * 0.35,
        f"{chosen_nm} NM ({chosen_nm * 1.852:.1f} km)",
        ha="center",
        va="bottom",
        fontsize=7.5,
        fontweight="bold",
        color="#1a202c",
        bbox=dict(boxstyle="square,pad=0.15", facecolor="white", edgecolor="none", alpha=0.8),
        zorder=9,
    )

    # 4. Nautical North Arrow
    na_x = bbox.max_longitude - (bbox.max_longitude - bbox.min_longitude) * 0.06
    na_y = bbox.max_latitude - (bbox.max_latitude - bbox.min_latitude) * 0.08
    ax.annotate(
        "N",
        xy=(na_x, na_y),
        xytext=(na_x, na_y - lat_pad * 1.2),
        ha="center",
        va="center",
        fontsize=9,
        fontweight="bold",
        color="#1a202c",
        arrowprops=dict(facecolor="#1a202c", edgecolor="white", width=2.0, headwidth=6.0, shrink=0.05),
        zorder=9,
    )

    # 5. Legend
    handles, labels = ax.get_legend_handles_labels()
    if handles:
        ax.legend(
            loc="upper left",
            fontsize=8,
            framealpha=0.92,
            facecolor="white",
            edgecolor="#cbd5e0",
        )
